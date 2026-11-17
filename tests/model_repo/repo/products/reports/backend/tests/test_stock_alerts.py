from unittest.mock import patch

from products.reports.backend.logic.stock_alerts import alert_low_stock


def test_alert_low_stock_sends_one_alert():
    with patch("products.reports.backend.logic.stock_alerts.send_restock_alert") as send:
        alert_low_stock(1)
    send.assert_called_once_with(1)
