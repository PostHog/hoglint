"""Migration graph as MigrationLoader(connection=None).build_graph() builds it, from facts.

Settings, AppConfig classes and Migration bodies come from `assigns`, `defs` and `calls` facts.
The routing file is the only non-fact input (database aliases per app label).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import cast

import yaml

from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.packs.django.apps import AppRegistry
from hoglint.packs.django.config import DatabaseRouting, DjangoPackConfig
from hoglint.packs.django.sites import PRODUCER_ERROR
from hoglint.spike.core import CLASS, NO_CONST, SITE_MARKER, Core, const_of, is_const

Key = tuple[str, str]
FIRST = "__first__"
LATEST = "__latest__"


def value_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=False)
class MigrationDecl:
    app_label: str
    name: str
    path: str
    module: str
    line: int = 0
    dependencies: list[Key] = field(default_factory=list)
    run_before: list[Key] = field(default_factory=list)
    replaces: list[Key] = field(default_factory=list)
    initial: object = None
    unresolved: list[str] = field(default_factory=list)


def top_classes(record: dict) -> list[tuple[int, dict]]:
    return [
        (i, d) for i, d in enumerate(record.get("defs", ())) if d["s"] == -1 and d["k"] == "class" and not d.get("f")
    ]


def class_body_assigns(record: dict, idx: int) -> list[dict]:
    flags = record["defs"][idx].get("f", 0) | CLASS
    return [row for row in record.get("assigns", ()) if row["s"] == idx and row.get("f", 0) == flags]


class MigrationParser:
    """One migration record into a MigrationDecl."""

    def __init__(self, apps: AppRegistry, config: DjangoPackConfig) -> None:
        self.apps = apps
        self.proxies = {p.name: p.prefix for p in config.settings_proxies}
        self.helpers = {p.name: p.prefix for p in config.setting_name_helpers}

    def parse(self, app_label: str, path: str, module: str, record: dict) -> MigrationDecl:
        decl = MigrationDecl(app_label, PurePosixPath(path).stem, path, module)
        constants = {}
        for row in record.get("assigns", ()):
            if row["s"] == -1 and not row.get("f") and row["op"] == "=" and len(row["t"]) == 1:
                target = row["t"][0]
                if isinstance(target, str) and "." not in target:
                    constants[target] = row.get("v")
        cls = next(((i, d) for i, d in top_classes(record) if d["n"] == "Migration"), None)
        if cls is None:
            decl.unresolved.append("no Migration class")
            return decl
        decl.line = cls[1]["l"]
        for row in class_body_assigns(record, cls[0]):
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

    def keys(self, node: object, constants: dict, decl: MigrationDecl) -> list[Key]:
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

    def key(self, elt: object, constants: dict) -> Key | None:
        if not isinstance(elt, dict):
            return None
        items = elt.get("tuple", elt.get("list"))
        if (
            items is not None
            and len(items) == 2
            and all(isinstance(e, dict) and "s" in e and len(e) == 1 for e in items)
        ):
            return (items[0]["s"], items[1]["s"])
        callee = elt.get("call")
        if isinstance(callee, str) and callee.endswith("swappable_dependency") and elt.get("args"):
            value = self.setting(elt["args"][0], constants)
            if value is None:
                return None
            return (value.split(".", 1)[0], FIRST)
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
            return self.getattr_setting(node["args"][1])
        if isinstance(node, str) and node.count(".") == 1:
            base, attr = node.split(".")
            if base == "settings":
                return self.apps.setting(attr)
            prefix = self.proxies.get(base)
            if prefix is not None:
                return self.apps.setting(prefix + attr)
        return None

    def getattr_setting(self, name_node: object) -> str | None:
        if isinstance(name_node, dict) and name_node.get("call") in self.helpers:
            parts = [const_of(a) for a in name_node.get("args", ())]
            if not all(isinstance(p, str) for p in parts):
                return None
            return self.apps.setting(self.helpers[name_node["call"]] + "_".join(cast(list[str], parts)))
        if is_const(name_node):
            name = const_of(name_node)
            return self.apps.setting(name) if isinstance(name, str) else None
        return None


class StaticGraph:
    """Replay MigrationLoader.build_graph() with nothing applied."""

    def __init__(self, decls: dict[Key, MigrationDecl], migrated: set[str], unmigrated: set[str]) -> None:
        self.decls = decls
        self.migrated = migrated
        self.unmigrated = unmigrated
        self.nodes: set[Key] = set(decls)
        self.parents: dict[Key, set[Key]] = {k: set() for k in decls}
        self.children: dict[Key, set[Key]] = {k: set() for k in decls}
        self.how: dict[tuple[Key, Key], str] = {}
        self.dummies: set[Key] = set()
        self.unresolved_keys: list[tuple[Key, Key]] = []

    def add(self, child: Key, parent: Key, how: str) -> None:
        for key in (child, parent):
            if key not in self.nodes:
                self.nodes.add(key)
                self.dummies.add(key)
                self.parents[key] = set()
                self.children[key] = set()
        self.parents[child].add(parent)
        self.children[parent].add(child)
        self.how.setdefault((child, parent), how)

    def roots(self, app: str) -> list[Key]:
        return sorted(n for n in self.nodes if n[0] == app and all(p[0] != app for p in self.parents[n]))

    def leaves(self, app: str) -> list[Key]:
        return sorted(n for n in self.nodes if n[0] == app and all(c[0] != app for c in self.children[n]))

    def check_key(self, key: Key, current_app: str, origin: Key) -> Key | None:
        if key[1] not in (FIRST, LATEST) or key in self.nodes:
            return key
        if key[0] == current_app or key[0] in self.unmigrated:
            return None
        if key[0] in self.migrated:
            found = self.roots(key[0]) if key[1] == FIRST else self.leaves(key[0])
            return found[0] if found else None
        self.unresolved_keys.append((origin, key))
        return None

    def build(self) -> None:
        for key, m in self.decls.items():
            for parent in m.dependencies:
                if parent[0] == key[0] and parent[1] != FIRST:
                    self.add(key, parent, "dependency")
        for key, m in self.decls.items():
            for parent in m.dependencies:
                if key[0] == parent[0]:
                    continue
                checked = self.check_key(parent, key[0], key)
                if checked is not None:
                    self.add(key, checked, "dependency")
            for child in m.run_before:
                checked = self.check_key(child, key[0], key)
                if checked is not None:
                    self.add(checked, key, "run_before")
        for key, m in self.decls.items():
            if m.replaces:
                self.remove_replaced(key, m.replaces)

    def remove_replaced(self, replacement: Key, replaced_list: list[Key]) -> None:
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
                    self.how.setdefault((child, replacement), "replaces")
            for parent in self.parents.pop(rk):
                self.children[parent].discard(rk)
                if parent not in replaced:
                    self.parents[replacement].add(parent)
                    self.children[parent].add(replacement)
                    self.how.setdefault((replacement, parent), "replaces")

    def edges(self) -> list[tuple[Key, Key]]:
        return sorted((c, p) for c in self.nodes for p in self.parents[c])


class AliasProjection:
    """Database aliases per app label from the routing file; unrouted apps use the default alias."""

    def __init__(self, repo_root: Path, routing: DatabaseRouting | None) -> None:
        self.routing = routing
        self.route: dict[str, str] = {}
        if routing is None:
            return
        path = routing.path if routing.path.is_absolute() else repo_root / routing.path
        routes = (yaml.safe_load(path.read_text()) or {}).get("routes", [])
        self.route = {r["app_label"]: r["database"] for r in routes}

    def aliases(self, app: str) -> frozenset[str]:
        if self.routing is None:
            return frozenset({"default"})
        if app in self.route:
            return frozenset({self.routing.alias_template.format(database=self.route[app])})
        return frozenset({self.routing.default_alias})


@dataclass(frozen=False)
class MigrationGraph:
    decls: dict[Key, MigrationDecl]
    graph: StaticGraph
    aliases: dict[str, frozenset[str]]


class MigrationReader:
    def __init__(self, core: Core, apps: AppRegistry, config: DjangoPackConfig) -> None:
        self.core = core
        self.apps = apps
        self.parser = MigrationParser(apps, config)

    def files(self, package_module: str) -> list[str]:
        """`<package>/*.py` whose stem does not start with _ or ~ (iterdir of the package)."""
        directory = self.core.index.path_of[package_module].rsplit("/", 1)[0] + "/"
        out = []
        for path in self.core.store.records:
            if not path.startswith(directory) or "/" in path[len(directory) :]:
                continue
            stem = path[len(directory) :]
            if stem.endswith(".py") and stem[0] not in "_~":
                out.append(path)
        return sorted(out)

    def parse(self, app_label: str, path: str) -> MigrationDecl:
        """A migration that cannot be read keeps its node, with its parents unknown."""
        module = self.core.index.module_of[path]
        try:
            return self.parser.parse(app_label, path, module, self.core.store.records[path])
        except Exception as error:  # one unreadable migration is unknown, the others still count
            reason = f"{PRODUCER_ERROR}: {type(error).__name__}: {error}"
            return MigrationDecl(app_label, PurePosixPath(path).stem, path, module, unresolved=[reason])

    def read(self, projection: AliasProjection) -> MigrationGraph:
        decls: dict[Key, MigrationDecl] = {}
        migrated: set[str] = set()
        unmigrated: set[str] = set()
        for app in self.apps.apps:
            module = f"{app.name}.migrations"
            if not (self.core.index.is_file_module(module) and self.core.index.is_package(module)):
                unmigrated.add(app.label)
                continue
            migrated.add(app.label)
            for path in self.files(module):
                decls[(app.label, PurePosixPath(path).stem)] = self.parse(app.label, path)
        graph = StaticGraph(decls, migrated, unmigrated)
        graph.build()
        aliases = {app: projection.aliases(app) for app in {a for a, _ in graph.nodes} | migrated | unmigrated}
        return MigrationGraph(decls, graph, aliases)


MIGRATION_PRODUCER = "django.migrations@1"
MIGRATION_CONTEXT = frozenset({Context.MIGRATION})


class MigrationEdges:
    """One `migration-parent` edge per resolved parent, and one unresolved edge per unread key."""

    def __init__(self, migrations: MigrationGraph) -> None:
        self.migrations = migrations

    def aliases_text(self, app: str) -> str:
        return ",".join(sorted(self.migrations.aliases.get(app, frozenset())))

    def site(self, decl: MigrationDecl) -> Site:
        return Site(decl.path, decl.module, "Migration", decl.line)

    def parent_edge(self, child: MigrationDecl, parent: Key) -> Edge:
        found = self.migrations.decls.get(parent)
        if found is None:
            targets: tuple[EntityId, ...] = ()
            resolution = Resolution.UNRESOLVED
        else:
            targets = (EntityId(LANGUAGE_PYTHON, found.module),)
            resolution = Resolution.EXTERNAL if SITE_MARKER in found.path else Resolution.RESOLVED
        how = self.migrations.graph.how.get(((child.app_label, child.name), parent), "dependency")
        return Edge(
            kind="migration-parent",
            site=self.site(child),
            surface=f"{parent[0]}.{parent[1]}",
            targets=targets,
            resolution=resolution,
            contexts=MIGRATION_CONTEXT,
            producer=MIGRATION_PRODUCER,
            shape=how,
            attrs=(
                ("child", f"{child.app_label}.{child.name}"),
                ("parent", f"{parent[0]}.{parent[1]}"),
                ("child_databases", self.aliases_text(child.app_label)),
                ("parent_databases", self.aliases_text(parent[0])),
            ),
        )

    def unresolved_edge(self, decl: MigrationDecl, text: str) -> Edge:
        return Edge(
            kind="migration-parent",
            site=self.site(decl),
            surface=text,
            targets=(),
            resolution=Resolution.UNRESOLVED,
            contexts=MIGRATION_CONTEXT,
            producer=MIGRATION_PRODUCER,
            attrs=(("child", f"{decl.app_label}.{decl.name}"),),
        )

    def produce(self) -> list[Edge]:
        out: list[Edge] = []
        graph = self.migrations.graph
        for child, parent in graph.edges():
            decl = self.migrations.decls.get(child)
            if decl is not None:
                out.append(self.parent_edge(decl, parent))
        for decl in self.migrations.decls.values():
            if SITE_MARKER in decl.path:
                continue
            out.extend(self.unresolved_edge(decl, text) for text in decl.unresolved)
        for origin, key in graph.unresolved_keys:
            decl = self.migrations.decls.get(origin)
            if decl is not None:
                out.append(self.unresolved_edge(decl, f"{key[0]}.{key[1]}"))
        return out
