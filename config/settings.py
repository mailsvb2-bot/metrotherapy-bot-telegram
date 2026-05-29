from dataclasses import dataclass
import os

# ВАЖНО (prod-safe): НЕ подхватываем .env автоматически в продакшене.
# Иначе локальный .env рядом с кодом может неожиданно переопределить системные переменные окружения.
#
# По умолчанию .env читаем только в dev/stage.
# Для принудительного поведения можно выставить LOAD_DOTENV=1.
APP_ENV = (os.getenv("APP_ENV", "dev") or "dev").strip().lower()
if os.getenv("LOAD_DOTENV", "") == "1" or APP_ENV in {"dev", "stage"}:
    try:
        from dotenv import load_dotenv

        # override=False: не перетираем уже заданные переменные окружения
        load_dotenv(override=False)
    except ImportError:
        # dotenv не обязателен (особенно в минимальных прод-сборках)
        pass

# --- Админ-доступ к аналитике ---
# ADMIN_IDS можно задать так: ADMIN_IDS="123,456" или ADMIN_ID="123"
def _parse_admin_ids_env() -> list[int]:
    raw = os.getenv("ADMIN_IDS", "").strip()
    if raw:
        out: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(int(part))
            except ValueError:
                continue
        return out
    one = os.getenv("ADMIN_ID", "").strip()
    if one:
        try:
            return [int(one)]
        except ValueError:
            return []
    return []

# ВАЖНО: это module-level переменная для удобного импорта в keyboards/handlers.
ADMIN_IDS: list[int] = _parse_admin_ids_env()



def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


def _env_bool(name: str, default: str = "0") -> bool:
    """Parse boolean environment variables consistently across runtime settings.

    Accepts the common deployment values used in systemd/GitHub Actions/manual
    shells: 1/true/yes/on/webhook. This prevents a dangerous split where one
    runtime resolver treats "true" as enabled while Settings treats it as false.
    """
    raw = _env(name, default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "webhook"}


