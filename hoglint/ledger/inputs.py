"""What a run reads besides its policy: its sources and its side inputs, and the files of one git
tree, read by blob id with no checkout.

A staged run captures the index once with `git write-tree`, and every later read uses that tree:
the sources, the policy and the side inputs. `TreeFiles` writes chosen files of a snapshot's
listing (listing.py), a git tree's or the working tree's, into directories.

Side inputs are the files a run reads besides its sources and its policy files: what providers
and coverage adapters declare (`inputs` in plugins.py), the files layout rules ask about, and
the ledger. `RunInputs` holds their path patterns, which depend on the config alone, so they are
known before a run.
"""

from __future__ import annotations

import re
import hashlib
import tempfile
from collections.abc import Iterable
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from hoglint.config.model import Config
from hoglint.globs import CAPTURE_RE, Braces, literal_ends, translate
from hoglint.ledger.envelope import EngineError
from hoglint.ledger.listing import SOURCE_SUFFIXES, Listing, TreeListing
from hoglint.ledger.scan import write_atomic
from hoglint.plugins import PLUGIN_ROOTS, REGISTRY, declared_inputs

SOURCE_INPUTS = tuple(f"**/*{suffix}" for suffix in SOURCE_SUFFIXES)
INPUTS_DIGEST_CHARS = 40
PATTERN_DIGEST_CHARS = 16
# What an inputs digest hashes; a cached digest of another layout is never read.
DIGEST_LAYOUT = "l2"
# A stored digest of any other form is damaged, so it is a miss.
STORED_DIGEST = re.compile(f"[0-9a-f]{{{INPUTS_DIGEST_CHARS}}}")


class InputPatterns:
    """Path patterns as one regex: `**` spans directories, `*` and `?` stay in one segment. A
    path may hold any character, a newline included, as discovery takes it."""

    def __init__(self, patterns: Iterable[str]) -> None:
        self.patterns = tuple(sorted(set(patterns)))
        regex = "|".join(f"(?:{translate(pattern, Braces.LITERAL)})" for pattern in self.patterns)
        self.regex = re.compile(regex, re.DOTALL)
        # Every match ends with one of these texts, so a cheap test turns most paths away; a
        # pattern with no literal end (`dir/**`) leaves every path to the regex.
        ends = tuple(literal_ends(pattern, Braces.LITERAL)[1] for pattern in self.patterns)
        self.ends = ends if all(ends) else ("",)

    def matches(self, path: str) -> bool:
        return bool(self.patterns) and path.endswith(self.ends) and self.regex.fullmatch(path) is not None

    def digest(self) -> str:
        return hashlib.sha256("\0".join(self.patterns).encode()).hexdigest()[:PATTERN_DIGEST_CHARS]


class RunInputs:
    """The path patterns of everything a run of `config` reads but its policy files. One per
    config object (`of`), so every reader of a run's patterns reads the same ones."""

    taken: ClassVar[dict[int, RunInputs]] = {}

    def __init__(self, config: Config) -> None:
        self.config = config

    @staticmethod
    def of(config: Config) -> RunInputs:
        found = RunInputs.taken.get(id(config))
        if found is None:
            found = RunInputs.taken[id(config)] = RunInputs(config)
        return found

    def declared(self) -> tuple[str, ...]:
        """What each provider and coverage adapter declares: the files a plugin reads."""
        patterns: set[str] = set()
        for spec in self.config.providers.values():
            patterns.update(declared_inputs(REGISTRY.providers.get(spec.use), dict(spec.options)))
        for spec in self.config.rules.values():
            if spec.primitive == "coverage":
                adapter = REGISTRY.coverage_adapters.get(str(spec.input("adapter")))
                options = {key: value for key, value in spec.inputs if key != "adapter"}
                patterns.update(declared_inputs(adapter, options))
        return tuple(sorted(patterns))

    def side(self) -> tuple[str, ...]:
        """What each provider and coverage adapter declares, and the paths layout rules ask
        about. A layout rule asks only whether a path exists, so its patterns cover every file
        under such a path too."""
        patterns = set(self.declared())
        for spec in self.config.rules.values():
            if spec.primitive == "layout" and spec.within:
                within = CAPTURE_RE.sub("*", spec.within)
                for name in (*spec.files, *spec.absent):
                    path = f"{within}/{name.strip('/')}"
                    patterns.update((path, f"{path}/**"))
        return tuple(sorted(patterns))

    @cached_property
    def patterns(self) -> InputPatterns:
        """Sources, side inputs and the ledger."""
        return InputPatterns((*SOURCE_INPUTS, *self.side(), self.config.ledger.file))

    @cached_property
    def plugin_patterns(self) -> InputPatterns:
        return InputPatterns(self.declared())


