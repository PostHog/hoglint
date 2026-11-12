from unittest.mock import patch

from products.inventory.backend.models import StockItem
from products.legacy.backend.tests.factories import make_widget


class TestWidgets:
    def test_make(self):
        StockItem.objects.create(team_id=1, name="bolt", quantity=1)
        with patch("products.inventory.backend.logic.stock.log_restock"):
            assert make_widget(1)
