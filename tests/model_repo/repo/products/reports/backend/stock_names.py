# Names loaded on first read (PEP 562), so the inventory logic stays off this module's import.
_LAZY = {"move_stock": "products.inventory.backend.logic.stock"}

# Plain text in a dict of the same module: no lazy entry.
LABELS = {"move_stock": "products.inventory.move_stock"}


def __getattr__(name):
    raise AttributeError(name)
