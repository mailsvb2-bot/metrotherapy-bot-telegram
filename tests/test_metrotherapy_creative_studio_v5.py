from __future__ import annotations

import pytest

import services.metrotherapy_creative_studio as studio
from services.metrotherapy_creative_experiments import MarketingVariantPerformance, evaluate_marketing_experiment
from services.metrotherapy_creative_studio import (
    MetrotherapyBrandDNA,
    build_metrotherapy_studio_variants,
    poll_metrotherapy_studio_variant,
    submit_metrotherapy_studio_variant,
)
from services.visual_creative_gateway import VisualCreativeJob
from services.visual_creative_render_gateway import VisualRenderPack


def test_builds_three_stable_safe_staff_variants():
    first = build_metrotherapy_studio_variants("Тихий вечер после напряжённого дня")
    second = build_metrotherapy_studio_variants("Тихий вечер после напряжённого дня")
    assert first == second
    assert len(first) == 3
    assert len({item.variant_id for item in first}) == 3
    assert len({item.experiment_id for item in first}) == 1
    assert all(item.safety_score == 100 for item in first)
    assert all("do not depict a medical procedure" in item.brief.prompt.lower() for item in first)
    assert all("do not render readable promotional typography" in item.brief.prompt.lower() for item in first)


def test_risky_therapeutic_claim_fails_before_paid_submit(monkeypatch):
    variant = build_metrotherapy_studio_variants("100% вылечит тревогу")[0]
    assert variant.safety_score < 70
    called = False

    def fake_submit(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(studio, "submit_visual", fake_submit)
    with pytest.raises(ValueError, match="unsafe_metrotherapy"):
        submit_metrotherapy_studio_variant(variant, staff_user_id=42)
    assert called is False


def test_submit_and_poll_keep_exact_staff_scope_and_stable_render_idempotency(monkeypatch):
    variant = build_metrotherapy_studio_variants("Городские огни и спокойный ритм")[0]
    calls = []

    def fake_submit(brief, *, scope_id, idempotency_key, wait_seconds=0):
        calls.append(("submit", scope_id, idempotency_key))
        return VisualCreativeJob("job1", "fake", scope_id, "image", "succeeded", asset_ready=True)

    def fake_render(job, *, formats, composition, idempotency_key):
        calls.append(("render", job.scope_id, idempotency_key))
        return VisualRenderPack("pack1", job.scope_id, job.id, "succeeded", "", ())

    def fake_poll(job_id, *, scope_id):
        calls.append(("poll", scope_id, job_id))
        return VisualCreativeJob(job_id, "fake", scope_id, "image", "succeeded", asset_ready=True)

    monkeypatch.setattr(studio, "submit_visual", fake_submit)
    monkeypatch.setattr(studio, "render_visual_pack", fake_render)
    monkeypatch.setattr(studio, "poll_visual", fake_poll)
    job, pack = submit_metrotherapy_studio_variant(variant, staff_user_id=42)
    assert job.scope_id == "staff:42"
    assert pack is not None
    assert calls[0][2] == f"metrotherapy:{variant.variant_id}:generate"
    assert calls[1][2] == f"metrotherapy:{variant.variant_id}:render"
    current, repack = poll_metrotherapy_studio_variant(job, variant, staff_user_id=42)
    assert current.scope_id == "staff:42"
    assert repack is not None
    with pytest.raises(ValueError, match="staff_scope_mismatch"):
        poll_metrotherapy_studio_variant(job, variant, staff_user_id=43)


def test_brand_colors_fail_closed():
    with pytest.raises(ValueError, match="brand_color"):
        MetrotherapyBrandDNA(primary_color="red").normalized()


def test_marketing_experiment_uses_only_observed_marketing_outcomes():
    result = evaluate_marketing_experiment(
        (
            MarketingVariantPerformance(
                "a", impressions=1000, clicks=80, opens=30, purchases=5, spend_micros=5_000_000
            ),
            MarketingVariantPerformance(
                "b", impressions=1000, clicks=70, opens=40, purchases=9, spend_micros=6_000_000
            ),
            MarketingVariantPerformance("tiny", impressions=20, clicks=15, opens=10, purchases=2),
        ),
        objective="purchase_rate",
        minimum_impressions=100,
    )
    assert result.leader == "b"
    assert result.winner is None
    assert "tiny" not in result.eligible
    assert result.reason == "observed_rate_leader_not_significant"


def test_english_coercive_or_cure_claim_is_blocked_before_provider(monkeypatch):
    variant = build_metrotherapy_studio_variants("Guaranteed cure through mind control", kind="image")[0]
    called = False

    def forbidden_submit(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(studio, "submit_visual", forbidden_submit)
    with pytest.raises(ValueError, match="unsafe_metrotherapy"):
        submit_metrotherapy_studio_variant(variant, staff_user_id=42)
    assert called is False


def test_country_route_is_bound_into_experiment_and_provider_brief():
    ru = build_metrotherapy_studio_variants("Тихий вечер", country_code="ru")[0]
    nl = build_metrotherapy_studio_variants("Тихий вечер", country_code="nl")[0]
    assert ru.experiment_id != nl.experiment_id
    assert ru.variant_id != nl.variant_id
    assert ru.brief.country_code == "RU"
    assert nl.brief.country_code == "NL"


def test_marketing_experiment_confirms_rate_winner_only_with_95_percent_separation():
    result = evaluate_marketing_experiment(
        (
            MarketingVariantPerformance("a", impressions=10000, clicks=1600, opens=900, purchases=500),
            MarketingVariantPerformance("b", impressions=10000, clicks=1000, opens=550, purchases=250),
        ),
        objective="purchase_rate",
    )
    assert result.leader == "a"
    assert result.winner == "a"
    assert result.reason == "statistically_supported_observed_rate"


def test_marketing_metrics_fail_closed_when_rate_would_exceed_one():
    with pytest.raises(ValueError, match="exceed_impressions"):
        MarketingVariantPerformance("bad", impressions=10, clicks=11, opens=2, purchases=3).normalized()


def test_marketing_metrics_do_not_invent_a_cross_channel_funnel():
    normalized = MarketingVariantPerformance(
        "email",
        impressions=100,
        clicks=10,
        opens=60,
        purchases=12,
    ).normalized()
    assert normalized.opens == 60
    assert normalized.clicks == 10
    assert normalized.purchases == 12


def test_variant_identity_binds_render_and_brand_spec():
    base = build_metrotherapy_studio_variants("Тихий вечер")[0]
    changed_cta = build_metrotherapy_studio_variants("Тихий вечер", cta="Записаться")[0]
    changed_format = build_metrotherapy_studio_variants("Тихий вечер", formats=("story",))[0]
    changed_brand = build_metrotherapy_studio_variants(
        "Тихий вечер",
        brand=MetrotherapyBrandDNA(accent_color="#112233"),
    )[0]
    assert len({base.variant_id, changed_cta.variant_id, changed_format.variant_id, changed_brand.variant_id}) == 4
    assert base.experiment_id == changed_cta.experiment_id == changed_format.experiment_id == changed_brand.experiment_id
