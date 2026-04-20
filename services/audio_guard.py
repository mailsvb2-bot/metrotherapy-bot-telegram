from __future__ import annotations
import logging


from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from services.catalog import DEMO_DIR, FULL_DIR, EXTS
"""Audio access guard.

NOTE: use services.access.has_access() (wrapper) because
services.subscription.has_access() expects (user_id, slot=...) and doesn't
accept the keyword `required_scope`.
"""

from services.access import has_access


@dataclass
class AudioGuardResult:
    ok: bool
    message: str | None = None
    paths: list[Path] | None = None


def _scan(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTS]
    files.sort(key=lambda p: p.name.lower())
    return files


def pick_demo_file(kind: str) -> Path | None:
    """Подбирает демо-файл по kind (work/home) максимально совместимо.

    Правила:
    1) {kind}.ogg / {kind}.opus / {kind}.*
    2) если в DEMO_DIR ровно 2 файла — work=первый, home=второй
    """
    kind = (kind or "work").strip().lower()
    if kind not in ("work", "home"):
        kind = "work"

    # 1) точные имена с поддерживаемыми расширениями
    for ext in EXTS:
        p = DEMO_DIR / f"{kind}{ext}"
        if p.exists():
            return p

    # 2) любое расширение (на случай экзотики), но файл существует
    try:
        for p in DEMO_DIR.iterdir():
            if p.is_file() and p.stem.lower() == kind:
                return p
    except OSError:
        logging.getLogger(__name__).exception("DEMO_DIR scan failed")

    # 3) ровно 2 файла — выбираем по порядку
    files = _scan(DEMO_DIR)
    if len(files) == 2:
        return files[0] if kind == "work" else files[1]

    return None


def get_full_files_guarded(user_id: int, required_scope: str = "both") -> AudioGuardResult:
    """Возвращает полный каталог, но только если есть доступ и есть файлы."""
    if not has_access(user_id, required_scope=required_scope):
        return AudioGuardResult(
            ok=False,
            message="🔐 Полный доступ доступен по подписке.\n\nНажми «💳 Подписка / тарифы».",
        )

    files = _scan(FULL_DIR)
    if not files:
        return AudioGuardResult(
            ok=False,
            message="Полные аудио не найдены. Проверь папку audio/Full",
        )

    return AudioGuardResult(ok=True, paths=files)
