"""L3 labels: typed fields on modules and symbols, from globs, zones, kinds, providers and registries.

Each value keeps the source that set it, so `hoglint explain` can name the config line. Two
sources that disagree are a config error unless one says `override = true`. A field read inside
its domain with no value is unknown; outside its domain it is a known `none`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoglint.config.model import Assignment, Config, Domain, ExportSpec, FieldSpec, KindSpec, ProviderSpec
from hoglint.globs import glob_match
from hoglint.model import Edge, Graph, Resolution
from hoglint.plugins import REGISTRY, Provided
from hoglint.selectors import NONE, UNKNOWN, FieldType, Ref, State, Truth, Value, any_of

CAPTURE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def fill(template: Any, captures: dict[str, str]) -> Any:
    if not isinstance(template, str):
        return template
    return CAPTURE_RE.sub(lambda m: captures.get(m.group(1), m.group(0)), template)


@dataclass(frozen=True, slots=True)
class Label:
    value: str | bool | frozenset[str]
    source: str


@dataclass(frozen=True, slots=True)
class Candidate:
    value: Any
    source: str
    override: bool


# ── Symbol facts ──────────────────────────────────────────────────


def split_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


DECL_NAMES = {"def": "function", "async": "function"}
DATACLASS_DECORATORS = frozenset({"dataclasses.dataclass"})


class SymbolFacts:
    """Symbol facts from the graph: declarations, bases and decorators.

    Bases come from `inherit` edges (attr `class`), decorators from `decorate` edges (attr
    `decorated`). An unresolved base is kept as `?<surface>`, so ancestry knows it is incomplete.
    Producers may add string facts to Graph.symbols: `abstract`, `frozen`, `metaclass`, `members`.
    """

    def __init__(self, graph: Graph) -> None:
        self.symbols = graph.symbols
        self.base_map: dict[str, list[str]] = {}
        self.decorator_map: dict[str, list[str]] = {}
        self.frozen: set[str] = set()
        self.ancestry_cache: dict[str, tuple[frozenset[str], bool]] = {}
        self.decl_seen: dict[str, bool] = {}
        for edge in graph.edges:
            if edge.kind == "inherit" and edge.attr("class"):
                owner = f"{edge.site.module}.{edge.attr('class')}"
                self.base_map.setdefault(owner, []).extend(self.names(edge))
            elif edge.kind == "decorate" and edge.attr("decorated"):
                owner = f"{edge.site.module}.{edge.attr('decorated')}"
                self.decorator_map.setdefault(owner, []).extend([edge.surface, *self.names(edge)])
                if edge.attr("frozen") == "1":
                    self.frozen.add(owner)

    @staticmethod
    def names(edge: Edge) -> list[str]:
        if edge.resolution is Resolution.UNRESOLVED:
            return [f"?{edge.surface}"]
        if edge.targets:
            return [str(target) for target in edge.targets]
        return [edge.surface] if edge.surface else []

    def has(self, symbol: str) -> bool:
        return symbol in self.symbols

    def any_decl(self, decl: str) -> bool:
        """Whether any symbol has this declaration, so a producer that records it ran."""
        if decl not in self.decl_seen:
            self.decl_seen[decl] = any(self.decl(symbol) == decl for symbol in self.symbols)
        return self.decl_seen[decl]

    def get(self, symbol: str, key: str) -> str:
        return self.symbols.get(symbol, {}).get(key, "")

    def decl(self, symbol: str) -> str:
        facts = self.symbols.get(symbol, {})
        raw = facts.get("decl") or facts.get("kind", "")
        return DECL_NAMES.get(raw, raw)

    def bases(self, symbol: str) -> list[str]:
        return self.base_map.get(symbol, []) + split_list(self.get(symbol, "bases"))

    def decorators(self, symbol: str) -> list[str]:
        return self.decorator_map.get(symbol, []) + split_list(self.get(symbol, "decorators"))

    def is_dataclass(self, symbol: str) -> bool:
        return bool(DATACLASS_DECORATORS & set(self.decorators(symbol))) or self.get(symbol, "dataclass") == "true"

    def is_frozen(self, symbol: str) -> bool:
        return symbol in self.frozen or self.get(symbol, "frozen") == "true"

    def ancestors(self, symbol: str) -> tuple[frozenset[str], bool]:
        """Every base class reachable from `symbol`, and whether the walk saw every base."""
        cached = self.ancestry_cache.get(symbol)
        if cached is not None:
            return cached
        seen: set[str] = set()
        complete = True
        queue = list(self.bases(symbol))
        while queue:
            base = queue.pop()
            if base.startswith("?"):
                complete = False
                continue
            if base in seen:
                continue
            seen.add(base)
            queue.extend(self.bases(base))
        result = (frozenset(seen), complete)
        self.ancestry_cache[symbol] = result
        return result


# ── Providers ─────────────────────────────────────────────────────


class ProviderRunner:
    """Fills provider-owned fields per path. A provider that fails is reported, never silent."""

    def __init__(self, specs: Iterable[ProviderSpec], repo_root: Path | None) -> None:
        self.specs = list(specs)
        self.repo_root = repo_root
        self.failures: dict[str, str] = {}
        self.loaded: dict[str, Any] = {}

    def load(self, spec: ProviderSpec) -> Any:
        if spec.name in self.loaded:
            return self.loaded[spec.name]
        handle: Any = None
        try:
            handle = self.open(spec)
        except Exception as error:  # a provider must never crash the run; its fields read as unknown
            self.failures[spec.name] = f"{type(error).__name__}: {error}"
        self.loaded[spec.name] = handle
        return handle

    def open(self, spec: ProviderSpec) -> Any:
        if self.repo_root is None:
            raise FileNotFoundError("no repo root to read provider files from")
        return REGISTRY.providers[spec.use].open(dict(spec.options), self.repo_root)

    def values(self, path: str) -> dict[str, Candidate]:
        out: dict[str, Candidate] = {}
        for spec in self.specs:
            handle = self.load(spec)
            if handle is None:
                continue
            source = f"provider {spec.name} ({spec.use})"
            produced = REGISTRY.providers[spec.use].values(handle, path)
            for output, name in spec.bindings:
                if output not in produced:
                    continue
                value = produced[output]
                detail = ""
                if isinstance(value, Provided):
                    value, detail = value.value, value.detail
                out[name] = Candidate(value, f"{source}: {detail}" if detail else source, False)
        return out

    def failed_fields(self) -> set[str]:
        return {name for spec in self.specs if spec.name in self.failures for name in spec.sets}


# ── Labeler ───────────────────────────────────────────────────────


@dataclass(frozen=False, slots=True)
class LabelSet:
    fields: dict[str, Label] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KindMatch:
    kinds: tuple[str, ...]
    unknown: bool


class Labeler:
    """Computes labels on demand and caches them. `conflicts` holds config errors found while labelling."""

    def __init__(self, config: Config, graph: Graph, repo_root: Path | None = None) -> None:
        self.config = config
        self.graph = graph
        self.facts = SymbolFacts(graph)
        self.providers = ProviderRunner(config.providers.values(), repo_root)
        self.provider_fields = {name for spec in config.providers.values() for name in spec.sets}
        self.module_cache: dict[str, LabelSet] = {}
        self.symbol_cache: dict[str, LabelSet] = {}
        self.kind_cache: dict[str, KindMatch] = {}
        self.value_cache: dict[tuple[str | None, str | None, str], tuple[Value, str]] = {}
        self.conflicts: list[str] = []
        self.path_to_module = {path: module for module, path in graph.modules.items()}
        self.module_assigns = [a for a in config.assigns if a.symbols is None]
        self.symbol_assigns = [a for a in config.assigns if a.symbols is not None]
        self.zone_index = config.zones.by_name()

    # ── modules and paths ──

    def module_of_symbol(self, symbol: str) -> str | None:
        if symbol in self.graph.modules:
            return symbol
        head = symbol
        while "." in head:
            head = head.rpartition(".")[0]
            if head in self.graph.modules:
                return head
        return None

    def path_of(self, module: str) -> str | None:
        return self.graph.modules.get(module)

    def assignment_candidates(self, assignment: Assignment, path: str) -> dict[str, Candidate]:
        for excluded in assignment.except_paths:
            if glob_match(excluded, path) is not None:
                return {}
        for pattern in assignment.match:
            captures = glob_match(pattern, path)
            if captures is None:
                continue
            if any(captures.get(name) in values for name, values in assignment.except_values):
                continue
            if any(name in captures and captures[name] not in values for name, values in assignment.only_values):
                continue
            return {
                name: Candidate(fill(value, captures), assignment.label(), assignment.override)
                for name, value in assignment.sets
            }
        return {}

    def zone_candidate(self, path: str) -> Candidate | None:
        table = self.config.zones
        for within in table.within:
            root = glob_match(within + "/**", path)
            if root is None:
                continue
            prefix = fill(within, root) + "/"
            relative = path[len(prefix) :]
            matched = [zone for zone in table.zones if any(glob_match(p, relative) is not None for p in zone.paths)]
            ancestors = {a for zone in matched for a in table.chain(zone.name)[1:]}
            leaves = [zone for zone in matched if zone.name not in ancestors]
            if not leaves:
                return Candidate(table.default, f"default private zone under {within}", False)
            if len(leaves) > 1:
                overriding = [zone for zone in leaves if zone.override]
                if len(overriding) != 1:
                    names = ", ".join(zone.name for zone in leaves)
                    self.conflicts.append(f"{path}: zones {names} all match; mark one `override = true`")
                    return None
                leaves = overriding
            return Candidate(leaves[0].name, f"zones.{leaves[0].name} ({leaves[0].source})", False)
        return None

    def resolve_candidates(self, entity: str, name: str, candidates: list[Candidate]) -> Label | None:
        spec = self.config.fields.get(name)
        if spec is not None and spec.type is FieldType.SET:
            values: set[str] = set()
            for candidate in candidates:
                values |= (
                    set(candidate.value)
                    if isinstance(candidate.value, (frozenset, set, list, tuple))
                    else {str(candidate.value)}
                )
            return Label(frozenset(values), "; ".join(c.source for c in candidates))
        overriding = [c for c in candidates if c.override]
        pool = overriding or candidates
        distinct = {repr(c.value) for c in pool}
        if len(distinct) > 1:
            sources = "; ".join(f"{c.value!r} from {c.source}" for c in pool)
            self.conflicts.append(f"{entity}: field {name!r} set differently by {sources}")
            return None
        return Label(pool[0].value, pool[0].source)

    def module_labels(self, module: str) -> LabelSet:
        cached = self.module_cache.get(module)
        if cached is not None:
            return cached
        labels = LabelSet()
        path = self.path_of(module)
        if path is not None:
            labels = self.label_path(module, path)
        self.module_cache[module] = labels
        return labels

    def label_path(self, entity: str, path: str) -> LabelSet:
        collected: dict[str, list[Candidate]] = {}
        for assignment in self.module_assigns:
            for name, candidate in self.assignment_candidates(assignment, path).items():
                collected.setdefault(name, []).append(candidate)
        zone_fields = [spec.name for spec in self.config.fields.values() if spec.type is FieldType.ZONE]
        zone = self.zone_candidate(path) if zone_fields else None
        if zone is not None:
            for name in zone_fields:
                collected.setdefault(name, []).append(zone)
        for name, candidate in self.providers.values(path).items():
            collected.setdefault(name, []).append(candidate)
        labels = LabelSet()
        for name, candidates in collected.items():
            label = self.resolve_candidates(entity, name, candidates)
            if label is not None:
                labels.fields[name] = label
        registry = self.registry_of(path, entity)
        if registry:
            labels.fields["registry"] = Label(registry, "registries")
        return labels

    def registry_of(self, path: str, module: str) -> str:
        for spec in self.config.registries.values():
            if glob_match(spec.module, path) is not None:
                return spec.name
            if spec.loader.rpartition(".")[0] == module:
                return spec.name
        return ""

    # ── symbols ──

    def symbol_labels(self, symbol: str) -> LabelSet:
        if not self.symbol_assigns:
            return LabelSet()
        cached = self.symbol_cache.get(symbol)
        if cached is not None:
            return cached
        collected: dict[str, list[Candidate]] = {}
        module = self.module_of_symbol(symbol)
        path = self.path_of(module) if module else None
        for assignment in self.symbol_assigns:
            if not self.symbol_matches(assignment, symbol, path):
                continue
            for name, value in assignment.sets:
                collected.setdefault(name, []).append(Candidate(value, assignment.label(), assignment.override))
        labels = LabelSet()
        for name, candidates in collected.items():
            label = self.resolve_candidates(symbol, name, candidates)
            if label is not None:
                labels.fields[name] = label
        self.symbol_cache[symbol] = labels
        return labels

    def symbol_matches(self, assignment: Assignment, symbol: str, path: str | None) -> bool:
        match = assignment.symbols
        if match is None:
            return False
        if assignment.match and (path is None or not any(glob_match(p, path) is not None for p in assignment.match)):
            return False
        if match.decorated_by and not set(match.decorated_by) & set(self.facts.decorators(symbol)):
            return False
        if match.subclass_of:
            ancestors, _ = self.facts.ancestors(symbol)
            if not set(match.subclass_of) & ancestors:
                return False
        return True

    def kind_matches(self, spec: KindSpec, symbol: str) -> Truth:
        if spec.subclass_of and symbol in spec.subclass_of:
            return Truth.TRUE
        if not self.facts.has(symbol):
            return Truth.UNKNOWN
        if spec.abstract is False and self.facts.get(symbol, "abstract") == "true":
            return Truth.FALSE
        if spec.decl and self.facts.decl(symbol) != spec.decl:
            return Truth.FALSE
        if spec.decorated_by and not set(spec.decorated_by) & set(self.facts.decorators(symbol)):
            return Truth.FALSE
        if spec.subclass_of:
            ancestors, complete = self.facts.ancestors(symbol)
            if set(spec.subclass_of) & ancestors:
                return Truth.TRUE
            return Truth.FALSE if complete else Truth.UNKNOWN
        return Truth.TRUE

    def kind_match(self, symbol: str) -> KindMatch:
        """Which [kinds] the symbol matches, and whether any could not be decided. Cached per symbol."""
        cached = self.kind_cache.get(symbol)
        if cached is not None:
            return cached
        matched: list[str] = []
        unknown = False
        for spec in self.config.kinds.values():
            truth = self.kind_matches(spec, symbol)
            if truth is Truth.TRUE:
                matched.append(spec.name)
            elif truth is Truth.UNKNOWN:
                unknown = True
        if len(matched) > 1:
            self.conflicts.append(f"{symbol}: matches kinds {', '.join(matched)}")
        result = KindMatch(tuple(matched), unknown)
        self.kind_cache[symbol] = result
        return result

    def kind(self, symbol: str, first_party: bool = True) -> tuple[Value, str]:
        """The symbol's kind from [kinds]. A first-party symbol without facts is unknown."""
        match = self.kind_match(symbol)
        if len(match.kinds) > 1:
            return UNKNOWN, "conflicting kinds"
        if match.kinds:
            return Value.known(match.kinds[0]), f"kinds.{match.kinds[0]}"
        if match.unknown and (first_party or self.facts.has(symbol)):
            return UNKNOWN, "no facts for this symbol, or a base did not resolve"
        return NONE, "no kind matches"

    # ── values ──

    def in_domain(self, domain: Domain | None, path: str | None, entity_resolver: Any) -> bool:
        if domain is None:
            return False
        if domain.everywhere:
            return True
        if domain.selector is not None:
            return domain.selector.evaluate(entity_resolver) is Truth.TRUE
        return path is not None and any(glob_match(glob, path) is not None for glob in domain.globs)

    def to_value(self, spec: FieldSpec, label: Label) -> Value:
        if spec.type is FieldType.ZONE:
            return Value.known(str(label.value), self.config.zones.chain(str(label.value)))
        if spec.type is FieldType.SET and isinstance(label.value, frozenset):
            return Value.known(label.value)
        if isinstance(label.value, bool):
            return Value.known(label.value)
        return Value.known(str(label.value))

    def field_value(self, module: str | None, symbol: str | None, name: str) -> tuple[Value, str]:
        """A declared field on a module (and the symbol inside it, for symbol fields)."""
        key = (module, symbol if self.config.fields[name].on == "symbol" else None, name)
        cached = self.value_cache.get(key)
        if cached is None:
            cached = self.compute_value(module, symbol, name)
            self.value_cache[key] = cached
        return cached

    def compute_value(self, module: str | None, symbol: str | None, name: str) -> tuple[Value, str]:
        spec = self.config.fields[name]
        if spec.on == "symbol":
            if symbol is None:
                return NONE, "not a symbol"
            label = self.symbol_labels(symbol).fields.get(name)
            if label is not None:
                return self.to_value(spec, label), label.source
            fact = self.facts.get(symbol, name)
            if fact:
                return Value.known(fact), "producer fact"
        else:
            labels = self.module_labels(module) if module else LabelSet()
            label = labels.fields.get(name)
            if label is not None:
                return self.to_value(spec, label), label.source
        if name in self.providers.failed_fields():
            return UNKNOWN, "provider failed"
        path = self.path_of(module) if module else None
        resolver = self.entity_resolver(module, symbol, domain_check=False)
        if self.in_domain(spec.domain, path, resolver):
            return UNKNOWN, "inside the field's domain, but nothing sets it"
        return NONE, "outside the field's domain"

    def entity_resolver(self, module: str | None, symbol: str | None, domain_check: bool = True) -> Any:
        """A resolver for bare entity selectors (layout, levels, domains)."""

        def resolve(ref: Ref) -> Value:
            name = ref.path[-1]
            if name in ("module", "subject"):
                return Value.known(module) if module else NONE
            if name == "symbol":
                return Value.known(symbol) if symbol else NONE
            if name == "path":
                path = self.path_of(module) if module else None
                return Value.known(path) if path else NONE
            if name == "name":
                target = symbol or module
                return Value.known(target.rpartition(".")[2]) if target else NONE
            if name == "decl":
                return Value.known(self.facts.decl(symbol)) if symbol and self.facts.decl(symbol) else NONE
            if name == "kind":
                return self.kind(symbol)[0] if symbol else NONE
            if name == "registry":
                label = self.module_labels(module).fields.get("registry") if module else None
                return Value.known(str(label.value)) if label else NONE
            if name not in self.config.fields:
                return UNKNOWN
            if not domain_check:
                return self.label_only(module, symbol, name)
            return self.field_value(module, symbol, name)[0]

        return resolve

    def label_only(self, module: str | None, symbol: str | None, name: str) -> Value:
        spec = self.config.fields[name]
        holder = (
            self.symbol_labels(symbol)
            if spec.on == "symbol" and symbol
            else self.module_labels(module)
            if module
            else LabelSet()
        )
        label = holder.fields.get(name)
        return self.to_value(spec, label) if label is not None else NONE

    # ── exports ──

    def export_root(self, spec: ExportSpec, path: str) -> str | None:
        if not spec.within:
            return path
        root = glob_match(spec.within + "/**", path)
        if root is None:
            return None
        prefix = fill(spec.within, root) + "/"
        return path[len(prefix) :]

    def export_covers(self, spec: ExportSpec, path: str) -> bool:
        """Whether the path lies inside the export's `within` and `paths`."""
        relative = self.export_root(spec, path)
        return relative is not None and any(glob_match(pattern, relative) is not None for pattern in spec.paths)

    def export_member(self, spec: ExportSpec, path: str, symbol: str | None) -> Truth:
        """Whether a module (symbol None) or a symbol at `path` belongs to the export.

        With `subclass_of` only symbols count: a listed base itself, or a class that inherits one.
        """
        if not self.export_covers(spec, path):
            return Truth.FALSE
        if not spec.subclass_of:
            return Truth.TRUE
        if symbol is None:
            return Truth.FALSE
        ancestors, complete = self.facts.ancestors(symbol)
        if set(spec.subclass_of) & (ancestors | {symbol}):
            return Truth.TRUE
        return Truth.FALSE if complete else Truth.UNKNOWN

    def exported(self, module: str | None, symbol: str | None, audience_resolver: Any) -> Truth:
        """Is this module or symbol in an export whose audience includes the caller?"""
        path = self.path_of(module) if module else None
        if path is None:
            return Truth.UNKNOWN
        results: list[Truth] = []
        for spec in self.config.exports.values():
            truth = self.export_member(spec, path, symbol)
            if truth is Truth.FALSE:
                continue
            if truth is Truth.TRUE and spec.audience is not None:
                truth = spec.audience.evaluate(audience_resolver)
            results.append(truth)
        return any_of(results)

    # ── whole-repo pass ──

    def label_everything(self) -> list[str]:
        """Labels every module and symbol once, so conflicts surface before rules run."""
        for module in self.graph.modules:
            self.module_labels(module)
        for symbol in self.graph.symbols:
            self.symbol_labels(symbol)
            if self.config.kinds:
                self.kind(symbol)
        return self.conflicts

    def explain(self, module: str) -> list[tuple[str, str, str]]:
        """(field, value, source) for every declared field of a module, unassigned ones included."""
        rows: list[tuple[str, str, str]] = []
        for name, spec in self.config.fields.items():
            if spec.on == "symbol":
                continue
            value, source = self.field_value(module, None, name)
            rows.append((name, render(value), source))
        registry = self.module_labels(module).fields.get("registry")
        if registry is not None:
            rows.append(("registry", str(registry.value), registry.source))
        return rows


def render(value: Value) -> str:
    if value.state is State.UNKNOWN:
        return "unknown"
    if value.state is State.NONE:
        return "none"
    if isinstance(value.data, frozenset):
        return ", ".join(sorted(value.data)) or "{}"
    if value.chain and len(value.chain) > 1:
        return f"{value.data}  (inside {', '.join(value.chain[1:])})"
    return str(value.data)
