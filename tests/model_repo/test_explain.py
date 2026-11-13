"""`explain` on the model repository, held to its stored output (explained.txt): every module's
labels, the rows the replay's labels table writes, and `explain edge:` for every fifth edge some
rule over edges sees. One engine judges and labels; this file holds what `explain` says still. Rewrite it
with `HOGLINT_REFRESH_GENERATED=1` and review the diff."""

from __future__ import annotations

import os
import contextlib
from io import StringIO
from pathlib import Path

import pytest

from hoglint.cli import Commands, Run
from native.require import native_module

from model_repo.harness import copy_tree, stub_site

EXPLAINED = Path(__file__).parent / "explained.txt"
# Every n-th edge that some rule sees is explained: the whole set would store a few megabytes.
EDGE_STEP = 5


@pytest.fixture(scope="module")
def explained(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    native_module()
    work = tmp_path_factory.mktemp("model-repo-explain")
    repo = work / "repo"
    copy_tree(repo)
    (work / "work").mkdir()
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(work / "cache"))
        commands = Commands(repo, work / "work", memo=False)
        run = commands.runner.run(repo)
        return explain_lines(commands, run)


def explain_lines(commands: Commands, run: Run) -> list[str]:
    """Each module's `explain` rows, then `explain edge:` of every `EDGE_STEP`-th edge some rule
    sees, as text lines."""
    labeler = run.result.labeler
    lines = []
    for module in sorted(run.graph.modules):
        lines.extend(f"labels {module} {name} = {value} <- {source}" for name, value, source in labeler.explain(module))
    ordinals = labeler.index.ordinals
    seen = 0
    for edge in run.graph.edges:
        out = StringIO()
        with contextlib.redirect_stdout(out):
            commands.explain_edge(run, edge.run_id(ordinals.get(edge.handle, 0)))
        text = out.getvalue()
        if "rule     " not in text:
            continue
        if seen % EDGE_STEP == 0:
            lines.extend(text.splitlines())
        seen += 1
    return lines


def test_explain_says_what_it_stored(explained: list[str]) -> None:
    if os.environ.get("HOGLINT_REFRESH_GENERATED"):
        EXPLAINED.write_text("".join(f"{line}\n" for line in explained))
    stored = EXPLAINED.read_text().splitlines()
    moved = [line for line in explained if line not in stored] + [line for line in stored if line not in explained]
    assert moved[:20] == []
    assert explained == stored
