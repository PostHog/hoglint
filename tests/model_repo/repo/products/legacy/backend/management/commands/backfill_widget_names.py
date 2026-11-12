from django.core.management.base import BaseCommand

from products.legacy.backend.logic.widgets import widget_total


class Command(BaseCommand):
    def handle(self, *args, **options):
        return widget_total(options["team_id"])
