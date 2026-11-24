"""The use reading's inventory (`Walker::inventory`) against CPython's own `symtable`: for every
module of the model repository and every module the watched-shape datasets lay, the names the
inventory binds at module level and in each class body (classes nested in classes too) equal what
the symbol table reports as assigned or imported in that scope. A name a class body or a function
at any depth declares `global` and binds is the module's. A mismatch is a binding form the
inventory misses, or one it invents.

Since 3.12 CPython inlines list, set and dict comprehensions, and its table then reports their
iteration variables in the enclosing scope, which binds nothing at run time. So the table is
taken of the module with each such comprehension written as the generator expression it equals
(`list(e for x in it)`), which keeps its own scope: the first iterable and a walrus still bind the
enclosing scope. The names a generic class's statement makes for itself (`__type_params__`) are
the compiler's, not the code's."""

from __future__ import annotations

import ast
import symtable
from collections.abc import Iterator
from typing import Any

import pytest

from native.require import native_module

from model_repo.harness import TREE
from model_repo.shapes_loads import LOADS
from model_repo.shapes_round4 import ROUND4
from model_repo.shapes_round5 import ROUND5
from model_repo.shapes_round6 import ROUND6
from model_repo.shapes_round6_cases import CASES as ROUND6_CASES
from model_repo.shapes_round7 import ROUND7
from model_repo.shapes_round7_cases import CASES as ROUND7_CASES
from model_repo.shapes_round8 import ROUND8
from model_repo.shapes_round9 import ROUND9
from model_repo.shapes_round9b_cases import CASES as ROUND9B
from model_repo.shapes_round10_cases import CASES as ROUND10
from model_repo.shapes_round11_cases import CASES as ROUND11
from model_repo.test_watched_shapes import SHAPES

Binding = tuple[str, str]  # (the class's dotted name, empty for the module; the name)

DATASETS: dict[str, dict[str, dict[str, Any]]] = {
    "shapes": SHAPES,
    "loads": LOADS,
    "round4": ROUND4,
    "round5": ROUND5,
    "round6": {**ROUND6, **ROUND6_CASES},
    "round7": {**ROUND7, **ROUND7_CASES},
    "round8": ROUND8,
    "round9": ROUND9,
    "round9b": ROUND9B,
    "round10": ROUND10,
    "round11": ROUND11,
}


def bound(symbol: symtable.Symbol) -> bool:
    return symbol.is_assigned() or symbol.is_imported()


class Generators(ast.NodeTransformer):
    """Each list, set or dict comprehension as the generator expression it equals."""

    def visit_ListComp(self, node: ast.ListComp) -> ast.AST:
        self.generic_visit(node)
        return wrap("list", ast.GeneratorExp(node.elt, node.generators))

    def visit_SetComp(self, node: ast.SetComp) -> ast.AST:
        self.generic_visit(node)
        return wrap("set", ast.GeneratorExp(node.elt, node.generators))

    def visit_DictComp(self, node: ast.DictComp) -> ast.AST:
        self.generic_visit(node)
        pair = ast.Tuple([node.key, node.value], ast.Load())
        return wrap("dict", ast.GeneratorExp(pair, node.generators))


def wrap(builder: str, generator: ast.GeneratorExp) -> ast.Call:
    return ast.Call(ast.Name(builder, ast.Load()), [generator], [])


def expected(source: str) -> set[Binding]:
    """What the symbol table says each inventory scope binds."""
    tree = ast.fix_missing_locations(Generators().visit(ast.parse(source)))
    top = symtable.symtable(ast.unparse(tree), "<row>", "exec")
    out = {("", symbol.get_name()) for symbol in top.get_symbols() if bound(symbol)}

    def walk(table: symtable.SymbolTable, path: str | None) -> None:
        for child in table.get_children():
            kind = child.get_type()
            if kind == symtable.SymbolTableType.CLASS and path is not None:
                inner = f"{path}.{child.get_name()}" if path else child.get_name()
                generic = kind == symtable.SymbolTableType.CLASS and table.get_type() == (
                    symtable.SymbolTableType.TYPE_PARAMETERS
                )
                for symbol in child.get_symbols():
                    if symbol.is_declared_global() and bound(symbol):
                        out.add(("", symbol.get_name()))
                    elif bound(symbol) and not (generic and symbol.get_name() == "__type_params__"):
                        out.add((inner, symbol.get_name()))
                walk(child, inner)
                continue
            # A generic class's own table sits under its type parameters' scope.
            through = path if kind == symtable.SymbolTableType.TYPE_PARAMETERS else None
            for symbol in child.get_symbols():
                if symbol.is_declared_global() and bound(symbol):
                    out.add(("", symbol.get_name()))
            walk(child, through)

    walk(top, "")
    return out


def modules() -> Iterator[tuple[str, str]]:
    """(an id, the source) of each module of the model repository and of each dataset row."""
    for path in sorted(TREE.rglob("*.py")):
        yield str(path.relative_to(TREE)), path.read_text()
    for dataset, rows in DATASETS.items():
        for p, row in rows.items():
            for path, text in row["files"].items():
                if path.endswith(".py"):
                    yield f"{dataset}:{p}:{path}", text.replace("{p}", p)


def test_the_inventory_binds_what_the_symbol_table_assigns() -> None:
    native = native_module()
    mismatches = []
    compared = 0
    for name, source in modules():
        try:
            want = expected(source)
        except SyntaxError:
            continue
        got = native.inventory(source.encode())
        assert got is not None, f"{name}: CPython parses it, the extractor does not"
        compared += 1
        got = set(map(tuple, got))
        if got != want:
            mismatches.append((name, sorted(want - got), sorted(got - want)))
    assert compared > 1000
    assert mismatches == [], mismatches[:20]


@pytest.mark.parametrize(
    "source",
    [
        "_Y = []\ndef _d(a=(_Y := 1)): pass\n",
        "_Y = []\n_l = lambda a=(_Y := 1): a\n",
        "class _K(metaclass=(_Y := type)): pass\n",
        "@(_Y := (lambda c: c))\nclass _K: pass\n",
        "@(_Y := (lambda f: f))\ndef g(x=(_Z := 1)): pass\n",
        "match 1:\n    case print: pass\n",
        "match {}:\n    case {**rest}: pass\n",
        "match []:\n    case [*rest] as whole: pass\n",
        "try:\n    pass\nexcept Exception as len:\n    pass\n",
        "[_Y := x for x in range(3)]\n",
        "[x for x in range(3) if (_Y := x)]\n{k: (_Z := v) for k, v in {}.items()}\n",
        "def f():\n    global _G\n    _G = 1\n",
        "def f():\n    class C:\n        global _G\n        _G = 1\n",
        "class C:\n    global _G\n    _G = 1\n    def m(self): pass\n",
        "class C:\n    x: int\n    class D:\n        y = 1\n",
        "import a.b as c, d.e\nfrom f import g as h, i\n",
        "type T = int\ndel T\n",
        "with open('f') as (a, b):\n    pass\nfor [c, *d] in []:\n    pass\n",
    ],
)
def test_each_binding_form(source: str) -> None:
    got = set(map(tuple, native_module().inventory(source.encode())))
    assert got == expected(source)
