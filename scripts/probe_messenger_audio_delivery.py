from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from runtime.messenger_max_sender import MaxBotSender
from runtime.messenger_vk_sender import VkBotSender
from services.audio_anchor import get_by_anchor, scan_full_anchored


@dataclass(frozen=True)
class ProbeTargetResult:
    platform: str
    ok: bool
    dry_run: bool
    external_user_id: str
    audio_path: str
    problem: str = ""
    response_type: str = ""


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _pick_audio(anchor: int | None) -> Path:
    item = get_by_anchor(int(anchor)) if anchor is not None else None
    if item is None:
        items = scan_full_anchored()
        if not items:
            raise RuntimeError("no_full_audio_files_found")
        item = items[0]
    if not item.path.exists():
        raise RuntimeError(f"audio_file_missing:{item.path}")
    return item.path


def _target_user_id(platform: str, explicit: str | None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    env_name = "VK_AUDIO_PROBE_USER_ID" if platform == "vk" else "MAX_AUDIO_PROBE_USER_ID"
    return _env(env_name)


async def _send(platform: str, external_user_id: str, audio_path: Path, *, caption: str, dry_run: bool) -> ProbeTargetResult:
    if not external_user_id:
        return ProbeTargetResult(
            platform=platform,
            ok=False,
            dry_run=dry_run,
            external_user_id="",
            audio_path=str(audio_path),
            problem=f"missing_{platform.upper()}_AUDIO_PROBE_USER_ID",
        )
    if dry_run:
        return ProbeTargetResult(
            platform=platform,
            ok=True,
            dry_run=True,
            external_user_id=external_user_id,
            audio_path=str(audio_path),
            response_type="dry_run",
        )

    sender: Any = VkBotSender() if platform == "vk" else MaxBotSender()
    try:
        response = await sender.send_audio_file(external_user_id, audio_path, caption=caption)
    except Exception as exc:  # validator: allow-wide-except
        return ProbeTargetResult(
            platform=platform,
            ok=False,
            dry_run=False,
            external_user_id=external_user_id,
            audio_path=str(audio_path),
            problem=f"{type(exc).__name__}:{exc}",
        )
    return ProbeTargetResult(
        platform=platform,
        ok=True,
        dry_run=False,
        external_user_id=external_user_id,
        audio_path=str(audio_path),
        response_type=type(response).__name__,
    )


async def run_probe(
    *,
    platforms: tuple[str, ...],
    vk_user_id: str | None,
    max_user_id: str | None,
    anchor: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    audio_path = _pick_audio(anchor)
    caption = f"Тест доставки аудио Метротерапии: {audio_path.name}"
    results: list[ProbeTargetResult] = []
    for platform in platforms:
        explicit = vk_user_id if platform == "vk" else max_user_id
        user_id = _target_user_id(platform, explicit)
        results.append(await _send(platform, user_id, audio_path, caption=caption, dry_run=dry_run))
    return {
        "ok": all(item.ok for item in results),
        "dry_run": dry_run,
        "audio_path": str(audio_path),
        "results": [asdict(item) for item in results],
    }


def _parse_platforms(raw: str) -> tuple[str, ...]:
    values = tuple(part.strip().lower() for part in str(raw or "").split(",") if part.strip())
    allowed = {"vk", "max"}
    bad = [item for item in values if item not in allowed]
    if bad:
        raise ValueError(f"unsupported_platforms:{','.join(bad)}")
    return values or ("vk", "max")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe real VK/MAX audio delivery with an existing full audio file.")
    parser.add_argument("--platform", default="vk,max", help="Comma-separated platforms: vk,max")
    parser.add_argument("--vk-user-id", default="", help="VK recipient id. Falls back to VK_AUDIO_PROBE_USER_ID.")
    parser.add_argument("--max-user-id", default="", help="MAX recipient id. Falls back to MAX_AUDIO_PROBE_USER_ID.")
    parser.add_argument("--anchor", type=int, default=None, help="Audio anchor number to send. Defaults to the first full-series file.")
    parser.add_argument("--send", action="store_true", help="Actually send audio. Default is dry-run only.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args(argv)

    try:
        result = asyncio.run(run_probe(
            platforms=_parse_platforms(args.platform),
            vk_user_id=args.vk_user_id or None,
            max_user_id=args.max_user_id or None,
            anchor=args.anchor,
            dry_run=not args.send,
        ))
    except (RuntimeError, ValueError) as exc:
        result = {"ok": False, "problem": str(exc)}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("VK/MAX audio probe result:")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
