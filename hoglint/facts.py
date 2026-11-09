"""L1 fact store: run the Rust extractor over a snapshot and load its records."""

from __future__ import annotations

import os
import sys
import shutil
import sysconfig
import subprocess
from dataclasses import dataclass
from pathlib import Path

import orjson

BINARY_NAME = "hoglint-facts"
# Where tools/hoglint/bin/build puts the binary when CARGO_TARGET_DIR is not set.
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


def site_packages_files(packages: tuple[str, ...]) -> dict[str, str]:
    """Sources of the named distributions in this interpreter's site-packages: path -> path
    inside site-packages, which names the module."""
    site = Path(sysconfig.get_paths()["purelib"])
    out: dict[str, str] = {}
    for package in packages:
        root = site / package
        if root.is_dir():
            for path in sorted(root.rglob("*.py")):
                out[str(path)] = str(path.relative_to(site))
    return out


@dataclass(frozen=True)
class ExtractRequest:
    """One extractor run over an explicit file list. `root` may be a temp tree of staged blobs."""

    root: Path
    files: tuple[str, ...]
    first_party: tuple[str, ...]
    out_path: Path
    cache_dir: Path | None = None
    needs: FactNeeds = FactNeeds()


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
        raise FileNotFoundError(
            f"{BINARY_NAME} not found; build it with tools/hoglint/bin/build or set HOGLINT_FACTS_BIN"
        )

    def __init__(self, binary: Path | None = None) -> None:
        self.binary = binary or self.find_binary()

    def extract(self, request: ExtractRequest) -> Path:
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        list_path = request.out_path.with_suffix(".files")
        list_path.write_text("\n".join(request.files) + "\n")
        command = [
            str(self.binary),
            str(request.root),
            str(list_path),
            "--v1.1",
            "--first-party",
            ",".join(request.first_party),
            "--out",
            str(request.out_path),
        ]
        if request.needs.watch_callees:
            command += ["--watch-callees", ",".join(sorted(request.needs.watch_callees))]
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
    def from_jsonl(cls, path: Path) -> FactStore:
        store = cls()
        with open(path, "rb") as handle:
            for line in handle:
                record = orjson.loads(line)
                store.records[record["path"]] = record
        return store


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "hoglint" / "facts"
