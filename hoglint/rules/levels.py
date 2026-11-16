"""The level primitive: which groups pass an ordered level such as Isolated.

A level is specified apart from lint acceptance:
- A group passes when every rule in `requires` ran for it and none of its findings is new.
- Ledgered debt may coexist with the level, except where it is safety-relevant: a finding that
  exists because something did not resolve, an incomplete result, or any finding of a rule in
  `strict` forbids the level even when the ledger accepts it.
- Exceptions make a use allowed, so an allowed finding does not count against the group. They
  never remove impact edges; test selection reads edges, not this result.
- A row of a rule in `within_group` counts only when its subjects do not fall in two or more
  groups: a row between two groups is a lint matter only.
- A group claims the level where the outside input `claimed_by` names is narrowed for one of its
  module files. A claiming group that fails the level fails the run, and no ledger line or
  exception accepts that: an input that narrows on a level the group does not hold is never debt.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from hoglint.config.model import LevelSpec
from hoglint.index import Memo
from hoglint.labels import Labeler, label_text
from hoglint.model import FindingStatus
from hoglint.plugins import REGISTRY, open_coverage
from hoglint.rules.base import RuleRun
from hoglint.rules.findings import FindingTable
from hoglint.selectors import State, Truth


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


# Why a claiming group that the level does not judge fails its claim.
NOT_JUDGED = "the level's select leaves the group out, so it cannot pass the level it claims"


@dataclass(frozen=False, slots=True)
class LevelResult:
    name: str
    passing: list[str] = field(default_factory=list)
    blocks: dict[str, list[Block]] = field(default_factory=dict)
    # group -> the file that claims the level for it, as the claiming input names it
    claims: dict[str, str] = field(default_factory=dict)
    # why the claims cannot be read; the run fails while it is set
    claim_error: str = ""

    @property
    def failing(self) -> Reasons:
        return Reasons(self.blocks)

    def failed_claims(self) -> list[tuple[str, list[str]]]:
        """(group, why it fails the level) of each group that claims the level and does not pass
        it, by group."""
        out = []
        for group in sorted(self.claims):
            if group in self.passing:
                continue
            out.append((group, self.failing[group] if group in self.blocks else [NOT_JUDGED]))
        return out

    @property
    def claim_failed(self) -> bool:
        return bool(self.claim_error) or any(group not in self.passing for group in self.claims)


class LevelEvaluator:
    def __init__(
        self, spec: LevelSpec, labeler: Labeler, runs: dict[str, RuleRun], repo_root: Path | None = None
    ) -> None:
        self.spec = spec
        self.labeler = labeler
        self.runs = runs
        self.repo_root = repo_root
        self.group_of = Memo(self.find_group)

    def find_group(self, subject: str) -> str | None:
        """The group of a finding's subject, read on the subject's module. A directory without
        `__init__.py` whose module reads no group, such as a layout root just below a package that
        is in no group, is read as the directory itself."""
        labeler = self.labeler
        value, _ = labeler.field_value(labeler.module_of_symbol(subject) or subject, None, self.spec.group_by)
        if value.state is not State.KNOWN and subject in labeler.namespaces.paths:
            value, _ = labeler.field_value(subject, None, self.spec.group_by)
        if value.state is not State.KNOWN:
            return None
        return label_text(value.data)

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
        if self.spec.claimed_by:
            self.claim(result)
        return result

    def claim(self, result: LevelResult) -> None:
        """The groups that claim the level through `claimed_by`: a module file of the group sits
        in a directory where the input is narrowed."""
        name = str(self.spec.claim_option("adapter"))
        adapter = REGISTRY.coverage_adapters[name]
        if self.repo_root is None:
            result.claim_error = f"no repo root to read the {name} inputs from"
            return
        options = {key: value for key, value in self.spec.claimed_by if key != "adapter"}
        try:
            handle = open_coverage(adapter, options, self.repo_root)
        except Exception as error:  # noqa: BLE001 - an unreadable claim fails the run, never passes it
            result.claim_error = f"cannot read the {name} inputs: {error}"
            return
        by_directory: dict[str, str | None] = {}
        unplaced: list[str] = []
        for module, path in sorted(self.labeler.graph.modules.items()):
            directory = posixpath.dirname(path)
            if directory not in by_directory:
                by_directory[directory] = adapter.narrowed(handle, directory)
            where = by_directory[directory]
            if where is None:
                continue
            group = self.group_of(module)
            if group is None:
                unplaced.append(path)
                continue
            result.claims.setdefault(group, where)
        if unplaced:
            result.claim_error = f"cannot tell the group of {len(unplaced)} narrowed files, such as {unplaced[0]}"

    def collect(self, rule_id: str, run: RuleRun, reasons: dict[str, list[Block]]) -> None:
        strict = rule_id in self.spec.strict
        within_group = rule_id in self.spec.within_group
        table = run.findings
        unknown = table.unknown
        # A ledgered finding blocks only when it is unknown or its rule is strict.
        for row in range(len(table)):
            status = table.status(row)
            if status is FindingStatus.ALLOWED or not (unknown[row] or strict or status is FindingStatus.NEW):
                continue
            why = "unknown" if unknown[row] else "strict, so the ledger does not excuse it" if strict else "new finding"
            identity = table.identity(row)
            groups = {self.group_of(subject) for subject in table.subjects(row)}
            if within_group and len(groups - {None}) > 1:
                continue
            for group in groups:
                if group in reasons:
                    reasons[group].append(Block(rule_id, why, identity, table=table, row=row))
        for item in run.incomplete:
            groups = {self.group_of(subject) for subject in item.subjects} if item.subjects else set(reasons)
            for group in groups:
                if group in reasons:
                    reasons[group].append(Block(rule_id, text=f"{rule_id}: incomplete: {item.reason}"))
