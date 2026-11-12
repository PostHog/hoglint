"""Which files' results a commit can change, from the parent tree's traces (stage S5).

A check with `HOGLINT_LINK_TRACE=1` records, for every analyzed file, what its edges read from
other files at the grain of (module, name), and every repo file's interface: a digest per name of
what other files can read of it (`Link.write_traces`). Given the parent tree's dump and the files
a commit changed, the prediction runs in two steps, as a check that reuses stored answers would:

1. The link. The changed files' interfaces of parent and child give the (module, name) keys that
   moved; an added or deleted file moves its whole module, the existence of every module above
   it and the labels of a directory without `__init__.py` that holds it. A file whose trace reads a moved key is predicted, with every changed file and every file
   of a changed module.
2. What rule rows read. The predicted files are recomputed (here: their rows are taken from the
   child's dump), which tells whose symbol facts, class ancestry and labels really moved. A file
   whose edges name such a symbol, or whose rule rows read such a module's labels, is predicted
   too. Labels of every module are recomputed when a file a label provider declares changed.

Rules folded over packages or the whole graph rerun when what they read moved: any predicted
file's edges outside their positions, any label, any symbol fact, or a declared side input.

What it does not see: the reads of the packs. Trace recording ends when the link has made its
edges, before the packs run, while the truth set holds pack edges by the file they sit in. A pack
edge can depend on what it read of another file, such as a function body that an interface
digest leaves out: a helper gains a call that reaches another product's wiring, and the drives
pack makes a new edge in an unchanged test file that calls the helper, while no key of the
helper's interface moves. That test file is not predicted. The pack memo
(hoglint/core/memo.py) traces the reads of each pack item, but nothing joins those reads to this
prediction yet.

This module is generic: it knows trace kinds, not any repo's names.

Usage:
  python -m hoglint.native.predict summary <traces.jsonl>
      counts and sizes of one tree's traces and interfaces
  python -m hoglint.native.predict show <traces.jsonl> <path>
      what one file read and why, and the interface of that file
"""

from __future__ import annotations

import sys
import json
import argparse
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoglint.config.schema import FOLDING_VIEWS
from hoglint.core.modules import longest_prefix
from hoglint.ledger.envelope import ConfigInputs
from hoglint.ledger.inputs import InputPatterns, RunInputs
from hoglint.native.normal import FIELDS
from hoglint.native.parity import read_rows
from hoglint.rules.engine import primitive_of

TRACES = "traces.jsonl"
TRACE_CONFIG = "trace-config.json"
# Interface entries that are no Python name (`trace::STARS` and the others in Rust).
STARS, LAZY, NAMES, LINES = "<stars>", "<lazy>", "<names>", "<lines>"
# Read kinds whose key is (module, name) itself.
NAME_KINDS = frozenset({"found", "missing", "class", "def", "alias", "target", "spelled"})
# Read kinds that name a symbol a rule row or a pack reads the facts of.
SYMBOL_KINDS = frozenset({"target", "spelled"})
# Where an edge row holds each field (`normal.FIELDS`).
KIND, PATH, MODULE, SURFACE, TARGETS, RESOLUTION, ATTRS = (
    FIELDS.index(name) for name in ("kind", "path", "module", "surface", "targets", "resolution", "attrs")
)
# The fields of an edge's place, which move with every edit above it.
PLACE_FIELDS = frozenset({"path", "line", "column"})


def module_ancestors(module: str) -> list[str]:
    """`a.b.c` -> `a`, `a.b`: the packages a module sits in."""
    parts = module.split(".")
    return [".".join(parts[:end]) for end in range(1, len(parts))]


@dataclass(frozen=True)
class TraceConfig:
    """What a run's config says about its inputs, written next to its trace dump: the patterns of
    the files label providers and coverage adapters read, the policy files (every config file of
    the repo, each rule's `classify` file and each pack's policy files), and the rules whose rows
    fold many edges."""

    side: tuple[str, ...]
    policy: tuple[str, ...]
    folded: tuple[str, ...]

    @staticmethod
    def of(config: Any) -> TraceConfig:
        folded = []
        for spec in config.rules.values():
            if primitive_of(spec).FOLDS or spec.view in FOLDING_VIEWS:
                folded.append(spec.id)
        policy = ConfigInputs.of(config).predicted_policy()
        return TraceConfig(RunInputs.of(config).side(), policy, tuple(sorted(folded)))

    def write(self, out: Path) -> None:
        data = {"side": list(self.side), "policy": list(self.policy), "folded": list(self.folded)}
        (out / TRACE_CONFIG).write_text(json.dumps(data))

    @staticmethod
    def read(out: Path) -> TraceConfig:
        data = json.loads((out / TRACE_CONFIG).read_text())
        return TraceConfig(tuple(data["side"]), tuple(data["policy"]), tuple(data["folded"]))


