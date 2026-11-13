from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.config.model import KindSpec
from hoglint.index import SymbolFacts
from hoglint.labels import Labeler
from hoglint.model import Resolution
from hoglint.rules.engine import Engine
from hoglint.selectors import NONE, UNKNOWN, State, Truth, Value

from rules.support import GraphBuilder, config_from

CONFIG = """
schema = 1

[fields]
package = { type = "id" }

[[assign]]
match = "app/**"
set = { package = "app" }

[kinds]
model = { subclass_of = "django.db.models.Model", abstract = false }
manager = { subclass_of = "django.db.models.Manager", abstract = false }
cell = { decorated_by = "lib.cell_silo_model" }
handler = { decl = "function", decorated_by = "lib.handler" }
"""

MODEL = "django.db.models.Model"
MANAGER = "django.db.models.Manager"


def graph_builder() -> GraphBuilder:
    builder = GraphBuilder().module("app.models", "app.views")
    for name in ("Team", "Base", "Child", "Mystery", "Deep", "Both", "Cell", "AbstractMystery", "MysteryCell"):
        builder.symbol(f"app.models.{name}")
    builder.symbol("app.models.Base", abstract="true").symbol("app.models.AbstractMystery", abstract="true")
    builder.symbol("app.views.handle", decl="function").symbol("app.views.plain", decl="function")
    bases = [
        ("Team", MODEL),
        ("Base", MODEL),
        ("Child", "app.models.Base"),
        ("Deep", "app.models.Mystery"),
        ("Both", MODEL),
        ("Both", MANAGER),
        ("Cell", MODEL),
    ]
    for owner, base in bases:
        resolution = Resolution.EXTERNAL if base.startswith("django.") else Resolution.RESOLVED
        builder.edge("inherit", "app.models", base, resolution=resolution, attrs=(("class", owner),))
    for owner in ("Mystery", "AbstractMystery", "MysteryCell"):
        builder.edge(
            "inherit",
            "app.models",
            None,
            "somewhere.Unknown",
            resolution=Resolution.UNRESOLVED,
            attrs=(("class", owner),),
        )
    for module, owner, decorator in (
        ("app.models", "Cell", "lib.cell_silo_model"),
        ("app.models", "MysteryCell", "lib.cell_silo_model"),
        ("app.views", "handle", "lib.handler"),
    ):
        builder.edge("decorate", module, decorator, attrs=(("decorated", owner),))
    return builder


def spec_truth(facts: SymbolFacts, spec: KindSpec, symbol: str) -> Truth:
    if spec.subclass_of and symbol in spec.subclass_of:
        return Truth.TRUE
    if not facts.has(symbol):
        return Truth.UNKNOWN
    if spec.abstract is False and facts.get(symbol, "abstract") == "true":
        return Truth.FALSE
    if spec.decl and facts.decl(symbol) != spec.decl:
        return Truth.FALSE
    if spec.decorated_by and not set(spec.decorated_by) & set(facts.decorators(symbol)):
        return Truth.FALSE
    if spec.subclass_of:
        ancestors, complete = facts.ancestors(symbol)
        if set(spec.subclass_of) & ancestors:
            return Truth.TRUE
        return Truth.FALSE if complete else Truth.UNKNOWN
    return Truth.TRUE


def classify(labeler: Labeler, symbol: str, first_party: bool) -> tuple[Value, str]:
    """The kind found by testing every [kinds] spec against the symbol alone."""
    truths = {spec.name: spec_truth(labeler.facts, spec, symbol) for spec in labeler.config.kinds.values()}
    matched = [name for name, truth in truths.items() if truth is Truth.TRUE]
    if len(matched) > 1:
        return UNKNOWN, "conflicting kinds"
    if matched:
        return Value.known(matched[0]), f"kinds.{matched[0]}"
    if Truth.UNKNOWN in truths.values() and (first_party or labeler.facts.has(symbol)):
        return UNKNOWN, "no facts for this symbol, or a base did not resolve"
    return NONE, "no kind matches"


NAMES_WITHOUT_FACTS = ("app.models.Ghost", "elsewhere.Thing", MODEL, MANAGER)


def test_kind_sets_answer_like_classifying_each_symbol(tmp_path: Path) -> None:
    graph = graph_builder().graph
    labeler = Labeler(config_from(tmp_path, CONFIG), graph, tmp_path)

    names = [*graph.symbols, *NAMES_WITHOUT_FACTS]
    for name in names:
        for first_party in (True, False):
            assert labeler.kind(name, first_party) == classify(labeler, name, first_party), (name, first_party)
    for wanted in ({"model"}, {"manager", "cell"}, {"handler"}, {"no-such-kind"}):
        kinds = {symbol: classify(labeler, symbol, True)[0] for symbol in sorted(graph.symbols)}
        expected = [
            symbol
            for symbol, value in kinds.items()
            if value.state is State.UNKNOWN or (value.state is State.KNOWN and value.data in wanted)
        ]
        assert labeler.kind_candidates(frozenset(wanted)) == expected, wanted
    conflicts = {name for name in names if classify(labeler, name, True)[1] == "conflicting kinds"}
    assert conflicts == {"app.models.Both", "app.models.Cell"}
    assert {line.partition(":")[0] for line in labeler.find_conflicts()} == conflicts


LAYOUT = """
[rules.pinned]
primitive = "layout"
on = "symbol"
select = "{pinned}"
require = "package == 'elsewhere'"
identity = ["package", "subject"]
growth_key = ["package"]

[rules.unpinned]
primitive = "layout"
on = "symbol"
select = "{unpinned}"
require = "package == 'elsewhere'"
identity = ["package", "subject"]
growth_key = ["package"]
"""


@pytest.mark.parametrize(
    "pinned, unpinned",
    [
        ("kind == 'model' and package == 'app'", "not kind != 'model' and package == 'app'"),
        ("kind in ['manager', 'handler']", "not kind not in ['manager', 'handler']"),
        ("kind == none", "not kind != none"),
    ],
)
def test_a_layout_rule_that_pins_kind_reads_only_candidates_but_finds_the_same(
    tmp_path: Path, pinned: str, unpinned: str
) -> None:
    graph = graph_builder().graph
    for conflicting in ("app.models.Both", "app.models.Cell"):
        del graph.symbols[conflicting]
    config = config_from(tmp_path, CONFIG + LAYOUT.format(pinned=pinned, unpinned=unpinned))
    runs = Engine(config, graph, tmp_path).run_rules()

    def outcome(rule: str) -> tuple[list[tuple[str, ...]], list[tuple[str, str]]]:
        run = runs[rule]
        return [f.identity for f in run.findings], [(item.key, item.reason) for item in run.incomplete]

    assert outcome("pinned") == outcome("unpinned")
    assert outcome("pinned") != ([], [])
