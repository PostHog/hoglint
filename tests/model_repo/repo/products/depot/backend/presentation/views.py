from products.depot.backend.facade.api import bin_count


def depot_page(depot_id: int) -> str:
    return str(bin_count(depot_id).bins)
