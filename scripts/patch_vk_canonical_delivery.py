from __future__ import annotations

"""Safely wire VK text replies through CanonicalResponse rendering.

This migration keeps runtime/messenger_webhooks.py as the runtime orchestrator,
but moves VK text-button rendering to the unified messaging seam:

  MessengerReply -> CanonicalResponse -> VK renderer -> VkBotSender

The patch is narrow and idempotent. It preserves the existing sender.send_text
path for non-MAX/non-VK platforms and leaves special reply kinds unchanged.
"""

from pathlib import Path

RUNTIME_PATH = Path("runtime/messenger_webhooks.py")

IMPORT_ANCHOR = "from services.messenger.webhook_dedupe import register_inbound_event\n"
IMPORT_LINES = (
    "from interfaces.messaging.legacy_bridge import messenger_reply_to_canonical\n"
    "from interfaces.messaging.max.delivery import send_canonical_max_response\n"
    "from interfaces.messaging.vk.delivery import send_canonical_vk_response\n"
)

# Existing MAX-patched text branch. VK will be added as a peer branch.
OLD_BLOCK = '''            if platform == 'max':\n                await send_canonical_max_response(\n                    sender,\n                    external_user_id,\n                    messenger_reply_to_canonical(reply),\n                )\n            else:\n                await sender.send_text(external_user_id, reply.text, **_with_vk_keyboard(platform, kwargs))\n            continue\n'''

NEW_BLOCK = '''            if platform == 'max':\n                await send_canonical_max_response(\n                    sender,\n                    external_user_id,\n                    messenger_reply_to_canonical(reply),\n                )\n            elif platform == 'vk':\n                await send_canonical_vk_response(\n                    sender,\n                    external_user_id,\n                    messenger_reply_to_canonical(reply),\n                )\n            else:\n                await sender.send_text(external_user_id, reply.text, **_with_vk_keyboard(platform, kwargs))\n            continue\n'''


def _ensure_imports(text: str) -> str:
    # Normalize previous partial imports to the full import group while keeping
    # this patch idempotent and avoiding duplicate import lines.
    for line in IMPORT_LINES.splitlines(keepends=True):
        if line not in text:
            if IMPORT_ANCHOR not in text:
                raise SystemExit("Import anchor not found; runtime file changed, review manually")
            text = text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + line, 1)
    return text


def apply_patch(path: Path = RUNTIME_PATH) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    text = _ensure_imports(text)

    if NEW_BLOCK in text:
        pass
    elif OLD_BLOCK in text:
        text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)
    else:
        raise SystemExit("VK canonical delivery anchor not found; run scripts.patch_max_canonical_delivery first or review runtime manually")

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> int:
    changed = apply_patch()
    print("runtime/messenger_webhooks.py VK canonical delivery patched" if changed else "runtime/messenger_webhooks.py VK canonical delivery already patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
