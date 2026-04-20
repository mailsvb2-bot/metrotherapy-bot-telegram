# Метротерапия (Aiogram 3) — канон v16

Проект бота на **Aiogram 3**. Этот архив зафиксирован как **канон v16**.

## Структура
- `main.py` — запуск
- `app.py` — сборка приложения (DP, роутеры, scheduler, init_db)
- `handlers/` — обработчики команд/колбэков
- `services/` — БД, схемы, логика доступа, scheduler, funnel
- `keyboards/` — клавиатуры
- `audio/` — контент
  - `audio/demo/` — демо-файлы (work/home)
  - `audio/full/` — полный доступ (если используется)
- `data/` — тексты/тарифы

## Установка
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Настройка
Скопируй `.env.example` в `.env` и заполни значения.

## Запуск
```bat
python main.py
```

## Smoke-check
```bat
python -m compileall .
python -c "from services.schema import init_db; init_db(); print('DB OK')"
```

## Продакшн-ядро (что уже закрыто)

1) ✅ БД и конкурентность
   - WAL + timeout + сериализация доступа в `services/db.py`.

2) ✅ Единая проверка доступа/подписки
   - `services/access.py` — стабильный API для handlers.
   - `services/subscription.py` остаётся совместимым и не ломается.

3) ✅ Детерминированный scheduler (idempotent jobs)
   - `core/engine.py` атомарно забирает due-jobs через `services/jobs.claim_due_jobs()`.
   - при сетевых ошибках job возвращается в очередь с задержкой.

4) ✅ Воронка / аналитика / деньги
   - заготовки находятся в `services/funnel*.py`, `services/analytics.py`, `services/events.py`, `handlers/admin_*.py`.


## Импорты
- Внутри пакета `services/` используются только абсолютные импорты: `from services.xxx import ...`.
- Снаружи можно импортировать из единого API: `from services import db, init_db, store, has_access`.


## Проверка импортов
- Windows: `scripts\check_imports.bat`
- Linux/Mac: `bash scripts/check_imports.sh`

## Окружение и режимы валидации
Валидация контента/схемы запускается на старте приложения. Жёсткость управляется через `.env`.

### Переменные окружения
```env
APP_ENV=prod|dev
VALIDATOR_STRICT=0|1
VALIDATOR_RELEASE_MODE=0|1
```

### Как это работает
- **APP_ENV**
  - `prod` → строгая валидация по умолчанию
  - `dev` → мягкая валидация по умолчанию
- **VALIDATOR_STRICT** (переопределяет APP_ENV)
  - `1` → строгий режим (гейтит запуск: при проблемах будет `ValidationError`)
  - `0` → soft-режим (пишет warning в логи, но не валит запуск)
- **VALIDATOR_RELEASE_MODE**
  - `1` → включает проверку «чистоты релиза» (нет `__pycache__/` и `*.pyc`)
  - `0` → проверка выключена

### Рекомендованные пресеты
**Прод:**
```env
APP_ENV=prod
VALIDATOR_RELEASE_MODE=1
```

**Dev:**
```env
APP_ENV=dev
VALIDATOR_STRICT=0
```

## CI-smoke (2–3 команды)
Минимальный smoke-набор для CI (быстро ловит поломки импортов/контрактов):

```bash
python -m compileall .
python scripts/validate_project.py
python -c "from services.validator import validate_all; validate_all(strict=False); print('OK')"
```
