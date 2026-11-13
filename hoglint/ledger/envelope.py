"""The envelope of a run summary: which code, engine, policy and framework sources made it.

The envelope is the summary's cache key. A stored summary may stand in for a run only when its
whole envelope equals the one the run would have. Every part is built from file contents, so a
summary made on Linux CI and one made on a Mac agree:
- inputs: a digest of (path, blob oid) over the git tree's run inputs, sorted by path: its
  sources, the side inputs the config's plugins and layout rules declare, and the ledger (see
  `RunInputs` in inputs.py). A tree is a commit's, or the index's from `git write-tree`. A
  change to any other file keeps the key. Empty for a working tree that differs from HEAD in an
  input, which is never stored.
- engine: the engine name, then the `[engine]` id of `engine-sources.txt`: a digest of the Rust
  and Python sources, manifests, Cargo.lock and the summary protocol. Never a binary's hash or
  the platform.
- policy: a digest of the config hash, the exceptions as written and the content of every policy
  data file the config names (a rule's `classify` file, a pack's `policy_file_keys`).
- framework: a digest of the name, version and RECORD of each installed distribution that ships
  a `[discovery] external` package, the packages the extractor reads from site-packages.
`Envelope.key` prints the four as one string, for a CI cache step.
"""

from __future__ import annotations

import os
import base64
import fnmatch
import hashlib
import sysconfig
import subprocess
import dataclasses
import importlib.util
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path
from typing import Any, ClassVar

from hoglint.config.loader import PACK_PREFIX
from hoglint.config.model import Config
from hoglint.config.schema import as_tuple, canonical
from hoglint.core.config import CoreConfig
from hoglint.ledger.cache import answer_key
from hoglint.ledger.errors import EngineError
from hoglint.plugins import REGISTRY

# The version of the run summary format. A reader refuses any other; engine-sources.txt repeats it.
PROTOCOL = 4
# The engine's name: the prefix of every engine id, and what `--engine` accepts.
ENGINE_NATIVE = "native"
ENGINE_SOURCES = "engine-sources.txt"
PROTOCOL_LINE = "protocol "
# The sections of engine-sources.txt, each adding files to the one before it.
FACTS, NATIVE, ENGINE = "facts", "native", "engine"
SECTIONS = (FACTS, NATIVE, ENGINE)
# The flag that makes a hoglint-facts binary print each id.
ID_FLAGS = {FACTS: "--facts-id", NATIVE: "--native-id", ENGINE: "--build-id"}
DIGEST_CHARS = 16
SOURCE_DIGEST_BYTES = 8
MISSING = "missing"


def short_digest(parts: Iterable[str | bytes]) -> str:
    return answer_key(*parts, chars=DIGEST_CHARS)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else MISSING


@dataclass(frozen=True, slots=True)
class Envelope:
    inputs: str = ""
    engine: str = ""
    policy: str = ""
    framework: str = ""

    def to_json(self) -> dict[str, str]:
        return dataclasses.asdict(self)

    @staticmethod
    def from_json(data: dict[str, Any]) -> Envelope:
        return Envelope(
            str(data.get("inputs", "")),
            str(data.get("engine", "")),
            str(data.get("policy", "")),
            str(data.get("framework", "")),
        )

    def key(self) -> str:
        """The envelope as one printable string, for a CI cache step (actions/cache `key`)."""
        digest = hashlib.sha256("\0".join((self.inputs, self.engine, self.policy, self.framework)).encode())
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
class SourceSection:
    """The files one section of engine-sources.txt adds: those matching a pattern and no `!` pattern."""

    patterns: tuple[tuple[str, ...], ...] = ()
    excludes: tuple[tuple[str, ...], ...] = ()

    def adds(self, parts: tuple[str, ...]) -> bool:
        return any(glob_match(p, parts) for p in self.patterns) and not any(glob_match(p, parts) for p in self.excludes)


