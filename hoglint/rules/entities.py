"""Primitives over entities and outside inputs: layout, inventory, coverage."""

from __future__ import annotations

from collections.abc import Iterator

from hoglint.config.loader import read_table
from hoglint.config.model import CoverSpec, RegistrySpec, RuleSpec
from hoglint.globs import fill, glob_match
from hoglint.model import Edge
from hoglint.plugins import REGISTRY
from hoglint.rules.base import (
    EdgeEnv,
    EdgeRule,
    IncompleteItem,
    RuleContext,
    RuleRun,
    StagedSelection,
    conjuncts,
    contexts_of,
    joined,
    project,
)
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


class EntityRule:
    """Shared emit and selection for rules whose subject is a module, symbol or path."""

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

    def entities(self) -> Iterator[tuple[str | None, str | None, str]]:
        """(module, symbol, subject) for every entity the rule looks at. A `select` that pins
        `kind` is false for a symbol whose kind is known to be another, so on symbols only the
        pinned kinds' members and the symbols of unknown kind are read."""
        labeler = self.ctx.labeler
        if self.spec.on == "symbol":
            wanted = self.spec.select.restricts(("kind",)) if self.spec.select is not None else None
            symbols = labeler.index.sorted_symbols if wanted is None else labeler.kind_candidates(wanted)
            for symbol in symbols:
                yield labeler.module_of_symbol(symbol), symbol, symbol
            return
        for module, _ in labeler.index.sorted_modules:
            yield module, None, module


class LayoutPrimitive(EntityRule):
    """Required files and zones exist, moved files are gone, selected entities meet `require`."""

    def evaluate(self) -> RuleRun:
        if self.spec.select is not None or self.spec.require is not None:
            self.check_entities()
        if self.spec.within and (self.spec.files or self.spec.absent):
            self.check_files()
        for name in self.spec.registry:
            self.check_registry(self.ctx.config.registries[name])
        return self.run

    def check_entities(self) -> None:
        for module, symbol, subject in self.entities():
            resolve = self.resolver(module, symbol, subject)
            selected = self.selection.truth(resolve) if self.selection is not None else Truth.TRUE
            if selected is Truth.FALSE:
                continue
            if selected is Truth.UNKNOWN:
                self.incomplete(subject, module, "cannot tell whether this entity is selected")
                continue
            verdict = self.spec.require.evaluate(resolve) if self.spec.require is not None else Truth.FALSE
            if verdict is Truth.TRUE:
                continue
            suffix = f" does not meet `{self.spec.require.source}`" if self.spec.require is not None else ""
            self.emit(subject, module, f"{subject}{suffix}", resolve)

    def roots(self) -> list[str]:
        roots: set[str] = set()
        for path in self.ctx.graph.modules.values():
            captures = glob_match(self.spec.within + "/**", path)
            if captures is not None:
                roots.add(fill(self.spec.within, captures))
        return sorted(roots)

    def check_files(self) -> None:
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
                if not (repo / root / required).exists():
                    self.emit(
                        f"{root}/{required}",
                        module,
                        f"{root} lacks {required}",
                        self.resolver(module, None, f"{root}/{required}"),
                    )
            for forbidden in self.spec.absent:
                if repo is not None and (repo / root / forbidden).exists():
                    self.emit(
                        f"{root}/{forbidden}",
                        module,
                        f"{root}/{forbidden} must be gone",
                        self.resolver(module, None, f"{root}/{forbidden}"),
                    )

    def check_registry(self, registry: RegistrySpec) -> None:
        facts = self.ctx.labeler.facts
        base = registry.shape.removeprefix("abc:")
        for module, path in self.ctx.labeler.index.sorted_modules:
            if glob_match(registry.module, path) is None:
                continue
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

    def evaluate(self) -> RuleRun:
        name = self.spec.exactly_one
        for module, symbol, subject in self.entities():
            resolve = self.resolver(module, symbol, subject)
            if self.selection is not None:
                selected = self.selection.truth(resolve)
                if selected is Truth.UNKNOWN:
                    self.incomplete(subject, module, "cannot tell whether this entity is selected")
                if selected is not Truth.TRUE:
                    continue
            value = resolve(Ref((name,)))
            if value.state is State.UNKNOWN:
                self.incomplete(subject, module, f"{name} is unknown")
                continue
            count = len(value.data) if value.state is State.KNOWN and isinstance(value.data, frozenset) else 0
            if count != 1:
                self.emit(subject, module, f"{subject} has {count} {name} values, needs exactly one", resolve)
        return self.run


