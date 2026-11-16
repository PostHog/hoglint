"""Small synthetic graphs and configs for rule tests."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

from core.support import FileFacts, Linked
from hoglint.config import Config, load_config
from hoglint.config.loader import PACKS_DIR
from hoglint.core.config import CoreConfig
from hoglint.labels import NamespaceDirectories
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Graph, Resolution, Site
from hoglint.native.graph import NativeGraph

DJANGO_PACK_STUB = """
schema = 1

[vocabulary]
edge_kinds = ["model-label", "relation", "reverse-accessor", "migration-parent", "drives", "signal", "installs", "registry"]
use_shapes = ["instance-single", "instance-many", "write", "lock", "scalar-chain", "subquery", "construct", "meta",
              "nested-class-attr", "exception", "type-use", "in-collection", "passed-as-class"]
sections = ["django"]
"""


def module_path(module: str, package: bool = False) -> str:
    return module.replace(".", "/") + ("/__init__.py" if package else ".py")


class GraphBuilder:
    """Builds a Graph from short calls: modules first, then edges between them."""

    def __init__(self) -> None:
        self.graph = Graph()

    def module(self, *modules: str) -> GraphBuilder:
        for module in modules:
            self.graph.modules[module] = module_path(module)
        return self

    def package(self, *modules: str) -> GraphBuilder:
        for module in modules:
            self.graph.modules[module] = module_path(module, package=True)
        return self

    def symbol(self, symbol: str, decl: str = "class", **facts: str) -> GraphBuilder:
        self.graph.symbols[symbol] = {"kind": decl, **facts}
        return self

    def edge(
        self,
        kind: str,
        src: str,
        target: str | None,
        surface: str | None = None,
        *,
        resolution: Resolution = Resolution.RESOLVED,
        contexts: tuple[Context, ...] = (Context.PROD,),
        scope: str = "<module>",
        shape: str = "",
        detail: str = "",
        use: str = "",
        attrs: tuple[tuple[str, str], ...] = (),
        via: tuple[str, ...] = (),
        line: int = 1,
        producer: str = "test@1",
    ) -> GraphBuilder:
        targets: tuple[EntityId, ...] = ()
        if target is not None and resolution is Resolution.EXTERNAL:
            targets = (EntityId(LANGUAGE_PYTHON, target),)
        elif target is not None and resolution is not Resolution.UNRESOLVED:
            targets = (self.entity(target),)
        self.graph.edges.append(
            Edge(
                kind=kind,
                site=Site(self.graph.modules.get(src, module_path(src)), src, scope, line),
                surface=surface or target or "",
                targets=targets,
                resolution=resolution,
                contexts=frozenset(contexts),
                producer=producer,
                via=tuple(self.entity(hop) for hop in via),
                use=use,
                shape=shape,
                shape_detail=detail,
                attrs=attrs,
            )
        )
        return self

    def entity(self, dotted: str) -> EntityId:
        if dotted in self.graph.modules:
            return EntityId(LANGUAGE_PYTHON, dotted)
        # A directory without `__init__.py` that holds modules: the module is the directory.
        if dotted in NamespaceDirectories(self.graph.modules).paths:
            return EntityId(LANGUAGE_PYTHON, dotted)
        head = dotted
        while "." in head:
            head = head.rpartition(".")[0]
            if head in self.graph.modules:
                return EntityId(LANGUAGE_PYTHON, head, dotted[len(head) + 1 :])
        module, _, name = dotted.rpartition(".")
        return EntityId(LANGUAGE_PYTHON, module, name)


def native_graph(graph: Graph) -> NativeGraph:
    """A hand-built graph loaded into a native link, which the native evaluator judges: an empty
    file per module, the graph's symbols and edges as a pack writes them, its hand-outs and failed
    producers. A module whose path sits under a directory its name does not hold (`src/`) makes
    that directory a source root."""
    if isinstance(graph, NativeGraph):
        return graph
    records = {path: FileFacts(path).record for path in graph.modules.values()}
    roots = {"."}
    for module, path in graph.modules.items():
        for named in (module_path(module), module_path(module, package=True)):
            if path.endswith("/" + named):
                roots.add(path.removesuffix("/" + named))
    first_party = tuple(sorted({module.split(".")[0] for module in graph.modules}))
    linked = Linked(records, CoreConfig(source_roots=tuple(sorted(roots)), first_party=first_party))
    sink = linked.sink()
    if graph.symbols:
        sink.add_symbols(graph.symbols)
    sink.add_edges("test@1", graph.edges)
    native = linked.graph
    native.hand_outs.update(graph.hand_outs)
    native.failed_producers.update(graph.failed_producers)
    return native


def write_config(root: Path, text: str, extra: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "hoglint.toml"
    path.write_text(textwrap.dedent(text))
    for name, content in (extra or {}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(content))
    return path


def packs_dir(root: Path) -> Path:
    pack = root / "_packs" / "django" / "pack.toml"
    pack.parent.mkdir(parents=True, exist_ok=True)
    pack.write_text(DJANGO_PACK_STUB)
    return root / "_packs"


def config_from(root: Path, text: str, extra: dict[str, str] | None = None) -> Config:
    path = write_config(root, text, extra)
    return load_config(root, path, packs_dir(root))


SHAPES = Path(__file__).parent / "shapes"
PRESET = Path(__file__).resolve().parents[2] / "presets" / "posthog.toml"


def without_tree_rules(preset: str) -> str:
    """The preset without the rules these tests cannot run: they build their graphs by hand, so
    the export rule goes (its tests are under tests/native), and their trees are no git tree, so
    the layout rule that asks which files exist goes, with the level's requirement of it."""
    for name in ("export-shape", "has-facade"):
        start = preset.index(f"[rules.{name}]\n")
        end = preset.index("\n[", start + 1) + 1
        preset = preset[:start] + preset[end:]
        assert preset.count(f', "{name}"') in (0, 2)
        preset = preset.replace(f', "{name}"', "")
    return preset


def shape_config(root: Path, name: str, files: dict[str, str] | None = None) -> Config:
    """The config of repo shape `name` (`shapes/<name>.toml`) in a tree that holds the PostHog
    preset where a PostHog checkout keeps it, and `files`."""
    shutil.copy(SHAPES / f"{name}.toml", root / "hoglint.toml")
    preset = root / "tools" / "hoglint" / "presets" / "posthog.toml"
    preset.parent.mkdir(parents=True)
    preset.write_text(without_tree_rules(PRESET.read_text()))
    for path, content in (files or {}).items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(content)
    return load_config(root, root / "hoglint.toml", PACKS_DIR)
