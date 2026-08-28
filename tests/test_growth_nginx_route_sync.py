from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import sync_growth_nginx_route as route_sync


TARGET = route_sync.TARGET_HOST


def _config(*, location: str = "") -> str:
    return f'''server {{
    listen 80;
    server_name {TARGET};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {TARGET};

    location /healthz {{
        proxy_pass http://127.0.0.1:8082/healthz;
    }}
{location}
}}
'''


def test_patch_adds_growth_route_only_to_tls_server() -> None:
    updated, changed = route_sync._patch_config_text(_config())

    assert changed is True
    assert updated.count(route_sync.MARKER) == 1
    tls = [block for block in route_sync._server_blocks(updated) if route_sync._is_tls(block[2])]
    plain = [block for block in route_sync._server_blocks(updated) if not route_sync._is_tls(block[2])]
    assert len(tls) == 1
    assert "location /a/" in tls[0][2]
    assert "proxy_pass http://127.0.0.1:8082;" in tls[0][2]
    assert all("location /a/" not in block[2] for block in plain)


def test_patch_is_idempotent_for_canonical_growth_route() -> None:
    first, changed = route_sync._patch_config_text(_config())
    assert changed is True

    second, changed = route_sync._patch_config_text(first)
    assert changed is False
    assert second == first


def test_patch_fails_closed_for_conflicting_growth_route() -> None:
    location = '''
    location /a/ {
        proxy_pass http://127.0.0.1:8081;
    }
'''
    with pytest.raises(route_sync.GrowthRouteSyncError, match="does not proxy"):
        route_sync._patch_config_text(_config(location=location))


def test_patch_requires_exact_tls_vhost() -> None:
    wrong = _config().replace(TARGET, "other.example")
    with pytest.raises(route_sync.GrowthRouteSyncError, match="no TLS server block"):
        route_sync._patch_config_text(wrong)


def test_active_config_discovery_uses_nginx_dump_and_resolves_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "metrotherapy.conf"
    real.write_text(_config(), encoding="utf-8")
    enabled = tmp_path / "enabled.conf"
    enabled.symlink_to(real)
    dump = f"# configuration file {enabled}:\n{_config()}\n"
    monkeypatch.setattr(route_sync, "_run", lambda _command: SimpleNamespace(stdout=dump, stderr=""))

    assert route_sync._active_target_config("/usr/sbin/nginx", host=TARGET) == real.resolve()


def test_active_config_discovery_fails_on_ambiguity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first.conf"
    second = tmp_path / "second.conf"
    first.write_text(_config(), encoding="utf-8")
    second.write_text(_config(), encoding="utf-8")
    dump = (
        f"# configuration file {first}:\n{_config()}\n"
        f"# configuration file {second}:\n{_config()}\n"
    )
    monkeypatch.setattr(route_sync, "_run", lambda _command: SimpleNamespace(stdout=dump, stderr=""))

    with pytest.raises(route_sync.GrowthRouteSyncError, match="exactly one active TLS config"):
        route_sync._active_target_config("/usr/sbin/nginx", host=TARGET)
