from collections.abc import Callable

RESTOCK_HOOKS: list[Callable[[int], None]] = []


def register_restock_hook(hook: Callable[[int], None]) -> None:
    RESTOCK_HOOKS.append(hook)
