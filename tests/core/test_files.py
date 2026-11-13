from __future__ import annotations

import re
from pathlib import Path

import pytest

from hoglint.core import symbols
from hoglint.core.config import PathGlobs
from hoglint.outputs.tach_map import TachResolver

TACH_EXCLUDES = ("**/*__pycache__", "**/*egg-info", "docs", "products/posthog_ai/eval_harness", "products/*/evals")


@pytest.mark.parametrize(
    "path, excluded",
    [
        ("products/x/evals/run.py", True),
        ("products/x/backend/evals/run.py", False),
        ("products/posthog_ai/eval_harness/a/b.py", True),
        ("products/posthog_ai/eval_harness_v2.py", False),
        ("a/docs/conf.py", True),
        ("a/docs.py", False),
        ("a/x__pycache__/m.py", True),
    ],
)
def test_excludes_follow_gitignore_globs(path: str, excluded: bool) -> None:
    assert PathGlobs(TACH_EXCLUDES).matches(path) is excluded


@pytest.mark.parametrize(
    "dotted, case_insensitive, expected",
    [
        ("pkg.mod", False, "pkg/mod.pyi"),
        ("pkg.mod.Thing", False, "pkg/mod.pyi"),
        ("pkg", False, "pkg/__init__.py"),
        ("pkg.Mod", False, "pkg/__init__.py"),
        ("pkg.Mod", True, "pkg/Mod.pyi"),
        ("pkg.mod.a.b", False, None),
    ],
)
def test_tach_resolution_order_and_member_fallback(dotted: str, case_insensitive: bool, expected: str | None) -> None:
    resolver = TachResolver(["pkg/__init__.py", "pkg/mod.py", "pkg/mod.pyi"], (".",), case_insensitive)
    assert resolver.resolve(dotted) == expected


def test_python_flag_constants_match_the_extractor() -> None:
    facts_rs = (Path(__file__).resolve().parents[2] / "src" / "facts.rs").read_text()
    block = facts_rs[facts_rs.index("pub mod flags {") : facts_rs.index("}", facts_rs.index("pub mod flags {"))]
    rust = {name: int(value) for name, value in re.findall(r"pub const (\w+): u32 = (\d+);", block)}
    assert rust and rust == {name: getattr(symbols, name) for name in rust}
