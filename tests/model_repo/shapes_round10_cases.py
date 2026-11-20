"""Round 10's cases (CASES.md `WA22`, `engine/brief-watched-round10.md`): recheck 8's seven families,
each narrowed to positive evidence. Each attack row registers BFlow in plain Python; each twin
does not. The facade binds `WORKFLOWS = [AFlow]` and the shape (`case`), or, for a computed
module key, another file G of the product reaches the facade (`row`, as in round 9b):

1. a chained assignment links every target to the same object; another target escapes;
2. a `sys.modules` key resolves only on positive evidence (a literal, a name bound once in a file
   with no namespace writer, `spec.name` of a spec nothing else touches before the store), and
   every operation on `sys.modules` is a write of the keys it may insert, replace, remove or
   restore (strict: a teardown or an unknown key is every module, `alone`);
3. `getattr(x, "<literal>")` is `x.<literal>`, dunders too; a computed name may be any attribute;
4. a class read is one through a class statement's name, of a stable class whose binding runs no
   first-party code (no decorator but a trusted builtin, an inert value);
5. a class holder escapes when its decorator, its metaclass or a subclass may run code;
6. a record class has no `__del__`; a store into an object no dotted name gives stores into every
   class the file defines or imports;
7. T9 to T13 read only when every operand is a literal and every item the operation compares,
   hashes, formats or releases is proved inert."""

from __future__ import annotations

from typing import Any

from model_repo.shapes_round7_cases import case
from model_repo.shapes_round9b_cases import G_HEAD, IM, row

CASES: dict[str, dict[str, Any]] = {}


# The imports a facade needs when its body binds WORKFLOWS itself (a second binding alone would
# make the list unread).
OWN_HEAD = "from products.{p}.backend.temporal.a import AFlow\nfrom products.{p}.backend.temporal.b import BFlow\n\n"


def add(p: str, body: str, outcome: str, note: str, **extra: Any) -> None:
    """A facade case: `body` after `WORKFLOWS = [AFlow]`, or alone when it binds WORKFLOWS."""
    files = dict(extra.pop("files", None) or {})
    if any(line.startswith(("WORKFLOWS = ", "_V = WORKFLOWS")) for line in body.splitlines()):
        files["backend/facade/temporal.py"] = OWN_HEAD + body
        body = ""
    CASES[p] = {**case(p, body, outcome, note, files=files), **extra}


def add_g(p: str, g: str, outcome: str, note: str, **extra: Any) -> None:
    """A case of G (round 9b's facade and record), `g` after G's head."""
    files = extra.pop("files", {})
    out = row(p, g, outcome, note, **extra)
    for path, text in files.items():
        out["files"][path] = text.replace("{p}", p)
    CASES[p] = out


# 1. Chained assignment: every target is the same object.

add(
    "r10_chain_append",
    "WORKFLOWS = _V = [AFlow]\n_V.append(BFlow)\n",
    "unread",
    "chained, then a change through the other target",
)
add(
    "r10_chain_reverse",
    "_V = WORKFLOWS = [AFlow]\n_V += [BFlow]\n",
    "unread",
    "chained the other way, then `+=` through it",
)
add(
    "r10_chain_escape",
    "WORKFLOWS = _V = [AFlow]\n_S = [_V]\n_S[0].append(BFlow)\n",
    "unread",
    "chained, then the other target escapes",
)
add(
    "r10_chain_attribute_target",
    "\n\nclass _N:\n    pass\n\n\nWORKFLOWS = _N.x = [AFlow]\n_N.x.append(BFlow)\n",
    "unread",
    "a target that is no plain name hands the object on",
)
add(
    "r10_chain_subscript_target",
    '_D = {}\nWORKFLOWS = _D["k"] = [AFlow]\n_D["k"].append(BFlow)\n',
    "unread",
    "a subscript target hands the object on",
)
add(
    "r10_ok_chain_star",
    "WORKFLOWS = _V = [AFlow]\n_X = [*_V]\n",
    "watched",
    "harmless: chained, then T1 of the other target",
)
add(
    "r10_global_alias",
    "\n\ndef _p():\n    global _X\n    _X = WORKFLOWS\n\n\n_p()\n_X.append(BFlow)\n",
    "unread",
    "a function binds a global name to the list; a module-level change through it",
)
add(
    "r10_global_holder",
    "\n\ndef _p():\n    global _X\n    _X = [WORKFLOWS]\n\n\n_p()\n_X[0].append(BFlow)\n",
    "unread",
    "a function binds a global name to a holder of the list",
)

