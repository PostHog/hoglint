"""Both engines over small repos, compared output by output as `hoglint.native.parity` does."""

from __future__ import annotations

import gc
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from hoglint.ledger.envelope import EngineError
from hoglint.model import Edge, WitnessRow, witness_row
from hoglint.native.parity import Dump, compare_dumps, read_lines
from hoglint.native.rules import Rejudged
from hoglint.native.source import ENGINES
from hoglint.rules.base import EdgeRule, Judgment, context_class, project
from hoglint.rules.engine import Engine, primitive_of
from rules.support import GraphBuilder, config_from

from native.require import native_module

# Two core modules whose labels are equal for everything the rule reads, so the native engine
# judges them as one group; each has its own owners.
OWNERS_REPO = {
    "hoglint.toml": """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products"]

[fields]
package = { type = "id" }
tier = { type = "enum", values = ["core", "product"] }
owners = { type = "set" }

[roles]
owner = "owners"

[[assign]]
match = "products/{product}/**"
set = { package = "{product}", tier = "product" }

[[assign]]
match = "core/**"
set = { package = "core", tier = "core" }

[[assign]]
match = "core/a.py"
set = { owners = "team-a" }

[[assign]]
match = "core/b.py"
set = { owners = "team-b" }

[rules.core-to-product]
primitive = "edge"
kinds = ["import"]
from = "src.tier == 'core'"
deny = "dst.tier == 'product'"
identity = "package-pair"
ledger = false
""",
    "core/__init__.py": "",
    "core/a.py": "from products.billing.models import Invoice\n",
    "core/b.py": "from products.billing.logic import charge\n",
    "products/__init__.py": "",
    "products/billing/__init__.py": "",
    "products/billing/logic.py": "def charge():\n    pass\n",
    "products/billing/models.py": "class Invoice:\n    pass\n",
}


# One presentation module whose classes and function differ only in their bases: an export that
# decides by class must keep them apart when the native engine classes destinations.
ADAPTER_REPO = {
    "hoglint.toml": """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products"]

[fields]
package = { type = "id" }
tier = { type = "enum", values = ["core", "product"] }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}", tier = "product" }

[[assign]]
match = "core/**"
set = { package = "core", tier = "core" }

[shapes.adapter]
allow = ["class"]

[exports.adapter]
within = "products/{product}"
paths = ["presentation/**"]
subclass_of = ["products.billing.presentation.mixins.BillingMixin"]
shape = "adapter"

[rules.public-surface]
primitive = "entrypoint"
kinds = ["import"]
select = "src.package != dst.package and dst.tier == 'product'"
identity = ["dst.symbol"]
ledger = false
""",
    "core/__init__.py": "",
    "core/views.py": "from products.billing.presentation.views import InvoiceViewSet, Plain, render\n",
    "products/__init__.py": "",
    "products/billing/__init__.py": "",
    "products/billing/presentation/__init__.py": "",
    "products/billing/presentation/mixins.py": "class BillingMixin:\n    pass\n",
    "products/billing/presentation/views.py": (
        "from products.billing.presentation.mixins import BillingMixin\n\n\n"
        "class InvoiceViewSet(BillingMixin):\n    pass\n\n\n"
        "class Plain:\n    pass\n\n\n"
        "def render():\n    pass\n"
    ),
}


def write_repo(repo: Path, sources: dict[str, str]) -> None:
    """A git repo that holds `sources`, staged."""
    for path, text in sources.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)


def dumps(
    tmp_path: Path, sources: dict[str, str], monkeypatch: pytest.MonkeyPatch, unreadable: tuple[str, ...] = ()
) -> dict[str, Path]:
    native_module()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    write_repo(repo, sources)
    for path in unreadable:
        (repo / path).chmod(0)
        if os.access(repo / path, os.R_OK):
            pytest.skip("this user reads files whatever their mode (root)")
    out = {}
    try:
        for engine in ENGINES:
            out[engine] = tmp_path / engine
            work = tmp_path / f"work-{engine}"
            work.mkdir()
            Dump(repo, engine, out[engine], work).run()
    finally:
        gc.enable()
        for path in unreadable:
            (repo / path).chmod(0o644)
    return out


