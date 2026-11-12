from products.inventory.backend.facade import api as inventory


def shared_stock(team_id, item_id):
    return inventory.get_stock(team_id, item_id)
