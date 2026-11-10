"""The level primitive: which groups pass an ordered level such as Isolated.

A level is specified apart from lint acceptance:
- A group passes when every rule in `requires` ran for it and none of its findings is new.
- Ledgered debt may coexist with the level, except where it is safety-relevant: a finding that
  exists because something did not resolve, an incomplete result, or any finding of a rule in
  `strict` forbids the level even when the ledger accepts it.
- Exceptions make a use allowed, so an allowed finding does not count against the group. They
  never remove impact edges; test selection reads edges, not this result.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

from hoglint.config.model import LevelSpec
from hoglint.index import Memo
from hoglint.labels import Labeler
from hoglint.model import FindingStatus
from hoglint.rules.base import RuleRun
from hoglint.rules.findings import FindingTable
from hoglint.selectors import Truth


@dataclass(frozen=True, slots=True)
class Block:
    """One reason a group fails a level: a finding of a required rule, kept as its identity and
    its row, so its message is made only when the reason is shown; or a reason with no finding
    (an incomplete result, a rule that did not run), kept as `text`."""

    rule: str
    why: str = ""
    identity: tuple[str, ...] = ()
    text: str = ""
    table: FindingTable | None = field(default=None, compare=False, repr=False)
    row: int = -1

    def render(self) -> str:
        if self.table is None:
            return self.text
        return f"{self.rule}: {self.why}: {self.table.finding(self.row).message}"


class Reasons(Mapping[str, list[str]]):
    """group -> why it fails, as text. A group's reasons are made when they are read."""

    def __init__(self, blocks: dict[str, list[Block]]) -> None:
        self.blocks = blocks

    def __getitem__(self, group: str) -> list[str]:
        return [block.render() for block in self.blocks[group]]

    def __iter__(self) -> Iterator[str]:
        return iter(self.blocks)

    def __len__(self) -> int:
        return len(self.blocks)


@dataclass(frozen=False, slots=True)
class LevelResult:
    name: str
    passing: list[str] = field(default_factory=list)
    blocks: dict[str, list[Block]] = field(default_factory=dict)

    @property
    def failing(self) -> Reasons:
        return Reasons(self.blocks)


class LevelEvaluator:
    def __init__(self, spec: LevelSpec, labeler: Labeler, runs: dict[str, RuleRun]) -> None:
        self.spec = spec
        self.labeler = labeler
        self.runs = runs
        self.group_of = Memo(self.find_group)

    def find_group(self, subject: str) -> str | None:
        return self.labeler.known_text(self.labeler.module_of_symbol(subject) or subject, self.spec.group_by)

    def groups(self) -> list[str]:
        found = self.labeler.level_groups(self.spec)
        if found is not None:
            return found
        out: set[str] = set()
        for module in self.labeler.graph.modules:
            group = self.group_of(module)
            if group is None:
                continue
            if self.spec.select is not None:
                resolve = self.labeler.entity_resolver(module, None)
                if self.spec.select.evaluate(resolve) is not Truth.TRUE:
                    continue
            out.add(group)
        return sorted(out)

    def evaluate(self) -> LevelResult:
        result = LevelResult(self.spec.name)
        reasons: dict[str, list[Block]] = {group: [] for group in self.groups()}
        for rule_id in self.spec.requires:
            run = self.runs.get(rule_id)
            if run is None:
                for group in reasons:
                    reasons[group].append(Block(rule_id, text=f"{rule_id} did not run"))
                continue
            self.collect(rule_id, run, reasons)
        for group, why in sorted(reasons.items()):
            if why:
                result.blocks[group] = why
            else:
                result.passing.append(group)
        return result

    def collect(self, rule_id: str, run: RuleRun, reasons: dict[str, list[Block]]) -> None:
        strict = rule_id in self.spec.strict
        table = run.findings
        unknown = table.unknown
        new = set(table.rows_with(FindingStatus.NEW))
        # A ledgered finding blocks only when it is unknown or its rule is strict.
        for row in range(len(table)):
            if table.allowed[row] or not (unknown[row] or strict or row in new):
                continue
            why = "unknown" if unknown[row] else "strict, so the ledger does not excuse it" if strict else "new finding"
            identity = table.identity(row)
            for group in {self.group_of(subject) for subject in table.subjects(row)}:
                if group in reasons:
                    reasons[group].append(Block(rule_id, why, identity, table=table, row=row))
        for item in run.incomplete:
            groups = {self.group_of(subject) for subject in item.subjects} if item.subjects else set(reasons)
            for group in groups:
                if group in reasons:
                    reasons[group].append(Block(rule_id, text=f"{rule_id}: incomplete: {item.reason}"))
