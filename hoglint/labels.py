"""L3 labels: typed fields on modules and symbols, from globs, zones, kinds, providers and registries.

Each value keeps the source that set it, so `hoglint explain` can name the config line. Two
sources that disagree are a config error unless one says `override = true`. A field read inside
its domain with no value is unknown; outside its domain it is a known `none`.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any

from hoglint.config.model import (
    Assignment,
    Config,
    Domain,
    ExportSpec,
    FieldSpec,
    KindSpec,
    LevelSpec,
    ProviderSpec,
    RegistrySpec,
)
from hoglint.config.schema import ENTITY_FIELDS, canonical
from hoglint.globs import CAPTURE_RE, GlobIndex, compile_label_glob, fill, glob_match
from hoglint.index import GraphIndex, Memo, index_of
from hoglint.model import Context, Edge, Graph, Resolution
from hoglint.plugins import PLUGIN_ROOTS, REGISTRY, Provided
from hoglint.selectors import NONE, UNKNOWN, FieldType, Ref, Resolver, State, Truth, Value, any_of, members, refs

REGISTRY_FIELD = "registry"
HANDED_ZONES = "handed_zones"
# Entity attributes the labeler answers itself; every other name is a declared field.
BUILTIN_ENTITY_NAMES = frozenset({*ENTITY_FIELDS, "kind", "subject"})
MODULE_LEVEL_BUILTINS = frozenset({"module", "path", "external", "registry", "lifecycle", HANDED_ZONES})
# A type-only or test import hands nothing out (the export primitive's default exclude).
NOT_HANDED_CONTEXTS = frozenset({Context.TYPE_ONLY, Context.TEST})


@dataclass(frozen=True, slots=True)
class Label:
    value: str | bool | frozenset[str]
    source: str


@dataclass(frozen=True, slots=True)
class Candidate:
    value: Any
    source: str
    override: bool


@dataclass(frozen=True, slots=True)
class PathCandidates:
    """What assignments and zones say about one path under one policy, and the zone conflicts."""

    fields: dict[str, list[Candidate]]
    conflicts: tuple[str, ...]


class PathAssignment:
    """A module assignment, compiled once: its globs, its source text, and the candidates of its
    fixed values. A value with `{name}` templates is filled per match."""

    def __init__(self, assignment: Assignment) -> None:
        self.assignment = assignment
        self.match = [compile_label_glob(pattern) for pattern in assignment.match]
        self.excluded = [compile_label_glob(pattern) for pattern in assignment.except_paths]
        self.source = assignment.label()
        self.fixed = {
            name: Candidate(value, self.source, assignment.override)
            for name, value in assignment.sets
            if not (isinstance(value, str) and CAPTURE_RE.search(value))
        }

    def filled(self, captures: dict[str, str]) -> dict[str, Candidate]:
        out: dict[str, Candidate] = {}
        for name, value in self.assignment.sets:
            fixed = self.fixed.get(name)
            out[name] = (
                fixed if fixed is not None else Candidate(fill(value, captures), self.source, self.assignment.override)
            )
        return out

    def candidates(self, path: str) -> dict[str, Candidate]:
        """What the assignment says about the path: nothing unless a `match` glob matches and the
        captures pass `except` and `only`."""
        assignment = self.assignment
        if any(glob.match(path) is not None for glob in self.excluded):
            return {}
        for glob in self.match:
            captures = glob.match(path)
            if captures is None:
                continue
            if any(captures.get(name) in values for name, values in assignment.except_values):
                continue
            if any(name in captures and captures[name] not in values for name, values in assignment.only_values):
                continue
            return self.filled(captures)
        return {}


class CandidateCache:
    """Path candidates per config, shared by the labelers of one runner (compare labels base and
    head code under one policy). Keyed by the policy and by where each key came from: two configs
    with one policy hash can still name different source files."""

    def __init__(self) -> None:
        self.tables: dict[tuple[str, str], dict[str, PathCandidates]] = {}

    def table(self, config: Config) -> dict[str, PathCandidates]:
        key = (config.policy_hash, canonical(config.provenance))
        return self.tables.setdefault(key, {})


# ── Providers ─────────────────────────────────────────────────────


class ProviderRunner:
    """Fills provider-owned fields per path. A provider that fails is reported, never silent."""

    def __init__(self, specs: Iterable[ProviderSpec], repo_root: Path | None) -> None:
        self.specs = list(specs)
        self.repo_root = repo_root
        self.failures: dict[str, str] = {}
        self.handles: dict[str, Any] = {}
        for spec in self.specs:
            try:
                self.handles[spec.name] = self.open(spec)
            except Exception as error:  # noqa: BLE001 - a provider never crashes the run; its fields read as unknown
                self.failures[spec.name] = f"{type(error).__name__}: {error}"
        self.failed_fields = {name for spec in self.specs if spec.name in self.failures for name in spec.sets}

    def open(self, spec: ProviderSpec) -> Any:
        """In a run of a git tree the provider reads a root that holds only the files it declares."""
        if self.repo_root is None:
            raise FileNotFoundError("no repo root to read provider files from")
        provider = REGISTRY.providers[spec.use]
        options = dict(spec.options)
        return provider.open(options, PLUGIN_ROOTS.of(self.repo_root, provider, options))

    def values(self, path: str) -> dict[str, Candidate]:
        out: dict[str, Candidate] = {}
        for spec in self.specs:
            if spec.name not in self.handles:
                continue
            source = f"provider {spec.name} ({spec.use})"
            produced = REGISTRY.providers[spec.use].values(self.handles[spec.name], path)
            for output, name in spec.bindings:
                if output not in produced:
                    continue
                value = produced[output]
                detail = ""
                if isinstance(value, Provided):
                    value, detail = value.value, value.detail
                out[name] = Candidate(value, f"{source}: {detail}" if detail else source, False)
        return out


# ── Directories without `__init__.py` ───────────────────────────


# A file name no glob spells: the path a namespace directory is labelled by is that of a module
# file directly inside it, so a glob written for files (`X/**`) holds for the directory `X`.
NAMESPACE_PROBE = "\x00.py"


class NamespaceDirectories:
    """The directories without `__init__.py` that hold graph modules, by dotted name: an edge can
    name one (a namespace package), and its labels come from the path of a file directly inside
    it. A name two graph modules place in two directories gets no path and is held in `clashes`.
    `inside` holds, per name, the graph modules whose file lies directly in the directory."""

    def __init__(self, modules: dict[str, str]) -> None:
        found: dict[str, str] = {}
        clashes: set[str] = set()
        for module, path in modules.items():
            directories = self.directories(module, path)
            if directories is None:
                continue
            root = len(directories) - module.count(".") - 1
            # Each dotted parent is a prefix of the module: most are normal packages, so the
            # membership test comes before any text is built.
            dots = [at for at, char in enumerate(module) if char == "."]
            for end, dot in enumerate(dots):
                name = module[:dot]
                if name in modules or name in clashes:
                    continue
                directory = "/".join(directories[: root + end + 1])
                if found.setdefault(name, directory) != directory:
                    clashes.add(name)
                    del found[name]
        self.paths = {name: f"{directory}/{NAMESPACE_PROBE}" for name, directory in found.items()}
        self.clashes = frozenset(clashes)
        named = {directory: name for name, directory in found.items()}
        self.inside: dict[str, list[str]] = {}
        for module, path in modules.items():
            name = named.get(path.rpartition("/")[0])
            if name is not None:
                self.inside.setdefault(name, []).append(module)

    @staticmethod
    def directories(module: str, path: str) -> tuple[str, ...] | None:
        """The directories of a module's path, when the path ends in the module's own parts
        (`a/b/c.py` or `a/b/c/__init__.py` for `a.b.c`)."""
        file = PurePosixPath(path)
        own = file.parent if file.stem == "__init__" else file.with_suffix("")
        parts = module.split(".")
        directories = own.parts
        if len(directories) < len(parts) or list(directories[len(directories) - len(parts) :]) != parts:
            return None
        return directories


# ── Labeler ───────────────────────────────────────────────────────

Labels = dict[str, Label]


@dataclass(frozen=True, slots=True)
class NamespaceLabels:
    """A namespace directory's labels, and the fields it reads as unknown."""

    labels: Labels
    unknown: frozenset[str]


