from __future__ import annotations
import logging


import time
from services.sla import record as sla_record
import asyncio
from typing import Any, Awaitable, Callable

from core.task_manager import TaskManager

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, ReplyKeyboardRemove
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramAPIError

from services.state_log import log_state
from services.behavior import log_interaction, update_behavior

from services.pending import peek_pending, clear_pending


class SlowHandlerLogMiddleware(BaseMiddleware):
    """Logs slow update handling with enough context to diagnose slow buttons.

    This middleware does NOT change UX. It only emits a warning/error when a
    single Telegram update takes longer than the configured threshold.

    Configure via env:
        SLOW_HANDLER_MS=1200
    """

    def __init__(self, threshold_ms: int = 1200):
        super().__init__()
        self.threshold_ms = max(0, int(threshold_ms))
        self._log = logging.getLogger("perf")

    @staticmethod
    def _clean(value: Any, *, limit: int = 90) -> str:
        raw = str(value or "").replace("\n", " ").replace("\r", " ").strip()
        if len(raw) > limit:
            return raw[: limit - 1] + "…"
        return raw

    @staticmethod
    def _handler_label(data: dict[str, Any]) -> str:
        for key in ("handler", "event_handler"):
            obj = data.get(key)
            if obj is None:
                continue
            callback = getattr(obj, "callback", None)
            if callback is None:
                callback = obj
            module = getattr(callback, "__module__", "") or ""
            qualname = getattr(callback, "__qualname__", "") or getattr(callback, "__name__", "")
            label = ".".join(part for part in (module, qualname) if part)
            if label:
                return SlowHandlerLogMiddleware._clean(label, limit=120)
        return "-"

    @staticmethod
    def _event_details(event: TelegramObject) -> dict[str, Any]:
        details: dict[str, Any] = {
            "event": type(event).__name__,
            "inner": "-",
            "uid": None,
            "update_id": getattr(event, "update_id", None),
            "payload": "-",
        }

        inner = event
        for attr in (
            "callback_query",
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "my_chat_member",
            "chat_member",
        ):
            candidate = getattr(event, attr, None)
            if candidate is not None:
                inner = candidate
                details["inner"] = type(candidate).__name__
                break

        if details["inner"] == "-":
            details["inner"] = type(inner).__name__

        try:
            from_user = getattr(inner, "from_user", None) or getattr(inner, "event_from_user", None)
            if from_user is not None:
                details["uid"] = int(getattr(from_user, "id"))
        except (AttributeError, TypeError, ValueError):
            details["uid"] = None

        try:
            if type(inner).__name__ == "CallbackQuery":
                details["payload"] = "callback=" + SlowHandlerLogMiddleware._clean(
                    getattr(inner, "data", "") or "-", limit=100
                )
            elif type(inner).__name__ == "Message":
                text = (getattr(inner, "text", None) or getattr(inner, "caption", None) or "").strip()
                if text.startswith("/"):
                    details["payload"] = "message_command=" + SlowHandlerLogMiddleware._clean(
                        text.split(maxsplit=1)[0], limit=60
                    )
                elif text:
                    details["payload"] = f"message_text_len={len(text)}"
                elif getattr(inner, "audio", None) is not None:
                    details["payload"] = "message_audio"
                elif getattr(inner, "voice", None) is not None:
                    details["payload"] = "message_voice"
                elif getattr(inner, "document", None) is not None:
                    details["payload"] = "message_document"
                elif getattr(inner, "photo", None) is not None:
                    details["payload"] = "message_photo"
                else:
                    details["payload"] = "message_other"
            else:
                details["payload"] = SlowHandlerLogMiddleware._clean(type(inner).__name__, limit=80)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            details["payload"] = "-"

        return details

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        t0 = time.monotonic()
        try:
            return await handler(event, data)
        finally:
            dt_ms = int((time.monotonic() - t0) * 1000)
            if dt_ms >= self.threshold_ms:
                details = self._event_details(event)
                handler_label = self._handler_label(data)
                message = (
                    "SLOW update "
                    f"event={details['event']} "
                    f"inner={details['inner']} "
                    f"uid={details['uid']} "
                    f"update_id={details['update_id']} "
                    f"{details['payload']} "
                    f"handler={handler_label} "
                    f"duration_ms={dt_ms}"
                )
                if dt_ms > 3000:
                    self._log.error(message)
                elif dt_ms > 1500:
                    self._log.warning(message)


