"""Primitives over entities and outside inputs: layout, inventory, coverage."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from hoglint.config.loader import read_table
from hoglint.config.model import CoverSpec, RegistrySpec, RuleSpec
from hoglint.globs import fill, glob_match
from hoglint.model import Edge
from hoglint.plugins import PLUGIN_ROOTS, REGISTRY
from hoglint.rules.base import (
    EdgeEnv,
    EdgeRule,
    Endpoint,
    IncompleteItem,
    Judgment,
    RuleContext,
    RuleRun,
    RuleType,
    Seen,
    StagedSelection,
    conjuncts,
    contexts_of,
    joined,
    project,
)
from hoglint.rules.plan import identity_paths, selector_tree, tree
from hoglint.selectors import (
    ClassSelection,
    Node,
    Ref,
    Resolver,
    Selector,
    State,
    Truth,
    Value,
    all_of,
    evaluate,
    refs,
    render_value,
)

# The edge fields that every edge of one kind with one set of contexts shares.
KIND_AND_CONTEXT = frozenset({("edge", "kind"), ("edge", "context")})
UNKNOWN_SELECTION = "cannot tell whether this entity is selected"


class EntityRule(RuleType):
    """Shared emit and selection for rules whose subject is a module, symbol or path."""

    ENTITIES = True

    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        self.ctx = ctx
        self.spec = spec
        self.run = RuleRun(spec)
        self.selection = ClassSelection(spec.select.node) if spec.select is not None else None

    def resolver(self, module: str | None, symbol: str | None, subject: str) -> Resolver:
        base = self.ctx.labeler.entity_resolver(module, symbol)

        def resolve(ref: Ref) -> Value:
            if ref.path == ("subject",):
                return Value.known(subject)
            return base(ref)

        return resolve

    def emit(self, subject: str, module: str | None, message: str, resolve: Resolver) -> None:
        identity = project(self.spec.identity, resolve)
        self.run.findings.append(self.ctx.finding(self.spec, identity, message, module, (module or subject,)))

    def incomplete(self, subject: str, module: str | None, reason: str) -> None:
        self.run.incomplete.append(IncompleteItem(self.spec.id, subject, reason, (module or subject,)))

    def message(self, subject: str, count: int) -> str:
        """A finding's message on `subject`; `count` is an inventory's count of values."""
        raise NotImplementedError

    def value_unknown(self) -> str:
        """Why an inventory cannot count an entity's values."""
        raise NotImplementedError

    def report_rows(self, rows: list[tuple[str, str, str | None, int, list[str]]]) -> None:
        """What a rule context found on the entities itself, (what, subject, module, count,
        identity) in order, worded here: a finding, an entity it cannot tell is selected, or an
        inventory's value it cannot read."""
        for what, subject, module, count, identity in rows:
            if what == "finding":
                message = self.message(subject, count)
                self.run.findings.append(
                    self.ctx.finding(self.spec, tuple(identity), message, module, (module or subject,))
                )
            elif what == "unknown-selection":
                self.incomplete(subject, module, UNKNOWN_SELECTION)
            else:
                self.incomplete(subject, module, self.value_unknown())

    def wanted(self) -> frozenset[str] | None:
        """The kinds a `select` on symbols pins: it is false for a symbol whose kind is known to be
        another. None when the rule is on modules or pins no kind."""
        if self.spec.on != "symbol" or self.spec.select is None:
            return None
        return self.spec.select.restricts(("kind",))

    def plan(self) -> dict[str, Any]:
        """Its `select` and `require` as entity selectors, on modules or on symbols."""
        spec = self.spec
        wanted = self.wanted()
        entities = {
            "on_symbol": spec.on == "symbol",
            "wanted": sorted(wanted) if wanted is not None else None,
            "exactly_one": None,
        }
        return {
            "id": spec.id,
            "mode": self.NATIVE_MODE,
            "selection": [tree(part) for part in conjuncts((spec.select,))],
            "require": selector_tree(spec.require),
            "identity": identity_paths(spec.identity),
            "entities": entities,
        }

    def entities(self) -> Iterator[tuple[str | None, str | None, str]]:
        """(module, symbol, subject) for every entity the rule looks at. A `select` that pins
        `kind` is false for a symbol whose kind is known to be another, so on symbols only the
        pinned kinds' members and the symbols of unknown kind are read."""
        labeler = self.ctx.labeler
        if self.spec.on == "symbol":
            wanted = self.wanted()
            symbols = labeler.index.sorted_symbols if wanted is None else labeler.kind_candidates(wanted)
            for symbol in symbols:
                yield labeler.module_of_symbol(symbol), symbol, symbol
            return
        for module, _ in labeler.index.sorted_modules:
            yield module, None, module