def write_trace_dump(link: Any, config: Any, out: Path) -> None:
    """The link's traces and interfaces, and the config's inputs, into `out`."""
    TraceConfig.of(config).write(out)
    link.write_traces(str(out / TRACES))


@dataclass(frozen=True)
class Read:
    """What a file read of one (module, name), with a bit per kind that read it."""

    module: str
    name: str
    kinds: int


class TraceDump:
    """One tree's dump: the traces of its analyzed files and the interfaces of its repo files.
    `interfaces_of` limits the interfaces kept to some paths; `traces` False skips the traces."""

    def __init__(self, path: Path, interfaces_of: set[str] | None = None, traces: bool = True) -> None:
        self.kinds: list[str] = []
        self.traces: dict[str, tuple[str, list[Read]]] = {}
        self.interfaces: dict[str, tuple[str, dict[str, str]]] = {}
        self.modules: dict[str, str] = {}
        # What each export module hands out by import, as a row rule rows read through `dst.exported`.
        self.hand_outs: dict[str, list[Any]] = {}
        for line in path.open():
            row = json.loads(line)
            if row[0] == "kinds":
                self.kinds = row[1]
            elif row[0] == "trace":
                _, file, module, reads = row
                self.modules[file] = module
                if traces:
                    self.traces[file] = (module, [Read(m, n, mask) for m, n, mask in reads])
            elif row[0] == "handout":
                self.hand_outs[row[1]] = row[2:]
            elif row[0] == "interface":
                _, file, module, digests = row
                self.modules[file] = module
                if interfaces_of is None or file in interfaces_of:
                    self.interfaces[file] = (module, dict(digests))

    def bit(self, kind: str) -> int:
        return 1 << self.kinds.index(kind)

    def mask(self, kinds: Iterable[str]) -> int:
        out = 0
        for kind in kinds:
            out |= self.bit(kind)
        return out


def packages(dump: TraceDump) -> set[str]:
    """Every module of a tree and every package above one: what exists in its module index."""
    out: set[str] = set()
    for module in dump.modules.values():
        out.add(module)
        out.update(module_ancestors(module))
    return out


@dataclass(frozen=False)
class Changes:
    """What a commit moved at the link's grain: (module, name) keys, whole modules (a file added
    or deleted), the changed paths, and the modules of the changed files."""

    keys: set[tuple[str, str]] = field(default_factory=set)
    whole: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    modules: set[str] = field(default_factory=set)
    # The changed paths either tree analyzes or reads an interface of.
    known: set[str] = field(default_factory=set)

    @staticmethod
    def of(parent: TraceDump, child: TraceDump, paths: set[str]) -> Changes:
        """From the interfaces of the changed files in both trees. A package above an added or
        deleted file moves only when it exists in one tree and not the other. A directory without
        `__init__.py` that holds an added or deleted file moves whole: its labels are those its
        files read."""
        out = Changes(paths=set(paths))
        out.known = {path for path in paths if path in parent.modules or path in child.modules}
        existence = packages(parent) ^ packages(child)
        with_file = set(parent.modules.values()) | set(child.modules.values())
        for path in sorted(paths):
            before = parent.interfaces.get(path)
            after = child.interfaces.get(path)
            if before is not None and after is not None:
                out.modules.add(after[0])
                names = set(before[1]) | set(after[1])
                out.keys.update((after[0], name) for name in names if before[1].get(name) != after[1].get(name))
                continue
            found = after if after is not None else before
            if found is None:
                continue
            module = found[0]
            out.modules.add(module)
            out.whole.add(module)
            out.keys.update((ancestor, "") for ancestor in module_ancestors(module) if ancestor in existence)
            out.keys.add((module, ""))
            directory = module.rpartition(".")[0]
            if directory and directory not in with_file:
                out.whole.add(directory)
                out.keys.add((directory, ""))
        return out


