"""hoglint.exceptions.toml: reviewed allowances. An exception makes a use allowed, so it creates
no finding. It never removes an edge, so impact views and levels still see the coupling.

Each entry needs: rule, match (identity columns of that rule and their values), reason, owners,
scope (prod or test). review_by is optional. An entry that matches nothing is stale and fails.
A passed review_by is reported, without blocking unrelated changes.
"""

from __future__ import annotations

import tomllib
import datetime
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoglint.config.model import Config
from hoglint.model import Finding, FindingStatus
from hoglint.rules.base import RuleRun, contexts_of

REQUIRED = ("rule", "match", "reason", "owners", "scope")
ALLOWED_KEYS = frozenset({*REQUIRED, "review_by"})
SCOPES = ("prod", "test")


@dataclass(frozen=True, slots=True)
class ExceptionEntry:
    index: int
    rule: str
    match: tuple[tuple[int, str], ...]  # (identity column index, value)
    reason: str
    owners: tuple[str, ...]
    scope: str
    review_by: datetime.date | None = None

    def label(self) -> str:
        return f"exception #{self.index} ({self.rule})"


@dataclass(frozen=False, slots=True)
class ExceptionsFile:
    entries: list[ExceptionEntry] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @staticmethod
    def read(path: Path, config: Config) -> ExceptionsFile:
        if not path.is_file():
            return ExceptionsFile()
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as error:
            return ExceptionsFile(problems=[f"{path.name}: {error}"])
        return ExceptionsFile.parse(data, config, path.name)

    @staticmethod
    def parse(data: dict[str, Any], config: Config, name: str = "hoglint.exceptions.toml") -> ExceptionsFile:
        out = ExceptionsFile()
        if data.get("schema") != 1:
            out.problems.append(f"{name}: `schema = 1` is required")
        for index, raw in enumerate(data.get("exception", [])):
            entry = out.parse_entry(index, raw, config, name)
            if entry is not None:
                out.entries.append(entry)
        return out

    def parse_entry(self, index: int, raw: dict[str, Any], config: Config, name: str) -> ExceptionEntry | None:
        where = f"{name} exception #{index}"
        for key in raw:
            if key not in ALLOWED_KEYS:
                self.problems.append(f"{where}: unknown key {key!r}")
        missing = [key for key in REQUIRED if not raw.get(key)]
        if missing:
            self.problems.append(f"{where}: missing {', '.join(missing)}")
            return None
        rule = config.rules.get(raw["rule"])
        if rule is None:
            self.problems.append(f"{where}: unknown rule {raw['rule']!r}")
            return None
        if raw["scope"] not in SCOPES:
            self.problems.append(f"{where}: scope must be prod or test")
            return None
        match: list[tuple[int, str]] = []
        for column, value in dict(raw["match"]).items():
            if column not in rule.identity:
                self.problems.append(
                    f"{where}: {column!r} is not an identity column of {rule.id} ({', '.join(rule.identity)})"
                )
                return None
            match.append((rule.identity.index(column), str(value)))
        review_by = raw.get("review_by")
        if isinstance(review_by, str):
            review_by = datetime.date.fromisoformat(review_by)
        owners = raw["owners"]
        return ExceptionEntry(
            index,
            rule.id,
            tuple(match),
            str(raw["reason"]),
            tuple([owners] if isinstance(owners, str) else owners),
            raw["scope"],
            review_by,
        )


def in_scope(entry: ExceptionEntry, finding: Finding) -> bool:
    in_test = any("test" in contexts_of(edge) for edge in finding.witnesses)
    return in_test if entry.scope == "test" else not in_test


def matches(entry: ExceptionEntry, finding: Finding) -> bool:
    if entry.rule != finding.rule or not in_scope(entry, finding):
        return False
    return all(index < len(finding.identity) and finding.identity[index] == value for index, value in entry.match)


@dataclass(frozen=False, slots=True)
class ExceptionReport:
    stale: list[ExceptionEntry] = field(default_factory=list)
    expired: list[ExceptionEntry] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def apply_exceptions(runs: dict[str, RuleRun], exceptions: ExceptionsFile, today: datetime.date) -> ExceptionReport:
    """Marks allowed findings in place and reports entries that are stale or past review."""
    used: set[int] = set()
    for run in runs.values():
        updated: list[Finding] = []
        for finding in run.findings:
            entry = next((e for e in exceptions.entries if matches(e, finding)), None)
            if entry is None:
                updated.append(finding)
                continue
            used.add(entry.index)
            updated.append(dataclasses.replace(finding, status=FindingStatus.ALLOWED))
        run.findings = updated
    report = ExceptionReport(problems=list(exceptions.problems))
    for entry in exceptions.entries:
        if entry.index not in used:
            report.stale.append(entry)
        if entry.review_by is not None and entry.review_by < today:
            report.expired.append(entry)
    return report
