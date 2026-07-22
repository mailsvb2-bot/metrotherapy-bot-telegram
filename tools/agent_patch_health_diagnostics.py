from __future__ import annotations

from pathlib import Path


TARGET = Path("runtime/health_server.py")


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    old = '''    return web.json_response(\n        response_payload,\n        status=status,\n        headers={'Cache-Control': 'no-store'},\n    )\n'''
    new = '''    return web.json_response(response_payload, status=status)\n'''
    actual = source.count(old)
    if actual != 2:
        raise SystemExit(f"refusing unsafe patch: expected 2 response wrappers, found {actual}")
    updated = source.replace(old, new)
    if updated == source:
        raise SystemExit("refusing empty patch")
    TARGET.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
