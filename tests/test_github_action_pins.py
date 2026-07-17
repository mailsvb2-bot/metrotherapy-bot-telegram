from pathlib import Path

from scripts.check_github_action_pins import mutable_action_references


def test_repository_workflows_pin_external_actions_to_commit_sha() -> None:
    assert mutable_action_references() == []


def test_pin_gate_rejects_mutable_tags_and_allows_local_actions(tmp_path: Path) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "bad.yml").write_text(
        """
steps:
  - uses: actions/checkout@v4
  - uses: ./local-action
""".strip(),
        encoding="utf-8",
    )

    problems = mutable_action_references(workflows)

    assert len(problems) == 1
    assert "actions/checkout@v4" in problems[0]


def test_pin_gate_accepts_exact_commit_sha(tmp_path: Path) -> None:
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "good.yaml").write_text(
        "steps:\n  - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5\n",
        encoding="utf-8",
    )

    assert mutable_action_references(workflows) == []
