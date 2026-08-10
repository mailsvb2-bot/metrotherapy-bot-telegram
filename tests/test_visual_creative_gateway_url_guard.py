import pytest

from services import visual_creative_gateway as gateway


def test_base_url_rejects_invalid_port_and_snapshot_fails_closed(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal:not-a-port")
    with pytest.raises(gateway.VisualCreativeGatewayError, match="not_configured"):
        gateway._base_url()
    assert gateway.gateway_snapshot()["configured"] is False
