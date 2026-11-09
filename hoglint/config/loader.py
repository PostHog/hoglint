"""Read hoglint.toml and everything it extends into one merged table with the source of each key.

Merge rules:
- `extends` loads packs (`hoglint:<name>`) and presets (repo-relative paths) first, in order.
- A named entry (`[rules.x]`, `[fields.x]`, a zone, ...) defined twice is an error, unless the
  later one says `replace = true` (the whole entry is replaced) or holds only list patches
  (`key = { add = [...] }` or `{ remove = [...] }`).
- A setting in a plain table (`[discovery]`, `[ledger]`, ...) set twice is an error unless the
  value is a list patch or the later table says `replace = true`.
- `[[assign]]` entries append.
- `[vocabulary]` lists are unions: packs add edge kinds and use shapes.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"
PACK_PREFIX = "hoglint:"

NAMED_SECTIONS = (
    "fields",
    "kinds",
    "exports",
    "shapes",
    "registries",
    "providers",
    "rules",
    "levels",
    "outputs",
    "identities",
)
APPEND_SECTIONS = ("assign",)
UNION_SECTIONS = ("vocabulary",)
ZONE_SETTINGS = ("within", "default")


class ConfigError(Exception):
    """One or more problems in the policy files. hoglint runs no rule while any exists."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


def is_patch_op(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and set(value) <= {"add", "remove"}


@dataclass(frozen=False, slots=True)
class MergedConfig:
    """The expanded config: one table, plus where each key came from."""

    data: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def note(self, key: str, source: str, patched: bool = False) -> None:
        if patched and key in self.provenance:
            self.provenance[key] = f"{self.provenance[key]}, patched by {source}"
            return
        self.provenance[key] = source


class ConfigLoader:
    """Loads the root file and its `extends` chain from one repo root."""

    def __init__(self, repo_root: Path, packs_dir: Path = PACKS_DIR) -> None:
        self.repo_root = repo_root
        self.packs_dir = packs_dir

    def load(self, root_file: Path) -> MergedConfig:
        merged = MergedConfig()
        self.load_into(merged, root_file, self.label(root_file), stack=())
        if merged.problems:
            raise ConfigError(merged.problems)
        return merged

    def label(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.repo_root.resolve()))
        except ValueError:
            if path.resolve().is_relative_to(self.packs_dir.resolve()):
                return PACK_PREFIX + path.resolve().parent.name
            return str(path)

    def locate(self, entry: str) -> Path | None:
        if entry.startswith(PACK_PREFIX):
            pack = self.packs_dir / entry.removeprefix(PACK_PREFIX) / "pack.toml"
            return pack if pack.is_file() else None
        path = self.repo_root / entry
        return path if path.is_file() else None

    def load_into(self, merged: MergedConfig, path: Path, label: str, stack: tuple[str, ...]) -> None:
        if label in stack:
            merged.problems.append(f"{label}: extends cycle {' -> '.join((*stack, label))}")
            return
        if label in merged.files:
            return
        try:
            raw = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as error:
            merged.problems.append(f"{label}: cannot read: {error}")
            return
        if raw.get("schema") != 1:
            merged.problems.append(f"{label}: `schema = 1` is required")
        extends = raw.pop("extends", [])
        raw.pop("schema", None)
        if isinstance(extends, str):
            extends = [extends]
        for entry in extends:
            located = self.locate(entry)
            if located is None:
                merged.problems.append(f"{label}: extends {entry!r}, which does not exist")
                continue
            self.load_into(
                merged, located, self.label(located) if not entry.startswith(PACK_PREFIX) else entry, (*stack, label)
            )
        merged.files.append(label)
        Merger(merged, label).merge(raw)


