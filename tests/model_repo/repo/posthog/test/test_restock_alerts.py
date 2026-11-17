from unittest.mock import patch


def test_restock_alert_is_patched():
    with patch("products.inventory.backend.facade.api.send_restock_alert"):
        return None
