"""Channel C: `tach map --direction dependents` from import and string facts (spike C semantics)."""

from __future__ import annotations

from hoglint.spike.core import SITE_MARKER, TC_ELSE, TYPE_ONLY, Core

STRING = 128  # binding from a dotted string literal part, not an import statement


def file_module_path(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "."


def is_potential_module_path(value: str) -> bool:
    if not value:
        return False
    for part in value.split("."):
        if not part:
            return False
        for char in part:
            if not (char.isalnum() or char == "_" or char == "*"):
                return False
    return True


def is_tach_string(value: str | None) -> bool:
    return value is not None and value.count(".") >= 2 and is_potential_module_path(value)


class Bindings:
    """(module path, flags) per file, like spike C's Extractor in `tokens` string mode."""

    def __init__(self, rel: str, record: dict) -> None:
        self.rel = rel
        self.record = record
        self.mod_path = file_module_path(rel)
        self.is_package = rel.endswith("__init__.py")

    def normalize_from(self, module: str | None, level: int, name: str) -> str | None:
        strip = max(level - 1, 0) if self.is_package else level
        if level > 0 and self.mod_path in ("", "."):
            return None
        base_parts = self.mod_path.split(".")
        if strip > len(base_parts):
            return None
        if strip > 0:
            base_parts = base_parts[: len(base_parts) - strip]
        if module is not None:
            base = ".".join(base_parts) + "." + module if level > 0 and base_parts else module
        else:
            if not base_parts:
                return None
            base = ".".join(base_parts)
        return f"{base}.{name}"

    def collect(self) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for _, _, _, flags, module, level, name, _ in self.record.get("imports", ()):
            if name is None:
                out.append((module, flags))
                continue
            path = self.normalize_from(module, level, name)
            if path is not None:
                out.append((path, flags))
        for row in self.record.get("strs", ()):
            parts = row[7] if len(row) > 7 else None
            # tach checks each literal part on its own; a single literal is its own part.
            for value in parts if parts is not None else [row[4]]:
                if is_tach_string(value):
                    out.append((value, STRING))
        return out


class Resolver:
    """tach's module_to_file_path(check_members=True) against an inventory of known paths."""

    def __init__(self, inventory: set[str], case_insensitive: bool) -> None:
        self.inventory = inventory
        self.lower = {p.lower() for p in inventory} if case_insensitive else None
        self.cache: dict[str, str | None] = {}

    def exists(self, candidate: str) -> bool:
        if self.lower is not None:
            return candidate.lower() in self.lower
        return candidate in self.inventory

    def first_existing(self, base: str) -> str | None:
        for candidate in (base + "/__init__.pyi", base + "/__init__.py", base + ".pyi", base + ".py"):
            if self.exists(candidate):
                return candidate
        return None

    def resolve(self, mod_path: str) -> str | None:
        if mod_path in self.cache:
            return self.cache[mod_path]
        result = None
        if is_potential_module_path(mod_path):
            fs_path = mod_path.replace(".", "/")
            result = self.first_existing(fs_path)
            if result is None and "/" in fs_path:
                result = self.first_existing(fs_path.rsplit("/", 1)[0])
        self.cache[mod_path] = result
        return result


class ImportMap:
    def __init__(self, core: Core, walked: list[str], case_insensitive: bool) -> None:
        self.core = core
        self.walked = walked
        inventory = {p for p in core.store.records if SITE_MARKER not in p}
        self.resolver = Resolver(inventory, case_insensitive)
        self.forward: dict[str, dict[str, list[int]]] = {}
        self.parse_failed: list[str] = []

    def build(self) -> None:
        for rel in self.walked:
            record = self.core.store.records[rel]
            if record.get("status") != "ok":
                self.parse_failed.append(rel)
                continue
            edges: dict[str, list[int]] = {}
            for mod_path, flags in Bindings(rel, record).collect():
                target = self.resolver.resolve(mod_path)
                if target is not None:
                    edges.setdefault(target, []).append(flags)
            self.forward[rel] = edges

    def dependents(self) -> dict[str, list[str]]:
        reverse: dict[str, set[str]] = {}
        for importer, edges in self.forward.items():
            for target, flag_list in edges.items():
                if any(not flags & (TYPE_ONLY | TC_ELSE) for flags in flag_list):
                    reverse.setdefault(target, set()).add(importer)
        return {k: sorted(v) for k, v in sorted(reverse.items())}


def run(core: Core, walked: list[str], case_insensitive: bool = True) -> dict:
    """`walked` is the tach file set (spike C discovery, i.e. the paths of out.jsonl)."""
    graph = ImportMap(core, walked, case_insensitive)
    graph.build()
    return {"map": graph.dependents(), "parse_failed": graph.parse_failed}
