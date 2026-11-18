"""Primitives over entities and outside inputs: layout, inventory, coverage."""

from __future__ import annotations

import bisect
from functools import cached_property
from typing import Any

from hoglint.config.loader import read_table
from hoglint.config.model import CoverSpec, RegistrySpec, RuleSpec
from hoglint.config.schema import required_file
from hoglint.globs import alternatives
from hoglint.model import IncompleteKind
from hoglint.plugins import PLUGIN_ROOTS, REGISTRY, open_coverage
from hoglint.rules.base import EdgeRule, IncompleteItem, Judgment, RuleContext, RuleRun, RuleType, Seen, project
from hoglint.rules.plan import identity_paths, selector_tree, tree
from hoglint.selectors import Node, Ref, Resolver, Truth, Value, conjuncts, joined, refs

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

    def incomplete(self, subject: str, module: str | None, reason: str, kind: IncompleteKind) -> None:
        self.run.incomplete.append(IncompleteItem(self.spec.id, subject, reason, (module or subject,), kind=kind))

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
                self.incomplete(subject, module, UNKNOWN_SELECTION, IncompleteKind.UNKNOWN_LABEL)
            else:
                self.incomplete(subject, module, self.value_unknown(), IncompleteKind.UNKNOWN_LABEL)

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


class LayoutPrimitive(EntityRule):
    """Required files and zones exist, moved files are gone, selected entities meet `require`.
    A required file may also have to define a function or a class."""

    PRIMITIVE = MODE = "layout"
    NATIVE_MODE = "entities"
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
        self.report_rows(self.ctx.entity_rows(self))

    def message(self, subject: str, count: int) -> str:
        if self.spec.require is None:
            return f"{subject} is in a place that the rule does not allow"
        return f"{subject} does not meet `{self.spec.require.source}`"

    def root_module(self, root: str, namespaces: dict[str, str]) -> str | None:
        """The module that stands for a root: its `__init__.py`, else the root as a namespace
        directory (`namespaces`: directory -> its dotted name), so a finding there has a package."""
        module = self.ctx.labeler.path_to_module.get(f"{root}/__init__.py")
        return module if module is not None else namespaces.get(root)

    def check_files(self) -> None:
        """Whether a file exists is a question about the snapshot: in a run of a git tree the
        tree's path list answers it, never the working tree. A required file may have `{a,b}`
        alternatives, and may name a declaration it must define, which the evaluator answers once
        for every root."""
        repo = self.ctx.repo_root
        namespaces = {directory: name for name, directory in self.ctx.labeler.namespaces.directory_of.items()}
        defining: list[tuple[str, str | None, str]] = []
        for root in self.ctx.layout_roots(self.spec.within):
            module = self.root_module(root, namespaces)
            resolve = self.resolver(module, None, root)
            if self.spec.select is not None and self.spec.select.evaluate(resolve) is not Truth.TRUE:
                continue
            for required in self.spec.files:
                if repo is None:
                    self.incomplete(root, module, "no repo root to check files against", IncompleteKind.PRODUCER_FAILED)
                    break
                path, decl = required_file(required)
                held = [f"{root}/{choice.strip('/')}" for choice in alternatives(path)]
                held = [found for found in held if PLUGIN_ROOTS.holds(repo, found)]
                if not held:
                    self.emit(
                        f"{root}/{path}",
                        module,
                        f"{root} lacks {path}",
                        self.resolver(module, None, f"{root}/{path}"),
                    )
                elif decl:
                    defining.append((held[0], module, decl))
            for forbidden in self.spec.absent:
                if repo is not None and PLUGIN_ROOTS.holds(repo, f"{root}/{forbidden.strip('/')}"):
                    self.emit(
                        f"{root}/{forbidden}",
                        module,
                        f"{root}/{forbidden} must be gone",
                        self.resolver(module, None, f"{root}/{forbidden}"),
                    )
        if defining:
            self.check_defined(defining)

    def check_defined(self, defining: list[tuple[str, str | None, str]]) -> None:
        """Each (path, root module, declaration): the file defines a symbol with that declaration.
        A file no graph module has cannot be told."""
        found = self.ctx.defined_in([path for path, _, _ in defining])
        for (path, module, decl), decls in zip(defining, found, strict=True):
            if decls is None:
                reason = f"no module has the path {path}, so what it defines cannot be told"
                self.incomplete(path, module, reason, IncompleteKind.UNRESOLVED)
            elif decl not in decls:
                self.emit(path, module, f"{path} defines no {decl}", self.resolver(module, None, path))

    def check_registry(self, registry: RegistrySpec) -> None:
        facts = self.ctx.labeler.facts
        base = registry.shape.removeprefix("abc:")
        for module, path in self.ctx.registry_modules(registry.module):
            attribute = f"{module}.{registry.attribute}"
            resolve = self.resolver(module, None, attribute)
            if not facts.has(attribute) and not facts.any_decl("assignment"):
                reason = "no producer records module-level assignments"
                self.incomplete(attribute, module, reason, IncompleteKind.NOT_MODELLED)
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
                    reason = f"cannot tell whether {member} subclasses {base}"
                    self.incomplete(member, module, reason, IncompleteKind.UNRESOLVED)
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
    READS = frozenset({"on", "select", "exactly_one"})

    def message(self, subject: str, count: int) -> str:
        return f"{subject} has {count} {self.spec.exactly_one} values, needs exactly one"

    def value_unknown(self) -> str:
        return f"{self.spec.exactly_one} is unknown"

    def plan(self) -> dict[str, Any]:
        planned = super().plan()
        return {**planned, "entities": {**planned["entities"], "exactly_one": self.spec.exactly_one}}

    def evaluate(self) -> RuleRun:
        self.report_rows(self.ctx.entity_rows(self))
        return self.run