class Predictor:
    """The files whose edges, symbols, labels or rule rows a commit can change, from the parent's
    traces. `ignored` is a mask of read kinds left out of every trace: a test that drops a kind
    must see the prediction miss."""

    def __init__(self, parent: TraceDump, changes: Changes, ignored: int = 0) -> None:
        self.parent = parent
        self.changes = changes
        self.ignored = ignored
        self.name_mask = parent.mask(NAME_KINDS)
        self.symbol_mask = parent.mask(SYMBOL_KINDS)
        self.label_bit = parent.bit("label")
        self.special = {
            parent.bit("stars"): (STARS,),
            parent.bit("lazy"): (LAZY, "__getattr__"),
            parent.bit("names"): (NAMES,),
            parent.bit("lines"): (LINES,),
            parent.bit("module"): ("",),
            parent.bit("head"): ("",),
        }

    def keys_of(self, read: Read) -> Iterator[tuple[str, str]]:
        """The interface keys a read depends on."""
        kinds = read.kinds & ~self.ignored
        if kinds & self.name_mask:
            yield (read.module, read.name)
        for bit, names in self.special.items():
            if kinds & bit:
                for name in names:
                    yield (read.module, name)

    def moved(self, read: Read) -> bool:
        if read.kinds & ~self.ignored & ~self.label_bit == 0:
            return False
        if read.module in self.changes.whole:
            return True
        return any(key in self.changes.keys for key in self.keys_of(read))

    def link_files(self) -> set[str]:
        """Step 1: the changed files, every file of a changed module, and every file whose
        trace reads a key that moved."""
        changes = self.changes
        out = set(changes.known)
        for path, (module, reads) in self.parent.traces.items():
            if module in changes.modules or any(self.moved(read) for read in reads):
                out.add(path)
        return out

    def readers(self, symbols: set[tuple[str, str]], labels: set[str]) -> set[str]:
        """Step 2: the files whose edges name a symbol whose facts moved, or whose rule rows read
        the labels of a module whose labels moved. Every rule row reads its own module's labels
        (its owners, its package), which no trace holds: a file of such a module is predicted."""
        out = set()
        symbol_mask = self.symbol_mask & ~self.ignored
        label_bit = self.label_bit & ~self.ignored
        for path, (module, reads) in self.parent.traces.items():
            if label_bit and module in labels:
                out.add(path)
                continue
            for read in reads:
                if read.kinds & symbol_mask and (read.module, read.name) in symbols:
                    out.add(path)
                    break
                if read.kinds & label_bit and read.module in labels:
                    out.add(path)
                    break
        return out


# ── step 2 from two dumps of tables ──


def longest_module(name: str, modules: dict[str, str]) -> str:
    return longest_prefix(name, modules.__contains__) or ""


def without_place(row: list[Any]) -> list[Any]:
    """An edge row without the fields of its place."""
    return [value for field, value in zip(FIELDS, row, strict=True) if field not in PLACE_FIELDS]


def symbol_key(name: str, modules: dict[str, str]) -> tuple[str, str]:
    """(module, top-level name) of a dotted symbol."""
    module = longest_module(name, modules)
    rest = name[len(module) + 1 :] if module else name
    return module, rest.split(".")[0]


