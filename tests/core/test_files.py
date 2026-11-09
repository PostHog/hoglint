from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hoglint.core.config import PathGlobs
from hoglint.core.discovery import StagedIndex
from hoglint.facts import SiteRoots
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


def test_index_mode_reads_staged_blobs_not_the_working_tree(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "a.py").write_text("STAGED = 1\n")
    git("add", "a.py")
    (tmp_path / "a.py").write_text("WORKING = 1\n")
    (tmp_path / "untracked.py").write_text("")
    snapshot = StagedIndex(tmp_path)
    try:
        assert snapshot.list(("*.py",)) == ["a.py"]
        assert (snapshot.root / "a.py").read_text() == "STAGED = 1\n"
    finally:
        snapshot.close()


@pytest.mark.parametrize(
    "path, inside",
    [
        ("/env/lib/python3.14/site-packages/django/db/models.py", "django/db/models.py"),
        ("/opt/py/purelib/attr/__init__.py", "attr/__init__.py"),
        (".venv/lib/python3.14/site-packages/django/apps.py", "django/apps.py"),
        ("/usr/lib/python3/dist-packages/yaml/__init__.py", "yaml/__init__.py"),
        ("tools/my-site-packages/x.py", None),
        ("posthog/models.py", None),
    ],
)
def test_site_roots_know_third_party_paths(path: str, inside: str | None) -> None:
    assert SiteRoots(("/opt/py/purelib",)).relative(path) == inside
