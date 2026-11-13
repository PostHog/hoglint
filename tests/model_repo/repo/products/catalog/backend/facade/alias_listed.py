from products.catalog.backend.logic import handouts

_Box = handouts.HandoutBox
Kind = handouts.HandoutHolder.Kind
place = handouts.place_entry
made = handouts.make_box()

__all__ = ["_Box", "Kind", "place", "made"]
