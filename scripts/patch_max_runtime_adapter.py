from __future__ import annotations

"""Safely wire runtime/messenger_webhooks.py to services.messenger.max_events.

The GitHub contents API rewrites whole files, which is risky for this large
runtime module. This migration script performs narrow, idempotent text patches
on a checked-out repository and refuses to continue if the expected anchors are
not found.

Run from repository root:
  python -m scripts.patch_max_runtime_adapter
"""

from pathlib import Path

RUNTIME_PATH = Path("runtime/messenger_webhooks.py")

IMPORT_ANCHOR = "from services.messenger.webhook_dedupe import register_inbound_event\n"
IMPORT_LINE = "from services.messenger.max_events import extract_max_inbound_message, max_event_key\n"

OLD_MAX_EVENT_KEY = '''def _max_event_key(payload: dict[str, Any]) -> str:\n    message = payload.get('message') or {}\n    body = message.get('body') or {}\n    parts = [\n        str(payload.get('update_id') or payload.get('event_id') or ''),\n        str(message.get('message_id') or message.get('id') or body.get('mid') or ''),\n        str((message.get('sender') or {}).get('user_id') or (message.get('sender') or {}).get('id') or ''),\n        str(message.get('created_at') or payload.get('timestamp') or ''),\n    ]\n    key = ':'.join(part for part in parts if part)\n    return key or _stable_payload_key('max', payload)\n'''

NEW_MAX_EVENT_KEY = '''def _max_event_key(payload: dict[str, Any]) -> str:\n    return max_event_key(payload)\n'''

OLD_EXTRACT_MAX_MESSAGE = '''def _extract_max_message(payload: dict[str, Any]) -> dict[str, Any] | None:\n    message = payload.get('message') or {}\n    sender = message.get('sender') or {}\n    body = message.get('body') or {}\n    user_id = sender.get('user_id') or sender.get('id')\n    safe_user_id = _safe_int(user_id)\n    if safe_user_id is None:\n        return None\n    text = (body.get('text') or '').strip()\n    full_name = ' '.join(part for part in [sender.get('first_name'), sender.get('last_name')] if part).strip() or sender.get('name')\n    return {\n        'user_id': safe_user_id,\n        'external_user_id': str(user_id),\n        'username': sender.get('username'),\n        'display_name': full_name,\n        'first_name': sender.get('first_name') or sender.get('name'),\n        'text': text or 'start',\n    }\n'''

NEW_EXTRACT_MAX_MESSAGE = '''def _extract_max_message(payload: dict[str, Any]) -> dict[str, Any] | None:\n    message = extract_max_inbound_message(payload)\n    if message is None:\n        return None\n    return {\n        'user_id': message.user_id,\n        'external_user_id': message.external_user_id,\n        'username': message.username,\n        'display_name': message.display_name,\n        'first_name': message.first_name,\n        'text': _normalise_messenger_text(message.text),\n    }\n'''


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f"Patch anchor not found for {label}; runtime file changed, review manually")
    return text.replace(old, new, 1)


def apply_patch(path: Path = RUNTIME_PATH) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    if IMPORT_LINE not in text:
        if IMPORT_ANCHOR not in text:
            raise SystemExit("Import anchor not found; runtime file changed, review manually")
        text = text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_LINE, 1)

    text = _replace_once(text, OLD_MAX_EVENT_KEY, NEW_MAX_EVENT_KEY, label="_max_event_key")
    text = _replace_once(text, OLD_EXTRACT_MAX_MESSAGE, NEW_EXTRACT_MAX_MESSAGE, label="_extract_max_message")

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> int:
    changed = apply_patch()
    print("runtime/messenger_webhooks.py patched" if changed else "runtime/messenger_webhooks.py already patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
