"""L1 fact store: run the Rust extractor over a snapshot and load its records."""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
import sysconfig
import subprocess
from dataclasses import dataclass
from pathlib import Path

import orjson

BINARY_NAME = "hoglint-facts"
# Where tools/hoglint/bin/build puts the binary when CARGO_TARGET_DIR is not set.
DEFAULT_TARGET_DIR = Path.home() / ".cache" / "hoglint-target"
SITE_DIR_NAMES = ("site-packages", "dist-packages")


class SiteRoots:
    """Directories that hold third-party sources: the interpreter's purelib and platlib, and
    any `site-packages` or `dist-packages` directory on a path (another environment's)."""

    def __init__(self, roots: tuple[str, ...]) -> None:
        self.roots = tuple(sorted({root.rstrip("/") for root in roots}, key=len, reverse=True))

    @classmethod
    def of_interpreter(cls) -> SiteRoots:
        paths = sysconfig.get_paths()
        return cls((paths["purelib"], paths["platlib"]))

    def relative(self, path: str) -> str | None:
        """The path inside its site root, or None for a path outside every site root."""
        for root in self.roots:
            if path.startswith(root + "/"):
                return path[len(root) + 1 :]
        parts = path.split("/")
        for at, part in enumerate(parts[:-1]):
            if part in SITE_DIR_NAMES:
                return "/".join(parts[at + 1 :])
        return None

    def contains(self, path: str) -> bool:
        return self.relative(path) is not None


SITE_ROOTS = SiteRoots.of_interpreter()


@dataclass(frozen=True)
class ExtractOptions:
    repo_root: Path
    first_party: tuple[str, ...]
    external_packages: tuple[str, ...]
    out_path: Path
    cache_dir: Path | None = None
    watch_callees: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractRequest:
    """One extractor run over an explicit file list. `root` may be a temp tree of staged blobs."""

    root: Path
    files: tuple[str, ...]
    first_party: tuple[str, ...]
    out_path: Path
    cache_dir: Path | None = None
    watch_callees: tuple[str, ...] = ()


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

    def build_id(self) -> str:
        return subprocess.run(
            [str(self.binary), "--build-id"], capture_output=True, text=True, check=True
        ).stdout.strip()

    def discover(self, repo_root: Path) -> list[str]:
        result = subprocess.run(
            [str(self.binary), "--discover", str(repo_root), "--v1.1"], capture_output=True, text=True, check=True
        )
        return [line for line in result.stdout.splitlines() if line]

    @staticmethod
    def site_packages_files(packages: tuple[str, ...]) -> list[Path]:
        site = Path(sysconfig.get_paths()["purelib"])
        out: list[Path] = []
        for package in packages:
            root = site / package
            if root.is_dir():
                out.extend(sorted(root.rglob("*.py")))
        return out

    def external_files(self, repo_root: Path, packages: tuple[str, ...]) -> list[str]:
        return [str(path.relative_to(repo_root.resolve(), walk_up=True)) for path in self.site_packages_files(packages)]

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
        if request.watch_callees:
            command += ["--watch-callees", ",".join(request.watch_callees)]
        if request.cache_dir is not None:
            command += ["--mode", "cache", "--cache", str(request.cache_dir)]
        else:
            command += ["--mode", "stream"]
        subprocess.run(command, check=True, stdout=sys.stderr)
        return request.out_path

    def run(self, options: ExtractOptions) -> Path:
        """Spike entry point: discover the working tree and extract it with site-packages sources."""
        files = self.discover(options.repo_root) + self.external_files(options.repo_root, options.external_packages)
        request = ExtractRequest(
            root=options.repo_root,
            files=tuple(files),
            first_party=options.first_party,
            out_path=options.out_path,
            cache_dir=options.cache_dir,
            watch_callees=options.watch_callees,
        )
        return self.extract(request)


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

    def repo_paths(self) -> list[str]:
        return sorted(p for p in self.records if not SITE_ROOTS.contains(p))


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "hoglint" / "facts"


def scratch_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="hoglint-facts-"))
