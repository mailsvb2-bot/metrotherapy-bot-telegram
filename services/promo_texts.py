from __future__ import annotations
import logging


from pathlib import Path


def _root() -> Path:
    # project root = parent of /services
    return Path(__file__).resolve().parents[1]


def _read_text(path: Path) -> str | None:
    try:
        if path.exists() and path.is_file():
            txt = path.read_text(encoding="utf-8").strip()
            return txt or None
    except (OSError, UnicodeDecodeError):
        logging.getLogger(__name__).exception("promo_text read failed")
        return None
    return None


def get_share_template() -> str:
    """Template for recommendation message.

    Placeholders:
      {link} - deep link to bot
      {from_name} - sender display name
    """
    p = _root() / "data" / "promo_share.txt"
    txt = _read_text(p)
    if txt:
        return txt
    return (
        "Привет! Это рекомендация от {from_name}.\n\n"
        "🧠 Метротерапия — короткие аудио-ритмы (утро/вечер) для дороги, перезагрузки и ясной головы.\n"
        "Зайди в бота по ссылке и нажми Start — там есть демо и меню: \n{link}"
    )


def get_gift_template() -> str:
    """Template for gift message.

    Placeholders:
      {link} - deep link to bot gift activation
      {from_name} - sender display name
    """
    p = _root() / "data" / "promo_gift.txt"
    txt = _read_text(p)
    if txt:
        return txt
    return (
        "🎁 Вам подарили Метротерапию — подарок от {from_name}.\n\n"
        "Метротерапия — это переобучение нервной системы через ритм повседневности.\n"
        "Вы ничего не планируете и никуда специально не идёте — вы просто едете, и с вами происходит работа.\n\n"
        "Нажмите Start по ссылке, чтобы принять подарок и выбрать удобное время: \n{link}"
    )
