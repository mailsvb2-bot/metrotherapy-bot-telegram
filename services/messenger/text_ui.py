from __future__ import annotations

from dataclasses import dataclass, field
import urllib.parse

from config.settings import settings
from services.personalization import get_preface
from services.delivery_preferences import (
    describe_delivery_preferences,
    set_user_timezone,
    set_quiet_hours,
    clear_quiet_hours,
    set_slot_channel,
    build_delivery_policy_decision,
)

from services.messenger.bridge import issue_bridge_token
from services.messenger.entrypoints import register_user_entry
from services.messenger.links import build_messenger_targets, build_switch_targets
from services.messenger.platforms import normalize_platform, platform_title
from services.messenger.preferences import get_channel_snapshot, set_preferred_platform
from services.messenger.audio_progress import get_progress_snapshot, SEQUENCE_FULL_SERIES, confirm_pending_audio_delivery
from services.messenger.timeline import get_recent_audio_timeline
from services.mood_text_flow import parse_score_text, find_pending_pre_session_id, find_pending_post_session_id
from services.mood import create_session
from datetime import datetime, timezone


@dataclass(frozen=True)
class MessengerReply:
    kind: str = 'text'
    text: str = ''
    meta: dict[str, str] = field(default_factory=dict)


def _score_scale_text() -> str:
    return (
        'Шкала оценки: -10 — стало сильно хуже, 0 — без изменений, +10 — стало сильно лучше.\n'
        'Можно отправить любое число от -10 до 10, например: -2, 0, 4 или 8.'
    )


def _menu_text(user_id: int) -> str:
    preface = get_preface(int(user_id), context="menu")
    return (
        f"{preface}"
        "Главное меню\n\n"
        "Выберите маршрут: можно начать с бесплатной практики, открыть полный доступ или посмотреть свой прогресс.\n\n"
        "Кнопки ВКонтакте соответствуют главному меню Telegram:\n"
        "• 🌿 Попробовать бесплатно\n"
        "• 🔐 Полный маршрут\n"
        "• 💳 Тарифы\n"
        "• 🎁 Подарить\n"
        "• 📈 Мой прогресс\n"
        "• 🧠 Настройки\n"
        "• 📣 Посоветовать\n"
        "• 🌤 Погода"
    )


def _settings_text(user_id: int) -> str:
    snapshot = get_channel_snapshot(int(user_id))
    current = platform_title(snapshot.get("preferred_platform"))
    linked: list[str] = []
    for identity in snapshot.get("identities") or []:
        title = platform_title(identity.get("platform"))
        if title not in linked:
            linked.append(title)
    linked_text = ", ".join(linked) if linked else "пока нет"
    return (
        "⚙️ Настройки канала\n\n"
        f"Предпочтительный мессенджер: {current}\n"
        f"Подключённые каналы: {linked_text}\n\n"
        "Чтобы сменить приоритет, отправьте одну из команд:\n"
        "/platform telegram\n"
        "/platform max\n"
        "/platform vk\n\n"
        "Чтобы привязать ещё один мессенджер к этому же профилю без потери прогресса, отправьте: switch\n\n"
        f"{describe_delivery_preferences(int(user_id))}"
    )


def _share_text(user_id: int) -> str:
    targets = build_messenger_targets(int(user_id))

    lines = [
        "↗️ Поделиться Метротерапией",
        "",
        "В VK нельзя надёжно открыть системный выбор друзей прямо из кнопки бота.",
        "Поэтому я подготовил готовый текст: его можно скопировать и отправить человеку в VK, Telegram или любом другом мессенджере.",
        "",
        "Текст для пересылки:",
        "",
        "🌿 Попробуй Метротерапию — короткие аудиопрактики для спокойствия, сна и восстановления.",
        "Начать можно здесь: https://vk.com/im?sel=-238191212",
        "",
        "Кнопки ниже открывают VK-бота, Telegram и сайт.",
    ]

    if targets:
        lines.append("")
        lines.append("Дополнительные ссылки:")
        for item in targets:
            lines.append(f"• {item['title']}: {item['url']}")

    return "\n".join(lines)


