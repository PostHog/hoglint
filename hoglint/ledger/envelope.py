"""The envelope of a run summary: which code, engine, policy and framework sources made it.

The envelope is the summary's cache key. A stored summary may stand in for a run only when its
whole envelope equals the one the run would have. Every part is built from file contents, so a
summary made on Linux CI and one made on a Mac agree:
- tree: the git tree oid of the code (a commit's tree, or the index's from `git write-tree`);
  empty for a working tree, which is never stored.
- engine: the engine name, then a digest of the summary protocol and the engine's sources (the
  Python package, the crate's Rust sources, manifests and Cargo.lock). Never a binary's hash or
  the platform.
- policy: a digest of the config hash, the exceptions as written and the content of every policy
  data file the config names (a rule's `classify` file, a pack's `policy_file_keys`).
- framework: a digest of the versions and sources of the `[discovery] external` packages that
  the extractor reads from site-packages.
`Envelope.key` prints the four as one string, for a CI cache step.
"""

from __future__ import annotations

import os
import hashlib
import subprocess
import dataclasses
import importlib.metadata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hoglint.config.model import Config
from hoglint.config.schema import as_tuple, canonical
from hoglint.facts import site_packages_files
from hoglint.plugins import REGISTRY

# The version of the run summary format. A reader refuses any other, and it is part of the engine id.
PROTOCOL = 1
ENGINE_PYTHON = "python"
ENGINE_SOURCE_SUFFIXES = frozenset({".py", ".pyi", ".rs", ".toml", ".lock"})
# Directories under the engine root that hold no engine source: tests, policy presets, build output.
ENGINE_SKIPPED_DIRS = frozenset({"tests", "presets", "target", "__pycache__"})
# The extractor's build id hashes these crate files after `src/*.rs`, in this order (build.rs).
CRATE_FILES = ("Cargo.toml", "Cargo.lock", "build.rs", "rust-toolchain.toml")
DIGEST_CHARS = 16
BUILD_ID_BYTES = 6
MISSING = "missing"


class EngineError(Exception):
    """An engine that a comparison needs cannot run as pinned."""


def short_digest(parts: Iterable[str | bytes]) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode() if isinstance(part, str) else part)
        hasher.update(b"\0")
    return hasher.hexdigest()[:DIGEST_CHARS]


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else MISSING


@dataclass(frozen=True, slots=True)
class Envelope:
    tree: str = ""
    engine: str = ""
    policy: str = ""
    framework: str = ""

    def to_json(self) -> dict[str, str]:
        return dataclasses.asdict(self)

    @staticmethod
    def from_json(data: dict[str, Any]) -> Envelope:
        return Envelope(
            str(data.get("tree", "")),
            str(data.get("engine", "")),
            str(data.get("policy", "")),
            str(data.get("framework", "")),
        )

    def key(self) -> str:
        """The envelope as one printable string, for a CI cache step (actions/cache `key`)."""
        digest = hashlib.sha256("\0".join((self.tree, self.engine, self.policy, self.framework)).encode())
        return f"hoglint-summary-p{PROTOCOL}-{digest.hexdigest()[:32]}"

    def causes(self, other: Envelope) -> list[str]:
        """What differs between two scans of the same code, named as a reviewer reads it."""
        out = []
        if self.engine != other.engine:
            out.append("scanner")
        if self.policy != other.policy:
            out.append("policy")
        if self.framework != other.framework:
            out.append("environment")
        return out


class BuildId:
    """The extractor build id: of a binary, and of a crate's sources (the same hash build.rs makes)."""

    @staticmethod
    def of_sources(crate: Path) -> str | None:
        """None when the directory holds no extractor crate."""
        source_dir = crate / "src"
        if not (crate / "Cargo.toml").is_file() or not source_dir.is_dir():
            return None
        files = sorted(path for path in source_dir.iterdir() if path.suffix == ".rs")
        files += [crate / name for name in CRATE_FILES]
        hasher = hashlib.sha256()
        for path in files:
            hasher.update(path.relative_to(crate).as_posix().encode())
            hasher.update(b"\0")
            hasher.update(path.read_bytes() if path.is_file() else b"")
            hasher.update(b"\0")
        return hasher.digest()[:BUILD_ID_BYTES].hex()

    @staticmethod
    def of_binary(binary: Path) -> str:
        try:
            completed = subprocess.run([str(binary), "--build-id"], capture_output=True, text=True, timeout=60)
        except OSError as error:
            raise EngineError(f"cannot run {binary}: {error}") from error
        if completed.returncode != 0 or not completed.stdout.strip():
            raise EngineError(f"{binary} --build-id failed: {completed.stderr.strip()[-500:]}")
        return completed.stdout.strip()


