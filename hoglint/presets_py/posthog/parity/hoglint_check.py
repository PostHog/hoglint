"""One hoglint check of the monorepo tree, shared by the comparisons that read its results.

The check runs as at cutover: the tree's own `hoglint.toml`, and a ledger written from this run's
scan, so today's findings are accepted debt and a level blocks only on what the ledger never
excuses (an unknown, an incomplete item, a finding of a strict rule).

The comparisons read `HoglintFacts`: plain rows a test can write by hand.
"""

from __future__ import annotations

import tomllib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from hoglint.cli import Runner
from hoglint.config import CONFIG_FILE, Config, load_config
from hoglint.labels import Labeler
from hoglint.ledger.envelope import PolicyFiles
from hoglint.model import Context, Edge, Finding, Graph, Resolution
from hoglint.native.source import NativeGraphSource, batch_mode
from hoglint.plugins import PLUGIN_ROOTS, REGISTRY
from hoglint.rules.base import RuleContext
from hoglint.rules.engine import Engine, primitive_of
from hoglint.rules.entities import CoveragePrimitive
from hoglint.selectors import Compare, Ref, Selector, leaves, literal_values

LEVEL = "isolated"
WIRING_RULE = "wiring-watched"
REQUEST_PATH_RULE = "request-path"
SETTING = ("edge", "setting")
# An import in one of these does not run when its module is imported.
NOT_AT_IMPORT = frozenset({Context.TEST, Context.TYPE_ONLY, Context.FUNCTION_LOCAL})


@dataclass(frozen=True)
class Witness:
    path: str
    line: int
    surface: str
    targets: tuple[str, ...] = ()

    def names(self, dotted: str) -> bool:
        """Whether this edge is the use of `dotted`, a name as an old tool writes it."""
        return self.surface == dotted or dotted in self.targets or self.surface.startswith(dotted + ".")


@dataclass(frozen=True)
class FindingRow:
    rule: str
    identity: tuple[str, ...]
    witnesses: tuple[Witness, ...] = ()
    subjects: tuple[str, ...] = ()

    def text(self) -> str:
        return f"{self.rule} [{' | '.join(self.identity)}]"


@dataclass(frozen=True)
class MigrationEdge:
    """A `migration-parent` edge: child and parent as `app.name`, and the databases each migrates on."""

    child: str
    parent: str
    child_databases: tuple[str, ...] = ()
    parent_databases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Wiring:
    """An `installs` or `signal` edge: the file it sits in, the hook or sender, and the callback or
    receiver it names."""

    path: str
    surface: str
    targets: tuple[str, ...] = ()
    callback: str = ""