def _payment_public_base_url() -> str:
    base = (
        getattr(settings, "PAYMENT_PUBLIC_BASE_URL", "")
        or getattr(settings, "MESSENGER_PUBLIC_BASE_URL", "")
        or "https://metrotherapy-bot.metrotherapy.ru"
    )
    return str(base).strip().rstrip("/")


def _payment_url(user_id: int, *, platform: str, external_user_id: str | None, kind: str) -> str:
    public_id = (external_user_id or "").strip() or str(user_id)
    params = urllib.parse.urlencode(
        {
            "source": platform or "messenger",
            "user_id": public_id,
            "kind": kind,
        }
    )
    return f"{_payment_public_base_url()}/pay/yookassa?{params}"


def _payment_text(user_id: int, *, platform: str, external_user_id: str | None) -> str:
    url = _payment_url(
        int(user_id),
        platform=platform,
        external_user_id=external_user_id,
        kind="subscription",
    )
    return (
        "💳 Оплата доступа к Метротерапии\n\n"
        "Нажмите ссылку ниже, чтобы открыть безопасную оплату YooKassa:\n"
        f"{url}\n\n"
        "После оплаты вернитесь сюда и нажмите «🎧 Получить аудио»."
    )


def _gift_text(user_id: int, *, platform: str, external_user_id: str | None) -> str:
    gift_payment_url = _payment_url(
        int(user_id),
        platform=platform,
        external_user_id=external_user_id,
        kind="gift",
    )
    share_url = (
        "https://vk.com/share.php?"
        + urllib.parse.urlencode(
            {
                "url": "https://metrotherapy.ru",
                "title": "Метротерапия",
                "comment": "Дарю тебе короткую аудиопрактику Метротерапии.",
            }
        )
    )
    return (
        "🎁 Подарить Метротерапию\n\n"
        "1. Сначала оплатите подарок по ссылке:\n"
        f"{gift_payment_url}\n\n"
        "2. Потом отправьте человеку ссылку на проект:\n"
        f"{share_url}\n\n"
        "Позже можно усилить это до полноценного выбора друга внутри VK, "
        "но сейчас это рабочий безопасный контур: оплата + ссылка для передачи."
    )


def _switch_text(user_id: int) -> str:
    token = issue_bridge_token(int(user_id))
    targets = build_switch_targets(token)
    if not targets:
        return (
            "🔁 Переключение между мессенджерами пока не настроено ссылками.\n\n"
            "Нужно задать TELEGRAM_BOT_USERNAME, MAX_BOT_LINK_BASE/MAX_BOT_NAME и VK_GROUP_ID."
        )
    lines = [
        "🔁 Продолжить в другом мессенджере без потери прогресса можно по ссылкам ниже:",
        "",
    ]
    for item in targets:
        lines.append(f"• {item['title']}: {item['url']}")
    lines.append("")
    lines.append("После входа по одной из этих ссылок новый мессенджер привяжется к вашему текущему профилю.")
    lines.append("Дальше просто отправьте: continue — и система пришлёт текущее или следующее аудио общей очереди.")
    return "\n".join(lines)


def _bridge_linked_text(user_id: int, platform: str) -> str:
    snapshot = get_progress_snapshot(int(user_id))
    current = platform_title(platform)
    if snapshot.pending_item is not None:
        tail = f"У вас уже выдано, но ещё не открыто аудио №{snapshot.pending_item.anchor} — {snapshot.pending_item.title}. Сейчас пришлю его в этом мессенджере без дублей."
    elif snapshot.last_anchor is None and snapshot.next_item is not None:
        tail = f"Следующим будет №{snapshot.next_item.anchor} — {snapshot.next_item.title}. Сейчас пришлю его сюда."
    elif snapshot.next_item is not None:
        tail = f"Вы уже дошли до №{snapshot.last_anchor} — {snapshot.last_title}. Следующим будет №{snapshot.next_item.anchor} — {snapshot.next_item.title}. Сейчас пришлю его сюда."
    else:
        tail = "Основная серия уже дослушана до конца."
    return f"✅ {current} привязан к вашему существующему профилю.\n\n{tail}"


def _should_auto_resume_after_bridge(user_id: int) -> bool:
    snapshot = get_progress_snapshot(int(user_id))
    return snapshot.pending_item is not None or snapshot.next_item is not None


