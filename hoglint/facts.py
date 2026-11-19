"""L1 fact store: run the Rust extractor over a snapshot and load its records."""

from __future__ import annotations

import os
import stat
import time
import shutil
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoglint.ledger.cache import FACTS, SITE_PACKAGES_ANSWERS, AnswerStore, cache_root

BINARY_NAME = "hoglint-facts"
# Where bin/build puts the binary when CARGO_TARGET_DIR is not set.
DEFAULT_TARGET_DIR = Path.home() / ".cache" / "hoglint-target"
# Where the hoglint wheel installs its own binary (bin/stage-facts). A checkout has none here.
PACKAGED_BINARY = Path(__file__).resolve().parent / "bin" / BINARY_NAME
FACT_KINDS = frozenset(
    {
        "assigns",
        "branches",
        "callees",
        "calls",
        "computed",
        "defs",
        "derived",
        "escapes",
        "imports",
        "listed",
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
    - watch_hooks: hook names: their calls, also through an import alias, get a `calls` row in
      which a call argument keeps its callee.
    - watch_aliased: full names of callees whose calls get a `calls` row through any import
      alias too (the loaders).
    """

    kinds: frozenset[str] = frozenset()
    watch_callees: frozenset[str] = frozenset()
    watch_prefixes: frozenset[str] = frozenset()
    watch_keys: frozenset[str] = frozenset()
    watch_hooks: frozenset[str] = frozenset()
    watch_aliased: frozenset[str] = frozenset()

    def __or__(self, other: FactNeeds) -> FactNeeds:
        return FactNeeds(
            self.kinds | other.kinds,
            self.watch_callees | other.watch_callees,
            self.watch_prefixes | other.watch_prefixes,
            self.watch_keys | other.watch_keys,
            self.watch_hooks | other.watch_hooks,
            self.watch_aliased | other.watch_aliased,
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

    STORE = AnswerStore(SITE_PACKAGES_ANSWERS, __file__)

    @staticmethod
    def of(packages: tuple[str, ...]) -> dict[str, str]:
        site = site_packages()
        key = SitePackagesFiles.STORE.key(str(site), *packages)
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


def installed_wheel() -> bool:
    """Whether this package came from the hoglint wheel, which ships its own binary and native
    module. Then a build in the cargo target directory is another engine's and is never used."""
    return PACKAGED_BINARY.is_file()


def find_binary() -> Path:
    """The hoglint-facts binary: HOGLINT_FACTS_BIN, else the wheel's own binary, else the release
    build in the cargo target directory, else one on PATH."""
    override = os.environ.get("HOGLINT_FACTS_BIN")
    if override:
        return Path(override)
    if installed_wheel():
        return PACKAGED_BINARY
    target_dir = Path(os.environ.get("CARGO_TARGET_DIR") or DEFAULT_TARGET_DIR)
    built = target_dir / "release" / BINARY_NAME
    if built.exists():
        return built
    on_path = shutil.which(BINARY_NAME)
    if on_path:
        return Path(on_path)
    raise FileNotFoundError(f"{BINARY_NAME} not found; build it with bin/build or set HOGLINT_FACTS_BIN")


def default_cache_dir() -> Path:
    return cache_root() / FACTS