def same_in_both_engines(out: dict[str, Path]) -> None:
    assert [diff.render() for diff in compare_dumps(out["python"], out["native"]) if not diff.equal] == []


def test_grouped_findings_take_owners_and_subjects_from_their_own_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = dumps(tmp_path, OWNERS_REPO, monkeypatch)
    same_in_both_engines(out)
    findings = read_lines(out["native"] / "findings.jsonl")
    owners = {row[3][1]: (row[6], row[8]) for row in findings}
    assert owners == {
        "core.a": (["team-a"], ["core.a", "products.billing.models"]),
        "core.b": (["team-b"], ["core.b", "products.billing.logic"]),
    }


def test_a_source_no_engine_can_read_is_the_same_incomplete_item_in_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = dumps(tmp_path, OWNERS_REPO, monkeypatch, unreadable=("core/a.py",))
    same_in_both_engines(out)
    reason = "core/a.py could not be read, so its facts are missing"
    assert ["source:unreadable", "core/a.py", reason, [], "extractor"] in read_lines(out["native"] / "incomplete.jsonl")


@dataclass(frozen=True, slots=True)
class Held:
    """A finding's edge, already made."""

    edge: Edge

    @property
    def edge_id(self) -> int:
        return self.edge.handle

    def get(self) -> Edge:
        return self.edge

    def row(self) -> WitnessRow:
        return witness_row(self.edge)


def test_a_finding_judged_again_apart_from_its_group_is_an_engine_error(tmp_path: Path) -> None:
    config = config_from(tmp_path, OWNERS_REPO["hoglint.toml"])
    built = (
        GraphBuilder()
        .module("core.a", "products.billing.models")
        .edge("import", "core.a", "products.billing.models.Invoice")
        .graph
    )
    engine = Engine(config, built, tmp_path)
    ctx = context_class(engine.labeler.index)(config, built, engine.labeler, tmp_path)
    spec = config.rules["core-to-product"]
    rule = primitive_of(spec)(ctx, spec)
    assert isinstance(rule, EdgeRule)
    edge = Held(built.edges[0])
    identity = project(spec.identity, rule.envs_of(built.edges[0])[0].resolve)
    assert Rejudged(rule, edge, Judgment.VIOLATION, identity).get().message
    with pytest.raises(EngineError):
        Rejudged(rule, edge, Judgment.UNKNOWN_VIOLATION, identity).get()
    with pytest.raises(EngineError, match="the identity"):
        Rejudged(rule, edge, Judgment.VIOLATION, ("made", "up")).get()


def test_an_export_that_decides_by_class_keeps_the_classes_of_one_module_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = dumps(tmp_path, ADAPTER_REPO, monkeypatch)
    same_in_both_engines(out)
    flagged = [row[1] for row in read_lines(out["native"] / "findings.jsonl") if row[0] == "public-surface"]
    assert sorted(flagged) == [
        ["products.billing.presentation.views.Plain"],
        ["products.billing.presentation.views.render"],
    ]


# Two core modules import a product name that resolves and one that does not, with equal labels,
# so each pair is one group: a grouped finding keeps the flags its judgment sets.
BLOCK_REPO = {
    **OWNERS_REPO,
    "hoglint.toml": OWNERS_REPO["hoglint.toml"].replace(
        "[rules.core-to-product]",
        """[rules.core-imports]
primitive = "edge"
kinds = ["import"]
from = "src.tier == 'core'"
identity = ["src.module", "edge.surface"]
growth_key = ["edge.surface"]
on_unresolved = "block"

[rules.core-to-product]""",
    ),
    "core/a.py": "from products.billing.models import Invoice\nfrom products.billing.gone import Lost\n",
    "core/b.py": "from products.billing.logic import charge\nfrom products.billing.gone import Lost\n",
}