@dataclass(frozen=False)
class TreeTables:
    """What step 2 reads of one tree's tables, for the files `files` and the label modules
    `label_modules` (None: every module) only: a check that reuses stored answers has these rows
    for what it recomputed, and stored rows for the rest. The labels of the modules of `files` are
    read too: a label derived from a module's edges, such as the zones it hands out names from,
    moves when those edges do."""

    modules: dict[str, str]
    symbols: dict[str, list[Any]] = field(default_factory=dict)
    classes: dict[str, list[Any]] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    labels: dict[str, list[Any]] = field(default_factory=dict)

    @staticmethod
    def read(dump: Path, files: set[str], label_modules: set[str] | None) -> TreeTables:
        modules = dict(read_rows(dump / "modules.jsonl"))
        out = TreeTables(modules)
        for name, facts in read_rows(dump / "symbols.jsonl"):
            module = longest_module(name, modules)
            if modules.get(module) in files:
                out.symbols[name] = facts
        for row in read_rows(dump / "edges.jsonl"):
            path = row[PATH]
            if path not in files:
                continue
            kind, attrs = row[KIND], dict(map(tuple, row[ATTRS]))
            # An edge's place moves with every edit above it; what folded rules read does not.
            out.edges[path].append(json.dumps(without_place(row)))
            owner = attrs.get("class") if kind == "inherit" else attrs.get("decorated") if kind == "decorate" else None
            if owner:
                found = [kind, row[SURFACE], row[TARGETS], row[RESOLUTION]]
                out.classes.setdefault(f"{row[MODULE]}.{owner}", []).append(found)
        for module, path, rows in read_rows(dump / "labels.jsonl"):
            if label_modules is None or module in label_modules or path in files:
                out.labels[module] = rows
        return out


def inherit_children(dump: Path) -> dict[str, set[str]]:
    """Base -> the classes that name it in an inherit edge, over a whole tree."""
    children: dict[str, set[str]] = defaultdict(set)
    for row in read_rows(dump / "edges.jsonl"):
        if row[KIND] != "inherit":
            continue
        owner = dict(map(tuple, row[ATTRS])).get("class")
        if not owner:
            continue
        for base in row[TARGETS] or [f"?{row[SURFACE]}"]:
            children[base].add(f"{row[MODULE]}.{owner}")
    return children


