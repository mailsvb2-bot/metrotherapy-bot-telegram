from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass

from services.metrotherapy_creative_experiments import stable_marketing_experiment_id
from services.visual_creative_gateway import (
    VisualCreativeBrief,
    VisualCreativeJob,
    poll_visual,
    submit_visual,
)
from services.visual_creative_render_gateway import VisualRenderPack, render_visual_pack

_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_ANGLES = (
    (
        "night_city",
        "quiet night city reflections, soft metro motion, human-scale cinematic distance",
    ),
    (
        "nature_breath",
        "calm natural environment, air, water or forest rhythm, spacious composition",
    ),
    (
        "warm_human",
        "warm contemporary interior or everyday human-scale scene, grounded and non-clinical",
    ),
)


@dataclass(frozen=True, slots=True)
class MetrotherapyBrandDNA:
    primary_color: str = "#141A24"
    accent_color: str = "#D9B56D"
    text_color: str = "#FFFFFF"
    visual_keywords: tuple[str, ...] = (
        "atmospheric",
        "immersive",
        "contemporary",
        "warm",
        "non-clinical",
    )
    forbidden_visuals: tuple[str, ...] = (
        "medical procedure imagery",
        "diagnosis labels",
        "coercion",
        "hypnosis control clichés",
        "miraculous transformation",
        "guaranteed treatment outcomes",
        "frightening imagery",
    )

    def normalized(self) -> "MetrotherapyBrandDNA":
        return MetrotherapyBrandDNA(
            primary_color=_color(self.primary_color),
            accent_color=_color(self.accent_color),
            text_color=_color(self.text_color),
            visual_keywords=_tokens(self.visual_keywords, 12),
            forbidden_visuals=_tokens(self.forbidden_visuals, 16),
        )

    @classmethod
    def from_environment(cls) -> "MetrotherapyBrandDNA":
        return cls(
            primary_color=os.getenv("METRO_CREATIVE_PRIMARY_COLOR", "#141A24"),
            accent_color=os.getenv("METRO_CREATIVE_ACCENT_COLOR", "#D9B56D"),
            text_color=os.getenv("METRO_CREATIVE_TEXT_COLOR", "#FFFFFF"),
        ).normalized()

    def prompt_context(self) -> str:
        value = self.normalized()
        return (
            "Metrotherapy visual identity. Visual language: "
            + ", ".join(value.visual_keywords)
            + ". Never show: "
            + ", ".join(value.forbidden_visuals)
            + "."
        )[:2500]

    def render_brand(self) -> dict[str, str]:
        value = self.normalized()
        return {
            "primary_color": value.primary_color,
            "accent_color": value.accent_color,
            "text_color": value.text_color,
        }


@dataclass(frozen=True, slots=True)
class MetrotherapyStudioVariant:
    experiment_id: str
    variant_id: str
    angle_id: str
    label: str
    concept: str
    kind: str
    brief: VisualCreativeBrief
    formats: tuple[str, ...]
    composition: dict[str, object]
    safety_score: int
    safety_issues: tuple[str, ...]
    country_code: str = ""


def build_metrotherapy_studio_variants(
    concept: str,
    *,
    kind: str = "image",
    headline: str = "Метротерапия",
    cta: str = "Открыть",
    brand: MetrotherapyBrandDNA | None = None,
    formats: tuple[str, ...] = ("feed", "story", "square"),
    country_code: str = "",
) -> tuple[MetrotherapyStudioVariant, ...]:
    clean_concept = " ".join(str(concept or "").split())[:1200]
    if not clean_concept:
        raise ValueError("concept is required")
    visual_kind = str(kind or "image").strip().lower()
    if visual_kind not in {"image", "video"}:
        raise ValueError("kind must be image or video")

    selected_formats = _formats(formats)
    active_brand = (brand or MetrotherapyBrandDNA.from_environment()).normalized()
    context = active_brand.prompt_context()
    title = " ".join(str(headline or "").split())[:160]
    action = " ".join(str(cta or "").split())[:80]
    country = str(country_code or "").strip().upper()
    if country and (len(country) != 2 or not country.isalpha()):
        raise ValueError("invalid_creative_country_code")

    experiment_id = stable_marketing_experiment_id(
        concept=clean_concept,
        kind=visual_kind,
        country_code=country,
    )
    variants: list[MetrotherapyStudioVariant] = []
    for index, (angle_id, direction) in enumerate(_ANGLES, start=1):
        prompt = (
            f"Create an atmospheric Metrotherapy marketing/content visual for concept: {clean_concept}. "
            f"Art direction: {direction}. {context} "
            "Do not depict a medical procedure, diagnosis, mind control, guaranteed cure, miracle, fear or coercion. "
            "Do not render readable promotional typography in the generated pixels; leave a clean safe area "
            "for deterministic composition."
        )[:12000]
        brief = VisualCreativeBrief(
            kind=visual_kind,
            prompt=prompt,
            aspect_ratio="9:16" if visual_kind == "video" else "4:5",
            duration_seconds=8 if visual_kind == "video" else 5,
            brand_context=context,
            country_code=country,
        )
        composition: dict[str, object] = {
            "headline": title,
            "body": clean_concept[:500],
            "cta": action,
            "layout": "minimal_bottom" if index == 1 else "lower_card",
            "brand": active_brand.render_brand(),
        }
        variant_id = _stable_variant_id(
            experiment_id=experiment_id,
            angle_id=angle_id,
            brief=brief,
            formats=selected_formats,
            composition=composition,
        )
        score, issues = _safety_preflight(clean_concept, prompt)
        variants.append(
            MetrotherapyStudioVariant(
                experiment_id=experiment_id,
                variant_id=variant_id,
                angle_id=angle_id,
                label=f"Вариант {index}",
                concept=clean_concept,
                kind=visual_kind,
                brief=brief,
                formats=selected_formats,
                composition=composition,
                safety_score=score,
                safety_issues=issues,
                country_code=country,
            )
        )
    return tuple(variants)