def _progress_text(user_id: int) -> str:
    snapshot = get_progress_snapshot(int(user_id))
    pending_tail = ''
    if snapshot.pending_item is not None:
        pending_tail = (
            f"\n\n⏳ Уже отправлено, но ещё не подтверждено открытием: "
            f"№{snapshot.pending_item.anchor} — {snapshot.pending_item.title} "
            f"({platform_title(snapshot.pending_platform)})."
        )
    if snapshot.last_anchor is None:
        if snapshot.next_item is None:
            audio_part = "🎧 Аудиосерия пока не найдена в каталоге."
        else:
            audio_part = (
                "🎧 Вы ещё не запускали общую очередь аудио.\n\n"
                f"Следующим будет №{snapshot.next_item.anchor} — {snapshot.next_item.title}."
                f"{pending_tail}"
            )
    else:
        tail = f"Следующим будет №{snapshot.next_item.anchor} — {snapshot.next_item.title}." if snapshot.next_item else "Серия уже дослушана до конца."
        channel = platform_title(snapshot.last_platform)
        audio_part = (
            "🎧 Общий прогресс аудио\n\n"
            f"Последнее подтверждённое аудио: №{snapshot.last_anchor} — {snapshot.last_title}\n"
            f"Подтверждено в канале: {channel}\n\n"
            f"{tail}{pending_tail}"
        )
    return (
        f"{audio_part}\n\n"
        "📈 Анализ состояния\n\n"
        "Сейчас пришлю графики по тем же данным, что используются в Telegram: "
        "быстрая шкала состояния, дорога на работу, дорога домой и общая динамика — если по ним уже есть данные.\n\n"
        "Чтобы добавить новую оценку состояния, отправьте число от -10 до 10 после прослушивания аудио."
    )


def _history_text(user_id: int) -> str:
    events = get_recent_audio_timeline(int(user_id), sequence_key=SEQUENCE_FULL_SERIES, limit=8)
    if not events:
        return "🧾 История аудио и переходов пока пуста."
    labels = {
        'bridge_linked': 'перешёл в другой мессенджер',
        'issued_pending': 'выдано следующее аудио',
        'reused_pending': 'повторно показано уже выданное аудио',
        'link_sent': 'отправлена ссылка на аудио',
        'access_confirmed': 'аудио открыто и подтверждено',
        'confirmed_delivery': 'аудио подтверждено доставкой',
        'telegram_sent': 'аудио отправлено в Telegram',
        'native_audio_sent': 'аудио отправлено как вложение',
        'native_audio_fallback': 'native-вложение недоступно, использована ссылка',
        'manual_confirmed': 'аудио подтверждено вручную',
        'pre_score_received': 'оценка до прослушивания сохранена',
        'post_score_received': 'оценка после прослушивания сохранена',
    }
    lines = ["🧾 Последние шаги по общей аудио-очереди:", ""]
    for event in events:
        label = labels.get(event.event_type, event.event_type)
        piece = f"• {event.created_at}: {label}"
        if event.anchor is not None:
            piece += f" — №{event.anchor}"
        if event.title:
            piece += f" — {event.title}"
        if event.platform:
            piece += f" ({platform_title(event.platform)})"
        lines.append(piece)
    return "\n".join(lines)


def _help_text() -> str:
    return (
        "Подсказка по мульти-мессенджерному режиму\n\n"
        "• /start или start — регистрация входа и меню\n"
        "• settings — посмотреть каналовые настройки\n"
        "• /platform telegram|max|vk — выбрать приоритетный мессенджер\n"
        "• share — получить ссылки для рекламы и рекомендаций\n"
        "• switch — привязать другой мессенджер к этому же профилю\n"
        "• continue — прислать текущее/следующее аудио общей очереди\n"
        "• done — подтвердить, что текущее аудио дослушано\n"
        "• число от -10 до 10 — сохранить оценку до/после прослушивания\n"
        "• progress — показать, где вы остановились\n"
        "• history — показать недавнюю историю переходов и аудио\n"
        "• time — показать время отправки, часовой пояс и тихие часы\n"
        "• timezone Europe/Amsterdam — сменить часовой пояс\n"
        "• quiet 22:00-08:00 — задать тихие часы, quiet off — выключить\n"
        "• channel morning max — выбрать канал для утренних отправок\n"
        "• channel evening auto — вернуть авто-выбор\n\n"
        "Очередь аудио общая для Telegram, MAX и ВКонтакте, если мессенджеры привязаны к одному профилю через switch-ссылки. "
        "Для VK и MAX можно явно написать done / готово / прослушал, когда трек дослушан, а затем отправить число от -10 до 10 как оценку после прослушивания."
    )


