from contextlib import contextmanager


@contextmanager
def unscoped():
    yield


@contextmanager
def team_scope(team_id: int):
    yield team_id
