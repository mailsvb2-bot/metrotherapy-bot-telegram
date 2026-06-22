# Messenger live smoke checklist

Use this checklist after every change to MAX/VK buttons, payload extraction, webhook routing, or reply rendering.

## Scope

Admin/control-plane is Telegram-only. Do not test or expose admin buttons in VK/MAX.

## Preconditions

- Server is on `main` and clean.
- `python -m pytest -q -p no:cacheprovider` is green.
- `python scripts/post_deploy_verify.py` is green.
- MAX/VK webhook secrets and public URLs are configured only on the target server.

## MAX manual pass

1. Open the MAX bot.
2. Send `/start` or tap start.
3. Confirm main menu has these user actions: 🌿 Попробовать бесплатно, 🔐 Полный маршрут, 💳 Тарифы, 🎁 Подарить, 📈 Мой прогресс, 🧠 Настройки, 📣 Посоветовать, 🌤 Погода.
4. Confirm there is no 🛠 Панель button.
5. Tap 🌿 Попробовать бесплатно.
6. Confirm route buttons: 🚗 Практика на утро / дорогу, 🌙 Практика на вечер / домой, ⬅️ Меню.
7. Tap one route and confirm score scale appears.
8. Tap score buttons including -10, 0, 1, 2, 10. Confirm 1/2 are saved as scores, not demo route choices.
9. Confirm audio/send-link step appears.
10. Tap ✅ Прослушал and confirm post-score scale appears.
11. Tap 🔐 Полный маршрут and confirm 🎧 Получить аудио, ✅ Прослушал, ⬅️ Меню.
12. Tap 🌤 Погода and confirm 🔄 Обновить погоду, 🏙 Изменить город, ⬅️ Меню.
13. Tap 📈 Мой прогресс and confirm 🎧 Получить аудио, ✅ Прослушал, 🔁 Повторить аудио, 🧾 История, ⬅️ Назад.
14. Tap 🧠 Настройки and confirm public settings buttons only.
15. Tap 💳 Тарифы and 🎁 Подарить and confirm link buttons are rendered.

## VK manual pass

1. Open the VK bot/chat.
2. Send `/start` or `start`.
3. Confirm main menu has the same eight public user actions as Telegram.
4. Confirm there is no 🛠 Панель button.
5. Tap 🌿 Попробовать бесплатно.
6. Confirm route buttons: 🚗 Практика на утро / дорогу, 🌙 Практика на вечер / домой, ⬅️ Назад.
7. Tap one route and confirm score scale appears.
8. Tap score buttons including -10, 0, +1, +2, +10. Confirm payloads are treated as scores.
9. Confirm audio/send-link step appears.
10. Tap ✅ Прослушал and confirm post-score scale appears.
11. Tap 🔐 Полный маршрут and confirm 🎧 Получить аудио, ✅ Прослушал, ⬅️ Назад.
12. Tap 🌤 Погода and confirm 🌤 Погода, 🏙 Изменить город, ⬅️ Назад.
13. Tap 📈 Мой прогресс and confirm 🎧 Получить аудио, ✅ Прослушал, 🔁 Повторить аудио, 🧾 История, ⬅️ Назад.
14. Tap 🧠 Настройки and confirm public settings buttons only.
15. Tap 💳 Тарифы and 🎁 Подарить and confirm link buttons are rendered.

## Evidence to keep

- Screenshot of main menu in MAX and VK.
- Screenshot of score scale in MAX and VK.
- Screenshot of weather surface in MAX and VK.
- Screenshot of progress surface in MAX and VK.
- Server log lines showing payload normalization and action completion.

## Stop condition

Live smoke is complete only when both platforms pass without any decorative/dead button and without exposing Telegram admin controls.