@dataclass(frozen=False)
class HoglintFacts:
    """What the comparisons read of a check: findings, packages, the level and the watched paths."""

    findings: list[FindingRow] = field(default_factory=list)
    module_of_path: dict[str, str] = field(default_factory=dict)
    package_of_module: dict[str, str] = field(default_factory=dict)
    level_passing: list[str] = field(default_factory=list)
    level_failing: dict[str, list[str]] = field(default_factory=dict)
    # path -> (why the wiring rule needs it watched, whether the product watches it)
    watched: dict[str, tuple[str, bool]] = field(default_factory=dict)
    migration_edges: list[MigrationEdge] = field(default_factory=list)
    # module -> (settings file, setting key) for each settings entry that names it by dotted path
    settings_names: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    installs: list[Wiring] = field(default_factory=list)
    signals: list[Wiring] = field(default_factory=list)
    # The hooks the root config's `[django] hooks` lists: an install of any other is no edge.
    hooks: frozenset[str] = frozenset()
    symbols: frozenset[str] = frozenset()
    # product -> its files that core imports at module level and its contract-check inputs do not
    # watch: what the safety setting chosen on Nov 16 blocks on, besides unwatched wiring.
    core_reach_unwatched: dict[str, list[str]] = field(default_factory=dict)
    # (operator, values) of each comparison of a settings key in the request-path rule's selection.
    # The preset writes them all under one `not`: the keys the request path does not follow.
    unfollowed_settings: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def follows_setting(self, key: str) -> bool:
        for op, values in self.unfollowed_settings:
            if (op in ("==", "in") and key in values) or (op == "startswith" and key.startswith(values)):
                return False
        return True

    def known(self, dotted: str) -> bool:
        """Whether a dotted name is a first-party module or symbol hoglint knows."""
        return dotted in self.package_of_module or dotted in self.symbols

    @cached_property
    def path_of(self) -> dict[str, str]:
        return {module: path for path, module in self.module_of_path.items()}

    def path_of_module(self, module: str) -> str | None:
        return self.path_of.get(module)

    @cached_property
    def by_site(self) -> dict[tuple[str, int], list[tuple[FindingRow, Witness]]]:
        out: dict[tuple[str, int], list[tuple[FindingRow, Witness]]] = defaultdict(list)
        for row in self.findings:
            for witness in row.witnesses:
                out[(witness.path, witness.line)].append((row, witness))
        return out

    @cached_property
    def by_identity(self) -> dict[tuple[str, tuple[str, ...]], list[FindingRow]]:
        out: dict[tuple[str, tuple[str, ...]], list[FindingRow]] = defaultdict(list)
        for row in self.findings:
            out[(row.rule, row.identity)].append(row)
        return out

    def naming(self, path: str, line: int, dotted: str, rules: frozenset[str] | None = None) -> list[FindingRow]:
        """Findings with a witness at path:line that uses `dotted`, of `rules` when given."""
        found = []
        for row, witness in self.by_site.get((path, line), ()):
            if (rules is None or row.rule in rules) and witness.names(dotted) and row not in found:
                found.append(row)
        return found

    def with_identity(self, rule: str, identity: tuple[str, ...]) -> list[FindingRow]:
        return self.by_identity.get((rule, identity), [])

    def module_of(self, dotted: str) -> str | None:
        """The longest prefix of a dotted name that is a module hoglint knows."""
        parts = dotted.split(".")
        for end in range(len(parts), 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in self.package_of_module:
                return candidate
        return None

    def package_of_name(self, dotted: str) -> str | None:
        module = self.module_of(dotted)
        return self.package_of_module.get(module) if module is not None else None

    def package_of_path(self, path: str) -> str | None:
        module = self.module_of_path.get(path)
        return self.package_of_module.get(module) if module is not None else None


class RecordingEngine(Engine):
    """An engine that keeps the rule context of its run, so a rule can be asked again afterwards."""

    context: RuleContext | None = None

    def rule_context(self) -> RuleContext:
        self.context = super().rule_context()
        return self.context


def witness(edge: Edge) -> Witness:
    return Witness(edge.site.path, edge.site.line, edge.surface, tuple(str(t) for t in edge.targets))


def databases(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.split(",") if part)


def migration_edge(edge: Edge) -> MigrationEdge:
    return MigrationEdge(
        edge.attr("child"),
        edge.attr("parent"),
        databases(edge.attr("child_databases") or ""),
        databases(edge.attr("parent_databases") or ""),
    )


def wiring(edge: Edge, named: str) -> Wiring:
    return Wiring(edge.site.path, edge.surface, tuple(str(t) for t in edge.targets), edge.attr(named) or "")


def settings_names(graph: Graph) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in graph.by_kind("dotted-path"):
        setting = edge.attr("setting")
        module = edge.attr("module")
        if setting and module:
            out[module].append((edge.site.path, setting))
    return dict(out)


def core_reach_unwatched(graph: Graph, labeler: Labeler, covered: Callable[[str], bool]) -> dict[str, list[str]]:
    """Product files a core import statement names at module level, which the product's
    contract-check inputs do not watch, by product. The file is the one the statement names; a
    name a facade hands out from elsewhere stays behind the facade."""
    tiers: dict[str, str | None] = {}

    def tier(module: str) -> str | None:
        if module not in tiers:
            tiers[module] = labeler.known_text(module, "tier")
        return tiers[module]

    out: dict[str, set[str]] = defaultdict(set)
    for edge in graph.by_kind("import"):
        if edge.resolution != Resolution.RESOLVED or edge.contexts & NOT_AT_IMPORT or tier(edge.site.module) != "core":
            continue
        module = written_module(edge, graph)
        if module is None or tier(module) != "product":
            continue
        path = graph.modules[module]
        if covered(path):
            continue
        package = labeler.known_text(module, "package")
        if package is not None:
            out[package].add(path)
    return {package: sorted(paths) for package, paths in sorted(out.items())}


def written_module(edge: Edge, graph: Graph) -> str | None:
    """The module an import statement names, as written: not the module that defines the name."""
    written = edge.attr("module") or edge.surface
    parts = written.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in graph.modules:
            return candidate
    return None


def setting_comparisons(selector: Selector | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Each comparison of `edge.setting` in a selector, as (operator, the values it compares to)."""
    if selector is None:
        return ()
    out = []
    for leaf in leaves(selector.node):
        if isinstance(leaf, Compare) and isinstance(leaf.left, Ref) and leaf.left.path == SETTING:
            out.append((leaf.op, tuple(str(value) for value in literal_values(leaf.right))))
    return tuple(out)


def django_hooks(root: Path) -> frozenset[str]:
    section = tomllib.loads((root / CONFIG_FILE).read_text()).get("django", {})
    return frozenset(section.get("hooks", ()))


def finding_row(finding: Finding) -> FindingRow:
    return FindingRow(finding.rule, finding.identity, tuple(witness(e) for e in finding.witnesses), finding.subjects)


class HoglintCheck:
    def __init__(self, root: Path, work: Path) -> None:
        self.root = root
        self.work = work

    @cached_property
    def facts(self) -> HoglintFacts:
        batch_mode()
        source = NativeGraphSource(self.work)
        runner = Runner(source)
        config = load_config(self.root, self.root / CONFIG_FILE)
        judged = PolicyFiles(config).anchored(self.root, self.root)
        graph = source.build(self.root, judged)
        files_root = runner.files_root(self.root, "", config)
        engine = RecordingEngine(judged, graph, files_root, runner.today, runner.index_of(graph), runner.candidates)
        result = engine.evaluate(rewrite_ledger=True)
        labeler = result.labeler
        packages = {}
        for module in graph.modules:
            package = labeler.known_text(module, "package")
            if package is not None:
                packages[module] = package
        level = result.levels[LEVEL]
        covered = self.coverage(judged, engine)
        return HoglintFacts(
            findings=[finding_row(f) for f in result.findings()],
            module_of_path={path: module for module, path in graph.modules.items()},
            package_of_module=packages,
            level_passing=list(level.passing),
            level_failing={group: list(reasons) for group, reasons in level.failing.items()},
            watched=self.watched_paths(judged, engine, covered),
            migration_edges=[migration_edge(edge) for edge in graph.by_kind("migration-parent")],
            settings_names=settings_names(graph),
            installs=[wiring(edge, "callback") for edge in graph.by_kind("installs")],
            signals=[wiring(edge, "receiver") for edge in graph.by_kind("signal")],
            hooks=django_hooks(self.root),
            symbols=frozenset(graph.symbols),
            core_reach_unwatched=core_reach_unwatched(graph, labeler, covered),
            unfollowed_settings=setting_comparisons(judged.rules[REQUEST_PATH_RULE].select),
        )

    @staticmethod
    def coverage(config: Config, engine: RecordingEngine) -> Callable[[str], bool]:
        """Whether a path is in its product's contract-check inputs, as the wiring rule's adapter
        answers it (a product with no narrowed inputs watches every file)."""
        spec = config.rules[WIRING_RULE]
        context = engine.context
        assert context is not None and context.repo_root is not None
        repo = context.repo_root
        adapter = REGISTRY.coverage_adapters[str(spec.input("adapter"))]
        options = {key: value for key, value in spec.inputs if key != "adapter"}
        open_run = getattr(adapter, "open_run", None)
        if open_run is not None:
            handle = open_run(options, repo)
        else:
            handle = adapter.open(options, PLUGIN_ROOTS.of(repo, adapter, options))
        return lambda path: adapter.covered(handle, path)[0]

    @staticmethod
    def watched_paths(
        config: Config, engine: RecordingEngine, covered: Callable[[str], bool]
    ) -> dict[str, tuple[str, bool]]:
        """The wiring rule's paths, asked of the rule as the check ran it."""
        spec = config.rules[WIRING_RULE]
        context = engine.context
        assert context is not None
        rule = primitive_of(spec)(context, spec)
        assert isinstance(rule, CoveragePrimitive)
        out = {}
        for location, (why, _) in sorted(rule.required().items()):
            for path in rule.expand(location):
                out[path] = (why, covered(path))
        return out