def submit_metrotherapy_studio_variant(
    variant: MetrotherapyStudioVariant,
    *,
    staff_user_id: int,
    wait_seconds: int = 0,
) -> tuple[VisualCreativeJob, VisualRenderPack | None]:
    if variant.safety_score < 70 or variant.safety_issues:
        raise ValueError("unsafe_metrotherapy_creative_variant")
    scope_id = _staff_scope(staff_user_id)
    job = submit_visual(
        variant.brief,
        scope_id=scope_id,
        idempotency_key=f"metrotherapy:{variant.variant_id}:generate",
        wait_seconds=wait_seconds,
    )
    return job, _render_when_ready(job, variant)


def poll_metrotherapy_studio_variant(
    job: VisualCreativeJob,
    variant: MetrotherapyStudioVariant,
    *,
    staff_user_id: int,
) -> tuple[VisualCreativeJob, VisualRenderPack | None]:
    scope_id = _staff_scope(staff_user_id)
    if job.scope_id != scope_id:
        raise ValueError("creative_job_staff_scope_mismatch")
    current = poll_visual(job.id, scope_id=scope_id)
    return current, _render_when_ready(current, variant)


def _render_when_ready(
    job: VisualCreativeJob,
    variant: MetrotherapyStudioVariant,
) -> VisualRenderPack | None:
    if job.status != "succeeded" or not job.asset_ready:
        return None
    return render_visual_pack(
        job,
        formats=variant.formats,
        composition=variant.composition,
        idempotency_key=f"metrotherapy:{variant.variant_id}:render",
    )


def _stable_variant_id(
    *,
    experiment_id: str,
    angle_id: str,
    brief: VisualCreativeBrief,
    formats: tuple[str, ...],
    composition: dict[str, object],
) -> str:
    """Bind paid-generation/render idempotency to the complete normalized creative spec."""

    spec = {
        "experiment_id": experiment_id,
        "angle_id": str(angle_id),
        "brief": {
            "kind": brief.kind,
            "prompt": brief.prompt,
            "country_code": brief.country_code,
            "preferred_provider": brief.preferred_provider,
            "aspect_ratio": brief.aspect_ratio,
            "duration_seconds": brief.duration_seconds,
            "negative_prompt": brief.negative_prompt,
            "reference_url": brief.reference_url,
            "brand_context": brief.brand_context,
            "seed": brief.seed,
        },
        "formats": list(formats),
        "composition": composition,
    }
    canonical = json.dumps(
        spec,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "mtv_" + hashlib.sha256(canonical).hexdigest()[:20]


def _staff_scope(staff_user_id: int) -> str:
    value = int(staff_user_id)
    if value <= 0:
        raise ValueError("valid staff user id is required")
    return f"staff:{value}"


def _color(value: object) -> str:
    token = str(value or "").strip().upper()
    if _COLOR_RE.fullmatch(token) is None:
        raise ValueError("invalid_metrotherapy_brand_color")
    return token


def _tokens(values: tuple[str, ...], maximum: int) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = " ".join(str(value or "").split())[:120]
        folded = token.casefold()
        if token and folded not in seen:
            out.append(token)
            seen.add(folded)
        if len(out) >= maximum:
            break
    return tuple(out)


def _formats(values: tuple[str, ...]) -> tuple[str, ...]:
    allowed = {"square", "feed", "story", "landscape"}
    out: list[str] = []
    for value in values:
        token = str(value or "").strip().lower()
        if token not in allowed:
            raise ValueError("invalid_creative_format")
        if token not in out:
            out.append(token)
    if not out or len(out) > len(allowed):
        raise ValueError("creative_formats_required")
    return tuple(out)


def _safety_preflight(concept: str, prompt: str) -> tuple[int, tuple[str, ...]]:
    low = concept.casefold()
    issues: list[str] = []
    score = 100
    risky = (
        "гарантированно вылеч",
        "100% вылеч",
        "гарантия лечения",
        "исцелит навсегда",
        "контроль сознания",
        "подчинение",
        "диагноз:",
        "guaranteed cure",
        "100% cure",
        "guaranteed treatment",
        "mind control",
        "forced submission",
    )
    if any(token in low for token in risky):
        score -= 70
        issues.append("unsafe_therapeutic_or_coercive_claim")
    if "do not depict a medical procedure" not in prompt.lower():
        score -= 30
        issues.append("safety_prompt_contract_missing")
    return max(0, score), tuple(issues)


__all__ = [
    "MetrotherapyBrandDNA",
    "MetrotherapyStudioVariant",
    "build_metrotherapy_studio_variants",
    "poll_metrotherapy_studio_variant",
    "submit_metrotherapy_studio_variant",
]
