from __future__ import annotations

from services.messenger.text_ui import handle_incoming_text
from services.messenger.entrypoints import register_user_entry
from services.mood import create_session
from services.mood_text_flow import find_pending_pre_session_id


def _new_user(seed: int, *, platform: str = "vk") -> tuple[int, str]:
    entry = register_user_entry(
        seed,
        platform=platform,
        external_user_id=str(seed),
        username=None,
        display_name=None,
        first_name=None,
        start_payload="",
    )
    return int(entry.user_id), str(seed)


def test_admin_panel_is_not_exposed_in_vk_or_max_menu_text():
    for platform, user_id in [("vk", -930001), ("max", -930002)]:
        canonical_user_id, replies = handle_incoming_text(
            user_id,
            platform=platform,
            external_user_id=str(user_id),
            text="start",
        )
        joined = "\n".join(reply.text for reply in replies)
        assert canonical_user_id
        assert "🛠 Панель" not in joined
        assert "Попробовать бесплатно" in joined
        assert "Полный маршрут" in joined
        assert "Погода" in joined


def test_vk_pending_pre_score_treats_plus_one_as_score_not_demo_alias():
    user_id, external_user_id = _new_user(-930101, platform="vk")
    session_id = create_session(
        user_id,
        kind="work",
        source="settings",
        day="2026-06-01",
        slot="morning",
        scheduled_at=None,
        anchor_id=1,
    )
    assert int(session_id)
    assert find_pending_pre_session_id(user_id) is not None

    canonical_user_id, replies = handle_incoming_text(
        user_id,
        platform="vk",
        external_user_id=external_user_id,
        text="+1",
    )

    assert canonical_user_id == user_id
    assert replies
    assert replies[0].kind == "auto_pre_score"
    assert replies[0].meta.get("score") == "1"


def test_max_score_payload_value_reaches_pre_score_transition():
    user_id, external_user_id = _new_user(-930201, platform="max")
    session_id = create_session(
        user_id,
        kind="home",
        source="settings",
        day="2026-06-01",
        slot="evening",
        scheduled_at=None,
        anchor_id=2,
    )
    assert int(session_id)
    assert find_pending_pre_session_id(user_id) is not None

    canonical_user_id, replies = handle_incoming_text(
        user_id,
        platform="max",
        external_user_id=external_user_id,
        text="+1",
    )

    assert canonical_user_id == user_id
    assert replies
    assert replies[0].kind == "auto_pre_score"
    assert replies[0].meta.get("score") == "1"


def test_weather_city_pending_allows_back_navigation_without_saving_city():
    user_id, external_user_id = _new_user(-930401, platform="max")

    canonical_user_id, replies = handle_incoming_text(
        user_id,
        platform="max",
        external_user_id=external_user_id,
        text="weather_city",
    )
    assert canonical_user_id == user_id
    assert replies[0].meta.get("vk_keyboard") == "weather_city"

    canonical_user_id, replies = handle_incoming_text(
        user_id,
        platform="max",
        external_user_id=external_user_id,
        text="⬅️ Меню",
    )

    assert canonical_user_id == user_id
    joined = "\n".join(reply.text for reply in replies)
    assert "Главное меню" in joined
