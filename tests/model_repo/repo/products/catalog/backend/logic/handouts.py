from products.catalog.backend.models import CatalogEntry


class HandoutRejected(Exception):
    def entry(self) -> CatalogEntry:
        raise ValueError


class HandoutBox:
    pass


class HandoutHolder:
    class Kind:
        pass


def place_entry(entry: CatalogEntry) -> None:
    pass


def make_box() -> HandoutBox:
    return HandoutBox()


__all__ = ["HandoutRejected", "HandoutBox", "HandoutHolder", "place_entry", "make_box"]
