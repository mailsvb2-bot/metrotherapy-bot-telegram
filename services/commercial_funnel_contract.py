from __future__ import annotations

"""Canonical event taxonomy for the commercial user journey.

This module is intentionally data-only: it defines which runtime telemetry names
belong to each commercial milestone. Analytics, payment-path reporting, and
large-table indexing import the same contract so they cannot silently drift into
three different definitions of the funnel.
"""

COMMERCIAL_STEP_EVENT_NAMES: dict[str, tuple[str, ...]] = {
    "start": ("start", "bot_start", "user_start"),
    "demo": (
        "funnel_demo_open",
        "funnel_demo_work",
        "funnel_demo_home",
        "demo_sent",
    ),
    "listened": (
        "funnel_demo_ack",
        "audio_listened",
        "demo_ack",
    ),
    "offer": (
        "funnel_offer_shown",
        "view_tariffs",
        "sub_menu",
    ),
    "pay_click": (
        "funnel_offer_pay_clicked",
        "pay_selected",
        "payment_started",
    ),
    # ``checkout`` is deliberately stricter than ``pay_click``.  The canonical
    # payment_started event is emitted only after a provider adapter created a
    # payable invoice / confirmation surface.
    "checkout": ("payment_started",),
    "paid_event": (
        "funnel_pay_success",
        "payment_success",
        "successful_payment",
        "invoice_paid",
        "sub_paid",
    ),
}

# These events are useful for legacy campaign/funnel diagnostics and belong in
# the same selective large-table index, but they are not user milestones in the
# strict money chain.
COMMERCIAL_AUXILIARY_EVENT_NAMES: tuple[str, ...] = (
    "funnel_nudge_sent",
    "funnel_offer_sent",
    "funnel_deadline_sent",
    "funnel_lastcall_sent",
    "invoice_created",
)


def _ordered_unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


COMMERCIAL_FUNNEL_EVENT_NAMES: tuple[str, ...] = _ordered_unique(
    [
        name
        for names in COMMERCIAL_STEP_EVENT_NAMES.values()
        for name in names
    ]
    + list(COMMERCIAL_AUXILIARY_EVENT_NAMES)
)

# Preserve the exact path vocabulary expected by the detailed per-payment admin
# report while sourcing it from this single contract.
PAYMENT_PATH_STEP_NAMES: dict[str, tuple[str, ...]] = {
    "start": COMMERCIAL_STEP_EVENT_NAMES["start"],
    "demo": COMMERCIAL_STEP_EVENT_NAMES["demo"],
    "listened": COMMERCIAL_STEP_EVENT_NAMES["listened"],
    "offer": COMMERCIAL_STEP_EVENT_NAMES["offer"],
    "pay_click": COMMERCIAL_STEP_EVENT_NAMES["pay_click"],
    "paid": COMMERCIAL_STEP_EVENT_NAMES["paid_event"],
}


__all__ = [
    "COMMERCIAL_AUXILIARY_EVENT_NAMES",
    "COMMERCIAL_FUNNEL_EVENT_NAMES",
    "COMMERCIAL_STEP_EVENT_NAMES",
    "PAYMENT_PATH_STEP_NAMES",
]
