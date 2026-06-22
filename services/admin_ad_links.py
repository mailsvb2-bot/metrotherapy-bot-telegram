from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from services.db import db

_ALLOWED_SOURCES = {
    "telegram_ads": "Telegram Ads",
    "telegram_post": "Пост в Telegram",
    "partner": "Партнёр/посев",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _safe_token(value: object, *, fallback: str, limit: int = 48) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9а-яё_\-]+", "_", text, flags=re.IGNORECASE)
    text = re.sub(r"_+", "_", text).strip("_-")
    return (text or fallback)[:limit]


def _bot_username() -> str:
    raw = (
        os.getenv("TELEGRAM_BOT_USERNAME")
        or os.getenv("BOT_USERNAME")
        or os.getenv("TELEGRAM_USERNAME")
        or "metrotherapybot"
    )
    return _safe_token(raw.replace("@", ""), fallback="metrotherapybot", limit=64)


def _rowdict(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except TypeError:
        return None
    except ValueError:
        return None


def build_start_payload(*, source: str, campaign: str, creative: str, ad_spend: str = "") -> str:
    source = _safe_token(source, fallback="telegram_ads")
    campaign = _safe_token(campaign, fallback="campaign")
    creative = _safe_token(creative, fallback="creative")
    spend = _safe_token(ad_spend, fallback="", limit=32)
    parts = [f"src_{source}", f"camp_{campaign}", f"creative_{creative}"]
    if spend:
        parts.append(f"cost_{spend}")
    return "__".join(parts)


def build_start_url(payload: str, *, bot_username: str | None = None) -> str:
    username = _safe_token(bot_username or _bot_username(), fallback="metrotherapybot", limit=64)
    return f"https://t.me/{username}?start={quote_plus(str(payload or '').strip())}"


def create_ad_link(source: str, *, campaign: str | None = None, creative: str | None = None, ad_spend: str = "") -> dict[str, Any]:
    now = _utc_now()
    src = _safe_token(source, fallback="telegram_ads")
    if src not in _ALLOWED_SOURCES:
        src = "telegram_ads"
    campaign_token = _safe_token(campaign or f"campaign_{now:%Y%m%d}", fallback=f"campaign_{now:%Y%m%d}")
    creative_token = _safe_token(creative or "creative_1", fallback="creative_1")
    spend = _safe_token(ad_spend, fallback="", limit=32)
    payload = build_start_payload(source=src, campaign=campaign_token, creative=creative_token, ad_spend=spend)
    url = build_start_url(payload)
    created_at = now.isoformat()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO admin_ad_links(source, campaign, creative, ad_spend, start_payload, url, created_at)
            VALUES(?,?,?,?,?,?,?)
            """.strip(),
            (src, campaign_token, creative_token, spend, payload, url, created_at),
        )
        row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    rid = _rowdict(row) or {}
    return {
        "id": int(rid.get("id") or 0),
        "source": src,
        "source_label": _ALLOWED_SOURCES.get(src, src),
        "campaign": campaign_token,
        "creative": creative_token,
        "ad_spend": spend,
        "payload": payload,
        "url": url,
        "created_at": created_at,
    }


def list_ad_links(*, limit: int = 10) -> list[dict[str, Any]]:
    with db() as conn:
        try:
            rows = conn.execute(
                """
                SELECT id, source, campaign, creative, ad_spend, start_payload, url, created_at
                FROM admin_ad_links
                ORDER BY id DESC
                LIMIT ?
                """.strip(),
                (int(limit),),
            ).fetchall()
        except sqlite3.Error:
            return []
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = _rowdict(row)
        if item:
            item["source_label"] = _ALLOWED_SOURCES.get(str(item.get("source") or ""), str(item.get("source") or ""))
            out.append(item)
    return out


def ad_links_report() -> dict[str, Any]:
    links = list_ad_links(limit=8)
    return {"ok": True, "links": links, "sources": dict(_ALLOWED_SOURCES)}


def format_ad_links_report(report: dict[str, Any]) -> str:
    links = report.get("links") or []
    lines = [
        "📣 Рекламные ссылки",
        "",
        "Здесь можно создать ссылку для рекламы. Когда человек нажмёт её и потом оплатит, в карточке клиента будет видно: откуда пришёл, кампания, креатив и расход.",
        "",
        "Нажмите кнопку ниже, чтобы создать ссылку.",
    ]
    if links:
        lines += ["", "Последние ссылки:"]
        for item in links[:8]:
            spend = item.get("ad_spend") or "расход не указан"
            lines.append(
                f"#{item.get('id')} — {item.get('source_label')}: {item.get('campaign')} / {item.get('creative')} / {spend}\n{item.get('url')}"
            )
    else:
        lines += ["", "Пока ссылок нет."]
    return "\n".join(lines)


def format_created_ad_link(item: dict[str, Any]) -> str:
    return "\n".join([
        "✅ Ссылка создана",
        "",
        f"Источник: {item.get('source_label')}",
        f"Кампания: {item.get('campaign')}",
        f"Креатив: {item.get('creative')}",
        f"Расход: {item.get('ad_spend') or 'можно добавить позже в названии ссылки'}",
        "",
        str(item.get("url") or ""),
        "",
        "Эту ссылку можно ставить в рекламу. Данные попадут в путь до оплаты у новых пользователей.",
    ])
