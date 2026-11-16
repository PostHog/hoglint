from django.apps import AppConfig

from products.inventory.backend.facade.hooks import register_restock_hook


def _reorder_when_restocked(item_id):
    from products.inventory.backend.logic.reorder import reorder

    reorder(item_id)


class InventoryConfig(AppConfig):
    name = "products.inventory.backend"
    label = "inventory"

    def ready(self):
        from products.inventory.backend.logic.stock import log_restock

        def audit_restock(item_id):
            from products.inventory.backend.logic.audit import record_restock

            record_restock(item_id)

        register_restock_hook(log_restock)
        register_restock_hook(audit_restock)
        register_restock_hook(_reorder_when_restocked)
