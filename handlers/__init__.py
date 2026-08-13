"""Telegram handler composition boundary.

Install Telegram-specific payment adapters here, before handler submodules bind
functions from the payment domain. Keeping this wiring out of
``services.payments.__init__`` prevents unrelated provider workers (for example
YooKassa refund drills) from bootstrapping Telegram/DB side effects on import.
"""

from services.payments.stars_invoice_transport import install_stars_invoice_link_transport


install_stars_invoice_link_transport()
