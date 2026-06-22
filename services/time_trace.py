from __future__ import annotations

"""Diagnostics for HH:MM input routing.

We had a real production issue where a generic text handler (e.g. weather city input)
intercepted a HH:MM message and prevented the settings time handler from running.

This module provides a tiny context-local trace that allows any handler to mark
that it has seen a HH:MM input, so we can log "who intercepted" quickly.

No UX changes.
"""

import contextvars
from dataclasses import dataclass


@dataclass
class TimeTrace:
    uid: int
    text: str
    marks: list[str]


_trace: contextvars.ContextVar[TimeTrace | None] = contextvars.ContextVar("time_trace", default=None)


def begin(uid: int, text: str) -> None:
    """Start a trace for current task."""
    _trace.set(TimeTrace(uid=int(uid), text=str(text), marks=[]))


def mark(handler_name: str) -> None:
    """Mark that the given handler has seen the HH:MM message."""
    t = _trace.get()
    if not t:
        return
    t.marks.append(str(handler_name))


def end() -> TimeTrace | None:
    """Return current trace (if any) and clear it."""
    t = _trace.get()
    _trace.set(None)
    return t
