# The facade's contracts module imports StockState from the facade's enums module and keeps it to
# itself; StockLevelNote, defined in inventory's logic, is kept there too.
from products.inventory.backend.facade.contracts import StockLevelNote, StockState


def is_empty(state: StockState, note: StockLevelNote) -> bool:
    return state == StockState.EMPTY and note is not None