class EdgeInventory(EdgeRule):
    """`classify`: every selected edge carries a classification, and every classification still matches one."""

    PRIMITIVE = "inventory"
    MODE = "inventory-classify"
    MODE_KEY = "classify"
    NATIVE_MODE = "judge"
    FOLDS = False
    READS = frozenset({"kinds", "contexts", "from", "select", "classify", "on_unresolved"})

    def unclassified(self, identity: tuple[str, ...]) -> str:
        """The message of an identity the classification file lacks."""
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
            self.incomplete(seen, "selection reads an unknown value", IncompleteKind.UNKNOWN_LABEL)
            return
        super().report(seen, judgment, describe)

    def evaluate(self) -> RuleRun:
        self.ctx.inventory_judged(self)
        return self.run


class CoveragePrimitive(EntityRule):
    """Does an outside input list (a coverage adapter, such as turbo task inputs) cover every path the rule names?

    The schema reads its identity as an entity rule's; the evaluator finds its paths in a mode of
    its own and never reads the identity.
    """

    PRIMITIVE = MODE = "coverage"
    NATIVE_MODE = "coverage"
    READS = frozenset({"covers", "inputs"})

    def required(self) -> dict[str, tuple[CoverSpec, str | None]]:
        """path -> (the cover that needs it, module) for every path that must be covered."""
        return self.ctx.coverage_required(self)

    @classmethod
    def cover_kinds(cls, spec: RuleSpec) -> set[str] | None:
        """The edge kinds the rule's edge covers read; None when one reads every kind. A failed
        producer of one of them leaves the rule's paths short, so the rule is incomplete."""
        out: set[str] = set()
        for cover in spec.covers:
            if cover.rule or cover.entity:
                continue
            kinds, _, _ = cls.split(cover)
            if kinds is None:
                return None
            out |= kinds
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
        """Why a cover needs a path, as a finding's message names it: its own words, else its text."""
        if cover.why:
            return cover.why
        return f"rules.{cover.rule}.{cover.column}" if cover.rule else cover.text

    def everywhere(self, key: str, reason: str) -> None:
        """An item about no one module, an input or a rule the coverage could not read: with no
        subject it blocks every level group."""
        self.run.incomplete.append(IncompleteItem(self.spec.id, key, reason, kind=IncompleteKind.PRODUCER_FAILED))

    def not_run(self, cover: CoverSpec) -> None:
        self.everywhere(f"rules.{cover.rule}", f"rule {cover.rule} did not run before this coverage rule")

    def unplaced(self, module: str | None, surface: str) -> None:
        reason = f"cannot place {surface!r} for coverage"
        self.incomplete(f"{module}|{surface}", module, reason, IncompleteKind.NOT_MODELLED)

    def unread_items(self, module: str | None, sequence: str) -> None:
        reason = f"cannot read every item of {sequence!r}, so the modules that define them are unknown"
        self.incomplete(f"{module}|{sequence}", module, reason, IncompleteKind.NOT_MODELLED)

    def unread_core_items(self, sequence: str) -> None:
        """An item the link cannot read, of a list no group owns that a registration point
        registers: it may hold any product's list, so it blocks every group and the answer."""
        reason = (
            f"cannot read every item of {sequence!r}, a list core registers, so the product lists it holds are unknown"
        )
        item = IncompleteItem(self.spec.id, f"unread|{sequence}", reason, kind=IncompleteKind.NOT_MODELLED)
        self.run.incomplete.append(item)

    def unknown_selection(self, module: str | None, cover: CoverSpec) -> None:
        reason = f"cannot tell whether `{cover.text}` selects {module}"
        self.incomplete(f"{module}|{cover.text}", module, reason, IncompleteKind.UNKNOWN_LABEL)

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
                inherited = IncompleteItem(self.spec.id, key, reason, item.subjects, edge=item.edge, kind=item.kind)
                self.run.incomplete.append(inherited)

    @staticmethod
    def reads_kind_and_context(node: Node) -> bool:
        return all(ref.path in KIND_AND_CONTEXT for ref in refs(node))

    def record(self) -> RuleRun:
        """A rule with no outside input list: its paths are what `hoglint watched` derives the
        watched files from, so it emits no finding. What it cannot tell stays incomplete."""
        self.run.required = self.required()
        self.inherit_unknowns()
        return self.run

    @cached_property
    def module_paths(self) -> list[str]:
        """Every module path, sorted: the paths inside a directory sit side by side."""
        return sorted(self.ctx.graph.modules.values())

    def expand(self, location: str) -> list[str]:
        """The files a location stands for: a directory's are the module paths inside it, else
        its `__init__.py`."""
        if not location.endswith("/"):
            return [location]
        paths = self.module_paths
        start = end = bisect.bisect_left(paths, location)
        while end < len(paths) and paths[end].startswith(location):
            end += 1
        return paths[start:end] or [location + "__init__.py"]

    @staticmethod
    def any_inside(location: str, cover: CoverSpec) -> bool:
        """Whether one covered file inside the location covers it, as for a location cover's
        directory. Any other directory needs each file inside it covered."""
        return cover.endpoint == "location" and location.endswith("/")

    def evaluate(self) -> RuleRun:
        if not self.spec.inputs:
            return self.record()
        repo = self.ctx.repo_root
        name = str(self.spec.input("adapter"))
        adapter = REGISTRY.coverage_adapters[name]
        if repo is None:
            self.everywhere("inputs", f"no repo root to read the {name} inputs from")
            return self.run
        options = {key: value for key, value in self.spec.inputs if key != "adapter"}
        try:
            handle = open_coverage(adapter, options, repo)
        except Exception as error:  # noqa: BLE001 - unreadable inputs make the rule incomplete, never clean
            self.everywhere("inputs", f"cannot read the {name} inputs: {error}")
            return self.run
        required = self.run.required = self.required()
        self.inherit_unknowns()
        for location, (cover, module) in sorted(required.items()):
            why = self.why(cover)
            paths = self.expand(location)
            if self.any_inside(location, cover):
                if any(adapter.covered(handle, path)[0] for path in paths):
                    continue
                _, decided = adapter.covered(handle, paths[0])
                resolve = self.resolver(module, None, location)
                self.emit(location, module, f"{decided} watches no file in {location}: {why}", resolve)
                continue
            for path in paths:
                covered, decided = adapter.covered(handle, path)
                if covered:
                    continue
                owner_module = module or self.ctx.labeler.path_to_module.get(path)
                resolve = self.resolver(owner_module, None, path)
                self.emit(path, owner_module, f"{decided} does not watch {path}: {why}", resolve)
        return self.run
