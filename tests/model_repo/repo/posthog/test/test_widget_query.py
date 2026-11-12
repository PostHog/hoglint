from posthog.hogql_queries.query_runner import process_query_dict


class TestWidgetQuery:
    def test_runs(self):
        assert process_query_dict(None, {"kind": "WidgetQuery"})

    def test_entries(self):
        assert process_query_dict(None, {"kind": "EntryQuery"})

    def test_audit(self):
        assert process_query_dict(None, {"kind": "AuditQuery"})
