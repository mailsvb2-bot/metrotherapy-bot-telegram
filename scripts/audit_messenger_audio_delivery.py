from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from runtime.messenger_max_sender import MaxBotSender
from runtime.messenger_vk_sender import VkBotSender
from services.db import db
from services.messenger.audio_delivery import send_next_audio_to_user
from services.messenger.audio_progress import get_next_audio_item, get_progress_snapshot
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery, build_delivery_plan
from services.messenger.platforms import MessengerPlatform, normalize_platform


@dataclass(frozen=True)
class Candidate:
    user_id: int
    platform: str
    external_user_id: str
    last_seen_at: str | None = None
    preferred_platform: str | None = None


@dataclass(frozen=True)
class CandidateAudit:
    user_id: int
    platform: str
    external_user_id: str
    eligible: bool
    status: str
    next_anchor: int | None = None
    pending_anchor: int | None = None
    problem: str = ""


@dataclass(frozen=True)
class SendResult:
    user_id: int
    platform: str
    external_user_id: str
    ok: bool
    dry_run: bool
    status: str
    transport: str = ""
    anchor: int | None = None
    problem: str = ""


def _parse_platforms(raw: str) -> tuple[str, ...]:
    values = tuple(part.strip().lower() for part in str(raw or "").split(",") if part.strip())
    allowed = {MessengerPlatform.VK.value, MessengerPlatform.MAX.value}
    bad = [item for item in values if item not in allowed]
    if bad:
        raise ValueError(f"unsupported_platforms:{','.join(bad)}")
    return values or (MessengerPlatform.VK.value, MessengerPlatform.MAX.value)


def load_candidates(*, platforms: tuple[str, ...], limit: int, only_preferred: bool) -> list[Candidate]:
    placeholders = ",".join("?" for _ in platforms)
    preferred_filter = "AND COALESCE(p.preferred_platform, p.last_seen_platform, i.platform)=i.platform" if only_preferred else ""
    query = f"""
        SELECT i.user_id, i.platform, i.external_user_id, i.last_seen_at,
               COALESCE(p.preferred_platform, p.last_seen_platform, '') AS preferred_platform
        FROM user_channel_identities i
        LEFT JOIN user_channel_preferences p ON p.user_id=i.user_id
        WHERE i.platform IN ({placeholders})
          AND COALESCE(i.external_user_id, '') <> ''
          {preferred_filter}
        ORDER BY i.last_seen_at DESC, i.user_id ASC
        LIMIT ?
    """.strip()
    with db() as conn:
        rows = conn.execute(query, (*platforms, int(limit))).fetchall()
    return [
        Candidate(
            user_id=int(row["user_id"]),
            platform=normalize_platform(row["platform"]),
            external_user_id=str(row["external_user_id"] or ""),
            last_seen_at=row["last_seen_at"],
            preferred_platform=str(row["preferred_platform"] or "") or None,
        )
        for row in rows
    ]


def audit_candidate(candidate: Candidate) -> CandidateAudit:
    try:
        plan = build_delivery_plan(candidate.user_id, fallback=candidate.platform, preferred_platform=candidate.platform)
        if plan.platform != candidate.platform:
            return CandidateAudit(candidate.user_id, candidate.platform, candidate.external_user_id, False, "platform_mismatch", problem=plan.platform)
        if not plan.external_user_id:
            return CandidateAudit(candidate.user_id, candidate.platform, candidate.external_user_id, False, "no_external_user_id")
        snapshot = get_progress_snapshot(candidate.user_id)
        pending = snapshot.pending_item
        next_item = pending or get_next_audio_item(candidate.user_id)
        if next_item is None:
            return CandidateAudit(
                candidate.user_id,
                candidate.platform,
                candidate.external_user_id,
                False,
                "queue_finished",
                pending_anchor=getattr(pending, "anchor", None),
            )
        return CandidateAudit(
            candidate.user_id,
            candidate.platform,
            candidate.external_user_id,
            True,
            "eligible",
            next_anchor=int(next_item.anchor),
            pending_anchor=int(pending.anchor) if pending else None,
        )
    except Exception as exc:  # validator: allow-wide-except
        return CandidateAudit(candidate.user_id, candidate.platform, candidate.external_user_id, False, "audit_error", problem=f"{type(exc).__name__}:{exc}")


