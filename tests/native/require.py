"""Tests that need the built native code. CI and gate runs set HOGLINT_REQUIRE_NATIVE=1, and then a
missing native module or extractor binary fails the test instead of skipping it."""

from __future__ import annotations

import os
from types import ModuleType

import pytest

from hoglint.native import bridge


def native_required() -> bool:
    return os.environ.get("HOGLINT_REQUIRE_NATIVE") == "1"


def native_module() -> ModuleType:
    """The built `hoglint_native`. One built from other Rust sources raises EngineError, a failure."""
    try:
        return bridge.load()
    except ImportError as error:
        if native_required():
            pytest.fail(f"HOGLINT_REQUIRE_NATIVE=1, but hoglint_native is not built: {error}")
        pytest.skip("hoglint_native is not built (bin/build)")
