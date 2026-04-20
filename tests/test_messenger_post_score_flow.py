from pathlib import Path

from services.schema import init_db
from services.mood import create_session, set_pre, mark_audio_sent
from services.messenger.audio_progress import AudioProgressItem, mark_pending_audio_delivery
from services.messenger.text_ui import handle_incoming_text


def setup_module(module):
    init_db()


def test_done_with_pending_post_requests_post_score_in_text_channels():
    sid = create_session(930001, kind='work', source='auto', day='2026-04-16', slot='morning', anchor_id=11)
    assert set_pre(sid, 3)
    mark_audio_sent(sid)
    item = AudioProgressItem(ordinal=1, anchor=11, title='A11', path=Path('audio/full/a11.opus'))
    mark_pending_audio_delivery(930001, item=item, platform='max', token=None)

    canonical_user_id, replies = handle_incoming_text(930001, platform='max', external_user_id='930001', text='done')

    assert canonical_user_id == 930001
    assert len(replies) == 1
    assert 'оцените состояние после прослушивания' in replies[0].text.lower()


def test_numeric_reply_routes_to_post_score_when_post_is_pending():
    sid = create_session(930002, kind='home', source='auto', day='2026-04-16', slot='evening', anchor_id=12)
    assert set_pre(sid, -1)
    mark_audio_sent(sid)

    canonical_user_id, replies = handle_incoming_text(930002, platform='vk', external_user_id='930002', text='4')

    assert canonical_user_id == 930002
    assert replies[0].kind == 'auto_post_score'
    assert replies[0].meta['score'] == '4'
