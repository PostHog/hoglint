"""Typed policy: what `hoglint.toml` and its extends chain say, after validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hoglint.globs import ExportPaths
from hoglint.selectors import FieldType, Selector


@dataclass(frozen=True, slots=True)
class Domain:
    """Where a field must be set. Outside it the value is a known `none`."""

    globs: tuple[str, ...] = ()
    selector: Selector | None = None
    everywhere: bool = False


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    type: FieldType
    values: tuple[str, ...] = ()
    on: str = "module"  # module | symbol
    domain: Domain | None = None
    source: str = ""


@dataclass(frozen=True, slots=True)
class SymbolMatch:
    decorated_by: tuple[str, ...] = ()
    subclass_of: tuple[str, ...] = ()
    names: tuple[str, ...] = ()  # the symbols' full dotted names


@dataclass(frozen=True, slots=True)
class Assignment:
    index: int
    match: tuple[str, ...]
    sets: tuple[tuple[str, Any], ...]
    override: bool = False
    except_values: tuple[tuple[str, tuple[str, ...]], ...] = ()
    only_values: tuple[tuple[str, tuple[str, ...]], ...] = ()
    except_paths: tuple[str, ...] = ()
    symbols: SymbolMatch | None = None
    source: str = ""

    def label(self) -> str:
        what = ", ".join(self.match) if self.match else "symbols"
        return f"[[assign]] {what}  ({self.source})"


@dataclass(frozen=True, slots=True)
class ZoneSpec:
    name: str
    parent: str = ""
    paths: tuple[str, ...] = ()
    override: bool = False
    source: str = ""


@dataclass(frozen=True, slots=True)
class ZoneTable:
    within: tuple[str, ...] = ()
    default: str = "internal"
    zones: tuple[ZoneSpec, ...] = ()

    def by_name(self) -> dict[str, ZoneSpec]:
        return {zone.name: zone for zone in self.zones}

    def chain(self, name: str) -> tuple[str, ...]:
        """The zone and its ancestors, nearest first."""
        index = self.by_name()
        out: list[str] = []
        current = name
        while current and current not in out:
            out.append(current)
            spec = index.get(current)
            current = spec.parent if spec is not None else ""
        return tuple(out)


@dataclass(frozen=True, slots=True)
class KindSpec:
    name: str
    subclass_of: tuple[str, ...] = ()
    decorated_by: tuple[str, ...] = ()
    abstract: bool | None = None
    decl: str = ""
    source: str = ""
    expected: bool = False  # zero matching symbols means a producer found nothing, not a clean repo


@dataclass(frozen=True, slots=True)
class ShapePolicy:
    name: str
    data: tuple[str, ...] = ()
    # The shape that makes a class an allowed behavior class where `owned_elsewhere` holds for a
    # class of that shape in its lineage; empty for none.
    behavior: str = ""
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    unknown: str = "error"  # error | incomplete
    signatures: bool = False  # the signatures of what the export hands out are part of the contract
    # An entity selector: a symbol it holds for is allowed as it stands, whatever its kind.
    allow_symbols: Selector | None = None
    # An entity selector: a class it holds for is its owner's contract, so an allowed class that is
    # no data shape is walked only where it does not hold, and it makes a behavior class.
    owned_elsewhere: Selector | None = None
    # The types a slot's row reads as unknown when the export rule walks under this policy.
    unknown_types: tuple[str, ...] = ()
    # Read by a signature rule that names the policy: a mapping given type arguments is typed, and
    # with `opaque_payload = "except-return"` an unknown type as its argument is an opaque payload,
    # clean in a parameter or a field (D93).
    mappings: tuple[str, ...] = ()
    opaque_payload: str = "never"  # never | except-return
    source: str = ""


@dataclass(frozen=True, slots=True)
class ExportSpec:
    name: str
    within: str
    paths: tuple[str, ...]
    shape: str
    audience: Selector | None = None  # None: everyone
    subclass_of: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()  # only these symbols, not their subclasses
    source: str = ""
    files: ExportPaths = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", ExportPaths(self.within, self.paths))

    def label(self) -> str:
        return f"[exports.{self.name}]  ({self.source})"


@dataclass(frozen=True, slots=True)
class RegistrySpec:
    name: str
    loader: str
    module: str
    attribute: str
    shape: str = ""
    lifecycle: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """`bindings` pairs each provider output with the field it fills."""

    name: str
    use: str
    sets: tuple[str, ...]
    options: tuple[tuple[str, Any], ...] = ()
    source: str = ""
    bindings: tuple[tuple[str, str], ...] = ()

    def option(self, key: str, default: Any = None) -> Any:
        for name, value in self.options:
            if name == key:
                return value
        return default


@dataclass(frozen=True, slots=True)
class CoverSpec:
    """One thing a coverage rule needs covered: another rule's column, an edge selection read at
    an endpoint (`src`, `dst`, `surface`), or an entity selection over modules (`module`) or over
    the locations they stand in (`location`)."""

    text: str
    rule: str = ""
    column: str = ""
    selector: Selector | None = None
    endpoint: str = ""
    # Why the cover needs a path, in words, as a finding's message names it.
    why: str = ""


# How safe a way out of a finding is: `safe` cannot change behavior, `care` is a code change to
# review, `person` stops and tells the person, `note` asks for no action.
WAY_SAFETIES = ("safe", "care", "person", "note")


@dataclass(frozen=True, slots=True)
class Way:
    """One way out of a finding of a rule, as the output prints it under the rule's first finding."""

    safety: str
    text: str


