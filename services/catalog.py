import logging
from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = PROJECT_ROOT / "audio"


def _pick_subdir(base: Path, *candidates: str) -> Path:
    """Возвращает существующую подпапку внутри `base` по одному из имён.

    В исходных версиях/ОС могут отличаться регистр и написание папок:
    Demo/demo/DEMO, Full/full, а также русское "Демо/демо".
    Чтобы не ловить "аудиофайл не найден" при корректной структуре,
    выбираем первую реально существующую папку.
    """

    # 1) точное совпадение
    for name in candidates:
        p = base / name
        if p.exists() and p.is_dir():
            return p

    # 2) совпадение без учёта регистра
    try:
        items = {p.name.lower(): p for p in base.iterdir() if p.is_dir()}
        for name in candidates:
            p = items.get(name.lower())
            if p:
                return p
    except OSError:
        logging.getLogger(__name__).exception("AUDIO_DIR scan failed")

    # 3) дефолт (пусть будет первое имя)
    return base / (candidates[0] if candidates else "")


DEMO_DIR = _pick_subdir(AUDIO_DIR, "Demo", "demo", "DEMO", "Демо", "демо")
FULL_DIR = _pick_subdir(AUDIO_DIR, "Full", "full", "FULL", "Полный", "полный")

EXTS = (".ogg", ".opus", ".mp3", ".wav", ".m4a")
NUM_RE = re.compile(r"(\d+)")

def _num_key(name: str):
    m = NUM_RE.search(name)
    return int(m.group(1)) if m else 10**9

def _scan(folder: Path):
    if not folder.exists():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTS]
    files.sort(key=lambda p: (_num_key(p.name), p.name.lower()))
    return files

class AudioCatalog:
    def get_demo(self):
        return _scan(DEMO_DIR)

    def get_full(self):
        return _scan(FULL_DIR)
