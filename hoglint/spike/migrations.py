"""Channel B: migration graph as MigrationLoader(connection=None).graph builds it (spike B semantics).

Settings, AppConfig classes and Migration bodies come from `assigns`, `defs` and `calls` facts.
The only non-fact input is products/db_routing.yaml (alias projection).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml

from hoglint.spike.core import CLASS, NO_CONST, SITE_MARKER, Core, const_of, is_const

SETTINGS_PROXIES = {"oauth2_settings": "OAUTH2_PROVIDER_"}


def value_text(value: object) -> str:
    """Stable text for an unresolved V value (stands in for ast.unparse)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def last_segment(value: object) -> str:
    if isinstance(value, str):
        return value.split(".")[-1]
    return value_text(value)


@dataclass(frozen=False)
class MigrationDecl:
    app_label: str
    name: str
    path: str
    dependencies: list[tuple[str, str]] = field(default_factory=list)
    run_before: list[tuple[str, str]] = field(default_factory=list)
    replaces: list[tuple[str, str]] = field(default_factory=list)
    initial: object = None
    unresolved: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AppDecl:
    entry: str
    label: str
    name: str


class SettingsReader:
    """Module-level str constants and lists, plus every INSTALLED_APPS.append(<const>)."""

    def __init__(self, records: list[dict]) -> None:
        self.strings: dict[str, str] = {}
        self.lists: dict[str, list] = {}
        self.appends: list[str] = []
        for record in records:
            self.read(record)

    def read(self, record: dict) -> None:
        for row in record.get("assigns", ()):
            if row["op"] == "=" and len(row["t"]) == 1:
                self.assign(row["t"][0], row.get("v"))
            elif row["op"] == ":" and "v" in row:
                self.assign(row["t"][0], row["v"])
        for row in record.get("calls", ()):
            if row[4] == "INSTALLED_APPS.append" and row[5] and is_const(row[5][0]):
                self.appends.append(const_of(row[5][0]))

    def assign(self, target: object, value: object) -> None:
        if not isinstance(target, str) or "." in target:
            return
        if isinstance(value, dict) and "s" in value and len(value) == 1:
            self.strings[target] = value["s"]
        elif isinstance(value, dict) and "list" in value:
            self.lists[target] = value["list"]

    def installed_apps(self) -> list[str]:
        out: list[str] = []
        for elt in self.lists["INSTALLED_APPS"]:
            if is_const(elt):
                out.append(const_of(elt))
            elif isinstance(elt, dict) and "star" in elt and isinstance(elt["star"], str) and "." not in elt["star"]:
                out.extend(const_of(e) for e in self.lists[elt["star"]] if is_const(e))
        return out + self.appends


def top_classes(record: dict) -> list[tuple[int, dict]]:
    """Class statements directly in the module body."""
    return [
        (i, d) for i, d in enumerate(record.get("defs", ())) if d["s"] == -1 and d["k"] == "class" and not d.get("f")
    ]


def class_body_assigns(record: dict, idx: int) -> list[dict]:
    """Assign / AnnAssign statements directly in the body of class `idx`."""
    flags = record["defs"][idx].get("f", 0) | CLASS
    return [row for row in record.get("assigns", ()) if row["s"] == idx and row.get("f", 0) == flags]


