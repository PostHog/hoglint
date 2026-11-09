"""Migration graph as MigrationLoader(connection=None).build_graph() builds it, from facts.

Settings, AppConfig classes and Migration bodies come from `assigns`, `defs` and `calls` facts.
The routing file is the only non-fact input (database aliases per app label).

Each migration file becomes a summary first: what the file says on its own, with no setting
read. Summaries are cached by the digest of the file's facts, so a warm run decodes no
migration body; the graph is then built from the summaries and the settings.
"""

from __future__ import annotations

import os
import sys
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache, cached_property
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml
import orjson

from hoglint.core import records as records_module
from hoglint.core.harness import PRODUCER_ERROR, failure_text
from hoglint.core.records import NO_CONST, RecordBodies, const_of, is_const, str_literal, value_text
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Resolution, Site
from hoglint.packs.django.apps import AppRegistry
from hoglint.packs.django.config import DatabaseRouting, DjangoPackConfig
from hoglint.plugins import PackReader

Key = tuple[str, str]
FIRST = "__first__"
LATEST = "__latest__"


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


Summary = dict[str, Any]
SUMMARY_KINDS = ("assigns", "defs")
KEY_LISTS = ("dependencies", "run_before", "replaces")


def referenced(node: object, constants: dict, out: dict) -> dict:
    """The module constants a value names, and the ones those name in turn."""
    if isinstance(node, str):
        if "." not in node and node in constants and node not in out:
            out[node] = constants[node]
            referenced(constants[node], constants, out)
    elif isinstance(node, dict):
        for value in node.values():
            referenced(value, constants, out)
    elif isinstance(node, list):
        for value in node:
            referenced(value, constants, out)
    return out


class MigrationSummary:
    """One migration file as JSON, read from its facts alone: the line of its Migration class
    and each key list it assigns, in order. A key is ["key", app, name], or ["unresolved", text],
    or ["setting", node, constants, text] for a swappable dependency: its setting is read later,
    with the module constants the node names."""

    def __init__(self, bodies: RecordBodies) -> None:
        self.bodies = bodies
        self.constants: dict[str, object] = {}

    def read(self) -> Summary:
        bodies = self.bodies
        for row in bodies.assigns(-1):
            if row["op"] == "=" and len(row["t"]) == 1:
                target = row["t"][0]
                if isinstance(target, str) and "." not in target:
                    self.constants[target] = row.get("v")
        defs = bodies.defs
        cls = next((i for i in bodies.children(-1) if defs[i]["k"] == "class" and defs[i]["n"] == "Migration"), None)
        if cls is None:
            return {"class": False}
        summary: Summary = {"class": True, "line": defs[cls]["l"], "lists": []}
        for row in bodies.assigns(cls):
            if row["op"] not in ("=", ":"):
                continue
            target = row["t"][0]
            if not isinstance(target, str) or "." in target:
                continue
            value = row.get("v")
            if target in KEY_LISTS:
                summary["lists"].append([target, self.keys(value)])
            elif target == "initial" and is_const(value):
                found = const_of(value)
                summary["initial"] = ["opaque"] if found is NO_CONST else ["value", found]
        return summary

    def keys(self, node: object) -> list[list]:
        if isinstance(node, str) and "." not in node and node in self.constants:
            node = self.constants[node]
        if isinstance(node, dict) and node.get("bin") == "+":
            return self.keys(node["l"]) + self.keys(node["r"])
        elts = node.get("list", node.get("tuple")) if isinstance(node, dict) else None
        if elts is None:
            return [["unresolved", value_text(node)]]
        return [self.key(elt) for elt in elts]

    def key(self, elt: object) -> list:
        if not isinstance(elt, dict):
            return ["unresolved", value_text(elt)]
        items = elt.get("tuple", elt.get("list"))
        texts = [str_literal(e) for e in items] if items is not None else []
        if len(texts) == 2 and texts[0] is not None and texts[1] is not None:
            return ["key", texts[0], texts[1]]
        callee = elt.get("call")
        if isinstance(callee, str) and callee.endswith("swappable_dependency") and elt.get("args"):
            node = elt["args"][0]
            return ["setting", node, referenced(node, self.constants, {}), value_text(elt)]
        return ["unresolved", value_text(elt)]


