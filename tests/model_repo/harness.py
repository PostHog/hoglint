"""Checks the model repository with the real preset in the native engine and compares the result
with the reviewed expected file and the stored digests of the ten tables. `test_model_repo.py` and
`bin/model-repo` use it."""

from __future__ import annotations

import gc
import re
import json
import time
import shutil
import hashlib
import tomllib
import subprocess
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from hoglint import facts
from hoglint.core import analysis as core_analysis
from hoglint.ledger.envelope import Framework
from hoglint.native import analysis as native_analysis
from hoglint.native.parity import LIST_OUTPUTS, VALUE_OUTPUTS, Dump, read_lines

HERE = Path(__file__).resolve().parent
TREE = HERE / "repo"
SITE = HERE / "site"
EXPECTED = HERE / "expected.toml"
TABLES = HERE / "tables.toml"
CASES = HERE / "CASES.md"
PRESET = HERE.parents[1] / "presets" / "posthog.toml"
# Where the tree's root config finds the preset, as in the monorepo.
PRESET_IN_TREE = Path("tools/hoglint/presets/posthog.toml")
UNTIED = "?"
# A case id as the catalog writes it: two letters and two digits, as code.
CASE_ID = re.compile(r"`[A-Z]{2}[0-9]{2}`")
ANY_RULE = "*"
ANY_VERDICT = "*"
# The column of a findings row that says it exists because something did not resolve.
UNKNOWN_COLUMN = 10

EXPECTED_HEADER = """\
# The reviewed result of the model repository on the real preset: every finding and incomplete
# row, each tied to a case of CASES.md. Refresh it with `bin/model-repo --refresh`, then review
# the diff: a row with case "?" is new and must get a case (or the tree has a bug), a row that
# went is a verdict that changed. See docs/design/12-how-work-is-done.md, "The model repository".
"""
WANTED_HEADER = """\
# Wanted: verdicts that are decided and not built (or built and not firing) on this preset. Each
# is an expected failure of test_model_repo.py until the rule gives it. A wanted row matches a
# row of `rule` ("*" for any rule) with `verdict` ("*" for any: a finding or an incomplete row)
# whose identity holds every string of `match`.
"""
LEVELS_HEADER = "# Levels: the products that pass and fail each level.\n"
TABLES_HEADER = """\
# The ten parity tables of the model repository as the native engine dumps them: per table the
# row count and a digest of its bytes. A change that moves any row of any table changes a digest.
# `bin/model-repo --refresh` rewrites it with expected.toml; `bin/model-repo --keep` keeps the dump
# to read the rows. See docs/design/12-how-work-is-done.md, "The model repository".
"""


def stub_site_packages() -> Path:
    return SITE


@contextmanager
def stub_site() -> Iterator[None]:
    """The engine reads the stub `django` and `rest_framework` sources as site-packages, so the
    model repository needs neither installed. Each module that looks site-packages up is patched
    where it holds the function."""
    patches: list[tuple[object, str, object]] = [
        (facts, "site_packages", stub_site_packages),
        (core_analysis, "site_packages", stub_site_packages),
        (native_analysis, "site_packages", stub_site_packages),
        (Framework, "site", staticmethod(stub_site_packages)),
    ]
    saved = [(owner, name, vars(owner)[name]) for owner, name, _ in patches]
    for owner, name, replacement in patches:
        setattr(owner, name, replacement)
    try:
        yield
    finally:
        for owner, name, original in saved:
            setattr(owner, name, original)


def quoted(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def quoted_list(items: list[str] | tuple[str, ...]) -> str:
    return "[" + ", ".join(quoted(item) for item in items) + "]"


@dataclass(frozen=True, order=True)
class Row:
    """One result row: a finding by its identity (verdict "finding", or "unknown" when it exists
    because something did not resolve), or an incomplete item by its key."""

    rule: str
    verdict: str
    identity: tuple[str, ...]

    def render(self) -> str:
        return f"{self.verdict} {' | '.join(self.identity)}"


@dataclass(frozen=True)
class Result:
    """What the engine gave: every row with its count, and per level its passing and failing products."""

    rows: Counter[Row]
    levels: dict[str, dict[str, list[str]]]


def copy_tree(repo: Path) -> None:
    """A copy of the tree at `repo`, with the real preset where the tree's root config finds it,
    staged in a new git repo."""
    shutil.copytree(TREE, repo)
    preset = repo / PRESET_IN_TREE
    preset.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(PRESET, preset)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)


