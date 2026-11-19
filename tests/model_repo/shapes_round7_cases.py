"""Round 7's table cases (CASES.md `WA18`), as data for `test_watched_shapes.py`: for each entry of
the use table (T1 to T13 of `engine/round7-table.md`), the entry on the registered list itself, on a
holder of it and on a holder of a holder, and one case per slot the entry does not name, which must
escape. Each case is a product facade of its own that binds `WORKFLOWS = [AFlow]` and the shape; the
worker registers `WORKFLOWS`. `watched`: each probe file is watched and the product passes;
`unread`: the product cannot pass, and its reason names `WORKFLOWS`."""

from __future__ import annotations

from typing import Any

FLOWS = "class {name}:\n    def run(self, x):\n        return x\n"

# Holders, as a use of the entry sees them (`X` in a shape): a display holds the list, a display
# holds that, a class body binds it. None of them is a form the table reads, so each fails closed
# at its own site, whatever the entry does with it.
HOLDERS: dict[str, str] = {
    "h": "_H = [WORKFLOWS]\n",
    "hh": "_H = [WORKFLOWS]\n_HH = [_H]\n",
    "k": "\n\nclass _K:\n    L = WORKFLOWS\n\n\n",
}
HOLDER_NAMES = {"h": "_H", "hh": "_HH", "k": "_K"}


def case(
    p: str,
    body: str,
    outcome: str,
    note: str,
    head: str = "",
    files: dict[str, str] | None = None,
    probes: tuple[str, ...] = ("a",),
) -> dict[str, Any]:
    """A product facade that binds WORKFLOWS and `body` (`{p}` is the product); the worker
    registers WORKFLOWS. AFlow is registered; BFlow only by a shape that adds it."""
    facade = (
        head
        + f"from products.{p}.backend.temporal.a import AFlow\n"
        + f"from products.{p}.backend.temporal.b import BFlow\n\n"
        + "WORKFLOWS = [AFlow]\n"
        + body.replace("{p}", p)
    )
    laid = {
        "backend/temporal/__init__.py": "",
        "backend/temporal/a.py": FLOWS.format(name="AFlow"),
        "backend/temporal/b.py": FLOWS.format(name="BFlow"),
        "backend/facade/temporal.py": facade,
    }
    for path, text in (files or {}).items():
        laid[path] = text.replace("{p}", p)
    return {
        "files": laid,
        "probes": [f"backend/temporal/{probe}.py" for probe in probes],
        "outcome": outcome,
        "reason": f"products.{p}.backend.facade.temporal.WORKFLOWS",
        "note": note,
    }


