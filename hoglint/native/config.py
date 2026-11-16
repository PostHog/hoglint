"""What the native link reads besides facts: the core config, the analyzed files, site-packages
paths and the interpreter's builtin names."""

from __future__ import annotations

import builtins
from collections.abc import Mapping
from typing import Any

from hoglint.core.config import CoreConfig
from hoglint.ledger.cache import TRACE_ENV, RunStores

# The trace switch is declared with the run's stores; its name stays importable here.
__all__ = ["TRACE_ENV", "link_config"]


def link_config(config: CoreConfig, analyzed: tuple[str, ...], external: Mapping[str, str]) -> dict[str, Any]:
    """The JSON the link takes. Builtins come from this interpreter."""
    return {
        "source_roots": list(config.source_roots),
        "first_party": list(config.first_party),
        "test_paths": list(config.test_paths),
        "migration_paths": list(config.migration_paths),
        "dynamic_loaders": list(config.dynamic_loaders),
        "dataclass_decorators": list(config.dataclass_decorators),
        "frozen_dataclass_decorators": list(config.frozen_dataclass_decorators),
        "dataclass_bases": list(config.dataclass_bases),
        "patch_callees": list(config.patch_callees),
        "path_callees": list(config.path_callees),
        "path_modules": list(config.path_modules),
        "contract_surfaces": [
            {"within": surface.within, "paths": list(surface.paths)} for surface in config.contract_surfaces
        ],
        "export_surfaces": [
            {"within": surface.within, "paths": list(surface.paths)} for surface in config.export_surfaces
        ],
        "builtins": sorted(dir(builtins)),
        "external": sorted(external.items()),
        "analyzed": list(analyzed),
        "trace": RunStores.current().trace_on(),
    }
