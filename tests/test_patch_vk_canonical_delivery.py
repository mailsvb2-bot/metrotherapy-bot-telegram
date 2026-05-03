from scripts.patch_vk_canonical_delivery import apply_patch


RUNTIME_TEMPLATE = '''from services.messenger.webhook_dedupe import register_inbound_event
from interfaces.messaging.legacy_bridge import messenger_reply_to_canonical
from interfaces.messaging.max.delivery import send_canonical_max_response

async def _send_reply_bundle(platform, external_user_id, canonical_user_id, replies):
    for reply in replies:
        if reply.kind == 'text':
            kwargs: dict[str, Any] = {}
            if platform == 'vk':
                keyboard_kind = (reply.meta or {}).get('vk_keyboard')
                if keyboard_kind == 'demo_kind':
                    kwargs['keyboard_json'] = _vk_demo_kind_keyboard_json()
            if platform == 'max':
                await send_canonical_max_response(
                    sender,
                    external_user_id,
                    messenger_reply_to_canonical(reply),
                )
            else:
                await sender.send_text(external_user_id, reply.text, **_with_vk_keyboard(platform, kwargs))
            continue
'''


def test_patch_vk_canonical_delivery_is_idempotent(tmp_path):
    path = tmp_path / 'messenger_webhooks.py'
    path.write_text(RUNTIME_TEMPLATE, encoding='utf-8')

    assert apply_patch(path) is True
    first = path.read_text(encoding='utf-8')

    assert 'from interfaces.messaging.vk.delivery import send_canonical_vk_response' in first
    assert "if platform == 'max':" in first
    assert "elif platform == 'vk':" in first
    assert 'send_canonical_vk_response(' in first
    assert 'messenger_reply_to_canonical(reply)' in first
    assert 'sender.send_text(external_user_id, reply.text, **_with_vk_keyboard(platform, kwargs))' in first

    assert apply_patch(path) is False
    second = path.read_text(encoding='utf-8')
    assert second == first
