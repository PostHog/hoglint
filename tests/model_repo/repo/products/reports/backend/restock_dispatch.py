from celery import current_app

# Celery runs the task the name says: the name is a path into inventory's tasks.
RESTOCK_TASK = "products.inventory.backend.tasks.restock.restock"


def dispatch_restock(team_id: int, item_id: int) -> None:
    current_app.send_task(RESTOCK_TASK, args=(team_id, item_id))
