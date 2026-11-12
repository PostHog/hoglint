from products.catalog.backend.tasks.reindex import reindex_catalog
from products.inventory.backend.tasks.restock import restock
from products.reports.backend.tasks.nightly import nightly_reports


def setup_periodic_tasks(sender):
    sender.add(restock)
    sender.add(reindex_catalog)
    sender.add(nightly_reports)
