from __future__ import annotations

import subprocess
from pathlib import Path


TARGET = Path("deploy/metrotherapy.env.example")


def main() -> None:
    source = subprocess.check_output(
        ["git", "show", "origin/main:deploy/metrotherapy.env.example"],
        text=True,
        encoding="utf-8",
    )
    marker = "HEALTHCHECK_PORT=8082\n"
    addition = (
        marker
        + "# Public /health, /healthz and /readyz responses are intentionally minimal.\n"
        + "# Set a strong secret to receive full diagnostics through either the\n"
        + "# X-Metrotherapy-Diagnostics-Token header or an Authorization: Bearer header.\n"
        + "HEALTHCHECK_DIAGNOSTICS_TOKEN=\n"
    )
    if source.count(marker) != 1:
        raise SystemExit("refusing unsafe env patch: healthcheck marker is not unique")
    updated = source.replace(marker, addition)
    if updated.count("HEALTHCHECK_DIAGNOSTICS_TOKEN=") != 1:
        raise SystemExit("refusing unsafe env patch: diagnostics token line count mismatch")
    TARGET.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
