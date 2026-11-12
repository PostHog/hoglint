from products.batch_exports.backend.sql import EXPORT_TABLE_SQL


class TestSchema:
    def test_export_table(self):
        assert "batch_export_runs" in EXPORT_TABLE_SQL
