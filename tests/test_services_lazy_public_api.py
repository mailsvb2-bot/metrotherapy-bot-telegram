from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_leaf_service_import_does_not_bootstrap_database_or_schema() -> None:
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "import services.practice_token_contract as contract; "
        "assert contract.package_by_id('practice_start_7').tokens == 7; "
        "assert 'services.db' not in sys.modules; "
        "assert 'services.schema' not in sys.modules; "
        "assert 'services.store' not in sys.modules; "
        "assert 'services.subscription' not in sys.modules; "
        "assert 'services.access' not in sys.modules"
    )
    completed = _python(code)

    assert completed.returncode == 0, completed.stderr


def test_legacy_db_export_remains_callable_canonical_package() -> None:
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "from services import db; "
        "import services.db as package; "
        "assert db is package; "
        "assert callable(db); "
        "assert db.__name__ == 'services.db'"
    )
    completed = _python(code)

    assert completed.returncode == 0, completed.stderr


def test_public_api_is_discoverable_without_eager_resolution() -> None:
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "import services; "
        "expected={'db','get_db','tx','init_db','store','has_access','is_active','get_scope',"
        "'has_active_subscription','get_subscription_scope','grant_subscription'}; "
        "assert expected.issubset(set(dir(services))); "
        "assert 'services.db' not in sys.modules"
    )
    completed = _python(code)

    assert completed.returncode == 0, completed.stderr
