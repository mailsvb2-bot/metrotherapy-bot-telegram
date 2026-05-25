from __future__ import annotations

"""Public checkout redirect probe.

This probe deliberately does not follow redirects. The live payment closure probe
uses urllib's default redirect behavior, which can turn a valid YooKassa/YooMoney
302 into a final 200 HTML page. This script captures the first response and
therefore proves that the bot backend returned a real checkout redirect.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.practice_token_contract import PracticePackage, public_practice_packages  # noqa: E402


@dataclass(frozen=True)
class CheckoutProbeResult:
    package_id: str
    ok: bool
    status: int
    location: str
    url: str
    detail: str = ""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _selected_packages(names: list[str]) -> tuple[PracticePackage, ...]:
    packages = public_practice_packages()
    if not names or names == ["all"]:
        return packages
    by_id = {package.package_id: package for package in packages}
    missing = [name for name in names if name not in by_id]
    if missing:
        raise SystemExit(f"Unknown public package id(s): {', '.join(missing)}")
    return tuple(by_id[name] for name in names)


def _checkout_url(base_url: str, *, user_id: int, source: str, package: PracticePackage) -> str:
    query = urllib.parse.urlencode(
        {
            "source": source,
            "user_id": str(int(user_id)),
            "kind": "tokens",
            "package_id": package.package_id,
        }
    )
    return f"{base_url.rstrip('/')}/pay/yookassa?{query}"


def _first_response(url: str) -> tuple[int, dict[str, str], str]:
    opener = urllib.request.build_opener(NoRedirectHandler())
    request = urllib.request.Request(url, method="GET")
    try:
        with opener.open(request, timeout=20) as response:
            headers = {str(k): str(v) for k, v in response.headers.items()}
            body = response.read().decode("utf-8", "replace")
            return int(response.status), headers, body
    except urllib.error.HTTPError as exc:
        headers = {str(k): str(v) for k, v in exc.headers.items()} if exc.headers else {}
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return int(exc.code), headers, body


def check_package(base_url: str, *, user_id: int, source: str, package: PracticePackage) -> CheckoutProbeResult:
    url = _checkout_url(base_url, user_id=user_id, source=source, package=package)
    status, headers, body = _first_response(url)
    location = headers.get("Location") or headers.get("location") or ""
    ok = status in {301, 302, 303, 307, 308} and (
        "yoomoney.ru" in location or "yookassa.ru" in location or "checkout" in location
    )
    return CheckoutProbeResult(
        package_id=package.package_id,
        ok=ok,
        status=status,
        location=location,
        url=url,
        detail=f"body={body[:160]}" if not ok else "redirect captured without following it",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://metrotherapy-bot.metrotherapy.ru")
    parser.add_argument("--user-id", type=int, default=990000001)
    parser.add_argument("--source", default="telegram")
    parser.add_argument("--package", action="append", default=[], help="Public package id to test; repeat or use all")
    args = parser.parse_args()

    packages = _selected_packages(args.package)
    results = [check_package(args.base_url, user_id=args.user_id, source=args.source, package=package) for package in packages]
    report: dict[str, Any] = {
        "ok": all(item.ok for item in results),
        "base_url": args.base_url.rstrip("/"),
        "user_id": int(args.user_id),
        "source": args.source,
        "results": [asdict(item) for item in results],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
