from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from config.settings import settings
from core.time_utils import today_tz
from services.db import db
from services.delivery_preferences import get_user_timezone
from services.messenger.audio_progress import AudioProgressItem, get_progress_snapshot
from services.mood import create_session
from services.practice_tokens import (
    EMPTY_BALANCE_MESSAGE,
    enforcement_mode,
    get_delivery_mode,
    get_wallet,
    token_access_authoritative,
)


@dataclass(frozen=True)
class PracticeJourneyStart:
    status: str
    message: str
    session_id: int | None = None
    kind: str | None = None
    item: AudioProgressItem | None = None
    available_tokens: int = 0

    @property
    def ready_for_pre_score(self) -> bool:
        return self.status == "pre_score" and self.session_id is not None and self.item is not None


def infer_kind_for_item(item: AudioProgressItem) -> str:
    title = str(item.title or "").casefold().replace("ё", "е")
    if any(marker in title for marker in ("evening", "home", "вечер", "домой", "дом")):
        return "home"
    return "home" if int(item.anchor) % 2 == 0 else "work"


def _existing_pending_session(user_id: int, audio_anchor: int) -> int | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM mood_sessions
            WHERE user_id=? AND anchor_id=?
              AND COALESCE(audio_sent,0)=0
              AND source IN ('auto','settings')
            ORDER BY id DESC
            LIMIT 1
            """.strip(),
            (int(user_id), int(audio_anchor)),
        ).fetchone()
    return int(row["id"]) if row else None


def _get_or_create_session(user_id: int, item: AudioProgressItem, kind: str) -> int:
    existing = _existing_pending_session(int(user_id), int(item.anchor))
    if existing is not None:
        return existing

    try:
        return int(
            create_session(
                int(user_id),
                kind=kind,
                source="settings",
                day=today_tz(
                    get_user_timezone(int(user_id)) or settings.TIMEZONE or "UTC"
                ).isoformat(),
                slot="morning" if kind == "work" else "evening",
                scheduled_at=None,
                anchor_id=int(item.anchor),
            )
        )
    except sqlite3.IntegrityError:
        existing = _existing_pending_session(int(user_id), int(item.anchor))
        if existing is None:
            raise
        return existing


def start_or_resume_paid_practice(user_id: int) -> PracticeJourneyStart:
    """Resolve the next user-visible step for the paid practice route."""

    uid = int(user_id)
    wallet = get_wallet(uid)
    snapshot = get_progress_snapshot(uid)

    if snapshot.pending_item is not None:
        item = snapshot.pending_item
        return PracticeJourneyStart(
            status="pending_audio",
            message=(
                f"🎧 У Вас уже выдано аудио №{item.anchor} — {item.title}.\n\n"
                "Повторная выдача не списывает ещё одну практику. "
                "Прослушайте его и нажмите «✅ Прослушал» либо запросите повтор."
            ),
            item=item,
            kind=infer_kind_for_item(item),
            available_tokens=int(wallet.available_tokens),
        )

    item = snapshot.next_item
    if item is None:
        return PracticeJourneyStart(
            status="finished",
            message=(
                "✅ Все доступные аудио общего маршрута уже пройдены.\n\n"
                "Можно посмотреть прогресс и историю или повторить последнюю практику."
            ),
            available_tokens=int(wallet.available_tokens),
        )

    mode = enforcement_mode()
    if token_access_authoritative() and mode == "hard" and int(wallet.available_tokens) <= 0:
        return PracticeJourneyStart(
            status="insufficient_balance",
            message=EMPTY_BALANCE_MESSAGE,
            item=item,
            kind=infer_kind_for_item(item),
            available_tokens=0,
        )

    kind = infer_kind_for_item(item)
    session_id = _get_or_create_session(uid, item, kind)
    return PracticeJourneyStart(
        status="pre_score",
        message=(
            f"🌿 Следующая практика: №{item.anchor} — {item.title}.\n\n"
            "Сначала оцените состояние ДО прослушивания от −10 до +10. "
            "После оценки бот отправит аудио; одна практика будет списана только после успешной отправки."
        ),
        session_id=session_id,
        kind=kind,
        item=item,
        available_tokens=int(wallet.available_tokens),
    )


def paid_route_summary(user_id: int) -> str:
    wallet = get_wallet(int(user_id))
    mode = get_delivery_mode(int(user_id))
    mode_titles = {
        "single_daily": "1 практика в день",
        "morning_only": "только утро",
        "evening_only": "только вечер",
        "both": "утро + вечер",
        "paused": "пауза",
    }
    return (
        "🔐 Полный маршрут\n\n"
        f"Баланс: {wallet.available_tokens} практик"
        + (f" • {wallet.reserved_tokens} в процессе" if wallet.reserved_tokens else "")
        + f".\nРитм: {mode_titles.get(mode, mode)}.\n\n"
        "Каждое новое аудио проходит единый цикл: оценка ДО → аудио → «Прослушал» → оценка ПОСЛЕ. "
        "Повтор уже выданного аудио не списывает практику повторно."
    )
