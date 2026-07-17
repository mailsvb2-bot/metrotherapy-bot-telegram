from scripts.check_release_hygiene import ALLOWED_ROOT_FILES


def test_dependency_source_and_lock_files_are_release_artifacts() -> None:
    assert {
        "requirements.in",
        "requirements-dev.in",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-py313.txt",
    } <= ALLOWED_ROOT_FILES
