from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from services.messenger import package_payment_ui as payment_ui


@dataclass(frozen=True)
class _Package:
    package_id: str = "practice_test"
    title: str = "Тестовый пакет"
    description: str = "Пакет для проверки идентичности оплаты"
    price_rub: int = 990


class _DbCtx:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.conn.close()
        return False


def _fake_db_with_identity(db_path, *, canonical_user_id: int, platform: str, external_user_id: str):
    def _connect() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_channel_identities(
                user_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                external_user_id TEXT,
                username TEXT,
                display_name TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT
            )
            """.strip()
        )
        conn.execute("DELETE FROM user_channel_identities")
        conn.execute(
            """
            INSERT INTO user_channel_identities(
                user_id, platform, external_user_id, username, display_name, first_seen_at, last_seen_at
            ) VALUES(?,?,?,?,?,?,?)
            """.strip(),
            (
                int(canonical_user_id),
                platform,
                external_user_id,
                None,
                None,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        return conn

    def fake_db() -> _DbCtx:
        return _DbCtx(_connect())

    return fake_db


def _first_url(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("https://"):
            return line.strip()
    raise AssertionError("no public payment url found")


def test_payment_links_bind_vk_checkout_to_existing_canonical_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(
        payment_ui,
        "db",
        _fake_db_with_identity(tmp_path / "identity.db", canonical_user_id=111, platform="vk", external_user_id="900"),
    )
    monkeypatch.setattr(payment_ui, "payment_public_base_url", lambda: "https://example.test")
    monkeypatch.setattr(payment_ui, "public_practice_packages", lambda: (_Package(),))

    link = payment_ui.package_payment_links(user_id=900, platform="vk", external_user_id="900")[0]
    params = parse_qs(urlsplit(link.url).query)

    assert params["source"] == ["vk"]
    assert params["user_id"] == ["111"]
    assert params["external_user_id"] == ["900"]
    assert params["package_id"] == ["practice_test"]


def test_gift_links_reserve_token_for_canonical_max_buyer(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_create_gift_checkout_token(
        *,
        buyer_user_id: int,
        package_id: str,
        source_platform: str = "telegram",
        recipient_hint: str = "",
    ) -> str:
        captured.update(
            {
                "buyer_user_id": int(buyer_user_id),
                "package_id": package_id,
                "source_platform": source_platform,
                "recipient_hint": recipient_hint,
            }
        )
        return "gift_" + "a" * 32

    monkeypatch.setattr(
        payment_ui,
        "db",
        _fake_db_with_identity(tmp_path / "identity.db", canonical_user_id=222, platform="max", external_user_id="910"),
    )
    monkeypatch.setattr(payment_ui, "payment_public_base_url", lambda: "https://example.test")
    monkeypatch.setattr(payment_ui, "public_practice_packages", lambda: (_Package(),))
    monkeypatch.setattr(payment_ui, "create_gift_checkout_token", fake_create_gift_checkout_token)

    text = payment_ui.gift_package_text(
        user_id=910,
        platform="max",
        external_user_id="910",
        recipient_hint="Мария из MAX",
    )
    params = parse_qs(urlsplit(_first_url(text)).query)

    assert captured == {
        "buyer_user_id": 222,
        "package_id": "practice_test",
        "source_platform": "max",
        "recipient_hint": "Мария из MAX",
    }
    assert params["source"] == ["max"]
    assert params["user_id"] == ["222"]
    assert params["external_user_id"] == ["910"]
    assert params["gift_token"] == ["gift_" + "a" * 32]
