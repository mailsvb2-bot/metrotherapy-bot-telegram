from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deploy_wrapper_starts_with_exact_bash_shebang() -> None:
    payload = (ROOT / "deploy.sh").read_bytes()

    assert payload.startswith(b"#!/usr/bin/env bash\n")
    assert not payload.startswith(b"\\")
    assert b"\n        #!/usr/bin/env bash" not in payload