class EdgeInventory(EdgeRule):
    """`classify`: every selected edge carries a classification, and every classification still matches one."""

    def classifications(self) -> set[tuple[str, ...]]:
        repo = self.ctx.repo_root
        if repo is None or not (repo / self.spec.classify).is_file():
            return set()
        data = read_table(repo / self.spec.classify)
        return {tuple(str(v) for v in entry.get("identity", ())) for entry in data.get("entry", [])}

    def evaluate(self) -> RuleRun:
        seen: dict[tuple[str, ...], EdgeEnv] = {}
        for env in self.envs():
            selected = self.selection(env)
            if selected is Truth.FALSE:
                continue
            if env.dst.unresolved:
                self.on_unresolved(env)
                continue
            if selected is Truth.UNKNOWN:
                self.incomplete(env, "selection reads an unknown value")
                continue
            seen.setdefault(project(self.spec.identity, env.resolve), env)
        classified = self.classifications()
        for identity in sorted(seen):
            if identity not in classified:
                self.emit(seen[identity], f"{' '.join(identity)} has no classification in {self.spec.classify}")
        for identity in sorted(classified - set(seen)):
            message = f"classification {' '.join(identity)} matches nothing (stale)"
            self.run.findings.append(self.ctx.finding(self.spec, identity, message, None, ledgerable=False))
        return self.run


class CoveragePrimitive(EntityRule):
    """Does an outside input list (a coverage adapter, such as turbo task inputs) cover every path the rule names?"""

    def required(self) -> dict[str, tuple[str, str | None]]:
        """path -> (why, module) for every path that must be covered."""
        out: dict[str, tuple[str, str | None]] = {}
        for cover in self.spec.covers:
            if cover.rule:
                self.from_rule(cover.rule, cover.column, out)
            else:
                self.from_edges(cover, out)
        return out

    def from_rule(self, rule_id: str, column: str, out: dict[str, tuple[str, str | None]]) -> None:
        run = self.ctx.results.get(rule_id)
        if run is None:
            self.incomplete(f"rules.{rule_id}", None, f"rule {rule_id} did not run before this coverage rule")
            return
        ref = Ref(tuple(column.split("."))) if "." in column else Ref(("edge", column))
        for finding in run.findings:
            for edge in finding.witnesses:
                src = self.ctx.src_endpoint(edge.site)
                env = EdgeEnv(self.ctx, edge, src, self.ctx.dst_endpoints(edge, "origin")[0])
                value = env.resolve(ref)
                if value.state is State.KNOWN:
                    out.setdefault(render_value(value), (f"rules.{rule_id}.{column}", None))

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
        kinds = selector.restricts(("edge", "kind")) if selector is not None else None
        parts = conjuncts((selector,))
        by_edge = joined([part for part in parts if self.reads_kind_and_context(part)])
        rest = joined([part for part in parts if not self.reads_kind_and_context(part)])
        staged = StagedSelection(
            self.ctx, (Selector(selector.source, rest),) if selector is not None and rest is not None else ()
        )
        truths: dict[tuple[str, frozenset[str]], Truth] = {}
        for edge in self.ctx.edges_of(tuple(sorted(kinds or ()))):
            edge_truth = self.kind_and_context_truth(by_edge, edge, truths) if by_edge is not None else Truth.TRUE
            if edge_truth is Truth.FALSE:
                continue
            src = self.ctx.src_endpoint(edge.site)
            for dst in self.ctx.dst_endpoints(edge, "origin"):
                truth = all_of([edge_truth, staged.truth(EdgeEnv(self.ctx, edge, src, dst))])
                if truth is Truth.FALSE:
                    continue
                endpoint = src if cover.endpoint == "src" else dst
                if endpoint.unresolved or truth is Truth.UNKNOWN:
                    self.incomplete(
                        f"{src.module}|{edge.surface}", src.module, f"cannot place {edge.surface!r} for coverage"
                    )
                    continue
                if endpoint.path and endpoint.external is None:
                    out.setdefault(endpoint.path, (cover.text, endpoint.module))

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
            self.incomplete("inputs", None, f"no repo root to read the {name} inputs from")
            return self.run
        options = {key: value for key, value in self.spec.inputs if key != "adapter"}
        try:
            handle = adapter.open(options, repo)
        except Exception as error:  # noqa: BLE001 - unreadable inputs make the rule incomplete, never clean
            self.incomplete("inputs", None, f"cannot read the {name} inputs: {error}")
            return self.run
        for location, (why, module) in sorted(self.required().items()):
            for path in self.expand(location):
                covered, decided = adapter.covered(handle, path)
                if covered:
                    continue
                owner_module = module or self.ctx.labeler.path_to_module.get(path)
                resolve = self.resolver(owner_module, None, path)
                self.emit(path, owner_module, f"{path} is needed by {why} but {decided} does not watch it", resolve)
        return self.run
