from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "probe_user_journey_e2e.py"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_user_journey_e2e_probe_declares_canonical_probe_type() -> None:
    text = _source()
    assert 'PROBE_TYPE = "synthetic_user_journey_e2e_probe"' in text
    assert "record_yookassa_webhook" in text
    assert "check_and_reserve_for_audio" in text
    assert "finalize_audio_access" in text
    assert "record_demo_ack" in text


def test_user_journey_e2e_probe_has_no_broad_exception_handlers() -> None:
    tree = ast.parse(_source())
    broad_handlers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            broad_handlers.append("bare")
            continue
        if isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}:
            broad_handlers.append(node.type.id)
    assert broad_handlers == []


def test_user_journey_e2e_probe_defaults_to_cleanup() -> None:
    text = _source()
    assert "keep_artifacts: bool = False" in text
    assert 'cleanup_status = "clean"' in text
    assert "DELETE FROM users WHERE user_id=?" in text
