from django.core.management.base import BaseCommand

from products.depot.backend.facade.temporal import ACTIVITIES as DEPOT_ACTIVITIES, WORKFLOWS as DEPOT_WORKFLOWS
from products.inventory.backend.facade.temporal import stock_cleanup, stock_schedule, warm_stock
from products.racks.backend.facade.temporal import WORKFLOWS as RACK_WORKFLOWS
from products.reports.backend.temporal.workflows import BuildReportWorkflow

STOCK_SCHEDULES = [("stock", [stock_schedule])]
QUEUES = [("depot", DEPOT_WORKFLOWS, DEPOT_ACTIVITIES), ("racks", RACK_WORKFLOWS, [])]


class Command(BaseCommand):
    def handle(self, *args, **options):
        warm_stock()
        return [BuildReportWorkflow]
