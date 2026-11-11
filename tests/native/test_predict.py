"""What a commit can change, predicted from the parent's link traces (`hoglint.native.predict`):
one small repo per kind of read, a child that edits another file than the reader, and the reader
must be predicted. Leaving the case's kind of read out of the traces must lose the reader."""

from __future__ import annotations

import gc
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from hoglint.native.config import TRACE_ENV
from hoglint.native.graph import NativeGraph
from hoglint.native.normal import edge_row
from hoglint.native.parity import Dump, finding_row
from hoglint.native.predict import predict, write_trace_dump

from native.require import native_module

CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["app"]

[fields]
package = { type = "id" }
owners = { type = "set" }

[providers.codeowners]
use = "codeowners"
file = "CODEOWNERS"
sets = ["owners"]

[[assign]]
match = "app/{package}/**"
set = { package = "{package}" }

[kinds.model]
subclass_of = ["app.base.Model"]

[shapes.contract]
data = ["dataclass"]
unknown = "error"
signatures = true

[exports.facade]
within = "app"
paths = ["facade/**"]
shape = "contract"

[rules.facade-signature]
primitive = "signature"
on_unknown = "finding"
identity = ["src.module", "edge.callable", "edge.slot", "dst.symbol"]
ledger = false

[rules.no-models-in-views]
primitive = "edge"
kinds = ["import"]
from = "src.package == 'views'"
deny = "dst.kind == 'model'"
identity = "module-symbol"
ledger = false
"""

BASE = {
    "hoglint.toml": CONFIG,
    "CODEOWNERS": "app/ @team-a\n",
    "app/__init__.py": "",
    "app/base.py": "class Model:\n    pass\n",
    "app/lib.py": "def other():\n    pass\n",
}


@dataclass(frozen=True)
class Case:
    """A parent repo, the files the child changes (None deletes one), the reader that must be
    predicted although the child leaves it alone, and the read kinds it cannot do without: the
    kinds that read the part that moved."""

    name: str
    parent: dict[str, str]
    child: dict[str, str | None]
    reader: str
    kinds: tuple[str, ...]


CASES = [
    Case(
        "a name that was missing appears",
        {"app/use.py": "from app.lib import thing\n"},
        {"app/lib.py": "def other():\n    pass\n\n\ndef thing():\n    pass\n"},
        "app/use.py",
        ("missing", "spelled"),
    ),
    Case(
        "a re-export is removed",
        {
            "app/pkg/__init__.py": "from app.pkg.impl import Thing\n",
            "app/pkg/impl.py": "class Thing:\n    pass\n",
            "app/use.py": "from app.pkg import Thing\n",
        },
        {"app/pkg/__init__.py": ""},
        "app/use.py",
        ("found", "spelled"),
    ),
    Case(
        "a module gains a star source",
        {
            "app/lib.py": "def extra():\n    pass\n",
            "app/star.py": "from app.base import *\n",
            "app/use.py": "from app.star import extra\n",
        },
        {"app/star.py": "from app.base import *\nfrom app.lib import *\n"},
        "app/use.py",
        ("stars",),
    ),
    Case(
        "a base class in another file gains a base",
        {
            "app/mid.py": "class Mid:\n    pass\n",
            "app/thing.py": "from app.mid import Mid\n\n\nclass Plain(Mid):\n    pass\n",
            "app/views/__init__.py": "",
            "app/views/page.py": "from app.thing import Plain\n",
        },
        {"app/mid.py": "from app.base import Model\n\n\nclass Mid(Model):\n    pass\n"},
        "app/views/page.py",
        ("target", "spelled"),
    ),
    Case(
        "a handed-out function's annotation changes in its origin file",
        {
            "app/logic.py": "def charge(amount: int) -> None:\n    pass\n",
            "app/facade/__init__.py": "",
            "app/facade/api.py": "from app.logic import charge as charge\n",
        },
        {"app/logic.py": "from app.base import Model\n\n\ndef charge(amount: Model) -> None:\n    pass\n"},
        "app/facade/api.py",
        ("found", "def", "target", "spelled"),
    ),
    Case(
        "an alias's value changes in another module",
        {
            "app/types.py": "Totals = dict[str, int]\n",
            "app/facade/__init__.py": "",
            "app/facade/api.py": "from app.types import Totals\n\n\ndef charge(amount: Totals) -> None:\n    pass\n",
        },
        {"app/types.py": "from app.base import Model\n\nTotals = dict[str, Model]\n"},
        "app/facade/api.py",
        ("found", "alias", "target", "spelled"),
    ),
    Case(
        "a file is added and deleted so that a module becomes a package",
        {"app/pkg.py": "x = 1\n", "app/use.py": "from app.pkg import helper\n"},
        {"app/pkg.py": None, "app/pkg/__init__.py": "", "app/pkg/helper.py": "def run():\n    pass\n"},
        "app/use.py",
        ("module", "missing", "spelled", "stars", "lazy", "head", "label"),
    ),
]


def write_repo(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)


def jsonl(path: Path, rows: list) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def traced_dump(tmp_path: Path, name: str, files: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Path:
    """A native check of `files` with traces, and the tables the prediction reads."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv(TRACE_ENV, "1")
    repo, out = tmp_path / f"{name}-repo", tmp_path / name
    write_repo(repo, files)
    work = tmp_path / f"{name}-work"
    work.mkdir()
    out.mkdir()
    try:
        graph, result, _, _ = Dump(repo, "native", out, work).check()
    finally:
        gc.enable()
    jsonl(out / "modules.jsonl", [[module, path] for module, path in graph.modules.items()])
    jsonl(out / "symbols.jsonl", [[name, sorted(facts.items())] for name, facts in graph.symbols.items()])
    jsonl(out / "edges.jsonl", [edge_row(edge) for edge in graph.edges])
    jsonl(out / "findings.jsonl", [finding_row(f) for f in result.findings()])
    labels = result.labeler
    jsonl(out / "labels.jsonl", [[m, p, [list(r) for r in labels.explain(m)]] for m, p in graph.modules.items()])
    assert isinstance(graph, NativeGraph)
    write_trace_dump(graph.link, result.config, out)
    return out


