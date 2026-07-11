from __future__ import annotations

import sqlite3
from pathlib import Path

from services import admin_ad_links
from services.migrations.admin_ad_links_v1 import apply as apply_admin_ad_links_v1


class _DbCtx:
    def __init__(self, path: Path):
        self.path = path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        assert self.conn is not None
        if exc_type is None:
            self.conn.commit()
        self.conn.close()
        return False


def _fake_db(path: Path):
    return _DbCtx(path)


def _prepare_ad_links_db(path: Path) -> None:
    with _fake_db(path) as conn:
        apply_admin_ad_links_v1(conn)


def test_build_start_payload_never_exceeds_telegram_limit():
    payload = admin_ad_links.build_start_payload(
        source="Telegram Ads",
        campaign="May Launch 2026 with an intentionally very long campaign name",
        creative="Reels #1 with another intentionally very long creative name",
        ad_spend="340 RUB monthly attribution budget",
    )

    assert len(payload) <= 64
    assert payload.startswith("src_telegram_ads")
    assert "__h_" in payload


def test_build_click_tracking_url_requires_public_base(monkeypatch):
    monkeypatch.delenv("GROWTH_CLICK_BASE_URL", raising=False)
    monkeypatch.delenv("METRO_GROWTH_CLICK_BASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)

    assert admin_ad_links.build_click_tracking_url("payload") == ""
    assert admin_ad_links.build_click_tracking_url("payload", base_url="ftp://bad") == ""
    assert admin_ad_links.build_click_tracking_url("src_a b", base_url="https://metrotherapy.ru") == "https://metrotherapy.ru/a/src_a+b"


def test_create_ad_link_persists_short_tme_payload_and_resolves_metadata(tmp_path, monkeypatch):
    path = tmp_path / "adlinks.db"
    monkeypatch.setattr(admin_ad_links, "db", lambda: _fake_db(path))
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "metrotherapybot")
    _prepare_ad_links_db(path)

    item = admin_ad_links.create_ad_link(
        "telegram_ads",
        campaign="may",
        creative="reels1",
        ad_spend="340rub",
    )

    assert item["id"] == 1
    assert item["source"] == "telegram_ads"
    assert item["payload"] == "ad_1"
    assert item["url"] == "https://t.me/metrotherapybot?start=ad_1"

    resolved = admin_ad_links.resolve_ad_link_payload("ad_1")
    assert resolved is not None
    assert resolved["utm_source"] == "telegram_ads"
    assert resolved["utm_campaign"] == "may"
    assert resolved["utm_creative"] == "reels1"
    assert resolved["ad_spend"] == "340rub"

    links = admin_ad_links.list_ad_links()
    assert len(links) == 1
    assert links[0]["url"] == item["url"]
    assert links[0]["start_payload"] == "ad_1"


def test_create_ad_link_adds_tracking_url_when_public_base_is_set(tmp_path, monkeypatch):
    path = tmp_path / "adlinks_tracking.db"
    monkeypatch.setattr(admin_ad_links, "db", lambda: _fake_db(path))
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "metrotherapybot")
    monkeypatch.setenv("GROWTH_CLICK_BASE_URL", "https://metrotherapy.ru")
    _prepare_ad_links_db(path)

    item = admin_ad_links.create_ad_link("telegram_ads", campaign="may", creative="reels1")

    assert item["tracking_url"] == "https://metrotherapy.ru/a/ad_1"
    text = admin_ad_links.format_created_ad_link(item)
    assert "Tracking-ссылка для рекламы" in text
    assert "Прямая Telegram-ссылка" in text


def test_ad_links_report_is_plain_admin_text(tmp_path, monkeypatch):
    path = tmp_path / "adlinks_report.db"
    monkeypatch.setattr(admin_ad_links, "db", lambda: _fake_db(path))
    _prepare_ad_links_db(path)
    admin_ad_links.create_ad_link("partner", campaign="may", creative="post1")

    text = admin_ad_links.format_ad_links_report(admin_ad_links.ad_links_report())

    assert "Рекламные ссылки" in text
    assert "Партнёр/посев" in text
    assert "https://t.me/" in text
