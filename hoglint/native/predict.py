"""Which files' results a commit can change, from the parent tree's traces (stage S5).

A check with `HOGLINT_LINK_TRACE=1` records, for every analyzed file, what its edges read from
other files at the grain of (module, name), and every repo file's interface: a digest per name of
what other files can read of it (`Link.write_traces`). Given the parent tree's dump and the files
a commit changed, the prediction runs in two steps, as a check that reuses stored answers would:

1. The link. The changed files' interfaces of parent and child give the (module, name) keys that
   moved; an added or deleted file moves its whole module and the existence of every module above
   it. A file whose trace reads a moved key is predicted, with every changed file and every file
   of a changed module.
2. What rule rows read. The predicted files are recomputed (here: their rows are taken from the
   child's dump), which tells whose symbol facts, class ancestry and labels really moved. A file
   whose edges name such a symbol, or whose rule rows read such a module's labels, is predicted
   too. Labels of every module are recomputed when a file a label provider declares changed.

Rules folded over packages or the whole graph rerun when what they read moved: any predicted
file's edges outside their positions, any label, any symbol fact, or a declared side input.

This module is generic: it knows trace kinds, not any repo's names.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hoglint.ledger.inputs import InputPatterns, RunInputs
from hoglint.rules.engine import primitive_of

TRACES = "traces.jsonl"
# The read kinds in the order of their bits (`trace::KIND_NAMES` in Rust).
KIND_NAMES = (
    "found",
    "missing",
    "stars",
    "lazy",
    "names",
    "module",
    "head",
    "class",
    "def",
    "alias",
    "lines",
    "target",
    "spelled",
    "label",
)
TRACE_CONFIG = "trace-config.json"
# Interface entries that are no Python name (`trace::STARS` and the others in Rust).
STARS, LAZY, NAMES, LINES = "<stars>", "<lazy>", "<names>", "<lines>"
# Read kinds whose key is (module, name) itself.
NAME_KINDS = frozenset({"found", "missing", "class", "def", "alias", "target", "spelled"})
# Read kinds that name a symbol a rule row or a pack reads the facts of.
SYMBOL_KINDS = frozenset({"target", "spelled"})
# The native modes of rules that judge one edge at a time; any other mode folds many edges.
ROW_MODE = "judge"
FOLDING_VIEWS = frozenset({"packages"})


def module_ancestors(module: str) -> list[str]:
    """`a.b.c` -> `a`, `a.b`: the packages a module sits in."""
    parts = module.split(".")
    return [".".join(parts[:end]) for end in range(1, len(parts))]


@dataclass(frozen=True)
class TraceConfig:
    """What a run's config says about its inputs, written next to its trace dump: the patterns of
    the files label providers and coverage adapters read, the policy files, and the rules whose
    rows fold many edges."""

    side: tuple[str, ...]
    policy: tuple[str, ...]
    folded: tuple[str, ...]

    @staticmethod
    def of(config: Any) -> TraceConfig:
        folded = []
        for spec in config.rules.values():
            mode = primitive_of(spec).NATIVE_MODE
            if mode != ROW_MODE or spec.view in FOLDING_VIEWS:
                folded.append(spec.id)
        policy = tuple(sorted({str(path) for path in config.provenance.values()}))
        return TraceConfig(RunInputs.of(config).side(), policy, tuple(sorted(folded)))

    def write(self, out: Path) -> None:
        data = {"side": list(self.side), "policy": list(self.policy), "folded": list(self.folded)}
        (out / TRACE_CONFIG).write_text(json.dumps(data))

    @staticmethod
    def read(out: Path) -> TraceConfig:
        data = json.loads((out / TRACE_CONFIG).read_text())
        return TraceConfig(tuple(data["side"]), tuple(data["policy"]), tuple(data["folded"]))


def write_trace_dump(link: Any, config: Any, out: Path) -> tuple[int, int, int, int]:
    """The link's traces and interfaces, and the config's inputs, into `out`."""
    TraceConfig.of(config).write(out)
    return link.write_traces(str(out / TRACES))


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
        for line in path.open():
            row = json.loads(line)
            if row[0] == "kinds":
                self.kinds = row[1]
            elif row[0] == "trace":
                _, file, module, reads = row
                self.modules[file] = module
                if traces:
                    self.traces[file] = (module, [Read(m, n, mask) for m, n, mask in reads])
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


@dataclass(frozen=False)
class Changes:
    """What a commit moved at the link's grain: (module, name) keys, whole modules (a file added
    or deleted), the changed paths, and the modules of the changed files."""

    keys: set[tuple[str, str]] = field(default_factory=set)
    whole: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    modules: set[str] = field(default_factory=set)

    @staticmethod
    def of(parent: TraceDump, child: TraceDump, paths: set[str]) -> Changes:
        """From the interfaces of the changed files in both trees."""
        out = Changes(paths=set(paths))
        for path in sorted(paths):
            before = parent.interfaces.get(path)
            after = child.interfaces.get(path)
            if before is None and after is None:
                continue
            if before is None or after is None:
                module = after[0] if after is not None else before[0]  # type: ignore[index]
                out.modules.add(module)
                out.whole.add(module)
                out.keys.update((ancestor, "") for ancestor in module_ancestors(module))
                out.keys.add((module, ""))
                continue
            module = after[0]
            out.modules.add(module)
            names = set(before[1]) | set(after[1])
            out.keys.update((module, name) for name in names if before[1].get(name) != after[1].get(name))
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
        out = {path for path in changes.paths if path in self.parent.traces or path in self.parent.modules}
        for path, (module, reads) in self.parent.traces.items():
            if module in changes.modules or any(self.moved(read) for read in reads):
                out.add(path)
        return out

    def readers(self, symbols: set[tuple[str, str]], labels: set[str]) -> set[str]:
        """Step 2: the files whose edges name a symbol whose facts moved, or whose rule rows read
        the labels of a module whose labels moved."""
        out = set()
        symbol_mask = self.symbol_mask & ~self.ignored
        label_bit = self.label_bit & ~self.ignored
        for path, (_, reads) in self.parent.traces.items():
            for read in reads:
                if read.kinds & symbol_mask and (read.module, read.name) in symbols:
                    out.add(path)
                    break
                if read.kinds & label_bit and read.module in labels:
                    out.add(path)
                    break
        return out


# ── step 2 from two dumps of tables ──


def jsonl(path: Path) -> Iterator[Any]:
    with path.open() as handle:
        for line in handle:
            yield json.loads(line)


def longest_module(name: str, modules: dict[str, str]) -> str:
    module = name
    while module and module not in modules:
        module = module.rpartition(".")[0]
    return module


def symbol_key(name: str, modules: dict[str, str]) -> tuple[str, str]:
    """(module, top-level name) of a dotted symbol."""
    module = longest_module(name, modules)
    rest = name[len(module) + 1 :] if module else name
    return module, rest.split(".")[0]


@dataclass(frozen=False)
class TreeTables:
    """What step 2 reads of one tree's tables, for the files `files` and the label modules
    `label_modules` (None: every module) only: a check that reuses stored answers has these rows
    for what it recomputed, and stored rows for the rest."""

    modules: dict[str, str]
    symbols: dict[str, list[Any]] = field(default_factory=dict)
    classes: dict[str, list[Any]] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    labels: dict[str, list[Any]] = field(default_factory=dict)

    @staticmethod
    def read(dump: Path, files: set[str], label_modules: set[str] | None) -> TreeTables:
        modules = dict(jsonl(dump / "modules.jsonl"))
        out = TreeTables(modules)
        for name, facts in jsonl(dump / "symbols.jsonl"):
            module = longest_module(name, modules)
            if modules.get(module) in files:
                out.symbols[name] = facts
        for row in jsonl(dump / "edges.jsonl"):
            path = row[1]
            if path not in files:
                continue
            kind, attrs = row[0], dict(map(tuple, row[15]))
            # An edge's place moves with every edit above it; what folded rules read does not.
            out.edges[path].append(json.dumps([row[0], *row[2:4], *row[6:]]))
            owner = attrs.get("class") if kind == "inherit" else attrs.get("decorated") if kind == "decorate" else None
            if owner:
                out.classes.setdefault(f"{row[2]}.{owner}", []).append([kind, row[6], row[7], row[8]])
        for module, _, rows in jsonl(dump / "labels.jsonl"):
            if label_modules is None or module in label_modules:
                out.labels[module] = rows
        return out


def inherit_children(dump: Path) -> dict[str, set[str]]:
    """Base -> the classes that name it in an inherit edge, over a whole tree."""
    children: dict[str, set[str]] = defaultdict(set)
    for row in jsonl(dump / "edges.jsonl"):
        if row[0] != "inherit":
            continue
        owner = dict(map(tuple, row[15])).get("class")
        if not owner:
            continue
        for base in row[7] or [f"?{row[6]}"]:
            children[base].add(f"{row[2]}.{owner}")
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
    labels: set[str] = field(default_factory=set)
    edges: bool = False


@dataclass(frozen=True)
class Prediction:
    """A commit's prediction: the files to recompute, the label modules recomputed (None for
    every module), the folded rules that rerun, and each step's share."""

    files: frozenset[str]
    link: frozenset[str]
    readers: frozenset[str]
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
        for path in files:
            if sorted(before.edges.get(path, [])) != sorted(after.edges.get(path, [])):
                out.edges = True
                break
        return out

    def predict(self, ignored_kinds: Iterable[str] = ()) -> Prediction:
        config = self.config
        predictor = Predictor(self.parent, self.changes, self.parent.mask(ignored_kinds))
        link = predictor.link_files()
        if any(path in set(config.policy) for path in self.changed):
            every = frozenset(self.parent.traces) | frozenset(self.child.modules)
            return Prediction(every, every, frozenset(), None, frozenset(config.folded))
        moved = self.moved(link)
        readers = predictor.readers(moved.symbols, moved.labels)
        folded_moved = moved.edges or bool(moved.labels) or bool(moved.symbols) or self.side_changed
        rerun = frozenset(config.folded) if folded_moved else frozenset()
        labels = frozenset(self.label_modules) if self.label_modules is not None else None
        return Prediction(frozenset(link | readers), frozenset(link), frozenset(readers - link), labels, rerun)


def predict(parent_dump: Path, child_dump: Path, changed: set[str], ignored_kinds: Iterable[str] = ()) -> Prediction:
    """`PairPrediction.predict` for one question."""
    return PairPrediction(parent_dump, child_dump, changed).predict(ignored_kinds)
