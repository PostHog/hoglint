from django.core.management.base import BaseCommand

from products.legacy.backend.tasks.cleanup import cleanup


class Command(BaseCommand):
    def handle(self, *args, **options):
        cleanup(options["team_id"])
