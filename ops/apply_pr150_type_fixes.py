from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0 and new in text:
        return
    if count != 1:
        raise SystemExit(f"expected one patch target in {path}, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    replace_once(
        ROOT / "services/payments/yookassa_refunds.py",
        '    obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}\n',
        '    raw_object = payload.get("object")\n'
        '    obj: dict[str, Any] = raw_object if isinstance(raw_object, dict) else {}\n',
    )
    replace_once(
        ROOT / "services/payments/yookassa_refunds.py",
        '    amount = obj.get("amount") if isinstance(obj.get("amount"), dict) else {}\n',
        '    raw_amount = obj.get("amount")\n'
        '    amount: dict[str, Any] = raw_amount if isinstance(raw_amount, dict) else {}\n',
    )
    replace_once(
        ROOT / "runtime/messenger_webhooks.py",
        '        if telegram_enabled:\n            await bot.set_webhook(\n',
        '        if telegram_enabled:\n'
        '            if bot is None:\n'
        '                raise RuntimeError("Telegram webhook bot disappeared after route registration")\n'
        '            await bot.set_webhook(\n',
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