class EngineId:
    """An engine's id, from its sources alone.

    `root` is the engine root: the crate directory when the engine ships its sources (the Python
    package inside it, Rust sources, manifests, Cargo.lock), else the installed Python package.
    An installed engine has no crate, so the extractor build id stands for it: build.rs makes
    that id from the same crate sources, so it is no binary's own hash either."""

    @staticmethod
    def source_files(root: Path) -> list[Path]:
        out = []
        for directory, names, files in os.walk(root):
            names[:] = sorted(n for n in names if n not in ENGINE_SKIPPED_DIRS and not n.startswith("."))
            out += [Path(directory) / name for name in files if Path(name).suffix in ENGINE_SOURCE_SUFFIXES]
        return sorted(out, key=lambda path: path.relative_to(root).as_posix())

    @staticmethod
    def root_of(package_dir: Path) -> Path:
        """The crate directory around a Python package that ships with its crate, else the package."""
        crate = package_dir.parent
        return crate if (crate / "Cargo.toml").is_file() else package_dir

    @staticmethod
    def of(root: Path, name: str = ENGINE_PYTHON, build_id: str = "") -> str:
        parts: list[str | bytes] = [str(PROTOCOL)]
        for path in EngineId.source_files(root):
            parts += [path.relative_to(root).as_posix(), path.read_bytes()]
        if not (root / "Cargo.toml").is_file():
            parts.append(build_id)
        return f"{name}-{short_digest(parts)}"


class Framework:
    """Versions and sources of the external packages the extractor reads from site-packages."""

    @staticmethod
    def digest(packages: tuple[str, ...]) -> str:
        parts: list[str | bytes] = []
        for package in sorted(packages):
            try:
                version = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                version = ""
            parts += [package, version]
        files = site_packages_files(tuple(sorted(packages)))
        for path, name in sorted(files.items(), key=lambda item: item[1]):
            parts += [name, file_digest(Path(path))]
        return short_digest(parts)

    @staticmethod
    def of_config(config: Config) -> str:
        return Framework.digest(as_tuple(config.discovery.get("external")))


class PolicyFiles:
    """Data files the policy names: each rule's `classify` file and each active pack's
    `policy_file_keys` values. Paths are relative to the policy root."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def rule_files(self) -> dict[str, str]:
        return {spec.id: spec.classify for spec in self.config.rules.values() if spec.classify}

    def pack_files(self) -> dict[tuple[str, str], str]:
        out: dict[tuple[str, str], str] = {}
        for name in self.config.pack_sections:
            section = self.config.sections.get(name)
            pack = REGISTRY.packs.get(name)
            if pack is None or not isinstance(section, dict):
                continue
            for key in getattr(pack, "policy_file_keys", ()):
                value = section.get(key)
                if isinstance(value, str) and value:
                    out[(name, key)] = value
        return out

    def paths(self) -> list[str]:
        return sorted({*self.rule_files().values(), *self.pack_files().values()})

    def digests(self, root: Path) -> dict[str, str]:
        return {path: file_digest(root / path) for path in self.paths()}

    def anchored(self, policy_root: Path, code_root: Path) -> Config:
        """The config with every data file that differs between the two roots read from the
        policy root, so a run of one policy over other code reads that policy's files."""
        if policy_root.resolve() == code_root.resolve():
            return self.config
        differs = {path for path in self.paths() if file_digest(policy_root / path) != file_digest(code_root / path)}
        if not differs:
            return self.config
        rules = {
            rule_id: dataclasses.replace(spec, classify=str(policy_root / spec.classify))
            if spec.classify in differs
            else spec
            for rule_id, spec in self.config.rules.items()
        }
        sections = dict(self.config.sections)
        for (name, key), path in self.pack_files().items():
            if path in differs:
                sections[name] = {**sections[name], key: str(policy_root / path)}
        return dataclasses.replace(self.config, rules=rules, sections=sections)


def policy_digest(config: Config, exceptions: list[dict[str, Any]], problems: list[str], root: Path) -> str:
    files = PolicyFiles(config).digests(root)
    return short_digest([config.policy_hash, canonical(exceptions), canonical(problems), canonical(files)])
