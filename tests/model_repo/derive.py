"""The preset in the form a repo takes when test selection reads `hoglint watched`: the Isolated
level claimed by `isolated: true` in a product's `product.yaml`, its structural zones named once,
and the two coverage rules with no inputs, so they record the paths they need and judge none. It
adds the covers the derived set needs: the modules that define the items of a list a registration
point registers, the imports a hook callback or a function `ready()` defines makes, and an unknown
for a hook call whose callables cannot be told.

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
LAST_WIRING_COVER = (
    "  { cover = \"edge.kind == 'import' and edge.autodiscovered == 'admin' and dst.package == src.package "
    'and \'type_only\' not in edge.context: surface", why = "the admin module of the product imports this module" },\n'
)
DERIVED_COVERS = (
    "  { cover = \"edge.kind in ['import', 'reference', 'dotted-path'] and 'type_only' not in edge.context and "
    "src.wiring == 'registration' and crosses-package and dst.tier == 'product': items\", "
    'why = "core registers an item of a list, and this file defines the item" },\n'
    "  { cover = \"edge.kind == 'import' and (edge.hook_callback != none or edge.app_setup == 'callback') and "
    "'test' not in edge.context and 'type_only' not in edge.context and dst.package == src.package: chain\", "
    'why = "a hook callback, or a function that ready() defines, imports this module" },\n'
    "  { cover = \"edge.kind == 'installs' and 'test' not in edge.context and crosses-package and edge.hook == none "
    'and edge.callback != none: src", why = "this file hands a hook a callback" },\n'
)


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"the preset holds {text.count(old)} copies of {old!r}, not one")
    return text.replace(old, new)


def derive_preset(text: str) -> str:
    text = replace_once(text, TURBO_CLAIM, YAML_CLAIM)
    if text.count(TURBO_INPUTS) != 2:
        raise ValueError(f"the preset holds {text.count(TURBO_INPUTS)} turbo inputs, not two")
    text = text.replace(TURBO_INPUTS, "")
    return replace_once(text, LAST_WIRING_COVER, LAST_WIRING_COVER + DERIVED_COVERS)
