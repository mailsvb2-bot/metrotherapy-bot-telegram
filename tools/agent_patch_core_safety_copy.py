from __future__ import annotations

from pathlib import Path


TARGET = Path("core/engine.py")

REPLACEMENTS: tuple[tuple[str, str, int], ...] = (
    (
        'InlineKeyboardButton(text="💳 Подписка", callback_data="sub:menu")',
        'InlineKeyboardButton(text="💳 Пакеты практик", callback_data="sub:menu")',
        3,
    ),
    (
        'InlineKeyboardButton(text="🎁 Подарить подписку другу", callback_data="gift:menu")',
        'InlineKeyboardButton(text="🎁 Подарить пакет практик", callback_data="gift:menu")',
        1,
    ),
    (
        '            "Пожалуйста, по возможности наденьте наушники — так эффект ощущается глубже.\\n\\n"\n'
        '            "Если за рулём — просто включите и слушайте безопасно."',
        '            "Выберите спокойное место и, по возможности, наденьте наушники — так эффект ощущается глубже.\\n\\n"\n'
        '            "Не включайте практику за рулём или при управлении механизмами. "\n'
        '            "Дождитесь безопасной остановки или слушайте как пассажир."',
        1,
    ),
    (
        "        # Лимит бесплатных демо: максимум 2 (work + home). Дальше предлагаем подписку.",
        "        # Лимит бесплатных демо: максимум 2 (work + home). Дальше предлагаем пакет практик.",
        1,
    ),
    (
        '"✅ Вы уже получили оба ресурсных демо-транса.\\n\\nЕсли Вы хотите продолжить — пожалуйста, оформите подписку."',
        '"✅ Вы уже получили оба ресурсных демо-транса.\\n\\nЕсли хотите продолжить — выберите пакет практик."',
        1,
    ),
    (
        '"Вы можете послушать второй ресурсный демо-транс или оформить подписку."',
        '"Вы можете послушать второй ресурсный демо-транс или выбрать пакет практик."',
        1,
    ),
    (
        '"Если Вы уже послушали и хотите продолжить регулярно — можно выбрать подписку."',
        '"Если Вы уже послушали и хотите продолжить регулярно — выберите пакет практик."',
        1,
    ),
    (
        '"Если Вы хотите продолжить — можно выбрать подписку и открыть полный доступ. "',
        '"Если Вы хотите продолжить — выберите пакет практик и откройте полный маршрут. "',
        1,
    ),
    (
        '"Если хотите — откройте подписку и выберите удобный тариф."',
        '"Если хотите продолжить — выберите подходящий пакет практик."',
        1,
    ),
)


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    updated = source

    for old, new, expected_count in REPLACEMENTS:
        count = updated.count(old)
        if count != expected_count:
            raise SystemExit(
                f"refusing unsafe patch: expected {expected_count} occurrence(s), found {count}: {old[:100]!r}"
            )
        updated = updated.replace(old, new)

    if updated == source:
        raise SystemExit("refusing empty patch")
    if "Если за рулём — просто включите и слушайте безопасно." in updated:
        raise SystemExit("unsafe driving guidance remains")

    TARGET.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
