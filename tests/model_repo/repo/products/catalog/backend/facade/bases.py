from typing import TYPE_CHECKING, Generic, TypeVar

T = TypeVar("T")


def _error_base() -> type:
    return Exception


def _error_meta() -> type:
    return type


_EntryBase = _error_base()

if TYPE_CHECKING:
    _EntryRoot = KeyError
else:
    _EntryRoot = Exception


class EntryHeld(_error_base()):
    pass


class EntryLocked(Exception, _EntryBase):
    pass


class EntryClosed(_EntryRoot):
    pass


class EntryPageMissing(Exception, Generic[T]):
    pass


class EntryStale(Exception, metaclass=_error_meta()):
    pass
