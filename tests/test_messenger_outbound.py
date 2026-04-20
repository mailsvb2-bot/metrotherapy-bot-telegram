from services.schema import init_db
from services.messenger import preferences as prefs
from services.messenger.outbound import build_delivery_plan


def setup_module(module):
    init_db()


def test_build_delivery_plan_prefers_selected_platform():
    prefs.record_channel_identity(7, 'telegram', 'tg7')
    prefs.record_channel_identity(7, 'vk', 'vk7')
    prefs.set_preferred_platform(7, 'vk')

    plan = build_delivery_plan(7)
    assert plan.platform == 'vk'
    assert plan.external_user_id == 'vk7'


def test_build_delivery_plan_falls_back_to_telegram_id():
    plan = build_delivery_plan(99)
    assert plan.platform == 'telegram'
    assert plan.external_user_id == '99'



def test_build_delivery_plan_can_target_current_platform():
    from services.messenger.preferences import record_channel_identity, set_preferred_platform
    from services.messenger.outbound import build_delivery_plan

    user_id = 902100
    record_channel_identity(user_id, 'telegram', 'tg-902100')
    record_channel_identity(user_id, 'vk', 'vk-902100')
    set_preferred_platform(user_id, 'telegram')

    plan = build_delivery_plan(user_id, preferred_platform='vk', fallback='vk')
    assert plan.platform == 'vk'
    assert plan.external_user_id == 'vk-902100'
