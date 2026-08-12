from __future__ import annotations

from typing import Any

import pytest

from services import practice_tokens_access_core as access
from services.practice_tokens_wallet import PracticeWallet


class _DbContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_args: Any) -> None:
        return None


def _wallet(available: int) -> PracticeWallet:
    return PracticeWallet(user_id=7, available_tokens=available, reserved_tokens=1, used_tokens=10)


def test_low_balance_warning_uses_two_days_of_single_daily_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "get_delivery_mode", lambda _uid: "single_daily")

    assert access._low_balance_warning(7, _wallet(3)) == ""
    warning = access._low_balance_warning(7, _wallet(2))

    assert "останется 2 практики" in warning
    assert "примерно на два дня" in warning
    assert "Пакеты практик" in warning


def test_low_balance_warning_adapts_to_twice_daily_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "get_delivery_mode", lambda _uid: "both")

    warning = access._low_balance_warning(7, _wallet(4))

    assert "останется 4 практики" in warning
    assert "примерно на два дня" in warning
    assert access._low_balance_warning(7, _wallet(3)) == ""


def test_low_balance_warning_paused_mode_uses_sparse_manual_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "get_delivery_mode", lambda _uid: "paused")

    warning = access._low_balance_warning(7, _wallet(2))

    assert "останется 2 практики" in warning
    assert "небольшой запас для ручного продолжения" in warning


def test_last_token_warning_does_not_depend_on_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        access,
        "get_delivery_mode",
        lambda _uid: (_ for _ in ()).throw(AssertionError("must not read preferences")),
    )

    warning = access._low_balance_warning(7, _wallet(0))

    assert "последний доступный токен" in warning
    assert "следующий аудиотранс не прервался" in warning


def test_low_balance_preference_failure_never_blocks_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        access,
        "get_delivery_mode",
        lambda _uid: (_ for _ in ()).throw(RuntimeError("preferences unavailable")),
    )

    warning = access._low_balance_warning(7, _wallet(2))

    assert "останется 2 практики" in warning


def test_successful_reservation_attaches_refill_warning_without_changing_access_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access, "enforcement_mode", lambda: "hard")
    monkeypatch.setattr(access, "token_economy_enabled", lambda: True)
    monkeypatch.setattr(access, "canonical_practice_user_id", lambda uid: int(uid))
    monkeypatch.setattr(access, "db", lambda: _DbContext())
    monkeypatch.setattr(access, "ensure_wallet", lambda *_args: None)
    monkeypatch.setattr(access, "_existing_reserved", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        access,
        "get_wallet_in_conn",
        lambda _conn, uid: PracticeWallet(int(uid), 3, 0, 10),
    )
    monkeypatch.setattr(
        access,
        "reserve_practice",
        lambda *_args, **_kwargs: (True, PracticeWallet(7, 2, 1, 10), "reservation-1"),
    )
    monkeypatch.setattr(access, "get_delivery_mode", lambda _uid: "single_daily")

    decision = access.check_and_reserve_for_audio(7, is_demo=False, audio_anchor=12)

    assert decision.allowed is True
    assert decision.mode == "hard"
    assert decision.reason == "reserved"
    assert decision.reservation_id == "reservation-1"
    assert "останется 2 практики" in decision.warning


def test_demo_path_never_adds_refill_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "enforcement_mode", lambda: "hard")
    monkeypatch.setattr(access, "token_economy_enabled", lambda: True)
    monkeypatch.setattr(
        access,
        "get_delivery_mode",
        lambda _uid: (_ for _ in ()).throw(AssertionError("demo must not inspect balance cadence")),
    )

    decision = access.check_and_reserve_for_audio(7, is_demo=True)

    assert decision.allowed is True
    assert decision.reason == "free_demo_or_disabled"
    assert decision.warning == ""