class LayoutPrimitive(EntityRule):
    """Required files and zones exist, moved files are gone, selected entities meet `require`."""

    PRIMITIVE = MODE = "layout"
    NATIVE_MODE = "entities"
    CONFIRM = "whole"
    READS = frozenset({"on", "select", "require", "within", "files", "absent", "registry"})

    def evaluate(self) -> RuleRun:
        if self.spec.select is not None or self.spec.require is not None:
            self.check_entities()
        if self.spec.within and (self.spec.files or self.spec.absent):
            self.check_files()
        for name in self.spec.registry:
            self.check_registry(self.ctx.config.registries[name])
        return self.run

    def check_entities(self) -> None:
        rows = self.ctx.entity_rows(self)
        if rows is not None:
            self.report_rows(rows)
            return
        for module, symbol, subject in self.entities():
            resolve = self.resolver(module, symbol, subject)
            selected = self.selection.truth(resolve) if self.selection is not None else Truth.TRUE
            if selected is Truth.FALSE:
                continue
            if selected is Truth.UNKNOWN:
                self.incomplete(subject, module, UNKNOWN_SELECTION)
                continue
            verdict = self.spec.require.evaluate(resolve) if self.spec.require is not None else Truth.FALSE
            if verdict is Truth.TRUE:
                continue
            self.emit(subject, module, self.message(subject, 0), resolve)

    def message(self, subject: str, count: int) -> str:
        if self.spec.require is None:
            return subject
        return f"{subject} does not meet `{self.spec.require.source}`"

    def roots(self) -> list[str]:
        found = self.ctx.layout_roots(self.spec.within)
        if found is not None:
            return found
        roots: set[str] = set()
        for path in self.ctx.graph.modules.values():
            captures = glob_match(self.spec.within + "/**", path)
            if captures is not None:
                roots.add(fill(self.spec.within, captures))
        return sorted(roots)

    def check_files(self) -> None:
        """Whether a file exists is a question about the snapshot: in a run of a git tree the
        tree's path list answers it, never the working tree."""
        repo = self.ctx.repo_root
        for root in self.roots():
            module = self.ctx.labeler.path_to_module.get(f"{root}/__init__.py")
            resolve = self.resolver(module, None, root)
            if self.spec.select is not None and self.spec.select.evaluate(resolve) is not Truth.TRUE:
                continue
            for required in self.spec.files:
                if repo is None:
                    self.incomplete(root, module, "no repo root to check files against")
                    break
                if not PLUGIN_ROOTS.holds(repo, f"{root}/{required.strip('/')}"):
                    self.emit(
                        f"{root}/{required}",
                        module,
                        f"{root} lacks {required}",
                        self.resolver(module, None, f"{root}/{required}"),
                    )
            for forbidden in self.spec.absent:
                if repo is not None and PLUGIN_ROOTS.holds(repo, f"{root}/{forbidden.strip('/')}"):
                    self.emit(
                        f"{root}/{forbidden}",
                        module,
                        f"{root}/{forbidden} must be gone",
                        self.resolver(module, None, f"{root}/{forbidden}"),
                    )

    def check_registry(self, registry: RegistrySpec) -> None:
        facts = self.ctx.labeler.facts
        base = registry.shape.removeprefix("abc:")
        matched = self.ctx.registry_modules(registry.module)
        if matched is None:
            matched = [
                (module, path)
                for module, path in self.ctx.labeler.index.sorted_modules
                if glob_match(registry.module, path) is not None
            ]
        for module, path in matched:
            attribute = f"{module}.{registry.attribute}"
            resolve = self.resolver(module, None, attribute)
            if not facts.has(attribute) and not facts.any_decl("assignment"):
                self.incomplete(attribute, module, "no producer records module-level assignments")
                continue
            if not facts.has(attribute):
                self.emit(
                    attribute,
                    module,
                    f"{path} matches registry {registry.name} but defines no {registry.attribute}",
                    resolve,
                )
                continue
            for member in facts.get(attribute, "members").split(","):
                member = member.strip()
                if not member or not base:
                    continue
                ancestors, complete = facts.ancestors(member)
                if base in ancestors:
                    continue
                if not complete:
                    self.incomplete(member, module, f"cannot tell whether {member} subclasses {base}")
                    continue
                self.emit(
                    member,
                    module,
                    f"{member} in {attribute} does not subclass {base}",
                    self.resolver(module, member, member),
                )