def dump_tree(repo: Path, engine: str, out: Path, work: Path) -> None:
    """The ten parity tables of a copy of the tree, with the stub site-packages."""
    with stub_site():
        try:
            Dump(repo, engine, out, work).run()
        finally:
            gc.enable()


class Dumps:
    """One check of a copy of the tree in the native engine, dumped as the ten parity tables."""

    def __init__(self, work: Path) -> None:
        self.work = work
        self.repo = work / "repo"
        self.out = work / "native"
        self.seconds = 0.0

    def run(self) -> None:
        copy_tree(self.repo)
        scratch = self.work / "work-native"
        scratch.mkdir()
        started = time.perf_counter()
        dump_tree(self.repo, "native", self.out, scratch)
        self.seconds = round(time.perf_counter() - started, 2)

    def result(self) -> Result:
        out = self.out
        rows: Counter[Row] = Counter()
        for finding in read_lines(out / "findings.jsonl"):
            verdict = "unknown" if finding[UNKNOWN_COLUMN] else "finding"
            rows[Row(finding[0], verdict, tuple(finding[1]))] += 1
        for item in read_lines(out / "incomplete.jsonl"):
            rows[Row(item[0], "incomplete", (item[1],))] += 1
        levels = {}
        for name, (passing, failing) in json.loads((out / "levels.json").read_text()).items():
            levels[name] = {"passing": sorted(passing), "failing": sorted(product for product, _ in failing)}
        return Result(rows, levels)


@dataclass(frozen=True)
class Table:
    """One parity table as stored: its row count and the digest of its bytes."""

    rows: int
    digest: str

    @staticmethod
    def of(data: bytes, rows: int) -> Table:
        return Table(rows, hashlib.blake2b(data, digest_size=16).hexdigest())


def dumped_tables(out: Path) -> dict[str, Table]:
    """The ten tables of a dump: each table file's rows (a value table is one row) and digest."""
    tables = {}
    for name in LIST_OUTPUTS:
        data = (out / f"{name}.jsonl").read_bytes()
        tables[name] = Table.of(data, data.count(b"\n"))
    for name in VALUE_OUTPUTS:
        tables[name] = Table.of((out / f"{name}.json").read_bytes(), 1)
    return tables


def read_tables(path: Path = TABLES) -> dict[str, Table]:
    data = tomllib.loads(path.read_text())
    return {name: Table(entry["rows"], entry["digest"]) for name, entry in data.items()}


def render_tables(tables: dict[str, Table]) -> str:
    out = [TABLES_HEADER]
    for name, table in tables.items():
        out.append(f"\n[{quoted(name)}]\nrows = {table.rows}\ndigest = {quoted(table.digest)}\n")
    return "".join(out)


def changed_tables(stored: dict[str, Table], found: dict[str, Table]) -> list[str]:
    """One line per table whose rows or bytes differ from the stored ones."""
    lines = []
    for name in sorted(set(stored) | set(found)):
        before, after = stored.get(name), found.get(name)
        if before == after:
            continue
        rows = f"{before.rows if before else '-'} -> {after.rows if after else '-'} rows"
        lines.append(
            f"table {name}: {rows}, digest {before.digest if before else '-'} -> {after.digest if after else '-'}"
        )
    return lines


@dataclass(frozen=True)
class ExpectedRow:
    row: Row
    count: int
    case: str

    def render(self) -> str:
        lines = [
            "[[row]]",
            f"case = {quoted(self.case)}",
            f"rule = {quoted(self.row.rule)}",
            f"verdict = {quoted(self.row.verdict)}",
            f"identity = {quoted_list(self.row.identity)}",
        ]
        if self.count != 1:
            lines.append(f"count = {self.count}")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class WantedRow:
    """A verdict that a decided rule gives once it is built: a row of `rule` ("*" for any rule)
    with `verdict` ("*" for any) whose identity holds every string of `match`."""

    case: str
    decision: str
    rule: str
    verdict: str
    match: tuple[str, ...]

    def found_in(self, rows: Counter[Row]) -> bool:
        for row in rows:
            if self.rule not in (ANY_RULE, row.rule) or self.verdict not in (ANY_VERDICT, row.verdict):
                continue
            text = " | ".join(row.identity)
            if all(part in text for part in self.match):
                return True
        return False

    def render(self) -> str:
        return (
            f"[[wanted]]\ncase = {quoted(self.case)}\ndecision = {quoted(self.decision)}\n"
            f"rule = {quoted(self.rule)}\nverdict = {quoted(self.verdict)}\nmatch = {quoted_list(self.match)}\n"
        )


