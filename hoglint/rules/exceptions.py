"""hoglint.exceptions.toml: reviewed allowances. An exception makes a use allowed, so it creates
no finding. It never removes an edge, so impact views and levels still see the coupling.

Each entry needs: rule, match (identity columns of that rule and their values), reason, owners,
scope (prod or test). review_by is optional. An entry that matches nothing is stale and fails.
A passed review_by is reported, without blocking unrelated changes.
"""

from __future__ import annotations

import tomllib
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoglint.config.model import Config
from hoglint.rules.base import RuleRun, contexts_of
from hoglint.selectors import render_data

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


def render_match(value: Any) -> str | None:
    """A match value rendered the way identity columns render, or None when it cannot match one."""
    if isinstance(value, (bool, str, int, float)):
        return render_data(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return render_data(value)
    return None


@dataclass(frozen=False, slots=True)
class ExceptionsFile:
    """`raw` keeps the entries as written, so compare can list changed policy without a second read."""

    entries: list[ExceptionEntry] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    raw: list[dict[str, Any]] = field(default_factory=list)

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
        raw_entries = data.get("exception", [])
        if not isinstance(raw_entries, list):
            out.problems.append(f"{name}: `exception` must be a list of [[exception]] tables")
            return out
        out.raw = [raw for raw in raw_entries if isinstance(raw, dict)]
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, dict):
                out.problems.append(f"{name} exception #{index}: must be a table")
                continue
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
        if not isinstance(raw["rule"], str):
            self.problems.append(f"{where}: rule must be a string")
            return None
        rule = config.rules.get(raw["rule"])
        if rule is None:
            self.problems.append(f"{where}: unknown rule {raw['rule']!r}")
            return None
        if raw["scope"] not in SCOPES:
            self.problems.append(f"{where}: scope must be prod or test")
            return None
        reported = len(self.problems)
        match = self.parse_match(where, raw["match"], rule.id, rule.identity)
        review_by = self.parse_review_by(where, raw.get("review_by"))
        owners = self.parse_owners(where, raw["owners"])
        if len(self.problems) > reported:
            return None
        return ExceptionEntry(index, rule.id, match, str(raw["reason"]), owners, raw["scope"], review_by)

    def parse_match(self, where: str, raw: Any, rule_id: str, identity: tuple[str, ...]) -> tuple[tuple[int, str], ...]:
        if not isinstance(raw, dict):
            self.problems.append(
                f'{where}: match must be a table of identity columns, such as {{ "src.module" = "..." }}'
            )
            return ()
        match: list[tuple[int, str]] = []
        for column, value in raw.items():
            if column not in identity:
                self.problems.append(
                    f"{where}: {column!r} is not an identity column of {rule_id} ({', '.join(identity)})"
                )
                continue
            rendered = render_match(value)
            if rendered is None:
                self.problems.append(
                    f"{where}: match value for {column!r} must be a string, number, bool or list of strings"
                )
                continue
            match.append((identity.index(column), rendered))
        return tuple(match)

    def parse_review_by(self, where: str, raw: Any) -> datetime.date | None:
        if raw is None:
            return None
        if isinstance(raw, datetime.datetime):
            return raw.date()
        if isinstance(raw, datetime.date):
            return raw
        if isinstance(raw, str):
            try:
                return datetime.date.fromisoformat(raw)
            except ValueError:
                pass
        self.problems.append(f"{where}: review_by must be a date such as 2026-12-31, found {raw!r}")
        return None

    def parse_owners(self, where: str, raw: Any) -> tuple[str, ...]:
        owners = [raw] if isinstance(raw, str) else raw
        if not isinstance(owners, list) or not all(isinstance(owner, str) for owner in owners):
            self.problems.append(f"{where}: owners must be a string or a list of strings")
            return ()
        return tuple(owners)


def identity_matches(entry: ExceptionEntry, identity: tuple[str, ...]) -> bool:
    return all(index < len(identity) and identity[index] == value for index, value in entry.match)


def in_scope(entry: ExceptionEntry, in_test: bool) -> bool:
    return in_test if entry.scope == "test" else not in_test


@dataclass(frozen=False, slots=True)
class ExceptionReport:
    stale: list[ExceptionEntry] = field(default_factory=list)
    expired: list[ExceptionEntry] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def apply_exceptions(runs: dict[str, RuleRun], exceptions: ExceptionsFile, today: datetime.date) -> ExceptionReport:
    """Marks the rows an exception allows and reports entries that are stale or past review."""
    used: set[int] = set()
    by_rule: dict[str, list[ExceptionEntry]] = {}
    for entry in exceptions.entries:
        by_rule.setdefault(entry.rule, []).append(entry)
    for rule, run in runs.items():
        entries = by_rule.get(rule, [])
        if not entries:
            continue
        table = run.findings
        # An entry matches a row by its identity first: only rows it could allow read their edges.
        candidates = [[e for e in entries if identity_matches(e, identity)] for identity in table.identities]
        for row, place in enumerate(table.identity_at):
            matching = candidates[place]
            if not matching:
                continue
            in_test = any("test" in contexts_of(edge) for edge in table.witnesses(row))
            entry = next((e for e in matching if in_scope(e, in_test)), None)
            if entry is None:
                continue
            used.add(entry.index)
            table.allow(row)
    report = ExceptionReport(problems=list(exceptions.problems))
    for entry in exceptions.entries:
        if entry.index not in used:
            report.stale.append(entry)
        if entry.review_by is not None and entry.review_by < today:
            report.expired.append(entry)
    return report
