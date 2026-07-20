from __future__ import annotations

import builtins
import importlib
import json
import math
import sys
from typing import Any

import pytest

from dashboard import sla_retention_money as model


def test_module_import_does_not_require_numpy_or_matplotlib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "dashboard.sla_retention_money"
    sys.modules.pop(module_name, None)
    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "numpy" or name.startswith("matplotlib"):
            raise AssertionError(f"heavy plotting dependency imported at module load: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    imported = importlib.import_module(module_name)

    assert imported.DATA_ORIGIN == "illustrative_synthetic"
    assert imported.DECISION_USE == "demo_only_not_observed_business_data"


def test_model_rows_are_explicitly_calculated_from_assumptions() -> None:
    rows = model.model_rows(mau=10_000)

    assert len(rows) == 6
    assert rows[0] == {
        "sla_latency_ms": 200.0,
        "retention_rate": 0.55,
        "arpu_rub": 5200.0,
        "monthly_revenue_rub": 28_600_000.0,
    }
    assert rows[-1]["monthly_revenue_rub"] == pytest.approx(3_240_000.0)
    assert all(math.isfinite(value) for row in rows for value in row.values())


def test_report_cannot_be_mistaken_for_observed_business_data() -> None:
    report = model.build_report(mau=25_000)

    assert report["ok"] is True
    assert report["data_origin"] == "illustrative_synthetic"
    assert report["decision_use"] == "demo_only_not_observed_business_data"
    assert report["observed_production_data"] is False
    assert report["mau_assumption"] == 25_000
    assert report["currency"] == "RUB"
    assert report["model_assumptions"] == {
        "retention_and_arpu_are_fixed_synthetic_inputs": True,
        "causality_is_not_established": True,
        "forecast_or_valuation_use_is_prohibited": True,
    }
    assert report["error_code"] == ""


@pytest.mark.parametrize(
    "value,code",
    [
        (True, "mau_must_be_integer"),
        (0, "mau_must_be_positive"),
        (-10, "mau_must_be_positive"),
        (model.MAX_MAU + 1, "mau_exceeds_safe_limit"),
    ],
)
def test_mau_validation_is_fail_closed(value: Any, code: str) -> None:
    with pytest.raises(model.SlaModelInputError) as exc:
        model.model_rows(mau=value)

    assert exc.value.code == code


def test_model_series_validation_rejects_invalid_assumptions() -> None:
    with pytest.raises(model.SlaModelInputError, match="model_series_length_mismatch"):
        model.model_rows(latency_ms=(100.0,), retention_rate=(0.5, 0.4), arpu_rub=(1000.0,))
    with pytest.raises(model.SlaModelInputError, match="latency_series_invalid"):
        model.model_rows(latency_ms=(float("nan"),), retention_rate=(0.5,), arpu_rub=(1000.0,))
    with pytest.raises(model.SlaModelInputError, match="retention_series_invalid"):
        model.model_rows(latency_ms=(100.0,), retention_rate=(1.1,), arpu_rub=(1000.0,))
    with pytest.raises(model.SlaModelInputError, match="arpu_series_invalid"):
        model.model_rows(latency_ms=(100.0,), retention_rate=(0.5,), arpu_rub=(-1.0,))


def test_regular_cli_never_plots_without_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        model,
        "show",
        lambda **kwargs: pytest.fail("show called without --plot"),
    )

    assert model.main(["--mau", "10000", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["data_origin"] == "illustrative_synthetic"
    assert payload["observed_production_data"] is False
    assert len(payload["rows"]) == 6


def test_human_output_contains_prominent_synthetic_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        model,
        "show",
        lambda **kwargs: pytest.fail("show called without --plot"),
    )

    assert model.main(["--mau", "10000"]) == 0
    output = capsys.readouterr().out

    assert "data_origin=illustrative_synthetic" in output
    assert "demo_only_not_observed_business_data" in output
    assert "not observed production analytics" in output


def test_strict_invalid_input_returns_safe_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert model.main(["--mau", "0", "--json", "--strict"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "ok": False,
        "data_origin": "illustrative_synthetic",
        "decision_use": "demo_only_not_observed_business_data",
        "observed_production_data": False,
        "error_code": "mau_must_be_positive",
    }


def test_json_never_contains_nan_or_infinity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert model.main(["--json"]) == 0
    raw = capsys.readouterr().out
    payload = json.loads(raw)

    assert "NaN" not in raw
    assert "Infinity" not in raw
    assert all(
        not isinstance(value, float) or math.isfinite(value)
        for row in payload["rows"]
        for value in row.values()
    )
