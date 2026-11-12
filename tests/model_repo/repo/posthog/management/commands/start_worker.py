from django.core.management.base import BaseCommand

from products.reports.backend.temporal.workflows import BuildReportWorkflow


class Command(BaseCommand):
    def handle(self, *args, **options):
        return [BuildReportWorkflow]