class QuickAckCallbackMiddleware(BaseMiddleware):
    """Answers CallbackQuery ASAP so buttons feel instant.

    Telegram shows a spinner until we call `answer()`.
    This middleware makes UX snappier even if the handler does heavy work.

    Extra hardening:
    - wraps ``cb.answer`` so the first successful answer wins;
    - later duplicate ``cb.answer(...)`` calls from handlers become no-ops.

    This removes redundant Telegram round-trips on the hot callback path without
    forcing us to rewrite dozens of existing handlers.
    """

    @staticmethod
    def _patch_callback_answer(event: CallbackQuery):
        original_answer = event.answer
        answered = False

        async def _safe_answer(*args, **kwargs):
            nonlocal answered
            if answered:
                return None
            # Mark as answered before the network call so duplicate handler-level
            # cb.answer() calls do not create extra Telegram round-trips during
            # the same callback lifecycle when the network is flaky.
            answered = True
            try:
                return await original_answer(*args, **kwargs)
            except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError, asyncio.TimeoutError):  # validator: allow-wide-except
                return None

        if hasattr(original_answer, "calls"):
            _safe_answer.calls = original_answer.calls  # type: ignore[attr-defined]
        object.__setattr__(event, "answer", _safe_answer)  # type: ignore[arg-type]


    @staticmethod
    async def _dismiss_stale_picker(event: CallbackQuery, data: dict[str, Any]) -> None:
        cb_data = (event.data or '').strip()
        if cb_data in {'share:pick', 'gift:pick_target', 'admin:add_admin'}:
            return
        fu = getattr(event, 'from_user', None)
        if fu is None:
            return
        uid = int(fu.id)
        pending = peek_pending(uid)
        should_clear = bool(pending and pending.kind in {'share', 'gift_target'})
        state = data.get('state')
        state_name = None
        if state is not None:
            try:
                state_name = await state.get_state()
            except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError, asyncio.TimeoutError, AttributeError, RuntimeError, ValueError, TypeError):  # validator: allow-wide-except
                state_name = None
        if state_name == 'AdminManageState:waiting_admin_user':
            should_clear = True
            try:
                await state.clear()
            except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError, asyncio.TimeoutError, AttributeError, RuntimeError, ValueError, TypeError):  # validator: allow-wide-except
                pass
        if not should_clear:
            return
        # Silent cleanup: stale picker state must not create an extra chat message.
        clear_pending(uid)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            self._patch_callback_answer(event)
            await event.answer(cache_time=0)
            await self._dismiss_stale_picker(event, data)
        return await handler(event, data)


class TimeInputTraceMiddleware(BaseMiddleware):
    """Logs which handlers touched HH:MM text input.

    This is a guard against accidental interception by generic text handlers.
    Handlers that can plausibly touch a HH:MM input should call:
        services.time_trace.mark("handlers.module:function")

    If no mark is recorded, we log a warning.
    """

    def __init__(self):
        super().__init__()
        self._log = logging.getLogger("time_trace")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            text = (event.text or "").strip()
            # strict HH:MM only (we don't want noise)
            if len(text) in (4, 5) and ":" in text:
                try:
                    hh, mm = text.split(":", 1)
                    if hh.isdigit() and mm.isdigit() and 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59:
                        from services import time_trace

                        time_trace.begin(int(event.from_user.id), text)
                        try:
                            res = await handler(event, data)
                        finally:
                            tr = time_trace.end()
                            if tr:
                                if tr.marks:
                                    self._log.info(
                                        "HH:MM='%s' uid=%s handled_by=%s",
                                        tr.text,
                                        tr.uid,
                                        " > ".join(tr.marks),
                                    )
                                else:
                                    self._log.warning(
                                        "HH:MM='%s' uid=%s had NO handler marks (possible intercept)",
                                        tr.text,
                                        tr.uid,
                                    )
                        return res
                except (AttributeError, TypeError, ValueError, KeyError, OSError, RuntimeError):  # validator: allow-wide-except
                    # never break routing
                    logging.getLogger(__name__).exception("TimeInputTraceMiddleware failed")

        return await handler(event, data)

def _spawn_bg(data: dict[str, Any] | None, fn, *args, **kwargs) -> None:
    """Запуск sync-функции в фоне без блокировки обработки апдейта.

    В v16.1 все фоновые задачи создаём через TaskManager (единая точка контроля).
    Здесь мы запускаем *синхронные* функции в отдельном потоке через asyncio.to_thread().
    """
    tm: TaskManager | None = None
    if data:
        tm = data.get("task_manager")  # type: ignore[assignment]

    if tm is None:
        # Без TaskManager — просто пропускаем (логирование не должно ломать UX).
        return

    async def _runner():
        try:
            await asyncio.to_thread(fn, *args, **kwargs)
        except (AttributeError, TypeError, ValueError, KeyError, OSError, RuntimeError):  # validator: allow-wide-except
            logging.getLogger(__name__).exception("Background middleware task failed: %s", getattr(fn, "__name__", str(fn)))

    tm.create(_runner())
