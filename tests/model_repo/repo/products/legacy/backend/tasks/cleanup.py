from products.legacy.backend.tests.factories import make_widget


def cleanup(team_id: int) -> None:
    make_widget(team_id).delete()