class TreeFiles:
    """Files of one snapshot, written into directories on request: a git tree, or the working
    tree as git sees it (D40), so a plugin of a working tree run never reads an ignored file or a
    file behind a skip-worktree bit. As a `TreeRoot` (plugins.py) it writes one directory per
    plugin, each holding exactly the files the plugin declares, into `work_dir`, and answers
    whether a path exists only for a path `asked` matches: the run's input patterns, so the
    answer is in the run's key. The first plugin directory reads the bytes of every file
    `declared` matches (what the run's plugins declare) in one call; the later ones only write."""

    def __init__(
        self,
        listing: Listing,
        work_dir: Path | None = None,
        asked: InputPatterns | None = None,
        declared: InputPatterns | None = None,
    ) -> None:
        self.listing = listing
        self.work_dir = work_dir
        self.asked = asked
        self.declared = declared
        self.plugin_roots: dict[tuple[str, ...], Path] = {}

    @cached_property
    def declared_bytes(self) -> dict[str, bytes]:
        if self.declared is None:
            return {}
        declared = self.declared
        return self.listing.read(path for path in self.listing.modes if declared.matches(path))

    def write(self, target: Path, paths: Iterable[str]) -> None:
        """Writes the paths the snapshot holds and can read under `target`; the others are left out."""
        self.listing.write(target, paths)

    def scratch(self, prefix: str) -> Path:
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self.work_dir)).resolve()

    def run_root(self) -> Path:
        """An empty directory that stands for the snapshot: rules and providers that get it as
        their repo root read the snapshot through `PLUGIN_ROOTS`."""
        root = self.scratch("tree-")
        PLUGIN_ROOTS.add(root, self)
        return root

    def plugin_root(self, patterns: tuple[str, ...]) -> Path:
        found = self.plugin_roots.get(patterns)
        if found is not None:
            return found
        root = self.scratch("plugin-")
        wanted = InputPatterns(patterns)
        paths = [path for path in self.listing.modes if wanted.matches(path)]
        unreadable = self.listing.write(root, paths, self.declared_bytes)
        if unreadable:
            more = f" and {len(unreadable) - 1} more" if len(unreadable) > 1 else ""
            raise OSError(
                f"cannot read {unreadable[0]}{more}: a link that leads nowhere in the snapshot, or a lost file"
            )
        self.plugin_roots[patterns] = root
        PLUGIN_ROOTS.add(root, self)
        return root

    def holds(self, path: str) -> bool:
        """Whether the snapshot holds `path`. A path no input pattern matches is not in the run's
        key, so the question is an engine error: its plugin or rule must declare the path."""
        if self.asked is not None and not self.asked.matches(path):
            raise EngineError(f"the run asked whether {path} exists, but no source or declared input pattern covers it")
        return self.listing.holds(path)


class InputDigests:
    """The `inputs` part of a summary key: a digest of (path, mode, blob oid) over every file of a
    git tree that the run's input patterns match, sorted by path. A link's blob is the one it
    leads to, and an unreadable link has none (D41). A tree is immutable, so the digest of each
    (tree, patterns) pair is kept under `<cache>/inputs`, and a tree seen before needs no listing.
    That file is a cache: one that does not read is computed again."""

    def __init__(self, cache: Path) -> None:
        self.root = cache / "inputs"
        self.known: dict[tuple[str, str], str] = {}

    @staticmethod
    def compute(listing: TreeListing, patterns: InputPatterns) -> str:
        hasher = hashlib.sha256()
        for path in sorted((path for path in listing.modes if patterns.matches(path)), key=str.encode):
            hasher.update(f"{path}\0{listing.modes[path]}\0{listing.blob(path) or ''}\0".encode())
        return hasher.hexdigest()[:INPUTS_DIGEST_CHARS]

    def of(self, repo: Path, tree: str, patterns: InputPatterns) -> str:
        """The digest for `tree`; empty when there is no tree."""
        if not tree:
            return ""
        key = (tree, patterns.digest())
        found = self.known.get(key)
        if found is not None:
            return found
        path = self.root / f"{tree}-{key[1]}-{DIGEST_LAYOUT}"
        try:
            found = path.read_text().strip()
        except (OSError, UnicodeDecodeError):
            found = ""
        if not STORED_DIGEST.fullmatch(found):
            found = self.compute(TreeListing.of(repo, tree), patterns)
            self.keep(path, found)
        self.known[key] = found
        return found

    @staticmethod
    def keep(path: Path, digest: str) -> None:
        try:
            write_atomic(path, digest.encode())
        except OSError:
            return