# 2. `sys.modules` keys and publication.
FAKE = (
    'import sys\nimport types\n\n_fake = types.ModuleType("x")\n_fake.WORKFLOWS = [AFlow, BFlow]\n_fake.Rec = _Evil\n'
)
TARGET = '"products.{p}.backend.facade.temporal"'
SPEC = "import importlib.util\n" + FAKE + '_s = importlib.util.spec_from_loader("_harmless", None)\n'
add_g(
    "r10_spec_name_store",
    SPEC + f"_s.name = {TARGET}\nsys.modules[_s.name] = _fake\n",
    "unread",
    "spec.name stored to before the store",
    alone=True,
)
add_g(
    "r10_spec_name_setattr",
    SPEC + f'setattr(_s, "name", {TARGET})\nsys.modules[_s.name] = _fake\n',
    "unread",
    "spec.name set by setattr before the store",
    alone=True,
)
add_g(
    "r10_spec_alias_in_function",
    "import importlib.util\n"
    + FAKE
    + '\n\ndef _r():\n    _s = importlib.util.spec_from_loader("_harmless", None)\n    _t = _s\n'
    + f"    _t.name = {TARGET}\n    sys.modules[_s.name] = _fake\n\n\n_r()\n",
    "unread",
    "a spec handed to another name in a function before the store",
    alone=True,
)
add_g(
    "r10_key_rebound_by_globals",
    FAKE + f'_K = "_harmless"\nglobals()["_K"] = {TARGET}\n\n\ndef _p():\n    sys.modules[_K] = _fake\n\n\n_p()\n',
    "unread",
    "a key name a `globals()` write rebinds",
    alone=True,
)
add_g(
    "r10_key_rebound_by_exec",
    FAKE + f"_K = \"_harmless\"\nexec('_K = {TARGET}')\nsys.modules[_K] = _fake\n",
    "unread",
    "a key name `exec` rebinds",
    alone=True,
)
add_g("r10_modules_update", FAKE + f"sys.modules.update({{{TARGET}: _fake}})\n", "unread", "published by `update`")
add_g(
    "r10_modules_setitem", FAKE + f"sys.modules.__setitem__({TARGET}, _fake)\n", "unread", "published by `__setitem__`"
)
add_g(
    "r10_modules_setdefault",
    FAKE + f"sys.modules.pop({TARGET})\nsys.modules.setdefault({TARGET}, _fake)\n",
    "unread",
    "removed, then published by `setdefault`",
)
add_g("r10_modules_ior", FAKE + f"sys.modules |= {{{TARGET}: _fake}}\n", "unread", "published by `|=`")
add_g(
    "r10_modules_get",
    f"import sys\n\nsys.modules.get({TARGET}).WORKFLOWS.append(BFlow)\n",
    "unread",
    "a change through `sys.modules.get`",
)
add_g(
    "r10_modules_passed",
    FAKE + f"\n\ndef _put(d):\n    d[{TARGET}] = _fake\n\n\n_put(sys.modules)\n",
    "unread",
    "sys.modules handed to a call",
    alone=True,
)
add_g(
    "r10_modules_stored",
    FAKE + f"_held = [sys.modules]\n_held[0][{TARGET}] = _fake\n",
    "unread",
    "sys.modules stored",
    alone=True,
)
add_g(
    "r10_modules_values",
    "import sys\n\nfor _m in list(sys.modules.values()):\n"
    f'    if getattr(_m, "__name__", "") == {TARGET}:\n        _m.WORKFLOWS.append(BFlow)\n',
    "unread",
    "any module by `values()`",
    alone=True,
)
add_g(
    "r10_patch_dict_string",
    "from unittest.mock import patch\n\n"
    + FAKE
    + f'_patcher = patch.dict("sys.modules", {{{TARGET}: _fake}})\n_patcher.start()\n',
    "unread",
    "`patch.dict` on the string `sys.modules`: the keys of its values (limit 10: its teardown is not followed)",
)
add_g(
    "r10_ok_setdefault_library",
    FAKE + 'sys.modules.setdefault("_harmless_library", _fake)\n',
    "watched",
    "harmless: a key no first-party module has",
)
LOADER = (
    "import importlib.util\nimport sys\nfrom pathlib import Path\n\n"
    '_PATH = Path(__file__).with_name("helper.py")\n\n\n'
    "def _load():\n"
    '    spec = importlib.util.spec_from_file_location("_harmless_helper", _PATH)\n'
    "    assert spec is not None\n    assert spec.loader is not None\n"
    "    module = importlib.util.module_from_spec(spec)\n"
    "{touch}"
    "    sys.modules[spec.name] = module\n    spec.loader.exec_module(module)\n    return module\n\n\n"
    "_helper = _load()\n"
)
add_g(
    "r10_ok_spec_loader",
    LOADER.format(touch=""),
    "watched",
    "harmless: the tools/ loaders' shape (t30's ruling (a))",
    files={"backend/helper.py": "X = 1\n"},
)
add_g(
    "r10_spec_module_handed",
    "\n\ndef _touch(m):\n    return m\n\n\n" + LOADER.format(touch="    _touch(module)\n"),
    "unread",
    "over-fail by rule: the module handed on before the store (its `__spec__` is the spec)",
    files={"backend/helper.py": "X = 1\n"},
    alone=True,
    overfail=True,
)
MONKEYPATCH = "import sys\nimport types\n\n\ndef test_fake(monkeypatch):\n{body}"
for name, body, outcome, note, extra in (
    (
        "library",
        '    monkeypatch.setitem(sys.modules, "_harmless_library", types.ModuleType("x"))\n',
        "watched",
        "harmless: pytest's monkeypatch, a library key",
        {},
    ),
    (
        "first_party",
        f'    monkeypatch.setitem(sys.modules, {TARGET}, types.ModuleType("x"))\n',
        "unread",
        "pytest's monkeypatch, the facade's key",
        {},
    ),
    (
        "loop_key",
        '    for name in ["a"]:\n        monkeypatch.delitem(sys.modules, name)\n',
        "watched",
        "limit 10: pytest's monkeypatch removes a key no literal gives; a removal is not followed",
        {},
    ),
):
    add_g(
        f"r10_monkeypatch_{name}",
        "",
        outcome,
        note,
        files={
            "backend/g.py": G_HEAD + "import products.{p}.backend.tests.test_g  # noqa: F401\n",
            "backend/tests/__init__.py": "",
            "backend/tests/test_g.py": MONKEYPATCH.format(body=body),
        },
        witness=False,
        **extra,
    )