async def send_candidate(candidate: Candidate, *, dry_run: bool, retries: int, retry_delay_sec: float) -> SendResult:
    audit = audit_candidate(candidate)
    if not audit.eligible:
        return SendResult(
            user_id=candidate.user_id,
            platform=candidate.platform,
            external_user_id=candidate.external_user_id,
            ok=False,
            dry_run=dry_run,
            status=audit.status,
            anchor=audit.next_anchor or audit.pending_anchor,
            problem=audit.problem,
        )
    if dry_run:
        return SendResult(
            user_id=candidate.user_id,
            platform=candidate.platform,
            external_user_id=candidate.external_user_id,
            ok=True,
            dry_run=True,
            status="would_send",
            anchor=audit.next_anchor,
        )

    registry = SenderRegistry(max=MaxBotSender(), vk=VkBotSender())
    attempts = max(1, int(retries) + 1)
    last_problem = ""
    for attempt in range(1, attempts + 1):
        try:
            result = await send_next_audio_to_user(
                candidate.user_id,
                senders=registry,
                target_platform=candidate.platform,
                fallback=candidate.platform,
            )
            return SendResult(
                user_id=candidate.user_id,
                platform=candidate.platform,
                external_user_id=candidate.external_user_id,
                ok=result.transport != "none",
                dry_run=False,
                status="sent" if result.transport != "none" else "not_sent",
                transport=result.transport,
                anchor=int(result.item.anchor) if result.item else None,
                problem="" if result.transport != "none" else result.message,
            )
        except (UnsupportedMessengerDelivery, RuntimeError, OSError, ValueError, TypeError) as exc:
            last_problem = f"attempt={attempt}:{type(exc).__name__}:{exc}"
            if attempt < attempts:
                await asyncio.sleep(max(0.0, float(retry_delay_sec)))
    return SendResult(
        user_id=candidate.user_id,
        platform=candidate.platform,
        external_user_id=candidate.external_user_id,
        ok=False,
        dry_run=False,
        status="failed",
        anchor=audit.next_anchor,
        problem=last_problem,
    )


async def run(
    *,
    platforms: tuple[str, ...],
    limit: int,
    only_preferred: bool,
    send: bool,
    batch_size: int,
    delay_sec: float,
    retries: int,
    retry_delay_sec: float,
) -> dict[str, Any]:
    candidates = load_candidates(platforms=platforms, limit=limit, only_preferred=only_preferred)
    audits = [audit_candidate(candidate) for candidate in candidates]
    eligible_candidates = [candidate for candidate, audit in zip(candidates, audits) if audit.eligible]

    results: list[SendResult] = []
    selected = eligible_candidates[: max(0, int(batch_size))]
    for idx, candidate in enumerate(selected):
        if idx and delay_sec > 0:
            await asyncio.sleep(delay_sec)
        results.append(await send_candidate(candidate, dry_run=not send, retries=retries, retry_delay_sec=retry_delay_sec))

    status_counts: dict[str, int] = {}
    for audit in audits:
        status_counts[audit.status] = status_counts.get(audit.status, 0) + 1
    send_status_counts: dict[str, int] = {}
    for result in results:
        send_status_counts[result.status] = send_status_counts.get(result.status, 0) + 1

    return {
        "ok": all(result.ok for result in results) if results else True,
        "mode": "send" if send else "dry_run",
        "platforms": list(platforms),
        "candidate_count": len(candidates),
        "eligible_count": len(eligible_candidates),
        "status_counts": status_counts,
        "selected_count": len(selected),
        "send_status_counts": send_status_counts,
        "audits_sample": [asdict(item) for item in audits[:20]],
        "results": [asdict(item) for item in results],
        "generated_at_epoch": int(time.time()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit and optionally batch-send VK/MAX native audio through canonical delivery path.")
    parser.add_argument("--platform", default="vk,max", help="Comma-separated: vk,max")
    parser.add_argument("--limit", type=int, default=1000, help="Max linked users to audit")
    parser.add_argument("--only-preferred", action="store_true", help="Only audit identities whose platform is preferred/last-seen")
    parser.add_argument("--send", action="store_true", help="Actually send audio to eligible users. Default is dry-run")
    parser.add_argument("--batch-size", type=int, default=10, help="How many eligible users to send/would-send in one run")
    parser.add_argument("--delay-sec", type=float, default=0.2, help="Delay between sends")
    parser.add_argument("--retries", type=int, default=1, help="Retries per recipient when --send")
    parser.add_argument("--retry-delay-sec", type=float, default=1.0, help="Delay between retries")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args(argv)

    try:
        result = asyncio.run(run(
            platforms=_parse_platforms(args.platform),
            limit=max(1, int(args.limit)),
            only_preferred=bool(args.only_preferred),
            send=bool(args.send),
            batch_size=max(0, int(args.batch_size)),
            delay_sec=max(0.0, float(args.delay_sec)),
            retries=max(0, int(args.retries)),
            retry_delay_sec=max(0.0, float(args.retry_delay_sec)),
        ))
    except (RuntimeError, ValueError) as exc:
        result = {"ok": False, "problem": str(exc)}

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
