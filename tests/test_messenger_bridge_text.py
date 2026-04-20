from services.schema import init_db
from services.messenger.bridge import issue_bridge_token
from services.messenger.text_ui import handle_incoming_text


def setup_module(module):
    init_db()


def test_bridge_start_adds_resume_hint():
    token = issue_bridge_token(7777)
    user_id, replies = handle_incoming_text(
        99001,
        platform='max',
        external_user_id='99001',
        text=f'/start bridge_{token}',
    )
    assert user_id == 7777
    texts = '\n'.join(reply.text for reply in replies)
    assert 'привязан к вашему существующему профилю' in texts
    assert 'Сейчас пришлю' in texts



def test_bridge_start_auto_resumes_next_audio():
    token = issue_bridge_token(8888)
    user_id, replies = handle_incoming_text(99002, platform='vk', external_user_id='99002', text=f'/start bridge_{token}')
    assert user_id == 8888
    assert replies
    assert replies[0].kind == 'text'
    assert 'Сейчас пришлю' in replies[0].text
    assert len(replies) >= 2
    assert replies[1].kind == 'next_audio'