@dataclass(frozen=True, slots=True)
class KindSets:
    """[kinds] as sets. `members` holds every name each kind holds true, `kinds_of` the kinds
    that hold each name, in config order. `unknown` holds the symbols with facts that no kind
    holds, but that a kind with `subclass_of` cannot rule out: they pass its other tests and a
    base on their way up did not resolve. Every other symbol with facts has no kind."""

    members: dict[str, frozenset[str]]
    kinds_of: dict[str, tuple[str, ...]]
    unknown: frozenset[str]


class Labeler:
    """Computes labels on demand and caches them. `conflicts` holds config errors found while labelling.

    Assignment and zone candidates depend only on the config and the path; `candidates` shares
    them with other labelers.
    """

    def __init__(
        self,
        config: Config,
        graph: Graph,
        repo_root: Path | None = None,
        index: GraphIndex | None = None,
        candidates: CandidateCache | None = None,
    ) -> None:
        self.config = config
        self.path_candidate_table = (candidates or CandidateCache()).table(config)
        self.graph = graph
        self.index = index or index_of(graph)
        self.facts = self.index.facts
        self.path_to_module = self.index.path_to_module
        self.module_of_symbol = self.index.module_of_symbol
        self.providers = ProviderRunner(config.providers.values(), repo_root)
        self.conflicts: list[str] = []
        self.module_assigns = GlobIndex((PathAssignment(a), a.match) for a in config.assigns if a.symbols is None)
        self.symbol_assigns = [a for a in config.assigns if a.symbols is not None]
        self.zone_fields = [spec.name for spec in config.fields.values() if spec.type is FieldType.ZONE]
        self.zone_roots = [(within, compile_label_glob(within + "/**")) for within in config.zones.within]
        self.zones = GlobIndex(
            ((zone, [compile_label_glob(pattern) for pattern in zone.paths]), zone.paths) for zone in config.zones.zones
        )
        self.zone_candidates = {
            zone.name: Candidate(zone.name, f"zones.{zone.name} ({zone.source})", False) for zone in config.zones.zones
        }
        self.registries = [
            (spec, compile_label_glob(spec.module), spec.loader.rpartition(".")[0])
            for spec in config.registries.values()
        ]
        self.default_zones = {
            within: Candidate(config.zones.default, f"default private zone under {within}", False)
            for within in config.zones.within
        }
        self.provider_fields = frozenset(name for spec in self.providers.specs for name in spec.sets)
        self.module_labels = Memo(self.label_module)
        self.namespace_labels = Memo(self.label_namespace)
        self.provider_labels = Memo(self.label_provided)
        self.symbol_labels = Memo(self.label_symbol)
        self.handed_zones = Memo(self.find_handed_zones)
        self.kind_answers = {name: (Value.known(name), f"kinds.{name}") for name in config.kinds}
        self.export_member = Memo(self.find_export_member)
        self.path_exports = Memo(self.find_path_exports)
        self.module_ordinal = {module: at for at, module in enumerate(graph.modules)}
        self.columns: dict[str, list[tuple[Value, str] | None]] = {}
        self.sparse_values: dict[tuple[str | None, str | None, str], tuple[Value, str]] = {}
        self.exported_cache: dict[tuple[str | None, str | None, str | None, str | None], Truth] = {}
        self.audience_by_module = all(
            spec.audience is None or all(self.module_level(ref) for ref in refs(spec.audience.node))
            for spec in config.exports.values()
        )

    def module_level(self, ref: Ref) -> bool:
        """Whether `src.<name>` or `dst.<name>` reads only the endpoint's module, never its symbol or edge."""
        if len(ref.path) != 2 or ref.path[0] not in ("src", "dst"):
            return False
        name = ref.path[1]
        if name in MODULE_LEVEL_BUILTINS:
            return True
        spec = self.config.fields.get(name)
        return spec is not None and spec.on == "module"

    # ── modules and paths ──

    def path_of(self, module: str) -> str | None:
        return self.graph.modules.get(module)

    @cached_property
    def namespaces(self) -> NamespaceDirectories:
        return NamespaceDirectories(self.graph.modules)

    def labelled_path(self, module: str) -> str | None:
        """The path that labels a module: its file, or the directory of a namespace package."""
        return self.path_of(module) or self.namespaces.paths.get(module)

    def zone_candidate(self, path: str) -> tuple[Candidate | None, str]:
        """The path's zone, or a conflict when several leaf zones match and none overrides."""
        table = self.config.zones
        for within, inside in self.zone_roots:
            root = inside.match(path)
            if root is None:
                continue
            relative = path[len(fill(within, root)) + 1 :]
            matched = [
                zone
                for zone, globs in self.zones.candidates(relative)
                if any(glob.match(relative) is not None for glob in globs)
            ]
            ancestors = {a for zone in matched for a in table.chain(zone.name)[1:]}
            leaves = [zone for zone in matched if zone.name not in ancestors]
            if not leaves:
                return self.default_zones[within], ""
            if len(leaves) > 1:
                overriding = [zone for zone in leaves if zone.override]
                if len(overriding) != 1:
                    names = ", ".join(zone.name for zone in leaves)
                    return None, f"{path}: zones {names} all match; mark one `override = true`"
                leaves = overriding
            return self.zone_candidates[leaves[0].name], ""
        return None, ""

    def path_candidates(self, path: str) -> PathCandidates:
        cached = self.path_candidate_table.get(path)
        if cached is not None:
            return cached
        collected: dict[str, list[Candidate]] = {}
        for assignment in self.module_assigns.candidates(path):
            for name, candidate in assignment.candidates(path).items():
                collected.setdefault(name, []).append(candidate)
        conflicts: tuple[str, ...] = ()
        if self.zone_fields:
            zone, conflict = self.zone_candidate(path)
            conflicts = (conflict,) if conflict else ()
            for name in self.zone_fields if zone is not None else ():
                collected.setdefault(name, []).append(zone)
        cached = self.path_candidate_table[path] = PathCandidates(collected, conflicts)
        return cached

    def settle(self, name: str, candidates: list[Candidate]) -> tuple[Label | None, str]:
        """A field's label from its candidates, or none and the conflict, without the entity."""
        spec = self.config.fields.get(name)
        if spec is not None and spec.type is FieldType.SET:
            values: set[str] = set()
            for candidate in candidates:
                values |= (
                    set(candidate.value)
                    if isinstance(candidate.value, (frozenset, set, list, tuple))
                    else {str(candidate.value)}
                )
            return Label(frozenset(values), "; ".join(c.source for c in candidates)), ""
        overriding = [c for c in candidates if c.override]
        pool = overriding or candidates
        distinct = {repr(c.value) for c in pool}
        if len(distinct) > 1:
            sources = "; ".join(f"{c.value!r} from {c.source}" for c in pool)
            return None, f"field {name!r} set differently by {sources}"
        return Label(pool[0].value, pool[0].source), ""

    def resolve_candidates(self, entity: str, name: str, candidates: list[Candidate]) -> Label | None:
        label, conflict = self.settle(name, candidates)
        if conflict:
            self.conflicts.append(f"{entity}: {conflict}")
        return label

    def resolve_all(self, entity: str, collected: dict[str, list[Candidate]]) -> Labels:
        labels: Labels = {}
        for name, candidates in collected.items():
            label = self.resolve_candidates(entity, name, candidates)
            if label is not None:
                labels[name] = label
        return labels

    def own_candidates(self, cached: PathCandidates) -> dict[str, list[Candidate]]:
        """A path's candidates for the fields no provider sets."""
        return {
            name: list(candidates) for name, candidates in cached.fields.items() if name not in self.provider_fields
        }

    def label_module(self, module: str) -> Labels:
        """Labels from the config's paths, zones and registries. Fields a provider sets are left to
        `label_provided`, so the conflict check never reads providers' files."""
        path = self.path_of(module)
        if path is None:
            return self.namespace_labels(module).labels
        cached = self.path_candidates(path)
        self.conflicts.extend(cached.conflicts)
        labels = self.resolve_all(module, self.own_candidates(cached))
        registry = self.registry_of(path, module)
        if registry is not None:
            labels[REGISTRY_FIELD] = Label(registry.name, "registries")
        return labels

    def label_namespace(self, module: str) -> NamespaceLabels:
        """A namespace directory's labels: those a file directly inside it reads from the path's
        assignments and zones. A field two sources set apart, or that a module file inside the
        directory reads otherwise, is unknown; the modules inside report their own conflicts, so
        none is recorded here."""
        path = self.namespaces.paths.get(module)
        if path is None:
            return NamespaceLabels({}, frozenset())
        inside = [self.module_labels(other) for other in self.namespaces.inside.get(module, ())]
        cached = self.path_candidates(path)
        collected = self.own_candidates(cached)
        labels: Labels = {}
        for name, candidates in collected.items():
            label, _ = self.settle(name, candidates)
            if label is not None:
                labels[name] = label
        unknown = {name for name in collected if name not in labels}
        if cached.conflicts:
            unknown.update(self.zone_fields)
        for other in inside:
            for name in self.config.fields:
                if name in self.provider_fields or self.config.fields[name].on == "symbol":
                    continue
                if self.label_value(other.get(name)) != self.label_value(labels.get(name)):
                    unknown.add(name)
        for name in unknown:
            labels.pop(name, None)
        return NamespaceLabels(labels, frozenset(unknown))

    @staticmethod
    def label_value(label: Label | None) -> object:
        return None if label is None else label.value

    def label_provided(self, module: str) -> Labels:
        """The fields providers set (owners, for example), from the config's paths and the
        providers' files. Read only when something needs them: owners at render time."""
        path = self.path_of(module)
        if path is None:
            return {}
        cached = self.path_candidates(path)
        collected = {
            name: list(candidates) for name, candidates in cached.fields.items() if name in self.provider_fields
        }
        for name, candidate in self.providers.values(path).items():
            collected.setdefault(name, []).append(candidate)
        return self.resolve_all(module, collected)

    def planned_provided(self, module: str) -> tuple[Labels, bool]:
        """`label_provided` for a plan that sends provider labels before anything reads them: the
        labels, and whether two sources set one apart. Such a module's labels are not kept and its
        conflicts not recorded, so they are found where something reads them, as in a run without
        a plan."""
        kept = self.provider_labels.values.get(module)
        if kept is not None:
            return kept, False
        before = len(self.conflicts)
        labels = self.label_provided(module)
        if len(self.conflicts) == before:
            self.provider_labels.values[module] = labels
            return labels, False
        del self.conflicts[before:]
        return labels, True

    def registry_of(self, path: str, module: str) -> RegistrySpec | None:
        for spec, glob, loader_module in self.registries:
            if glob.match(path) is not None or loader_module == module:
                return spec
        return None

    # ── symbols ──

    def label_symbol(self, symbol: str) -> Labels:
        if not self.symbol_assigns:
            return {}
        collected: dict[str, list[Candidate]] = {}
        module = self.module_of_symbol(symbol)
        path = self.path_of(module) if module else None
        for assignment in self.symbol_assigns:
            if not self.symbol_matches(assignment, symbol, path):
                continue
            for name, value in assignment.sets:
                collected.setdefault(name, []).append(Candidate(value, assignment.label(), assignment.override))
        return self.resolve_all(symbol, collected)

    @staticmethod
    def path_allows(assignment: Assignment, path: str | None) -> bool:
        """Whether a symbol assignment's `match` globs let symbols at this path through."""
        if not assignment.match:
            return True
        return path is not None and any(glob_match(p, path) is not None for p in assignment.match)

    def symbol_matches(self, assignment: Assignment, symbol: str, path: str | None) -> bool:
        match = assignment.symbols
        if match is None:
            return False
        if match.names and symbol not in match.names:
            return False
        if not self.path_allows(assignment, path):
            return False
        if match.decorated_by and not set(match.decorated_by) & set(self.facts.decorators(symbol)):
            return False
        if match.subclass_of:
            ancestors, _ = self.facts.ancestors(symbol)
            if not set(match.subclass_of) & ancestors:
                return False
        return True

    def kind_tests_pass(self, spec: KindSpec, symbol: str) -> bool:
        """Whether a symbol with facts passes a kind's tests other than `subclass_of`."""
        facts = self.facts
        if spec.decl and facts.decl(symbol) != spec.decl:
            return False
        if spec.abstract is False and facts.get(symbol, "abstract") == "true":
            return False
        return not spec.decorated_by or bool(set(spec.decorated_by) & set(facts.decorators(symbol)))

    def find_kind_members(self, spec: KindSpec) -> set[str]:
        """Every name the kind holds: the names `subclass_of` lists, and the symbols with facts that
        pass its tests and, with `subclass_of`, inherit a listed name."""
        facts = self.facts
        out = set(spec.subclass_of)
        if spec.subclass_of:
            pool: Iterable[str] = [symbol for symbol in facts.descendants(spec.subclass_of) if facts.has(symbol)]
        elif spec.decl:
            pool = facts.with_decl(spec.decl)
        else:
            pool = self.graph.symbols
        out.update(symbol for symbol in pool if self.kind_tests_pass(spec, symbol))
        return out

    def find_kind_unknowns(self, held: Collection[str]) -> frozenset[str]:
        """Symbols with facts that no kind holds, whose kind is still unknown: a kind with
        `subclass_of` lets them pass its other tests, and a base on their way up did not resolve."""
        specs = [spec for spec in self.config.kinds.values() if spec.subclass_of]
        if not specs:
            return frozenset()
        facts = self.facts
        unresolved = tuple(name for name in facts.children if name.startswith("?"))
        return frozenset(
            symbol
            for symbol in facts.descendants(unresolved)
            if symbol not in held and facts.has(symbol) and any(self.kind_tests_pass(spec, symbol) for spec in specs)
        )

    @cached_property
    def kind_sets(self) -> KindSets:
        members = {spec.name: frozenset(self.find_kind_members(spec)) for spec in self.config.kinds.values()}
        kinds_of: dict[str, list[str]] = {}
        for name, found in members.items():
            for symbol in found:
                kinds_of.setdefault(symbol, []).append(name)
        unknown = self.find_kind_unknowns(kinds_of)
        return KindSets(members, {symbol: tuple(kinds) for symbol, kinds in kinds_of.items()}, unknown)

    def kind_members(self, spec: KindSpec) -> frozenset[str]:
        return self.kind_sets.members[spec.name]

    def kind(self, symbol: str, first_party: bool = True) -> tuple[Value, str]:
        """The symbol's kind from [kinds]. A first-party symbol without facts is unknown."""
        sets = self.kind_sets
        kinds = sets.kinds_of.get(symbol, ())
        if len(kinds) > 1:
            return UNKNOWN, "conflicting kinds"
        if kinds:
            return self.kind_answers[kinds[0]]
        if symbol in sets.unknown or (first_party and self.config.kinds and not self.facts.has(symbol)):
            return UNKNOWN, "no facts for this symbol, or a base did not resolve"
        return NONE, "no kind matches"

    def kind_candidates(self, wanted: frozenset[str]) -> list[str]:
        """The symbols with facts whose kind is not known to be outside `wanted`, sorted: members of
        a wanted kind, names two kinds hold, and symbols of unknown kind."""
        sets = self.kind_sets
        found = set(sets.unknown)
        for name in wanted:
            found.update(sets.members.get(name, ()))
        found.update(symbol for symbol, kinds in sets.kinds_of.items() if len(kinds) > 1)
        return sorted(symbol for symbol in found if self.facts.has(symbol))

    # ── values ──

    def in_domain(self, domain: Domain | None, path: str | None, resolve: Resolver) -> bool:
        if domain is None:
            return False
        if domain.everywhere:
            return True
        if domain.selector is not None:
            return domain.selector.evaluate(resolve) is Truth.TRUE
        return path is not None and any(glob_match(glob, path) is not None for glob in domain.globs)

    def to_value(self, spec: FieldSpec, label: Label) -> Value:
        if spec.type is FieldType.ZONE:
            return Value.known(str(label.value), self.config.zones.chain(str(label.value)))
        if spec.type is FieldType.SET and isinstance(label.value, frozenset):
            return Value.known(label.value)
        if isinstance(label.value, bool):
            return Value.known(label.value)
        return Value.known(str(label.value))

    def label_of(self, module: str | None, symbol: str | None, name: str) -> Label | None:
        """The label a source set on the entity, if any: symbol labels for symbol fields."""
        if self.config.fields[name].on == "symbol":
            return self.symbol_labels(symbol).get(name) if symbol else None
        if name in self.provider_fields:
            return self.provider_labels(module).get(name) if module else None
        return self.module_labels(module).get(name) if module else None

    def column(self, name: str) -> list[tuple[Value, str] | None]:
        """A module field's values, one slot per module ordinal; a slot fills on its first read."""
        found = self.columns.get(name)
        if found is None:
            found = self.columns[name] = [None] * len(self.module_ordinal)
        return found

    def field_value(
        self, module: str | None, symbol: str | None, name: str, sourced: bool = False
    ) -> tuple[Value, str]:
        """A declared field on a module (and the symbol inside it, for symbol fields). A module
        field of a graph module is read from its column; symbol fields stay sparse. `sourced`
        asks for the label pass's own value and source, as `explain` shows them: this labeler's
        are always its own."""
        on_module = self.config.fields[name].on == "module"
        at = self.module_ordinal.get(module) if on_module and module is not None else None
        if at is not None:
            column = self.column(name)
            found = column[at]
            if found is None:
                found = column[at] = self.compute_value(module, None, name)
            return found
        key = (module, None if on_module else symbol, name)
        found = self.sparse_values.get(key)
        if found is None:
            found = self.sparse_values[key] = self.compute_value(module, symbol, name)
        return found

    def read_apart(self, module: str | None, name: str) -> bool:
        """Whether a namespace directory reads a field as unknown: the files directly inside it
        read it apart. Only a directory whose labels were read can say so."""
        found = self.namespace_labels.values.get(module) if module is not None else None
        return found is not None and name in found.unknown

    def compute_value(self, module: str | None, symbol: str | None, name: str) -> tuple[Value, str]:
        spec = self.config.fields[name]
        if spec.on == "symbol" and symbol is None:
            return NONE, "not a symbol"
        label = self.label_of(module, symbol, name)
        if label is not None:
            return self.to_value(spec, label), label.source
        if spec.on == "module" and self.read_apart(module, name):
            return UNKNOWN, "the files directly inside the directory read it apart"
        if spec.on == "symbol" and symbol is not None and self.facts.get(symbol, name):
            return Value.known(self.facts.get(symbol, name)), "producer fact"
        if name in self.providers.failed_fields:
            return UNKNOWN, "provider failed"
        path = self.labelled_path(module) if module else None
        owner = symbol if spec.on == "symbol" else None
        if self.in_domain(spec.domain, path, self.label_resolver(module, owner)):
            return UNKNOWN, "inside the field's domain, but nothing sets it"
        return NONE, "outside the field's domain"

    def label_only(self, module: str | None, symbol: str | None, name: str) -> Value:
        """A field as its labels say, without the domain: what a domain selector itself reads."""
        label = self.label_of(module, symbol, name)
        if label is not None:
            return self.to_value(self.config.fields[name], label)
        if self.config.fields[name].on == "module" and self.read_apart(module, name):
            return UNKNOWN
        return NONE

    def builtin(self, module: str | None, symbol: str | None, name: str, sourced: bool = False) -> Value:
        """An entity attribute the labeler answers itself (see BUILTIN_ENTITY_NAMES); `sourced`
        as for `field_value`."""
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
            decl = self.facts.decl(symbol) if symbol else ""
            return Value.known(decl) if decl else NONE
        if name == "kind":
            return self.kind(symbol)[0] if symbol else NONE
        if name == HANDED_ZONES:
            return self.handed_zones(module) if module else NONE
        label = self.module_labels(module).get(REGISTRY_FIELD) if module else None
        if label is None:
            return NONE
        if name == "registry":
            return Value.known(str(label.value))
        lifecycle = self.config.registries[str(label.value)].lifecycle
        return Value.known(lifecycle) if lifecycle else NONE

    def handed_imports(self, module: str) -> list[Edge]:
        """The module-scope imports of the names a module hands out (`Graph.hand_outs`): every
        one when its `__all__` cannot be read, none when the module is no export."""
        hand_out = self.graph.hand_outs.get(module)
        if hand_out is None:
            return []
        imports = self.index.module_imports.get(module, [])
        return [edge for edge in imports if hand_out.unread or edge.attr("binds") in hand_out.handed]

    def name_owner(self, name: str) -> str | None:
        """The longest dotted prefix of a first-party name that is a graph module or a directory
        without `__init__.py`, a directory two graph modules place apart included: the module whose
        labels the name reads, as a destination's are. None when there is none."""
        head = name
        while head:
            if head in self.graph.modules or head in self.namespaces.paths or head in self.namespaces.clashes:
                return head
            head = head.rpartition(".")[0]
        return None

    def handed_owner(self, name: str) -> str | None:
        """The longest dotted prefix of a handed name that is a graph module or a namespace
        directory: the module whose zone the name has. None when there is none, or when a
        directory two graph modules place apart comes first."""
        owner = self.name_owner(name)
        return None if owner in self.namespaces.clashes else owner

    def find_handed_zones(self, module: str) -> Value:
        """The zones, each with its ancestors, of the modules whose names a module hands out by
        import (`Graph.hand_outs`): none for a module that is no export or when no field is a
        zone, unknown when its `__all__` cannot be read, a handed import did not resolve or a
        handed module's zone cannot be told. A namespace directory has the zone its files read.
        A third-party name has no zone; a first-party name no module answers for is unknown."""
        hand_out = self.graph.hand_outs.get(module)
        if hand_out is None or not self.zone_fields:
            return NONE
        if hand_out.unread:
            return UNKNOWN
        names: set[str] = set()
        for edge in self.handed_imports(module):
            if edge.contexts & NOT_HANDED_CONTEXTS:
                continue
            if edge.resolution is Resolution.UNRESOLVED:
                return UNKNOWN
            for target in edge.targets:
                owner = self.handed_owner(str(target))
                if owner is None and edge.resolution is Resolution.EXTERNAL:
                    continue
                if owner is None:
                    return UNKNOWN
                zone, _ = self.field_value(owner, None, self.zone_fields[0])
                if zone.state is State.UNKNOWN:
                    return UNKNOWN
                if zone.state is State.KNOWN:
                    names.update(members(zone))
        return Value.known(frozenset(names))

    def resolver(self, module: str | None, symbol: str | None, read_field: Callable[..., Value]) -> Resolver:
        def resolve(ref: Ref) -> Value:
            name = ref.path[-1]
            if name in BUILTIN_ENTITY_NAMES:
                return self.builtin(module, symbol, name)
            if name not in self.config.fields:
                return UNKNOWN
            return read_field(module, symbol, name)

        return resolve

    def entity_value(self, module: str | None, symbol: str | None, name: str) -> Value:
        if name in BUILTIN_ENTITY_NAMES:
            return self.builtin(module, symbol, name)
        if name not in self.config.fields:
            return UNKNOWN
        return self.field_value(module, symbol, name)[0]

    def entity_resolver(self, module: str | None, symbol: str | None) -> Resolver:
        """A resolver for bare entity selectors (layout, levels, domains)."""
        return lambda ref: self.entity_value(module, symbol, ref.path[-1])

    def label_resolver(self, module: str | None, symbol: str | None) -> Resolver:
        """Like entity_resolver, but fields read their labels only, so a domain never reads a domain."""
        return self.resolver(module, symbol, self.label_only)

    def known_text(self, module: str, name: str) -> str | None:
        """A module field's value as text when it is known."""
        value, _ = self.field_value(module, None, name)
        return str(value.data) if value.state is State.KNOWN else None

    # ── exports ──

    def find_path_exports(self, path: str) -> frozenset[str]:
        """The exports whose `within` and `paths` the path lies inside."""
        return frozenset(spec.name for spec in self.config.exports.values() if spec.files.covers(path))

    def export_reads_symbol(self, path: str) -> bool:
        """Whether an export that decides by class or by name covers the path. Elsewhere a
        symbol's export membership is its path's."""
        return any(
            self.config.exports[name].subclass_of or self.config.exports[name].symbols
            for name in self.path_exports(path)
        )

    def export_covers(self, spec: ExportSpec, path: str) -> bool:
        """Whether the path lies inside the export's `within` and `paths`."""
        return spec.name in self.path_exports(path)

    def find_export_member(self, key: tuple[str, str, str | None]) -> Truth:
        """Whether a module (symbol None) or a symbol at `path` belongs to the export.

        With `subclass_of` only symbols count: a listed base itself, or a class that inherits one.
        With `symbols` only the listed symbols count.
        """
        name, path, symbol = key
        spec = self.config.exports[name]
        if not self.export_covers(spec, path):
            return Truth.FALSE
        if spec.symbols:
            return Truth.of(symbol in spec.symbols)
        if not spec.subclass_of:
            return Truth.TRUE
        if symbol is None:
            return Truth.FALSE
        ancestors, complete = self.facts.ancestors(symbol)
        if set(spec.subclass_of) & (ancestors | {symbol}):
            return Truth.TRUE
        return Truth.FALSE if complete else Truth.UNKNOWN

    def exported(
        self,
        module: str | None,
        symbol: str | None,
        audience_resolver: Resolver,
        src: tuple[str | None, str | None] | None = None,
    ) -> Truth:
        """Is this module or symbol in an export whose audience includes the caller?

        `src` is the caller's (module, path); when every audience reads only module-level fields
        the answer is cached on it."""
        key = (*src, module, symbol) if src is not None and self.audience_by_module else None
        if key is not None and key in self.exported_cache:
            return self.exported_cache[key]
        truth = self.compute_exported(module, symbol, audience_resolver)
        if key is not None:
            self.exported_cache[key] = truth
        return truth

    def hands_out(self, module: str, symbol: str | None) -> Truth:
        """Whether a module hands out the name a symbol in it is bound by (`Graph.hand_outs`). A
        name it does not bind by import is its own definition; one it imports and keeps to itself
        is no part of what it exports, unknown when its `__all__` cannot be read."""
        hand_out = self.graph.hand_outs.get(module)
        if hand_out is None or symbol is None or not symbol.startswith(f"{module}."):
            return Truth.TRUE
        name = symbol[len(module) + 1 :].split(".", 1)[0]
        if name not in hand_out.kept:
            return Truth.TRUE
        return Truth.UNKNOWN if hand_out.unread else Truth.FALSE

    def compute_exported(self, module: str | None, symbol: str | None, audience_resolver: Resolver) -> Truth:
        path = self.path_of(module) if module else None
        if module is None or path is None:
            return Truth.UNKNOWN
        handed = self.hands_out(module, symbol)
        if handed is not Truth.TRUE:
            return handed
        results: list[Truth] = []
        for spec in self.config.exports.values():
            truth = self.export_member((spec.name, path, symbol))
            if truth is Truth.FALSE:
                continue
            if truth is Truth.TRUE and spec.audience is not None:
                truth = spec.audience.evaluate(audience_resolver)
            results.append(truth)
        return any_of(results)

    def level_groups(self, spec: LevelSpec) -> list[str] | None:
        """The groups a level judges, when a labeler that holds every module's labels finds them
        itself; else None, and the level reads module by module."""
        return None

    # ── whole-repo pass ──

    def find_conflicts(self) -> list[str]:
        """Every config conflict of this graph, found before rules run, whatever the rules read.

        Each module is labelled, except for the fields providers set: those are resolved where
        something reads them (owners at render time). Kinds conflict where two kinds' members
        meet. Symbol assignments are checked only where two that set a field to different values
        both reach the symbol's path."""
        for module in self.graph.modules:
            self.module_labels(module)
        self.find_kind_conflicts()
        self.find_symbol_assignment_conflicts()
        return self.conflicts

    def find_kind_conflicts(self) -> None:
        """A name two kinds hold, from each kind's members: no symbol is classified on its own."""
        for symbol, kinds in self.kind_sets.kinds_of.items():
            if len(kinds) > 1:
                self.kind_conflict(symbol)

    def kind_conflict(self, symbol: str) -> None:
        """Records that two kinds hold `symbol`."""
        self.conflicts.append(f"{symbol}: matches kinds {', '.join(self.kind_sets.kinds_of[symbol])}")

    def rival_symbol_assigns(self) -> list[Assignment]:
        """Symbol assignments that set a field another symbol assignment sets to a different value."""
        values: dict[str, set[str]] = {}
        for assignment in self.symbol_assigns:
            for name, value in assignment.sets:
                spec = self.config.fields.get(name)
                if spec is None or spec.type is not FieldType.SET:
                    values.setdefault(name, set()).add(repr(value))
        contested = {name for name, seen in values.items() if len(seen) > 1}
        return [a for a in self.symbol_assigns if any(name in contested for name, _ in a.sets)]

    def find_symbol_assignment_conflicts(self) -> None:
        rivals = self.rival_symbol_assigns()
        if len(rivals) < 2:
            return
        contested: dict[str | None, bool] = {}
        for symbol in self.graph.symbols:
            module = self.module_of_symbol(symbol)
            if module not in contested:
                path = self.path_of(module) if module else None
                contested[module] = sum(self.path_allows(rival, path) for rival in rivals) > 1
            if contested[module]:
                self.symbol_labels(symbol)

    def explain(self, module: str) -> list[tuple[str, str, str]]:
        """(field, value, source) for every declared field of a module, unassigned ones included,
        and the zones it hands out names from when it is an export module: what a rule row reads
        of the module, so the change predictor compares it."""
        rows: list[tuple[str, str, str]] = []
        for name, spec in self.config.fields.items():
            if spec.on == "symbol":
                continue
            value, source = self.field_value(module, None, name, sourced=True)
            rows.append((name, render(value), source))
        if self.zone_fields and module in self.graph.hand_outs:
            rows.append((HANDED_ZONES, render(self.handed_zones(module)), "hand-outs"))
        registry = self.module_labels(module).get(REGISTRY_FIELD)
        if registry is not None:
            rows.append(("registry", str(registry.value), registry.source))
            lifecycle = self.builtin(module, None, "lifecycle", sourced=True)
            rows.append(("lifecycle", render(lifecycle), f"registries.{registry.value}.lifecycle"))
        return rows


def render(value: Value) -> str:
    """A value as `explain` shows it to a person."""
    if value.state is State.UNKNOWN:
        return "unknown"
    if value.state is State.NONE:
        return "none"
    if isinstance(value.data, frozenset):
        return ", ".join(sorted(value.data)) or "{}"
    if value.chain and len(value.chain) > 1:
        return f"{value.data}  (inside {', '.join(value.chain[1:])})"
    return str(value.data)
