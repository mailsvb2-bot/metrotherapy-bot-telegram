from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "services" / "payments" / "retry_queue.py"

OLD = "WHERE provider=? AND provider_payment_id=? AND event=? AND status<>'dead'"
NEW = "WHERE provider=? AND provider_payment_id=? AND event=?"


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    count = text.count(OLD)
    if count == 0 and NEW in text:
        return 0
    if count != 1:
        raise SystemExit(f"expected exactly one dead-recovery target, got {count}")
    TARGET.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
