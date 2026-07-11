from __future__ import annotations

import hashlib
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
_TELEGRAM_START_MAX_LEN = 64
_SHORT_PAYLOAD_RE = re.compile(r"^ad_(\d+)$")


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


def _click_tracking_base_url() -> str:
    raw = (
        os.getenv("GROWTH_CLICK_BASE_URL")
        or os.getenv("METRO_GROWTH_CLICK_BASE_URL")
        or os.getenv("PUBLIC_BASE_URL")
        or ""
    ).strip()
    if not raw:
        return ""
    if not (raw.startswith("https://") or raw.startswith("http://")):
        return ""
    return raw.rstrip("/")


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


def _telegram_safe_payload(payload: str) -> str:
    clean = str(payload or "").strip()
    if len(clean) <= _TELEGRAM_START_MAX_LEN:
        return clean
    digest = hashlib.blake2s(clean.encode("utf-8"), digest_size=5).hexdigest()
    suffix = f"__h_{digest}"
    prefix = clean[: _TELEGRAM_START_MAX_LEN - len(suffix)].rstrip("_-")
    return f"{prefix}{suffix}"


def build_start_payload(*, source: str, campaign: str, creative: str, ad_spend: str = "") -> str:
    source = _safe_token(source, fallback="telegram_ads")
    campaign = _safe_token(campaign, fallback="campaign")
    creative = _safe_token(creative, fallback="creative")
    spend = _safe_token(ad_spend, fallback="", limit=32)
    parts = [f"src_{source}", f"camp_{campaign}", f"creative_{creative}"]
    if spend:
        parts.append(f"cost_{spend}")
    return _telegram_safe_payload("__".join(parts))


def build_start_url(payload: str, *, bot_username: str | None = None) -> str:
    username = _safe_token(bot_username or _bot_username(), fallback="metrotherapybot", limit=64)
    safe_payload = _telegram_safe_payload(str(payload or "").strip())
    return f"https://t.me/{username}?start={quote_plus(safe_payload)}"


def build_click_tracking_url(payload: str, *, base_url: str | None = None) -> str:
    base = (base_url or _click_tracking_base_url()).strip().rstrip("/")
    if not base:
        return ""
    if not (base.startswith("https://") or base.startswith("http://")):
        return ""
    return f"{base}/a/{quote_plus(str(payload or '').strip())}"


def _attach_tracking_url(item: dict[str, Any]) -> dict[str, Any]:
    payload = str(item.get("payload") or item.get("start_payload") or "")
    item["tracking_url"] = build_click_tracking_url(payload)
    return item


def _short_start_payload(link_id: int) -> str:
    if int(link_id) <= 0:
        raise ValueError("ad link id must be positive")
    return f"ad_{int(link_id)}"


def resolve_ad_link_payload(payload: str) -> dict[str, str] | None:
    """Resolve a compact Telegram start payload back to stored attribution metadata."""

    match = _SHORT_PAYLOAD_RE.fullmatch(str(payload or "").strip())
    if match is None:
        return None
    link_id = int(match.group(1))
    with db() as conn:
        try:
            row = conn.execute(
                """
                SELECT source, campaign, creative, ad_spend
                FROM admin_ad_links
                WHERE id=?
                LIMIT 1
                """.strip(),
                (link_id,),
            ).fetchone()
        except sqlite3.Error:
            return None
    item = _rowdict(row)
    if not item:
        return None
    return {
        "utm_source": str(item.get("source") or ""),
        "source": str(item.get("source") or ""),
        "utm_campaign": str(item.get("campaign") or ""),
        "campaign": str(item.get("campaign") or ""),
        "utm_creative": str(item.get("creative") or ""),
        "creative": str(item.get("creative") or ""),
        "ad_spend": str(item.get("ad_spend") or ""),
    }


