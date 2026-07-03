from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.practice_token_contract import PracticePackage, public_practice_packages


@dataclass(frozen=True)
class RedirectProbeResult:
    package_id: str
    ok: bool
    status: int
    location_present: bool
    detail: str = ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _packages(wanted: list[str]) -> tuple[PracticePackage, ...]:
    public = public_practice_packages()
    if not wanted or wanted == ["all"]:
        return public
    by_id = {package.package_id: package for package in public}
    missing = [package_id for package_id in wanted if package_id not in by_id]
    if missing:
        raise SystemExit(f"Unknown public package id(s): {', '.join(missing)}")
    return tuple(by_id[package_id] for package_id in wanted)


def _url(base_url: str, *, user_id: int, source: str, package: PracticePackage) -> str:
    query = urllib.parse.urlencode(
        {
            "source": source,
            "user_id": str(int(user_id)),
            "kind": "tokens",
            "package_id": package.package_id,
        }
    )
    return f"{base_url.rstrip('/')}/pay/yookassa?{query}"


def probe_one(base_url: str, *, user_id: int, source: str, package: PracticePackage) -> RedirectProbeResult:
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(_url(base_url, user_id=user_id, source=source, package=package), method="GET")
    try:
        with opener.open(request, timeout=20) as response:
            status = int(response.status)
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            body = response.read().decode("utf-8", "replace")[:160]
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        headers = {str(k).lower(): str(v) for k, v in exc.headers.items()} if exc.headers else {}
        body = exc.read().decode("utf-8", "replace")[:160] if exc.fp else ""
    location = headers.get("location", "")
    ok = status in {301, 302, 303, 307, 308} and bool(location)
    return RedirectProbeResult(
        package_id=package.package_id,
        ok=ok,
        status=status,
        location_present=bool(location),
        detail="redirect" if ok else body,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--user-id", type=int, default=990000001)
    parser.add_argument("--source", default="telegram")
    parser.add_argument("--package", action="append", default=[])
    args = parser.parse_args()

    results = [probe_one(args.base_url, user_id=args.user_id, source=args.source, package=p) for p in _packages(args.package)]
    report: dict[str, Any] = {"ok": all(item.ok for item in results), "results": [asdict(item) for item in results]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
