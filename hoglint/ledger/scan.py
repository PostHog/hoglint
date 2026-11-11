"""A run summary: what one (code, policy, engine) run produced, in the form compare, check and
query read. It is hoglint's one cached artifact (see `store.py`).

`hoglint scan` writes it as zstd-compressed JSON, so a base engine can run in its own checkout
and process. The file carries a protocol version and its envelope (see `envelope.py`); a reader
refuses any other protocol instead of guessing. It holds:
- lines and growth: the scan's ledger line counts, level lines included, and each ledgered
  rule's growth-key columns; a column the identity does not hold is a label of the line's source
  module, and `labels` keeps that label, and the group field's, of every source module a line names;
- findings: every finding with its identity, status, witnesses, owner module and subjects,
  allowed ones included (owners themselves are read at render time), and the message, site and
  remedies of each finding `check` prints;
- incomplete: every incomplete item in run order with its cause and a line-free fingerprint, so
  the count and a replacement under one key both survive serialization;
- triples: (src group, dst group, edge kind, context), when `with_triples` says they were listed;
- couplings and witnesses: what move admission reads, cut to `scope`, the modules a rename touches;
- levels: the groups that pass each level, and why the others fail (a blocking finding by its
  key, not its message);
- what else `check` prints: ledger equality, stale exceptions with every review date, exception
  problems, advisory rules, and the module of each path for `--changed`;
- ledger: the `source` of the ledger file the run read, so a summary stands in for a `check` run
  only when that run would read the same ledger (see `RunMemo` in cli.py).

A summary holds no absolute path and nothing that depends on the day or the machine, so one
built on Linux CI serves a Mac.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import repeat
from pathlib import Path
from typing import Any

import orjson
from compression import zstd

from hoglint.config.schema import canonical
from hoglint.index import Memo
from hoglint.ledger.cache import write_atomic
from hoglint.ledger.couplings import Coupling, CouplingScan
from hoglint.ledger.envelope import PROTOCOL, Envelope, short_digest
from hoglint.ledger.equality import EqualityResult
from hoglint.ledger.file import LedgerFile, LineKey, parse_key, render_key
from hoglint.ledger.records import (
    BlockRecord,
    ExceptionsRecord,
    IncompleteRecord,
    Portable,
    ShownRecord,
    StoredFindings,
    equality_from_json,
    equality_to_json,
    touches,
)
from hoglint.model import SCHEMA_VERSION, Edge, FindingStatus, Graph, WitnessRow
from hoglint.rules.engine import UNREADABLE, RunResult
from hoglint.rules.exceptions import ExceptionsFile
from hoglint.rules.findings import FindingTable

ZSTD_LEVEL = 1  # a whole-repo summary shrinks about 20 times at level 1, in a few hundredths of a second
# The parts a reader loads on their own (see store.py), and the fields each part holds.
SUMMARY_SECTIONS: dict[str, tuple[str, ...]] = {
    "envelope": ("protocol", "envelope", "schema", "growth", "docs", "scope", "with_triples", "advisory", "ledger"),
    "incompletes": ("incomplete",),
    "findings": ("findings", "lines", "labels", "equality", "exceptions", "modules"),
    "couplings": ("triples", "couplings", "witnesses"),
    "levels": ("levels", "level_failures"),
}
ALL_SECTIONS = frozenset(SUMMARY_SECTIONS)
Triple = tuple[str, str, str, str]  # (src group, dst group, edge kind, context)
# A rule's growth-key columns: an identity index, or (field, index) for that field's label of the
# module at an identity index.
GrowthColumns = tuple[int | tuple[str, int], ...]
ModuleLabels = dict[str, dict[str, str]]  # field -> source module -> its label ("" when none)
SOURCE_MODULE = "src.module"
Obligation = tuple[str, str, str]  # (rule, key, reason)


class ProtocolError(Exception):
    """A summary file this engine cannot read."""


def is_shown(
    status: FindingStatus, witnesses: tuple[WitnessRow, ...], subjects: tuple[str, ...], touched: set[str] | None
) -> bool:
    """Whether `check` prints a finding: a new one, and with `--changed` (`touched` holds the
    changed paths and modules) also accepted debt whose witness or subject is touched."""
    if status is FindingStatus.NEW:
        return True
    if touched is None or status is not FindingStatus.ACCEPTED:
        return False
    return touches(touched, (witness[StoredFindings.WITNESS_PATH] for witness in witnesses), subjects)


def shown_column(
    table: FindingTable,
    statuses: list[FindingStatus],
    witnesses: list[tuple[WitnessRow, ...]],
    subjects: list[tuple[str, ...]],
    portable: Portable,
    touched: set[str] | None,
) -> list[Any]:
    """The stored message, site and remedies of each row `check` prints (`is_shown`), None for any
    other. With no `touched` set `check` prints only new rows, so only they are asked."""
    shown: list[Any] = [None] * len(statuses)
    if touched is None:
        asked: Iterable[int] = [row for row, status in enumerate(statuses) if status is FindingStatus.NEW]
    else:
        asked = range(len(statuses))
    for row in asked:
        if is_shown(statuses[row], witnesses[row], subjects[row], touched):
            shown[row] = ShownRecord.of(table.finding(row), portable).to_json()
    return shown


def finding_rows(result: RunResult, portable: Portable, touched: set[str] | None) -> list[Any]:
    """Every finding of a run as the findings section stores it (`FindingRecord.to_json`), built
    from each rule's table: a Finding is made only for a row `check` prints, for its message."""
    out: list[Any] = []
    for rule, run in result.runs.items():
        table = run.findings
        statuses = table.statuses()
        witnesses, owners, subjects = table.stored_parts()
        columns: list[Iterable[Any]] = [()] * StoredFindings.FIELDS
        columns[StoredFindings.RULE] = repeat(rule)
        columns[StoredFindings.IDENTITY] = [table.identities[place] for place in table.identity_at]
        columns[StoredFindings.STATUS] = [status.value for status in statuses]
        columns[StoredFindings.SHOWN] = shown_column(table, statuses, witnesses, subjects, portable, touched)
        columns[StoredFindings.WITNESSES] = witnesses
        columns[StoredFindings.OWNER] = owners
        columns[StoredFindings.SUBJECTS] = subjects
        columns[StoredFindings.LEDGERABLE] = map(bool, table.ledgerable)
        columns[StoredFindings.UNKNOWN] = map(bool, table.unknown)
        out.extend(zip(*columns))
    return out


