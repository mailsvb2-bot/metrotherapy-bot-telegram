"""Платёжные сценарии (подписка/подарки).

Handlers должны быть тонкими: только маршрутизация, вся логика здесь.
"""

from services.payments.stars_invoice_transport import install_stars_invoice_link_transport


install_stars_invoice_link_transport()