class AppResolver:
    """Replay AppConfig.create() on facts: entry -> (label, name)."""

    def __init__(self, core: Core) -> None:
        self.core = core
        self.index = core.index

    def config_classes(self, record: dict) -> dict[str, dict]:
        classes: dict[str, dict] = {}
        for idx, d in top_classes(record):
            bases = [last_segment(b) for b in d.get("bases", ())]
            attrs: dict = {}
            for row in class_body_assigns(record, idx):
                if row["op"] != "=":
                    continue
                target = row["t"][0]
                if isinstance(target, str) and "." not in target and is_const(row.get("v")):
                    attrs[target] = const_of(row["v"])
            classes[d["n"]] = {"bases": bases, "attrs": attrs}
        out: dict[str, dict] = {}
        for cname in classes:
            chain, cur, ok = [], cname, False
            while cur in classes:
                chain.append(classes[cur]["attrs"])
                nxt = classes[cur]["bases"][0] if classes[cur]["bases"] else None
                if nxt == "AppConfig" or (nxt and nxt.endswith("Config") and nxt not in classes):
                    ok = True
                    break
                cur = nxt
            if ok:
                merged: dict = {}
                for attrs in reversed(chain):
                    merged.update(attrs)
                out[cname] = merged
        return out

    def resolve(self, entry: str) -> AppDecl:
        if self.index.is_file_module(entry) and self.index.is_package(entry):
            apps_record = self.index.record(f"{entry}.apps")
            if apps_record is not None and not self.index.is_package(f"{entry}.apps"):
                configs = self.config_classes(apps_record)
                candidates = {k: v for k, v in configs.items() if v.get("default", True)}
                if len(candidates) != 1:
                    candidates = {k: v for k, v in candidates.items() if v.get("default", False)}
                if len(candidates) == 1:
                    return self.decl(entry, next(iter(candidates.values())))
            return AppDecl(entry, entry.rsplit(".", 1)[-1], entry)
        mod, _, cls = entry.rpartition(".")
        record = self.index.record(mod)
        if record is None:
            # An installed app without facts (a dev-only third-party app): label by convention.
            return AppDecl(entry, entry.rsplit(".", 1)[-1], entry)
        return self.decl(entry, self.config_classes(record)[cls])

    @staticmethod
    def decl(entry: str, attrs: dict) -> AppDecl:
        name = attrs["name"]
        return AppDecl(entry, attrs.get("label", name.rsplit(".", 1)[-1]), name)


class MigrationParser:
    """One migration record into a MigrationDecl."""

    def __init__(self, settings: SettingsReader) -> None:
        self.settings = settings

    def parse(self, app_label: str, path: str, record: dict) -> MigrationDecl:
        decl = MigrationDecl(app_label, Path(path).stem, path)
        constants = {}
        for row in record.get("assigns", ()):
            if row["s"] == -1 and not row.get("f") and row["op"] == "=" and len(row["t"]) == 1:
                target = row["t"][0]
                if isinstance(target, str) and "." not in target:
                    constants[target] = row.get("v")
        cls = next((i for i, d in top_classes(record) if d["n"] == "Migration"), None)
        if cls is None:
            decl.unresolved.append("no Migration class")
            return decl
        for row in class_body_assigns(record, cls):
            if row["op"] not in ("=", ":"):
                continue
            target = row["t"][0]
            if not isinstance(target, str) or "." in target:
                continue
            value = row.get("v")
            if target in ("dependencies", "run_before", "replaces"):
                setattr(decl, target, self.keys(value, constants, decl))
            elif target == "initial" and is_const(value):
                decl.initial = const_of(value)
        return decl

    def keys(self, node: object, constants: dict, decl: MigrationDecl) -> list[tuple[str, str]]:
        if isinstance(node, str) and "." not in node and node in constants:
            node = constants[node]
        if isinstance(node, dict) and node.get("bin") == "+":
            return self.keys(node["l"], constants, decl) + self.keys(node["r"], constants, decl)
        elts = node.get("list", node.get("tuple")) if isinstance(node, dict) else None
        if elts is None:
            decl.unresolved.append(value_text(node))
            return []
        out = []
        for elt in elts:
            key = self.key(elt, constants)
            if key is None:
                decl.unresolved.append(value_text(elt))
            else:
                out.append(key)
        return out

    def key(self, elt: object, constants: dict) -> tuple[str, str] | None:
        if isinstance(elt, dict):
            items = elt.get("tuple", elt.get("list"))
            if items is not None and all(isinstance(e, dict) and "s" in e and len(e) == 1 for e in items):
                return (items[0]["s"], items[1]["s"])
            if (
                "call" in elt
                and isinstance(elt["call"], str)
                and elt["call"].endswith("swappable_dependency")
                and elt.get("args")
            ):
                value = self.setting(elt["args"][0], constants)
                if value is None:
                    return None
                return (value.split(".", 1)[0], "__first__")
        return None

    def setting(self, node: object, constants: dict | None = None) -> str | None:
        if is_const(node):
            value = const_of(node)
            return None if value is NO_CONST else cast(str | None, value)
        if isinstance(node, str) and "." not in node:
            if constants and node in constants:
                return self.setting(constants[node], constants)
            return None
        if isinstance(node, dict) and node.get("bool") == "or":
            for value in node["vs"]:
                found = self.setting(value, constants)
                if found:
                    return found
            return None
        if isinstance(node, dict) and node.get("call") == "getattr" and node.get("args", [None])[0] == "settings":
            name_node = node["args"][1]
            if isinstance(name_node, dict) and name_node.get("call") == "setting_name":
                name = "SOCIAL_AUTH_" + "_".join(const_of(a) for a in name_node.get("args", ()))
            elif is_const(name_node):
                name = const_of(name_node)
            else:
                return None
            return self.settings.strings.get(name)
        if isinstance(node, str) and node.count(".") == 1:
            base, attr = node.split(".")
            if base == "settings":
                return self.settings.strings.get(attr)
            prefix = SETTINGS_PROXIES.get(base)
            if prefix:
                return self.settings.strings.get(prefix + attr)
        return None