def descendants(roots: set[str], children: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    queue = list(roots)
    while queue:
        for child in children.get(queue.pop(), ()):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen


@dataclass(frozen=False)
class Moved:
    """What step 2 found moved among the recomputed files."""

    symbols: set[tuple[str, str]] = field(default_factory=set)
    # Modules whose labels or hand-outs moved: what a rule row reads of a module, not a symbol.
    labels: set[str] = field(default_factory=set)
    edges: bool = False


@dataclass(frozen=True)
class Prediction:
    """A commit's prediction: the files to recompute, the label modules recomputed (None for
    every module), and the folded rules that rerun."""

    files: frozenset[str]
    labels: frozenset[str] | None
    rerun: frozenset[str]


class PairPrediction:
    """The prediction for the commit from the tree of `parent_dump` to that of `child_dump`.
    From the child it reads only the interfaces of the changed files, and in step 2 the rows of
    the files step 1 predicted (what a check recomputes). The dumps are read once, so the
    prediction can be asked again with some read kinds left out."""

    def __init__(self, parent_dump: Path, child_dump: Path, changed: set[str]) -> None:
        self.parent_dump = parent_dump
        self.child_dump = child_dump
        self.changed = set(changed)
        self.parent = TraceDump(parent_dump / TRACES, interfaces_of=self.changed)
        self.child = TraceDump(child_dump / TRACES, interfaces_of=self.changed, traces=False)
        self.config = TraceConfig.read(parent_dump)
        self.changes = Changes.of(self.parent, self.child, self.changed)
        self.side_changed = any(InputPatterns(self.config.side).matches(path) for path in self.changed)
        self.label_modules: set[str] | None = (
            None if self.side_changed else {*self.changes.modules, *self.changes.whole}
        )
        self.tables: tuple[TreeTables, TreeTables] | None = None
        self.tables_of: frozenset[str] = frozenset()
        self.children: dict[str, set[str]] | None = None

    def read_tables(self, files: set[str]) -> tuple[TreeTables, TreeTables]:
        """Both trees' rows of `files`; read once for the largest set asked."""
        if self.tables is None or not files <= self.tables_of:
            self.tables_of = frozenset(files)
            self.tables = (
                TreeTables.read(self.parent_dump, files, self.label_modules),
                TreeTables.read(self.child_dump, files, self.label_modules),
            )
        return self.tables

    def class_children(self) -> dict[str, set[str]]:
        if self.children is None:
            self.children = inherit_children(self.parent_dump)
            for base, names in inherit_children(self.child_dump).items():
                self.children.setdefault(base, set()).update(names)
        return self.children

    def moved(self, files: set[str]) -> Moved:
        """Symbols whose facts or class ancestry moved, modules whose labels moved, and whether
        any edge moved, among `files` and the recomputed label modules."""
        before, after = self.read_tables(files)
        modules = {**before.modules, **after.modules}
        out = Moved()

        def mine(name: str) -> bool:
            return modules.get(longest_module(name, modules)) in files

        for name in set(before.symbols) | set(after.symbols):
            if mine(name) and before.symbols.get(name) != after.symbols.get(name):
                out.symbols.add(symbol_key(name, modules))
        changed_classes = {
            owner
            for owner in set(before.classes) | set(after.classes)
            if mine(owner) and before.classes.get(owner) != after.classes.get(owner)
        }
        if changed_classes:
            for owner in changed_classes | descendants(changed_classes, self.class_children()):
                out.symbols.add(symbol_key(owner, modules))
        for module in set(before.labels) | set(after.labels):
            if before.labels.get(module) != after.labels.get(module):
                out.labels.add(module)
        recomputed = {module for path, module in {**self.parent.modules, **self.child.modules}.items() if path in files}
        for module in recomputed:
            if self.parent.hand_outs.get(module) != self.child.hand_outs.get(module):
                out.labels.add(module)
        for path in files:
            if sorted(before.edges.get(path, [])) != sorted(after.edges.get(path, [])):
                out.edges = True
                break
        return out

    def predict(self, ignored_kinds: Iterable[str] = ()) -> Prediction:
        config = self.config
        if any(path in set(config.policy) for path in self.changed):
            every = frozenset(self.parent.traces) | frozenset(self.child.modules)
            return Prediction(every, None, frozenset(config.folded))
        predictor = Predictor(self.parent, self.changes, self.parent.mask(ignored_kinds))
        link = predictor.link_files()
        moved = self.moved(link)
        readers = predictor.readers(moved.symbols, moved.labels)
        folded_moved = moved.edges or bool(moved.labels) or bool(moved.symbols) or self.side_changed
        rerun = frozenset(config.folded) if folded_moved else frozenset()
        labels = frozenset(self.label_modules) if self.label_modules is not None else None
        return Prediction(frozenset(link | readers), labels, rerun)


def predict(parent_dump: Path, child_dump: Path, changed: set[str], ignored_kinds: Iterable[str] = ()) -> Prediction:
    """`PairPrediction.predict` for one question."""
    return PairPrediction(parent_dump, child_dump, changed).predict(ignored_kinds)


# ── reading a dump as a person ──


def summary(dump: Path) -> str:
    """Counts of one dump: files, trace entries by kind, interfaces and digests, the bytes."""
    traces = TraceDump(dump)
    by_kind = dict.fromkeys(traces.kinds, 0)
    entries = 0
    for _, reads in traces.traces.values():
        entries += len(reads)
        for read in reads:
            for at, kind in enumerate(traces.kinds):
                by_kind[kind] += read.kinds >> at & 1
    digests = sum(len(names) for _, names in traces.interfaces.values())
    lines = [
        f"{dump}: {dump.stat().st_size / 1e6:.1f} MB",
        f"files with a trace: {len(traces.traces)}; trace entries (module, name): {entries}",
        "entries per kind (an entry counts once per kind that read it):",
        *(f"  {kind:8s} {count}" for kind, count in by_kind.items()),
        f"interfaces: {len(traces.interfaces)}; digests: {digests}",
    ]
    return "\n".join(lines)


def show(dump: Path, path: str) -> str:
    """What one file read from other files, grouped by module, each with the kinds that read
    it; then the interface other files read of it."""
    traces = TraceDump(dump)
    module, reads = traces.traces.get(path, ("", []))
    lines = [f"{path} ({module}): {len(reads)} reads"]
    for read in reads:
        kinds = ", ".join(kind for at, kind in enumerate(traces.kinds) if read.kinds >> at & 1)
        lines.append(f"  {read.module}  {read.name or '-'}  [{kinds}]")
    found = traces.interfaces.get(path)
    if found is not None:
        lines.append(f"interface: {len(found[1])} entries")
        lines.extend(f"  {name}  {digest}" for name, digest in sorted(found[1].items()))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("summary", "show"))
    parser.add_argument("dump", type=Path)
    parser.add_argument("path", nargs="?", default="")
    args = parser.parse_args()
    text = summary(args.dump) if args.command == "summary" else show(args.dump, args.path)
    sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
