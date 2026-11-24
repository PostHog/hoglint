EXPORT_TABLE_SQL = "CREATE TABLE batch_export_runs (id Int64)"

# CY04: a third product closes a longer cycle: batch_exports -> catalog here, catalog -> reports, reports -> batch_exports.
from products.catalog.backend.facade.contracts import EntryInfo  # noqa: E402

EXPORTED_ROW = EntryInfo
