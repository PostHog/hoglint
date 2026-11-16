from django.core.management.base import BaseCommand

from products.inventory.backend.facade.temporal import stock_cleanup, stock_schedule, warm_stock
from products.reports.backend.temporal.workflows import BuildReportWorkflow

STOCK_SCHEDULES = [("stock", [stock_schedule])]


class Command(BaseCommand):
    def handle(self, *args, **options):
        warm_stock()
        return [BuildReportWorkflow]
