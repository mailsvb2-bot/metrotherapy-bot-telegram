from __future__ import annotations


def _pricing():
    # Lazy import to keep interface layer free from direct economy module imports (Decision Sovereignty).
    from services import pricing as _p
    return _p

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers.admin_tariffs.common import TariffsCtx, log, parse_price_int
from handlers.admin_tariffs.ui import render_tariffs_menu
from handlers.admin_tariffs.ui import _kb_tariffs_nav
async def admin_tariffs_input(msg: Message, state: FSMContext, *, admin_id: int | None = None) -> None:
    text = (msg.text or "").strip()
    if not text:
        return

    # массовый ввод: title/code=price
    updates = []
    bad = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            bad.append(line)
            continue
        k, v = [x.strip() for x in line.split("=", 1)]
        price = parse_price_int(v)
        if price is None:
            bad.append(line)
            continue
        updates.append((k, price))

    if not updates:
        await msg.answer("Не вижу ни одной строки вида <тариф>=<цена>. Пример: Утро — 1 неделя=990")
        return

    ok, report = _pricing().set_plan_prices_by_titles_verbose(dict(updates), changed_by=admin_id)
    # report уже человекочитаемый
    await msg.answer(report)

    if bad:
        await msg.answer("⚠️ Не распознаны строки:\n" + "\n".join(bad))

    await state.clear()

    await msg.answer("Готово.", reply_markup=_kb_tariffs_nav())




async def admin_tariff_single_price_input(msg: Message, state: FSMContext, *, admin_id: int | None = None) -> None:
    price = parse_price_int(msg.text or "")
    if price is None:
        await msg.answer("Введите целое число (например 990).")
        return

    data = await state.get_data()
    code = str(data.get("tariff_code") or "").strip()
    if not code:
        await msg.answer("Не знаю, какой тариф вы выбрали. Откройте: Админка → Тарифы → Изменить.")
        await state.clear()
        return

    ok = _pricing().set_plan_price_by_code(code, price, changed_by=admin_id)
    if ok:
        await msg.answer(f"✅ Цена тарифа {code} обновлена: {price} ₽")
    else:
        # подсказка по похожим названиям
        suggestions = _pricing().suggest_plan_titles(code)
        hint = ("\nПохожие тарифы: " + ", ".join(suggestions)) if suggestions else ""
        await msg.answer(f"❌ Не удалось обновить тариф {code}.{hint}")

    await state.clear()

    await msg.answer("Готово.", reply_markup=_kb_tariffs_nav())