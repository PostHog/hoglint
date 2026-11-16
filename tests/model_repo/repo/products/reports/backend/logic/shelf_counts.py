from products.shelf.backend.logic.count import shelf_count


def shelves_in_report(team_id: int) -> int:
    return shelf_count(team_id)
