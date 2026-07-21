from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import audio_asset_integrity as integrity


def _asset_tree(root: Path, *, suffix: str = "") -> Path:
    asset = root / f"asset{suffix}"
    (asset / "demo").mkdir(parents=True)
    (asset / "full").mkdir(parents=True)
    (asset / "demo" / "work.ogg").write_bytes(b"OggS" + b"w" * 128)
    (asset / "demo" / "home.ogg").write_bytes(b"OggS" + b"h" * 128)
    (asset / "full" / "1_work.ogg").write_bytes(b"OggS" + b"1" * 128)
    (asset / "full" / "2_home.ogg").write_bytes(b"OggS" + b"2" * 128)
    for directory in (asset, asset / "demo", asset / "full"):
        directory.chmod(0o750)
    for file_path in asset.rglob("*.ogg"):
        file_path.chmod(0o640)
    return asset


def _publish(root: Path, source: Path) -> integrity.AudioAssetInfo:
    digest, _count = integrity.asset_tree_sha256(source)
    target = root / "audio-releases" / digest
    target.parent.mkdir(parents=True)
    source.rename(target)
    return integrity.seal_asset_dir(target)


def test_versioned_asset_seal_validate_and_tamper_detection(tmp_path: Path) -> None:
    info = _publish(tmp_path, _asset_tree(tmp_path))

    assert Path(info.asset_dir).name == info.asset_sha256
    assert info.file_count == 4
    assert integrity.validate_asset_dir(info.asset_dir) == info

    (Path(info.asset_dir) / "full" / "1_work.ogg").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="content changed"):
        integrity.validate_asset_dir(info.asset_dir)


def test_asset_digest_rejects_symlinks_and_empty_trees(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="empty"):
        integrity.asset_tree_sha256(empty)

    source = _asset_tree(tmp_path, suffix="-link")
    (source / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="must not contain symlinks"):
        integrity.asset_tree_sha256(source)


def test_release_pointer_pins_exact_asset_version(tmp_path: Path) -> None:
    first = _publish(tmp_path, _asset_tree(tmp_path, suffix="-one"))
    second_source = _asset_tree(tmp_path, suffix="-two")
    (second_source / "full" / "2_home.ogg").write_bytes(b"OggS" + b"new" * 80)
    second = _publish(tmp_path, second_source)

    release = tmp_path / "release"
    release.mkdir()
    (release / "audio").symlink_to(first.asset_dir)
    integrity.write_release_pointer(release, first.asset_dir)
    assert integrity.validate_release_assets(release, require_versioned=True) == first

    (release / "audio").unlink()
    (release / "audio").symlink_to(second.asset_dir)
    with pytest.raises(ValueError, match="does not match"):
        integrity.validate_release_assets(release, require_versioned=True)

    assert first.asset_sha256 != second.asset_sha256


def test_deployment_proof_contains_current_and_previous_asset_evidence(tmp_path: Path) -> None:
    first = _publish(tmp_path, _asset_tree(tmp_path, suffix="-previous"))
    second_source = _asset_tree(tmp_path, suffix="-current")
    (second_source / "demo" / "work.ogg").write_bytes(b"OggS" + b"current" * 32)
    second = _publish(tmp_path, second_source)

    previous_release = tmp_path / "previous"
    current_release = tmp_path / "current"
    previous_release.mkdir()
    current_release.mkdir()
    (previous_release / "audio").symlink_to(first.asset_dir)
    (current_release / "audio").symlink_to(second.asset_dir)
    integrity.write_release_pointer(previous_release, first.asset_dir)
    integrity.write_release_pointer(current_release, second.asset_dir)

    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps({"deployed_sha": "a" * 40}), encoding="utf-8")
    payload = integrity.augment_deployment_proof(
        proof,
        current_release=current_release,
        previous_release=previous_release,
    )

    assert payload["audio_asset_sha256"] == second.asset_sha256
    assert payload["previous_audio_asset_sha256"] == first.asset_sha256
    assert payload["audio_asset_file_count"] == 4
    assert proof.stat().st_mode & 0o777 == 0o644


def test_referenced_asset_dirs_only_returns_release_pointers(tmp_path: Path) -> None:
    info = _publish(tmp_path, _asset_tree(tmp_path))
    releases = tmp_path / "releases"
    release = releases / ("a" * 40)
    release.mkdir(parents=True)
    (release / "audio").symlink_to(info.asset_dir)
    integrity.write_release_pointer(release, info.asset_dir)
    (releases / ("b" * 40)).mkdir()

    assert integrity.referenced_asset_dirs(releases) == (str(Path(info.asset_dir).resolve()),)
