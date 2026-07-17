from __future__ import annotations

from types import SimpleNamespace

import pytest

from handlers import info


class FakeMessage:
    def __init__(self, user_id: int, text: str = "") -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.text = text
        self.answers: list[str] = []
        self.documents: list[tuple[object, str | None]] = []

    async def answer(self, text: str, **_kwargs) -> None:
        self.answers.append(text)

    async def answer_document(self, document, *, caption: str | None = None, **_kwargs) -> None:
        self.documents.append((document, caption))


def test_delete_confirmation_is_exact() -> None:
    assert info._delete_confirmed("/deletemydata CONFIRM") is True
    assert info._delete_confirmed("/deletemydata confirm") is True
    assert info._delete_confirmed("/deletemydata") is False
    assert info._delete_confirmed("/deletemydata YES") is False
    assert info._delete_confirmed("/deletemydata CONFIRM extra") is False


@pytest.mark.asyncio
async def test_export_uses_authenticated_message_user(monkeypatch) -> None:
    seen: list[int] = []

    def fake_export(user_id: int):
        seen.append(user_id)
        return {"user_id": user_id, "tables": {"events": []}}

    monkeypatch.setattr(info, "export_user_data_snapshot", fake_export)
    message = FakeMessage(91001, "/mydata")

    await info.cmd_my_data(message)

    assert seen == [91001]
    assert len(message.documents) == 1
    assert not message.answers


@pytest.mark.asyncio
async def test_delete_without_confirmation_does_not_mutate(monkeypatch) -> None:
    called = False

    def fake_erase(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not erase without confirmation")

    monkeypatch.setattr(info, "erase_user_behavioral_data", fake_erase)
    message = FakeMessage(91002, "/deletemydata")

    await info.cmd_delete_my_data(message)

    assert called is False
    assert message.answers
    assert "/deletemydata CONFIRM" in message.answers[0]


@pytest.mark.asyncio
async def test_confirmed_delete_uses_authenticated_message_user(monkeypatch) -> None:
    seen: list[tuple[int, str]] = []

    def fake_erase(user_id: int, *, reason: str):
        seen.append((user_id, reason))
        return SimpleNamespace(deleted_tables={"events": 3, "jobs": 2})

    monkeypatch.setattr(info, "erase_user_behavioral_data", fake_erase)
    message = FakeMessage(91003, "/deletemydata CONFIRM")

    await info.cmd_delete_my_data(message)

    assert seen == [(91003, "telegram_user_request")]
    assert message.answers
    assert "Удалено записей: 5" in message.answers[-1]
