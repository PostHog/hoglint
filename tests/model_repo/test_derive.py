"""The derived preset (`derive.py`) adds no cover: every cover it holds is the shipped preset's
own, so what the model repository and the real-size run read is what the preset ships."""

from __future__ import annotations

import tomllib
from typing import Any

from model_repo.derive import derive_preset
from model_repo.harness import PRESET


def covers(node: Any) -> set[str]:
    """Every cover expression in a parsed preset, at any depth."""
    if isinstance(node, dict):
        found = {node["cover"]} if isinstance(node.get("cover"), str) else set()
        for value in node.values():
            found |= covers(value)
        return found
    if isinstance(node, list):
        return set().union(*(covers(item) for item in node)) if node else set()
    return set()


def test_derived_preset_adds_no_cover() -> None:
    text = PRESET.read_text()
    shipped = covers(tomllib.loads(text))
    derived = covers(tomllib.loads(derive_preset(text)))
    assert derived - shipped == set()


def test_shipped_preset_reads_the_temporal_declarations() -> None:
    shipped = covers(tomllib.loads(PRESET.read_text()))
    assert "registry == 'temporal' and tier == 'product': registered" in shipped