def _demo_text() -> str:
    return (
        "🌿 Бесплатная практика\n\n"
        "Выберите короткий маршрут — как в Telegram.\n\n"
        "1️⃣ Утро / дорога — мягко включиться в день.\n"
        "2️⃣ Вечер / домой — снять напряжение и завершить день спокойнее.\n\n"
        "Нажмите кнопку ниже или отправьте цифру: 1 или 2.\n\n"
        "После аудио нажмите «✅ Прослушал», затем отправьте оценку от -10 до 10.\n"
        "Telegram для этого не нужен — сценарий исполняется внутри ВКонтакте."
    )


def _full_route_text(user_id: int) -> str:
    snapshot = get_progress_snapshot(int(user_id))
    if snapshot.pending_item is not None:
        current = f"Сейчас ожидает подтверждения аудио №{snapshot.pending_item.anchor} — {snapshot.pending_item.title}."
    elif snapshot.next_item is not None:
        current = f"Следующим будет аудио №{snapshot.next_item.anchor} — {snapshot.next_item.title}."
    else:
        current = "Основная серия уже дослушана до конца."

    return (
        "🔐 Полный маршрут\n\n"
        "В Telegram эта кнопка открывает полный доступ и список треков. "
        "Во ВКонтакте маршрут исполняется через ту же общую аудио-очередь, чтобы прогресс не расходился между каналами.\n\n"
        f"{current}\n\n"
        "Нажмите «🎧 Получить аудио», чтобы продолжить полный маршрут во ВКонтакте. "
        "После прослушивания нажмите «✅ Прослушал» и отправьте оценку от -10 до 10."
    )


def _weather_text() -> str:
    return (
        "🌤 Погода\n\n"
        "В Telegram этот раздел показывает погоду и позволяет менять город. "
        "Во ВКонтакте текстовый контур уже принимает команды, но полноценный ввод города отдельной кнопкой ещё нужно довести до parity.\n\n"
        "Сейчас это безопасный экран-заглушка без перехода в Telegram. "
        "Следующий шаг parity: добавить VK-команду смены города и общий weather-сервис для VK."
    )


def _platform_changed_text(user_id: int, platform: str) -> str:
    set_preferred_platform(int(user_id), platform)
    return f"Сохранено: приоритетный канал — {platform_title(platform)}."


def _parse_command(text: str) -> tuple[str, str | None]:
    raw = (text or "").strip()
    if not raw:
        return "menu", None
    lowered = raw.lower()
    if lowered.startswith("/start"):
        parts = raw.split(maxsplit=1)
        return "start", parts[1].strip() if len(parts) == 2 else ""
    if lowered in {"start", "menu", "/menu", "/start"}:
        return "menu", None
    if lowered in {"help", "/help", "помощь"}:
        return "help", None
    if lowered in {"settings", "/settings", "настройки"}:
        return "settings", None
    if lowered in {"share", "/share", "пригласить", "поделиться"}:
        return "share", None
    if lowered in {"switch", "/switch", "сменить канал", "другой мессенджер"}:
        return "switch", None
    if lowered in {"continue", "/continue", "next", "/next", "audio", "/audio", "следующее аудио"}:
        return "continue", None
    if lowered in {"done", "/done", "готово", "прослушал", "дослушал", "listen done"}:
        return "done", None
    if lowered in {"progress", "/progress", "где остановился", "прогресс"}:
        return "progress", None
    if lowered in {"history", "/history", "timeline", "/timeline", "история", "🧾 история"}:
        return "history", None
    if lowered in {"time", "/time", "schedule", "/schedule", "время", "расписание"}:
        return "time", None
    if lowered.startswith("timezone ") or lowered.startswith("/timezone "):
        parts = raw.replace("/timezone", "timezone", 1).split(maxsplit=1)
        return "timezone", parts[1].strip() if len(parts) == 2 else ""
    if lowered.startswith("quiet ") or lowered.startswith("/quiet "):
        parts = raw.replace("/quiet", "quiet", 1).split(maxsplit=1)
        return "quiet", parts[1].strip() if len(parts) == 2 else ""
    if lowered.startswith("channel ") or lowered.startswith("/channel "):
        parts = raw.replace("/channel", "channel", 1).split(maxsplit=2)
        if len(parts) >= 3:
            return "channel", f"{parts[1].strip()} {parts[2].strip()}"
        return "channel", ""
    if lowered in {"demo", "/demo", "демо", "попробовать бесплатно", "🌿 попробовать бесплатно", "бесплатная практика"}:
        return "demo", None
    if lowered in {"demo_work", "1", "1.", "1️⃣", "1️⃣ утро / дорога", "утро", "утро / дорога", "дорога на работу", "практика на утро / дорогу"}:
        return "demo_work", None
    if lowered in {"demo_home", "2", "2.", "2️⃣", "2️⃣ вечер / домой", "вечер", "вечер / домой", "дорога домой", "практика на вечер / домой"}:
        return "demo_home", None
    if lowered in {"full", "/full", "полный маршрут", "🔐 полный маршрут", "полный доступ"}:
        return "full", None
    if lowered in {"weather", "/weather", "погода", "🌤 погода"}:
        return "weather", None
    if lowered.startswith("/platform") or lowered.startswith("platform "):
        parts = raw.replace("/platform", "platform", 1).split(maxsplit=1)
        value = parts[1].strip() if len(parts) == 2 else ""
        return "platform", value
    return "menu", None



