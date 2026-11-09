"""The export primitive: does a public door hand out a denied shape?

The check is recursive. An exported class must have an allowed shape, and the types in its
fields, its bases' fields and its methods' signatures must too. An exported function's
signature types are checked the same way. Re-exports are judged at their origin, so a shim
module hides nothing. Shape names are core recognizers plus the config's [kinds].

Membership is the labeler's: an export's paths, narrowed by `subclass_of`. The audience is not
read here: it limits who may use an export, not what the export hands out, so every member is
checked whatever its audience.
"""

from __future__ import annotations

from hoglint.config.model import ExportSpec, RuleSpec, ShapePolicy
from hoglint.model import Edge, Finding, Resolution
from hoglint.rules.base import MODULE_SCOPE, IncompleteItem, RuleContext, RuleRun, contexts_of, project
from hoglint.selectors import NONE, Ref, Resolver, State, Truth, Value

ABC_BASES = frozenset({"abc.ABC"})
ABC_METAS = frozenset({"abc.ABCMeta"})
ENUM_BASES = frozenset({"enum.Enum", "enum.StrEnum", "enum.IntEnum", "enum.Flag", "enum.IntFlag"})
PROTOCOL_BASES = frozenset({"typing.Protocol", "typing_extensions.Protocol"})


class ExportPrimitive:
    def __init__(self, ctx: RuleContext, spec: RuleSpec) -> None:
        self.ctx = ctx
        self.spec = spec
        self.run = RuleRun(spec)
        self.facts = ctx.labeler.facts
        self.exposes: dict[str, list[tuple[str, Edge]]] = {}
        for edge in ctx.edges_of(("exposes",)):
            callable_name = edge.attr("callable") or edge.site.scope
            self.exposes.setdefault(edge.site.module, []).append((callable_name, edge))
        self.reported: set[tuple[str, str, str]] = set()
        self.visited: set[tuple[str, str, str]] = set()
        self.top_level: dict[str, list[str]] = {}
        for symbol in sorted(ctx.graph.symbols):
            module = ctx.labeler.module_of_symbol(symbol)
            name = symbol[len(module) + 1 :] if module else ""
            if module and name and "." not in name and not name.startswith("_"):
                self.top_level.setdefault(module, []).append(symbol)
        self.module_imports: dict[str, list[Edge]] = {}
        for edge in ctx.edges_of(("import",)):
            if edge.site.scope in (MODULE_SCOPE, ""):
                self.module_imports.setdefault(edge.site.module, []).append(edge)

    def selected_exports(self) -> list[ExportSpec]:
        names = self.spec.exports or tuple(self.ctx.config.exports)
        return [self.ctx.config.exports[name] for name in names]

    def evaluate(self) -> RuleRun:
        labeler = self.ctx.labeler
        for export in self.selected_exports():
            policy = self.ctx.config.shapes[export.shape]
            for module, path in sorted(self.ctx.graph.modules.items()):
                if not labeler.export_covers(export, path):
                    continue
                for symbol, edge in self.exported_symbols(module):
                    member = labeler.export_member(export, path, symbol)
                    if member is Truth.FALSE:
                        continue
                    if member is Truth.UNKNOWN:
                        why = f"cannot tell whether it is in export {export.name}: a base did not resolve"
                        self.unknown(symbol, module, policy, (symbol,), edge, why)
                        continue
                    self.check(symbol, module, policy, edge, (symbol,))
        return self.run

    def exported_symbols(self, module: str) -> list[tuple[str, Edge | None]]:
        out: list[tuple[str, Edge | None]] = [(symbol, None) for symbol in self.top_level.get(module, [])]
        for edge in self.module_imports.get(module, []):
            if contexts_of(edge) & {"type_only", "test"}:
                continue
            if edge.resolution is Resolution.UNRESOLVED or not edge.targets:
                self.unresolved(edge, module)
                continue
            for target in edge.targets:
                if target.symbol:
                    out.append((str(target), edge))
        return out

    # ── shapes ──

    def recognize(self, symbol: str) -> tuple[set[str], bool]:
        """Shape names of a symbol, and whether hoglint saw enough facts to be sure."""
        names: set[str] = set()
        kind, _ = self.ctx.labeler.kind(symbol, first_party=self.facts.has(symbol))
        if kind.state is State.KNOWN:
            names.add(str(kind.data))
        if not self.facts.has(symbol):
            return names, kind.state is not State.UNKNOWN
        ancestors, complete = self.facts.ancestors(symbol)
        lineage = ancestors | {symbol}
        decl = self.facts.decl(symbol)
        if decl == "function":
            names.add("function")
        if decl == "assignment":
            names.add("constant")
        if decl == "class":
            names.add("class")
        if self.facts.is_dataclass(symbol):
            names.add("dataclass")
            if self.facts.is_frozen(symbol):
                names.add("frozen-dataclass")
        if lineage & ABC_BASES or self.facts.get(symbol, "metaclass") in ABC_METAS:
            names.add("abc")
        if lineage & ENUM_BASES:
            names.add("enum")
        if lineage & PROTOCOL_BASES:
            names.add("protocol")
        return names, complete and kind.state is not State.UNKNOWN

    def behavior_ok(self, symbol: str, policy: ShapePolicy) -> bool:
        behavior = policy.behavior
        if behavior is None:
            return False
        ancestors, _ = self.facts.ancestors(symbol)
        for candidate in sorted(ancestors | {symbol}):
            shapes, _ = self.recognize(candidate)
            if behavior.kind not in shapes:
                continue
            if behavior.where is None:
                return True
            module = self.ctx.labeler.module_of_symbol(candidate)
            if behavior.where.evaluate(self.ctx.labeler.entity_resolver(module, candidate)) is Truth.TRUE:
                return True
        return False

    # ── recursion ──

    def check(self, symbol: str, module: str, policy: ShapePolicy, edge: Edge | None, trail: tuple[str, ...]) -> None:
        """Findings are keyed by (symbol, module), so a symbol already checked for this module and
        policy adds nothing the second time."""
        visit = (module, policy.name, symbol)
        if visit in self.visited:
            return
        self.visited.add(visit)
        shapes, complete = self.recognize(symbol)
        denied = sorted(shapes & set(policy.deny))
        if denied:
            self.finding(symbol, module, f"export-denied({denied[0]})", trail, edge)
            return
        if symbol in self.spec.unknown_types:
            self.unknown(symbol, module, policy, trail, edge, "a type hoglint reads as unknown")
            return
        if not self.facts.has(symbol):
            if not complete:
                self.unknown(symbol, module, policy, trail, edge, "no facts for this symbol")
            return
        if "class" in shapes:
            allowed = bool(shapes & {*policy.data, *policy.allow}) or self.behavior_ok(symbol, policy)
            if not allowed:
                if not complete:
                    self.unknown(symbol, module, policy, trail, edge, "its bases did not all resolve")
                    return
                self.finding(symbol, module, "export-shape(class)", trail, edge)
                return
            if shapes & {"dataclass", "frozen-dataclass"}:
                self.check_members(symbol, module, policy, trail)
            return
        if "function" in shapes:
            self.check_members(symbol, module, policy, trail)

    def member_edges(self, symbol: str, include_bases: bool) -> list[Edge]:
        """Exposes edges of a callable, or of a class's fields and methods (and its bases')."""
        lineage = [symbol]
        if include_bases:
            ancestors, _ = self.facts.ancestors(symbol)
            lineage.extend(sorted(ancestors))
        out: list[Edge] = []
        for target in lineage:
            owner = self.ctx.labeler.module_of_symbol(target)
            if owner is None or owner == target:
                continue
            qualname = target[len(owner) + 1 :]
            for callable_name, edge in self.exposes.get(owner, []):
                if callable_name == qualname or callable_name.startswith(qualname + "."):
                    out.append(edge)
        return out

    def check_members(self, symbol: str, module: str, policy: ShapePolicy, trail: tuple[str, ...]) -> None:
        for edge in self.member_edges(symbol, include_bases=self.facts.decl(symbol) == "class"):
            if edge.attr("annotation") == "missing":
                self.unknown(symbol, module, policy, trail, edge, f"{edge.attr('slot') or 'a slot'} has no annotation")
                continue
            if edge.resolution is Resolution.UNRESOLVED:
                self.unresolved(edge, module)
                continue
            names = [str(target) for target in edge.targets] or [edge.surface]
            for name in names:
                if name and name not in trail:
                    self.check(name, module, policy, edge, (*trail, name))

    # ── output ──

    def resolver(self, symbol: str, module: str, label: str) -> Resolver:
        labeler = self.ctx.labeler

        def resolve(ref: Ref) -> Value:
            root, name = (ref.path[0], ref.path[-1]) if len(ref.path) == 2 else ("", ref.path[-1])
            if root == "edge":
                return Value.known(label if name == "label" else "export") if name in ("label", "kind") else NONE
            if root == "dst":
                if name == "symbol":
                    return Value.known(symbol)
                return labeler.entity_resolver(labeler.module_of_symbol(symbol), symbol)(Ref((name,)))
            if name in ("module", "symbol", "subject"):
                return Value.known(module)
            return labeler.entity_resolver(module, None)(Ref((name,)))

        return resolve

    def finding(
        self,
        symbol: str,
        module: str,
        label: str,
        trail: tuple[str, ...],
        edge: Edge | None,
        ledgerable: bool = True,
        unknown: bool = False,
    ) -> None:
        key = (symbol, module, label)
        if key in self.reported:
            return
        self.reported.add(key)
        path = " -> ".join(trail)
        self.run.findings.append(
            Finding(
                rule=self.spec.id,
                identity=project(self.spec.identity, self.resolver(symbol, module, label)),
                message=f"{module} exports {path}: {label}",
                site=edge.site if edge is not None else None,
                witnesses=(edge,) if edge is not None else (),
                owners=self.ctx.owners_of(module),
                subjects=(module, self.ctx.labeler.module_of_symbol(symbol) or symbol),
                ledgerable=ledgerable and self.spec.ledger,
                unknown=unknown,
            )
        )

    def unknown(
        self, symbol: str, module: str, policy: ShapePolicy, trail: tuple[str, ...], edge: Edge | None, why: str
    ) -> None:
        mode = "incomplete" if policy.unknown == "incomplete" else self.spec.on_unknown
        if mode == "incomplete":
            self.run.incomplete.append(
                IncompleteItem(self.spec.id, f"{module}|{symbol}", f"{' -> '.join(trail)}: {why}", (module,))
            )
            return
        self.finding(symbol, module, "export-unknown", trail, edge, ledgerable=mode == "finding", unknown=True)

    def unresolved(self, edge: Edge, module: str) -> None:
        if self.spec.on_unresolved == "incomplete":
            self.run.incomplete.append(
                IncompleteItem(self.spec.id, f"{module}|{edge.surface}", f"{edge.surface!r} did not resolve", (module,))
            )
            return
        ledgerable = self.spec.on_unresolved == "finding"
        self.finding(edge.surface, module, f"unresolved-{edge.kind}", (edge.surface,), edge, ledgerable, unknown=True)