# Limit 10 (droppable, the owner's call): a removal or a restore publishes no value, so it is not
# followed; strict (commit 1) each of these may replace a module.
A_KEY = '"products.{p}.backend.temporal.a"'
for name, g in {
    "pop": f"import sys\n\nsys.modules.pop({A_KEY}, None)\n",
    "del": f"import sys\n\ndel sys.modules[{A_KEY}]\n",
    "snapshot": "import sys\nfrom unittest.mock import patch\n\nwith patch.dict(sys.modules):\n    pass\n",
}.items():
    add_g(f"r10_limit10_{name}", g, "watched", f"limit 10: a removal or a restore ({name}) is not followed")

# 3. `getattr` with a literal is the attribute, dunders too.
add_g(
    "r10_getattr_dict_store",
    IM + 'getattr(importlib.import_module(_n), "__dict__")["Rec"] = _Evil\n',
    "unread",
    "a namespace by `getattr`, then a store",
)
add_g(
    "r10_getattr_setattr_call",
    IM + 'getattr(importlib.import_module(_n), "__setattr__")("Rec", _Evil)\n',
    "unread",
    "`__setattr__` by `getattr`, then called",
    alone=True,
)
GF = "from products.{p}.backend.facade.temporal import _hook\n\n"
add_g(
    "r10_getattr_globals", GF + 'getattr(_hook, "__globals__")["Rec"] = _Evil\n', "unread", "`__globals__` by `getattr`"
)
add_g(
    "r10_getattr_computed_globals",
    GF + 'getattr(_hook, "__glo" + "bals__")["Rec"] = _Evil\n',
    "unread",
    "a computed attribute name may be `__globals__`",
)
add_g(
    "r10_ok_getattr_member",
    'import importlib\n\ngetattr(importlib.import_module("products.{p}.backend.facade.temporal"), "OTHER", None)\n',
    "watched",
    "harmless: a literal member read by `getattr`",
)

