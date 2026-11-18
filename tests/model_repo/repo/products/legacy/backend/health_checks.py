import importlib

_LAZY = {"widget_probe": "probes"}


def widgets_healthy() -> bool:
    return True


def __getattr__(name: str):
    return getattr(importlib.import_module("products.legacy.backend." + _LAZY[name]), name)
