"""What a commit can change, predicted from the parent's link traces (`hoglint.native.predict`):
one small repo per kind of read, a child that edits another file than the reader, and the reader
must be predicted. Leaving the case's kind of read out of the traces must lose the reader."""

from __future__ import annotations

import gc
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hoglint.native.config import TRACE_ENV
from hoglint.native.graph import NativeGraph
from hoglint.native.normal import FIELDS, edge_row
from hoglint.native.parity import Dump, finding_row
from hoglint.native.predict import predict, write_trace_dump
from rules.test_compare import git_in, write_files

from native.require import native_module
from native.test_contract_slots import findings_of

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
    """A parent repo over `base`, the files the child changes (None deletes one), the reader that
    must be predicted although the child leaves it alone, and the read kinds it cannot do without:
    the kinds that read the part that moved."""

    name: str
    parent: dict[str, str]
    child: dict[str, str | None]
    reader: str
    kinds: tuple[str, ...]
    base: dict[str, str] = field(default_factory=lambda: BASE)


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


# Where an edge row holds its site's path; a witness is an edge row too.
PATH = FIELDS.index("path")


def write_repo(root: Path, files: dict[str, str]) -> None:
    write_files(root, files)
    git_in(root, "init", "-q")
    git_in(root, "add", ".")


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
    edges = [line for line in (out / "edges.jsonl").read_text().splitlines() if json.loads(line)[PATH] == path]
    findings = [
        line
        for line in (out / "findings.jsonl").read_text().splitlines()
        if any(witness[PATH] == path for witness in json.loads(line)[4])
    ]
    return sorted(edges + findings)