# Each entry's shapes: (name, the shape with `X` for the value, imports, outcome on the list
# itself, probes). The holders of `HOLDERS` replace `X` in the same shape and fail closed.
ENTRIES: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    # T1
    ("t1_list_star", "_X = [*X]\n", "", "watched", ("a",)),
    ("t1_tuple_star", "_X = (1, *X)\n", "", "watched", ("a",)),
    # T2
    ("t2_alias", "_X = X\n", "", "watched", ("a",)),
    ("t2_annotated", "_X: list = X\n", "", "watched", ("a",)),
    ("t2_chained", "_X = _Y = X\n", "", "watched", ("a",)),
    ("t2_class_body", "\n\nclass _C:\n    L = X\n", "", "watched", ("a",)),
    ("t2_branch", "_X = X if True else ()\n", "", "watched", ("a",)),
    # T5
    ("t5_list", "_X = list(X)\n", "", "watched", ("a",)),
    ("t5_tuple", "_X = tuple(X)\n", "", "watched", ("a",)),
    ("t5_set", "_X = set(X)\n", "", "watched", ("a",)),
    ("t5_frozenset", "_X = frozenset(X)\n", "", "watched", ("a",)),
    ("t5_dict", "_X = dict(X)\n", "", "watched", ("a",)),
    ("t5_sorted", "_X = sorted(X)\n", "", "watched", ("a",)),
    ("t5_sorted_key", "_X = sorted(X, key=lambda w: w.__name__, reverse=True)\n", "", "watched", ("a",)),
    ("t5_sorted_key_builtin", "_X = sorted(X, key=repr)\n", "", "watched", ("a",)),
    ("t5_fromkeys", "_X = dict.fromkeys(X, 0)\n", "", "watched", ("a",)),
    # T6
    ("t6_len", "_X = len(X)\n", "", "watched", ("a",)),
    ("t6_bool", "_X = bool(X)\n", "", "watched", ("a",)),
    # Round 8: `id` is no reader (`ctypes` turns its integer back into the object).
    ("t6_id", "_X = id(X)\n", "", "unread", ("a",)),
    ("t6_hash", "_X = hash(tuple(X))\n", "", "watched", ("a",)),
    ("t6_repr", "_X = repr(X)\n", "", "watched", ("a",)),
    ("t6_str", "_X = str(X)\n", "", "watched", ("a",)),
    ("t6_any", "_X = any(X)\n", "", "watched", ("a",)),
    ("t6_all", "_X = all(X)\n", "", "watched", ("a",)),
    ("t6_min", "_X = min(X)\n", "", "watched", ("a",)),
    ("t6_max", "_X = max(X)\n", "", "watched", ("a",)),
    ("t6_sum", "_X = sum(X)\n", "", "watched", ("a",)),
    ("t6_print", 'print("w", X)\n', "", "watched", ("a",)),
    ("t6_isinstance", "assert isinstance(X, (list, tuple))\n", "", "watched", ("a",)),
    # T7
    ("t7_if", "if X:\n    pass\n", "", "watched", ("a",)),
    ("t7_while", "while not X:\n    break\n", "", "watched", ("a",)),
    ("t7_assert", "assert X\n", "", "watched", ("a",)),
    ("t7_conditional", "_X = 1 if X else 2\n", "", "watched", ("a",)),
    ("t7_for", "for _w in X:\n    _n = _w.__name__\n", "", "watched", ("a",)),
    ("t7_comprehension", "_X = {_w.__name__: _w for _w in X if _w is not None}\n", "", "watched", ("a",)),
    # T8
    ("t8_enumerate_for", "for _i, _w in enumerate(X):\n    pass\n", "", "watched", ("a",)),
    ("t8_reversed_for", "for _w in reversed(X):\n    pass\n", "", "watched", ("a",)),
    ("t8_zip_dict", '_X = dict(zip(["a"], X))\n', "", "watched", ("a",)),
    ("t8_map_lambda", "_X = list(map(lambda w: w.__name__, X))\n", "", "watched", ("a",)),
    ("t8_filter_none", "_X = list(filter(None, X))\n", "", "watched", ("a",)),
    ("t8_chain_comp", "_X = [_w for _w in itertools.chain(X, [])]\n", "import itertools\n", "watched", ("a",)),
    ("t8_iter_for", "for _w in iter(X):\n    pass\n", "", "watched", ("a",)),
    # T9
    ("t9_element", "_X = X[0]\n", "", "watched", ("a",)),
    ("t9_slice", "_X = X[1:]\n", "", "watched", ("a",)),
    ("t9_element_attribute", "_X = X[0].__name__\n", "", "watched", ("a",)),
    # T10
    ("t10_copy", "_X = X.copy()\n", "", "watched", ("a",)),
    ("t10_count", "_X = X.count(AFlow)\n", "", "watched", ("a",)),
    ("t10_index", "_X = X.index(AFlow)\n", "", "watched", ("a",)),
    # T12
    ("t12_fstring", '_X = f"{X!r:>10}"\n', "", "watched", ("a",)),
    ("t12_percent", '_X = "%s" % (X,)\n', "", "watched", ("a",)),
    ("t12_format", '_X = "{0.__class__.__name__} {w}".format(X, w=X)\n', "", "watched", ("a",)),
    # T13
    ("t13_concat", "_X = X + [BFlow]\n", "", "watched", ("a",)),
    ("t13_concat_left", "_X = [BFlow] + X\n", "", "watched", ("a",)),
    ("t13_in_literal", '_X = "a" in X\n', "", "watched", ("a",)),
    ("t13_eq_literal", "_X = X == 1\n", "", "watched", ("a",)),
    ("t13_is", "_X = X is not None\n", "", "watched", ("a",)),
]

