"""Finds and imports the `hoglint_native` extension module.

`tools/hoglint/bin/build` unpacks the module it builds into `<target>/python/`, next to the
`hoglint-facts` binary; a module found there wins, as the binary does. Otherwise an installed
wheel is imported, so a developer needs no Rust toolchain at run time.
"""

from __future__ import annotations

import os
import sys
import importlib
import importlib.util
from pathlib import Path
from types import ModuleType

from hoglint.facts import DEFAULT_TARGET_DIR

MODULE = "hoglint_native"


def built_module_dir() -> Path:
    override = os.environ.get("HOGLINT_NATIVE_DIR")
    if override:
        return Path(override)
    target_dir = Path(os.environ.get("CARGO_TARGET_DIR") or DEFAULT_TARGET_DIR)
    return target_dir / "python"


def load() -> ModuleType:
    loaded = sys.modules.get(MODULE)
    if loaded is not None:
        return loaded
    init = built_module_dir() / MODULE / "__init__.py"
    if not init.is_file():
        return importlib.import_module(MODULE)
    spec = importlib.util.spec_from_file_location(MODULE, init, submodule_search_locations=[str(init.parent)])
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {init}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    return module
