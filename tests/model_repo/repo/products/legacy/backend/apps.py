from django.apps import AppConfig

from posthog.api.file_system.deletion import register_post_delete_hook
from posthog.models.resource_transfer.registered_models import register_model


class LegacyConfig(AppConfig):
    name = "products.legacy.backend"
    label = "legacy"

    def ready(self):
        from products.legacy.backend.hooks import on_widget_deleted
        from products.legacy.backend.models import Widget

        register_model("widget", Widget)
        register_post_delete_hook(on_widget_deleted)