# The entries that change the list in place (T11): each must watch BFlow.
CHANGES: list[tuple[str, str]] = [
    ("t11_append", "WORKFLOWS.append(BFlow)\n"),
    ("t11_extend", "WORKFLOWS.extend([BFlow])\n"),
    ("t11_insert", "WORKFLOWS.insert(0, BFlow)\n"),
    ("t11_augassign", "WORKFLOWS += [BFlow]\n"),
    ("t11_store", "WORKFLOWS[0] = BFlow\n"),
    ("t11_slice_store", "WORKFLOWS[0:0] = [BFlow]\n"),
    ("t11_rebinding", "WORKFLOWS = WORKFLOWS + [BFlow]\n"),
]

# A slot the entry does not name: each escapes (the product cannot pass for WORKFLOWS).
SLOTS: list[tuple[str, str, str]] = [
    ("s1_set_star", "_X = {*WORKFLOWS}\n", ""),
    ("s1_call_star", "print(*WORKFLOWS)\n", ""),
    ("s2_unpack", "_X, _Y = WORKFLOWS, 1\n", ""),
    ("s2_attribute_target", "\n\nclass _O:\n    pass\n\n\n_O.w = WORKFLOWS\n", ""),
    ("s2_or", "_X = WORKFLOWS or []\n", ""),
    ("s5_sorted_key_imported", "_X = sorted(WORKFLOWS, key=getrefcount)\n", "from sys import getrefcount\n"),
    ("s5_list_second", "_X = list(WORKFLOWS, *())\n", ""),
    ("s5_sorted_other_keyword", "_X = sorted(WORKFLOWS, cmp=None)\n", ""),
    ("s6_min_key", "_X = min(WORKFLOWS, key=len)\n", ""),
    ("s6_sum_start", "_X = sum(WORKFLOWS, [])\n", ""),
    ("s6_print_file", "print(WORKFLOWS, file=_F)\n", "_F = None\n"),
    ("s6_isinstance_name", "_K = list\n_X = isinstance(WORKFLOWS, _K)\n", ""),
    ("s6_len_two", "_X = len(WORKFLOWS, 1)\n", ""),
    ("s7_item_handed", "for _w in WORKFLOWS:\n    print(_w, file=None)\n", ""),
    ("s7_item_method", "for _w in WORKFLOWS:\n    _w.run(1)\n", ""),
    ("s7_item_to_gc", "_R = gc.get_referrers(WORKFLOWS[0])\n", "import gc\n"),
    ("s7_comprehension_item_handed", "_X = [_keep(_w) for _w in WORKFLOWS]\n", "_keep = id\n"),
    ("s8_iter_bound", "_IT = iter(WORKFLOWS)\n", ""),
    ("s8_map_imported", "_X = list(map(getrefcount, WORKFLOWS))\n", "from sys import getrefcount\n"),
    ("s8_iter_sentinel", "_X = iter(lambda: 0, WORKFLOWS)\n", ""),
    ("s8_zip_bound", '_Z = zip(["a"], WORKFLOWS)\n', ""),
    ("s9_element_to_call", "_keep = id\n_X = _keep(WORKFLOWS[0])\n", ""),
    ("s9_slice_part", '_X = "abc"[WORKFLOWS:]\n', ""),
    ("s10_sort_key", "WORKFLOWS.sort(key=getrefcount)\n", "from sys import getrefcount\n"),
    ("s10_attribute_read", "_X = WORKFLOWS.append\n", ""),
    ("s11_not_a_statement", "_X = WORKFLOWS.append(BFlow)\n", ""),
    ("s11_getattr", 'getattr(WORKFLOWS, "append")(BFlow)\n', ""),
    ("s11_class_body", "\n\nclass _C:\n    WORKFLOWS.append(BFlow)\n", ""),
    ("s11_star_argument", "WORKFLOWS.append(*[BFlow])\n", ""),
    ("s11_in_block", "if True:\n    WORKFLOWS.append(BFlow)\n", ""),
    ("s12_tstring", '_X = t"{WORKFLOWS}"\n', ""),
    ("s12_format_builtin", '_X = format(WORKFLOWS, "")\n', ""),
    ("s13_eq_name", "_h = print\n_X = WORKFLOWS == _h\n", ""),
    ("s13_eq_display", "_X = WORKFLOWS == []\n", ""),
    ("s13_lt", "_X = WORKFLOWS < []\n", ""),
    ("s13_aug_other", "_ACC = []\n_ACC += WORKFLOWS\n", ""),
    ("s_match", "match WORKFLOWS:\n    case _:\n        pass\n", ""),
    ("s_lambda_default", "_f = lambda w=WORKFLOWS: w\n", ""),
    ("s_with", "import contextlib  # noqa: E402\n\nwith contextlib.nullcontext(WORKFLOWS):\n    pass\n", ""),
    ("s_walrus", "(_X := WORKFLOWS)\n", ""),
    ("s_library_reader", "_X = copy.copy(WORKFLOWS)\n", "import copy\n"),
    ("s_patched_builtin", "builtins.len = len\n_X = len(WORKFLOWS)\n", "import builtins\n"),
    ("s_patched_alias", "_b.len = len\n_X = len(WORKFLOWS)\n", "import builtins as _b\n"),
]