def check_reader_is_predicted(case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native_module()
    parent_files = {**case.base, **case.parent}
    child_files = {**parent_files, **case.child}
    child_files = {path: text for path, text in child_files.items() if text is not None}
    parent = traced_dump(tmp_path, "parent", parent_files, monkeypatch)
    child = traced_dump(tmp_path, "child", child_files, monkeypatch)
    assert case.reader not in case.child
    assert rows_of(parent, case.reader) != rows_of(child, case.reader), "the case must move the reader's rows"
    changed = set(case.child)
    assert case.reader in predict(parent, child, changed).files
    assert case.reader not in predict(parent, child, changed, ignored_kinds=case.kinds).files


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_a_reader_of_what_a_commit_moved_is_predicted(
    case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    check_reader_is_predicted(case, tmp_path, monkeypatch)


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


EXPORT_CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["app"]

[fields]
package = { type = "id" }

[[assign]]
match = "app/{package}/**"
set = { package = "{package}" }

[shapes.loose]
allow = ["class", "function"]

[exports.api]
within = "app"
paths = ["api/**"]
shape = "loose"

[rules.public-surface]
primitive = "entrypoint"
kinds = ["import"]
select = "src.package != dst.package"
identity = ["dst.symbol"]
ledger = false
"""


def test_an_export_module_that_stops_handing_out_a_name_moves_its_importers_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_module()
    parent_files = {
        "hoglint.toml": EXPORT_CONFIG,
        "app/__init__.py": "",
        "app/lib.py": "def other():\n    pass\n",
        "app/api/__init__.py": "from app.lib import other\n",
        "app/views/__init__.py": "",
        "app/views/page.py": "from app.api import other\n",
    }
    # A module with a function of its own keeps the imports it does not hand out by a clause.
    child_files = {**parent_files, "app/api/__init__.py": "from app.lib import other\n\n\ndef helper():\n    pass\n"}
    parent = traced_dump(tmp_path, "parent", parent_files, monkeypatch)
    child = traced_dump(tmp_path, "child", child_files, monkeypatch)
    reader = "app/views/page.py"
    assert rows_of(parent, reader) != rows_of(child, reader), "the case must move the reader's rows"
    changed = {"app/api/__init__.py"}
    assert reader in predict(parent, child, changed).files
    assert reader not in predict(parent, child, changed, ignored_kinds=["label"]).files


def patch_config(base: str) -> str:
    return f'schema = 1\nextends = ["base.toml"]\n\n[kinds.model]\nsubclass_of = {{ add = ["{base}"] }}\n'


def test_a_config_file_that_only_patches_a_key_predicts_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_module()
    parent_files = {**BASE, "base.toml": CONFIG, "hoglint.toml": patch_config("app.lib.Other")}
    child_files = {**parent_files, "hoglint.toml": patch_config("app.lib.Third")}
    parent = traced_dump(tmp_path, "parent", parent_files, monkeypatch)
    child = traced_dump(tmp_path, "child", child_files, monkeypatch)
    prediction = predict(parent, child, {"hoglint.toml"})
    assert prediction.labels is None
    assert "app/base.py" in prediction.files


DRIVES_CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["posthog", "products", "common"]
plugins = ["hoglint.presets_py.posthog"]

[vocabulary]
sections = ["posthog"]

[posthog]
"""
DRIVES_REPO = {
    "hoglint.toml": DRIVES_CONFIG,
    "posthog/__init__.py": "",
    "posthog/schema_enums.py": "",
    "posthog/hogql_queries/__init__.py": "",
    "posthog/hogql_queries/query_runner.py": (
        "def get_query_runner(kind):\n"
        '    if kind == "TrendsQuery":\n'
        "        from products.charts.backend.hogql_queries.trends import TrendsRunner\n\n"
        "        return TrendsRunner()\n"
        "    return None\n"
    ),
    "products/__init__.py": "",
    "products/charts/__init__.py": "",
    "products/charts/backend/__init__.py": "",
    "products/charts/backend/hogql_queries/__init__.py": "",
    "products/charts/backend/hogql_queries/trends.py": "class TrendsRunner:\n    pass\n",
    "common/__init__.py": "",
    "common/helpers.py": "def run_it(kind):\n    return kind\n",
    "posthog/test/__init__.py": "",
    "posthog/test/test_trends.py": (
        "from common.helpers import run_it\n\n\n"
        'class TestTrends:\n    def test_trends(self):\n        run_it("TrendsQuery")\n'
    ),
}


@pytest.mark.xfail(
    strict=True,
    reason="trace recording ends before the packs run: a pack edge that reads another file's function bodies is not traced",
)
def test_a_pack_edge_that_reads_a_helpers_body_predicts_the_unchanged_file_it_sits_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_module()
    child_files = {**DRIVES_REPO, "common/helpers.py": "def run_it(kind):\n    return process_query(kind)\n"}
    parent = traced_dump(tmp_path, "parent", DRIVES_REPO, monkeypatch)
    child = traced_dump(tmp_path, "child", child_files, monkeypatch)
    reader = "posthog/test/test_trends.py"
    assert rows_of(parent, reader) != rows_of(child, reader), "the helper's new call must move the test's drives"
    assert reader in predict(parent, child, {"common/helpers.py"}).files


HANDED_CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["app"]

[fields]
zone = { type = "zone" }

[zones]
within = "app"
default = "internal"
wiring = { paths = ["jobs/**"] }

[shapes.loose]
allow = ["class", "function"]

[exports.api]
within = "app"
paths = ["api/**"]
shape = "loose"

[rules.no-wiring-doors]
primitive = "edge"
kinds = ["import"]
endpoint = "surface"
from = "src.module == 'app.views'"
deny = "dst.handed_zones in ['wiring']"
identity = ["dst.module"]
ledger = false
"""


NAMESPACE_HANDED = {
    "hoglint.toml": HANDED_CONFIG.replace(
        'wiring = { paths = ["jobs/**"] }',
        'wiring = { paths = ["jobs/**"] }\ntests = { paths = ["**/test_*.py"], override = true }',
    ),
    "app/__init__.py": "",
    "app/jobs/run.py": "def run():\n    pass\n",
    "app/api/__init__.py": "from app import jobs\n\n__all__ = ['jobs', 'ping']\n\n\ndef ping():\n    pass\n",
    "app/views.py": "from app.api import ping\n",
}
TEST_FILE = "app/jobs/test_run.py"


# A directory without `__init__.py` has the zone its files read, so a file added to it or deleted
# from it moves what a facade that hands the directory out hands out.
@pytest.mark.parametrize("added", [True, False], ids=["a file is added", "a file is deleted"])
def test_a_file_added_to_a_handed_namespace_directory_rejudges_a_row_that_reads_handed_zones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, added: bool
) -> None:
    native_module()
    without = dict(NAMESPACE_HANDED)
    with_test = {**NAMESPACE_HANDED, TEST_FILE: "def test_run():\n    pass\n"}
    parent_files, child_files = (without, with_test) if added else (with_test, without)
    parent = traced_dump(tmp_path, "parent", parent_files, monkeypatch)
    child = traced_dump(tmp_path, "child", child_files, monkeypatch)
    reader = "app/views.py"
    assert rows_of(parent, reader) != rows_of(child, reader), "the case must move the reader's rows"
    assert reader in predict(parent, child, {TEST_FILE}).files


LAZY_GETATTR = "\n\ndef __getattr__(name):\n    raise AttributeError(name)\n"
HANDING_BASE = {
    "hoglint.toml": HANDED_CONFIG,
    "app/__init__.py": "",
    "app/jobs/__init__.py": "",
    "app/jobs/tasks.py": "def run():\n    pass\n",
    "app/logic.py": "def run():\n    pass\n",
    "app/shim.py": "from app.jobs.tasks import run as run\n",
    "app/views.py": "from app.api import ping\n",
}
FACADE = "app/api/__init__.py"
PING = "\n\ndef ping():\n    pass\n"


def hand_out_move(name: str, facade: str, child: dict[str, str | None], kinds: tuple[str, ...]) -> Case:
    return Case(name, {FACADE: facade}, child, "app/views.py", kinds, HANDING_BASE)


# Each moves the zones the facade hands out names from, so a row that reads `dst.handed_zones` at
# the facade moves.
HAND_OUT_MOVES = [
    hand_out_move(
        "the module a handed name comes from changes in the file it is imported from",
        "from app.shim import run\n\n__all__ = ['run', 'ping']\n\n\ndef ping():\n    pass\n",
        {"app/shim.py": "from app.logic import run as run\n"},
        ("label",),
    ),
    hand_out_move(
        "a lazy map entry names another module",
        '_LAZY = {"run": "app.jobs.tasks"}\n__all__ = ["run", "ping"]' + LAZY_GETATTR + PING,
        {FACADE: '_LAZY = {"run": "app.logic"}\n__all__ = ["run", "ping"]' + LAZY_GETATTR + PING},
        ("label",),
    ),
    hand_out_move(
        "an import of a module under a listed name is added",
        '__all__ = ["ping"]' + PING,
        {FACADE: 'import app.jobs.tasks as run\n\n__all__ = ["run", "ping"]' + PING},
        ("label",),
    ),
    hand_out_move(
        "the module a lazy name names is added",
        '_LAZY = {"run": "app.jobs.later"}\n__all__ = ["run", "ping"]' + LAZY_GETATTR + PING,
        {"app/jobs/later.py": "def run():\n    pass\n"},
        ("label", "module"),
    ),
    hand_out_move(
        "the module a lazy name comes from changes",
        '_LAZY = {"run": "app.shim"}\n__all__ = ["run", "ping"]' + LAZY_GETATTR + PING,
        {"app/shim.py": "from app.logic import run as run\n"},
        ("label",),
    ),
]


@pytest.mark.parametrize("case", HAND_OUT_MOVES, ids=[case.name for case in HAND_OUT_MOVES])
def test_a_moved_hand_out_rejudges_a_row_that_reads_handed_zones_at_the_destination(
    case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    check_reader_is_predicted(case, tmp_path, monkeypatch)


WATCHED_CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products"]

[fields]
package = { type = "id" }
tier = { type = "enum", values = ["core", "product"] }
seed = { type = "bool" }

[[assign]]
match = "products/{package}/**"
set = { package = "{package}", tier = "product" }

[[assign]]
match = "core/**"
set = { package = "core", tier = "core" }

[[assign]]
match = "core/routing.py"
set = { seed = true }

[rules.request-path]
primitive = "reach"
kinds = ["import"]
from = "src.seed == true"
deny = "dst.tier == 'product'"
through_denied = true
contexts = { exclude = ["function_local"] }
identity = ["src.module", "dst.module"]
report = "advisory"
ledger = false

[rules.wiring-watched]
primitive = "coverage"
covers = ["rules.request-path.dst.path", "edge.kind == 'import' and src.module == 'core.registry': dst"]
inputs = { adapter = "turbo", task = "contract-check" }
identity = ["subject"]
ledger = false
"""
WATCHED_REPO = {
    "hoglint.toml": WATCHED_CONFIG,
    "package.json": '{"name": "root", "private": true}',
    "turbo.json": '{"tasks": {"contract-check": {}}}',
    "core/__init__.py": "",
    "core/routing.py": "from products.shop.facade import api\n",
    "core/registry.py": "from products.shop import queries\n",
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/package.json": '{"name": "shop", "scripts": {"contract-check": "true"}}',
    "products/shop/turbo.json": json.dumps(
        {"extends": ["//"], "tasks": {"contract-check": {"inputs": ["facade/**", "queries/run.py"]}}}
    ),
    "products/shop/facade/__init__.py": "",
    "products/shop/facade/api.py": "def ask():\n    pass\n",
    "products/shop/logic.py": "def decide():\n    pass\n",
    "products/shop/queries/run.py": "def run():\n    pass\n",
}
# What a commit changes, and the rule whose rows it moves: both rules fold the whole graph, so the
# prediction covers their rows by naming them for rerun.
WATCHED_CHANGES = {
    "an import is added on a chain": (
        {"products/shop/facade/api.py": "from products.shop.logic import decide\n\n\ndef ask():\n    decide()\n"},
        "wiring-watched",
    ),
    "the seed moves": (
        {"core/routing.py": None, "core/routes.py": "from products.shop.facade import api\n"},
        "request-path",
    ),
    "a file is added to a directory without __init__.py": (
        {"products/shop/queries/extra.py": "def extra():\n    pass\n"},
        "wiring-watched",
    ),
    "a contract-check input changes": (
        {
            "products/shop/turbo.json": json.dumps(
                {"extends": ["//"], "tasks": {"contract-check": {"inputs": ["facade/**"]}}}
            )
        },
        "wiring-watched",
    ),
}


@pytest.fixture(scope="module")
def watched_parent(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The parent dump every watched change starts from."""
    native_module()
    with pytest.MonkeyPatch.context() as monkeypatch:
        return traced_dump(tmp_path_factory.mktemp("watched"), "parent", WATCHED_REPO, monkeypatch)


@pytest.mark.parametrize("change", sorted(WATCHED_CHANGES))
def test_a_change_to_the_request_path_or_a_watched_directory_reruns_the_rules_whose_rows_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, watched_parent: Path, change: str
) -> None:
    native_module()
    edits, rule = WATCHED_CHANGES[change]
    child_files = {path: text for path, text in {**WATCHED_REPO, **edits}.items() if text is not None}
    parent = watched_parent
    child = traced_dump(tmp_path, "child", child_files, monkeypatch)
    assert findings_of(parent, rule) != findings_of(child, rule), "the change must move the rule's rows"
    assert {"request-path", "wiring-watched"} <= predict(parent, child, set(edits)).rerun


SETTINGS_CONFIG = """
schema = 1
extends = ["hoglint:django"]

[discovery]
source_roots = ["."]
first_party = ["proj", "shop"]

[django]
settings = ["proj.settings", "proj.extra"]

[rules.middleware]
primitive = "edge"
kinds = ["dotted-path"]
select = "edge.setting == 'MIDDLEWARE'"
deny = "true"
identity = ["dst.module"]
ledger = false
"""
SETTINGS_REPO = {
    "hoglint.toml": SETTINGS_CONFIG,
    "proj/__init__.py": "",
    "proj/extra.py": 'EXTRA = ["shop.middleware.M"]\n',
    "proj/settings.py": 'from proj.extra import EXTRA\n\nINSTALLED_APPS = ["shop"]\nMIDDLEWARE = ["proj.extra", *EXTRA]\n',
    "shop/__init__.py": "",
    "shop/middleware.py": "class M:\n    pass\n",
}


# The Django pack names a settings string by the setting that takes it in, which another settings
# module can decide: the pack reads its settings modules as one unit.
def test_a_settings_module_that_stops_taking_in_a_list_moves_the_rows_of_the_module_that_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_module()
    child_files = {**SETTINGS_REPO, "proj/settings.py": 'INSTALLED_APPS = ["shop"]\nMIDDLEWARE = ["proj.extra"]\n'}
    parent = traced_dump(tmp_path, "parent", SETTINGS_REPO, monkeypatch)
    child = traced_dump(tmp_path, "child", child_files, monkeypatch)
    reader = "proj/extra.py"
    assert rows_of(parent, reader) != rows_of(child, reader), "the case must move the reader's rows"
    assert reader in predict(parent, child, {"proj/settings.py"}).files
    assert reader not in predict(parent, child, {"shop/middleware.py"}).files
