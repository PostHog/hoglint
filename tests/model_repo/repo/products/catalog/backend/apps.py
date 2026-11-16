from django.apps import AppConfig

from products.catalog.backend.events import ENTRY_CREATED


class CatalogConfig(AppConfig):
    name = "products.catalog.backend"
    label = "catalog"
    events = (ENTRY_CREATED,)
