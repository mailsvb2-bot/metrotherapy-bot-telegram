from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "probe_telegram_live_smoke.py"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_live_smoke_uses_aiogram_bot_methods() -> None:
    text = _source()
    assert "from aiogram import Bot" in text
    assert "get_me()" in text
    assert "get_webhook_info()" in text


def test_live_smoke_defaults_to_no_send() -> None:
    text = _source()
    assert 'parser.add_argument("--allow-send", action="store_true")' in text
    assert "allow_send: bool = False" in text
    assert "send_checked=False" in text


def test_live_smoke_records_probe_ledger() -> None:
    text = _source()
    assert 'PROBE_TYPE = "telegram_live_smoke_probe"' in text
    assert "start_probe_run" in text
    assert "finish_probe_run" in text


def test_live_smoke_has_no_broad_exception_handlers() -> None:
    tree = ast.parse(_source())
    broad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                broad.append("bare")
            elif isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}:
                broad.append(node.type.id)
    assert broad == []
