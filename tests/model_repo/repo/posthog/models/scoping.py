from contextlib import contextmanager


@contextmanager
def unscoped():
    yield
