from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from services.db import db, tx
from services.messenger.platforms import MessengerPlatform, normalize_platform
from services.messenger.preferences import get_channel_snapshot
from services.practice_token_contract import package_by_id

_REQUIRED_SCHEMA_TABLES = frozenset({
    "premium_entitlements",
    "premium_delivery_outbox",
    "consultation_requests",
})

VIDEO_ENTITLEMENT = "stress_video_course"
CONSULTATION_ENTITLEMENT = "consultation_60m"
VIDEO_PACKAGES = frozenset({"practice_antistress_60", "practice_personal_month"})
CONSULTATION_PACKAGES = frozenset({"practice_personal_month"})


@dataclass(frozen=True)
class PremiumGrantResult:
    video_granted: bool = False
    consultation_granted: bool = False
    outbox_created: int = 0
    consultation_request_created: bool = False


def ensure_schema(conn: Any) -> None:
    placeholders = ",".join("?" for _ in _REQUIRED_SCHEMA_TABLES)
    rows = conn.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
        tuple(sorted(_REQUIRED_SCHEMA_TABLES)),
    ).fetchall()
    existing = {str(row["name"] if hasattr(row, "keys") else row[0]) for row in rows}
    missing = sorted(_REQUIRED_SCHEMA_TABLES - existing)
    if missing:
        raise RuntimeError(f"premium_entitlements_schema_not_migrated:{','.join(missing)}")


def package_entitlement_types(package_id: str) -> tuple[str, ...]:
    package_id = (package_id or "").strip()
    out: list[str] = []
    if package_id in VIDEO_PACKAGES:
        out.append(VIDEO_ENTITLEMENT)
    if package_id in CONSULTATION_PACKAGES:
        out.append(CONSULTATION_ENTITLEMENT)
    return tuple(out)


def video_course_url() -> str:
    return (
        os.getenv("STRESS_VIDEO_COURSE_URL")
        or os.getenv("VIDEO_COURSE_URL")
        or "https://metrotherapy.ru/antistress-course"
    ).strip()


def consultation_admin_note(*, user_id: int, platform: str, external_user_id: str | None, package_title: str, provider_payment_id: str) -> str:
    return (
        "Новая заявка на консультацию\n\n"
        f"Пакет: {package_title}\n"
        f"user_id: {int(user_id)}\n"
        f"platform: {platform}\n"
        f"external_user_id: {external_user_id or '-'}\n"
        f"provider_payment_id: {provider_payment_id or '-'}\n\n"
        "Нужно связаться с пользователем и назначить консультацию 60 минут."
    )


def video_course_message(*, package_title: str) -> str:
    return (
        "🎓 Доступ к видеокурсу открыт\n\n"
        f"Пакет: {package_title}.\n\n"
        "Видеокурс по снижению стрессовой нагрузки доступен по ссылке:\n"
        f"{video_course_url()}\n\n"
        "Проходите в спокойном темпе. Практики и курс не заменяют врача или психотерапию."
    )


def consultation_user_message(*, package_title: str) -> str:
    return (
        "👤 Заявка на личную консультацию создана\n\n"
        f"Пакет: {package_title}.\n"
        "В пакет входит 1 личная консультация 60 минут для настройки маршрута самоподдержки.\n\n"
        "Администратор свяжется с вами в этом мессенджере."
    )


def delivery_targets(user_id: int, *, fallback_platform: str = MessengerPlatform.TELEGRAM.value) -> list[tuple[str, str | None]]:
    snapshot = get_channel_snapshot(int(user_id))
    targets: list[tuple[str, str | None]] = []
    for identity in snapshot.get("identities", []):
        platform = normalize_platform(str(identity.get("platform") or fallback_platform))
        external_user_id = (identity.get("external_user_id") or "").strip() or None
        if platform == MessengerPlatform.TELEGRAM.value and not external_user_id:
            external_user_id = str(int(user_id))
        item = (platform, external_user_id)
        if item not in targets:
            targets.append(item)
    if not targets:
        targets.append((normalize_platform(fallback_platform), str(int(user_id))))
    return targets


def _insert_entitlement(conn: Any, *, user_id: int, package_id: str, entitlement_type: str, provider: str, provider_payment_id: str, source: str) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO premium_entitlements(
            user_id, package_id, entitlement_type, provider, provider_payment_id, source, status, idempotency_key
        ) VALUES(?,?,?,?,?,?,?,?)
        """.strip(),
        (
            int(user_id), package_id, entitlement_type, provider, provider_payment_id, source,
            "active", f"premium:{provider}:{provider_payment_id}:{entitlement_type}",
        ),
    )
    return int(getattr(cursor, "rowcount", 0) or 0) > 0


def _insert_outbox(conn: Any, *, user_id: int, platform: str, external_user_id: str | None, delivery_kind: str, title: str, body: str, idempotency_key: str) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO premium_delivery_outbox(
            user_id, platform, external_user_id, delivery_kind, title, body, status, idempotency_key
        ) VALUES(?,?,?,?,?,?,?,?)
        """.strip(),
        (int(user_id), normalize_platform(platform), external_user_id, delivery_kind, title, body, "pending", idempotency_key),
    )
    return int(getattr(cursor, "rowcount", 0) or 0) > 0


