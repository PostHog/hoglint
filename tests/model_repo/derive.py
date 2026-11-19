"""The preset in the form a repo takes when test selection reads `hoglint watched`: the Isolated
level claimed by `isolated: true` in a product's `product.yaml`, its structural zones named once,
and the two coverage rules with no inputs, so they record the paths they need and judge none. It
adds no cover: the covers the derived set needs (the modules that define the items of a list a
registration point registers, and of what a declaration a registry loads registers, the imports a
hook callback or a function `ready()` defines makes, and a hook call's callables) are the
shipped preset's own, and `test_derive.py` fails when the derived form adds one the preset lacks.

The model repository's watched cases and the real-size run lay this form over the preset with
`derive_preset`. Each edit names the exact text it replaces, so a preset that moved fails loudly.
"""

from __future__ import annotations

TURBO_CLAIM = 'claimed_by   = { adapter = "turbo", task = "backend:contract-check" }'
YAML_CLAIM = (
    'claimed_by   = { adapter = "yaml-key", file = "product.yaml", key = "isolated" }\n'
    'watch_zones  = ["facade", "presentation", "migrations"]'
)
TURBO_INPUTS = 'inputs     = { adapter = "turbo", task = "backend:contract-check" }\n'


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"the preset holds {text.count(old)} copies of {old!r}, not one")
    return text.replace(old, new)


def derive_preset(text: str) -> str:
    text = replace_once(text, TURBO_CLAIM, YAML_CLAIM)
    if text.count(TURBO_INPUTS) != 2:
        raise ValueError(f"the preset holds {text.count(TURBO_INPUTS)} turbo inputs, not two")
    return text.replace(TURBO_INPUTS, "")
