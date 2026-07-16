from __future__ import annotations

from urllib.parse import urlencode


def stars_amount_label(amount_xtr: int) -> str:
    amount = int(amount_xtr)
    if amount <= 0:
        raise ValueError("stars_amount_invalid")
    return f"{amount:,} Stars".replace(",", " ")


def stars_topup_url(*, amount_xtr: int, package_id: str) -> str:
    amount = int(amount_xtr)
    if amount <= 0:
        raise ValueError("stars_topup_amount_invalid")
    package = str(package_id or "").strip()
    if not package:
        raise ValueError("stars_topup_package_invalid")
    purpose = f"metrotherapy_{package}"[:64]
    return "tg://stars_topup?" + urlencode(
        {
            "balance": amount,
            "purpose": purpose,
        }
    )