class EntityInventory(EntityRule):
    """`exactly_one`: every selected entity has exactly one value of a set field."""

    PRIMITIVE = "inventory"
    MODE = "inventory-exactly-one"
    MODE_KEY = "exactly_one"
    NATIVE_MODE = "entities"
    CONFIRM = "whole"
    READS = frozenset({"on", "select", "exactly_one"})

    def message(self, subject: str, count: int) -> str:
        return f"{subject} has {count} {self.spec.exactly_one} values, needs exactly one"

    def value_unknown(self) -> str:
        return f"{self.spec.exactly_one} is unknown"

    def plan(self) -> dict[str, Any]:
        planned = super().plan()
        return {**planned, "entities": {**planned["entities"], "exactly_one": self.spec.exactly_one}}

    def evaluate(self) -> RuleRun:
        rows = self.ctx.entity_rows(self)
        if rows is not None:
            self.report_rows(rows)
            return self.run
        name = self.spec.exactly_one
        for module, symbol, subject in self.entities():
            resolve = self.resolver(module, symbol, subject)
            if self.selection is not None:
                selected = self.selection.truth(resolve)
                if selected is Truth.UNKNOWN:
                    self.incomplete(subject, module, UNKNOWN_SELECTION)
                if selected is not Truth.TRUE:
                    continue
            value = resolve(Ref((name,)))
            if value.state is State.UNKNOWN:
                self.incomplete(subject, module, self.value_unknown())
                continue
            count = len(value.data) if value.state is State.KNOWN and isinstance(value.data, frozenset) else 0
            if count != 1:
                self.emit(subject, module, self.message(subject, count), resolve)
        return self.run


class EdgeInventory(EdgeRule):
    """`classify`: every selected edge carries a classification, and every classification still matches one."""

    PRIMITIVE = "inventory"
    MODE = "inventory-classify"
    MODE_KEY = "classify"
    NATIVE_MODE = "judge"
    FOLDS = False
    CONFIRM = "whole"
    READS = frozenset({"kinds", "contexts", "from", "select", "classify", "remedy", "on_unresolved"})

    def unclassified(self, identity: tuple[str, ...]) -> str:
        """The message of an identity the classification file lacks: the words of both engines."""
        return f"{' '.join(identity)} has no classification in {self.spec.classify}"

    @staticmethod
    def stale(identity: tuple[str, ...]) -> str:
        """The message of an entry of the classification file that matches nothing."""
        return f"classification {' '.join(identity)} matches nothing (stale)"

    def classifications(self) -> set[tuple[str, ...]]:
        repo = self.ctx.repo_root
        if repo is None or not (repo / self.spec.classify).is_file():
            return set()
        data = read_table(repo / self.spec.classify)
        return {tuple(str(v) for v in entry.get("identity", ())) for entry in data.get("entry", [])}

    def verdict_plan(self) -> dict[str, Any]:
        return {"kind": "inventory"}

    def report(self, seen: Seen, judgment: Judgment, describe: str) -> None:
        """What a selected edge reports when it cannot be classified: one that did not resolve,
        or whose selection reads an unknown value. A classifiable one is compared with the
        classification file instead."""
        if judgment is Judgment.UNKNOWN_SELECTION:
            self.incomplete(seen, "selection reads an unknown value")
            return
        super().report(seen, judgment, describe)

    def evaluate(self) -> RuleRun:
        if self.ctx.inventory_judged(self):
            return self.run
        seen: dict[tuple[str, ...], Seen] = {}
        for env in self.envs():
            judgment = self.decide(env)
            if judgment is Judgment.CLEAN:
                continue
            words = self.seen(env, judgment)
            if judgment is Judgment.VIOLATION:
                seen.setdefault(words.identity, words)
            else:
                self.report(words, judgment, self.describe(words))
        classified = self.classifications()
        for identity in sorted(seen):
            if identity not in classified:
                self.emit(seen[identity], Judgment.VIOLATION, self.unclassified(identity))
        for identity in sorted(classified - set(seen)):
            self.run.findings.append(
                self.ctx.finding(self.spec, identity, self.stale(identity), None, ledgerable=False)
            )
        return self.run


