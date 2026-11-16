"""What `explain` says on the model repository agrees with the run it explains.

- Labels: every value `explain` prints for every module is the value the evaluator holds for it:
  its value columns, its registries, and the zones a module hands out names from. The replay's
  labels table is these rows, so the change predictor reads what the rules read.
- Edges: for every rule judged per destination and every edge it sees, the truths `explain edge:`
  prints for each destination give a judgment by `tests/rules/connectives.txt` and
  `tests/rules/judgment.txt`. The rule has a finding with that edge as its witness, or an
  incomplete item on that edge, exactly where that judgment is not clean. What a judgment reads
  and `explain edge:` does not print comes from the edge (a slot's annotation) and from the
  evaluator's `edge_values` (the shape a use-shape rule reads). Reach and cycle rules are left out:
  their rows come from a search over many edges.
- A stored sample (`explained.txt`): what `explain` prints for a few modules and edges that cases
  of CASES.md name, so a change of wording, the label sources included, is seen and reviewed.
  Rewrite it with `HOGLINT_REFRESH_GENERATED=1` and review the diff."""

from __future__ import annotations

import os
import contextlib
from array import array
from collections import Counter
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from hoglint.cli import Commands, Run
from hoglint.config.model import RuleSpec
from hoglint.labels import HANDED_ZONES
from hoglint.model import Edge
from hoglint.native.labels import NativeLabeler
from hoglint.rules.engine import primitive_of
from native.require import native_module

from model_repo.harness import copy_tree, stub_site

EXPLAINED = Path(__file__).parent / "explained.txt"
TABLES = Path(__file__).parents[1] / "rules"

# The sample `explained.txt` holds, each with the case of CASES.md it shows.
SAMPLE_MODULES = [
    "posthog.api.widget_report",  # PS01: core code, labels from an assignment and outside a domain
    "posthog.settings.web",  # PS11: a settings module
    "products.legacy.backend.routes",  # PF03: a module a registry holds, in a zone inside a zone
    "products.catalog.backend.facade.models",  # ES01, FL04: a facade module that hands out names
    "products.warehouse_sources.sources.alpha.source",  # SV01: a sources vendor
    "sandbox.shapes",  # WK03: a top folder no assignment claims, so its labels are unknown
]
# Edges by (kind, source module, surface); one names every edge it matches.
SAMPLE_EDGES = [
    ("import", "posthog.api.widget_report", "products.legacy.backend.logic.widgets"),  # PS01: two findings
    ("reference", "products.legacy.backend.logic.widgets", "products.inventory.backend.models.StockItem"),  # MC01, MC03
    ("exposes", "products.catalog.backend.facade.api", "typing.Any"),  # FS06, TM01: unknown types and a clean slot
    ("import", "products.catalog.backend.facade.api", "posthog.models.ghost.Ghost"),  # CR03: an unresolved import
    ("import", "products.legacy.backend.logic.widgets", "scripts.helpers.slug"),  # WK04: incomplete
    ("import", "products.warehouse_sources.sources.sdk.base", "common.hogvm.stl.quote"),  # SV02: `unless` read
    ("import", "products.reports.backend.logic.build", "products.inventory.backend.facade.api.get_stock"),  # PS06
]

# A row's kind in the result: a finding, or an incomplete item.
FINDING = "finding"
INCOMPLETE = "incomplete"


class Truths:
    """`connectives.txt` (how `and`, `or` and `not` carry truth) and `judgment.txt` (a
    destination's judgment from what its rule reads of it), read as the Rust tests read them."""

    def __init__(self) -> None:
        self.connectives: dict[tuple[str, str, str], str] = {}
        for connective, left, right, truth in self.rows(TABLES / "connectives.txt"):
            self.connectives[(connective, left, right)] = truth
        self.judgments = self.rows(TABLES / "judgment.txt")

    @staticmethod
    def rows(path: Path) -> list[list[str]]:
        return [line.split() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]

    def join(self, connective: str, parts: list[str]) -> str:
        out = parts[0]
        for part in parts[1:]:
            out = self.connectives[(connective, out, part)]
        return out

    def negate(self, truth: str) -> str:
        return self.connectives[("not", truth, "-")]

    def judgment(self, inputs: list[str]) -> str:
        """The judgment of the first row whose inputs match; `-` matches any input."""
        for *cells, judgment in self.judgments:
            if all(cell in ("-", given) for cell, given in zip(cells, inputs)):
                return judgment
        raise AssertionError(f"judgment.txt has no row for {inputs}")