class StaticGraph:
    """Replay MigrationLoader.build_graph() with connection=None (nothing applied)."""

    def __init__(self, decls: dict[tuple[str, str], MigrationDecl], migrated: set[str], unmigrated: set[str]) -> None:
        self.decls = decls
        self.migrated = migrated
        self.unmigrated = unmigrated
        self.nodes: set[tuple[str, str]] = set(decls)
        self.parents: dict[tuple[str, str], set] = {k: set() for k in decls}
        self.children: dict[tuple[str, str], set] = {k: set() for k in decls}
        self.dummies: set[tuple[str, str]] = set()
        self.unresolved_keys: list[str] = []

    def add(self, child, parent) -> None:
        for key in (child, parent):
            if key not in self.nodes:
                self.nodes.add(key)
                self.dummies.add(key)
                self.parents[key] = set()
                self.children[key] = set()
        self.parents[child].add(parent)
        self.children[parent].add(child)

    def roots(self, app):
        return sorted(n for n in self.nodes if n[0] == app and all(p[0] != app for p in self.parents[n]))

    def leaves(self, app):
        return sorted(n for n in self.nodes if n[0] == app and all(c[0] != app for c in self.children[n]))

    def check_key(self, key, current_app):
        if key[1] not in ("__first__", "__latest__") or key in self.nodes:
            return key
        if key[0] == current_app or key[0] in self.unmigrated:
            return None
        if key[0] in self.migrated:
            found = self.roots(key[0]) if key[1] == "__first__" else self.leaves(key[0])
            return found[0] if found else None
        self.unresolved_keys.append(f"unknown app {key}")
        return None

    def build(self) -> None:
        for key, m in self.decls.items():
            for parent in m.dependencies:
                if parent[0] == key[0] and parent[1] != "__first__":
                    self.add(key, parent)
        for key, m in self.decls.items():
            for parent in m.dependencies:
                if key[0] == parent[0]:
                    continue
                parent = self.check_key(parent, key[0])
                if parent is not None:
                    self.add(key, parent)
            for child in m.run_before:
                child = self.check_key(child, key[0])
                if child is not None:
                    self.add(child, key)
        for key, m in self.decls.items():
            if m.replaces:
                self.remove_replaced(key, m.replaces)

    def remove_replaced(self, replacement, replaced_list) -> None:
        replaced = set(replaced_list)
        for rk in replaced:
            if rk not in self.parents:
                self.nodes.discard(rk)
                continue
            self.nodes.discard(rk)
            self.dummies.discard(rk)
            for child in self.children.pop(rk):
                self.parents[child].discard(rk)
                if child not in replaced:
                    self.children[replacement].add(child)
                    self.parents[child].add(replacement)
            for parent in self.parents.pop(rk):
                self.children[parent].discard(rk)
                if parent not in replaced:
                    self.parents[replacement].add(parent)
                    self.children[parent].add(replacement)

    def edges(self) -> list[str]:
        return sorted(f"{c[0]}.{c[1]} -> {p[0]}.{p[1]}" for c in self.nodes for p in self.parents[c])