# 4. A class read is through a class statement's name, of a stable class.
GETATTRIBUTE = (
    "\n\nclass _K:\n{body}\n    def __getattribute__(self, n):\n"
    "        return {value} if n == 'L' else object.__getattribute__(self, n)\n\n\n"
)
add(
    "r10_instance_read",
    GETATTRIBUTE.format(body="    L = [AFlow]\n", value="[AFlow, BFlow]") + "_k = _K()\nWORKFLOWS = _k.L\n",
    "unread",
    "a read through an instance bound to a name",
)
add(
    "r10_instance_item",
    GETATTRIBUTE.format(body="    def L(self):\n        pass\n", value="BFlow")
    + "_k = _K()\nWORKFLOWS = [AFlow, _k.L]\n",
    "unread",
    "an item read through an instance bound to a name",
)
add(
    "r10_def_decorated",
    "\n\ndef _d(f):\n    return BFlow\n\n\nclass _K:\n    @_d\n    def L(self):\n        pass\n\n\nWORKFLOWS = [AFlow, _K.L]\n",
    "unread",
    "a decorated def as the one binding",
)
add(
    "r10_class_decorated",
    "\n\ndef _d(c):\n    return BFlow\n\n\nclass _K:\n    @_d\n    class L:\n        pass\n\n\nWORKFLOWS = [AFlow, _K.L]\n",
    "unread",
    "a decorated class as the one binding",
)
add(
    "r10_metaclass_get",
    "\n\nclass _MDL(type):\n    def __get__(cls, obj, owner=None):\n        return [AFlow, BFlow]\n\n\n"
    "class _Dl(metaclass=_MDL):\n    pass\n\n\nclass _K:\n    L = _Dl\n\n\nWORKFLOWS = _K.L\n",
    "unread",
    "a value whose metaclass has `__get__`",
)
add(
    "r10_class_alias_read",
    "\n\nclass _K:\n    L = [AFlow]\n\n\n_C = _K\nWORKFLOWS = _C.L\n",
    "unread",
    "over-fail by rule: a read through another name of the class",
    overfail=True,
)
add(
    "r10_ok_class_read",
    "\n\nclass _K:\n    L = [AFlow]\n\n\nWORKFLOWS = _K.L\n",
    "watched",
    "harmless: a stable class read",
)
add(
    "r10_ok_staticmethod",
    "\n\nclass _K:\n    @staticmethod\n    def L():\n        pass\n\n\nWORKFLOWS = [AFlow, _K.L]\n",
    "watched",
    "harmless: a staticmethod read through the class",
)

# 5. Class holder uses that run code.
add(
    "r10_holder_decorator",
    "\n\ndef _d(c):\n    c.L.append(BFlow)\n    return c\n\n\n@_d\nclass _U:\n    L = WORKFLOWS\n",
    "unread",
    "a decorated holder",
)
add(
    "r10_holder_metaclass",
    "\n\nclass _M(type):\n    def __getattribute__(cls, n):\n        if n == 'x':\n"
    "            type.__getattribute__(cls, 'L').append(BFlow)\n        return type.__getattribute__(cls, n)\n\n\n"
    "class _U(metaclass=_M):\n    L = WORKFLOWS\n    x = 1\n\n\n_y = _U.x\n",
    "unread",
    "a holder whose metaclass runs code on another attribute read",
)
add(
    "r10_holder_super",
    "\n\nclass _U:\n    L = WORKFLOWS\n\n\nclass _V(_U):\n    L = []\n\n\nsuper(_V, _V).L.append(BFlow)\n",
    "unread",
    "a holder reached through `super()` of a subclass",
)
add(
    "r10_holder_base_elsewhere",
    "\n\nclass _U:\n    L = WORKFLOWS\n\n\nimport products.{p}.backend.g  # noqa: E402,F401\n",
    "unread",
    "a holder that another file's class statement takes as a base",
    files={
        "backend/g.py": "from products.{p}.backend.facade.temporal import _U\n"
        "from products.{p}.backend.temporal.b import BFlow\n\n\nclass _V(_U):\n    pass\n\n\n_V.L.append(BFlow)\n"
    },
)
add("r10_ok_holder", "\n\nclass _U:\n    L = WORKFLOWS\n", "watched", "harmless: a plain holder")
add(
    "r10_ok_holder_dataclass",
    "import dataclasses\n\n\n@dataclasses.dataclass\nclass _U:\n    L = WORKFLOWS\n",
    "watched",
    "harmless: a dataclass holder (not a field)",
)