def test_grouped_findings_keep_the_flags_their_judgment_sets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = dumps(tmp_path, BLOCK_REPO, monkeypatch)
    same_in_both_engines(out)
    flags = {
        (row[1][0], row[1][1]): (row[9], row[10])
        for row in read_lines(out["native"] / "findings.jsonl")
        if row[0] == "core-imports"
    }
    assert flags == {
        ("core.a", "products.billing.models.Invoice"): (True, False),
        ("core.b", "products.billing.logic.charge"): (True, False),
        ("core.a", "products.billing.gone.Lost"): (False, True),
        ("core.b", "products.billing.gone.Lost"): (False, True),
    }


NAMESPACE_CONFIG = """
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products", "tools"]

[fields]
package = { type = "id", required_on = ["**"] }
zone = { type = "zone" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}" }

[[assign]]
match = "core/**"
set = { package = "core" }

[[assign]]
match = "tools/{name}/**"
set = { package = "tools-{name}" }

[[assign]]
match = "tools/*"
set = { package = "tools" }

[zones]
within = "products/{product}"
default = "internal"
backend = { paths = ["backend/**"] }
facade = { parent = "backend", paths = ["backend/facade/**"] }
data-api = { parent = "facade", paths = ["backend/facade/api*.py"] }
presentation = { parent = "backend", paths = ["backend/presentation/**"] }

[rules.into]
primitive = "edge"
kinds = ["import"]
select = "src.module == 'core.user'"
deny = "dst.zone == 'presentation' or dst.zone in ['facade', 'data-api'] or dst.package == 'tools-hogli'"
identity = ["dst.module", "dst.zone", "dst.package"]
ledger = false
"""
NAMESPACE_REPO = {
    "hoglint.toml": NAMESPACE_CONFIG,
    "core/__init__.py": "",
    "core/user.py": (
        "from products.shop.backend import presentation\n"
        "from products.shop.backend import facade\n"
        "from tools import hogli\n"
    ),
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/presentation/views.py": "",
    "products/shop/backend/facade/api.py": "",
    "products/shop/backend/facade/logic.py": "",
    "tools/hogli/main.py": "",
}


# A directory without `__init__.py` reads the labels a file directly inside it reads from path
# rules, so a glob written for files (`X/**`) holds for the directory `X`. Where the files inside
# disagree, the directory's field is unknown.
def test_a_namespace_directory_reads_the_labels_of_a_file_inside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = dumps(tmp_path, NAMESPACE_REPO, monkeypatch)
    same_in_both_engines(out)
    assert sorted(row[1] for row in read_lines(out["native"] / "findings.jsonl")) == [
        ["products.shop.backend.facade", "?", "shop"],
        ["products.shop.backend.presentation", "presentation", "shop"],
        ["tools.hogli", "-", "tools-hogli"],
    ]


