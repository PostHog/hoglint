_TITLES = {"start_titled": "Start a widget job"}

__all__ = ["start_titled"]


def __getattr__(name):
    raise AttributeError(name)


def start_titled(name: str) -> str:
    return _TITLES["start_titled"] + name
