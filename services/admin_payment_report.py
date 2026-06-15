from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.payments.reconciliation import payment_problem_summary
from services.premium_entitlements import consultation_requests_summary


@dataclass(frozen=True)
class AdminPaymentReport:
    payment_problems: tuple[dict[str, Any], ...]
    consultation_requests: tuple[dict[str, Any], ...]

    @property
    def payment_problem_count(self) -> int:
        return len(self.payment_problems)

    @property
    def consultation_request_count(self) -> int:
        return len(self.consultation_requests)

    @property
    def ok(self) -> bool:
        return True


def build_admin_payment_report(*, limit: int = 20, user_id: int | None = None) -> AdminPaymentReport:
    try:
        payment_problems = payment_problem_summary(limit=limit, user_id=user_id)
    except TypeError:
        payment_problems = payment_problem_summary(limit=limit)
    consultation_requests = consultation_requests_summary(limit=limit, user_id=user_id)
    return AdminPaymentReport(
        payment_problems=tuple(dict(item) for item in payment_problems),
        consultation_requests=tuple(dict(item) for item in consultation_requests),
    )


def _short(value: Any, max_len: int = 180) -> str:
    text = str(value if value is not None else "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def render_admin_payment_report_text(report: AdminPaymentReport) -> str:
    lines: list[str] = []
    lines.append("Admin payment report")
    lines.append("")
    lines.append(f"Payment problems: {report.payment_problem_count}")
    if report.payment_problems:
        for item in report.payment_problems[:10]:
            lines.append(
                "- "
                f"payment_id={_short(item.get('provider_charge_id') or '-')} "
                f"user_id={_short(item.get('user_id') or '-')} "
                f"status={_short(item.get('provider_status') or '-')} "
                f"processing={_short(item.get('processing_status') or '-')} "
                f"problem={_short(item.get('problem') or item.get('processing_error') or '-')}"
            )
    else:
        lines.append("- no records requiring attention")

    lines.append("")
    lines.append(f"Consultation requests: {report.consultation_request_count}")
    if report.consultation_requests:
        for item in report.consultation_requests[:10]:
            lines.append(
                "- "
                f"request_id={_short(item.get('id') or '-')} "
                f"user_id={_short(item.get('user_id') or '-')} "
                f"platform={_short(item.get('platform') or '-')} "
                f"external_user_id={_short(item.get('external_user_id') or '-')} "
                f"package_id={_short(item.get('package_id') or '-')} "
                f"payment_id={_short(item.get('provider_payment_id') or '-')} "
                f"status={_short(item.get('status') or '-')}"
            )
    else:
        lines.append("- no new requests")

    return "\n".join(lines).strip()
