from django.apps import AppConfig

from products.inventory.backend.facade.hooks import register_restock_hook


class InventoryConfig(AppConfig):
    name = "products.inventory.backend"
    label = "inventory"

    def ready(self):
        from products.inventory.backend.logic.stock import log_restock

        register_restock_hook(log_restock)
