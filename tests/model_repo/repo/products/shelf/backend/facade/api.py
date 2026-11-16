from products.shelf.backend.facade.contracts import ShelfLabel
from products.shelf.backend.logic import labels


def shelf_label(shelf_id: int) -> ShelfLabel:
    return ShelfLabel(shelf_id=shelf_id, text=labels.label_for(shelf_id))
