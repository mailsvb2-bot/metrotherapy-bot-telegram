from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess  # nosec B404 - fixed administrative executables, never a shell
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TARGET_HOST = "metrotherapy-bot.metrotherapy.ru"
UPSTREAM = "http://127.0.0.1:8082"
MARKER = "# managed-by: metrotherapy growth-click-route"
ROUTE_BLOCK = f'''\n    {MARKER}\n    location /a/ {{\n        proxy_pass {UPSTREAM};\n        proxy_http_version 1.1;\n        proxy_read_timeout 10s;\n        proxy_connect_timeout 5s;\n        proxy_send_timeout 10s;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n    }}\n'''

_CONFIG_HEADER_RE = re.compile(r"^# configuration file (.+):$", re.MULTILINE)
_SERVER_RE = re.compile(r"(?m)^\s*server\s*\{")
_SERVER_NAME_RE = re.compile(r"(?m)^\s*server_name\s+([^;]+);")
_LISTEN_RE = re.compile(r"(?m)^\s*listen\s+([^;]+);")
_GROWTH_LOCATION_RE = re.compile(r"(?m)^\s*location\s+(?:\^~\s+)?/a/\s*\{")
_PROXY_RE = re.compile(r"(?m)^\s*proxy_pass\s+http://127\.0\.0\.1:8082(?:/)?\s*;")


class GrowthRouteSyncError(RuntimeError):
    pass


def _matching_brace(text: str, open_index: int) -> int:
    depth = 0
    quote = ""
    escaped = False
    comment = False
    for index in range(open_index, len(text)):
        char = text[index]
        if comment:
            if char == "\n":
                comment = False
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char == "#":
            comment = True
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise GrowthRouteSyncError("unbalanced nginx server block")


def _server_blocks(text: str) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    for match in _SERVER_RE.finditer(text):
        open_index = text.find("{", match.start(), match.end())
        close_index = _matching_brace(text, open_index)
        blocks.append((match.start(), close_index + 1, text[match.start() : close_index + 1]))
    return blocks


def _serves_target(block: str, host: str) -> bool:
    for match in _SERVER_NAME_RE.finditer(block):
        if host in match.group(1).split():
            return True
    return False


def _is_tls(block: str) -> bool:
    return any(re.search(r"(?<!\d)443(?!\d)", match.group(1)) for match in _LISTEN_RE.finditer(block))


def _patch_config_text(text: str, *, host: str = TARGET_HOST) -> tuple[str, bool]:
    targets = [block for block in _server_blocks(text) if _serves_target(block[2], host) and _is_tls(block[2])]
    if not targets:
        raise GrowthRouteSyncError(f"no TLS server block found for {host}")

    updated = text
    changed = False
    for _start, end, block in reversed(targets):
        location = _GROWTH_LOCATION_RE.search(block)
        if location is not None:
            location_open = block.find("{", location.start(), location.end())
            location_close = _matching_brace(block, location_open)
            location_text = block[location.start() : location_close + 1]
            if _PROXY_RE.search(location_text) is None:
                raise GrowthRouteSyncError("existing /a/ location does not proxy to the canonical health runtime")
            continue
        close_index = end - 1
        updated = updated[:close_index] + ROUTE_BLOCK + updated[close_index:]
        changed = True
    return updated, changed


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # nosec B603 - argv only; caller is the privileged deploy contract
        command, check=False, capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-1200:]
        raise GrowthRouteSyncError(f"command failed ({' '.join(command)}): {detail}")
    return result


def _active_target_config(nginx_bin: str, *, host: str) -> Path:
    result = _run([nginx_bin, "-T"])
    dump = result.stdout + "\n" + result.stderr
    headers = list(_CONFIG_HEADER_RE.finditer(dump))
    candidates: dict[Path, str] = {}
    for index, match in enumerate(headers):
        start = match.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(dump)
        segment = dump[start:end]
        if not any(_serves_target(block[2], host) and _is_tls(block[2]) for block in _server_blocks(segment)):
            continue
        raw_path = Path(match.group(1))
        try:
            resolved = raw_path.resolve(strict=True)
        except OSError as exc:
            raise GrowthRouteSyncError(f"active nginx config cannot be resolved: {raw_path}: {exc}") from exc
        candidates[resolved] = segment
    if len(candidates) != 1:
        names = ", ".join(str(path) for path in sorted(candidates)) or "none"
        raise GrowthRouteSyncError(f"expected exactly one active TLS config for {host}; found {len(candidates)}: {names}")
    return next(iter(candidates))


def _atomic_write(path: Path, data: bytes, source_stat: os.stat_result) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.growth-route.", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, stat.S_IMODE(source_stat.st_mode))
        os.chown(temp, source_stat.st_uid, source_stat.st_gid)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _redirect_location(url: str) -> tuple[int, str]:
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "metrotherapy-deploy-route-probe/1"})
    try:
        with opener.open(request, timeout=10) as response:
            return int(response.status), str(response.headers.get("Location") or "")
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc.headers.get("Location") or "")
    except OSError as exc:
        raise GrowthRouteSyncError(f"route probe failed for {url}: {exc}") from exc


def _verify_redirect(url: str) -> None:
    status_code, location = _redirect_location(url)
    if status_code not in {301, 302, 303, 307, 308} or not location.startswith("https://t.me/"):
        raise GrowthRouteSyncError(f"route probe expected Telegram redirect, got status={status_code} location={location!r}")


def sync_growth_route(*, nginx_bin: str, systemctl_bin: str, host: str, public_url: str, upstream_url: str) -> bool:
    _verify_redirect(upstream_url)
    config_path = _active_target_config(nginx_bin, host=host)
    original = config_path.read_bytes()
    source_stat = config_path.stat()
    updated_text, changed = _patch_config_text(original.decode("utf-8"), host=host)
    if not changed:
        _run([nginx_bin, "-t"])
        _verify_redirect(public_url)
        return False

    _atomic_write(config_path, updated_text.encode("utf-8"), source_stat)
    try:
        _run([nginx_bin, "-t"])
        _run([systemctl_bin, "reload", "nginx"])
        _verify_redirect(public_url)
    except BaseException:  # validator: allow-wide-except
        _atomic_write(config_path, original, source_stat)
        try:
            _run([nginx_bin, "-t"])
            _run([systemctl_bin, "reload", "nginx"])
        except BaseException as restore_exc:  # validator: allow-wide-except
            raise GrowthRouteSyncError(f"growth route failed and nginx restore also failed: {restore_exc}") from restore_exc
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely synchronize Metrotherapy's public growth-click nginx route.")
    parser.add_argument("--nginx-bin", default=os.getenv("NGINX_BIN", "/usr/sbin/nginx"))
    parser.add_argument("--systemctl-bin", default=os.getenv("SYSTEMCTL_BIN", "/usr/bin/systemctl"))
    parser.add_argument("--host", default=TARGET_HOST)
    parser.add_argument(
        "--public-url",
        default="https://metrotherapy-bot.metrotherapy.ru/a/src_probe__camp_deploy__creative_head",
    )
    parser.add_argument(
        "--upstream-url",
        default="http://127.0.0.1:8082/a/src_probe__camp_deploy__creative_head",
    )
    args = parser.parse_args()
    changed = sync_growth_route(
        nginx_bin=args.nginx_bin,
        systemctl_bin=args.systemctl_bin,
        host=args.host,
        public_url=args.public_url,
        upstream_url=args.upstream_url,
    )
    print(f"NGINX_GROWTH_ROUTE_OK changed={int(changed)} host={args.host}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