def _vk_pre_audio_score_text(kind: str, session_id: int) -> str:
    title = "утренней практики / дороги" if kind == "work" else "вечерней практики / дороги домой"
    return (
        f"🌿 Перед аудио для {title} оцените состояние сейчас.\n\n"
        "Это тот же шаг, что и в Telegram: сначала фиксируем состояние ДО практики, "
        "потом бот отправляет аудио.\n\n"
        f"{_score_scale_text()}\n\n"
        "Нажмите число ниже от -10 до +10. После выбора оценки аудио придёт прямо во ВКонтакте."
    )


def _start_vk_pre_audio_session(user_id: int, *, kind: str) -> MessengerReply:
    snapshot = get_progress_snapshot(int(user_id))
    item = snapshot.pending_item or snapshot.next_item
    if item is None:
        return MessengerReply(
            text=(
                "✅ Все доступные аудио уже выданы.\n\n"
                "Можно нажать «📊 Прогресс» или «🧾 История». "
                "Когда появятся новые аудио, кнопка «🎧 Получить аудио» снова начнёт цикл со шкалы ДО прослушивания."
            )
        )

    day = datetime.now(timezone.utc).date().isoformat()
    slot = "morning" if kind == "work" else "evening"
    session_id = create_session(
        int(user_id),
        kind=kind,
        source="settings",
        day=day,
        slot=slot,
        scheduled_at=None,
        anchor_id=int(item.anchor),
    )

    return MessengerReply(
        text=_vk_pre_audio_score_text(kind, int(session_id)),
        meta={"vk_keyboard": "score_scale"},
    )