# 6. Record checks.
REC_DC = "from dataclasses import dataclass\n\n{head}\n\n@dataclass\nclass Rec:\n    workflows: list\n{body}"
REC_FILE = "backend/temporal/rec.py"
REC_IMPORT = "from products.{p}.backend.temporal.rec import Rec\n"
add(
    "r10_record_del",
    REC_IMPORT + "\nREC = Rec(workflows=WORKFLOWS)\nREC = None\n",
    "unread",
    "a record class with `__del__`, its record released",
    files={
        REC_FILE: REC_DC.format(
            head="from products.{p}.backend.temporal.b import BFlow\n",
            body="\n    def __del__(self):\n        self.workflows.append(BFlow)\n",
        )
    },
)
EVIL_INIT = "\n\ndef _evil(self, workflows):\n    workflows.append(BFlow)\n    self.workflows = workflows\n\n\n"
add(
    "r10_record_type_store",
    REC_IMPORT + EVIL_INIT + "type(Rec([])).__init__ = _evil\nREC = Rec(workflows=WORKFLOWS)\n",
    "unread",
    "a store into `type(x)`, an object no dotted name gives",
    files={REC_FILE: REC_DC.format(head="", body="")},
)
add(
    "r10_record_call_store",
    REC_IMPORT + EVIL_INIT + "def _f():\n    return Rec\n\n\n_f().__init__ = _evil\nREC = Rec(workflows=WORKFLOWS)\n",
    "unread",
    "a store into a call's result",
    files={REC_FILE: REC_DC.format(head="", body="")},
)
add(
    "r10_ok_record",
    REC_IMPORT + "\nREC = Rec(workflows=WORKFLOWS)\n",
    "watched",
    "harmless: a plain record",
    files={REC_FILE: REC_DC.format(head="", body="")},
)

# 7. Operator hooks run by T9 to T13 (t30's ruling on family 7).
ONCE = "        if not any(x is BFlow for x in WORKFLOWS):\n            WORKFLOWS.append(BFlow)\n"
ITEM = "\n\nclass _F:\n    def {hook}(self{params}):\n" + ONCE + "        return {ret}\n\n\n"
HOOKS = {
    "repr": ("__repr__", "", '"F"'),
    "eq": ("__eq__", ", other", "False"),
    "del": ("__del__", "", "None"),
}
# `{0[0]}` formats the first item; the release forms drop the last.
FIRST, LAST = "WORKFLOWS = [_F(), AFlow]\n", "WORKFLOWS = [AFlow, _F()]\n"
for name, (hook, use) in {
    "fstring": ("repr", '_X = f"{WORKFLOWS}"\n'),
    "percent": ("repr", '_X = "%s" % WORKFLOWS\n'),
    "format": ("repr", '_X = "{}".format(WORKFLOWS)\n'),
    "format_index": ("repr", '_X = "{0[0]}".format(WORKFLOWS)\n'),
    "in": ("eq", "_X = 1 in WORKFLOWS\n"),
    "not_in": ("eq", "_X = 1 not in WORKFLOWS\n"),
    "count": ("eq", "_X = WORKFLOWS.count(1)\n"),
    "pop": ("del", "WORKFLOWS.pop()\n"),
    "del_item": ("del", "del WORKFLOWS[-1]\n"),
    "slice_store": ("del", "WORKFLOWS[1:] = []\n"),
}.items():
    method, params, ret = HOOKS[hook]
    add(
        f"r10_item_{name}",
        ITEM.format(hook=method, params=params, ret=ret) + (FIRST if name == "format_index" else LAST) + use,
        "unread",
        f"an item's `{method}` run by the operation",
    )
