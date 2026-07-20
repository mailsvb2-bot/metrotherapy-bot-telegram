from __future__ import annotations

"""Illustrative SLA-to-retention model; never presented as observed business data."""

import argparse
import json
import math
from typing import Any, Sequence

SLA_LATENCY_MS = (200.0, 400.0, 600.0, 800.0, 1000.0, 1200.0)
RETENTION_RATE = (0.55, 0.50, 0.44, 0.36, 0.28, 0.18)
ARPU_RUB = (5200.0, 4800.0, 4200.0, 3500.0, 2600.0, 1800.0)
DEFAULT_MAU = 10_000
MAX_MAU = 100_000_000
DATA_ORIGIN = "illustrative_synthetic"
DECISION_USE = "demo_only_not_observed_business_data"


class SlaModelInputError(ValueError):
    """Expected validation failure with a stable, non-sensitive code."""

    def __init__(self, code: str) -> None:
        normalized = str(code or "sla_model_input_invalid")
        super().__init__(normalized)
        self.code = normalized


def _validated_mau(value: int) -> int:
    if isinstance(value, bool):
        raise SlaModelInputError("mau_must_be_integer")
    try:
        mau = int(value)
    except TypeError:
        raise SlaModelInputError("mau_must_be_integer") from None
    except ValueError:
        raise SlaModelInputError("mau_must_be_integer") from None
    if mau < 1:
        raise SlaModelInputError("mau_must_be_positive")
    if mau > MAX_MAU:
        raise SlaModelInputError("mau_exceeds_safe_limit")
    return mau


def _validated_series(
    latency_ms: Sequence[float],
    retention_rate: Sequence[float],
    arpu_rub: Sequence[float],
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    if not latency_ms or len(latency_ms) != len(retention_rate) or len(latency_ms) != len(arpu_rub):
        raise SlaModelInputError("model_series_length_mismatch")

    latencies = tuple(float(value) for value in latency_ms)
    retention = tuple(float(value) for value in retention_rate)
    arpu = tuple(float(value) for value in arpu_rub)
    if any(not math.isfinite(value) or value < 0 for value in latencies):
        raise SlaModelInputError("latency_series_invalid")
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in retention):
        raise SlaModelInputError("retention_series_invalid")
    if any(not math.isfinite(value) or value < 0 for value in arpu):
        raise SlaModelInputError("arpu_series_invalid")
    return latencies, retention, arpu


def model_rows(
    *,
    mau: int = DEFAULT_MAU,
    latency_ms: Sequence[float] = SLA_LATENCY_MS,
    retention_rate: Sequence[float] = RETENTION_RATE,
    arpu_rub: Sequence[float] = ARPU_RUB,
) -> list[dict[str, float]]:
    audience = _validated_mau(mau)
    latencies, retention, arpu = _validated_series(latency_ms, retention_rate, arpu_rub)
    return [
        {
            "sla_latency_ms": latency,
            "retention_rate": retained,
            "arpu_rub": revenue_per_user,
            "monthly_revenue_rub": retained * revenue_per_user * audience,
        }
        for latency, retained, revenue_per_user in zip(latencies, retention, arpu, strict=True)
    ]


def build_report(*, mau: int = DEFAULT_MAU) -> dict[str, Any]:
    audience = _validated_mau(mau)
    return {
        "ok": True,
        "data_origin": DATA_ORIGIN,
        "decision_use": DECISION_USE,
        "observed_production_data": False,
        "mau_assumption": audience,
        "currency": "RUB",
        "model_assumptions": {
            "retention_and_arpu_are_fixed_synthetic_inputs": True,
            "causality_is_not_established": True,
            "forecast_or_valuation_use_is_prohibited": True,
        },
        "rows": model_rows(mau=audience),
        "error_code": "",
    }


def build_figures(*, mau: int = DEFAULT_MAU) -> list[Any]:
    """Build figures only when explicitly requested by an operator."""

    import matplotlib.pyplot as plt

    rows = model_rows(mau=mau)
    latency = [row["sla_latency_ms"] for row in rows]
    retention = [row["retention_rate"] for row in rows]
    arpu = [row["arpu_rub"] for row in rows]
    monthly_revenue = [row["monthly_revenue_rub"] for row in rows]

    figures: list[Any] = []

    figure, axis = plt.subplots()
    axis.plot(latency, retention, marker="o")
    axis.set_xlabel("Задержка SLA (мс)")
    axis.set_ylabel("Месячное удержание")
    axis.set_title("Синтетическая модель: задержка SLA → удержание")
    axis.grid(True)
    figures.append(figure)

    figure, axis = plt.subplots()
    axis.plot(retention, arpu, marker="o")
    axis.set_xlabel("Месячное удержание")
    axis.set_ylabel("ARPU (₽)")
    axis.set_title("Синтетическая модель: удержание → ARPU")
    axis.grid(True)
    figures.append(figure)

    figure, axis = plt.subplots()
    axis.plot(latency, monthly_revenue, marker="o")
    axis.set_xlabel("Задержка SLA (мс)")
    axis.set_ylabel("Выручка в месяц (₽)")
    axis.set_title(f"Синтетическая модель: SLA → выручка ({_validated_mau(mau):,} MAU)")
    axis.grid(True)
    figures.append(figure)

    return figures


def show(*, mau: int = DEFAULT_MAU) -> None:
    """Compatibility helper for an explicit interactive visualization."""

    import matplotlib.pyplot as plt

    build_figures(mau=mau)
    plt.show()


def _error_report(code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data_origin": DATA_ORIGIN,
        "decision_use": DECISION_USE,
        "observed_production_data": False,
        "error_code": str(code or "sla_model_failed"),
    }


def _print_human(report: dict[str, Any]) -> None:
    if report.get("ok") is not True:
        print(f"SLA_RETENTION_MODEL ok=false error_code={report.get('error_code')}")
        return
    print(
        "SLA_RETENTION_MODEL "
        f"ok=true data_origin={report['data_origin']} decision_use={report['decision_use']} "
        f"mau_assumption={report['mau_assumption']} rows={len(report['rows'])}"
    )
    print("WARNING: synthetic illustrative assumptions; not observed production analytics.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Illustrative synthetic SLA retention model")
    parser.add_argument("--mau", type=int, default=DEFAULT_MAU)
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = build_report(mau=args.mau)
        if args.plot:
            show(mau=args.mau)
    except SlaModelInputError as exc:
        report = _error_report(exc.code)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    else:
        _print_human(report)
    if args.strict and report.get("ok") is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
