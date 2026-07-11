from __future__ import annotations

import pytest

from services.validators.base import ValidationError
from services.validators.prod import validate_prod_admin_contract


def test_prod_admin_contract_rejects_non_numeric_admin_ids(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("ADMIN_IDS", "abc")
    monkeypatch.delenv("ADMIN_ID", raising=False)

    with pytest.raises(ValidationError, match="Production admin contract failed"):
        validate_prod_admin_contract(strict=True)


def test_prod_admin_contract_rejects_partially_invalid_admin_ids(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("ADMIN_IDS", "123,wrong,456")
    monkeypatch.delenv("ADMIN_ID", raising=False)

    with pytest.raises(ValidationError, match="wrong"):
        validate_prod_admin_contract(strict=True)


def test_prod_admin_contract_accepts_positive_numeric_admin_ids(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("ADMIN_IDS", "123,456")
    monkeypatch.delenv("ADMIN_ID", raising=False)

    validate_prod_admin_contract(strict=True)