class CoveragePrimitive(EntityRule):
    """Does an outside input list (a coverage adapter, such as turbo task inputs) cover every path the rule names?

    The schema reads its identity as an entity rule's; the evaluator finds its paths in a mode of
    its own and never reads the identity.
    """

    PRIMITIVE = MODE = "coverage"
    NATIVE_MODE = "coverage"
    CONFIRM = "whole"
    READS = frozenset({"covers", "inputs"})

    def required(self) -> dict[str, tuple[str, str | None]]:
        """path -> (why, module) for every path that must be covered."""
        found = self.ctx.coverage_required(self)
        if found is not None:
            return found
        out: dict[str, tuple[str, str | None]] = {}
        for cover in self.spec.covers:
            if cover.rule:
                self.from_rule(cover, out)
            else:
                self.from_edges(cover, out)
        return out

    @staticmethod
    def column_path(cover: CoverSpec) -> tuple[str, ...]:
        """The ref of a rule cover's column: `edge.<column>` when it names no root."""
        column = cover.column
        return tuple(column.split(".")) if "." in column else ("edge", column)

    @classmethod
    def split(cls, cover: CoverSpec) -> tuple[frozenset[str] | None, list[Node], list[Node]]:
        """An edge cover's selection: the kinds it pins, its conjuncts that read only an edge's
        kind and contexts, and the rest."""
        selector = cover.selector
        kinds = selector.restricts(("edge", "kind")) if selector is not None else None
        parts = conjuncts((selector,))
        by_edge = [part for part in parts if cls.reads_kind_and_context(part)]
        return kinds, by_edge, [part for part in parts if not cls.reads_kind_and_context(part)]

    def plan(self) -> dict[str, Any]:
        """Its covers. The evaluator never reads its identity, which Python projects."""
        spec = self.spec
        covers = []
        for cover in spec.covers:
            kinds, by_edge, rest = self.split(cover)
            edge_part = joined(by_edge)
            covers.append(
                {
                    "rule": cover.rule or None,
                    "column": list(self.column_path(cover)),
                    "kinds": sorted(kinds) if kinds else None,
                    "by_edge": tree(edge_part) if edge_part is not None else None,
                    "rest": [tree(part) for part in rest],
                    "endpoint": cover.endpoint,
                }
            )
        return {"id": spec.id, "mode": self.NATIVE_MODE, "covers": covers}

    @staticmethod
    def why(cover: CoverSpec) -> str:
        """Why a cover needs a path, as a finding's message names it: the words of both engines."""
        return f"rules.{cover.rule}.{cover.column}" if cover.rule else cover.text

    def everywhere(self, key: str, reason: str) -> None:
        """An item about no one module: with no subject it blocks every level group."""
        self.run.incomplete.append(IncompleteItem(self.spec.id, key, reason))

    def not_run(self, cover: CoverSpec) -> None:
        self.everywhere(f"rules.{cover.rule}", f"rule {cover.rule} did not run before this coverage rule")

    def unplaced(self, module: str | None, surface: str) -> None:
        self.incomplete(f"{module}|{surface}", module, f"cannot place {surface!r} for coverage")

    def inherit_unknowns(self) -> None:
        """What a rule a cover reads cannot tell, this rule cannot tell either: its paths may be
        missing a path. It holds even when that rule is advisory and its own items are not shown."""
        for cover in self.spec.covers:
            run = self.ctx.results.get(cover.rule) if cover.rule else None
            if run is None:
                continue
            for item in run.incomplete:
                reason = f"rule {cover.rule} cannot tell: {item.reason}"
                key = f"rules.{cover.rule}|{item.key}"
                self.run.incomplete.append(IncompleteItem(self.spec.id, key, reason, item.subjects))

    def from_rule(self, cover: CoverSpec, out: dict[str, tuple[str, str | None]]) -> None:
        run = self.ctx.results.get(cover.rule)
        if run is None:
            self.not_run(cover)
            return
        ref = Ref(self.column_path(cover))
        for finding in run.findings:
            for edge in finding.witnesses:
                src = self.ctx.src_endpoint(edge.site)
                dst = self.ctx.dst_endpoints(edge, "origin")[0]
                value = EdgeEnv(self.ctx, edge, src, dst).resolve(ref)
                if value.state is State.KNOWN:
                    out.setdefault(render_value(value), (self.why(cover), None))
                    continue
                files = self.namespace_files(dst) if ref.path[0] == "dst" else None
                if files is not None:
                    for path in files:
                        out.setdefault(path, (self.why(cover), None))
                elif value.state is State.UNKNOWN or self.namespace_clash(dst):
                    self.unplaced(src.module, edge.surface)

    def namespace_files(self, endpoint: Endpoint) -> list[str] | None:
        """What stands for a directory without `__init__.py`: each module file directly inside it,
        as `__init__.py` stands for a package; the directory itself (`expand`) when it holds none.
        None for an endpoint that is no such directory."""
        if endpoint.external is not None or endpoint.module is None:
            return None
        return self.ctx.labeler.namespaces.files(endpoint.module)

    def namespace_clash(self, endpoint: Endpoint) -> bool:
        """A name two directories without `__init__.py` hold: no file can stand for it."""
        return endpoint.module is not None and endpoint.module in self.ctx.labeler.namespaces.clashes

    @staticmethod
    def reads_kind_and_context(node: Node) -> bool:
        return all(ref.path in KIND_AND_CONTEXT for ref in refs(node))

    def kind_and_context_truth(self, part: Node, edge: Edge, truths: dict[tuple[str, frozenset[str]], Truth]) -> Truth:
        """`part`, which reads only the edge's kind and contexts, judged once per kind and contexts."""
        key = (edge.kind, contexts_of(edge))
        found = truths.get(key)
        if found is None:
            src = self.ctx.src_endpoint(edge.site)
            found = truths[key] = evaluate(part, EdgeEnv(self.ctx, edge, src, src).resolve)
        return found

    def from_edges(self, cover: CoverSpec, out: dict[str, tuple[str, str | None]]) -> None:
        """The paths the cover's edges name. The conjuncts of its selector that read only the edge's
        kind and contexts drop an edge before its endpoints are read; the rest are staged the way
        an edge rule's selection is, so endpoint labels are judged once per endpoint or module."""
        selector = cover.selector
        kinds, by_edge_parts, rest_parts = self.split(cover)
        by_edge = joined(by_edge_parts)
        rest = joined(rest_parts)
        staged = StagedSelection(
            self.ctx, (Selector(selector.source, rest),) if selector is not None and rest is not None else ()
        )
        truths: dict[tuple[str, frozenset[str]], Truth] = {}
        for edge in self.ctx.edges_passing(tuple(sorted(kinds or ())), by_edge):
            edge_truth = self.kind_and_context_truth(by_edge, edge, truths) if by_edge is not None else Truth.TRUE
            if edge_truth is Truth.FALSE:
                continue
            src = self.ctx.src_endpoint(edge.site)
            for dst in self.ctx.dst_endpoints(edge, "surface" if cover.endpoint == "surface" else "origin"):
                truth = all_of([edge_truth, staged.truth(EdgeEnv(self.ctx, edge, src, dst))])
                if truth is Truth.FALSE:
                    continue
                endpoint = src if cover.endpoint == "src" else dst
                if endpoint.unresolved or truth is Truth.UNKNOWN:
                    self.unplaced(src.module, edge.surface)
                    continue
                if endpoint.external is not None:
                    continue
                if endpoint.path:
                    out.setdefault(endpoint.path, (self.why(cover), endpoint.module))
                    continue
                files = self.namespace_files(endpoint)
                if files is None:
                    self.unplaced(src.module, edge.surface)
                    continue
                for path in files:
                    out.setdefault(path, (self.why(cover), None))

    def expand(self, location: str) -> list[str]:
        if not location.endswith("/"):
            return [location]
        inside = sorted(p for p in self.ctx.graph.modules.values() if p.startswith(location))
        return inside or [location + "__init__.py"]

    def evaluate(self) -> RuleRun:
        repo = self.ctx.repo_root
        name = str(self.spec.input("adapter"))
        adapter = REGISTRY.coverage_adapters[name]
        if repo is None:
            self.everywhere("inputs", f"no repo root to read the {name} inputs from")
            return self.run
        options = {key: value for key, value in self.spec.inputs if key != "adapter"}
        try:
            open_run = getattr(adapter, "open_run", None)
            if open_run is not None:
                handle = open_run(options, repo)
            else:
                handle = adapter.open(options, PLUGIN_ROOTS.of(repo, adapter, options))
        except Exception as error:  # noqa: BLE001 - unreadable inputs make the rule incomplete, never clean
            self.everywhere("inputs", f"cannot read the {name} inputs: {error}")
            return self.run
        required = self.required()
        self.inherit_unknowns()
        for location, (why, module) in sorted(required.items()):
            for path in self.expand(location):
                covered, decided = adapter.covered(handle, path)
                if covered:
                    continue
                owner_module = module or self.ctx.labeler.path_to_module.get(path)
                resolve = self.resolver(owner_module, None, path)
                self.emit(path, owner_module, f"{path} is needed by {why} but {decided} does not watch it", resolve)
        return self.run
