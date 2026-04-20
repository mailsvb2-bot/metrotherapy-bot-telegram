from __future__ import annotations

from dataclasses import dataclass

from services.store import store
from services.referrals import set_referral
from services.events import log_event
from .preferences import record_channel_identity, record_channel_touch, prefer_current_platform
from .bridge import consume_bridge_token
from .platforms import normalize_platform


@dataclass(frozen=True)
class StartPayload:
    raw: str
    kind: str
    value: str | None = None


@dataclass(frozen=True)
class EntryActionResult:
    user_id: int
    platform: str
    payload: StartPayload
    referral_applied: bool = False
    linked_via_bridge: bool = False


def parse_start_payload(raw_payload: str | None) -> StartPayload:
    payload = (raw_payload or '').strip()
    if not payload:
        return StartPayload(raw='', kind='plain', value=None)
    if payload.startswith('ref_'):
        value = payload.replace('ref_', '', 1).strip()
        return StartPayload(raw=payload, kind='referral', value=value or None)
    if payload.startswith('gift_'):
        value = payload.replace('gift_', '', 1).strip()
        return StartPayload(raw=payload, kind='gift', value=value or None)
    if payload.startswith('bridge_'):
        value = payload.replace('bridge_', '', 1).strip()
        return StartPayload(raw=payload, kind='bridge', value=value or None)
    return StartPayload(raw=payload, kind='plain', value=payload)


def register_user_entry(
    user_id: int,
    *,
    platform: str,
    external_user_id: str | None,
    username: str | None = None,
    display_name: str | None = None,
    first_name: str | None = None,
    start_payload: str | None = None,
) -> EntryActionResult:
    norm = normalize_platform(platform)
    parsed = parse_start_payload(start_payload)
    canonical_user_id = int(user_id)
    linked_via_bridge = False
    if parsed.kind == 'bridge' and parsed.value:
        resolved = consume_bridge_token(parsed.value, platform=norm, external_user_id=external_user_id)
        if resolved is not None:
            canonical_user_id = int(resolved.canonical_user_id)
            linked_via_bridge = True
    store.ensure_user(int(canonical_user_id), username, first_name)
    record_channel_identity(
        int(canonical_user_id),
        norm,
        external_user_id,
        username=username,
        display_name=display_name,
    )
    record_channel_touch(int(canonical_user_id), norm)

    referral_applied = False
    if parsed.kind == 'referral' and parsed.value and parsed.value.isdigit():
        referrer_id = int(parsed.value)
        referral_applied = set_referral(referrer_id, int(canonical_user_id))
        if referral_applied:
            log_event(int(canonical_user_id), 'ref_joined', {'referrer': referrer_id, 'platform': norm})
    if linked_via_bridge:
        prefer_current_platform(int(canonical_user_id), norm)
        log_event(int(canonical_user_id), 'channel_bridge_linked', {'platform': norm})
    return EntryActionResult(user_id=int(canonical_user_id), platform=norm, payload=parsed, referral_applied=referral_applied, linked_via_bridge=linked_via_bridge)
