from products.depot.backend.facade.contracts import BinCount


def bin_count(depot_id: int) -> BinCount:
    return BinCount(depot_id=depot_id, bins=0)