@cache
def summary_code() -> str:
    """The id of the code that writes summaries: a summary from other code is never read."""
    digest = hashlib.blake2b(digest_size=8)
    for module in (sys.modules[__name__], records_module):
        digest.update(Path(cast(str, module.__file__)).read_bytes())
    digest.update(sys.version.encode())
    return digest.hexdigest()


class SummaryCache:
    """Migration summaries by the digest of their file's facts, in one file under the cache root
    per summary code: the source of this module and of the record readers, and the interpreter.
    The file loads on the first lookup; a missing or broken one is an empty cache, and one that
    cannot be written is skipped."""

    MAX_ENTRIES = 50_000

    def __init__(self, root: Path) -> None:
        self.root = root
        self.used: dict[str, Summary] = {}
        self.added = False

    @cached_property
    def path(self) -> Path:
        return self.root / f"migrations-{summary_code()}.json"

    @cached_property
    def stored(self) -> dict[str, Summary]:
        try:
            return orjson.loads(self.path.read_bytes())
        except (OSError, orjson.JSONDecodeError):
            return {}

    def get(self, digest: str) -> Summary | None:
        found = self.used.get(digest) or self.stored.get(digest)
        if found is not None:
            self.used[digest] = found
        return found

    def put(self, digest: str, summary: Summary) -> None:
        self.used[digest] = summary
        self.added = True

    def save(self) -> None:
        if not self.added:
            return
        entries = dict(self.used)
        for digest, summary in self.stored.items():
            if len(entries) >= self.MAX_ENTRIES:
                break
            entries.setdefault(digest, summary)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(orjson.dumps(entries))
            os.replace(temporary, self.path)
        except OSError:
            return


