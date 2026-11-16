from unittest.mock import patch


def test_shelf_life_is_patched():
    with patch("products.inventory.backend.presentation.views.shelf_life_days", return_value=1):
        return None
