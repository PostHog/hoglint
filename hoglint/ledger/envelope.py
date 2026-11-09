"""The envelope of a run summary: which code, engine, policy and framework sources made it.

The envelope is the summary's cache key. A stored summary may stand in for a run only when its
whole envelope equals the one the run would have. Every part is built from file contents, so a
summary made on Linux CI and one made on a Mac agree:
- tree: the git tree oid of the code (a commit's tree, or the index's from `git write-tree`);
  empty for a working tree, which is never stored.
- engine: the engine name, then the digest of the files `engine-sources.txt` lists (Python
  package, Rust sources, manifests, Cargo.lock) and the summary protocol. Never a binary's hash or
  the platform.
- policy: a digest of the config hash, the exceptions as written and the content of every policy
  data file the config names (a rule's `classify` file, a pack's `policy_file_keys`).
- framework: a digest of the versions and sources of the `[discovery] external` packages that
  the extractor reads from site-packages.
`Envelope.key` prints the four as one string, for a CI cache step.
"""

from __future__ import annotations

import os
import fnmatch
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

# The version of the run summary format. A reader refuses any other; engine-sources.txt repeats it.
PROTOCOL = 1
ENGINE_PYTHON = "python"
ENGINE_SOURCES = "engine-sources.txt"
PROTOCOL_LINE = "protocol "
# build.rs before it read engine-sources.txt hashed `src/*.rs`, then these crate files in this order.
LEGACY_CRATE_FILES = ("Cargo.toml", "Cargo.lock", "build.rs", "rust-toolchain.toml")
DIGEST_CHARS = 16
LEGACY_BUILD_ID_BYTES = 6
ENGINE_DIGEST_BYTES = 8
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


def glob_match(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    """`**` matches any number of segments, `*` any characters within one segment."""
    if not pattern:
        return not path
    if pattern[0] == "**":
        return any(glob_match(pattern[1:], path[index:]) for index in range(len(path) + 1))
    return bool(path) and fnmatch.fnmatchcase(path[0], pattern[0]) and glob_match(pattern[1:], path[1:])


@dataclass(frozen=True, slots=True)
class EngineSources:
    """The files that define an engine, as `engine-sources.txt` in its crate lists them, and their
    digest. build.rs reads the same file and makes the same digest (see the file's header)."""

    crate: Path
    protocol: str
    patterns: tuple[tuple[str, ...], ...]

    @staticmethod
    def read(crate: Path) -> EngineSources | None:
        """None when the crate has no list: an engine from before the list existed."""
        listing = crate / ENGINE_SOURCES
        if not listing.is_file():
            return None
        protocol, patterns = "", []
        for raw in listing.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(PROTOCOL_LINE):
                protocol = line.removeprefix(PROTOCOL_LINE).strip()
                continue
            patterns.append(tuple(line.split("/")))
        return EngineSources(crate, protocol, tuple(patterns))

    def files(self) -> list[str]:
        out = []
        for directory, names, files in os.walk(self.crate):
            names[:] = [name for name in names if name != "target" and not name.startswith(".")]
            for name in files:
                rel = (Path(directory) / name).relative_to(self.crate).as_posix()
                if any(glob_match(pattern, tuple(rel.split("/"))) for pattern in self.patterns):
                    out.append(rel)
        return sorted(out, key=lambda rel: rel.encode())

    def digest(self) -> str:
        hasher = hashlib.sha256()
        hasher.update(f"{PROTOCOL_LINE}{self.protocol}".encode())
        hasher.update(b"\0")
        for rel in self.files():
            hasher.update(rel.encode())
            hasher.update(b"\0")
            hasher.update((self.crate / rel).read_bytes())
            hasher.update(b"\0")
        return hasher.digest()[:ENGINE_DIGEST_BYTES].hex()


class BuildId:
    """The extractor build id: of a binary, and of a crate's sources (the hash its build.rs makes)."""

    @staticmethod
    def of_sources(crate: Path) -> str | None:
        """None when the directory holds no extractor crate. A build.rs that reads
        engine-sources.txt makes the engine digest; an older one hashed `src/*.rs` and the crate files."""
        source_dir = crate / "src"
        if not (crate / "Cargo.toml").is_file() or not source_dir.is_dir():
            return None
        sources = EngineSources.read(crate)
        build = crate / "build.rs"
        if sources is not None and build.is_file() and ENGINE_SOURCES in build.read_text():
            return sources.digest()
        files = sorted(path for path in source_dir.iterdir() if path.suffix == ".rs")
        files += [crate / name for name in LEGACY_CRATE_FILES]
        hasher = hashlib.sha256()
        for path in files:
            hasher.update(path.relative_to(crate).as_posix().encode())
            hasher.update(b"\0")
            hasher.update(path.read_bytes() if path.is_file() else b"")
            hasher.update(b"\0")
        return hasher.digest()[:LEGACY_BUILD_ID_BYTES].hex()

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
    """An engine's id: its name and the digest of its engine sources.

    An engine that ships its crate (a checkout) has `engine-sources.txt` next to its Python
    package. An installed engine has no crate, so its extractor build id stands for the digest:
    a build.rs that reads the list makes the build id from the same sources."""

    @staticmethod
    def crate_of(package_dir: Path) -> Path | None:
        crate = package_dir.parent
        return crate if (crate / ENGINE_SOURCES).is_file() else None

    @staticmethod
    def of(crate: Path, name: str = ENGINE_PYTHON) -> str:
        sources = EngineSources.read(crate)
        if sources is None:
            raise EngineError(f"{crate} has no {ENGINE_SOURCES}: the engine predates run summary protocol {PROTOCOL}")
        return f"{name}-{sources.digest()}"

    @staticmethod
    def installed(name: str, build_id: str) -> str:
        return f"{name}-{build_id}"


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