add(
    "r10_item_metaclass",
    "\n\nclass _MF(type):\n    def __repr__(cls):\n        WORKFLOWS.append(BFlow)\n        return 'KF'\n\n\n"
    'class _KF(metaclass=_MF):\n    pass\n\n\nWORKFLOWS = [AFlow, _KF]\n_X = f"{WORKFLOWS}"\n',
    "unread",
    "an item's metaclass `__repr__` run by an f-string",
)
OPERAND = "\n\nclass _O:\n    def {hook}(self{params}):\n" + ONCE + "        return {ret}\n\n\n"
for name, (hook, params, ret, use) in {
    "index": ("__index__", "", "0", "_X = WORKFLOWS[_O()]\n"),
    "slice": ("__index__", "", "0", "_X = WORKFLOWS[_O():]\n"),
    "count": ("__eq__", ", other", "False", "_X = WORKFLOWS.count(_O())\n"),
    "pop": ("__index__", "", "0", "WORKFLOWS.append(AFlow)\nWORKFLOWS.pop(_O())\n"),
    "setitem": ("__index__", "", "0", "WORKFLOWS[_O()] = AFlow\n"),
    "format_extra": ("__format__", ", spec", '""', '_X = "{}{}".format(WORKFLOWS, _O())\n'),
    "percent_tuple": ("__str__", "", '""', '_X = "%s%s" % (WORKFLOWS, _O())\n'),
    "fstring_spec": ("__format__", ", spec", '""', '_X = f"{WORKFLOWS!s:{_O()}}"\n'),
}.items():
    add(
        f"r10_operand_{name}",
        OPERAND.format(hook=hook, params=params, ret=ret) + use,
        "unread",
        f"an operand's `{hook}` run by the operation",
    )
for name, use in {
    "index": "_X = WORKFLOWS[0]\n",
    "slice": "_X = WORKFLOWS[0:1]\n",
    "fstring": '_X = f"{WORKFLOWS}"\n',
    "count": "_X = WORKFLOWS.count(1)\n",
    "in": "_X = 1 in WORKFLOWS\n",
    "pop": "WORKFLOWS.append(AFlow)\nWORKFLOWS.pop()\n",
}.items():
    add(f"r10_ok_operation_{name}", use, "watched", f"harmless: literal operands and inert items ({name})")
add(
    "r10_ok_star_hooked_items",
    ITEM.format(hook="__repr__", params="", ret='"F"') + "WORKFLOWS = [AFlow, _F()]\n_X = [*WORKFLOWS]\n",
    "watched",
    "harmless: T1 runs no item's code",
)

# Codex's rows on the live stop (t30's ruling, point 5).
add(
    "r10_instance_shadowing",
    "\n\nclass _K:\n    def __init__(self):\n        self.L = BFlow\n\n    def L(self):\n        pass\n\n\n"
    "WORKFLOWS = [AFlow, _K().L]\n",
    "unread",
    "an instance attribute shadows the method a read names",
)
add(
    "r10_decorator_descriptor",
    "\n\nclass _Desc:\n    def __get__(self, obj, owner=None):\n        return BFlow\n\n\n"
    "def _d(f):\n    return _Desc()\n\n\nclass _K:\n    @_d\n    def L(self):\n        pass\n\n\n"
    "WORKFLOWS = [AFlow, _K.L]\n",
    "unread",
    "a decorator that returns a descriptor",
)
# Both workers' registrations of an activity method (the live fix, PostHog 38a7878ef5b9).
WAITER = (
    "\n\ndef _defn(f):\n    return f\n\n\nclass _Waiter:\n    @_defn\n    def wait(self, inputs):\n"
    "        return inputs\n\n\n_WAITER = _Waiter()\n\n\n"
)
add(
    "r10_ok_wrapper_activity",
    WAITER + "@_defn\ndef wait(inputs):\n    return _WAITER.wait(inputs)\n\n\nWORKFLOWS = [AFlow, wait]\n",
    "watched",
    "harmless: a module-level wrapper of a bound activity method",
)
add(
    "r10_bound_method_activity",
    WAITER + "WORKFLOWS = [AFlow, _WAITER.wait]\n",
    "unread",
    "over-fail by rule: a bound method of an instance (family 4, the live form before its fix)",
    overfail=True,
)
add_g(
    "r10_ok_external_cleanup",
    'import sys\n\nsys.modules.pop("_harmless_library", None)\n',
    "watched",
    "harmless: a cleanup of a key no first-party module has",
)