class Merger:
    """Merges one file's table into the accumulated config."""

    def __init__(self, merged: MergedConfig, source: str) -> None:
        self.merged = merged
        self.source = source

    def fail(self, message: str) -> None:
        self.merged.problems.append(f"{self.source}: {message}")

    def merge(self, raw: dict[str, Any]) -> None:
        data = self.merged.data
        for section, value in raw.items():
            if section in APPEND_SECTIONS:
                self.append(section, value)
            elif section in UNION_SECTIONS:
                self.union(section, value)
            elif section in NAMED_SECTIONS:
                self.merge_named(section, value)
            elif section == "zones":
                self.merge_zones(value)
            elif isinstance(value, dict):
                self.merge_table(section, value, data.setdefault(section, {}))
            elif section in data:
                self.fail(f"`{section}` is already set by {self.merged.provenance.get(section, '?')}")
            else:
                data[section] = value
                self.merged.note(section, self.source)

    def append(self, section: str, value: Any) -> None:
        if not isinstance(value, list):
            self.fail(f"`{section}` must be an array of tables ([[{section}]])")
            return
        entries = self.merged.data.setdefault(section, [])
        for entry in value:
            if not isinstance(entry, dict):
                self.fail(f"each [[{section}]] must be a table")
                continue
            entry = dict(entry)
            entry["__source__"] = self.source
            self.merged.note(f"{section}[{len(entries)}]", self.source)
            entries.append(entry)

    def union(self, section: str, value: Any) -> None:
        if not isinstance(value, dict):
            self.fail(f"`{section}` must be a table")
            return
        target = self.merged.data.setdefault(section, {})
        for key, items in value.items():
            if not isinstance(items, list):
                self.fail(f"`{section}.{key}` must be a list")
                continue
            existing = target.setdefault(key, [])
            existing.extend(item for item in items if item not in existing)
            self.merged.note(f"{section}.{key}", self.source, patched=True)

    def merge_named(self, section: str, value: Any) -> None:
        if not isinstance(value, dict):
            self.fail(f"`{section}` must be a table of named entries")
            return
        target = self.merged.data.setdefault(section, {})
        for name, entry in value.items():
            if not isinstance(entry, dict):
                if section == "identities" and isinstance(entry, list):
                    entry = {"columns": entry}
                else:
                    self.fail(f"`{section}.{name}` must be a table")
                    continue
            self.merge_entry(f"{section}.{name}", target, name, entry)

    def merge_entry(self, key: str, target: dict[str, Any], name: str, entry: dict[str, Any]) -> None:
        entry = dict(entry)
        replace = entry.pop("replace", False) is True
        if name not in target:
            if any(is_patch_op(v) for v in entry.values()):
                self.fail(f"`{key}` patches a list, but nothing defines `{key}` before this file")
                return
            target[name] = copy.deepcopy(entry)
            for sub in entry:
                self.merged.note(f"{key}.{sub}", self.source)
            self.merged.note(key, self.source)
            return
        if replace:
            target[name] = copy.deepcopy(entry)
            for stale in [k for k in self.merged.provenance if k.startswith(f"{key}.")]:
                del self.merged.provenance[stale]
            for sub in entry:
                self.merged.note(f"{key}.{sub}", self.source)
            self.merged.note(key, f"{self.source} (replaces the earlier definition)")
            return
        if entry and all(is_patch_op(v) for v in entry.values()):
            self.apply_patches(key, target[name], entry)
            return
        first = self.merged.provenance.get(key, "?")
        self.fail(
            f"`{key}` is already defined in {first}; set `replace = true` to replace it, "
            "or patch lists with `{ add = [...] }` / `{ remove = [...] }`"
        )

    def apply_patches(self, key: str, existing: dict[str, Any], patch: dict[str, Any]) -> None:
        for sub, op in patch.items():
            current = existing.get(sub, [])
            if not isinstance(current, list):
                self.fail(f"`{key}.{sub}` is not a list, so it cannot be patched")
                continue
            for item in op.get("remove", []):
                if item not in current:
                    self.fail(f"`{key}.{sub}` has no {item!r} to remove")
                    continue
                current = [c for c in current if c != item]
            current = current + [item for item in op.get("add", []) if item not in current]
            existing[sub] = current
            self.merged.note(f"{key}.{sub}", self.source, patched=True)

    def merge_table(self, section: str, value: dict[str, Any], target: dict[str, Any]) -> None:
        if value.get("replace") is True:
            target.clear()
            for stale in [k for k in self.merged.provenance if k.startswith(f"{section}.")]:
                del self.merged.provenance[stale]
        for key, item in value.items():
            if key == "replace":
                continue
            path = f"{section}.{key}"
            if is_patch_op(item):
                if key not in target:
                    self.fail(f"`{path}` patches a list, but nothing sets it before this file")
                    continue
                holder = {key: target[key]}
                self.apply_patches(section, holder, {key: item})
                target[key] = holder[key]
                continue
            if key in target:
                self.fail(f"`{path}` is already set by {self.merged.provenance.get(path, '?')}")
                continue
            target[key] = copy.deepcopy(item)
            self.merged.note(path, self.source)

    def merge_zones(self, value: Any) -> None:
        if not isinstance(value, dict):
            self.fail("`zones` must be a table")
            return
        target = self.merged.data.setdefault("zones", {})
        for name, entry in value.items():
            if name in ZONE_SETTINGS:
                if is_patch_op(entry) and name in target:
                    holder = {name: target[name] if isinstance(target[name], list) else [target[name]]}
                    self.apply_patches("zones", holder, {name: entry})
                    target[name] = holder[name]
                    continue
                if name in target and target[name] != entry:
                    self.fail(f"`zones.{name}` is already set by {self.merged.provenance.get(f'zones.{name}', '?')}")
                    continue
                target[name] = entry
                self.merged.note(f"zones.{name}", self.source)
                continue
            if not isinstance(entry, dict):
                self.fail(f"`zones.{name}` must be a table")
                continue
            self.merge_entry(f"zones.{name}", target, name, entry)
