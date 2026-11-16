from products.inventory.backend.facade.api import reorder_point
from products.inventory.backend.facade.testing import stock_item_factory


def test_reorder_point():
    assert reorder_point(stock_item_factory(9)) == 0
