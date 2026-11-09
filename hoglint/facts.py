"""L1 fact store: run the Rust extractor over the repo and load its records."""

from __future__ import annotations

import os
import sys
import shutil
import sysconfig
import subprocess
from dataclasses import dataclass
from pathlib import Path

import orjson

HOGLINT_DIR = Path(__file__).resolve().parent.parent
BINARY_NAME = "hoglint-facts"
SITE_MARKER = "site-packages/"


@dataclass(frozen=True)
class ExtractOptions:
    repo_root: Path
    first_party: tuple[str, ...]
    external_packages: tuple[str, ...]
    out_path: Path
    cache_dir: Path | None = None


class Extractor:
    """Runs the hoglint-facts binary. The binary only reads files; it never imports them."""

    def __init__(self, binary: Path | None = None) -> None:
        self.binary = binary or self.find_binary()

    @staticmethod
    def find_binary() -> Path:
        override = os.environ.get("HOGLINT_FACTS_BIN")
        if override:
            return Path(override)
        target_dir = Path(os.environ.get("CARGO_TARGET_DIR", HOGLINT_DIR / "target"))
        built = target_dir / "release" / BINARY_NAME
        if built.exists():
            return built
        on_path = shutil.which(BINARY_NAME)
        if on_path:
            return Path(on_path)
        raise FileNotFoundError(
            f"{BINARY_NAME} not found; build it with tools/hoglint/bin/build or set HOGLINT_FACTS_BIN"
        )

    def discover(self, repo_root: Path) -> list[str]:
        result = subprocess.run(
            [str(self.binary), "--discover", str(repo_root), "--v1.1"], capture_output=True, text=True, check=True
        )
        return [line for line in result.stdout.splitlines() if line]

    def external_files(self, repo_root: Path, packages: tuple[str, ...]) -> list[str]:
        site = Path(sysconfig.get_paths()["purelib"])
        out: list[str] = []
        for package in packages:
            root = site / package
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.py")):
                out.append(os.path.relpath(path, repo_root))
        return out

    def run(self, options: ExtractOptions) -> Path:
        files = self.discover(options.repo_root) + self.external_files(options.repo_root, options.external_packages)
        options.out_path.parent.mkdir(parents=True, exist_ok=True)
        list_path = options.out_path.with_suffix(".files")
        list_path.write_text("\n".join(files) + "\n")
        command = [
            str(self.binary),
            str(options.repo_root),
            str(list_path),
            "--v1.1",
            "--first-party",
            ",".join(options.first_party),
            "--out",
            str(options.out_path),
        ]
        if options.cache_dir is not None:
            command += ["--mode", "cache", "--cache", str(options.cache_dir)]
        else:
            command += ["--mode", "stream"]
        subprocess.run(command, check=True, stdout=sys.stderr)
        return options.out_path


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
        return sorted(p for p in self.records if SITE_MARKER not in p)
