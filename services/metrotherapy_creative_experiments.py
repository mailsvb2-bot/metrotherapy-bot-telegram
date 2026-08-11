from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from statistics import NormalDist

_OBJECTIVES = frozenset({"ctr", "open_rate", "purchase_rate", "cost_per_purchase"})
_RATE_OBJECTIVES = frozenset({"ctr", "open_rate", "purchase_rate"})


def stable_marketing_experiment_id(*, concept: str, kind: str, country_code: str = "") -> str:
    clean = " ".join(str(concept or "").split())
    visual_kind = str(kind or "").strip().lower()
    if not clean or visual_kind not in {"image", "video"}:
        raise ValueError("metrotherapy_marketing_experiment_identity_required")
    country = str(country_code or "").strip().upper()
    if country and (len(country) != 2 or not country.isalpha()):
        raise ValueError("invalid_creative_country_code")
    suffix = f"|{country}" if country else ""
    return "mtexp_" + hashlib.sha256(f"{clean}|{visual_kind}{suffix}".encode()).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class MarketingVariantPerformance:
    variant_id: str
    impressions: int = 0
    clicks: int = 0
    opens: int = 0
    purchases: int = 0
    spend_micros: int | None = None

    def normalized(self) -> "MarketingVariantPerformance":
        variant_id = str(self.variant_id or "").strip()
        if not variant_id:
            raise ValueError("variant_id_required")
        values = tuple(
            int(value)
            for value in (self.impressions, self.clicks, self.opens, self.purchases)
        )
        if any(value < 0 for value in values):
            raise ValueError("marketing_metrics_must_be_non_negative")
        impressions, clicks, opens, purchases = values
        # Different marketing surfaces do not share one universal click/open/purchase
        # funnel (for example email opens normally exceed clicks, while view-through
        # attribution can produce a purchase without a recorded click). What is
        # universally required by this evidence model is that every observed event
        # count is bounded by the supplied exposure denominator.
        if any(value > impressions for value in (clicks, opens, purchases)):
            raise ValueError("marketing_metrics_exceed_impressions")
        spend = None if self.spend_micros is None else int(self.spend_micros)
        if spend is not None and spend < 0:
            raise ValueError("marketing_spend_must_be_non_negative")
        return MarketingVariantPerformance(
            variant_id,
            impressions,
            clicks,
            opens,
            purchases,
            spend,
        )


@dataclass(frozen=True, slots=True)
class MarketingExperimentEvidence:
    objective: str
    minimum_impressions: int
    eligible: tuple[str, ...]
    ranking: tuple[str, ...]
    values: dict[str, float | None]
    leader: str | None
    winner: str | None
    reason: str


def evaluate_marketing_experiment(
    variants: tuple[MarketingVariantPerformance, ...],
    *,
    objective: str = "ctr",
    minimum_impressions: int = 100,
) -> MarketingExperimentEvidence:
    """Evaluate observed marketing evidence only; never infer therapeutic efficacy."""

    selected = str(objective or "").strip().lower()
    if selected not in _OBJECTIVES:
        raise ValueError("unsupported_metrotherapy_marketing_objective")
    threshold = max(1, int(minimum_impressions))
    normalized = tuple(item.normalized() for item in variants)
    if len({item.variant_id for item in normalized}) != len(normalized):
        raise ValueError("duplicate_marketing_variant")

    eligible = tuple(item.variant_id for item in normalized if item.impressions >= threshold)
    values = {item.variant_id: _metric(item, selected) for item in normalized}
    rows = [
        item
        for item in normalized
        if item.variant_id in eligible and values[item.variant_id] is not None
    ]
    reverse = selected != "cost_per_purchase"
    ranking = tuple(
        item.variant_id
        for item in sorted(
            rows,
            key=lambda row: float(values[row.variant_id] or 0.0),
            reverse=reverse,
        )
    )
    leader = ranking[0] if ranking else None
    winner: str | None = None
    if not ranking:
        reason = "insufficient_observed_sample"
    elif selected not in _RATE_OBJECTIVES:
        reason = "observed_cost_leader_no_significance_claim"
    elif len(ranking) < 2:
        reason = "insufficient_comparison_sample"
    else:
        by_id = {item.variant_id: item for item in normalized}
        first = by_id[ranking[0]]
        competitors = tuple(by_id[item] for item in ranking[1:])
        if _significant_rate(first, competitors, selected):
            winner, reason = first.variant_id, "statistically_supported_observed_rate"
        else:
            reason = "observed_rate_leader_not_significant"

    return MarketingExperimentEvidence(
        selected,
        threshold,
        eligible,
        ranking,
        values,
        leader,
        winner,
        reason,
    )


def _count(item: MarketingVariantPerformance, objective: str) -> int:
    return {
        "ctr": item.clicks,
        "open_rate": item.opens,
        "purchase_rate": item.purchases,
    }[objective]


def _significant_rate(
    first: MarketingVariantPerformance,
    competitors: tuple[MarketingVariantPerformance, ...],
    objective: str,
) -> bool:
    if not competitors or first.impressions <= 0:
        return False
    critical = NormalDist().inv_cdf(1.0 - (0.025 / len(competitors)))
    x1, n1 = _count(first, objective), first.impressions
    p1 = x1 / n1
    for second in competitors:
        n2 = second.impressions
        if n2 <= 0:
            return False
        x2 = _count(second, objective)
        p2 = x2 / n2
        if p1 <= p2:
            return False
        pooled = (x1 + x2) / (n1 + n2)
        variance = pooled * (1 - pooled) * (1 / n1 + 1 / n2)
        if variance <= 0 or (p1 - p2) / math.sqrt(variance) < critical:
            return False
    return True


def _metric(item: MarketingVariantPerformance, objective: str) -> float | None:
    if objective == "ctr":
        return _ratio(item.clicks, item.impressions)
    if objective == "open_rate":
        return _ratio(item.opens, item.impressions)
    if objective == "purchase_rate":
        return _ratio(item.purchases, item.impressions)
    if item.spend_micros is None or item.purchases <= 0:
        return None
    return item.spend_micros / item.purchases


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator <= 0 else numerator / denominator


__all__ = [
    "MarketingExperimentEvidence",
    "MarketingVariantPerformance",
    "evaluate_marketing_experiment",
    "stable_marketing_experiment_id",
]