def create_ad_link(source: str, *, campaign: str | None = None, creative: str | None = None, ad_spend: str = "") -> dict[str, Any]:
    now = _utc_now()
    src = _safe_token(source, fallback="telegram_ads")
    if src not in _ALLOWED_SOURCES:
        src = "telegram_ads"
    campaign_token = _safe_token(campaign or f"campaign_{now:%Y%m%d}", fallback=f"campaign_{now:%Y%m%d}")
    creative_token = _safe_token(creative or "creative_1", fallback="creative_1")
    spend = _safe_token(ad_spend, fallback="", limit=32)
    attribution_payload = build_start_payload(
        source=src,
        campaign=campaign_token,
        creative=creative_token,
        ad_spend=spend,
    )
    created_at = now.isoformat()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO admin_ad_links(source, campaign, creative, ad_spend, start_payload, url, created_at)
            VALUES(?,?,?,?,?,?,?)
            """.strip(),
            (src, campaign_token, creative_token, spend, attribution_payload, build_start_url(attribution_payload), created_at),
        )
        row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
        rid = _rowdict(row) or {}
        link_id = int(rid.get("id") or 0)
        payload = _short_start_payload(link_id)
        url = build_start_url(payload)
        conn.execute(
            "UPDATE admin_ad_links SET start_payload=?, url=? WHERE id=?",
            (payload, url, link_id),
        )

    return _attach_tracking_url({
        "id": link_id,
        "source": src,
        "source_label": _ALLOWED_SOURCES.get(src, src),
        "campaign": campaign_token,
        "creative": creative_token,
        "ad_spend": spend,
        "payload": payload,
        "url": url,
        "created_at": created_at,
    })


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
            out.append(_attach_tracking_url(item))
    return out


def ad_links_report() -> dict[str, Any]:
    links = list_ad_links(limit=8)
    return {"ok": True, "links": links, "sources": dict(_ALLOWED_SOURCES), "click_tracking_enabled": bool(_click_tracking_base_url())}


def _display_url(item: dict[str, Any]) -> str:
    return str(item.get("tracking_url") or item.get("url") or "")


def format_ad_links_report(report: dict[str, Any]) -> str:
    links = report.get("links") or []
    tracking_enabled = bool(report.get("click_tracking_enabled"))
    lines = [
        "📣 Рекламные ссылки",
        "",
        "Здесь можно создать ссылку для рекламы. Когда человек нажмёт её и потом оплатит, в карточке клиента будет видно: откуда пришёл, кампания, креатив и расход.",
        "",
        "Нажмите кнопку ниже, чтобы создать ссылку.",
    ]
    if tracking_enabled:
        lines += ["", "Click tracking: включён. В рекламу ставьте tracking-ссылку /a/<payload>."]
    else:
        lines += ["", "Click tracking: не настроен. Укажите GROWTH_CLICK_BASE_URL, чтобы считать click→start."]
    if links:
        lines += ["", "Последние ссылки:"]
        for item in links[:8]:
            spend = item.get("ad_spend") or "расход не указан"
            lines.append(
                f"#{item.get('id')} — {item.get('source_label')}: {item.get('campaign')} / {item.get('creative')} / {spend}\n{_display_url(item)}"
            )
            if item.get("tracking_url"):
                lines.append(f"Прямая Telegram-ссылка: {item.get('url')}")
    else:
        lines += ["", "Пока ссылок нет."]
    return "\n".join(lines)


def format_created_ad_link(item: dict[str, Any]) -> str:
    lines = [
        "✅ Ссылка создана",
        "",
        f"Источник: {item.get('source_label')}",
        f"Кампания: {item.get('campaign')}",
        f"Креатив: {item.get('creative')}",
        f"Расход: {item.get('ad_spend') or 'можно добавить позже в названии ссылки'}",
        "",
    ]
    if item.get("tracking_url"):
        lines += [
            "Tracking-ссылка для рекламы:",
            str(item.get("tracking_url") or ""),
            "",
            "Прямая Telegram-ссылка:",
            str(item.get("url") or ""),
        ]
    else:
        lines += [
            str(item.get("url") or ""),
            "",
            "Чтобы считать click→start, укажите GROWTH_CLICK_BASE_URL и используйте tracking-ссылку.",
        ]
    lines += ["", "Эту ссылку можно ставить в рекламу. Данные попадут в путь до оплаты у новых пользователей."]
    return "\n".join(lines)
