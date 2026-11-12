from django.apps import AppConfig

from products.inventory.backend.facade.hooks import register_restock_hook


class ReportsConfig(AppConfig):
    name = "products.reports.backend"
    label = "reports"

    def ready(self):
        from products.reports.backend.listeners import on_restock

        register_restock_hook(on_restock)