@pytest.fixture(scope="module")
def explained(tmp_path_factory: pytest.TempPathFactory) -> tuple[Commands, Run]:
    native_module()
    work = tmp_path_factory.mktemp("model-repo-explain")
    repo = work / "repo"
    copy_tree(repo)
    (work / "work").mkdir()
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(work / "cache"))
        commands = Commands(repo, work / "work", memo=False)
        return commands, commands.runner.run(repo)


def evaluator_of(run: Run) -> Any:
    """The native evaluator that judged the run."""
    labeler = run.result.labeler
    assert isinstance(labeler, NativeLabeler) and labeler.evaluator is not None
    return labeler.evaluator


def held_words(kind: str, items: list[str], chain: tuple[str, ...] = ()) -> str:
    """A value the evaluator holds, as (type, items), in the words `explain` prints."""
    if kind in ("unknown", "none"):
        return kind
    if kind == "set":
        return ", ".join(sorted(items)) or "{}"
    if kind == "zone" and len(chain) > 1:
        return f"{items[0]}  (inside {', '.join(chain[1:])})"
    return items[0]


def test_explain_prints_the_labels_the_evaluator_holds(explained: tuple[Commands, Run]) -> None:
    _, run = explained
    labeler, config = run.result.labeler, run.config
    evaluator = evaluator_of(run)
    no_place = native_module().NO_PLACE
    columns = {}
    for name, field in config.fields.items():
        if field.on == "module":
            typed, places = evaluator.values(name)
            columns[name] = (typed, array("I", places))
    registries = list(config.registries.values())
    registry_places = array("I", evaluator.registries())
    differ = []
    for at, module in enumerate(run.graph.modules):
        shown = {name: value for name, value, _ in labeler.explain(module)}
        held: dict[str, str] = {}
        for name, (typed, places) in columns.items():
            assert places[at] != no_place, f"{module}: the evaluator holds no {name}"
            kind, items = typed[places[at]]
            held[name] = held_words(kind, items, config.zones.chain(items[0]) if kind == "zone" else ())
        zones = evaluator.handed_zones(module)
        if HANDED_ZONES in shown or zones != ("none", []):
            held[HANDED_ZONES] = held_words(*zones)
        if registry_places[at] != no_place:
            registry = registries[registry_places[at]]
            held["registry"] = registry.name
            held["lifecycle"] = registry.lifecycle or "none"
        if shown != held:
            differ.append(f"{module}: explain prints {shown}, the evaluator holds {held}")
    assert differ == []


def use_shape_verdict(spec: RuleSpec, shape: str, label: str, use: str) -> str:
    """A use-shape rule's verdict from what its `edge_values` read (`-` is none): the shape, or the
    edge's use when it has none, is a violation when `allow_shapes` names neither it nor its form
    with detail, or when `deny_shapes` names one."""
    named = {shape, label} if shape != "-" else {use if use != "-" else "other"}
    if spec.allow_shapes and not named & set(spec.allow_shapes):
        return "true"
    return "true" if named & set(spec.deny_shapes) else "false"


def row_kind(spec: RuleSpec, judgment: str) -> str | None:
    """The row a judgment makes: none when clean; an incomplete item where the rule reports what it
    cannot tell as incomplete (`on_unresolved`, `on_unknown`); else a finding."""
    if judgment == "clean":
        return None
    if judgment == "unknown-selection":
        return INCOMPLETE
    if judgment == "unresolved" and spec.on_unresolved == "incomplete":
        return INCOMPLETE
    if judgment == "unknown-type" and spec.on_unknown == "incomplete":
        return INCOMPLETE
    return FINDING


