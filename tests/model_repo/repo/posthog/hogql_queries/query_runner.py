def get_query_runner(query, team):
    kind = query["kind"]
    if kind == "WidgetQuery":
        from products.reports.backend.hogql_queries.widget import WidgetQueryRunner

        return WidgetQueryRunner(query, team)
    if kind == "EntryQuery":
        from products.catalog.backend.hogql_queries.entries import EntryQueryRunner

        return EntryQueryRunner(query, team)
    raise ValueError(kind)


def process_query_dict(team, query):
    return get_query_runner(query, team).run()
