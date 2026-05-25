from __future__ import annotations

"""Read-only admin payment report surface.

This module intentionally does not reimplement payment decisions. It aggregates
existing canonical facts from:

- services.payments.reconciliation.payment_problem_summary
- services.premium_entitlements.consultation_requests_summary

The goal is admin/control-plane visibility for payment problems and personal
consultation requests without creating a second payment brain.
"""

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
        # The report itself is healthy if it could be built. Payment problems are
        # admin-visible facts, not report failures.
        return True


def build_admin_payment_report(*, limit: int = 20, user_id: int | None = None) -> AdminPaymentReport:
    payment_problems = payment_problem_summary(limit=limit, user_id=user_id)
    consultation_requests = consultation_requests_summary(limit=limit, user_id=user_id)
    return AdminPaymentReport(
        payment_problems=tuple(dict(item) for item in payment_problems),
        consultation_requests=tuple(dict(item) for item in consultation_requests),
    )


def _short(value: Any, max_len: int = 180) -> str:
    text = str(value if value is not None else "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def render_admin_payment_report_text(report: AdminPaymentReport) -> str:
    lines: list[str] = []
    lines.append("💳 Админ-отчёт по оплатам")
    lines.append("")
    lines.append(f"Проблемные платежи: {report.payment_problem_count}")
    if report.payment_problems:
        for item in report.payment_problems[:10]:
            lines.append(
                "- "
                f"payment_id={_short(item.get('provider_charge_id') or '-')} "
                f"user_id={_short(item.get('user_id') or '-')} "
                f"status={_short(item.get('provider_status') or '-')} "
                f"problem={_short(item.get('problem') or '-')}"
            )
    else:
        lines.append("- нет записей, требующих внимания")

    lines.append("")
    lines.append(f"Заявки на консультацию: {report.consultation_request_count}")
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
        lines.append("- нет новых заявок")

    return "\n".join(lines).strip()