class Explained:
    """Per (rule, edge, row kind), the rows the judgments of `explain edge:`'s truths give, and the
    count of each judgment met."""

    def __init__(self, run: Run, truths: Truths) -> None:
        self.evaluator = evaluator_of(run)
        self.truths = truths
        self.rows: Counter[tuple[str, int, str]] = Counter()
        self.judgments: Counter[str] = Counter()

    def verdicts(self, at: int, spec: RuleSpec, edge: Edge, truths: dict[str, str]) -> list[str]:
        """The verdict of each destination: a use-shape rule's from the shape it reads there, any
        other rule's from its predicates, which `explain edge:` prints (true when it has none)."""
        mode = primitive_of(spec).MODE
        if mode == "use-shape":
            columns = [["edge", "shape"], ["edge", "label"], ["edge", "use"]]
            found = self.evaluator.edge_values(at, [edge.handle], columns)[0]
            return [use_shape_verdict(spec, *values) for values in found]
        assert mode in ("edge", "entrypoint", "signature"), f"{spec.id}: no verdict read for mode {mode}"
        parts = [truths["deny"]] if "deny" in truths else []
        parts += [self.truths.negate(truths[key]) for key in ("allow", "require") if key in truths]
        # An entrypoint rule with no predicate reads `dst.exported`, which `explain edge:` does not print.
        assert parts or mode != "entrypoint", f"{spec.id}: an entrypoint rule with no predicate"
        return [self.truths.join("or", parts) if parts else "true"]

    def judge(self, at: int, spec: RuleSpec, edge: Edge) -> None:
        found = self.evaluator.explain_edge(at, edge.handle)
        if found is None:
            return
        signature = primitive_of(spec).MODE == "signature"
        verdicts: list[str] = []
        for place, (symbol, module, named) in enumerate(found):
            truths = dict(named)
            verdicts = verdicts or self.verdicts(at, spec, edge, truths)
            unknown_type = signature and (edge.attr("annotation") == "missing" or symbol in spec.unknown_types)
            inputs = [
                self.truths.join("and", [truths.get("from_", "true"), truths.get("select", "true")]),
                "yes" if module is None else "no",
                "yes" if unknown_type else "no",
                verdicts[place] if len(verdicts) > 1 else verdicts[0],
                truths.get("unless", "none"),
            ]
            judgment = self.truths.judgment(inputs)
            self.judgments[judgment] += 1
            kind = row_kind(spec, judgment)
            if kind is not None:
                self.rows[(spec.id, edge.handle, kind)] += 1


def result_rows(run: Run, rules: set[str]) -> Counter[tuple[str, int, str]]:
    """Per (rule, edge, row kind), the findings of `rules` whose witness is the edge and their
    incomplete items on it."""
    out: Counter[tuple[str, int, str]] = Counter()
    for finding in run.result.findings():
        if finding.rule in rules:
            out.update((finding.rule, edge.handle, FINDING) for edge in finding.witnesses)
    for item in run.result.incomplete():
        if item.rule in rules and item.edge is not None:
            out[(item.rule, item.edge.handle, INCOMPLETE)] += 1
    return out


def test_explain_edge_judges_each_edge_as_the_run_reported_it(explained: tuple[Commands, Run]) -> None:
    _, run = explained
    judged = {
        at: spec for at, spec in enumerate(run.config.rules.values()) if primitive_of(spec).NATIVE_MODE == "judge"
    }
    seen = Explained(run, Truths())
    for at, spec in judged.items():
        for edge in run.graph.edges:
            seen.judge(at, spec, edge)
    edges = {edge.handle: edge for edge in run.graph.edges}

    def described(key: tuple[str, int, str]) -> str:
        rule, handle, kind = key
        edge = edges[handle]
        return f"{rule} {kind} {edge.kind} {edge.site.path}:{edge.site.line} {edge.surface}"

    reported = result_rows(run, {spec.id for spec in judged.values()})
    assert [described(key) for key in seen.rows - reported] == []
    assert [described(key) for key in reported - seen.rows] == []
    # Each judgment but an unknown violation is met, so the agreement is no vacuous one. No case
    # of the model repository gives a predicate an unknown on a selected destination.
    assert {row[-1] for row in seen.truths.judgments} - set(seen.judgments) == {"unknown-violation"}, seen.judgments


def sample_lines(commands: Commands, run: Run) -> list[str]:
    """What `explain` prints for the sample: each module's labels, then `explain edge:` of each
    sample edge."""
    labeler = run.result.labeler
    lines = []
    for module in SAMPLE_MODULES:
        lines.extend(f"labels {module} {name} = {value} <- {source}" for name, value, source in labeler.explain(module))
    ordinals = labeler.index.ordinals
    for kind, module, surface in SAMPLE_EDGES:
        edges = [e for e in run.graph.edges if (e.kind, e.site.module, e.surface) == (kind, module, surface)]
        assert edges, f"no {kind} edge from {module} to {surface}"
        for edge in edges:
            out = StringIO()
            with contextlib.redirect_stdout(out):
                commands.explain_edge(run, edge.run_id(ordinals.get(edge.handle, 0)))
            lines.extend(out.getvalue().splitlines())
    return lines


def test_explain_says_what_its_stored_sample_says(explained: tuple[Commands, Run]) -> None:
    lines = sample_lines(*explained)
    if os.environ.get("HOGLINT_REFRESH_GENERATED"):
        EXPLAINED.write_text("".join(f"{line}\n" for line in lines))
    assert lines == EXPLAINED.read_text().splitlines()