# Records (T3, T4): a record class of the product, by how it is decorated or built. `watched`: the
# record holds WORKFLOWS and nothing changes it; `unread`: the class is not bare (T3) or not plain
# (T4), or the record is used outside T3 and T4.
REC = "backend/temporal/rec.py"
DATACLASS = "from dataclasses import dataclass, field\n\n\n"
RECORD_CLASSES: dict[str, str] = {
    "plain": "class Rec:\n    def __init__(self, workflows=(), name=''):\n        self.workflows = workflows\n",
    "dataclass": DATACLASS
    + "@dataclass\nclass Rec:\n    workflows: list = field(default_factory=list)\n    name: str = ''\n",
    "dataclass_args": DATACLASS
    + "@dataclass(frozen=True)\nclass Rec:\n    workflows: list = field(default_factory=list)\n    name: str = ''\n",
    "function_decorator": "def _mark(cls):\n    return cls\n\n\n@_mark\nclass Rec:\n    def __init__(self, workflows=(), name=''):\n        self.workflows = workflows\n",
    "new": "class Rec:\n    def __new__(cls, workflows=(), name=''):\n        return super().__new__(cls)\n\n    def __init__(self, workflows=(), name=''):\n        self.workflows = workflows\n",
    "other_decorator": "import functools\n\n\n@functools.total_ordering\nclass Rec:\n    def __init__(self, workflows=(), name=''):\n        self.workflows = workflows\n",
    "base": "class _Base:\n    pass\n\n\nclass Rec(_Base):\n    def __init__(self, workflows=(), name=''):\n        self.workflows = workflows\n",
    "metaclass": "class _Meta(type):\n    pass\n\n\nclass Rec(metaclass=_Meta):\n    def __init__(self, workflows=(), name=''):\n        self.workflows = workflows\n",
    "property": DATACLASS
    + "@dataclass\nclass Rec:\n    workflows: list = field(default_factory=list)\n    name: str = ''\n\n    @property\n    def size(self):\n        return len(self.workflows)\n",
}
RECORD_IMPORT = "from products.{p}.backend.temporal.rec import Rec\n"