def fingerprint(edge: Edge | None) -> str:
    """What tells apart two items of one obligation without line numbers: the site's scope and the
    edge's use, shape and attributes. Empty for an item that no single edge raised."""
    if edge is None:
        return ""
    attrs = canonical(sorted(edge.attrs))
    return short_digest([edge.site.scope, edge.kind, edge.surface, edge.use, edge.shape, edge.shape_detail, attrs])


def growth_columns(identity: tuple[str, ...], growth_key: tuple[str, ...]) -> GrowthColumns:
    """A growth key as columns: an identity column by its index, any other `src.<field>` as that
    field's label of the module the identity's `src.module` names (the schema allows no other)."""
    return tuple(
        identity.index(column) if column in identity else (column.removeprefix("src."), identity.index(SOURCE_MODULE))
        for column in growth_key
    )


def source_labels(result: RunResult, growth: dict[str, GrowthColumns]) -> ModuleLabels:
    """The labels a growth key reads of a line's source module: each derived field's, and the group
    field's, so a policy that starts to derive the group judges the base scan by the same key.
    Every source module a ledgered line names gets them."""
    names = {column[0] for columns in growth.values() for column in columns if not isinstance(column, int)}
    if result.config.roles.group:
        names.add(result.config.roles.group)
    modules: set[str] = set()
    for rule, identity in result.scan:
        spec = result.config.rules.get(rule)
        if spec is not None and SOURCE_MODULE in spec.identity:
            modules.add(identity[spec.identity.index(SOURCE_MODULE)])
    labeler = result.labeler
    return {
        name: {module: labeler.known_text(module, name) or "" for module in sorted(modules)} for name in sorted(names)
    }


