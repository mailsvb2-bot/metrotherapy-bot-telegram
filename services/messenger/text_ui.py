from __future__ import annotations

from dataclasses import dataclass, field

from services.personalization import get_preface
from services.delivery_preferences import (
    describe_delivery_preferences,
    set_user_timezone,
    set_quiet_hours,
    clear_quiet_hours,
    set_slot_channel,
    build_delivery_policy_decision,
)

from .bridge import issue_bridge_token
from .entrypoints import register_user_entry
from .links import build_messenger_targets, build_switch_targets
from .platforms import normalize_platform, platform_title
from .preferences import get_channel_snapshot, set_preferred_platform
from .audio_progress import get_progress_snapshot, SEQUENCE_FULL_SERIES, confirm_pending_audio_delivery
from .timeline import get_recent_audio_timeline
from services.mood_text_flow import parse_score_text, find_pending_pre_session_id, find_pending_post_session_id


@dataclass(frozen=True)
class MessengerReply:
    kind: str = 'text'
    text: str = ''
    meta: dict[str, str] = field(default_factory=dict)


def _menu_text(user_id: int) -> str:
    preface = get_preface(int(user_id), context="menu")
    return (
        f"{preface}Главное меню Метротерапии\n\n"
        "Команды:\n"
        "• start — открыть меню\n"
        "• demo — демо-режим\n"
        "• settings — настройки\n"
        "• share — рекомендации другу\n"
        "• switch — перейти в другой мессенджер без потери прогресса\n"
        "• continue — прислать текущее/следующее аудио общей очереди\n"
        "• done — подтвердить, что текущее аудио дослушано, и перейти дальше\n"
        "• progress — показать, где вы остановились\n"
        "• history — показать недавнюю историю переходов и аудио\n"
        "• time — показать правила отправки\n"
        "• timezone Europe/Amsterdam — задать свой часовой пояс\n"
        "• quiet 22:00-08:00 — задать тихие часы\n"
        "• channel morning max — канал для утренних отправок\n"
        "• channel evening auto — вернуть авто-выбор\n"
        "• help — подсказка\n\n"
        "Прогресс аудио теперь общий для подключённых мессенджеров: можно начать в одном канале и продолжить в другом."
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
    if not targets:
        return (
            "📣 Ссылки для рекомендаций пока не настроены.\n\n"
            "Проверьте TELEGRAM_BOT_USERNAME, MAX_BOT_LINK_BASE/MAX_BOT_NAME и VK_GROUP_ID в окружении."
        )
    lines = ["📣 Поделиться Метротерапией можно так:"]
    for item in targets:
        lines.append(f"• {item['title']}: {item['url']}")
    lines.append("")
    lines.append("Проект запомнит, какой мессенджер выбрал человек, и дальше будет считать его приоритетным.")
    return "\n".join(lines)


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
            return "🎧 Аудиосерия пока не найдена в каталоге."
        return (
            "🎧 Вы ещё не запускали общую очередь аудио.\n\n"
            f"Следующим будет №{snapshot.next_item.anchor} — {snapshot.next_item.title}."
            f"{pending_tail}"
        )
    tail = f"Следующим будет №{snapshot.next_item.anchor} — {snapshot.next_item.title}." if snapshot.next_item else "Серия уже дослушана до конца."
    channel = platform_title(snapshot.last_platform)
    return (
        "🎧 Общий прогресс аудио\n\n"
        f"Последнее подтверждённое аудио: №{snapshot.last_anchor} — {snapshot.last_title}\n"
        f"Подтверждено в канале: {channel}\n\n"
        f"{tail}{pending_tail}"
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
        "• done — подтвердить, что текущее аудио дослушано, и перейти дальше\n"
        "• progress — показать, где вы остановились\n"
        "• history — показать недавнюю историю переходов и аудио\n"
        "• time — показать время отправки, часовой пояс и тихие часы\n"
        "• timezone Europe/Amsterdam — сменить часовой пояс\n"
        "• quiet 22:00-08:00 — задать тихие часы, quiet off — выключить\n"
        "• channel morning max — выбрать канал для утренних отправок\n"
        "• channel evening auto — вернуть авто-выбор\n\n"
        "Очередь аудио общая для Telegram, MAX и ВКонтакте, если мессенджеры привязаны к одному профилю через switch-ссылки. Для native-аудио можно явно написать done / готово / прослушал, когда трек дослушан, а затем отправить число от -10 до 10 как оценку после прослушивания."
    )


def _demo_text() -> str:
    return (
        "🎧 Демо-режим сейчас полностью раскрыт в Telegram-ветке проекта.\n\n"
        "В MAX и ВКонтакте уже работает вход, меню, настройки канала, переход между мессенджерами и продолжение общей очереди аудио. Для rich-media сценариев Telegram остаётся самым полным каналом."
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
    if lowered in {"history", "/history", "timeline", "/timeline", "история"}:
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
    if lowered in {"demo", "/demo", "демо"}:
        return "demo", None
    if lowered.startswith("/platform") or lowered.startswith("platform "):
        parts = raw.replace("/platform", "platform", 1).split(maxsplit=1)
        value = parts[1].strip() if len(parts) == 2 else ""
        return "platform", value
    return "menu", None


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
    if action == "share":
        return canonical_user_id, [MessengerReply(text=_share_text(canonical_user_id))]
    if action == "switch":
        return canonical_user_id, [MessengerReply(text=_switch_text(canonical_user_id))]
    if action == "continue":
        return canonical_user_id, [MessengerReply(kind='next_audio')]
    if action == "pre_score":
        return canonical_user_id, [MessengerReply(kind='auto_pre_score', meta={'score': str(value or '')})]
    if action == "post_score":
        return canonical_user_id, [MessengerReply(kind='auto_post_score', meta={'score': str(value or '')})]
    if action == "done":
        confirmed = confirm_pending_audio_delivery(canonical_user_id, platform=platform)
        if confirmed is None:
            return canonical_user_id, [MessengerReply(text='ℹ️ Сейчас нет аудио, ожидающего подтверждения. Отправьте continue, чтобы получить текущее или следующее аудио.')]
        if find_pending_post_session_id(canonical_user_id) is not None:
            return canonical_user_id, [
                MessengerReply(text=(
                    f'✅ Подтвердил аудио №{confirmed.anchor} — {confirmed.title}.\n\n'
                    'Теперь оцените состояние после прослушивания числом от -10 до 10. '
                    'Просто отправьте, например: 4 или -2.'
                )),
            ]
        return canonical_user_id, [
            MessengerReply(text=f'✅ Подтвердил аудио №{confirmed.anchor} — {confirmed.title}. Отправляю дальше.'),
            MessengerReply(kind='next_audio'),
        ]
    if action == "progress":
        return canonical_user_id, [MessengerReply(text=_progress_text(canonical_user_id))]
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
    if action == "demo":
        return canonical_user_id, [MessengerReply(text=_demo_text())]
    if action == "platform":
        raw_platform = (value or "").strip().lower()
        if raw_platform not in {"telegram", "max", "vk"}:
            return canonical_user_id, [MessengerReply(text="Используйте: /platform telegram | /platform max | /platform vk.")]
        norm = normalize_platform(raw_platform)
        return canonical_user_id, [MessengerReply(text=_platform_changed_text(canonical_user_id, norm)), MessengerReply(text=_settings_text(canonical_user_id))]
    return canonical_user_id, [MessengerReply(text=_menu_text(canonical_user_id))]