@dataclass(frozen=True, slots=True)
class RuleExample:
    """Code the rule reports, and the same code fixed, as `explain <rule>` prints them."""

    finding: str
    fixed: str


@dataclass(frozen=True, slots=True)
class RuleSpec:
    id: str
    primitive: str
    doc: str = ""
    # The cause a finding prints, the ways out of it and an example: the rule's one source of text.
    why: str = ""
    ways: tuple[Way, ...] = ()
    example: RuleExample | None = None
    kinds: tuple[str, ...] = ()
    contexts_include: tuple[str, ...] = ()
    contexts_exclude: tuple[str, ...] | None = None  # None: unset, so the primitive's default applies
    view: str = ""
    endpoint: str = "origin"
    from_: Selector | None = None
    select: Selector | None = None
    deny: Selector | None = None
    allow: Selector | None = None
    require: Selector | None = None
    unless: Selector | None = None
    on_unresolved: str = "finding"
    on_unknown: str = "finding"
    identity_name: str = ""
    identity: tuple[str, ...] = ()
    counted: bool = False
    growth_key: tuple[str, ...] = ()
    ledger: bool = True
    report: str = "blocking"
    allow_shapes: tuple[str, ...] = ()
    deny_shapes: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    classify: str = ""
    exactly_one: str = ""
    covers: tuple[CoverSpec, ...] = ()
    inputs: tuple[tuple[str, Any], ...] = ()
    on: str = "module"
    within: str = ""
    files: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    registry: tuple[str, ...] = ()  # layout: the registries whose slots it checks
    exports: tuple[str, ...] = ()
    unknown_types: tuple[str, ...] = ()
    uses_at_import: bool = False  # entrypoint: a later use of an imported name is judged at its import
    through_denied: bool = False  # reach: the search goes on through a denied target and reports what lies past it

    slots_reported_by: tuple[str, ...] = ()  # export: the signature rules it leaves a slot to where they read its row
    producer_options: tuple[tuple[str, Any], ...] = ()
    shape: str = ""  # signature: the shape policy its `select`, `deny`, `unless` and unknown types come from
    written: tuple[tuple[str, str], ...] = ()  # (selector, where its text came from) for each one `shape` wrote
    source: str = ""

    def input(self, key: str, default: Any = None) -> Any:
        for name, value in self.inputs:
            if name == key:
                return value
        return default


