from __future__ import annotations


from dataclasses import dataclass


@dataclass
class GiftTarget:
    to_id: int
    to_name: str | None = None


_targets: dict[int, GiftTarget] = {}


def set_target(from_id: int, to_id: int, to_name: str | None = None):
    _targets[int(from_id)] = GiftTarget(to_id=int(to_id), to_name=to_name)


def get_target(from_id: int) -> GiftTarget | None:
    return _targets.get(int(from_id))


def clear_target(from_id: int):
    _targets.pop(int(from_id), None)
