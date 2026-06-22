from __future__ import annotations
import asyncio

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers.admin_inline_common import AdminCtx, safe_edit
from services.messenger.timeline import (
    get_messenger_runtime_overview,
    get_messenger_stage_overview,
    get_messenger_policy_overview,
)
from services.messenger.platforms import platform_title


async def run(cb: CallbackQuery, state: FSMContext, ctx: AdminCtx, log) -> bool:
    overview, stages, policy = await asyncio.gather(
        asyncio.to_thread(get_messenger_runtime_overview),
        asyncio.to_thread(get_messenger_stage_overview),
        asyncio.to_thread(get_messenger_policy_overview),
    )
    platform_counts = overview.get('platform_counts') or {}
    stage_platforms = stages.get('per_platform') or {}
    stage_slots = stages.get('per_slot') or {}
    stage_slot_platforms = stages.get('per_slot_platform') or {}
    waiting_pre_by_slot = stages.get('waiting_pre_by_slot') or {}
    waiting_post_by_slot = stages.get('waiting_post_by_slot') or {}
    timezone_counts = policy.get('timezone_counts') or {}
    fallback_pairs = policy.get('fallback_pairs') or {}
    fallback_by_slot = policy.get('fallback_by_slot') or {}
    fallback_by_slot_platform = policy.get('fallback_by_slot_platform') or {}
    fallback_by_slot_platform_timezone = policy.get('fallback_by_slot_platform_timezone') or {}
    blocked_by_slot = policy.get('blocked_by_slot') or {}
    blocked_by_slot_platform = policy.get('blocked_by_slot_platform') or {}
    blocked_by_slot_platform_timezone = policy.get('blocked_by_slot_platform_timezone') or {}
    blocked_by_timezone = policy.get('blocked_by_timezone') or {}
    delivery_ratio = (overview['audio_accesses'] / overview['pending_audio']) if overview['pending_audio'] else 0
    lines = [
        '💬 Мультиплатформенный контур — обзор',
        '',
        f"Профилей с channel-preference: {overview['total_profiles']}",
        f"Привязанных channel-identities: {overview['linked_identities']}",
        f"Успешных bridge-переходов: {overview['bridge_links']}",
        f"Подтверждённых аудио-прогрессов: {overview['confirmed_audio']}",
        f"Pending-аудио без подтверждения: {overview['pending_audio']}",
        f"Подтверждённых audio-access открытий: {overview['audio_accesses']}",
        f"Конверсия access/pending: {delivery_ratio:.2f}",
        f"Обработанных inbound webhook events: {overview['webhook_events']}",
        '',
        'Каналы по привязанным идентичностям:',
    ]
    if platform_counts:
        for platform, count in sorted(platform_counts.items()):
            lines.append(f"• {platform_title(platform)}: {count}")
    else:
        lines.append('• пока нет данных')
    lines.extend([
        '',
        f"Ожидают pre-score прямо сейчас: {stages['waiting_pre']}",
        f"Ожидают post-score прямо сейчас: {stages['waiting_post']}",
        f"• morning: pre {int(waiting_pre_by_slot.get('morning') or 0)} / post {int(waiting_post_by_slot.get('morning') or 0)}",
        f"• evening: pre {int(waiting_pre_by_slot.get('evening') or 0)} / post {int(waiting_post_by_slot.get('evening') or 0)}",
        '',
        'Воронка по каналам (pre → audio → confirmed → post):',
    ])
    if stage_platforms:
        for platform, bucket in sorted(stage_platforms.items()):
            pre = int(bucket.get('pre_score') or 0)
            audio = int(bucket.get('audio_sent') or 0)
            confirmed = int(bucket.get('confirmed') or 0)
            post = int(bucket.get('post_score') or 0)
            lines.append(f"• {platform_title(platform)}: {pre} → {audio} → {confirmed} → {post}")
            if pre and audio < pre:
                lines.append(f"  ↳ просадка до выдачи аудио: -{pre - audio}")
            if audio and confirmed < audio:
                lines.append(f"  ↳ просадка до подтверждения: -{audio - confirmed}")
            if confirmed and post < confirmed:
                lines.append(f"  ↳ просадка до post-score: -{confirmed - post}")
    else:
        lines.append('• пока нет данных по воронке')
    lines.extend([
        '',
        'Воронка по слотам (pre → audio → confirmed → post):',
    ])
    has_slot_data = False
    for slot in ('morning', 'evening'):
        bucket = stage_slots.get(slot) or {}
        pre = int(bucket.get('pre_score') or 0)
        audio = int(bucket.get('audio_sent') or 0)
        confirmed = int(bucket.get('confirmed') or 0)
        post = int(bucket.get('post_score') or 0)
        if pre or audio or confirmed or post:
            has_slot_data = True
            slot_label = 'morning' if slot == 'morning' else 'evening'
            lines.append(f"• {slot_label}: {pre} → {audio} → {confirmed} → {post}")
            platforms = stage_slot_platforms.get(slot) or {}
            for platform, platform_bucket in sorted(platforms.items()):
                p_pre = int(platform_bucket.get('pre_score') or 0)
                p_audio = int(platform_bucket.get('audio_sent') or 0)
                p_confirmed = int(platform_bucket.get('confirmed') or 0)
                p_post = int(platform_bucket.get('post_score') or 0)
                lines.append(f"  ↳ {platform_title(platform)}: {p_pre} → {p_audio} → {p_confirmed} → {p_post}")
    if not has_slot_data:
        lines.append('• пока нет данных по слотам')
    lines.extend([
        '',
        'Fallback между каналами:',
    ])
    if fallback_pairs:
        for pair, count in sorted(fallback_pairs.items()):
            lines.append(f"• {pair}: {int(count)}")
    else:
        lines.append('• пока нет fallback-переходов')
    for slot in ('morning', 'evening'):
        lines.append(f"• {slot} fallback: {int(fallback_by_slot.get(slot) or 0)}")
        slot_pairs = fallback_by_slot_platform.get(slot) or {}
        slot_pair_timezones = fallback_by_slot_platform_timezone.get(slot) or {}
        for pair, count in sorted(slot_pairs.items()):
            lines.append(f"  ↳ {pair}: {int(count)}")
            tz_counts = slot_pair_timezones.get(pair) or {}
            for timezone_name, tz_count in sorted(tz_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
                lines.append(f"    • {timezone_name}: {int(tz_count)}")
    lines.extend([
        '',
        'Блокировки тихими часами:',
    ])
    for slot in ('morning', 'evening'):
        lines.append(f"• {slot} blocked: {int(blocked_by_slot.get(slot) or 0)}")
        slot_platforms = blocked_by_slot_platform.get(slot) or {}
        slot_platform_timezones = blocked_by_slot_platform_timezone.get(slot) or {}
        for platform, count in sorted(slot_platforms.items()):
            lines.append(f"  ↳ {platform_title(platform)}: {int(count)}")
            tz_counts = slot_platform_timezones.get(platform) or {}
            for timezone_name, tz_count in sorted(tz_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
                lines.append(f"    • {timezone_name}: {int(tz_count)}")
    if blocked_by_timezone:
        for timezone_name, count in sorted(blocked_by_timezone.items(), key=lambda item: (-int(item[1]), str(item[0]))):
            lines.append(f"  ↳ {timezone_name}: {int(count)}")
    else:
        lines.append('• пока нет quiet-hours блокировок')
    lines.extend([
        '',
        'Распределение часовых поясов:',
    ])
    if timezone_counts:
        for timezone_name, count in sorted(timezone_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))):
            lines.append(f"• {timezone_name}: {int(count)}")
    else:
        lines.append('• пока нет настроенных timezone')
    lines.extend([
        '',
        'Риск-сигналы:',
        '• высокий pending при низком audio-access означает, что ссылки выданы, но мало открываются;',
        '• рост webhook events без роста identities обычно означает шум/дубли событий;',
        '• просадка между confirmed и post-score показывает, где люди дослушали/открыли, но не завершили цикл оценкой;',
        '• рост bridge-переходов показывает реальный cross-channel use-case, а не только декоративные ссылки.',
    ])
    await safe_edit(cb, '\n'.join(lines), reply_markup=ctx.staff_kb)
    return True
