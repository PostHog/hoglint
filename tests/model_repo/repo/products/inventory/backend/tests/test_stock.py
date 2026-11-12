from products.inventory.backend.logic.stock import move_stock
from products.inventory.backend.models import StockItem


class TestStock:
    def test_move(self):
        item = StockItem.objects.create(team_id=1, name="bolt", quantity=1)
        move_stock(1, item.pk, 2)