@dataclass(frozen=False)
class Expected:
    """The reviewed expected file."""

    rows: list[ExpectedRow] = field(default_factory=list)
    wanted: list[WantedRow] = field(default_factory=list)
    levels: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    @staticmethod
    def read(path: Path = EXPECTED) -> Expected:
        data = tomllib.loads(path.read_text())
        expected = Expected()
        for entry in data.get("row", []):
            row = Row(entry["rule"], entry["verdict"], tuple(entry["identity"]))
            expected.rows.append(ExpectedRow(row, entry.get("count", 1), entry["case"]))
        for entry in data.get("wanted", []):
            wanted = WantedRow(entry["case"], entry["decision"], entry["rule"], entry["verdict"], tuple(entry["match"]))
            expected.wanted.append(wanted)
        for name, level in data.get("levels", {}).items():
            expected.levels[name] = {"passing": level["passing"], "failing": level["failing"]}
        return expected

    def counter(self) -> Counter[Row]:
        return Counter({entry.row: entry.count for entry in self.rows})

    def cases(self) -> dict[Row, str]:
        return {entry.row: entry.case for entry in self.rows}

    def refreshed(self, result: Result) -> Expected:
        """The expected file made from `result`: rows that stay keep their case, new rows get "?"."""
        cases = self.cases()
        rows = [ExpectedRow(row, count, cases.get(row, UNTIED)) for row, count in result.rows.items()]
        return Expected(rows, self.wanted, result.levels)

    def render(self) -> str:
        out = [EXPECTED_HEADER]
        rule = None
        for entry in sorted(self.rows, key=lambda e: (e.row.rule, e.case, e.row)):
            if entry.row.rule != rule:
                rule = entry.row.rule
                out.append(f"# {rule}\n\n")
            out.append(entry.render() + "\n")
        out.append("\n" + WANTED_HEADER)
        out.extend("\n" + wanted.render() for wanted in self.wanted)
        out.append("\n" + LEVELS_HEADER)
        for name, level in sorted(self.levels.items()):
            out.append(f"\n[levels.{name}]\npassing = {quoted_list(level['passing'])}\n")
            out.append(f"failing = {quoted_list(level['failing'])}\n")
        return "".join(out)


def catalog_rows(path: Path = CASES) -> list[list[str]]:
    """The cells of each case row of the catalog: rows whose first cell is a case id."""
    rows = []
    for line in path.read_text().splitlines():
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if cells and CASE_ID.fullmatch(cells[0]):
            rows.append(cells)
    return rows


def case_ids(path: Path = CASES) -> set[str]:
    return {cells[0].strip("`") for cells in catalog_rows(path)}


class Comparison:
    """The result against the expected file, readable per rule and case."""

    def __init__(self, expected: Expected, result: Result) -> None:
        self.expected = expected
        self.result = result

    def changed_rows(self) -> list[str]:
        """One line per row whose count differs, grouped by rule, with the case it is tied to."""
        want = self.expected.counter()
        got = self.result.rows
        cases = self.expected.cases()
        lines = []
        rule = None
        for row in sorted(set(want) | set(got)):
            before, after = want.get(row, 0), got.get(row, 0)
            if before == after:
                continue
            if row.rule != rule:
                rule = row.rule
                lines.append(f"{rule}:")
            if after == 0:
                change = "gone"
            elif before == 0:
                change = "new"
            else:
                change = f"count {before} -> {after}"
            lines.append(f"  {cases.get(row, UNTIED):6} {change:16} {row.render()}")
        return lines

    def changed_levels(self) -> list[str]:
        lines = []
        for name in sorted(set(self.expected.levels) | set(self.result.levels)):
            before, after = self.expected.levels.get(name), self.result.levels.get(name)
            if before != after:
                lines.append(f"level {name}: expected {before}, got {after}")
        return lines

    def untied(self) -> list[str]:
        """Expected rows whose case is not in the catalog."""
        known = case_ids()
        return [
            f"{entry.case} {entry.row.rule} {entry.row.render()}"
            for entry in self.expected.rows
            if entry.case not in known
        ]

    def wanted(self) -> list[tuple[WantedRow, bool]]:
        return [(wanted, wanted.found_in(self.result.rows)) for wanted in self.expected.wanted]

    def equal(self) -> bool:
        return not (self.changed_rows() or self.changed_levels() or self.untied())

    def render(self) -> str:
        lines = [*self.changed_rows(), *self.changed_levels()]
        untied = self.untied()
        if untied:
            lines.append("rows tied to no case of CASES.md:")
            lines.extend(f"  {line}" for line in untied)
        return "\n".join(lines) if lines else "every row as expected"
