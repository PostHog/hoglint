def shelf_life_days(item_id: int) -> int:
    return 30 + item_id % 3