def rows_of(out: Path, path: str) -> list[str]:
    """The edges sited in a file and the findings whose witnesses sit there."""
    edges = [line for line in (out / "edges.jsonl").read_text().splitlines() if json.loads(line)[1] == path]
    findings = [
        line
        for line in (out / "findings.jsonl").read_text().splitlines()
        if any(witness[1] == path for witness in json.loads(line)[4])
    ]
    return sorted(edges + findings)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_a_reader_of_what_a_commit_moved_is_predicted(
    case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_module()
    parent_files = {**BASE, **case.parent}
    child_files = {**parent_files, **case.child}
    child_files = {path: text for path, text in child_files.items() if text is not None}
    parent = traced_dump(tmp_path, "parent", parent_files, monkeypatch)
    child = traced_dump(tmp_path, "child", child_files, monkeypatch)
    assert case.reader not in case.child
    assert rows_of(parent, case.reader) != rows_of(child, case.reader), "the case must move the reader's rows"
    changed = set(case.child)
    assert case.reader in predict(parent, child, changed).files
    assert case.reader not in predict(parent, child, changed, ignored_kinds=case.kinds).files


def test_an_ownership_file_relabels_the_modules_it_owns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native_module()
    parent_files = {**BASE, "app/use.py": "from app.lib import other\n"}
    child_files = {**parent_files, "CODEOWNERS": "app/ @team-a\napp/lib.py @team-b\n"}
    parent = traced_dump(tmp_path, "parent", parent_files, monkeypatch)
    child = traced_dump(tmp_path, "child", child_files, monkeypatch)
    prediction = predict(parent, child, {"CODEOWNERS"})
    assert prediction.labels is None, "a changed provider input relabels every module"
    assert "app/lib.py" in prediction.files, "its own rule rows carry its owners"
    assert "app/use.py" in prediction.files, "its rule rows read the owners of app.lib"
    assert "app/use.py" not in predict(parent, child, {"CODEOWNERS"}, ignored_kinds=["label"]).files
