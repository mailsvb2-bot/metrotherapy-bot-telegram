from __future__ import annotations

"""Canonical YooKassa 54-FZ receipt value contract.

The provider's 2026 reference supports VAT codes 1..12. For YooKassa-hosted
receipts the supported payment modes are full prepayment and full payment.
Keeping these values in one side-effect-free module prevents direct checkout,
legacy Telegram provider_data and production validation from drifting apart.
"""

TAX_SYSTEM_CODES = frozenset(range(1, 7))
VAT_CODES = frozenset(range(1, 13))
PAYMENT_MODES = frozenset({"full_prepayment", "full_payment"})
PAYMENT_SUBJECTS = frozenset(
    {
        "commodity",
        "excise",
        "job",
        "service",
        "payment",
        "casino",
        "gambling_bet",
        "gambling_prize",
        "lottery",
        "lottery_prize",
        "intellectual_activity",
        "agent_commission",
        "property_right",
        "non_operating_gain",
        "sales_tax",
        "resort_fee",
        "marked",
        "non_marked",
        "marked_excise",
        "non_marked_excise",
        "fine",
        "tax",
        "lien",
        "cost",
        "agent_withdrawals",
        "pension_insurance_without_payouts",
        "pension_insurance_with_payouts",
        "health_insurance_without_payouts",
        "health_insurance_with_payouts",
        "health_insurance",
        "another",
    }
)


def _integer_code(name: str, value: object, allowed: frozenset[int]) -> int:
    raw = str(value).strip()
    try:
        parsed = int(raw, 10)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed not in allowed:
        minimum = min(allowed)
        maximum = max(allowed)
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def validate_tax_system_code(value: object) -> int:
    return _integer_code("YOOKASSA_TAX_SYSTEM_CODE", value, TAX_SYSTEM_CODES)


def validate_vat_code(value: object) -> int:
    return _integer_code("YOOKASSA_VAT_CODE", value, VAT_CODES)


def _enum_value(name: str, value: object, allowed: frozenset[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        expected = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {expected}")
    return normalized


def validate_payment_mode(value: object) -> str:
    return _enum_value("YOOKASSA_PAYMENT_MODE", value, PAYMENT_MODES)


def validate_payment_subject(value: object) -> str:
    return _enum_value("YOOKASSA_PAYMENT_SUBJECT", value, PAYMENT_SUBJECTS)


def validate_receipt_contract(
    *,
    tax_system_code: object,
    vat_code: object,
    payment_mode: object,
    payment_subject: object,
) -> tuple[int, int, str, str]:
    return (
        validate_tax_system_code(tax_system_code),
        validate_vat_code(vat_code),
        validate_payment_mode(payment_mode),
        validate_payment_subject(payment_subject),
    )
