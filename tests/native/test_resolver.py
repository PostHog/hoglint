from __future__ import annotations

import json
import itertools
import subprocess
from pathlib import Path
from typing import Any

from core.support import CYCLE_KEYS, CYCLE_SOURCES
from hoglint.core.config import CoreConfig
from hoglint.native.analysis import LITE_KINDS
from hoglint.native.config import link_config
from hoglint.native.snapshot import BlobSnapshot

from native.require import native_module

Answer = tuple[str, tuple[str, ...]] | None
# What each name of the cycle resolves to: its kind and the chain of steps that reach it.
CYCLE_ANSWERS: dict[tuple[str, str], Answer] = {
    ("app.p", "X"): ("module", ("app.p.X", "app.q.X", "app.p.X")),
    ("app.q", "X"): ("module", ("app.q.X", "app.p.X", "app.q.X")),
    ("app.pkg", "sub"): ("module", ("app.pkg.sub", "app.pkg.sub")),
    ("app", "p"): ("module", ("app.p",)),
}


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    for path, text in CYCLE_SOURCES.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return repo


def linked(native: Any, repo: Path) -> Any:
    snapshot = BlobSnapshot.index(repo)
    paths = sorted(snapshot.blobs)
    blobs = [(path, *snapshot.blobs[path]) for path in paths]
    extract = {"first_party": ["app"], "kinds": [], "lite_kinds": list(LITE_KINDS)}
    config = link_config(CoreConfig(), tuple(paths), {})
    link, _ = native.link_blobs(str(repo), blobs, [], json.dumps(extract), json.dumps(config), None, 1)
    return link


def answer(hit: Any) -> Answer:
    if hit is None:
        return None
    kind, chain = hit
    return kind, tuple(f"{module}.{symbol}" if symbol else module for module, symbol in chain)


def test_names_that_meet_a_re_export_cycle_answer_alike_in_every_order(tmp_path: Path) -> None:
    native = native_module()
    repo = make_repo(tmp_path)
    shared = linked(native, repo)
    for order in itertools.permutations(CYCLE_KEYS):
        fresh = linked(native, repo) if order[0] == CYCLE_KEYS[-1] else shared
        assert {key: answer(fresh.resolve_name(*key)) for key in order} == CYCLE_ANSWERS
