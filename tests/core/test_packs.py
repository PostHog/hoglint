"""Core (`hoglint/` outside `packs/`) imports no pack, so a pack loads only when a config extends
it (`extends = ["hoglint:<name>"]`)."""

from __future__ import annotations

import ast
from pathlib import Path

HOGLINT = Path(__file__).resolve().parents[2] / "hoglint"
PACKS = HOGLINT / "packs"
PACKS_MODULE = "hoglint.packs"


class ImportedNames:
    """What the import statements of one source file name: each imported module, and each
    `module.name` of a from-import, with a relative import made absolute."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # The package a relative import starts from: the directory, for a module and an `__init__.py` alike.
        self.package = path.relative_to(HOGLINT.parent).parent.parts

    def base(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        anchor = self.package[: len(self.package) - node.level + 1]
        return ".".join([*anchor, node.module] if node.module else anchor)

    def names(self) -> list[str]:
        out: list[str] = []
        for node in ast.walk(ast.parse(self.path.read_bytes())):
            if isinstance(node, ast.Import):
                out.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = self.base(node)
                out.extend(f"{base}.{alias.name}" for alias in node.names)
        return out


def test_core_imports_no_pack() -> None:
    core = [path for path in sorted(HOGLINT.rglob("*.py")) if not path.is_relative_to(PACKS)]
    reaching = [
        f"{path.relative_to(HOGLINT.parent)}: {name}"
        for path in core
        for name in ImportedNames(path).names()
        if name == PACKS_MODULE or name.startswith(f"{PACKS_MODULE}.")
    ]
    assert core
    assert reaching == []