class AliasProjection:
    """aliases(app_label) from db_routing.yaml + router behaviour under TEST=1."""

    def __init__(self, routing_yaml: Path) -> None:
        routes = (yaml.safe_load(routing_yaml.read_text()) or {}).get("routes", [])
        self.route = {r["app_label"]: r["database"] for r in routes}

    def aliases(self, app: str) -> frozenset[str]:
        if app in self.route:
            return frozenset({f"{self.route[app]}_db_writer"})
        return frozenset({"default"})


def migration_files(core: Core, package_module: str) -> list[str]:
    """Paths of `<package>/*.py` whose stem does not start with _ or ~ (iterdir of the package)."""
    package_path = core.index.path_of[package_module]
    directory = package_path.rsplit("/", 1)[0] + "/"
    out = []
    for path in core.store.records:
        if not path.startswith(directory) or "/" in path[len(directory) :]:
            continue
        stem = path[len(directory) :]
        if stem.endswith(".py") and stem[0] not in "_~":
            out.append(path)
    return sorted(out)


def run(core: Core, routing_yaml: Path) -> dict:
    records = core.store.records
    settings_paths = sorted(p for p in records if p.startswith("posthog/settings/") and p.count("/") == 2)
    settings = SettingsReader([records[p] for p in [*settings_paths, "ee/settings.py"]])
    resolver = AppResolver(core)
    apps = [resolver.resolve(e) for e in settings.installed_apps()]

    parser = MigrationParser(settings)
    decls: dict[tuple[str, str], MigrationDecl] = {}
    migrated, unmigrated = set(), set()
    for app in apps:
        mod = f"{app.name}.migrations"
        if not (core.index.is_file_module(mod) and core.index.is_package(mod)):
            unmigrated.add(app.label)
            continue
        migrated.add(app.label)
        for path in migration_files(core, mod):
            decls[(app.label, Path(path).stem)] = parser.parse(app.label, path, records[path])

    graph = StaticGraph(decls, migrated, unmigrated)
    graph.build()

    projection = AliasProjection(routing_yaml)
    labels = {a for a, _ in graph.nodes}
    aliases = {app: projection.aliases(app) for app in labels}
    crossings = sorted(
        f"{c[0]}.{c[1]} -> {p[0]}.{p[1]}"
        for c in graph.nodes
        for p in graph.parents[c]
        if p[0] != c[0] and not aliases[c[0]] <= aliases[p[0]]
    )
    return {
        "nodes": sorted(f"{a}.{n}" for a, n in graph.nodes),
        "edges": graph.edges(),
        "dummies": sorted(f"{a}.{n}" for a, n in graph.dummies),
        "aliases": {k: sorted(v) for k, v in aliases.items()},
        "crossings": crossings,
        "app_configs": {a.label: a.name for a in apps},
        "migrated_apps": sorted(migrated),
        "unmigrated_apps": sorted(unmigrated),
        "disk_migrations": len(decls),
        "unresolved": {f"{k[0]}.{k[1]}": d.unresolved for k, d in decls.items() if d.unresolved},
        "unresolved_keys": graph.unresolved_keys,
        "site_migrations": sum(1 for d in decls.values() if SITE_MARKER in d.path),
    }