def _insert_consultation_request(conn: Any, *, user_id: int, platform: str, external_user_id: str | None, package_id: str, provider: str, provider_payment_id: str, note: str) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO consultation_requests(
            user_id, platform, external_user_id, package_id, provider, provider_payment_id, status, contact_payload, idempotency_key
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """.strip(),
        (int(user_id), normalize_platform(platform), external_user_id, package_id, provider, provider_payment_id, "new", note, f"consultation:{provider}:{provider_payment_id}"),
    )
    return int(getattr(cursor, "rowcount", 0) or 0) > 0


def grant_premium_entitlements_for_payment(*, user_id: int, package_id: str, provider: str, provider_payment_id: str, source: str = "webhook", fallback_platform: str = MessengerPlatform.TELEGRAM.value) -> PremiumGrantResult:
    package = package_by_id(package_id)
    entitlement_types = package_entitlement_types(package.package_id)
    if not entitlement_types:
        return PremiumGrantResult()
    targets = delivery_targets(int(user_id), fallback_platform=fallback_platform)
    video_granted = False
    consultation_granted = False
    outbox_created = 0
    consultation_request_created = False
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            if VIDEO_ENTITLEMENT in entitlement_types:
                video_granted = _insert_entitlement(conn, user_id=int(user_id), package_id=package.package_id, entitlement_type=VIDEO_ENTITLEMENT, provider=provider, provider_payment_id=provider_payment_id, source=source)
                for platform, external_user_id in targets:
                    if _insert_outbox(conn, user_id=int(user_id), platform=platform, external_user_id=external_user_id, delivery_kind="video_course_access", title="Доступ к видеокурсу", body=video_course_message(package_title=package.title), idempotency_key=f"premium_delivery:{provider}:{provider_payment_id}:video:{platform}:{external_user_id or ''}"):
                        outbox_created += 1
            if CONSULTATION_ENTITLEMENT in entitlement_types:
                consultation_granted = _insert_entitlement(conn, user_id=int(user_id), package_id=package.package_id, entitlement_type=CONSULTATION_ENTITLEMENT, provider=provider, provider_payment_id=provider_payment_id, source=source)
                primary_platform, primary_external_user_id = targets[0]
                if _insert_outbox(conn, user_id=int(user_id), platform=primary_platform, external_user_id=primary_external_user_id, delivery_kind="consultation_user_notice", title="Заявка на консультацию", body=consultation_user_message(package_title=package.title), idempotency_key=f"premium_delivery:{provider}:{provider_payment_id}:consultation_user:{primary_platform}:{primary_external_user_id or ''}"):
                    outbox_created += 1
                note = consultation_admin_note(user_id=int(user_id), platform=primary_platform, external_user_id=primary_external_user_id, package_title=package.title, provider_payment_id=provider_payment_id)
                consultation_request_created = _insert_consultation_request(conn, user_id=int(user_id), platform=primary_platform, external_user_id=primary_external_user_id, package_id=package.package_id, provider=provider, provider_payment_id=provider_payment_id, note=note)
    return PremiumGrantResult(video_granted=video_granted, consultation_granted=consultation_granted, outbox_created=outbox_created, consultation_request_created=consultation_request_created)


def pending_delivery(limit: int = 20) -> list[dict[str, Any]]:
    with db() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, user_id, platform, external_user_id, delivery_kind, title, body, attempts, last_error FROM premium_delivery_outbox WHERE status='pending' ORDER BY id ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_delivery_sent(delivery_id: int) -> None:
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            conn.execute("UPDATE premium_delivery_outbox SET status='sent', updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(delivery_id),))


def mark_delivery_failed(delivery_id: int, error: str) -> None:
    with db() as conn:
        with tx(conn):
            ensure_schema(conn)
            conn.execute("UPDATE premium_delivery_outbox SET attempts=attempts+1, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(error)[:500], int(delivery_id)))


def consultation_requests_summary(limit: int = 20) -> list[dict[str, Any]]:
    with db() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, user_id, platform, external_user_id, package_id, provider_payment_id, status, contact_payload, created_at FROM consultation_requests ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]
