from pathlib import Path

from scripts.patch_max_runtime_adapter import apply_patch


RUNTIME_TEMPLATE = '''from services.messenger.webhook_dedupe import register_inbound_event

def _stable_payload_key(platform: str, payload: dict) -> str:
    return 'stable'

def _normalise_messenger_text(text: str) -> str:
    return text

def _safe_int(value):
    return int(value)


def _max_event_key(payload: dict[str, Any]) -> str:
    message = payload.get('message') or {}
    body = message.get('body') or {}
    parts = [
        str(payload.get('update_id') or payload.get('event_id') or ''),
        str(message.get('message_id') or message.get('id') or body.get('mid') or ''),
        str((message.get('sender') or {}).get('user_id') or (message.get('sender') or {}).get('id') or ''),
        str(message.get('created_at') or payload.get('timestamp') or ''),
    ]
    key = ':'.join(part for part in parts if part)
    return key or _stable_payload_key('max', payload)


def _extract_max_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    message = payload.get('message') or {}
    sender = message.get('sender') or {}
    body = message.get('body') or {}
    user_id = sender.get('user_id') or sender.get('id')
    safe_user_id = _safe_int(user_id)
    if safe_user_id is None:
        return None
    text = (body.get('text') or '').strip()
    full_name = ' '.join(part for part in [sender.get('first_name'), sender.get('last_name')] if part).strip() or sender.get('name')
    return {
        'user_id': safe_user_id,
        'external_user_id': str(user_id),
        'username': sender.get('username'),
        'display_name': full_name,
        'first_name': sender.get('first_name') or sender.get('name'),
        'text': text or 'start',
    }
'''


def test_patch_max_runtime_adapter_is_idempotent(tmp_path):
    path = tmp_path / 'messenger_webhooks.py'
    path.write_text(RUNTIME_TEMPLATE, encoding='utf-8')

    assert apply_patch(path) is True
    first = path.read_text(encoding='utf-8')
    assert 'from services.messenger.max_events import extract_max_inbound_message, max_event_key' in first
    assert 'return max_event_key(payload)' in first
    assert 'message = extract_max_inbound_message(payload)' in first
    assert "payload.get('message') or {}" not in first

    assert apply_patch(path) is False
    second = path.read_text(encoding='utf-8')
    assert second == first
