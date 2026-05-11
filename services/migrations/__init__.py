from __future__ import annotations

import sqlite3
from services.db import schema as db_schema
from services.schema_core import ensure_prod_tables
from services.migrations.price_rub_migration_v1 import apply as _apply_price
from services.migrations.scheduled_jobs_to_jobs_v1 import apply as _apply_sched
from services.migrations.events_decision_tracking_v1 import apply as _apply_events
from services.migrations.payments_decision_attribution_v1 import apply as _apply_pay_decision
from services.migrations.user_channel_routing_v1 import apply as _apply_channel_routing
from services.migrations.user_channel_bridge_and_audio_progress_v1 import apply as _apply_channel_bridge_audio
from services.migrations.user_audio_access_tokens_v1 import apply as _apply_audio_access_tokens
from services.migrations.user_audio_progress_state_v2 import apply as _apply_audio_progress_state_v2
from services.migrations.user_messenger_runtime_v3 import apply as _apply_messenger_runtime_v3
from services.migrations.user_audio_timeline_v4 import apply as _apply_audio_timeline_v4
from services.migrations.messenger_media_assets_v5 import apply as _apply_messenger_media_assets_v5
from services.migrations.messenger_media_assets_v6 import apply as _apply_messenger_media_assets_v6
from services.migrations.user_delivery_preferences_v6 import apply as _apply_delivery_preferences_v6
from services.migrations.admin_ad_links_v1 import apply as _apply_admin_ad_links_v1


def apply_all_migrations(conn: sqlite3.Connection) -> None:
    """Apply repo migrations safely even on a fresh/empty SQLite file.

    Tests and ad-hoc scripts sometimes call migrations directly without going through
    services.schema.init_db(). In that case base tables may not exist yet.
    We ensure the canonical base schema first, then run one-time migrations.
    """
    db_schema.create_or_update_tables(conn)
    ensure_prod_tables(conn)
    # Keep order deterministic.
    _apply_sched(conn)
    _apply_events(conn)
    _apply_pay_decision(conn)
    _apply_channel_routing(conn)
    _apply_channel_bridge_audio(conn)
    _apply_audio_access_tokens(conn)
    _apply_audio_progress_state_v2(conn)
    _apply_messenger_runtime_v3(conn)
    _apply_audio_timeline_v4(conn)
    _apply_messenger_media_assets_v5(conn)
    _apply_messenger_media_assets_v6(conn)
    _apply_delivery_preferences_v6(conn)
    _apply_admin_ad_links_v1(conn)
    _apply_price(conn)

