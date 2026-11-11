"""L1 fact store: run the Rust extractor over a snapshot and load its records."""

from __future__ import annotations

import os
import sys
import stat
import time
import shutil
import sysconfig
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from hoglint.ledger.cache import AnswerStore, answer_key

BINARY_NAME = "hoglint-facts"
# Where bin/build puts the binary when CARGO_TARGET_DIR is not set.
DEFAULT_TARGET_DIR = Path.home() / ".cache" / "hoglint-target"
FACT_KINDS = frozenset(
    {
        "assigns",
        "branches",
        "callees",
        "calls",
        "defs",
        "derived",
        "imports",
        "names",
        "refs",
        "strs",
        "tags",
        "uses",
        "watched",
    }
)


@dataclass(frozen=True)
class FactNeeds:
    """What a consumer needs from the extractor: fact kinds, and which calls and dict keys to record.

    - kinds: fact lists to emit.
    - watch_callees: last dotted names of callees whose calls get a `calls` row.
    - watch_prefixes: the same, for last names that start with one of these.
    - watch_keys: string dict keys whose entries get a `watched` row.
    """

    kinds: frozenset[str] = frozenset()
    watch_callees: frozenset[str] = frozenset()
    watch_prefixes: frozenset[str] = frozenset()
    watch_keys: frozenset[str] = frozenset()

    def __or__(self, other: FactNeeds) -> FactNeeds:
        return FactNeeds(
            self.kinds | other.kinds,
            self.watch_callees | other.watch_callees,
            self.watch_prefixes | other.watch_prefixes,
            self.watch_keys | other.watch_keys,
        )


# The record path of a third-party file starts here instead of at one machine's site-packages,
# so a fact, an edge site or a witness reads the same on every machine.
SITE_PACKAGES = "<site-packages>"


def site_packages() -> Path:
    """This interpreter's site-packages directory."""
    return Path(sysconfig.get_paths()["purelib"])


def site_packages_files(packages: tuple[str, ...]) -> dict[str, str]:
    """Sources of the named distributions in this interpreter's site-packages: record path ->
    path inside site-packages, which names the module. The record path is
    `<site-packages>/<path inside>`; the file is `site_packages() / <path inside>`."""
    site = site_packages()
    out: dict[str, str] = {}
    for package in packages:
        root = site / package
        if root.is_dir():
            for path in sorted(root.rglob("*.py")):
                inside = path.relative_to(site).as_posix()
                out[f"{SITE_PACKAGES}/{inside}"] = inside
    return out


# A directory that changed this recently can change again within its clock's tick, with no new
# time, so a walk that met one is not stored.
SETTLED_NS = 2_000_000_000
# The state of a package root that is not a directory: no inode is 0.
NO_DIRECTORY = (0, 0, 0)


class SitePackagesFiles:
    """`site_packages_files`, stored with the state (inode and times) of every directory under
    the packages. A file comes, goes or is renamed only through its directory, which gives the
    directory new times, and the walk lists names only. So while each stored directory has its
    stored state, and each package root that was no directory is still none, the walk would list
    the same files, and the stored list is the answer."""

    STORE = AnswerStore("site-packages")

    @staticmethod
    def of(packages: tuple[str, ...]) -> dict[str, str]:
        site = site_packages()
        key = answer_key(Path(__file__).read_bytes(), sys.version, str(site), *packages)
        stored = SitePackagesFiles.STORE.get(key)
        if stored is not None:
            files = SitePackagesFiles.unchanged(site, stored)
            if files is not None:
                return files
        directories = SitePackagesFiles.directories(site, packages)
        files = site_packages_files(packages)
        if directories is not None:
            SitePackagesFiles.STORE.put(key, {"directories": directories, "files": list(files.items())})
        return files

    @staticmethod
    def state(path: Path) -> tuple[int, int, int]:
        try:
            found = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            return NO_DIRECTORY
        if not stat.S_ISDIR(found.st_mode):
            return NO_DIRECTORY
        return found.st_ino, found.st_mtime_ns, found.st_ctime_ns

    @staticmethod
    def directories(site: Path, packages: tuple[str, ...]) -> list[list[str | int]] | None:
        """Each package root and every directory under it, with its state, taken before the walk
        so a change during the walk shows at the next lookup; None when one changed too recently
        or could not be listed."""
        settled = time.time_ns() - SETTLED_NS
        rows: list[list[str | int]] = []
        for package in packages:
            root = site / package
            if not root.is_dir():
                rows.append([package, *NO_DIRECTORY])
                continue
            errors: list[OSError] = []
            for parent, _, _ in os.walk(root, onerror=errors.append):
                state = SitePackagesFiles.state(Path(parent))
                if max(state[1], state[2]) > settled:
                    return None
                rows.append([Path(parent).relative_to(site).as_posix(), *state])
            if errors:
                return None
        return rows

    @staticmethod
    def unchanged(site: Path, stored: Any) -> dict[str, str] | None:
        """The stored files while every stored directory keeps its state; else None."""
        try:
            for path, *state in stored["directories"]:
                if SitePackagesFiles.state(site / path) != tuple(state):
                    return None
            files = dict(stored["files"])
        except (OSError, KeyError, TypeError, ValueError):
            return None
        if not all(isinstance(item, str) for pair in files.items() for item in pair):
            return None
        return files