@dataclass(frozen=True, slots=True)
class EngineSources:
    """The three source sets `engine-sources.txt` in a crate defines, and their ids. build.rs reads
    the same file and makes the same ids (see the file's header)."""

    crate: Path
    protocol: str
    sections: tuple[SourceSection, ...]  # in SECTIONS order

    @staticmethod
    def read(crate: Path) -> EngineSources | None:
        """None when the crate has no list: an engine from before the list existed."""
        listing = crate / ENGINE_SOURCES
        if not listing.is_file():
            return None
        protocol, current = "", ""
        patterns: dict[str, list[tuple[str, ...]]] = {name: [] for name in SECTIONS}
        excludes: dict[str, list[tuple[str, ...]]] = {name: [] for name in SECTIONS}
        for raw in listing.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1]
                if current not in patterns:
                    raise EngineError(f"{listing}: unknown section [{current}]")
                continue
            if not current:
                raise EngineError(f"{listing}: {line!r} is outside a section")
            if line.startswith(PROTOCOL_LINE):
                protocol = line.removeprefix(PROTOCOL_LINE).strip()
            elif line.startswith("!"):
                excludes[current].append(tuple(line[1:].split("/")))
            else:
                patterns[current].append(tuple(line.split("/")))
        sections = tuple(SourceSection(tuple(patterns[name]), tuple(excludes[name])) for name in SECTIONS)
        return EngineSources(crate, protocol, sections)

    def crate_files(self) -> list[str]:
        out = []
        for directory, names, files in os.walk(self.crate):
            names[:] = [name for name in names if name != "target" and not name.startswith(".")]
            out += [(Path(directory) / name).relative_to(self.crate).as_posix() for name in files]
        return out

    def files(self, section: str) -> list[str]:
        """The section's files: those of every section up to it, sorted by path bytes."""
        wanted = self.sections[: SECTIONS.index(section) + 1]
        out = [rel for rel in self.crate_files() if any(part.adds(tuple(rel.split("/"))) for part in wanted)]
        return sorted(out, key=lambda rel: rel.encode())

    def digest(self, section: str = ENGINE) -> str:
        hasher = hashlib.sha256()
        if section == ENGINE:
            hasher.update(f"{PROTOCOL_LINE}{self.protocol}".encode())
            hasher.update(b"\0")
        for rel in self.files(section):
            hasher.update(rel.encode())
            hasher.update(b"\0")
            hasher.update((self.crate / rel).read_bytes())
            hasher.update(b"\0")
        return hasher.digest()[:SOURCE_DIGEST_BYTES].hex()


class BinaryIds:
    """The ids a hoglint-facts binary reports about the sources it was built from."""

    @staticmethod
    def of(binary: Path, section: str) -> str:
        flag = ID_FLAGS[section]
        try:
            completed = subprocess.run([str(binary), flag], capture_output=True, text=True, timeout=60)
        except OSError as error:
            raise EngineError(f"cannot run {binary}: {error}") from error
        if completed.returncode != 0 or not completed.stdout.strip():
            raise EngineError(f"{binary} {flag} failed, so it predates its sources: {completed.stderr.strip()[-300:]}")
        return completed.stdout.strip()


class EngineId:
    """An engine's id: its name and the digest of its engine sources.

    An engine that ships its crate (a checkout) has `engine-sources.txt` next to its Python
    package. An installed engine has no crate, so its binary's engine id stands for the digest:
    build.rs makes that id from the same list."""

    @staticmethod
    def crate_of(package_dir: Path) -> Path | None:
        crate = package_dir.parent
        return crate if (crate / ENGINE_SOURCES).is_file() else None

    @staticmethod
    def digest(crate: Path) -> str:
        sources = EngineSources.read(crate)
        if sources is None:
            raise EngineError(f"{crate} has no {ENGINE_SOURCES}: the engine predates run summary protocol {PROTOCOL}")
        return sources.digest(ENGINE)

    @staticmethod
    def of(crate: Path, name: str = ENGINE_NATIVE) -> str:
        return f"{name}-{EngineId.digest(crate)}"

    @staticmethod
    @cache
    def running_digest(crate: Path) -> str:
        """`digest` of the running code's crate: it does not change while the process runs, so
        its sources are read and hashed once per process."""
        return EngineId.digest(crate)

    @staticmethod
    def of_running(crate: Path, name: str = ENGINE_NATIVE) -> str:
        """`of` for the crate of the running code."""
        return f"{name}-{EngineId.running_digest(crate)}"

    @staticmethod
    def installed(name: str, build_id: str) -> str:
        return f"{name}-{build_id}"


