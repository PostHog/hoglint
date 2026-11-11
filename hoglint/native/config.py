"""What the native link reads besides facts: the core config, the analyzed files, site-packages
paths and the interpreter's builtin names."""

from __future__ import annotations

import builtins
from collections.abc import Mapping
from typing import Any

from hoglint.core.config import CoreConfig


def link_config(config: CoreConfig, analyzed: tuple[str, ...], external: Mapping[str, str]) -> dict[str, Any]:
    """The JSON the link takes. Builtins come from this interpreter, as the Python engine's do."""
    return {
        "source_roots": list(config.source_roots),
        "first_party": list(config.first_party),
        "test_paths": list(config.test_paths),
        "migration_paths": list(config.migration_paths),
        "dynamic_loaders": list(config.dynamic_loaders),
        "dataclass_decorators": list(config.dataclass_decorators),
        "patch_callees": list(config.patch_callees),
        "contract_surfaces": [
            {"within": surface.within, "paths": list(surface.paths)} for surface in config.contract_surfaces
        ],
        "builtins": sorted(dir(builtins)),
        "external": sorted(external.items()),
        "analyzed": list(analyzed),
    }
