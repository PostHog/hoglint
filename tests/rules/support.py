"""Small synthetic graphs and configs for rule tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

from hoglint.config import Config, load_config
from hoglint.model import LANGUAGE_PYTHON, Context, Edge, EntityId, Graph, Resolution, Site

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
                producer="test@1",
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
        head = dotted
        while "." in head:
            head = head.rpartition(".")[0]
            if head in self.graph.modules:
                return EntityId(LANGUAGE_PYTHON, head, dotted[len(head) + 1 :])
        module, _, name = dotted.rpartition(".")
        return EntityId(LANGUAGE_PYTHON, module, name)


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