class Framework:
    """The installed distributions a run reads or runs: those that ship the `[discovery] external`
    packages, and those the active plugins declare they import (`distributions`, see plugins.py).
    For each: name and version (the dist-info directory) and the RECORD rows of the package's
    Python sources. Each row holds the file's hash, and an install or upgrade rewrites RECORD, so
    no source file is read. Other rows depend on the install, not on the code: a console script
    carries the venv's own path in its shebang, and the installer adds rows of its own
    (INSTALLER, REQUESTED, bytecode), so they would make every machine's digest differ.

    An editable install's RECORD lists no sources, only a path hook, so its rows are made the
    same way from the files where the import finds the package: the same code installed either
    way has one digest."""

    @staticmethod
    def site() -> Path:
        return Path(sysconfig.get_paths()["purelib"])

    @staticmethod
    def dist_infos(site: Path, packages: tuple[str, ...]) -> dict[str, Path]:
        """The dist-info directory of each package: the one named after it, else the one whose
        top_level.txt or RECORD names it. A package with no metadata is left out."""
        infos = sorted(site.glob("*.dist-info"))
        found: dict[str, Path] = {}
        by_name = {info.name.split("-", 1)[0].lower(): info for info in infos}
        for package in packages:
            named = by_name.get(package.lower().replace("-", "_"))
            if named is not None:
                found[package] = named
        for listing, provides in (("top_level.txt", Framework.top_level), ("RECORD", Framework.recorded)):
            missing = [package for package in packages if package not in found]
            for info in infos:
                if not missing:
                    break
                text = (info / listing).read_text() if (info / listing).is_file() else ""
                for package in [p for p in missing if provides(text, p)]:
                    found[package] = info
                    missing.remove(package)
        return found

    @staticmethod
    def top_level(text: str, package: str) -> bool:
        return package in text.split()

    @staticmethod
    def recorded(text: str, package: str) -> bool:
        return text.startswith(f"{package}/") or f"\n{package}/" in text

    @staticmethod
    def sources(record: str, package: str) -> list[str]:
        """The RECORD rows of the `.py` files under the package, as `site_packages_files` lists them."""
        rows = (row for row in record.splitlines() if row.startswith(f"{package}/"))
        return sorted(row for row in rows if row.split(",", 1)[0].endswith(".py"))

    @staticmethod
    def imported_sources(package: str) -> list[str]:
        """RECORD rows (`path,sha256=<urlsafe base64>,size`) of the .py files where the import
        finds the package, for an install whose RECORD lists none."""
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            return []
        rows = []
        for location in spec.submodule_search_locations:
            root = Path(location)
            for path in root.rglob("*.py"):
                data = path.read_bytes()
                hashed = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                rows.append(f"{package}/{path.relative_to(root).as_posix()},sha256={hashed},{len(data)}")
        return sorted(rows)

    @staticmethod
    def digest(packages: tuple[str, ...]) -> str:
        infos = Framework.dist_infos(Framework.site(), packages)
        parts: list[str | bytes] = []
        for package in sorted(packages):
            info = infos.get(package)
            if info is None:
                parts += [package, MISSING]
                continue
            rows = Framework.sources((info / "RECORD").read_text(), package) or Framework.imported_sources(package)
            parts += [package, info.name, *rows]
        return short_digest(parts)

    @staticmethod
    def packages_of(config: Config) -> tuple[str, ...]:
        """`[discovery] external`, and the distributions the config's packs, providers and
        coverage adapters import."""
        packages = set(as_tuple(config.discovery.get("external")))
        plugins: list[object] = [REGISTRY.packs.get(name) for name in config.pack_sections]
        plugins += [REGISTRY.providers.get(spec.use) for spec in config.providers.values()]
        coverage = [spec for spec in config.rules.values() if spec.primitive == "coverage"]
        plugins += [REGISTRY.coverage_adapters.get(str(spec.input("adapter"))) for spec in coverage]
        for plugin in plugins:
            packages.update(getattr(plugin, "distributions", ()))
        return tuple(sorted(packages))

    @staticmethod
    def missing(packages: tuple[str, ...]) -> list[str]:
        """The packages no installed distribution ships, so no key part can name their code."""
        found = Framework.dist_infos(Framework.site(), packages)
        return [package for package in packages if package not in found]

    @staticmethod
    def of_config(config: Config) -> str:
        return Framework.digest(Framework.packages_of(config))