RECORDS: list[tuple[str, str, str, str]] = [
    # (name, record class, shape, outcome)
    ("t3_plain", "plain", "BINDING = Rec(workflows=WORKFLOWS)\n", "watched"),
    ("t3_positional", "plain", "BINDING = Rec(WORKFLOWS)\n", "watched"),
    ("t3_display", "plain", "BINDING = Rec(workflows=(WORKFLOWS,))\n", "watched"),
    ("t3_nested", "plain", "BINDING = Rec(workflows=[Rec(workflows=WORKFLOWS)])\n", "watched"),
    ("t3_record_of_record", "plain", "INNER = Rec(workflows=WORKFLOWS)\nBINDING = Rec(workflows=INNER)\n", "watched"),
    ("t3_class_holder", "plain", "\n\nclass _K:\n    L = WORKFLOWS\n\n\nBINDING = Rec(workflows=_K)\n", "watched"),
    ("t3_dataclass", "dataclass", "BINDING = Rec(workflows=WORKFLOWS)\n", "watched"),
    ("t3_dataclass_args", "dataclass_args", "BINDING = Rec(workflows=WORKFLOWS)\n", "watched"),
    # Round 8: a first-party decorator may return anything, so the class is not bare.
    ("t3_function_decorator", "function_decorator", "BINDING = Rec(workflows=WORKFLOWS)\n", "unread"),
    ("t3_new", "new", "BINDING = Rec(workflows=WORKFLOWS)\n", "watched"),
    ("t3_other_decorator", "other_decorator", "BINDING = Rec(workflows=WORKFLOWS)\n", "unread"),
    ("t3_base", "base", "BINDING = Rec(workflows=WORKFLOWS)\n", "unread"),
    ("t3_metaclass", "metaclass", "BINDING = Rec(workflows=WORKFLOWS)\n", "unread"),
    ("t3_kwargs", "plain", 'BINDING = Rec(**{"workflows": WORKFLOWS})\n', "unread"),
    ("t3_inline_handed", "plain", "print(Rec(workflows=WORKFLOWS))\n", "unread"),
    ("t3_in_display", "plain", "_L = [Rec(workflows=WORKFLOWS)]\n", "unread"),
    ("t3_bound_twice", "plain", "BINDING = Rec(workflows=WORKFLOWS)\nBINDING = Rec(workflows=WORKFLOWS)\n", "unread"),
    ("t3_record_test", "plain", "BINDING = Rec(workflows=WORKFLOWS)\nif BINDING:\n    pass\n", "unread"),
    ("t3_record_fstring", "plain", 'BINDING = Rec(workflows=WORKFLOWS)\n_M = f"{BINDING}"\n', "unread"),
    ("t3_record_attribute", "plain", "BINDING = Rec(workflows=WORKFLOWS)\n_X = BINDING.workflows\n", "unread"),
    ("t4_replace", "dataclass", "BINDING = Rec(workflows=WORKFLOWS)\nCOPY = replace(BINDING, name='y')\n", "watched"),
    (
        "t4_replace_of_replace",
        "dataclass",
        "BINDING = Rec(workflows=WORKFLOWS)\nCOPY = replace(BINDING, name='y')\nAGAIN = replace(COPY, name='z')\n",
        "unread",
    ),
    ("t4_property", "property", "BINDING = Rec(workflows=WORKFLOWS)\nCOPY = replace(BINDING, name='y')\n", "unread"),
    (
        "t4_kwargs",
        "dataclass",
        "BINDING = Rec(workflows=WORKFLOWS)\nCOPY = replace(BINDING, **{'name': 'y'})\n",
        "unread",
    ),
    (
        "t4_attribute_target",
        "dataclass",
        "BINDING = Rec(workflows=WORKFLOWS)\n\n\nclass _O:\n    pass\n\n\n_O.c = replace(BINDING, name='y')\n",
        "unread",
    ),
]


def entry_cases() -> dict[str, dict[str, Any]]:
    cases = {}
    for name, shape, head, outcome, probes in ENTRIES:
        p = f"r7e_{name}"
        cases[p] = case(p, shape.replace("X", "WORKFLOWS"), outcome, f"{name}: the list itself", head, probes=probes)
        for holder, binding in HOLDERS.items():
            p = f"r7e_{name}_{holder}"
            body = binding + shape.replace("X", HOLDER_NAMES[holder])
            cases[p] = case(p, body, "unread", f"{name}: through a holder ({holder})", head)
    for name, shape in CHANGES:
        p = f"r7e_{name}"
        cases[p] = case(p, shape, "watched", f"{name}: a change the table writes a row for", probes=("a", "b"))
    for name, shape, head in SLOTS:
        p = f"r7e_{name}"
        cases[p] = case(p, shape, "unread", f"{name}: a slot no entry names", head)
    for name, kind, shape, outcome in RECORDS:
        p = f"r7e_{name}"
        head = RECORD_IMPORT + ("from dataclasses import replace\n" if "replace(" in shape else "")
        files = {REC: RECORD_CLASSES[kind]}
        cases[p] = case(p, shape, outcome, f"{name}: a record ({kind})", head, files)
    return cases


CASES: dict[str, dict[str, Any]] = entry_cases()
