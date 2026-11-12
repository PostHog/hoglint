from products.catalog.backend.facade.models import CatalogEntry
from products.catalog.backend.logic.pricing import price_for
from products.batch_exports.backend.sql import EXPORT_TABLE_SQL
from products.catalog.backend.sql import ENTRY_TABLE_SQL
from products.inventory.backend.facade.api import get_stock
from products.reports.backend.models import ReportRun


def run_count(team_id: int) -> int:
    return ReportRun.objects.filter(team_id=team_id).count()


def build(team_id: int) -> int:
    entries = CatalogEntry.objects.filter(team_id=team_id)
    return len(entries) + price_for(1) + get_stock(team_id, 1).quantity + len(ENTRY_TABLE_SQL) + len(EXPORT_TABLE_SQL)
