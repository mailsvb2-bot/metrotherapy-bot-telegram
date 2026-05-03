from __future__ import annotations

"""Safely wire MAX text replies through CanonicalResponse rendering.

This patch keeps runtime/messenger_webhooks.py as the runtime orchestrator, but
moves MAX text-button rendering to the new unified messaging seam:

  MessengerReply -> CanonicalResponse -> MAX renderer -> MaxBotSender

The script is intentionally narrow and idempotent. It refuses to patch if the
expected anchors are missing.
"""

from pathlib import Path

RUNTIME_PATH = Path("runtime/messenger_webhooks.py")

IMPORT_ANCHOR = "from services.messenger.webhook_dedupe import register_inbound_event\n"
IMPORT_LINES = (
    "from interfaces.messaging.legacy_bridge import messenger_reply_to_canonical\n"
    "from interfaces.messaging.max.delivery import send_canonical_max_response\n"
)

OLD_TEXT_SEND_BLOCK = '''            await sender.send_text(external_user_id, reply.text, **_with_vk_keyboard(platform, kwargs))\n            continue\n'''

NEW_TEXT_SEND_BLOCK = '''            if platform == 'max':\n                await send_canonical_max_response(\n                    sender,\n                    external_user_id,\n                    messenger_reply_to_canonical(reply),\n                )\n            else:\n                await sender.send_text(external_user_id, reply.text, **_with_vk_keyboard(platform, kwargs))\n            continue\n'''


def apply_patch(path: Path = RUNTIME_PATH) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    if IMPORT_LINES not in text:
        if IMPORT_ANCHOR not in text:
            raise SystemExit("Import anchor not found; runtime file changed, review manually")
        text = text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_LINES, 1)

    if OLD_TEXT_SEND_BLOCK in text:
        text = text.replace(OLD_TEXT_SEND_BLOCK, NEW_TEXT_SEND_BLOCK, 1)
    elif NEW_TEXT_SEND_BLOCK not in text:
        raise SystemExit("MAX text send block anchor not found; runtime file changed, review manually")

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> int:
    changed = apply_patch()
    print("runtime/messenger_webhooks.py MAX canonical delivery patched" if changed else "runtime/messenger_webhooks.py MAX canonical delivery already patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
