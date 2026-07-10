from __future__ import annotations

import json
from typing import Any

from services.growth_autopilot_core import fmt_pct, money_rub_from_minor, parse_ad_spend_to_minor, pct, safe_int

_ATTR_KEYS = ("source", "campaign", "creative")
_EVENT_FIELDS = {
    "ad_click_redirect": "clicks",
    "funnel_start_command": "starts",
    "demo_sent": "demo_sent",
    "demo_ack": "demo_ack",
    "sub_menu_open": "tariff_open",
    "funnel_tariffs_command": "tariff_open",
    "payment_success": "payments",
    "gift_paid": "payments",
}


def _clean(value: Any, *, fallback: str = "unknown", limit: int = 80) -> str:
    text = str(value or "").strip().lower()
    text = " ".join(text.replace("\n", " ").replace("\r", " ").split())
    return (text or fallback)[:limit]


def _meta_from_row(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("meta") or row.get("payload") or ""
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw or "").strip()
    if not text or not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _creative_key(source: Any, campaign: Any, creative: Any) -> str:
    return "|".join([
        _clean(source),
        _clean(campaign),
        _clean(creative),
    ])


def _empty_bucket(*, source: Any, campaign: Any, creative: Any) -> dict[str, Any]:
    return {
        "source": _clean(source),
        "campaign": _clean(campaign),
        "creative": _clean(creative),
        "links": 0,
        "spend_minor_low_confidence": 0,
        "clicks": 0,
        "starts": 0,
        "demo_sent": 0,
        "demo_ack": 0,
        "tariff_open": 0,
        "payments": 0,
    }


def build_creative_diagnostics(*, ad_links: dict[str, Any], event_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build read-only source/campaign/creative diagnostics from existing evidence.

    The function is pure: no DB access, no external calls, no mutations.
    """

    buckets: dict[str, dict[str, Any]] = {}
    unattributed_events = 0

    for link in list(ad_links.get("latest") or []):
        key = _creative_key(link.get("source"), link.get("campaign"), link.get("creative"))
        bucket = buckets.setdefault(
            key,
            _empty_bucket(source=link.get("source"), campaign=link.get("campaign"), creative=link.get("creative")),
        )
        bucket["links"] += 1
        parsed_spend = link.get("parsed_spend_minor")
        if parsed_spend is None:
            parsed_spend = parse_ad_spend_to_minor(link.get("ad_spend"))
        bucket["spend_minor_low_confidence"] += safe_int(parsed_spend)

    for row in event_rows or []:
        event_name = str(row.get("name") or "")
        field = _EVENT_FIELDS.get(event_name)
        if not field:
            continue
        meta = _meta_from_row(row)
        source = meta.get("source") or meta.get("utm_source")
        campaign = meta.get("campaign") or meta.get("utm_campaign")
        creative = meta.get("creative") or meta.get("utm_creative") or meta.get("utm_content")
        if not any([source, campaign, creative]):
            unattributed_events += 1
            continue
        key = _creative_key(source, campaign, creative)
        bucket = buckets.setdefault(key, _empty_bucket(source=source, campaign=campaign, creative=creative))
        bucket[field] += 1

    items = []
    for bucket in buckets.values():
        clicks = safe_int(bucket.get("clicks"))
        starts = safe_int(bucket.get("starts"))
        demo_ack = safe_int(bucket.get("demo_ack"))
        payments = safe_int(bucket.get("payments"))
        spend_minor = safe_int(bucket.get("spend_minor_low_confidence"))
        enriched = dict(bucket)
        enriched["click_to_start_pct"] = pct(starts, clicks)
        enriched["start_to_demo_ack_pct"] = pct(demo_ack, starts)
        enriched["demo_ack_to_payment_pct"] = pct(payments, demo_ack)
        enriched["click_to_payment_pct"] = pct(payments, clicks)
        enriched["cost_per_click_minor_low_confidence"] = int(round(spend_minor / clicks)) if clicks > 0 and spend_minor > 0 else 0
        enriched["cost_per_payment_minor_low_confidence"] = int(round(spend_minor / payments)) if payments > 0 and spend_minor > 0 else 0
        items.append(enriched)

    items.sort(key=lambda x: (safe_int(x.get("payments")), safe_int(x.get("demo_ack")), safe_int(x.get("clicks"))), reverse=True)
    return {
        "items": items,
        "unattributed_events": unattributed_events,
        "tracked_creatives": len(items),
    }


def format_creative_diagnostics(summary: dict[str, Any], *, limit: int = 5) -> list[str]:
    items = list(summary.get("items") or [])
    lines = [
        "🎨 Креативы / кампании",
        f"— отслеживаемых связок: {safe_int(summary.get('tracked_creatives'))}",
        f"— событий без source/campaign/creative: {safe_int(summary.get('unattributed_events'))}",
    ]
    if not items:
        lines.append("— данных по креативам пока нет")
        return lines
    for idx, item in enumerate(items[:limit], 1):
        title = f"{item.get('source')} / {item.get('campaign')} / {item.get('creative')}"
        lines.append(
            f"{idx}. {title}: клики {safe_int(item.get('clicks'))}, /start {safe_int(item.get('starts'))} "
            f"({fmt_pct(item.get('click_to_start_pct'))}), demo_ack {safe_int(item.get('demo_ack'))}, "
            f"оплаты {safe_int(item.get('payments'))}"
        )
        cpc = safe_int(item.get("cost_per_click_minor_low_confidence"))
        cpp = safe_int(item.get("cost_per_payment_minor_low_confidence"))
        if cpc or cpp:
            lines.append(
                f"   Расход: {money_rub_from_minor(item.get('spend_minor_low_confidence'))}; "
                f"CPC≈{money_rub_from_minor(cpc)}; CPP≈{money_rub_from_minor(cpp)}"
            )
    return lines