class MigrationParser:
    """One migration summary into a MigrationDecl, with the settings it names."""

    def __init__(self, apps: AppRegistry, config: DjangoPackConfig) -> None:
        self.apps = apps
        self.proxies = {p.name: p.prefix for p in config.settings_proxies}
        self.helpers = {p.name: p.prefix for p in config.setting_name_helpers}

    def decl(self, app_label: str, path: str, module: str, summary: Summary) -> MigrationDecl:
        decl = MigrationDecl(app_label, PurePosixPath(path).stem, path, module)
        if not summary["class"]:
            decl.unresolved.append("no Migration class")
            return decl
        decl.line = summary["line"]
        for target, items in summary["lists"]:
            setattr(decl, target, self.keys(items, decl))
        initial = summary.get("initial")
        if initial is not None:
            decl.initial = initial[1] if initial[0] == "value" else NO_CONST
        return decl

    def keys(self, items: list[list], decl: MigrationDecl) -> list[Key]:
        out: list[Key] = []
        for item in items:
            if item[0] == "key":
                out.append((item[1], item[2]))
                continue
            if item[0] == "setting":
                value = self.setting(item[1], item[2])
                if value is not None:
                    out.append((value.split(".", 1)[0], FIRST))
                    continue
            decl.unresolved.append(item[-1])
        return out

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
        self.app_nodes: dict[str, set[Key]] = defaultdict(set)
        for key in decls:
            self.app_nodes[key[0]].add(key)
        self.parents: dict[Key, set[Key]] = {k: set() for k in decls}
        self.children: dict[Key, set[Key]] = {k: set() for k in decls}
        self.how: dict[tuple[Key, Key], str] = {}
        self.dummies: set[Key] = set()
        self.unresolved_keys: list[tuple[Key, Key]] = []
        self.ends: dict[tuple[str, str], list[Key]] = {}

    def forget(self, app: str) -> None:
        """An app's roots and leaves change only when it gains a node or an edge inside it."""
        self.ends.pop((app, FIRST), None)
        self.ends.pop((app, LATEST), None)

    def add(self, child: Key, parent: Key, how: str) -> None:
        for key in (child, parent):
            if key not in self.nodes:
                self.nodes.add(key)
                self.app_nodes[key[0]].add(key)
                self.forget(key[0])
                self.dummies.add(key)
                self.parents[key] = set()
                self.children[key] = set()
        if child[0] == parent[0]:
            self.forget(child[0])
        self.parents[child].add(parent)
        self.children[parent].add(child)
        self.how.setdefault((child, parent), how)

    def roots(self, app: str) -> list[Key]:
        found = self.ends.get((app, FIRST))
        if found is None:
            nodes = self.app_nodes[app]
            found = self.ends[(app, FIRST)] = sorted(n for n in nodes if all(p[0] != app for p in self.parents[n]))
        return found

    def leaves(self, app: str) -> list[Key]:
        found = self.ends.get((app, LATEST))
        if found is None:
            nodes = self.app_nodes[app]
            found = self.ends[(app, LATEST)] = sorted(n for n in nodes if all(c[0] != app for c in self.children[n]))
        return found

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
            self.app_nodes[rk[0]].discard(rk)
            self.forget(rk[0])
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
    def __init__(self, reader: PackReader, apps: AppRegistry, config: DjangoPackConfig, cache: SummaryCache) -> None:
        self.reader = reader
        self.apps = apps
        self.cache = cache
        self.parser = MigrationParser(apps, config)
        self.by_directory: dict[str, list[str]] = {}
        for path in sorted(reader.paths):
            directory, _, name = path.rpartition("/")
            if name.endswith(".py") and name[0] not in "_~":
                self.by_directory.setdefault(directory, []).append(path)

    def files(self, package_module: str) -> list[str]:
        """`<package>/*.py` whose stem does not start with _ or ~ (iterdir of the package)."""
        return self.by_directory.get(self.reader.index.path_of[package_module].rpartition("/")[0], [])

    def summary(self, path: str) -> Summary:
        record = self.reader.record(path)
        digest = self.reader.fact_digest(record, SUMMARY_KINDS)
        found = self.cache.get(digest) if digest is not None else None
        if found is None:
            found = MigrationSummary(self.reader.bodies(record)).read()
            if digest is not None:
                self.cache.put(digest, found)
        return found

    def parse(self, app_label: str, path: str) -> MigrationDecl:
        """A migration that cannot be read keeps its node, with its parents unknown."""
        module = self.reader.index.module_of[path]
        try:
            return self.parser.decl(app_label, path, module, self.summary(path))
        except Exception as error:  # noqa: BLE001 - one unreadable migration is unknown, the others still count
            reason = f"{PRODUCER_ERROR}: {failure_text(error)}"
            return MigrationDecl(app_label, PurePosixPath(path).stem, path, module, unresolved=[reason])

    def read(self, projection: AliasProjection) -> MigrationGraph:
        decls: dict[Key, MigrationDecl] = {}
        migrated: set[str] = set()
        unmigrated: set[str] = set()
        for app in self.apps.apps:
            module = f"{app.name}.migrations"
            if not (self.reader.index.is_file_module(module) and self.reader.index.is_package(module)):
                unmigrated.add(app.label)
                continue
            migrated.add(app.label)
            for path in self.files(module):
                decls[(app.label, PurePosixPath(path).stem)] = self.parse(app.label, path)
        self.cache.save()
        graph = StaticGraph(decls, migrated, unmigrated)
        graph.build()
        aliases = {app: projection.aliases(app) for app in {a for a, _ in graph.nodes} | migrated | unmigrated}
        return MigrationGraph(decls, graph, aliases)


MIGRATION_PRODUCER = "django.migrations@1"
MIGRATION_CONTEXT = frozenset({Context.MIGRATION})


class MigrationEdges:
    """One `migration-parent` edge per resolved parent, and one unresolved edge per unread key."""

    def __init__(self, migrations: MigrationGraph, external: dict[str, str]) -> None:
        self.migrations = migrations
        self.external = external

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
            resolution = Resolution.EXTERNAL if found.path in self.external else Resolution.RESOLVED
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
            if decl.path in self.external:
                continue
            out.extend(self.unresolved_edge(decl, text) for text in decl.unresolved)
        for origin, key in graph.unresolved_keys:
            decl = self.migrations.decls.get(origin)
            if decl is not None:
                out.append(self.unresolved_edge(decl, f"{key[0]}.{key[1]}"))
        return out