HANDED_CONFIG = """
schema = 1

[discovery]
source_roots = [".", "src"]
first_party = ["core", "products"]

[fields]
zone = { type = "zone", required_on = ["**"] }

[zones]
within = "products/{product}"
default = "internal"
wiring = { paths = ["backend/temporal/**", "backend/mixed/jobs.py"] }

[shapes.loose]
allow = ["class", "function"]

[exports.facade]
within = "products/{product}"
paths = ["backend/facade/**"]
shape = "loose"

[rules.logic]
primitive = "edge"
kinds = ["declares"]
from = "src.handed_zones in ['wiring']"
identity = ["dst.symbol"]
ledger = false
"""
BODY = "\n\n\ndef build() -> int:\n    return 1\n"
RUN = "def run() -> None:\n    pass\n"
BUILD_FOUND = ["products.shop.backend.facade.hooks.build"]
BUILD_INCOMPLETE = ["products.shop.backend.facade.hooks|declares|products.shop.backend.facade.hooks.build"]
GETATTR_FOUND = "products.shop.backend.facade.hooks.__getattr__"
GETATTR_INCOMPLETE = "products.shop.backend.facade.hooks|declares|products.shop.backend.facade.hooks.__getattr__"
LAZY_RUN = '_LAZY = {{"run": "{module}"}}\n\n\ndef __getattr__(name):\n    raise AttributeError(name)\n'
LAZY_TWO = (
    '_LAZY = {{"run": "{module}", "other": "{other}"}}\n\n\ndef __getattr__(name):\n    raise AttributeError(name)\n'
)
# The packages above the facade module the handed tests read.
SHOP = {
    "products/__init__.py": "",
    "products/shop/__init__.py": "",
    "products/shop/backend/__init__.py": "",
    "products/shop/backend/facade/__init__.py": "",
}
# (the import a facade module hands a name out by, the name, the files it reads, its findings, its
# incomplete items). A member whose zone cannot be told makes the module's handed zones unknown. A
# directory without `__init__.py` has the zone a file directly inside it reads; a first-party name
# that no module and no such directory answers for has none that can be told.
HANDED_CASES = {
    "a member in a known zone": (
        "from products.shop.backend.temporal.jobs import run",
        "run",
        {"products/shop/backend/temporal/__init__.py": "", "products/shop/backend/temporal/jobs.py": RUN},
        BUILD_FOUND,
        [],
    ),
    "a member whose zone is unknown": ("from core.jobs import run", "run", {}, [], BUILD_INCOMPLETE),
    "a namespace directory in a known zone": (
        "from products.shop.backend import temporal",
        "temporal",
        {"products/shop/backend/temporal/jobs.py": RUN},
        BUILD_FOUND,
        [],
    ),
    "a namespace directory inside a namespace directory": (
        "from products.shop.backend.temporal import flows",
        "flows",
        {"products/shop/backend/temporal/flows/run.py": RUN},
        BUILD_FOUND,
        [],
    ),
    "a namespace directory outside the zone": (
        "from products.shop.backend import plain",
        "plain",
        {"products/shop/backend/plain/run.py": RUN},
        [],
        [],
    ),
    "a namespace directory whose files disagree on zone": (
        "from products.shop.backend import mixed",
        "mixed",
        {"products/shop/backend/mixed/jobs.py": RUN, "products/shop/backend/mixed/other.py": RUN},
        [],
        BUILD_INCOMPLETE,
    ),
    "an unresolved first-party name": ("from products.shop.backend.gone import run", "run", {}, [], BUILD_INCOMPLETE),
    "a directory two source roots place apart": (
        "from products.shop.backend import temporal",
        "temporal",
        {"products/shop/backend/temporal/jobs.py": RUN, "src/products/shop/backend/temporal/other.py": RUN},
        [],
        BUILD_INCOMPLETE,
    ),
    "a lazy member in a known zone": (
        LAZY_RUN.format(module="products.shop.backend.temporal.jobs"),
        "run",
        {"products/shop/backend/temporal/jobs.py": RUN},
        [GETATTR_FOUND, *BUILD_FOUND],
        [],
    ),
    "a lazy member outside the zone": (
        LAZY_RUN.format(module="products.shop.backend.logic"),
        "run",
        {"products/shop/backend/logic.py": RUN},
        [],
        [],
    ),
    "a module imported under a listed name": (
        "import products.shop.backend.temporal.jobs as run",
        "run",
        {"products/shop/backend/temporal/jobs.py": RUN},
        BUILD_FOUND,
        [],
    ),
    "a module imported under a name that is not listed": (
        "import products.shop.backend.temporal.jobs as run",
        "build",
        {"products/shop/backend/temporal/jobs.py": RUN},
        [],
        [],
    ),
    "a relative import above the top package": ("from ...... import gone", "gone", {}, [], BUILD_INCOMPLETE),
    "a star import above the top package": ("from ...... import *", "gone", {}, [], BUILD_INCOMPLETE),
    "a lazy member whose module does not exist": (
        LAZY_RUN.format(module="products.shop.backend.gone"),
        "run",
        {},
        [],
        [GETATTR_INCOMPLETE, *BUILD_INCOMPLETE],
    ),
    "a module-level dict of names in a module with a lazy hook": (
        LAZY_RUN.format(module="runNow"),
        "build",
        {},
        [],
        [],
    ),
    "a module-level dict of text in a module with a lazy hook": (
        LAZY_RUN.format(module="Run the job"),
        "build",
        {},
        [],
        [],
    ),
    "a relative lazy member in a known zone": (
        LAZY_RUN.format(module="..temporal.jobs"),
        "run",
        {"products/shop/backend/temporal/jobs.py": RUN},
        [GETATTR_FOUND, *BUILD_FOUND],
        [],
    ),
    "a relative lazy member whose module does not exist": (
        LAZY_TWO.format(module="..temporal.gone", other="products.shop.backend.logic"),
        "run",
        {"products/shop/backend/logic.py": RUN},
        [],
        [GETATTR_INCOMPLETE, *BUILD_INCOMPLETE],
    ),
    "a relative value in a dict of text in a module with a lazy hook": (
        LAZY_RUN.format(module=".txt"),
        "build",
        {},
        [],
        [],
    ),
    "a relative import that resolves": (
        "from ..temporal.jobs import run",
        "run",
        {"products/shop/backend/temporal/jobs.py": RUN},
        BUILD_FOUND,
        [],
    ),
}


