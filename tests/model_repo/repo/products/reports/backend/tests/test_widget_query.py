from posthog.hogql_queries.query_runner import process_query_dict


class TestOwnQuery:
    def test_runs(self):
        assert process_query_dict(None, {"kind": "WidgetQuery"}) == []
