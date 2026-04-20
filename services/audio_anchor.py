from __future__ import annotations
import logging


import re
from dataclasses import dataclass
from pathlib import Path

from services.catalog import FULL_DIR, EXTS


# Имя файла может быть: "001_что-то", "01.что-то", "1 что-то", "1-что-то" и т.п.
# Требование проекта: нечётные номера → work, чётные → home,
# независимо от разделителей после числа.
LEAD_NUM_RE = re.compile(r"^\s*(\d{1,4})\s*[^\d\wА-Яа-я]*\s*(.*?)\s*$")


@dataclass(frozen=True)
class AnchoredAudio:
    anchor: int
    path: Path
    clean_title: str

    @property
    def is_morning(self) -> bool:
        return self.anchor % 2 == 1

    @property
    def is_evening(self) -> bool:
        return self.anchor % 2 == 0


def parse_anchor(filename: str) -> AnchoredAudio | None:
    """Парсит якорь из имени файла.

    Пример: "11 утреннее пробуждение.ogg" -> anchor=11, clean_title="утреннее пробуждение"
    """
    stem = Path(filename).stem
    m = LEAD_NUM_RE.match(stem)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except (ValueError, OSError) as e:
        log.warning("Audio anchor scan error: %s", e)
        logging.getLogger(__name__).exception("Unhandled exception")
        return None
    if n < 1 or n > 1000:
        return None
    title = (m.group(2) or "").strip()
    # Если после номера нет осмысленного текста — используем stem как заголовок.
    if not title:
        title = stem.strip()
    return AnchoredAudio(anchor=n, path=Path(filename), clean_title=title)


def scan_full_anchored() -> list[AnchoredAudio]:
    """Сканирует FULL_DIR и возвращает аудио с якорями."""
    if not FULL_DIR.exists():
        return []
    out: list[AnchoredAudio] = []
    for p in FULL_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
        aa = parse_anchor(p.name)
        if aa:
            out.append(AnchoredAudio(anchor=aa.anchor, path=p, clean_title=aa.clean_title))
    out.sort(key=lambda x: (x.anchor, x.path.name.lower()))
    return out


def pick_for_slot(slot: str, index: int = 0) -> AnchoredAudio | None:
    """Возвращает файл для слота morning/evening по индексу (циклично)."""
    slot = (slot or "").strip().lower()
    items = scan_full_anchored()
    if not items:
        return None
    if slot in ("morning", "work"):
        pool = [x for x in items if x.is_morning]
    else:
        pool = [x for x in items if x.is_evening]
    if not pool:
        return None
    i = int(index) % len(pool)
    return pool[i]


def get_by_anchor(anchor: int) -> AnchoredAudio | None:
    """Возвращает аудио из FULL_DIR по anchor.

    Используется в UX, где аудио отправляется после pre-оценки:
    мы сохраняем anchor_id в mood_sessions, а затем по нему достаём
    правильный файл.
    """
    try:
        a = int(anchor)
    except (ValueError, OSError) as e:
        log.warning("Audio anchor scan error: %s", e)
        logging.getLogger(__name__).exception("Unhandled exception")
        return None
    items = scan_full_anchored()
    for it in items:
        if int(it.anchor) == a:
            return it
    return None
