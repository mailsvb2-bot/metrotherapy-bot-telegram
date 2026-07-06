from __future__ import annotations

"""Small audited subprocess boundary.

The project still needs subprocess for operator/CI commands. Keep the raw
standard-library call here instead of scattering it through runtime and scripts.
Callers must pass an absolute executable path, usually ``sys.executable`` or a
path resolved by ``shutil.which``.
"""

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEVNULL = subprocess.DEVNULL


class CommandTimeoutError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stdout: str | bytes | None = None,
        stderr: str | bytes | None = None,
    ) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class PipelineResult:
    producer_returncode: int
    consumer_returncode: int
    output: str


def _normalize_cmd(cmd: Sequence[str | os.PathLike[str]]) -> list[str]:
    normalized = [os.fspath(part) for part in cmd]
    if not normalized:
        raise ValueError("command must not be empty")
    executable = Path(normalized[0])
    if not executable.is_absolute():
        raise ValueError(f"command executable must be an absolute path: {normalized[0]}")
    return normalized


def run_command(
    cmd: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    capture_output: bool = False,
    text: bool | None = None,
    timeout: float | None = None,
    input: str | bytes | None = None,
) -> subprocess.CompletedProcess[Any]:
    safe_cmd = _normalize_cmd(cmd)
    try:
        return subprocess.run(
            safe_cmd,
            cwd=os.fspath(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            check=check,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            input=input,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandTimeoutError(
            f"command timed out: {safe_cmd[0]}",
            stdout=exc.stdout,
            stderr=exc.stderr,
        ) from exc


def spawn_command(
    cmd: Sequence[str | os.PathLike[str]],
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    safe_cmd = _normalize_cmd(cmd)
    return subprocess.Popen(safe_cmd, **kwargs)


def run_pipeline(
    producer_cmd: Sequence[str | os.PathLike[str]],
    consumer_cmd: Sequence[str | os.PathLike[str]],
) -> PipelineResult:
    safe_producer = _normalize_cmd(producer_cmd)
    safe_consumer = _normalize_cmd(consumer_cmd)

    producer = subprocess.Popen(safe_producer, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert producer.stdout is not None
    consumer = subprocess.Popen(
        safe_consumer,
        stdin=producer.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    producer.stdout.close()
    consumer_stdout, consumer_stderr = consumer.communicate()
    producer_stderr = producer.stderr.read() if producer.stderr else b""
    producer_returncode = producer.wait()
    output = b"".join(
        part for part in (consumer_stdout, consumer_stderr, producer_stderr) if part
    ).decode("utf-8", errors="replace")
    return PipelineResult(
        producer_returncode=int(producer_returncode),
        consumer_returncode=int(consumer.returncode),
        output=output,
    )