# The import system (commit 3): G redirects the facade's later import of `temporal.a` to BFlow.
REDIRECT_FACADE = (
    "import products.{p}.backend.g  # noqa: F401\n"
    "from products.{p}.backend.temporal.a import AFlow\n\nWORKFLOWS = [AFlow]\n"
)
FINDER = (
    "import importlib.abc\nimport importlib.util\nimport types\n\n"
    "from products.{p}.backend.temporal.b import BFlow\n\n\n"
    "class _Loader(importlib.abc.Loader):\n    def create_module(self, spec):\n        return None\n\n"
    "    def exec_module(self, module):\n        module.AFlow = BFlow\n\n\n"
    "class _Finder(importlib.abc.MetaPathFinder):\n    def find_spec(self, name, path, target=None):\n"
    '        if name.endswith(".temporal.a"):\n            return importlib.util.spec_from_loader(name, _Loader())\n'
    "        return None\n\n\n"
)


def add_redirect(p: str, g: str, note: str, **extra: Any) -> None:
    files = {"backend/facade/temporal.py": REDIRECT_FACADE, "backend/g.py": g}
    CASES[p] = {**case(p, "", "unread", note, files=files), "alone": True, **extra}


add_redirect(
    "r10_meta_path_insert",
    "import sys\n" + FINDER + "sys.meta_path.insert(0, _Finder())\n",
    "a finder put on sys.meta_path",
)
add_redirect(
    "r10_meta_path_alias",
    "from sys import meta_path as _mp\n" + FINDER + "_mp.insert(0, _Finder())\n",
    "a finder put on sys.meta_path through an alias",
)
add_redirect(
    "r10_meta_path_setattr",
    "import sys\n" + FINDER + 'setattr(sys, "meta_path", [_Finder(), *sys.meta_path])\n',
    "sys.meta_path replaced by setattr",
)
PATH_DIR = (
    "import os\nimport tempfile\n\nimport products.{p}.backend.temporal as _pkg\n\n"
    '_d = tempfile.mkdtemp()\nwith open(os.path.join(_d, "a.py"), "w") as _f:\n'
    '    _f.write("from products.{p}.backend.temporal.b import BFlow as AFlow\\n")\n'
)
add_redirect(
    "r10_package_path_insert",
    PATH_DIR + "_pkg.__path__.insert(0, _d)\n",
    "a directory put first on a package's __path__",
    alone=False,
)
add_redirect(
    "r10_package_path_store",
    PATH_DIR + "_pkg.__path__ = [_d, *_pkg.__path__]\n",
    "a package's __path__ replaced",
    alone=False,
)
add_redirect(
    "r10_import_replaced",
    "import builtins\n\nfrom products.{p}.backend.temporal.b import BFlow\n\n_real = builtins.__import__\n\n\n"
    "def _imp(name, globals=None, locals=None, fromlist=(), level=0):\n    module = _real(name, globals, locals, fromlist, level)\n"
    '    if name.endswith(".temporal.a"):\n        module.AFlow = BFlow\n    return module\n\n\nbuiltins.__import__ = _imp\n',
    "builtins.__import__ replaced",
)
add_redirect(
    "r10_importer_cache",
    "import sys\n\nimport products.{p}.backend.temporal as _pkg\n\nsys.path_importer_cache.clear()\n",
    "sys.path_importer_cache cleared",
    witness=False,
)
add_redirect(
    "r10_invalidate_caches",
    "import importlib\n\nimportlib.invalidate_caches()\n",
    "an explicit importlib.invalidate_caches()",
    witness=False,
)
add_g(
    "r10_ok_iter_modules",
    "import pkgutil\n\nimport products.{p}.backend.temporal as _pkg\n\n_names = [m.name for m in pkgutil.iter_modules(_pkg.__path__)]\n",
    "watched",
    "harmless: pkgutil.iter_modules lists a package's path",
)
# Codex's rows on the commit 3 stop: `pkgutil.iter_modules(M.__path__)` is a read only while no
# file changes a `__path__` or touches the import hooks, and each item gives only `.name` and
# `.ispkg`. G (round 9b's facade: WORKFLOWS bound first) or a redirecting G before `temporal.a`.
PUBLISH = "    import products.{p}.backend.facade.temporal as _f\n\n    _f.WORKFLOWS.append(BFlow)\n"
PKG = "import pkgutil\nimport sys\n\nimport products.{p}.backend.temporal as _pkg\n\n"
READ = "for _m in pkgutil.iter_modules(_pkg.__path__):\n    _n = _m.name\n"
add_g(
    "r10_iter_declining_hook",
    PKG + "\n\ndef _hook(path):\n" + PUBLISH + "    raise ImportError\n\n\n"
    "sys.path_hooks.insert(0, _hook)\nsys.path_importer_cache.pop(_pkg.__path__[0], None)\n" + READ,
    "unread",
    "a path hook that declines and publishes B while iter_modules runs",
    alone=True,
)
add_g(
    "r10_iter_cache_pinning",
    PKG
    + "\n\nclass _Finder:\n    def iter_modules(self, prefix=''):\n"
    + PUBLISH.replace("    ", "        ")
    + "        return iter([])\n\n\nsys.path_importer_cache[_pkg.__path__[0]] = _Finder()\n"
    + READ,
    "unread",
    "a finder pinned in the shared importer cache for the package's path",
    alone=True,
)
add_g(
    "r10_iter_handler_registered",
    "import importlib.machinery\n"
    + PKG
    + "\n\n@pkgutil.iter_importer_modules.register(importlib.machinery.FileFinder)\n"
    + "def _handler(importer, prefix=''):\n"
    + PUBLISH
    + "    return iter([])\n\n\n"
    + READ,
    "unread",
    "a registered iter_importer_modules handler publishes B",
)
add_g(
    "r10_iter_handler_fallback",
    PKG + "\n\ndef _handler(importer, prefix=''):\n" + PUBLISH + "    return iter([])\n\n\n"
    "pkgutil.iter_importer_modules = _handler\n" + READ,
    "unread",
    "the generic iter_importer_modules replaced, a fallback that publishes B",
)
FINDER_ESCAPE = (
    "import importlib.util\n"
    + PKG
    + FINDER.split("class _Finder")[0]
    + "\n\ndef _evil(finder):\n    real = finder.find_spec\n\n"
    "    def find_spec(name, target=None):\n        if name.endswith('.temporal.a'):\n"
    "            return importlib.util.spec_from_loader(name, _Loader())\n        return real(name, target)\n\n"
    "    finder.find_spec = find_spec\n\n\n"
)
for name, loop in {
    "module_finder": "for _m in pkgutil.iter_modules(_pkg.__path__):\n    _evil(_m.module_finder)\n",
    "index": "for _m in pkgutil.iter_modules(_pkg.__path__):\n    _evil(_m[0])\n",
    "first_target": "for _f, _n, _k in pkgutil.iter_modules(_pkg.__path__):\n    _evil(_f)\n",
    "escaped_generator": "\n\ndef _all(modules):\n    for _f, _n, _k in modules:\n        _evil(_f)\n\n\n_all(pkgutil.iter_modules(_pkg.__path__))\n",
}.items():
    add_redirect(
        f"r10_iter_escape_{name}", FINDER_ESCAPE + loop, f"the importer cache's finder escapes: {name}", alone=False
    )
