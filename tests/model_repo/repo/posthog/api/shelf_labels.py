from products.shelf.backend.logic.labels import label_for


def shelf_header(shelf_id: int) -> str:
    return label_for(shelf_id).upper()
