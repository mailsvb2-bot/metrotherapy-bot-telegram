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


def test_build_start_payload_is_telegram_safe():
    payload = admin_ad_links.build_start_payload(
        source="Telegram Ads",
        campaign="May Launch 2026",
        creative="Reels #1",
        ad_spend="340 RUB",
    )

    assert payload == "src_telegram_ads__camp_may_launch_2026__creative_reels_1__cost_340_rub"


def test_create_ad_link_persists_and_returns_tme_url(tmp_path, monkeypatch):
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
    assert item["payload"] == "src_telegram_ads__camp_may__creative_reels1__cost_340rub"
    assert item["url"] == "https://t.me/metrotherapybot?start=src_telegram_ads__camp_may__creative_reels1__cost_340rub"

    links = admin_ad_links.list_ad_links()
    assert len(links) == 1
    assert links[0]["url"] == item["url"]


def test_ad_links_report_is_plain_admin_text(tmp_path, monkeypatch):
    path = tmp_path / "adlinks_report.db"
    monkeypatch.setattr(admin_ad_links, "db", lambda: _fake_db(path))
    _prepare_ad_links_db(path)
    admin_ad_links.create_ad_link("partner", campaign="may", creative="post1")

    text = admin_ad_links.format_ad_links_report(admin_ad_links.ad_links_report())

    assert "Рекламные ссылки" in text
    assert "Партнёр/посев" in text
    assert "https://t.me/" in text
