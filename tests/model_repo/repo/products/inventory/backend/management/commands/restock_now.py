from django.core.management.base import BaseCommand

# The facade's tasks module hands out `restock`, defined in the product's tasks.
from products.inventory.backend.facade.tasks import restock


class Command(BaseCommand):
    def handle(self, *args, **options):
        return restock(options["team_id"], options["item_id"])
