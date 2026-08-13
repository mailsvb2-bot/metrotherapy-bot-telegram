"""Payment-domain package.

Importing a payment leaf module must be side-effect free. In particular, a
YooKassa worker/probe must not bootstrap Telegram Stars handlers, database gift
flows, or mutate another module's public functions merely because Python first
loads ``services.payments``.

Telegram adapter wiring is installed explicitly at the handler composition
boundary (``handlers``), before payment handlers bind their imports.
"""

from __future__ import annotations