@dataclass(frozen=False, slots=True)
class ScanSummary:
    envelope: Envelope = field(default_factory=Envelope)
    schema: int = 0
    lines: dict[LineKey, int] = field(default_factory=dict)
    growth: dict[str, GrowthColumns] = field(default_factory=dict)
    labels: ModuleLabels = field(default_factory=dict)
    docs: dict[str, str] = field(default_factory=dict)  # rule -> its doc, for what `check` prints
    findings: StoredFindings = field(default_factory=StoredFindings)
    incomplete: list[IncompleteRecord] = field(default_factory=list)
    triples: set[Triple] = field(default_factory=set)
    with_triples: bool = False
    scope: frozenset[str] = frozenset()
    couplings: dict[Coupling, int] = field(default_factory=dict)
    witnesses: dict[LineKey, list[Coupling]] = field(default_factory=dict)
    levels: dict[str, list[str]] = field(default_factory=dict)
    level_failures: dict[str, dict[str, list[BlockRecord]]] = field(default_factory=dict)
    equality: EqualityResult | None = None
    exceptions: ExceptionsRecord = field(default_factory=ExceptionsRecord)
    advisory: frozenset[str] = frozenset()
    modules: dict[str, str] = field(default_factory=dict)
    ledger: str = ""  # the run ledger's `source`; empty when unknown
    loaded: frozenset[str] = ALL_SECTIONS  # the sections that hold data; a reader may load fewer

    @staticmethod
    def from_run(
        result: RunResult,
        graph: Graph,
        envelope: Envelope,
        scope: frozenset[str] = frozenset(),
        with_triples: bool = True,
        roots: tuple[Path, ...] = (),
        exceptions: ExceptionsFile | None = None,
        ledger: LedgerFile | None = None,
        touched: set[str] | None = None,
    ) -> ScanSummary:
        """`scope` names the modules whose couplings and witnesses move admission reads. `roots`
        are the run's policy and code roots, which text in the summary names relative to the repo;
        `exceptions` gives the review dates of the run's exception entries, `ledger` the ledger the
        run read. The summary keeps the message, site and remedies of the findings `check` prints
        (`is_shown`); `touched` names the paths and modules of a `--changed` run."""
        config = result.config
        portable = Portable.of(*roots)
        summary = ScanSummary(envelope=envelope, schema=SCHEMA_VERSION, lines=dict(result.scan), scope=scope)
        summary.ledger = ledger.source if ledger is not None else ""
        for rule in config.rules.values():
            if rule.ledger and rule.identity:
                summary.growth[rule.id] = growth_columns(rule.identity, rule.growth_key)
        summary.labels = source_labels(result, summary.growth)
        summary.docs = {rule.id: rule.doc for rule in config.rules.values() if rule.doc}
        summary.findings = StoredFindings(finding_rows(result, portable, touched))
        summary.incomplete = [
            IncompleteRecord.of(item, fingerprint(item.edge), portable) for item in result.incomplete()
        ]
        summary.levels = {name: list(level.passing) for name, level in result.levels.items()}
        summary.level_failures = {
            name: {
                group: [BlockRecord.of(block, portable) for block in blocks] for group, blocks in level.blocks.items()
            }
            for name, level in result.levels.items()
        }
        summary.equality = result.equality
        summary.exceptions = ExceptionsRecord.of(result.exceptions, exceptions)
        summary.advisory = frozenset(rule for rule in config.rules if result.advisory(rule))
        summary.modules = dict(graph.modules)
        if with_triples:
            summary.triples = group_triples(result, graph)
            summary.with_triples = True
        couplings = CouplingScan(result, graph, scope)
        summary.couplings = couplings.counts()
        summary.witnesses = couplings.witnesses()
        return summary

    def finding_keys(self) -> set[LineKey]:
        """The ledger key of every finding that no exception allows."""
        return self.findings.keys()

    @property
    def unread(self) -> list[str]:
        """The sources the run could not read (see `UNREADABLE`): the summary is not the tree's."""
        return [item.key for item in self.incomplete if item.rule == UNREADABLE]

    def obligations(self) -> dict[Obligation, list[str]]:
        out: dict[Obligation, list[str]] = {}
        for item in self.incomplete:
            out.setdefault((item.rule, item.key, item.reason), []).append(item.mark)
        return {obligation: sorted(marks) for obligation, marks in out.items()}

    def covers(self, scope: frozenset[str], triples: bool = False) -> bool:
        """Whether this summary's couplings answer move admission for renames within `scope`, and
        it lists triples when a reader needs them."""
        return scope <= self.scope and (self.with_triples or not triples)

    def to_json(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "envelope": self.envelope.to_json(),
            "schema": self.schema,
            "lines": {render_key(key): count for key, count in sorted(self.lines.items())},
            "growth": {
                rule: [column if isinstance(column, int) else list(column) for column in columns]
                for rule, columns in sorted(self.growth.items())
            },
            "labels": {name: dict(sorted(values.items())) for name, values in sorted(self.labels.items())},
            "docs": dict(sorted(self.docs.items())),
            "findings": self.findings.to_json(),
            "incomplete": [item.to_json() for item in self.incomplete],
            "triples": sorted(list(triple) for triple in self.triples),
            "with_triples": self.with_triples,
            "scope": sorted(self.scope),
            "couplings": [
                [rule, src, dst, kind, list(values), count]
                for (rule, src, dst, kind, values), count in sorted(self.couplings.items())
            ],
            "witnesses": {
                render_key(key): [[rule, src, dst, kind, list(values)] for rule, src, dst, kind, values in couplings]
                for key, couplings in sorted(self.witnesses.items())
            },
            "levels": self.levels,
            "level_failures": {
                name: {group: [block.to_json() for block in blocks] for group, blocks in failing.items()}
                for name, failing in self.level_failures.items()
            },
            "equality": equality_to_json(self.equality),
            "exceptions": self.exceptions.to_json(),
            "advisory": sorted(self.advisory),
            "modules": self.modules,
            "ledger": self.ledger,
        }

    @staticmethod
    def from_json(data: dict[str, Any]) -> ScanSummary:
        protocol = data.get("protocol", 0)
        if protocol != PROTOCOL:
            raise ProtocolError(f"the summary speaks protocol {protocol}; this engine reads protocol {PROTOCOL}")
        return ScanSummary(
            envelope=Envelope.from_json(data.get("envelope", {})),
            schema=int(data.get("schema", 0)),
            lines={parse_key(text): int(count) for text, count in data.get("lines", {}).items()},
            growth={
                rule: tuple(column if isinstance(column, int) else (column[0], column[1]) for column in columns)
                for rule, columns in data.get("growth", {}).items()
            },
            labels={name: dict(values) for name, values in data.get("labels", {}).items()},
            docs=dict(data.get("docs", {})),
            findings=StoredFindings.loaded(data.get("findings", [])),
            incomplete=[IncompleteRecord.from_json(item) for item in data.get("incomplete", [])],
            triples={(a, b, c, d) for a, b, c, d in data.get("triples", [])},
            with_triples=bool(data.get("with_triples", False)),
            scope=frozenset(data.get("scope", [])),
            couplings={
                (rule, src, dst, kind, tuple(values)): int(count)
                for rule, src, dst, kind, values, count in data.get("couplings", [])
            },
            witnesses={
                parse_key(text): [(rule, src, dst, kind, tuple(values)) for rule, src, dst, kind, values in couplings]
                for text, couplings in data.get("witnesses", {}).items()
            },
            levels={name: list(groups) for name, groups in data.get("levels", {}).items()},
            level_failures={
                name: {group: [BlockRecord.from_json(block) for block in blocks] for group, blocks in failing.items()}
                for name, failing in data.get("level_failures", {}).items()
            },
            equality=equality_from_json(data.get("equality")),
            exceptions=ExceptionsRecord.from_json(data.get("exceptions", {})),
            advisory=frozenset(data.get("advisory", [])),
            modules=dict(data.get("modules", {})),
            ledger=str(data.get("ledger", "")),
        )

    def to_sections(self) -> dict[str, dict[str, Any]]:
        if self.loaded != ALL_SECTIONS:
            raise ValueError(f"a summary that holds only {sorted(self.loaded)} cannot be written")
        data = self.to_json()
        return {name: {key: data[key] for key in keys} for name, keys in SUMMARY_SECTIONS.items()}

    @staticmethod
    def from_sections(parts: dict[str, dict[str, Any]]) -> ScanSummary:
        """A summary of the sections at hand; the envelope section is always one of them."""
        data: dict[str, Any] = {}
        for part in parts.values():
            data.update(part)
        summary = ScanSummary.from_json(data)
        summary.loaded = frozenset(parts)
        return summary

    def write(self, path: Path) -> None:
        """The whole summary as one zstd-compressed JSON file: what `scan` and `summary export` write."""
        self.to_sections()
        write_atomic(path, zstd.compress(orjson.dumps(self.to_json()), ZSTD_LEVEL))

    @staticmethod
    def read(path: Path) -> ScanSummary:
        try:
            data = orjson.loads(zstd.decompress(path.read_bytes()))
        except (OSError, zstd.ZstdError, orjson.JSONDecodeError) as error:
            raise ProtocolError(f"cannot read the summary {path}: {error}") from error
        if not isinstance(data, dict):
            raise ProtocolError(f"{path} is not a scan summary")
        try:
            return ScanSummary.from_json(data)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise ProtocolError(f"{path} is not a well-formed summary: {error}") from error


def group_triples(result: RunResult, graph: Graph) -> set[Triple]:
    """(src group, dst group, kind, context) for every edge between two known groups (`[roles] group`)."""
    group = result.config.roles.group
    if not group:
        return set()
    labeler = result.labeler
    out: set[Triple] = set()
    group_of = Memo(lambda module: labeler.known_text(module, group))
    for edge in graph.edges:
        src = group_of(edge.site.module)
        if src is None:
            continue
        context = "+".join(sorted(c.value for c in edge.contexts)) or "prod"
        for target in edge.targets:
            module = labeler.module_of_symbol(str(target))
            dst = group_of(module) if module is not None else None
            if dst is not None and dst != src:
                out.add((src, dst, edge.kind, context))
    return out
