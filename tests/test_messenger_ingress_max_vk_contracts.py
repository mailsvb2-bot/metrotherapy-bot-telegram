from __future__ import annotations

from runtime.messenger_ingress import _entry_start_text
from runtime.messenger_payloads import normalise_messenger_text


def test_vk_score_one_two_are_not_re_normalized_to_demo_routes_at_ingress() -> None:
    # The plain normalizer must keep legacy demo aliases for menu text.
    assert normalise_messenger_text("1") == "demo_work"
    assert normalise_messenger_text("2") == "demo_home"

    # The ingress layer receives already extracted VK text. Re-normalizing here
    # would convert pending score buttons 1/2 back into demo route selection.
    assert _entry_start_text("1") == "1"
    assert _entry_start_text("2") == "2"


def test_max_vk_start_payloads_are_routed_to_common_start_entrypoint() -> None:
    assert _entry_start_text("start bridge_abc") == "/start bridge_abc"
    assert _entry_start_text("/start ref_123") == "/start ref_123"
    assert _entry_start_text("bridge_token") == "/start bridge_token"
    assert _entry_start_text("ref_777") == "/start ref_777"
    assert _entry_start_text("gift_code") == "/start gift_code"


def test_non_entry_text_is_preserved_for_text_ui() -> None:
    assert _entry_start_text("pay") == "pay"
    assert _entry_start_text("+1") == "+1"
    assert _entry_start_text("weather") == "weather"
