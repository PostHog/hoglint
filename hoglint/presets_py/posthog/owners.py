"""The `owners-yaml` provider: owners of a path from PostHog's owners.yaml files."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.plugins import Provided


class OwnersYamlProvider:
    use = "owners-yaml"
    allowed_options = frozenset({"files"})
    outputs = ("owners",)

    def open(self, options: Mapping[str, Any], root: Path) -> Any:
        from owners_yaml import OwnersResolver  # noqa: PLC0415 — optional dependency, only for this provider

        return OwnersResolver(root)

    def values(self, handle: Any, path: str) -> Mapping[str, Any]:
        resolution = handle.resolve(path)
        if not resolution.owners:
            return {}
        return {"owners": Provided(frozenset(resolution.owners), str(resolution.source))}


OWNERS_YAML = OwnersYamlProvider()
