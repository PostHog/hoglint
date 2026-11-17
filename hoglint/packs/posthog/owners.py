"""The `owners-yaml` provider: owners of a path from PostHog's owners.yaml files."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hoglint.config.schema import as_tuple
from hoglint.ledger.envelope import Framework
from hoglint.plugins import Provided

# owners.yaml, and the alias file the root owners.yaml names (`alias_files`).
DEFAULT_FILES = ("owners.yaml", "product.yaml")


class OwnersYamlProvider:
    """`files` names every ownership file the provider reads, in any directory. The root
    owners.yaml names the alias files, so `open` fails when it names one `files` leaves out."""

    use = "owners-yaml"
    allowed_options = frozenset({"files"})
    outputs = ("owners",)
    distributions = ("owners_yaml",)

    @staticmethod
    def files(options: Mapping[str, Any]) -> tuple[str, ...]:
        return as_tuple(options.get("files")) or DEFAULT_FILES

    def inputs(self, options: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(f"**/{name}" for name in self.files(options))

    def open(self, options: Mapping[str, Any], root: Path) -> Any:
        missing = Framework.missing(self.distributions)
        if missing:
            raise ModuleNotFoundError(f"no installed distribution ships {', '.join(missing)}")
        from owners_yaml import OwnersResolver  # noqa: PLC0415 — optional dependency, only for this provider

        resolver = OwnersResolver(root)
        undeclared = [name for name in resolver.ownership_filenames() if name not in self.files(options)]
        if undeclared:
            raise ValueError(f"owners.yaml names ownership files {', '.join(undeclared)}; add them to `files`")
        return resolver

    def values(self, handle: Any, path: str) -> Mapping[str, Any]:
        resolution = handle.resolve(path)
        if not resolution.owners:
            return {}
        return {"owners": Provided(frozenset(resolution.owners), str(resolution.source))}


OWNERS_YAML = OwnersYamlProvider()
