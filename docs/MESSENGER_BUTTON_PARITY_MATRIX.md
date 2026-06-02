# Messenger button parity matrix

Telegram is the source of truth for the public user UX. MAX and VK must preserve the same user actions, while admin/control-plane buttons remain Telegram-only.

## Canonical rule

- Telegram defines the meaning of each public button.
- VK and MAX must expose the same public user actions.
- Platform-specific labels/payloads are allowed only when they are documented here and covered by tests.
- Admin surfaces stay Telegram-only and must never be rendered in VK/MAX.

## Main menu parity

| Meaning | Telegram label | Telegram callback | VK label | VK command | MAX label | MAX command | Status |
|---|---|---:|---|---:|---|---:|---|
| Free practice | 🌿 Попробовать бесплатно | demo | 🌿 Попробовать бесплатно | demo | 🌿 Попробовать бесплатно | demo | OK |
| Full route | 🔐 Полный маршрут | full | 🔐 Полный маршрут | full | 🔐 Полный маршрут | full | OK |
| Payment plans | 💳 Тарифы | pay | 💳 Тарифы | pay | 💳 Тарифы | pay | OK |
| Gift | 🎁 Подарить | gift | 🎁 Подарить | gift | 🎁 Подарить | gift | OK |
| Progress | 📈 Мой прогресс | progress | 📈 Мой прогресс | progress | 📈 Мой прогресс | progress | OK |
| Settings | 🧠 Настройки | settings | 🧠 Настройки | settings | 🧠 Настройки | settings | OK |
| Share | 📣 Посоветовать | share | 📣 Посоветовать | share | 📣 Посоветовать | share | OK |
| Weather | 🌤 Погода | weather | 🌤 Погода | weather | 🌤 Погода | weather | OK |
| Admin panel | 🛠 Панель | admin | not rendered | n/a | not rendered | n/a | Telegram-only |

## Context surfaces

| Surface | VK labels / commands | MAX labels / commands | Notes |
|---|---|---|---|
| Demo route choice | 🚗 Практика на утро / дорогу → demo_work; 🌙 Практика на вечер / домой → demo_home; ⬅️ Назад → start | 🚗 Практика на утро / дорогу → demo_work; 🌙 Практика на вечер / домой → demo_home; ⬅️ Меню → start | MAX keeps legacy back label. |
| Full route | 🎧 Получить аудио → continue; ✅ Прослушал → done; ⬅️ Назад → start | 🎧 Получить аудио → continue; ✅ Прослушал → done; ⬅️ Меню → start | MAX keeps legacy back label. |
| Weather | 🌤 Погода → weather; 🏙 Изменить город → weather_city; ⬅️ Назад → start | 🔄 Обновить погоду → weather; 🏙 Изменить город → weather_city; ⬅️ Меню → start | MAX keeps legacy refresh/back labels. |
| Score scale | visible -10..0..+10; commands -10..10 | visible -10..10; commands score:-10..score:10 | MAX ingress preserves score intent for score:1/score:2 before text routing. |
| Progress | 🎧 Получить аудио → continue; ✅ Прослушал → done; 🔁 Повторить аудио → repeat; 🧾 История → history; ⬅️ Назад → start | same labels and commands | repeat normalizes to repeat_audio in text UI. |
| Settings | 🌦 Погода в моём городе → weather; ⏰ Время: дорога на работу → time; ⏰ Время: дорога домой → time; 🎁 Мои бонусы за приглашения → share; 💬 Предпочтительный мессенджер → settings; 📨 Каналы по времени дня → time; 📈 Анализ моего состояния → progress; ⬅️ Назад → start | same labels and commands | Free-form settings changes stay text-command based. |
| Delivery slots | 🌅 Утренние отправки → channel morning auto; 🌙 Вечерние отправки → channel evening auto; ⬅️ Назад → settings | same labels and commands | Channel-specific selection is available through deep slot selectors. |
| Delivery channel select | ♻️ Авто / telegram / max / vk → channel morning/evening ...; ⬅️ Назад → time | same labels and commands | Mirrors Telegram public delivery channel selector. |
| State analysis | ⭐ Оценить состояние сейчас; 📅 Сегодня; 📆 Вчера; 🗓 За всё время; 🔐 Открыть полный маршрут; 🎁 Подарить; ⬅️ Меню | same labels and commands | Public state surface only; no admin analytics. |
| Post actions | 📈 Посмотреть изменение состояния; 🔐 Открыть полный маршрут; 🎧 Ещё одна бесплатная практика; 🎁 Подарить подписку; ⬅️ Главное меню | same labels and commands | Mirrors Telegram post-audio/post-chart public actions. |
| Sales/ref/locked/full-access | Sales offer, full-access prompt, locked-settings prompt and referral bonus actions | same labels and commands | Telegram-native friend picker remains a platform exception. |
| Payment/gift | package link buttons + ⬅️ Назад | package link buttons + ⬅️ Назад | Payment transport differs from Telegram invoice, but action meaning is the same. |

## Platform exceptions

- Admin/control-plane is Telegram-only.
- Telegram-native friend picker (`KeyboardButtonRequestUser`) cannot be rendered 1:1 in MAX/VK; VK/MAX use gift/share link and text-flow alternatives.
- MAX keeps some legacy labels (`⬅️ Меню`, `🔄 Обновить погоду`) because existing platform regression tests and user flows depend on them.
- VK visible positive score labels use `+1/+2`; VK payloads remain numeric for the established score contract.
- MAX visible positive score labels are numeric (`1/2`), while payloads are explicit (`score:1/score:2`) to avoid callback ambiguity.

## Mandatory regression tests

- `tests/test_messenger_button_parity.py`
- `tests/test_messenger_deep_button_parity.py`
- `tests/test_cross_messenger_score_scales.py`
- `tests/test_max_keyboard_parity.py`
- `tests/test_messenger_post_audio_score_scale_chain.py`
- `tests/test_messenger_webhook_split_parity.py`
- `tests/test_messenger_webhook_fixture_parity.py`
- `tests/test_messenger_state_transition_contract.py`

## Live proof checklist

Use `docs/MESSENGER_LIVE_SMOKE_CHECKLIST.md` after every change that touches messenger buttons, payloads, webhook extraction, or reply rendering.
