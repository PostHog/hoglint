from typing import Generic, TypeVar

T = TypeVar("T")


def _error_base() -> type:
    return Exception


def _error_meta() -> type:
    return type


_EntryBase = _error_base()


class EntryHeld(_error_base()):
    pass


class EntryLocked(Exception, _EntryBase):
    pass


class EntryPageMissing(Exception, Generic[T]):
    pass


class EntryStale(Exception, metaclass=_error_meta()):
    pass
