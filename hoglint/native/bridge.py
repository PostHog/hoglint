"""Finds and imports the `hoglint_native` extension module.

`bin/build` unpacks the module it builds into `<target>/python/`, next to the
`hoglint-facts` binary; a module found there wins, as the binary does. Otherwise an installed
wheel is imported, so a developer needs no Rust toolchain at run time. In a checkout, the module
must report the `[native]` id of the checkout's own Rust sources, or the run stops: a stale
module would link with old semantics.
"""

from __future__ import annotations

import os
import sys
import importlib
import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

from hoglint.facts import DEFAULT_TARGET_DIR
from hoglint.ledger.envelope import NATIVE, EngineError, EngineSources

MODULE = "hoglint_native"
CRATE = Path(__file__).resolve().parents[2]


@contextmanager
def engine_errors() -> Iterator[None]:
    """An error the native evaluator returns is the engine's, never the config's: the config
    loaded, so a refusal here is a bug or a stale build (exit 2)."""
    try:
        yield
    except ValueError as error:
        raise EngineError(f"the native evaluator failed: {error}") from error


def built_module_dir() -> Path:
    override = os.environ.get("HOGLINT_NATIVE_DIR")
    if override:
        return Path(override)
    target_dir = Path(os.environ.get("CARGO_TARGET_DIR") or DEFAULT_TARGET_DIR)
    return target_dir / "python"


def import_module() -> ModuleType:
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


def check_sources(module: ModuleType) -> None:
    """In a checkout, the module's `[native]` id must be the one its sources make."""
    sources = EngineSources.read(CRATE)
    if sources is None:
        return
    expected = sources.digest(NATIVE)
    if module.NATIVE_ID != expected:
        raise EngineError(
            f"{MODULE} was built from other Rust sources (native id {module.NATIVE_ID}, the checkout "
            f"has {expected}): rebuild it with bin/build"
        )


def load() -> ModuleType:
    try:
        module = import_module()
    except ImportError as error:
        raise EngineError(
            f"the native module {MODULE} is missing or does not load ({error}): rebuild it with bin/build"
        ) from error
    check_sources(module)
    return module
