from products.depot.backend.logic.cold import label_note
from products.depot.backend.logic.wording import describe_restock
from products.inventory.backend.facade.hooks import register_restock_hook

register_restock_hook(describe_restock)


def note_for(item_id):
    return label_note(item_id)