class ConfigInputs:
    """What a run of `config` reads from outside its snapshot: the config's sections, the files of
    its contract and export surfaces, and the policy data files it names (each rule's `classify`
    file and each active pack's `policy_file_keys` values, relative to the policy root). One per
    config object (`of`): the graph key, the pack memo key, the summary envelope and the
    predictor each take what they need from it, so none of them can miss an input."""

    taken: ClassVar[dict[int, ConfigInputs]] = {}

    def __init__(self, config: Config) -> None:
        self.config = config
        self.sections = {name: config.sections.get(name) for name in sorted(config.sections)}
        # The contract surfaces: the files of exports whose shape makes their signatures part of
        # their contract. The export surfaces: the files of every export.
        exports = config.exports.values()
        self.contract_surfaces = tuple(spec.files for spec in exports if config.shapes[spec.shape].signatures)
        self.export_surfaces = tuple(spec.files for spec in exports)

    @staticmethod
    def of(config: Config) -> ConfigInputs:
        found = ConfigInputs.taken.get(id(config))
        if found is None:
            found = ConfigInputs.taken[id(config)] = ConfigInputs(config)
        return found

    def key(self, sections: Mapping[str, Any] | None = None) -> str:
        """The inputs as one canonical text, which the graph key holds. `sections` stands in for
        the config's own: the pack memo names each policy file there by the bytes a pack reads."""
        return canonical(
            {
                "sections": self.sections if sections is None else sections,
                "contract_surfaces": [(surface.within, surface.paths) for surface in self.contract_surfaces],
                "export_surfaces": [(surface.within, surface.paths) for surface in self.export_surfaces],
            }
        )

    @cached_property
    def core(self) -> CoreConfig:
        """`[discovery]` and the surfaces as core reads them, parsed once: both engines and the
        rules read this one value."""
        return CoreConfig.from_discovery(self.config.discovery, self.contract_surfaces, self.export_surfaces)

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

    def policy_paths(self) -> list[str]:
        return sorted({*self.rule_files().values(), *self.pack_files().values()})

    def predicted_policy(self) -> tuple[str, ...]:
        """The files a prediction treats as policy: every config file of the repo (not a pack's
        own), and every policy data file."""
        configs = [label for label in self.config.files if not label.startswith(PACK_PREFIX)]
        return tuple(sorted({*configs, *self.policy_paths()}))


class PolicyFiles:
    """Data files the policy names (`ConfigInputs`). Paths are relative to the policy root."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.inputs = ConfigInputs.of(config)

    def paths(self) -> list[str]:
        return self.inputs.policy_paths()

    def digests(self, root: Path) -> dict[str, str]:
        return {path: file_digest(root / path) for path in self.paths()}

    def anchored(self, policy_root: Path, code_root: Path, always: bool = False) -> Config:
        """The config with every data file that differs between the two roots read from the
        policy root, so a run of one policy over other code reads that policy's files. With
        `always`, every data file is: a run of a git tree reads no file of the working tree."""
        if policy_root.resolve() == code_root.resolve():
            return self.config
        differs = {
            path for path in self.paths() if always or file_digest(policy_root / path) != file_digest(code_root / path)
        }
        if not differs:
            return self.config
        rules = {
            rule_id: dataclasses.replace(spec, classify=str(policy_root / spec.classify))
            if spec.classify in differs
            else spec
            for rule_id, spec in self.config.rules.items()
        }
        sections = dict(self.config.sections)
        for (name, key), path in self.inputs.pack_files().items():
            if path in differs:
                sections[name] = {**sections[name], key: str(policy_root / path)}
        return dataclasses.replace(self.config, rules=rules, sections=sections)


def policy_digest(config: Config, exceptions: list[dict[str, Any]], problems: list[str], root: Path) -> str:
    files = PolicyFiles(config).digests(root)
    return short_digest([config.policy_hash, canonical(exceptions), canonical(problems), canonical(files)])
