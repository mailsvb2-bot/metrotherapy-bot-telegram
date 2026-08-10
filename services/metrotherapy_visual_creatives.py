from __future__ import annotations

import os

from services.visual_creative_gateway import (
    VisualCreativeBrief,
    VisualCreativeJob,
    download_visual,
    poll_visual,
    submit_visual,
)


def build_metrotherapy_visual_brief(*, concept: str, kind: str, country_code: str = "", preferred_provider: str = "") -> VisualCreativeBrief:
    visual_kind = str(kind or "image").strip().lower()
    if visual_kind not in {"image", "video"}:
        raise ValueError("kind must be image or video")
    motion = (
        "Cinematic eight-second vertical sequence, slow smooth motion, calm final frame and clean copy space."
        if visual_kind == "video"
        else "Premium vertical advertising key visual with depth, atmospheric light and clean copy space."
    )
    prompt = (
        f"Metrotherapy advertising creative. Concept: {str(concept or '').strip()}. {motion} "
        "Mood: calm, immersive, contemporary, emotionally warm and non-clinical. "
        "No medical procedure, diagnosis, coercion, hypnosis-control cliché, miraculous transformation, guaranteed treatment outcome or frightening imagery. "
        "Prefer sensory metaphors: rain, city lights, metro motion, forest, sea, night reflections, breath-like movement and human-scale environments. "
        "Do not bake readable advertising text into pixels; typography is a separate production step."
    )
    return VisualCreativeBrief(
        kind=visual_kind,
        prompt=prompt,
        country_code=str(country_code or ""),
        preferred_provider=str(preferred_provider or ""),
        aspect_ratio="4:5" if visual_kind == "image" else "9:16",
        duration_seconds=8,
        brand_context="Metrotherapy: atmospheric, elegant, safe and non-clinical.",
    )


def visual_wait_seconds() -> int:
    raw = str(os.getenv("VISUAL_TELEGRAM_WAIT_SECONDS", "20") or "20").strip()
    try:
        value = int(raw)
    except ValueError:
        return 20
    return max(0, min(value, 60))


def create_metrotherapy_visual(*, concept: str, kind: str, scope_id: str, idempotency_key: str, country_code: str = "", preferred_provider: str = "", wait_seconds: int = 20) -> VisualCreativeJob:
    return submit_visual(
        build_metrotherapy_visual_brief(
            concept=concept,
            kind=kind,
            country_code=country_code,
            preferred_provider=preferred_provider,
        ),
        scope_id=scope_id,
        idempotency_key=idempotency_key,
        wait_seconds=max(0, min(int(wait_seconds or 0), 60)),
    )


def poll_metrotherapy_visual(*, job_id: str, scope_id: str) -> VisualCreativeJob:
    return poll_visual(job_id, scope_id=scope_id)


def materialize_metrotherapy_visual(job: VisualCreativeJob):
    return download_visual(job)


__all__ = [
    "build_metrotherapy_visual_brief",
    "create_metrotherapy_visual",
    "materialize_metrotherapy_visual",
    "poll_metrotherapy_visual",
    "visual_wait_seconds",
]