@dataclass(frozen=True, slots=True)
class LevelSpec:
    name: str
    group_by: str
    requires: tuple[str, ...]
    doc: str = ""
    select: Selector | None = None
    strict: tuple[str, ...] = ()
    source: str = ""
    # The required rules whose rows count only when all their subjects fall in one group.
    within_group: tuple[str, ...] = ()
    # The outside input where a group claims the level, as a coverage adapter and its options; a
    # claiming group that fails the level fails the run. Empty: nothing claims the level.
    claimed_by: tuple[tuple[str, Any], ...] = ()
    # The zones whose files `hoglint watched` adds to each group's watched files, whatever imports
    # them: the export, the surface reached with no import, the model surface.
    watch_zones: tuple[str, ...] = ()

    def claim_option(self, key: str, default: Any = None) -> Any:
        for name, value in self.claimed_by:
            if name == key:
                return value
        return default


@dataclass(frozen=True, slots=True)
class LedgerSpec:
    file: str = "hoglint.ledger"
    exceptions: str = "hoglint.exceptions.toml"


# What a runner template holds where the pinned version goes.
VERSION_SLOT = "{version}"
DEFAULT_LOCK = "uv.lock"
DEFAULT_RUNNER = "uv run --no-project --with hoglint=={version} hoglint"


@dataclass(frozen=True, slots=True)
class EngineSpec:
    """How `compare` finds and runs the engine a base commit pins, when hoglint runs installed:
    `lock` is the lock file whose `hoglint` entry pins the version, and `runner` the command
    that runs `hoglint` at the version that replaces `{version}`."""

    lock: str = DEFAULT_LOCK
    runner: str = DEFAULT_RUNNER


@dataclass(frozen=True, slots=True)
class Roles:
    """Fields that generic code reads by role. `owner` lists a finding's owners; `group` groups
    modules into the units cycles and compare's new-pair report are about."""

    owner: str = ""
    group: str = ""


@dataclass(frozen=True, slots=True)
class OutputSpec:
    name: str
    view: str
    format: str
    path: str


@dataclass(frozen=True, slots=True)
class Config:
    fields: dict[str, FieldSpec] = field(default_factory=dict)
    assigns: tuple[Assignment, ...] = ()
    zones: ZoneTable = ZoneTable()
    kinds: dict[str, KindSpec] = field(default_factory=dict)
    shapes: dict[str, ShapePolicy] = field(default_factory=dict)
    exports: dict[str, ExportSpec] = field(default_factory=dict)
    registries: dict[str, RegistrySpec] = field(default_factory=dict)
    providers: dict[str, ProviderSpec] = field(default_factory=dict)
    rules: dict[str, RuleSpec] = field(default_factory=dict)
    levels: dict[str, LevelSpec] = field(default_factory=dict)
    ledger: LedgerSpec = LedgerSpec()
    engine: EngineSpec = EngineSpec()
    outputs: dict[str, OutputSpec] = field(default_factory=dict)
    identities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    roles: Roles = Roles()
    views: dict[str, tuple[str, ...]] = field(default_factory=dict)  # view name -> the edge kinds it reads
    edge_kinds: tuple[str, ...] = ()
    use_shapes: tuple[str, ...] = ()
    sections: dict[str, Any] = field(default_factory=dict)  # discovery and pack sections, unvalidated by core
    pack_sections: tuple[str, ...] = ()  # sections a pack owns, in extends order
    policy_hash: str = ""
    provenance: dict[str, str] = field(default_factory=dict)
    files: tuple[str, ...] = ()  # the config files loaded, by label: a repo path, or `hoglint:<pack>`
    expanded: dict[str, Any] = field(default_factory=dict)

    @property
    def discovery(self) -> dict[str, Any]:
        return self.sections.get("discovery", {})
