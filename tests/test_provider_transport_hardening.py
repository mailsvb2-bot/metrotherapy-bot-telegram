from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.messenger import provider_transport


@pytest.mark.parametrize(
    "url,code",
    [
        ("http://upload.example/file", "upload_url_https_required"),
        ("https://user:pass@upload.example/file", "upload_url_credentials_forbidden"),
        ("https://localhost/file", "upload_url_local_host_forbidden"),
        ("https://127.0.0.1/file", "upload_url_private_ip_forbidden"),
        ("https://10.0.0.5/file", "upload_url_private_ip_forbidden"),
        ("https://[::1]/file", "upload_url_private_ip_forbidden"),
    ],
)
def test_upload_url_rejects_cleartext_credentials_and_private_targets(url: str, code: str) -> None:
    with pytest.raises(provider_transport.ProviderUploadURLRejected, match=code):
        provider_transport.validate_provider_upload_url(url)


def test_deployed_upload_url_rejects_dns_with_private_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(
        provider_transport.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("192.168.1.10", 443))],
    )

    with pytest.raises(
        provider_transport.ProviderUploadURLRejected,
        match="upload_url_non_public_dns_target",
    ):
        provider_transport.validate_provider_upload_url("https://upload.example/file")


def test_deployed_upload_url_accepts_only_public_dns_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(
        provider_transport.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("203.0.113.20", 443))],
    )
    monkeypatch.setattr(provider_transport, "_public_ip", lambda address: address == "203.0.113.20")

    parsed = provider_transport.validate_provider_upload_url("https://upload.example/path?q=1")

    assert parsed.hostname == "upload.example"
    assert parsed.path == "/path"


def test_multipart_upload_streams_file_without_read_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    source = tmp_path / "audio.bin"
    source.write_bytes(b"a" * (provider_transport._UPLOAD_CHUNK_SIZE + 17))
    sent: list[bytes] = []
    headers: dict[str, str] = {}

    class FakeResponse:
        status = 200
        reason = "OK"
        headers: dict[str, str] = {}

        @staticmethod
        def read() -> bytes:
            return json.dumps({"token": "uploaded"}).encode("utf-8")

    class FakeConnection:
        def __init__(self, host: str, port: int, *, timeout: float, context) -> None:
            assert host == "upload.example"
            assert port == 443
            assert timeout == 120
            assert context is None

        def putrequest(self, method: str, target: str) -> None:
            assert method == "POST"
            assert target == "/file"

        def putheader(self, name: str, value: str) -> None:
            headers[name] = value

        def endheaders(self) -> None:
            return None

        def send(self, data: bytes) -> None:
            sent.append(bytes(data))

        @staticmethod
        def getresponse() -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr(provider_transport.http.client, "HTTPSConnection", FakeConnection)
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _self: (_ for _ in ()).throw(AssertionError("read_bytes must not be used")),
    )

    result = provider_transport.multipart_upload(
        "https://upload.example/file",
        field_name="data",
        path=source,
        retries=1,
    )

    assert result == {"token": "uploaded"}
    assert len(sent) >= 4
    assert max(len(chunk) for chunk in sent[1:-1]) <= provider_transport._UPLOAD_CHUNK_SIZE
    assert int(headers["Content-Length"]) == sum(len(chunk) for chunk in sent)
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
