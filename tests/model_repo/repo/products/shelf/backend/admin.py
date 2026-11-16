from products.shelf.backend.logic.count import shelf_count


def count_column(team_id: int) -> int:
    return shelf_count(team_id)