@pytest.mark.parametrize("case", HANDED_CASES)
def test_a_handed_member_whose_zone_is_unknown_makes_the_handed_zones_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    line, name, files, found, incomplete = HANDED_CASES[case]
    repo = {
        "hoglint.toml": HANDED_CONFIG,
        "core/__init__.py": "",
        "core/jobs.py": RUN,
        **SHOP,
        "products/shop/backend/facade/hooks.py": f"{line}\n\n__all__ = ['{name}']" + BODY,
        **files,
    }
    out = dumps(tmp_path, repo, monkeypatch)
    same_in_both_engines(out)
    assert [row[1] for row in read_lines(out["native"] / "findings.jsonl")] == [[symbol] for symbol in found]
    assert [row[1] for row in read_lines(out["native"] / "incomplete.jsonl")] == incomplete


HANDING_IMPORTS = {
    "a module under a listed name": (
        "import products.shop.backend.temporal.jobs as jobs\n"
        "import products.shop.backend.temporal.jobs as hidden\n"
        "import products.shop.backend.temporal.jobs\n\n"
        "__all__ = ['jobs', 'build']",
        ["jobs"],
        ["hidden"],
    ),
    "a relative import above the top package": ("from ...... import gone\n\n__all__ = ['gone', 'build']", ["gone"], []),
    "a lazy name whose module does not exist": (
        LAZY_RUN.format(module="products.shop.backend.gone") + "\n__all__ = ['run', 'build']",
        ["run"],
        [],
    ),
    "a relative lazy name": (LAZY_RUN.format(module="..temporal.jobs") + "\n__all__ = ['run', 'build']", ["run"], []),
    "a relative lazy name whose module does not exist": (
        LAZY_TWO.format(module="..temporal.gone", other="products.shop.backend.temporal.jobs")
        + "\n__all__ = ['run', 'build']",
        ["other", "run"],
        [],
    ),
}


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("case", HANDING_IMPORTS)
def test_the_hand_out_set_holds_every_name_a_handed_import_binds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, engine: str
) -> None:
    native_module()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    text, handed, kept = HANDING_IMPORTS[case]
    repo = {
        "hoglint.toml": HANDED_CONFIG,
        **SHOP,
        "products/shop/backend/facade/hooks.py": text + BODY,
        "products/shop/backend/temporal/jobs.py": RUN,
    }
    root = tmp_path / "repo"
    write_repo(root, repo)
    (tmp_path / "work").mkdir()
    try:
        graph, _, _, _ = Dump(root, engine, tmp_path / "out", tmp_path / "work").check()
    finally:
        gc.enable()
    found = graph.hand_outs["products.shop.backend.facade.hooks"]
    assert (sorted(found.handed), sorted(found.kept), found.unread) == (handed, kept, False)
