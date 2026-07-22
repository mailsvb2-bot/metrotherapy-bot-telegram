from __future__ import annotations

from pathlib import Path


TARGET = Path("runtime/health_server.py")


def replace_exact(source: str, old: str, new: str, *, count: int = 1) -> str:
    actual = source.count(old)
    if actual != count:
        raise SystemExit(
            f"refusing unsafe patch: expected {count} occurrence(s), found {actual}: {old[:120]!r}"
        )
    return source.replace(old, new)


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    updated = source

    updated = replace_exact(
        updated,
        "import asyncio\nimport logging\n",
        "import asyncio\nimport hmac\nimport logging\n",
    )

    updated = replace_exact(
        updated,
        "log = logging.getLogger(__name__)\n\n\n@dataclass\n",
        '''log = logging.getLogger(__name__)\n\n\n_DIAGNOSTICS_HEADER = 'X-Metrotherapy-Diagnostics-Token'\n_DIAGNOSTICS_ENV = 'HEALTHCHECK_DIAGNOSTICS_TOKEN'\n\n\ndef _diagnostics_token() -> str:\n    return str(os.getenv(_DIAGNOSTICS_ENV) or '').strip()\n\n\ndef _provided_diagnostics_token(request: web.Request) -> str:\n    headers = getattr(request, 'headers', {}) or {}\n    explicit = str(headers.get(_DIAGNOSTICS_HEADER) or '').strip()\n    if explicit:\n        return explicit\n    authorization = str(headers.get('Authorization') or '').strip()\n    scheme, separator, value = authorization.partition(' ')\n    if separator and scheme.casefold() == 'bearer':\n        return value.strip()\n    return ''\n\n\ndef _diagnostics_authorized(request: web.Request) -> bool:\n    expected = _diagnostics_token()\n    provided = _provided_diagnostics_token(request)\n    return bool(expected and provided and hmac.compare_digest(provided, expected))\n\n\ndef _public_probe_payload(payload: dict[str, Any]) -> dict[str, Any]:\n    return {\n        'ok': bool(payload.get('ok')),\n        'service': str(payload.get('service') or 'metrotherapy'),\n        'probe': str(payload.get('probe') or 'health'),\n    }\n\n\n@dataclass\n''',
    )

    updated = replace_exact(
        updated,
        '''async def _health(request: web.Request) -> web.Response:\n    payload, status = await asyncio.to_thread(build_health_payload)\n    return web.json_response(payload, status=status)\n\n\nasync def _ready(request: web.Request) -> web.Response:\n    payload, status = await asyncio.to_thread(build_readiness_payload)\n    return web.json_response(payload, status=status)\n''',
        '''async def _health(request: web.Request) -> web.Response:\n    payload, status = await asyncio.to_thread(build_health_payload)\n    response_payload = payload if _diagnostics_authorized(request) else _public_probe_payload(payload)\n    return web.json_response(\n        response_payload,\n        status=status,\n        headers={'Cache-Control': 'no-store'},\n    )\n\n\nasync def _ready(request: web.Request) -> web.Response:\n    payload, status = await asyncio.to_thread(build_readiness_payload)\n    response_payload = payload if _diagnostics_authorized(request) else _public_probe_payload(payload)\n    return web.json_response(\n        response_payload,\n        status=status,\n        headers={'Cache-Control': 'no-store'},\n    )\n''',
    )

    if updated == source:
        raise SystemExit("refusing empty patch")
    TARGET.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