@dataclass
class Settings:
    BOT_TOKEN: str = _env("BOT_TOKEN", "")
    PAY_PROVIDER_TOKEN: str = _env("PAY_PROVIDER_TOKEN", "")

    # --- Multi-messenger routing ---
    TELEGRAM_BOT_USERNAME: str = _env("TELEGRAM_BOT_USERNAME", "")
    MAX_BOT_TOKEN: str = _env("MAX_BOT_TOKEN", "")
    MAX_WEBHOOK_SECRET: str = _env("MAX_WEBHOOK_SECRET", "")
    MAX_BOT_NAME: str = _env("MAX_BOT_NAME", "")
    MAX_BOT_LINK_BASE: str = _env("MAX_BOT_LINK_BASE", "")
    VK_GROUP_TOKEN: str = _env("VK_GROUP_TOKEN", "")
    VK_CONFIRMATION_TOKEN: str = _env("VK_CONFIRMATION_TOKEN", "")
    VK_SECRET: str = _env("VK_SECRET", "")
    VK_GROUP_ID: str = _env("VK_GROUP_ID", "")
    VK_API_VERSION: str = _env("VK_API_VERSION", "5.199")
    MESSENGER_WEBHOOK_ENABLED: bool = _env_bool("MESSENGER_WEBHOOK_ENABLED")
    MESSENGER_WEBHOOK_HOST: str = _env("MESSENGER_WEBHOOK_HOST", _env("WEBHOOK_HOST", "0.0.0.0"))
    MESSENGER_WEBHOOK_PORT: int = int(_env("MESSENGER_WEBHOOK_PORT", _env("WEBHOOK_PORT", "8081")))
    TELEGRAM_TRANSPORT: str = (_env("TELEGRAM_TRANSPORT", _env("RUN_MODE", "polling")) or "polling").strip().lower()
    TELEGRAM_WEBHOOK_ENABLED: bool = _env_bool("TELEGRAM_WEBHOOK_ENABLED")
    TELEGRAM_WEBHOOK_HOST: str = _env("TELEGRAM_WEBHOOK_HOST", _env("WEBHOOK_HOST", "127.0.0.1"))
    TELEGRAM_WEBHOOK_PORT: int = int(_env("TELEGRAM_WEBHOOK_PORT", _env("WEBHOOK_PORT", "8081")))
    TELEGRAM_WEBHOOK_PREFIX: str = _env("TELEGRAM_WEBHOOK_PREFIX", "/telegram-webhook")
    TELEGRAM_WEBHOOK_SECRET_TOKEN: str = _env("TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
    TELEGRAM_WEBHOOK_PUBLIC_BASE_URL: str = _env("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", _env("PUBLIC_BASE_URL", ""))
    TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES: bool = _env_bool("TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES")
    HEALTHCHECK_ENABLED: bool = _env_bool("HEALTHCHECK_ENABLED", "1")
    HEALTHCHECK_HOST: str = _env("HEALTHCHECK_HOST", "127.0.0.1")
    HEALTHCHECK_PORT: int = int(_env("HEALTHCHECK_PORT", "8082"))
    MESSENGER_PUBLIC_BASE_URL: str = _env("MESSENGER_PUBLIC_BASE_URL", "")
    MESSENGER_BRIDGE_TOKEN_TTL_HOURS: int = int(_env("MESSENGER_BRIDGE_TOKEN_TTL_HOURS", "72"))

    # --- YooKassa (Telegram Payments) ---
    # Во многих подключениях YooKassa требуется передавать чек (receipt) для фискализации.
    # Telegram позволяет прокинуть данные в провайдера через provider_data.
    # Параметры ниже можно переопределить в .env.
    # tax_system_code: 1..6 (система налогообложения в чеке)
    YOOKASSA_TAX_SYSTEM_CODE: int = int(_env("YOOKASSA_TAX_SYSTEM_CODE", "2"))
    # vat_code: 1..6 (ставка НДС для позиции; 1 обычно означает "без НДС")
    YOOKASSA_VAT_CODE: int = int(_env("YOOKASSA_VAT_CODE", "1"))
    # payment_subject/payment_mode для позиции чека (значения по умолчанию подходят для цифровой услуги)
    YOOKASSA_PAYMENT_SUBJECT: str = _env("YOOKASSA_PAYMENT_SUBJECT", "service")
    YOOKASSA_PAYMENT_MODE: str = _env("YOOKASSA_PAYMENT_MODE", "full_payment")

    # IANA timezone string (Москва)
    TIMEZONE: str = _env("TIMEZONE", "Europe/Moscow")

    # Daily auto-audio schedule (local time in TIMEZONE)
    # Format: HH:MM or HH:MM:SS
    MORNING_TIME: str = _env("MORNING_TIME", "08:30")
    EVENING_TIME: str = _env("EVENING_TIME", "19:30")

    # Default duration (seconds) for full tracks (used for post-rating fallback timer)
    # Override in .env: TRACK_DURATION_SEC=1680  (28 min)
    TRACK_DURATION_SEC: int = int(_env("TRACK_DURATION_SEC", "1680"))




    AUDIO_DIR: str = _env("AUDIO_DIR", "audio/full")
    DEMO_DIR: str = _env("DEMO_DIR", "audio/demo")

    # Источник истины для тарифов: БД (таблица plans). Файл тарифов не используется.

    # 0 = unlimited
    DEMO_LIMIT: int = int(_env("DEMO_LIMIT", "0"))

    # Funnel timings
    DEMO_REMINDER_MINUTES: int = int(_env("DEMO_REMINDER_MINUTES", "5"))
    FUNNEL_AFTER_DEMO_MINUTES: int = int(_env("FUNNEL_AFTER_DEMO_MINUTES", "30"))
    # Мягкое касание после demo_ack, если человек ещё не открыл тарифы
    FUNNEL_POSTDEMO_MINUTES: int = int(_env("FUNNEL_POSTDEMO_MINUTES", "10"))
    FUNNEL_DEADLINE_HOURS: int = int(_env("FUNNEL_DEADLINE_HOURS", "24"))
    FUNNEL_LASTCALL_HOURS: int = int(_env("FUNNEL_LASTCALL_HOURS", "48"))

    # Referral bonuses
    REF_BONUS_WEEK_DAYS: int = int(_env("REF_BONUS_WEEK_DAYS", "3"))
    REF_BONUS_MONTH_DAYS: int = int(_env("REF_BONUS_MONTH_DAYS", "7"))

    # Referral PRO anti-abuse
    REF_MAX_BONUSES: int = int(_env("REF_MAX_BONUSES", "10"))

    ADMIN_IDS: str = _env("ADMIN_IDS", "")
    PREWARM_ENABLED: bool = _env_bool("PREWARM_ENABLED")
    PREWARM_CHAT_ID: str = _env("PREWARM_CHAT_ID", "")

    # --- AI (OpenAI) ---
    # AI включён, если задан ключ. UX не меняем: AI помогает выбирать сценарии/тексты/цены.
    OPENAI_API_KEY: str = _env("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = _env("OPENAI_MODEL", "gpt-4.1-mini")
    OPENAI_BASE_URL: str = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")

    # Для безопасного запуска: можно жёстко отключить AI, даже если ключ задан.
    AI_ENABLED: int = int(_env("AI_ENABLED", "1"))


    @property
    def admin_id_list(self) -> list[int]:
        """Список ID админов/суперадмина.

        Исторически в проекте использовались переменные ADMIN_ID и ADMIN_IDS.
        Важно: поддерживаем оба варианта, иначе супер-админ может потеряться
        (например, если задан только ADMIN_ID).
        """
        # 1) Разбираем ADMIN_IDS/ADMIN_ID напрямую из окружения.
        parsed = _parse_admin_ids_env()
        if parsed:
            return parsed

        # 2) Фолбэк на строковое поле ADMIN_IDS из объекта Settings.
        raw = (self.ADMIN_IDS or "").strip()
        if not raw:
            return []
        out: list[int] = []
        for p in raw.replace(";", ",").split(","):
            p = p.strip()
            if not p:
                continue
            try:
                out.append(int(p))
            except ValueError:
                continue
        return out


settings = Settings()


def _fail_fast_prod_config() -> None:
    """Fail fast in prod if critical env vars are missing or inconsistent.

    In production we do not allow quiet defaults for secrets, webhook ingress,
    or partially configured payments. This catches server mixups before the bot
    starts accepting users from ads.
    """
    if APP_ENV not in {"prod", "production"}:
        return

    missing: list[str] = []
    if not (settings.BOT_TOKEN or '').strip():
        missing.append('BOT_TOKEN')
    if not (settings.PAY_PROVIDER_TOKEN or '').strip():
        missing.append('PAY_PROVIDER_TOKEN')
    if not (os.getenv('ADMIN_IDS') or os.getenv('ADMIN_ID') or '').strip():
        missing.append('ADMIN_IDS')

    telegram_webhook = (settings.TELEGRAM_TRANSPORT == 'webhook') or bool(settings.TELEGRAM_WEBHOOK_ENABLED)
    if telegram_webhook:
        if not (settings.TELEGRAM_WEBHOOK_PUBLIC_BASE_URL or '').strip():
            missing.append('TELEGRAM_WEBHOOK_PUBLIC_BASE_URL')
        if not (settings.TELEGRAM_WEBHOOK_SECRET_TOKEN or '').strip():
            missing.append('TELEGRAM_WEBHOOK_SECRET_TOKEN')
        if not (settings.TELEGRAM_WEBHOOK_PUBLIC_BASE_URL or '').strip().startswith('https://'):
            raise SystemExit('TELEGRAM_WEBHOOK_PUBLIC_BASE_URL must start with https:// in prod webhook mode')
        if not (settings.TELEGRAM_WEBHOOK_PREFIX or '').strip().startswith('/'):
            raise SystemExit('TELEGRAM_WEBHOOK_PREFIX must start with / in prod webhook mode')

    messenger_webhook = bool(settings.MESSENGER_WEBHOOK_ENABLED)
    if messenger_webhook:
        if not (settings.MESSENGER_PUBLIC_BASE_URL or '').strip():
            missing.append('MESSENGER_PUBLIC_BASE_URL')
        if (settings.VK_GROUP_TOKEN or settings.VK_GROUP_ID) and not (settings.VK_SECRET or '').strip():
            missing.append('VK_SECRET')
        if settings.MAX_BOT_TOKEN and not (settings.MAX_WEBHOOK_SECRET or '').strip():
            missing.append('MAX_WEBHOOK_SECRET')

    if not bool(settings.HEALTHCHECK_ENABLED):
        raise SystemExit('HEALTHCHECK_ENABLED must be 1 in prod')

    if missing:
        raise SystemExit(
            "Missing required environment variables for prod: "
            + ", ".join(sorted(set(missing)))
            + "\nSet them as system environment variables (recommended). .env is loaded only in dev/stage (or LOAD_DOTENV=1)."
        )


_fail_fast_prod_config()