def handle_incoming_text(
    user_id: int,
    *,
    platform: str,
    external_user_id: str | None,
    text: str,
    username: str | None = None,
    display_name: str | None = None,
    first_name: str | None = None,
) -> tuple[int, list[MessengerReply]]:
    score_value = parse_score_text(text)
    action, value = _parse_command(text)
    if score_value is not None and action == 'menu':
        if find_pending_post_session_id(int(user_id)) is not None:
            action, value = 'post_score', str(score_value)
        elif find_pending_pre_session_id(int(user_id)) is not None:
            action, value = 'pre_score', str(score_value)
    payload = value if action == "start" else None
    entry = register_user_entry(
        int(user_id),
        platform=platform,
        external_user_id=external_user_id,
        username=username,
        display_name=display_name,
        first_name=first_name,
        start_payload=payload,
    )
    canonical_user_id = int(entry.user_id)

    command_text = (text or "").strip()
    command_norm = command_text.casefold().replace("ё", "е")

    payment_aliases = {
        "pay",
        "payment",
        "оплата",
        "оплатить",
        "платеж",
        "платёж",
        "💳 оплатить",
        "💳 оплата",
    }
    gift_aliases = {
        "gift",
        "подарить",
        "подарок",
        "🎁 подарить",
        "🎁 подарок",
    }

    if command_norm in payment_aliases:
        return canonical_user_id, [
            MessengerReply(
                text=_payment_text(
                    canonical_user_id,
                    platform=platform,
                    external_user_id=external_user_id,
                )
            )
        ]

    if command_norm in gift_aliases:
        return canonical_user_id, [
            MessengerReply(
                text=_gift_text(
                    canonical_user_id,
                    platform=platform,
                    external_user_id=external_user_id,
                )
            )
        ]

    if action in {"start", "menu"}:
        replies: list[MessengerReply] = []
        if entry.linked_via_bridge:
            replies.append(MessengerReply(text=_bridge_linked_text(canonical_user_id, platform)))
            if _should_auto_resume_after_bridge(canonical_user_id):
                replies.append(MessengerReply(kind='next_audio'))
                return canonical_user_id, replies
        replies.append(MessengerReply(text=_menu_text(canonical_user_id)))
        return canonical_user_id, replies
    if action == "help":
        return canonical_user_id, [MessengerReply(text=_help_text())]
    if action == "settings":
        return canonical_user_id, [MessengerReply(text=_settings_text(canonical_user_id))]
    if action == "demo":
        return canonical_user_id, [MessengerReply(text=_demo_text(), meta={"vk_keyboard": "demo_kind"})]
    if action in {"demo_work", "demo_home"}:
        kind = "work" if action == "demo_work" else "home"
        return canonical_user_id, [_start_vk_pre_audio_session(canonical_user_id, kind=kind)]
    if action == "share":
        return canonical_user_id, [MessengerReply(text=_share_text(canonical_user_id))]
    if action == "switch":
        return canonical_user_id, [MessengerReply(text=_switch_text(canonical_user_id))]
    if action == "continue":
        return canonical_user_id, [_start_vk_pre_audio_session(canonical_user_id, kind="work")]
    if action == "pre_score":
        return canonical_user_id, [MessengerReply(kind='auto_pre_score', meta={'score': str(value or '')})]
    if action == "post_score":
        return canonical_user_id, [MessengerReply(kind='auto_post_score', meta={'score': str(value or '')})]
    if action == "done":
        pending_post_session_id = find_pending_post_session_id(canonical_user_id)
        confirmed = confirm_pending_audio_delivery(canonical_user_id, platform=platform)

        if confirmed is None and pending_post_session_id is None:
            return canonical_user_id, [
                MessengerReply(
                    text=(
                        'ℹ️ Сейчас нет аудио, ожидающего подтверждения.\n\n'
                        'Чтобы начать новый цикл, нажмите «🎧 Получить аудио»: '
                        'сначала появится шкала ДО, потом аудио, потом кнопка «✅ Прослушал».'
                    )
                )
            ]

        if confirmed is None:
            return canonical_user_id, [
                MessengerReply(
                    text=(
                        '✅ Принял: аудио уже было отмечено как доставленное во ВКонтакте.\n\n'
                        'Теперь оцените состояние ПОСЛЕ прослушивания.\n'
                        f'{_score_scale_text()}'
                    ),
                    meta={'vk_keyboard': 'score_scale'},
                ),
            ]

        return canonical_user_id, [
            MessengerReply(
                text=(
                    f'✅ Подтвердил аудио №{confirmed.anchor} — {confirmed.title}.\n\n'
                    'Теперь оцените состояние ПОСЛЕ прослушивания.\n'
                    f'{_score_scale_text()}'
                ),
                meta={'vk_keyboard': 'score_scale'},
            ),
        ]
    if action == "progress":
        return canonical_user_id, [
            MessengerReply(text=_progress_text(canonical_user_id)),
            MessengerReply(kind='progress_chart'),
        ]
    if action == "history":
        return canonical_user_id, [MessengerReply(text=_history_text(canonical_user_id))]
    if action == "time":
        morning_decision = build_delivery_policy_decision(canonical_user_id, "morning")
        evening_decision = build_delivery_policy_decision(canonical_user_id, "evening")
        return canonical_user_id, [MessengerReply(text=(
            "🕒 Правила отправки\n\n" + describe_delivery_preferences(canonical_user_id)
            + f"\n\nУтреннее касание сейчас пойдёт через: {platform_title(morning_decision.resolved_channel)}"
            + (" (fallback)" if morning_decision.fallback_used else "")
            + f"\nВечернее касание сейчас пойдёт через: {platform_title(evening_decision.resolved_channel)}"
            + (" (fallback)" if evening_decision.fallback_used else "")
            + "\n\nЧтобы поменять часовой пояс, отправьте timezone Europe/Amsterdam. Чтобы задать тихие часы, отправьте quiet 22:00-08:00 или quiet off. Для выбора канала: channel morning max / channel evening auto."
        ))]
    if action == "timezone":
        try:
            tz_name = set_user_timezone(canonical_user_id, value or "")
        except (ValueError, KeyError):
            return canonical_user_id, [MessengerReply(text='Пожалуйста, укажите корректный IANA timezone, например timezone Europe/Amsterdam.')]
        return canonical_user_id, [MessengerReply(text=f'✅ Часовой пояс сохранён: {tz_name}.\n\n{describe_delivery_preferences(canonical_user_id)}')]
    if action == "quiet":
        raw = (value or '').strip().lower()
        if raw in {'off', 'none', 'disable', 'выкл', 'отключить'}:
            clear_quiet_hours(canonical_user_id)
            return canonical_user_id, [MessengerReply(text=f'✅ Тихие часы выключены.\n\n{describe_delivery_preferences(canonical_user_id)}')]
        if '-' not in raw:
            return canonical_user_id, [MessengerReply(text='Укажите quiet в формате quiet 22:00-08:00 или quiet off.')]
        start_hhmm, end_hhmm = [part.strip() for part in raw.split('-', 1)]
        try:
            start_hhmm, end_hhmm = set_quiet_hours(canonical_user_id, start_hhmm, end_hhmm)
        except (ValueError, KeyError):
            return canonical_user_id, [MessengerReply(text='Не смог распознать тихие часы. Пример: quiet 22:00-08:00.')]
        return canonical_user_id, [MessengerReply(text=f'✅ Тихие часы сохранены: {start_hhmm}-{end_hhmm}.\n\n{describe_delivery_preferences(canonical_user_id)}')]
    if action == "channel":
        parts = (value or "").lower().split()
        if len(parts) != 2 or parts[0] not in {"morning", "evening"}:
            return canonical_user_id, [MessengerReply(text="Используйте: channel morning telegram|max|vk|auto или channel evening telegram|max|vk|auto.")]
        slot, platform_value = parts[0], parts[1].lower()
        if platform_value == "auto":
            set_slot_channel(canonical_user_id, slot, None)
            selected_text = "авто"
        elif platform_value in {"telegram", "max", "vk"}:
            chosen = set_slot_channel(canonical_user_id, slot, platform_value)
            selected_text = platform_title(chosen)
        else:
            return canonical_user_id, [MessengerReply(text="Допустимые каналы: telegram, max, vk, auto.")]
        decision = build_delivery_policy_decision(canonical_user_id, slot)
        note = f"\nСейчас проект фактически отправит через {platform_title(decision.resolved_channel)}."
        if decision.fallback_used:
            note += " Выбранный канал пока недоступен, поэтому сработает fallback."
        label = 'утренних' if slot == 'morning' else 'вечерних'
        return canonical_user_id, [MessengerReply(text=f"✅ Канал для {label} отправок обновлён: {selected_text}.\n\n{describe_delivery_preferences(canonical_user_id)}{note}")]
    if action == "full":
        return canonical_user_id, [MessengerReply(text=_full_route_text(canonical_user_id))]
    if action == "weather":
        return canonical_user_id, [MessengerReply(text=_weather_text())]
    if action == "platform":
        raw_platform = (value or "").strip().lower()
        if raw_platform not in {"telegram", "max", "vk"}:
            return canonical_user_id, [MessengerReply(text="Используйте: /platform telegram | /platform max | /platform vk.")]
        norm = normalize_platform(raw_platform)
        return canonical_user_id, [MessengerReply(text=_platform_changed_text(canonical_user_id, norm)), MessengerReply(text=_settings_text(canonical_user_id))]
    return canonical_user_id, [MessengerReply(text=_menu_text(canonical_user_id))]
