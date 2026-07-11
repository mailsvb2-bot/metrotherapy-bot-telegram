from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NGINX_CONFIG = ROOT / "deploy" / "nginx-metrotherapy.conf"


def _config() -> str:
    return NGINX_CONFIG.read_text(encoding="utf-8")


def test_nginx_routes_growth_clicks_to_health_runtime():
    text = _config()
    start = text.index("location /a/")
    end = text.index("location /", start + len("location /a/"))
    block = text[start:end]

    assert "proxy_pass http://127.0.0.1:8082" in block


def test_nginx_routes_readyz_to_readiness_runtime():
    text = _config()
    start = text.index("location /readyz")
    end = text.index("location /a/", start)
    block = text[start:end]

    assert "proxy_pass http://127.0.0.1:8082/readyz" in block


def test_nginx_keeps_general_http_ingress_on_8081():
    text = _config()
    start = text.rindex("location /")
    block = text[start:]

    assert "proxy_pass http://127.0.0.1:8081" in block