@dataclass(frozen=True)
class ExtractRequest:
    """One extractor run over an explicit file list. `root` may be a temp tree of staged blobs.

    `lite` files (third-party sources) only need what module tables and class ancestry read.
    """

    root: Path
    files: tuple[str, ...]
    first_party: tuple[str, ...]
    out_path: Path
    cache_dir: Path | None = None
    needs: FactNeeds = FactNeeds(kinds=FACT_KINDS)
    lite: tuple[str, ...] = ()


class Extractor:
    """Runs the hoglint-facts binary. The binary only reads files; it never imports them."""

    @staticmethod
    def find_binary() -> Path:
        override = os.environ.get("HOGLINT_FACTS_BIN")
        if override:
            return Path(override)
        target_dir = Path(os.environ.get("CARGO_TARGET_DIR") or DEFAULT_TARGET_DIR)
        built = target_dir / "release" / BINARY_NAME
        if built.exists():
            return built
        on_path = shutil.which(BINARY_NAME)
        if on_path:
            return Path(on_path)
        raise FileNotFoundError(f"{BINARY_NAME} not found; build it with bin/build or set HOGLINT_FACTS_BIN")

    def __init__(self, binary: Path | None = None) -> None:
        self.binary = binary or self.find_binary()

    @staticmethod
    def write_list(path: Path, files: tuple[str, ...]) -> Path:
        path.write_text("\n".join(files) + "\n")
        return path

    def command(self, request: ExtractRequest) -> list[str]:
        needs = request.needs
        command = [
            str(self.binary),
            str(request.root),
            str(self.write_list(request.out_path.with_suffix(".files"), request.files)),
            "--first-party",
            ",".join(request.first_party),
            "--kinds",
            ",".join(sorted(needs.kinds)),
            "--out",
            str(request.out_path),
        ]
        for flag, values in (
            ("--watch-callees", needs.watch_callees),
            ("--watch-prefixes", needs.watch_prefixes),
            ("--watch-keys", needs.watch_keys),
        ):
            if values:
                command += [flag, ",".join(sorted(values))]
        if request.lite:
            command += ["--lite-list", str(self.write_list(request.out_path.with_suffix(".lite"), request.lite))]
        return command

    def extract(self, request: ExtractRequest) -> Path:
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        command = self.command(request)
        if request.cache_dir is not None:
            command += ["--mode", "cache", "--cache", str(request.cache_dir)]
        else:
            command += ["--mode", "stream"]
        subprocess.run(command, check=True, stdout=sys.stderr)
        return request.out_path


class FactStore:
    """L1 records by path."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    @classmethod
    def from_jsonl(cls, path: Path, renamed: Mapping[str, str] | None = None) -> FactStore:
        """The records of an extractor's output; `renamed` gives a file read under another path
        its record path."""
        store = cls()
        with open(path, "rb") as handle:
            for line in handle:
                record = orjson.loads(line)
                if renamed is not None and record["path"] in renamed:
                    record["path"] = renamed[record["path"]]
                store.records[record["path"]] = record
        return store


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "hoglint" / "facts"
