from django.core.management.base import BaseCommand

from products.legacy.backend.models import Widget


class Command(BaseCommand):
    def handle(self, *args, **options):
        Widget.objects.create(name="demo")