add(
    "r10_iter_namespace_refresh",
    "",
    "unread",
    "a namespace package's path refreshed after its parent's __path__ changes",
    files={
        "backend/ns/a.py": "from products.{p}.backend.temporal.a import AFlow  # noqa: F401\n",
        "backend/facade/temporal.py": "import products.{p}.backend.g  # noqa: F401\nfrom products.{p}.backend.ns.a import AFlow\n\nWORKFLOWS = [AFlow]\n",
        "backend/g.py": "import os\nimport tempfile\n\nimport products.{p}.backend as _parent\n\n_d = tempfile.mkdtemp()\n"
        "os.makedirs(os.path.join(_d, 'ns'))\nwith open(os.path.join(_d, 'ns', 'a.py'), 'w') as _f:\n"
        "    _f.write('from products.{p}.backend.temporal.b import BFlow as AFlow\\n')\n"
        "_parent.__path__.insert(0, _d)\n",
    },
)
add_g(
    "r10_iter_fspath_item",
    PKG
    + "\n\nclass _P:\n    def __fspath__(self):\n"
    + PUBLISH.replace("    ", "        ")
    + "        return '/nonexistent'\n\n\n"
    "_pkg.__path__.insert(0, _P())\n" + READ,
    "unread",
    "a custom path item whose __fspath__ publishes B",
)
add(
    "r10_iter_path_replaced_elsewhere",
    "",
    "unread",
    "a __path__ replaced in another file voids the iter_modules read",
    files={
        "backend/facade/temporal.py": REDIRECT_FACADE,
        "backend/g.py": "import products.{p}.backend.h  # noqa: F401\n" + PKG + READ,
        "backend/h.py": PATH_DIR + "_pkg.__path__ = [_d, *_pkg.__path__]\n",
    },
)
