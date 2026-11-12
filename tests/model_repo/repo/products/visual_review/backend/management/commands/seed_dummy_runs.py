from django.core.management.base import BaseCommand

from products.visual_review.backend.models import record_run


class Command(BaseCommand):
    def handle(self, *args, **options):
        record_run("dummy")
