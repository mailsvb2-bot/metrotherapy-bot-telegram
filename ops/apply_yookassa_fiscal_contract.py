from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config" / "settings.py"
COMMON = ROOT / "services" / "payments" / "common.py"
CHECKOUT = ROOT / "services" / "payments" / "yookassa_checkout.py"
VALIDATOR = ROOT / "services" / "validators" / "prod.py"


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0 and new in text:
        return
    if count != 1:
        raise SystemExit(f"expected exactly one {label} target in {path}, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    replace_once(
        SETTINGS,
        'YOOKASSA_VAT_CODE: int = _env_int("YOOKASSA_VAT_CODE", 1, minimum=1, maximum=6)',
        'YOOKASSA_VAT_CODE: int = _env_int("YOOKASSA_VAT_CODE", 1, minimum=1, maximum=12)',
        label="settings VAT range",
    )

    replace_once(
        COMMON,
        "from config.settings import settings\n",
        "from config.settings import settings\nfrom services.payments.receipt_contract import validate_receipt_contract\n",
        label="common fiscal import",
    )
    replace_once(
        COMMON,
        '''    value = f"{Decimal(price_rub).quantize(Decimal('1'), rounding=ROUND_HALF_UP):.2f}"  # 10 -> "10.00"
    receipt = {
''',
        '''    value = f"{Decimal(price_rub).quantize(Decimal('1'), rounding=ROUND_HALF_UP):.2f}"  # 10 -> "10.00"
    tax_system_code, vat_code, payment_mode, payment_subject = validate_receipt_contract(
        tax_system_code=getattr(settings, "YOOKASSA_TAX_SYSTEM_CODE", 2),
        vat_code=getattr(settings, "YOOKASSA_VAT_CODE", 1),
        payment_mode=getattr(settings, "YOOKASSA_PAYMENT_MODE", "full_payment"),
        payment_subject=getattr(settings, "YOOKASSA_PAYMENT_SUBJECT", "service"),
    )
    receipt = {
''',
        label="common fiscal validation",
    )
    replace_once(
        COMMON,
        '''            "tax_system_code": int(getattr(settings, "YOOKASSA_TAX_SYSTEM_CODE", 2)),
''',
        '''            "tax_system_code": tax_system_code,
''',
        label="common tax system value",
    )
    replace_once(
        COMMON,
        '''                    "vat_code": int(getattr(settings, "YOOKASSA_VAT_CODE", 1)),
                    "payment_subject": getattr(settings, "YOOKASSA_PAYMENT_SUBJECT", "service"),
                    "payment_mode": getattr(settings, "YOOKASSA_PAYMENT_MODE", "full_payment"),
''',
        '''                    "vat_code": vat_code,
                    "payment_subject": payment_subject,
                    "payment_mode": payment_mode,
''',
        label="common receipt item values",
    )

    replace_once(
        CHECKOUT,
        "from services.practice_token_contract import package_by_id\n",
        "from services.practice_token_contract import package_by_id\nfrom services.payments.receipt_contract import validate_receipt_contract\n",
        label="checkout fiscal import",
    )
    replace_once(
        CHECKOUT,
        '''def build_yookassa_receipt(*, amount_value: str, description: str) -> dict:
    customer_email = _receipt_customer_email()
    tax_system_code = _receipt_int("YOOKASSA_TAX_SYSTEM_CODE", 2, minimum=1, maximum=6)
    vat_code = _receipt_int("YOOKASSA_VAT_CODE", 1, minimum=1, maximum=6)
    payment_mode = _env_value("YOOKASSA_PAYMENT_MODE", "full_payment")
    payment_subject = _env_value("YOOKASSA_PAYMENT_SUBJECT", "service")
    return {
''',
        '''def build_yookassa_receipt(*, amount_value: str, description: str) -> dict:
    customer_email = _receipt_customer_email()
    try:
        tax_system_code, vat_code, payment_mode, payment_subject = validate_receipt_contract(
            tax_system_code=_env_value("YOOKASSA_TAX_SYSTEM_CODE", "2"),
            vat_code=_env_value("YOOKASSA_VAT_CODE", "1"),
            payment_mode=_env_value("YOOKASSA_PAYMENT_MODE", "full_payment"),
            payment_subject=_env_value("YOOKASSA_PAYMENT_SUBJECT", "service"),
        )
    except ValueError as exc:
        raise YooKassaCheckoutError(str(exc)) from exc
    return {
''',
        label="checkout fiscal validation",
    )

    replace_once(
        VALIDATOR,
        "from services.validators.base import ValidationError\n",
        "from services.payments.receipt_contract import validate_receipt_contract\nfrom services.validators.base import ValidationError\n",
        label="production validator fiscal import",
    )
    replace_once(
        VALIDATOR,
        '''    if not _first_env("YOOKASSA_RECEIPT_EMAIL", "PAYMENT_RECEIPT_EMAIL", "ADMIN_EMAIL"):
        errors.append("YOOKASSA_RECEIPT_EMAIL or PAYMENT_RECEIPT_EMAIL or ADMIN_EMAIL is required in prod")

    for name in ("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", "PAYMENT_CHECKOUT_INTENT_REQUIRED"):
''',
        '''    if not _first_env("YOOKASSA_RECEIPT_EMAIL", "PAYMENT_RECEIPT_EMAIL", "ADMIN_EMAIL"):
        errors.append("YOOKASSA_RECEIPT_EMAIL or PAYMENT_RECEIPT_EMAIL or ADMIN_EMAIL is required in prod")

    try:
        validate_receipt_contract(
            tax_system_code=_env("YOOKASSA_TAX_SYSTEM_CODE", "2"),
            vat_code=_env("YOOKASSA_VAT_CODE", "1"),
            payment_mode=_env("YOOKASSA_PAYMENT_MODE", "full_payment"),
            payment_subject=_env("YOOKASSA_PAYMENT_SUBJECT", "service"),
        )
    except ValueError as exc:
        errors.append(str(exc))

    for name in ("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", "PAYMENT_CHECKOUT_INTENT_REQUIRED"):
''',
        label="production fiscal validation",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
