"""The checks `bin/prove` runs at real size, on snapshots and on the replay sample. Each works on
copies it makes itself and never changes a snapshot or a sample in place."""

from __future__ import annotations

import re
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from hoglint.native.parity import compare_dumps, row_key, row_texts

from prove.base import PRESET_IN_TREE, Check, Context, Native, Outcome, clone
from prove.local import build

# The tables whose rows are grouped (by rule, or by edge kind) in the row diff against the base.
GROUPED_TABLES = ("findings", "incomplete", "edges")


class Engine:
    """One side of a real-size comparison: an engine root, its native copy, and its preset."""

    def __init__(self, label: str, root: Path, native: Native) -> None:
        self.label = label
        self.root = root
        self.native = native

    @property
    def preset(self) -> Path:
        return self.root / "presets" / "posthog.toml"


class BaseBuild:
    """An export of the base rev, built into its own target directory."""

    def __init__(self, rev: str, target: Path) -> None:
        self.rev = rev
        self.target = target

    def run(self, context: Context) -> Engine:
        root = context.scratch / "base"
        root.mkdir(parents=True)
        archive = subprocess.Popen(["git", "-C", str(context.root), "archive", self.rev], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", str(root)], stdin=archive.stdout, check=True)
        if archive.wait() != 0:
            raise RuntimeError(f"git archive {self.rev} failed")
        log = context.log("base-build")
        binary = build(root, log, context.env("base-build", {"CARGO_TARGET_DIR": str(self.target)}))
        if binary is None:
            raise RuntimeError(f"bin/build of {self.rev} failed: {log}")
        return Engine(f"base {self.rev}", root, Native.copy(binary, context.scratch / "native" / "base"))


class Snapshot:
    """A copy of a snapshot with an engine's preset laid over the old one it carries."""

    def __init__(self, source: Path, name: str) -> None:
        self.source = source
        self.name = name

    def laid_copy(self, context: Context, engine: Engine) -> Path:
        copy = context.scratch / "snapshots" / f"{self.name}-{engine.label.split()[0]}"
        clone(self.source, copy)
        shutil.copyfile(engine.preset, copy / PRESET_IN_TREE)
        return copy


class RealDumps:
    """Dumps of one engine over one snapshot copy, with the Python given for real-size runs."""

    def __init__(self, context: Context, python: Path, log: Path) -> None:
        self.context = context
        self.python = python
        self.log = log

    def dump(self, engine: Engine, kind: str, repo: Path, label: str) -> Path:
        out = self.context.scratch / "dumps" / label
        work = self.context.scratch / "dumps" / f"work-{label}"
        argv = [str(self.python), "-m", "hoglint.native.parity", "dump", "--engine", kind]
        argv += ["--repo", str(repo), "--out", str(out), "--work", str(work)]
        env = self.context.env(f"dump-{label}", {**engine.native.env(), "PYTHONPATH": str(engine.root)})
        code = self.context.run(self.log, argv, env, cwd=engine.root)
        shutil.rmtree(work, ignore_errors=True)
        if code != 0:
            raise RuntimeError(f"the {label} dump failed: {self.log}")
        return out


class MovedRows:
    """The rows of a table that came and went between two dumps, as multisets, grouped by the
    first column (a rule, or an edge kind). Rows are counted by digest, so a table of a million
    rows fits in memory; the moved rows are then written out from a second read."""

    def __init__(self, table: str, base: Path, head: Path) -> None:
        self.table = table
        self.base = base / f"{table}.jsonl"
        self.head = head / f"{table}.jsonl"
        self.balance: Counter[bytes] = Counter()
        self.counts: dict[str, Counter[str]] = {"base": Counter(), "head": Counter()}

    def count(self) -> None:
        for side, path, sign in (("base", self.base, -1), ("head", self.head, 1)):
            for text in row_texts(path):
                self.balance[row_key(text)] += sign
                self.counts[side][json.loads(text)[0]] += 1
        self.balance = Counter({key: count for key, count in self.balance.items() if count})

    def write(self, out: Path) -> dict[str, Counter[str]]:
        """Writes `<table>-came.jsonl` and `<table>-went.jsonl`; returns the counts per group."""
        moved = {"came": Counter[str](), "went": Counter[str]()}
        left = Counter(self.balance)
        for word, path, sign in (("went", self.base, -1), ("came", self.head, 1)):
            with open(out / f"{self.table}-{word}.jsonl", "w") as handle:
                for text in row_texts(path):
                    key = row_key(text)
                    if left[key] * sign <= 0:
                        continue
                    left[key] -= sign
                    handle.write(text + "\n")
                    moved[word][json.loads(text)[0]] += 1
        return moved


def level_moves(base: Path, head: Path) -> list[str]:
    """Per level, the products that pass at the head and not at the base, and the other way."""
    before = json.loads((base / "levels.json").read_text())
    after = json.loads((head / "levels.json").read_text())
    lines = []
    for level in sorted(set(before) | set(after)):
        passing_before = set(before.get(level, [[], []])[0])
        passing_after = set(after.get(level, [[], []])[0])
        gained, lost = sorted(passing_after - passing_before), sorted(passing_before - passing_after)
        if gained or lost:
            lines.append(f"level {level}: gains {gained}, loses {lost}")
    return lines


class RowReport:
    """What moved from the base to the head on one snapshot, written to `out/report.txt`."""

    def __init__(self, base: Path, head: Path, out: Path) -> None:
        self.base = base
        self.head = head
        self.out = out

    def write(self) -> tuple[bool, str]:
        self.out.mkdir(parents=True, exist_ok=True)
        lines = []
        moved_total = 0
        for table in GROUPED_TABLES:
            rows = MovedRows(table, self.base, self.head)
            rows.count()
            moved = rows.write(self.out)
            lines.append(f"{table}, by {'kind' if table == 'edges' else 'rule'}: base, head, came, went")
            for group in sorted(set(rows.counts["base"]) | set(rows.counts["head"])):
                base, head = rows.counts["base"][group], rows.counts["head"][group]
                came, went = moved["came"][group], moved["went"][group]
                mark = "   <-- moves" if came or went else ""
                lines.append(f"  {group:40} {base:9} {head:9} {came:+7} {-went:+7}{mark}")
            moved_total += sum(moved["came"].values()) + sum(moved["went"].values())
        levels = level_moves(self.base, self.head)
        lines.extend(levels or ["levels: no product gains or loses a level"])
        diffs = compare_dumps(self.base, self.head)
        lines.append("ten tables:")
        lines.extend(diff.render() for diff in diffs)
        (self.out / "report.txt").write_text("\n".join(lines) + "\n")
        unequal = [diff.name for diff in diffs if not diff.equal]
        if not unequal:
            return True, "all ten tables equal"
        return False, f"{moved_total} rows moved, {len(levels)} levels moved; tables differ: {', '.join(unequal)}"


class RealSize(Check):
    """One snapshot: the head (and the base, when one is given) dumped in the native engine and
    compared row by row; with `parity`, a Python-engine dump of the head against the native one."""

    def __init__(self, snapshot: Snapshot, python: Path, head: Engine, base: Engine | None, parity: bool) -> None:
        self.snapshot = snapshot
        self.python = python
        self.head = head
        self.base = base
        self.parity = parity
        self.name = f"real size {snapshot.name}"

    def run(self, context: Context) -> list[Outcome]:
        log = context.log(f"real-{self.snapshot.name}")
        dumps = RealDumps(context, self.python, log)
        head_tree = self.snapshot.laid_copy(context, self.head)
        head = dumps.dump(self.head, "native", head_tree, f"{self.snapshot.name}-head-native")
        outcomes = []
        if self.base is not None:
            base_tree = self.snapshot.laid_copy(context, self.base)
            base = dumps.dump(self.base, "native", base_tree, f"{self.snapshot.name}-base-native")
            shutil.rmtree(base_tree)
            out = context.logs / f"real-{self.snapshot.name}"
            passed, line = RowReport(base, head, out).write()
            outcomes.append(Outcome(f"{self.name}: {self.base.label} vs head", passed, f"{line} ({out})"))
            shutil.rmtree(base)
        if self.parity:
            python = dumps.dump(self.head, "python", head_tree, f"{self.snapshot.name}-head-python")
            unequal = [diff for diff in compare_dumps(python, head) if not diff.equal]
            with open(log, "a") as handle:
                handle.write("\n".join(diff.render() for diff in compare_dumps(python, head)) + "\n")
            line = "python = native on all ten tables" if not unequal else f"differ: {[d.name for d in unequal]}"
            outcomes.append(Outcome(f"{self.name}: parity", not unequal, line))
            shutil.rmtree(python)
        shutil.rmtree(head)
        shutil.rmtree(head_tree)
        return outcomes


# The verdict words each replay mode gives a pair that passes.
GOOD_WORDS = {"today": ("EQUAL", "MEMO"), "traces": ("HELD",)}
COUNTS = re.compile(r"truth (\d+) predicted (\d+)")


class ReplayRun(Check):
    """The replay sample in one mode, the head's preset laid over every tree. Prints the pairs that
    are not EQUAL (today) or not HELD (the certificate); the certificate's truth and predicted
    counts are written to the log directory and compared with a stored file."""

    def __init__(self, sample: Path, python: Path, mode: str, stored: Path | None) -> None:
        self.sample = sample
        self.python = python
        self.mode = mode
        self.stored = stored
        self.name = f"replay {mode}"

    def pair_files(self) -> list[Path]:
        return sorted(self.sample.glob("pairs-*.tsv"))

    def pair_names(self) -> set[str]:
        names = set()
        for path in self.pair_files():
            names.update(line.split("\t")[0] for line in path.read_text().splitlines() if line.strip())
        return names

    def run(self, context: Context) -> list[Outcome]:
        log = context.log(f"replay-{self.mode}")
        repo = context.scratch / f"replay-{self.mode}" / "repo"
        clone(self.sample / "repo", repo)
        argv = [str(self.python), "-m", "hoglint.native.replay", "pairs", "--repo", str(repo)]
        for path in self.pair_files():
            argv += ["--pairs", str(path)]
        argv += ["--work", str(repo.parent / "work"), "--mode", self.mode]
        argv += ["--lay", f"{PRESET_IN_TREE}={context.root / 'presets' / 'posthog.toml'}"]
        env = context.env(f"replay-{self.mode}", {"PYTHONPATH": str(context.root)})
        code = context.run(log, argv, env)
        shutil.rmtree(repo.parent, ignore_errors=True)
        statuses, counts = self.read(log)
        bad = sorted(f"{name} {word}" for name, word in statuses.items() if word not in GOOD_WORDS[self.mode])
        line = f"{len(statuses)} pairs, {len(statuses) - len(bad)} {'/'.join(GOOD_WORDS[self.mode])}"
        if bad:
            line += f"; not: {', '.join(bad)}"
        if self.mode == "traces":
            line += self.compare_counts(context, counts)
        return [Outcome(self.name, code == 0 and not bad and bool(statuses), line)]

    def read(self, log: Path) -> tuple[dict[str, str], dict[str, tuple[int, int]]]:
        """Each pair's verdict word and (truth, predicted) counts, from the replay's pair lines."""
        names = self.pair_names()
        statuses: dict[str, str] = {}
        counts: dict[str, tuple[int, int]] = {}
        for text in log.read_text(errors="replace").splitlines():
            parts = text.split("  ")
            if len(parts) < 2 or parts[0] not in names:
                continue
            statuses[parts[0]] = parts[1]
            found = COUNTS.search(text)
            if found:
                counts[parts[0]] = (int(found.group(1)), int(found.group(2)))
        return statuses, counts

    def compare_counts(self, context: Context, counts: dict[str, tuple[int, int]]) -> str:
        written = context.logs / "replay-counts.tsv"
        written.write_text("".join(f"{name}\t{t}\t{p}\n" for name, (t, p) in sorted(counts.items())))
        if self.stored is None:
            return f"; counts in {written} (no stored file to compare)"
        stored = {}
        for text in self.stored.read_text().splitlines():
            name, truth, predicted = text.split("\t")
            stored[name] = (int(truth), int(predicted))
        changed = [
            f"{name} {stored.get(name)} -> {counts.get(name)}"
            for name in sorted(set(stored) | set(counts))
            if stored.get(name) != counts.get(name)
        ]
        if not changed:
            return "; truth and predicted counts as stored"
        with open(context.log(f"replay-{self.mode}"), "a") as handle:
            handle.write("counts changed against the stored file:\n" + "\n".join(changed) + "\n")
        return f"; counts changed in {len(changed)} pairs: {'; '.join(changed)}"
