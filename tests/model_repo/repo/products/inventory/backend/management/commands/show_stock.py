from django.core.management.base import BaseCommand

from products.inventory.backend.facade.api import get_stock


class Command(BaseCommand):
    def handle(self, *args, **options):
        return get_stock(options["team_id"], options["item_id"])