class SoftRateLimitMiddleware(BaseMiddleware):
    """Мягкий антиспам без склеивания разных типов событий.

    Callback-и и сообщения лимитируются отдельно, чтобы быстрый клик не блокировал
    следующий текст пользователя и наоборот.
    """

    def __init__(self, callback_interval_sec: float = 1.0, message_interval_sec: float = 1.0):
        super().__init__()
        self.callback_interval_sec = float(callback_interval_sec)
        self.message_interval_sec = float(message_interval_sec)
        self._last_ts: dict[tuple[int, str], float] = {}
        self._last_cleanup_ts: float = 0.0

    def _limit_key(self, user_id: int, event: TelegramObject) -> tuple[tuple[int, str] | None, float]:
        if isinstance(event, CallbackQuery):
            data = (event.data or '').strip()[:64]
            return (int(user_id), f'cb:{data}'), self.callback_interval_sec
        if isinstance(event, Message):
            text = (event.text or '').strip()[:64]
            return (int(user_id), f'msg:{text}'), self.message_interval_sec
        return None, 0.0

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id:
            now = time.time()
            if (now - self._last_cleanup_ts) > 600 and len(self._last_ts) > 2000:
                cutoff = now - 7200
                self._last_ts = {key: ts for key, ts in self._last_ts.items() if ts >= cutoff}
                self._last_cleanup_ts = now
            key, min_interval = self._limit_key(int(user_id), event)
            if key is not None and min_interval > 0:
                last = self._last_ts.get(key, 0.0)
                if now - last < min_interval:
                    if isinstance(event, CallbackQuery):
                        try:
                            await event.answer('Секунду…', show_alert=False)
                        except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError):
                            logging.getLogger(__name__).debug('Callback answer failed', exc_info=True)
                    elif isinstance(event, Message):
                        try:
                            await event.answer('Секунду…')
                        except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError):
                            logging.getLogger(__name__).debug('Message answer failed', exc_info=True)
                    return None
                self._last_ts[key] = now

        return await handler(event, data)


class StateLogMiddleware(BaseMiddleware):
    """Лёгкий лог состояния пользователя.

    Пишем в таблицу `user_state_log` только короткую строку `state`
    и минимальный meta, чтобы не засорять БД.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            user_id = None
            state = None
            meta: dict[str, Any] = {}

            if isinstance(event, Message) and event.from_user:
                user_id = event.from_user.id
                text = (event.text or "").strip()
                if text == "/start" or text.startswith("/start "):
                    state = "menu"
                elif text.startswith("/"):
                    state = "command"
                    meta["cmd"] = text.split()[0]
                else:
                    state = "text"

            elif isinstance(event, CallbackQuery) and event.from_user:
                user_id = event.from_user.id
                cb = (event.data or "").strip()
                meta["cb"] = cb[:128]
                if cb.startswith("demo"):
                    state = "demo"
                elif cb in ("full", "work", "home") or cb.startswith("audio"):
                    state = "session"
                elif cb.startswith("back"):
                    state = "menu"
                else:
                    state = "callback"

            if user_id and state:
                _spawn_bg(data, log_state, int(user_id), state, meta or None)
        except (TypeError, AttributeError, ValueError):
            logging.getLogger(__name__).exception("Middleware error (non-fatal)")

        return await handler(event, data)


class InteractionAnalyticsMiddleware(BaseMiddleware):
    """Collects lightweight behavioral signals.

    We do NOT infer diagnoses. We only track interaction rhythm to adapt the bot's tempo.
    All writes are minimal: one append log + one user_behavior update.
    """

    def __init__(self):
        super().__init__()
        self._last_mono: dict[int, float] = {}
        self._last_cleanup_mono: float = 0.0

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        kind = None
        key = None

        if isinstance(event, CallbackQuery) and event.from_user:
            user_id = int(event.from_user.id)
            kind = "callback"
            cb = (event.data or "").strip()
            key = cb.split(":", 1)[0] if cb else None
        elif isinstance(event, Message) and event.from_user:
            user_id = int(event.from_user.id)
            text = (event.text or "").strip()
            if text.startswith("/"):
                kind = "command"
                key = text.split()[0]
            else:
                kind = "message"
                key = None

        # compute delta between user actions
        delta_ms: int | None = None
        if user_id is not None and kind is not None:
            now_mono = time.monotonic()
            # occasional cleanup (TTL 2h) to prevent unbounded growth
            if (now_mono - self._last_cleanup_mono) > 600 and len(self._last_mono) > 2000:
                cutoff = now_mono - 7200
                self._last_mono = {uid:ts for uid,ts in self._last_mono.items() if ts >= cutoff}
                self._last_cleanup_mono = now_mono
            last = self._last_mono.get(user_id)
            if last is not None:
                delta_ms = int((now_mono - last) * 1000)
            self._last_mono[user_id] = now_mono

            try:
                _spawn_bg(data, log_interaction, user_id, kind, key, delta_ms)
                _spawn_bg(data, update_behavior, user_id, delta_ms)
            except (RuntimeError, AttributeError):
                logging.getLogger(__name__).exception("Middleware error (non-fatal)")

        return await handler(event, data)
