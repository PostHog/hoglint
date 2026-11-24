"""Round 11's cases (CASES.md `WA23`, `engine/brief-watched-round11.md`): recheck 9's families,
each rule keyed on the object it protects. Each attack row registers BFlow in plain Python 3.14;
each twin (`r11_ok_*`) does not. A row runs alone when its change may reach every module.

1. `sys` and `builtins` reached as a module value (`sys.modules["sys"]`, `os.sys`,
   `import_module("builtins")`), and either module handed to anything but a literal, non-dunder
   attribute read; `meta_path`, `path_hooks`, `path_importer_cache` on any receiver;
2. a package's `__path__` by `setattr`, `getattr`, `vars(...)[...]`, `__dict__[...]`, and
   `globals()["__path__"]` in the package's own `__init__.py`;
3. and 4. (ruling A) shared import machinery changed, replaced or handed on in any first-party
   file: the suffix tables, a loader class's method, a finder from the importer cache,
   `invalidate_caches` by any spelling, `pkgutil.iter_modules` replaced;
5. `pkgutil.iter_modules`: a patch of the callee, and the loop target used through `global`,
   from another file or through `sys.modules[__name__]`;
6. `sys.modules` as `vars(sys)["modules"]`, `sys.__dict__["modules"]`, through a call that takes
   `sys`, as `os.sys.modules`, and a key name another file stores into;
8. (ruling C) a trusted builtin replaced through `builtins` in the reading file or another;
9. T12: another field's items and a copy of the list that gains a value;
10. the item and operand proofs against a `global` rebinding and another file's store;
11. a class body's `global _X; _X = W`;
and the audit's implicit calls (an unpacked item's `__iter__`, an item's attribute read), and the
reads of `sys.meta_path` and `sys.path_hooks` the page counts as reads."""

from __future__ import annotations

from typing import Any

from model_repo.shapes_round7_cases import case

CASES: dict[str, dict[str, Any]] = {}

# The imports a facade needs when its body binds WORKFLOWS itself.
OWN_HEAD = "from products.{p}.backend.temporal.a import AFlow\nfrom products.{p}.backend.temporal.b import BFlow\n\n"


def add(p: str, body: str, outcome: str, note: str, **extra: Any) -> None:
    """A facade case: `body` after `WORKFLOWS = [AFlow]`, or alone when it binds WORKFLOWS."""
    files = dict(extra.pop("files", None) or {})
    if any(line.startswith("WORKFLOWS = ") for line in body.splitlines()):
        files["backend/facade/temporal.py"] = OWN_HEAD + body
        body = ""
    CASES[p] = {**case(p, body, outcome, note, files=files), **extra}


# The import system: the facade reads `L` from `temporal/sub/late.py` (a harmless class) after an
# install that redirects that import to BFlow: through a finder, a path hook, the importer cache, a
# replaced `__import__`, a package path (`alt/late.py`), or a source suffix (`sub/late.txt`).
HOOK = """import importlib.abc
import importlib.util
import os

TARGET = "products.{p}.backend.temporal.sub.late"
HERE = os.path.dirname(__file__)
SUBDIR = os.path.join(HERE, "sub")
ALT = os.path.join(HERE, "alt")


class Loader(importlib.abc.Loader):
    def create_module(self, spec):
        return None

    def exec_module(self, module):
        from products.{p}.backend.temporal.b import BFlow

        module.L = BFlow


class Finder:
    def find_spec(self, name, path=None, target=None):
        if name == TARGET:
            return importlib.util.spec_from_loader(name, Loader())
        return None

    def invalidate_caches(self):
        pass


class EntryFinder:
    def __init__(self, path=""):
        self.path = path

    def find_spec(self, name, target=None):
        return Finder().find_spec(name)

    def invalidate_caches(self):
        pass


def hook(path):
    if os.path.basename(path) == "sub":
        return EntryFinder(path)
    raise ImportError(path)


def entry_find_spec(name, target=None):
    return Finder().find_spec(name)


def fake_import(orig):
    def imp(name, g=None, l=None, fromlist=(), level=0):
        if name == TARGET:
            import types

            from products.{p}.backend.temporal.b import BFlow

            m = types.ModuleType(name)
            m.L = BFlow
            return m
        return orig(name, g, l, fromlist, level)

    return imp


def iter_modules(path=None, prefix=""):
    path.insert(0, ALT)
    return iter(())
"""
B_AS_L = "from products.{p}.backend.temporal.b import BFlow as L  # noqa: F401\n"
SUB = {
    "backend/temporal/hook.py": HOOK,
    "backend/temporal/sub/__init__.py": "",
    "backend/temporal/sub/late.py": "class L:\n    pass\n",
    "backend/temporal/sub/late.txt": B_AS_L,
    "backend/temporal/alt/late.py": B_AS_L,
}
HK = "from products.{p}.backend.temporal import hook as _hk  # noqa: E402\n"
LATE = "from products.{p}.backend.temporal.sub.late import L  # noqa: E402\n\nWORKFLOWS = [AFlow, L]\n"


def add_late(p: str, install: str, note: str, outcome: str = "unread", **extra: Any) -> None:
    """An import-system row: `install` runs in the facade before it imports `sub.late`; `files`
    adds or replaces files (an `ev` the facade imports, the package's own `__init__.py`)."""
    files = {**SUB, **(extra.pop("files", None) or {})}
    alone = extra.pop("alone", outcome == "unread")
    add(p, install + LATE, outcome, note, files=files, alone=alone, **extra)


# 1. `sys` and `builtins` as module values; either module handed on.
for name, (install, note) in {
    "mp_sysmodules": ('import sys\n\nsys.modules["sys"].meta_path.insert(0, _hk.Finder())\n', "sys.modules['sys']"),
    "mp_os_sys": ("import os\n\nos.sys.meta_path.insert(0, _hk.Finder())\n", "os.sys"),
    "mp_importlib_sys": ("import importlib\n\nimportlib.sys.meta_path.insert(0, _hk.Finder())\n", "importlib.sys"),
    "mp_pkgutil_sys": ("import pkgutil\n\npkgutil.sys.meta_path.insert(0, _hk.Finder())\n", "pkgutil.sys"),
    "mp_os_sys_fn": (
        "import os\n\n\ndef _p():\n    os.sys.meta_path.insert(0, _hk.Finder())\n\n\n_p()\n",
        "os.sys in a function",
    ),
    "ph_sysmodules": (
        'import sys\n\nsys.modules["sys"].path_hooks.insert(0, _hk.hook)\n',
        "a path hook, sys.modules['sys']",
    ),
    "ph_os_sys": ("import os\n\nos.sys.path_hooks.insert(0, _hk.hook)\n", "a path hook, os.sys"),
    "pic_sysmodules": (
        'import sys\n\nsys.modules["sys"].path_importer_cache[_hk.SUBDIR] = _hk.EntryFinder(_hk.SUBDIR)\n',
        "the importer cache, sys.modules['sys']",
    ),
    "pic_os_sys": (
        "import os\n\nos.sys.path_importer_cache[_hk.SUBDIR] = _hk.EntryFinder(_hk.SUBDIR)\n",
        "the importer cache, os.sys",
    ),
    "imp_sysmodules": (
        'import sys\n\n_b = sys.modules["builtins"]\n_b.__import__ = _hk.fake_import(_b.__import__)\n',
        "builtins as sys.modules['builtins'], through a name",
    ),
    "imp_import_module": (
        'import importlib\n\n_b = importlib.import_module("builtins")\n_b.__import__ = _hk.fake_import(_b.__import__)\n',
        "builtins as import_module('builtins')",
    ),
    "imp_dunder_import": (
        '_b = __import__("builtins")\n_b.__import__ = _hk.fake_import(_b.__import__)\n',
        "builtins as __import__('builtins')",
    ),
    "imp_sysmodules_setattr": (
        'import sys\n\nsetattr(sys.modules["builtins"], "__import__", _hk.fake_import(__import__))\n',
        "builtins as sys.modules['builtins'], by setattr",
    ),
    "imp_sysmodules_inline": (
        'import sys\n\nsys.modules["builtins"].__import__ = _hk.fake_import(__import__)\n',
        "builtins as sys.modules['builtins'], inline",
    ),
    "sys_handed_attrgetter": (
        'import operator\nimport sys\n\noperator.attrgetter("meta_path")(sys).insert(0, _hk.Finder())\n',
        "the module sys handed to a call that returns sys.meta_path",
    ),
    "sys_in_display": (
        "import sys\n\n_mods = [sys]\n_mods[0].meta_path.insert(0, _hk.Finder())\n",
        "the module sys put in a list",
    ),
    "hook_any_receiver": (
        "import sys\n\n\ndef _sys():\n    return sys\n\n\n_sys().meta_path.insert(0, _hk.Finder())\n",
        "meta_path read from a call's result",
    ),
}.items():
    add_late(f"r11_{name}", HK + install, f"{note}: an import hook installed")

add_late(
    "r11_ok_sys_module_value_read",
    "import importlib\nimport os\nimport sys\n\nimport products.{p}.backend.temporal.ev  # noqa: F401\n\n"
    '_v = sys.modules["sys"].version\n_a = os.sys.argv\n_p = importlib.sys.platform\n',
    "harmless: sys as a module value read by literal names; another file writes argv",
    outcome="watched",
    files={
        "backend/temporal/ev.py": "import os\nimport sys\nfrom unittest import mock\n\n"
        'mock.patch.object(os.sys, "argv", ["x"]).start()\nsetattr(sys, "dont_write_bytecode", True)\n'
    },
)
add_late(
    "r11_ok_builtins_module_value_read",
    'import sys\n\n_l = sys.modules["builtins"].len\n_n = __import__("builtins").print\n',
    "harmless: builtins as a module value read by literal names",
    outcome="watched",
)

# 2. A package's `__path__` by the spellings the page names, the package bound by a from-import.
FROM_SUB = "from products.{p}.backend.temporal import sub as _s  # noqa: E402\n"
for name, (install, note) in {
    "pp_setattr": ('setattr(_s, "__path__", [_hk.ALT])\n', "setattr"),
    "pp_getattr": ('getattr(_s, "__path__").insert(0, _hk.ALT)\n', "getattr"),
    "pp_vars": ('vars(_s)["__path__"].insert(0, _hk.ALT)\n', "vars()"),
    "pp_dict": ('_s.__dict__["__path__"].insert(0, _hk.ALT)\n', "__dict__"),
}.items():
    add_late(f"r11_{name}", FROM_SUB + HK + "\n" + install, f"a package's __path__ changed through {note}")
add_late(
    "r11_pp_own_init_globals",
    "",
    "globals()['__path__'] in the package's own __init__.py",
    files={
        "backend/temporal/sub/__init__.py": 'import os\n\nglobals()["__path__"].insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "alt"))\n'
    },
)
add_late(
    "r11_ok_path_spellings_read",
    FROM_SUB + '\n_d = getattr(_s, "__path__")[0]\n_e = "x" in vars(_s)["__path__"]\n',
    "harmless: a package's __path__ read by getattr and vars()",
    outcome="watched",
)

# 3. and 4. Shared import machinery (ruling A).
SUB["backend/temporal/sub/other.py"] = ""
for name, (install, note) in {
    "pic_pathfinder_cache": (
        "import importlib.machinery\n\n_f = importlib.machinery.PathFinder._path_importer_cache(_hk.SUBDIR)\n"
        "_f.find_spec = _hk.entry_find_spec\n",
        "a finder from PathFinder._path_importer_cache changed",
    ),
    "pic_cache_index": (
        "import sys\n\nimport products.{p}.backend.temporal.sub.other  # noqa: E402,F401\n\n"
        "sys.path_importer_cache[_hk.SUBDIR].find_spec = _hk.entry_find_spec\n",
        "a finder changed through direct cache indexing",
    ),
    "ss_source_suffixes": (
        'import importlib.machinery\n\nimportlib.machinery.SOURCE_SUFFIXES.insert(0, ".txt")\n',
        "SOURCE_SUFFIXES changed",
    ),
    "ss_source_suffixes_alias": (
        'from importlib.machinery import SOURCE_SUFFIXES as _ss\n\n_ss.insert(0, ".txt")\n',
        "SOURCE_SUFFIXES changed through an import alias",
    ),
    "ss_slice_store": (
        'import importlib.machinery\n\nimportlib.machinery.SOURCE_SUFFIXES[:0] = [".txt"]\n',
        "SOURCE_SUFFIXES changed by a slice store",
    ),
    "ss_loader_get_data": (
        "import importlib.machinery\n\n_orig = importlib.machinery.SourceFileLoader.get_data\n\n\n"
        "def _gd(self, path):\n"
        '    if path.endswith("late.py"):\n'
        '        return b"from products.{p}.backend.temporal.b import BFlow as L\\n"\n'
        "    return _orig(self, path)\n\n\n"
        "importlib.machinery.SourceFileLoader.get_data = _gd\n",
        "a loader class's method replaced",
    ),
    "ss_loader_patch": (
        "from unittest import mock\n\n\n"
        "def _gd(self, path):\n"
        '    if path.endswith("late.py"):\n'
        '        return b"from products.{p}.backend.temporal.b import BFlow as L\\n"\n'
        '    with open(path, "rb") as f:\n'
        "        return f.read()\n\n\n"
        'mock.patch("importlib.machinery.SourceFileLoader.get_data", _gd).start()\n',
        "a loader class's method patched by its string target",
    ),
}.items():
    add_late(f"r11_{name}", HK + install, note)
EV_SS = 'import importlib.machinery\n\nimportlib.machinery.SOURCE_SUFFIXES.insert(0, ".txt")\n'
add_late(
    "r11_ss_other_file",
    "import products.{p}.backend.temporal.ev  # noqa: F401\n",
    "SOURCE_SUFFIXES changed in another file (ruling A takes precedence over limit 9)",
    files={"backend/temporal/ev.py": EV_SS},
)
add_late(
    "r11_ss_other_file_function",
    "from products.{p}.backend.temporal import ev as _ev  # noqa: E402\n\n_ev.install()\n",
    "SOURCE_SUFFIXES changed in another file's function",
    files={
        "backend/temporal/ev.py": 'def install():\n    import importlib.machinery\n\n    importlib.machinery.SOURCE_SUFFIXES.insert(0, ".txt")\n'
    },
)
REWRITE = (
    "import os\n\n"
    'with open(os.path.join(_hk.SUBDIR, "late.py"), "w") as _fh:\n'
    '    _fh.write("from products.{p}.backend.temporal.b import BFlow as L\\n")\n'
)
for name, (install, note) in {
    "ic_from_alias": ("from importlib import invalidate_caches as _ic\n\n_ic()\n", "an import alias"),
    "ic_dunder": ('__import__("importlib").invalidate_caches()\n', "__import__('importlib')"),
    "ic_import_module": (
        'import importlib\n\nimportlib.import_module("importlib").invalidate_caches()\n',
        "import_module('importlib')",
    ),
    "ic_getattr": ('import importlib\n\ngetattr(importlib, "invalidate_caches")()\n', "getattr"),
    "ic_value": ("import importlib\n\n_ic = importlib.invalidate_caches\n_ic()\n", "a bound name"),
}.items():
    add_late(f"r11_{name}", HK + REWRITE + install, f"importlib.invalidate_caches through {note}", witness=False)
add_late(
    "r11_ok_live_spec_loop",
    "from products.{p}.backend.temporal import lookup as _lookup  # noqa: E402\n\n"
    '_found = _lookup.has_submodule("products.{p}.backend.temporal", "sub.late")\n',
    "harmless: the live spec loop (posthog/products.py) looks specs up with PathFinder.find_spec",
    outcome="watched",
    files={
        "backend/temporal/lookup.py": "import importlib.util\nfrom importlib.machinery import PathFinder\n\n\n"
        "def has_submodule(app_name, submodule):\n"
        "    app_spec = importlib.util.find_spec(app_name)\n"
        "    locations = app_spec.submodule_search_locations if app_spec else None\n"
        "    name = app_name\n"
        '    for part in submodule.split("."):\n'
        "        if locations is None:\n            return False\n"
        '        name = f"{name}.{part}"\n'
        "        spec = PathFinder.find_spec(name, locations)\n"
        "        if spec is None:\n            return False\n"
        "        locations = spec.submodule_search_locations\n"
        "    return True\n"
    },
)
add_late(
    "r11_pathfinder_other_locations",
    HK + "from importlib.machinery import PathFinder\n\n"
    '_spec = PathFinder.find_spec("products.{p}.backend.temporal.sub.late", [_hk.ALT])\n',
    "PathFinder.find_spec over locations that are no package's search locations (ruling A: its own proof)",
    witness=False,
)
add_late(
    "r11_ok_suffixes_read",
    'import importlib.machinery\n\n_x = ".py" in importlib.machinery.SOURCE_SUFFIXES\n',
    "harmless: a read of SOURCE_SUFFIXES",
    outcome="watched",
)

# 5. `pkgutil.iter_modules`: conditions 1 and 5 across files and scopes.
IM_HEAD = "import pkgutil\n" + FROM_SUB + HK
for name, (install, note) in {
    "iter_callee_patch": (
        "from unittest import mock\n\n"
        'mock.patch("pkgutil.iter_modules", _hk.iter_modules).start()\n'
        "for _mi in pkgutil.iter_modules(_s.__path__):\n    _x = _mi.name\n",
        "the callee patched by its string target",
    ),
    "iter_global_fn": (
        "\n\ndef _p():\n    global _mi\n    for _mi in pkgutil.iter_modules(_s.__path__):\n        pass\n\n\n"
        "_p()\n_mi.module_finder.find_spec = _hk.entry_find_spec\n",
        "the loop target declared global in a function, its finder changed at module level",
    ),
    "iter_finder_global_fn": (
        "\n\ndef _p():\n    global _mi\n    for _mi in pkgutil.iter_modules(_s.__path__):\n        pass\n\n\n"
        "_p()\n_f = _mi.module_finder\n_f.find_spec = _hk.entry_find_spec\n",
        "the loop target declared global, its finder bound to a name and changed",
    ),
}.items():
    add_late(f"r11_{name}", IM_HEAD + install, f"iter_modules: {note}")
EV_IM = (
    "import pkgutil\nimport sys\n\nfrom products.{p}.backend.temporal import hook as _hk\n"
    "from products.{p}.backend.temporal import sub as _s\n\n"
    "for _mi in pkgutil.iter_modules(_s.__path__):\n    _x = _mi.name\n"
)
add_late(
    "r11_iter_sysmodules_item",
    "import products.{p}.backend.temporal.ev  # noqa: F401\n",
    "iter_modules: the loop target's finder changed through sys.modules[__name__]",
    files={
        "backend/temporal/ev.py": EV_IM + "sys.modules[__name__]._mi.module_finder.find_spec = _hk.entry_find_spec\n"
    },
)
add_late(
    "r11_iter_other_file_item",
    "from products.{p}.backend.temporal import ev as _ev  # noqa: E402\n" + HK + "\n"
    "_ev._mi.module_finder.find_spec = _hk.entry_find_spec\n",
    "iter_modules: another file changes the loop target's finder",
    files={"backend/temporal/ev.py": EV_IM},
)
add_late(
    "r11_iter_callee_other_file",
    "import pkgutil\n" + FROM_SUB + "import products.{p}.backend.temporal.ev  # noqa: E402,F401\n\n"
    "for _mi in pkgutil.iter_modules(_s.__path__):\n    _x = _mi.name\n",
    "iter_modules replaced in another file (ruling A takes precedence over limit 9)",
    files={
        "backend/temporal/ev.py": "import pkgutil\n\nfrom products.{p}.backend.temporal import hook as _hk\n\npkgutil.iter_modules = _hk.iter_modules\n"
    },
)
add_late(
    "r11_ok_iter_unrelated_patch",
    "import os\nfrom unittest import mock\n\n" + IM_HEAD + '\nmock.patch("os.getcwd", os.getcwd).start()\n'
    "for _mi in pkgutil.iter_modules(_s.__path__):\n    _x = _mi.name\n",
    "harmless: iter_modules read by name next to a patch of an unrelated target",
    outcome="watched",
)

# 6. `sys.modules` spellings and a key another file rebinds. ev publishes `_fake` (BFlow
# registered) under the facade's name after the facade binds WORKFLOWS.
FAKE = (
    "import sys\nimport types\n\nfrom products.{p}.backend.temporal.a import AFlow\n"
    "from products.{p}.backend.temporal.b import BFlow\n\n"
    '_fake = types.ModuleType("x")\n_fake.WORKFLOWS = [AFlow, BFlow]\n'
)
TARGET = '"products.{p}.backend.facade.temporal"'
EV_LAST = "\n\nimport products.{p}.backend.temporal.ev  # noqa: E402,F401\n"


def add_km(p: str, ev: str, note: str, outcome: str = "unread", **files: str) -> None:
    laid = {
        "backend/temporal/ev.py": FAKE + ev,
        **{path.replace("__", "/") + ".py": text for path, text in files.items()},
    }
    add(p, "WORKFLOWS = [AFlow]\n" + EV_LAST, outcome, note, files=laid, alone=outcome == "unread")


add_km("r11_km_vars_sys", f'vars(sys)["modules"][{TARGET}] = _fake\n', "vars(sys)['modules']")
add_km("r11_km_dict_sys", f'sys.__dict__["modules"][{TARGET}] = _fake\n', "sys.__dict__['modules']")
add_km(
    "r11_km_attrgetter",
    f'import operator\n\noperator.attrgetter("modules")(sys)[{TARGET}] = _fake\n',
    "sys handed to operator.attrgetter",
)
add_km(
    "r11_km_reduce_getattr",
    f'import functools\n\nfunctools.reduce(getattr, ["modules"], sys)[{TARGET}] = _fake\n',
    "sys handed to functools.reduce",
)
add_km("r11_km_os_sys_update", f"import os\n\nos.sys.modules.update({{{TARGET}: _fake}})\n", "os.sys.modules.update")
add_km(
    "r11_km_other_file_store",
    '_K = "_harmless"\nimport products.{p}.backend.temporal.kset  # noqa: E402,F401\nsys.modules[_K] = _fake\n',
    "a key name another file stores into",
    backend__temporal__kset=f"import products.{{p}}.backend.temporal.ev as _e\n\n_e._K = {TARGET}\n",
)
add_km(
    "r11_ok_km_other_file_reads",
    '_K = "_harmless"\nimport products.{p}.backend.temporal.kset  # noqa: E402,F401\nsys.modules[_K] = _fake\n',
    "harmless: another file reads the key name",
    outcome="watched",
    backend__temporal__kset="import products.{p}.backend.temporal.ev as _e\n\n_x = _e._K\n",
)

# 8. A trusted builtin replaced through `builtins` (ruling C).
STATIC = "\n\nclass _K:\n    @staticmethod\n    def L():\n        pass\n\n\nWORKFLOWS = [AFlow, _K.L]\n"
add(
    "r11_builtin_staticmethod_same_file",
    "import builtins\n\nbuiltins.staticmethod = lambda f: BFlow\n" + STATIC,
    "unread",
    "builtins.staticmethod replaced in the reading file",
)
add(
    "r11_builtin_staticmethod_other_file",
    "import products.{p}.backend.temporal.ev  # noqa: F401\n" + STATIC,
    "unread",
    "builtins.staticmethod replaced in another file (ruling C overrides limit 9)",
    files={
        "backend/temporal/ev.py": "import builtins\n\nfrom products.{p}.backend.temporal.b import BFlow\n\nbuiltins.staticmethod = lambda f: BFlow\n"
    },
)
add(
    "r11_builtin_staticmethod_patch",
    'from unittest import mock\n\nmock.patch("builtins.staticmethod", lambda f: BFlow).start()\n' + STATIC,
    "unread",
    "builtins.staticmethod patched by its string target",
)
add(
    "r11_builtin_staticmethod_patch_object",
    'import builtins\nfrom unittest import mock\n\nmock.patch.object(builtins, "staticmethod", lambda f: BFlow).start()\n'
    + STATIC,
    "unread",
    "builtins.staticmethod patched by patch.object",
)
add(
    "r11_builtin_staticmethod_sysmodules",
    'import sys\n\nsys.modules["builtins"].staticmethod = lambda f: BFlow\n' + STATIC,
    "unread",
    "builtins.staticmethod replaced through sys.modules['builtins']",
)
add(
    "r11_ok_builtin_input_patch",
    'from unittest import mock\n\nmock.patch("builtins.input", lambda *a: "").start()\n' + STATIC,
    "watched",
    "harmless: a patch of builtins.input, which no proof relies on",
)

# 9. T12: another field's items, and a copy of the list that gains a value.
HIT = "        if not any(x is BFlow for x in WORKFLOWS):\n            WORKFLOWS.append(BFlow)\n"
HOOKED = (
    "\n\nclass _H:\n    def __repr__(self):\n"
    + HIT
    + "        return 'H'\n\n    def __format__(self, spec):\n"
    + HIT
    + "        return 'H'\n\n\n"
)
for name, use in {
    "field_list": "_Y = [_H()]\n_X = f'{WORKFLOWS}{_Y}'\n",
    "field_list_pct": "_Y = [_H()]\n_X = '%s%s' % (WORKFLOWS, _Y)\n",
    "field_list_format": "_Y = [_H()]\n_X = '{}{}'.format(WORKFLOWS, _Y)\n",
    "field_tuple": "_Y = (_H(),)\n_X = f'{WORKFLOWS}{_Y}'\n",
    "field_dict": "_Y = {1: _H()}\n_X = f'{WORKFLOWS}{_Y}'\n",
    "field_list_changed": "_Y = [1]\n_Y.append(_H())\n_X = f'{WORKFLOWS}{_Y}'\n",
    "field_list_alias": "_Y = [1]\n_Z = _Y\n_Z.append(_H())\n_X = f'{WORKFLOWS}{_Y}'\n",
    "field_nested_list": "_Y = [[_H()]]\n_X = f'{WORKFLOWS}{_Y}'\n",
    "copy_then_append": "_Z = WORKFLOWS[:]\n_Z.append(_H())\n_X = f'{_Z}'\n",
    "copy_concat": "_Z = WORKFLOWS + [_H()]\n_X = f'{_Z}'\n",
    "copy_star": "_Z = [*WORKFLOWS, _H()]\n_X = f'{_Z}'\n",
}.items():
    add(
        f"r11_t12_{name}",
        HOOKED + "WORKFLOWS = [AFlow]\n" + use,
        "unread",
        f"T12: {name.replace('_', ' ')} runs a hook",
    )
for name, use in {
    "field_list": "_Y = [1, 'x']\n_X = f'{WORKFLOWS}{_Y}'\n",
    "copy_then_append": "_Z = WORKFLOWS[:]\n_Z.append(1)\n_X = f'{_Z}'\n",
}.items():
    add(
        f"r11_ok_t12_{name}",
        "WORKFLOWS = [AFlow]\n" + use,
        "watched",
        f"harmless: T12 {name.replace('_', ' ')} of inert values",
    )

# 10. The item and operand proofs against a `global` rebinding and another file's store.
GLOBAL_REBOUND = "\n\nclass _X0:\n    pass\n\n\ndef _p():\n    global _X0\n    _X0 = _H()\n\n\n_p()\n"
HOOKED_EQ = HOOKED.replace(
    "    def __format__",
    "    def __eq__(self, other):\n"
    + HIT
    + "        return False\n\n    __hash__ = object.__hash__\n\n    def __format__",
)
add(
    "r11_operand_global_rebound",
    HOOKED_EQ
    + "WORKFLOWS = [AFlow]\n"
    + GLOBAL_REBOUND
    + "try:\n    _X = WORKFLOWS.index(_X0)\nexcept ValueError:\n    pass\n",
    "unread",
    "an operand class a function rebinds by global",
)
HK_OTHER = {
    "backend/temporal/hk.py": "from products.{p}.backend.temporal.b import BFlow\n\n\nclass _H:\n"
    "    def _hit(self):\n        import products.{p}.backend.facade.temporal as f\n\n"
    "        if not any(x is BFlow for x in f.WORKFLOWS):\n            f.WORKFLOWS.append(BFlow)\n\n"
    "    def __eq__(self, o):\n        self._hit()\n        return False\n\n    __hash__ = object.__hash__\n\n"
    "    def __repr__(self):\n        self._hit()\n        return 'H'\n\n"
    "    def __format__(self, spec):\n        self._hit()\n        return 'H'\n\n\nHI = _H()\n",
    "backend/temporal/hk2.py": "class HI:\n    pass\n",
    "backend/temporal/ev.py": "from products.{p}.backend.temporal import hk2 as _h2\nfrom products.{p}.backend.temporal.hk import HI\n\n_h2.HI = HI\n",
}
H2 = "from products.{p}.backend.temporal import hk2 as _h2  # noqa: E402\nimport products.{p}.backend.temporal.ev  # noqa: E402,F401\n\n"
for name, use in {
    "operand_other_file_index": "WORKFLOWS = [AFlow]\ntry:\n    _X = WORKFLOWS.index(_h2.HI)\nexcept ValueError:\n    pass\n",
    "operand_other_file_count": "WORKFLOWS = [AFlow]\n_X = WORKFLOWS.count(_h2.HI)\n",
    "operand_other_file_remove": "WORKFLOWS = [AFlow]\ntry:\n    WORKFLOWS.remove(_h2.HI)\nexcept ValueError:\n    pass\n",
    "item_other_file_fmt": "WORKFLOWS = [AFlow, _h2.HI]\n_X = f'{WORKFLOWS}'\n",
    "item_other_file_in": "WORKFLOWS = [AFlow, _h2.HI]\n_X = 1 in WORKFLOWS\n",
}.items():
    add(
        f"r11_{name}",
        H2 + use,
        "unread",
        f"{name.replace('_', ' ')}: another file stores into the relied name",
        files=HK_OTHER,
    )
add(
    "r11_ok_operand_other_name_stored",
    H2 + "WORKFLOWS = [AFlow]\n_X = WORKFLOWS.count(_h2.HI)\n",
    "watched",
    "harmless: another file stores into another name of the operand's module",
    files={
        **HK_OTHER,
        "backend/temporal/ev.py": HK_OTHER["backend/temporal/ev.py"].replace("_h2.HI = HI", "_h2.OTHER = HI"),
    },
)
add(
    "r11_ok_operand_global_other_name",
    "WORKFLOWS = [AFlow]\n\n\nclass _X0:\n    pass\n\n\ndef _p():\n    global _Y0\n    _Y0 = 1\n\n\n_p()\n_X = WORKFLOWS.count(_X0)\n",
    "watched",
    "harmless: a function rebinds another name by global",
)

# 11. A class body's `global` alias.
add(
    "r11_class_body_global_alias",
    "WORKFLOWS = [AFlow]\n\n\nclass _U:\n    global _X\n    _X = WORKFLOWS\n\n\n_X.append(BFlow)\n",
    "unread",
    "a class body's global alias, changed at module level",
)
add(
    "r11_ok_class_body_global_other",
    "WORKFLOWS = [AFlow]\n\n\nclass _U:\n    global _X\n    _X = [1]\n\n\n_X.append(2)\n",
    "watched",
    "harmless: a class body's global binding of another value",
)

# The audit: implicit calls of T7 and T9 entries (an unpacked item's `__iter__`, an item's
# attribute read through a metaclass `__getattribute__`).
ITER_ITEM = "\n\nclass _I:\n    def __iter__(self):\n" + HIT + "        return iter((1,))\n\n\n"
META_ITEM = (
    "\n\nclass _MH(type):\n    def __iter__(cls):\n" + HIT + "        return iter((1,))\n\n"
    "    def __getattribute__(cls, n):\n        if n == '__name__':\n"
    + HIT.replace("        ", "            ")
    + "        return type.__getattribute__(cls, n)\n\n\nclass _HK(metaclass=_MH):\n    pass\n\n\n"
)
for name, use in {
    "inst_unpack_for": ITER_ITEM
    + "WORKFLOWS = [_I(), AFlow]\ntry:\n    for (_a, *_r) in WORKFLOWS:\n        pass\nexcept TypeError:\n    pass\n",
    "meta_unpack_for": META_ITEM
    + "WORKFLOWS = [_HK, AFlow]\ntry:\n    for (_a, *_r) in WORKFLOWS:\n        pass\nexcept TypeError:\n    pass\n",
    "meta_item_name": META_ITEM + "WORKFLOWS = [_HK, AFlow]\n_X = WORKFLOWS[0].__name__\n",
    "meta_item_name_comp": META_ITEM + "WORKFLOWS = [_HK, AFlow]\n_X = [w.__name__ for w in WORKFLOWS]\n",
}.items():
    add(f"r11_audit_{name}", use, "unread", f"the audit: {name.replace('_', ' ')} runs an item's hook")
for name, use in {
    "item_name": "WORKFLOWS = [AFlow]\n_X = WORKFLOWS[0].__name__\n",
    "item_name_comp": "WORKFLOWS = [AFlow]\n_X = [w.__name__ for w in WORKFLOWS]\n",
    "unpack_for": "WORKFLOWS = [(1, 2), AFlow]\ntry:\n    for (_a, *_r) in WORKFLOWS:\n        pass\nexcept TypeError:\n    pass\n",
}.items():
    add(f"r11_ok_audit_{name}", use, "watched", f"harmless: {name.replace('_', ' ')} of inert items")

# Over-fails of round 10 the page counts as reads: `sys.meta_path` and `sys.path_hooks` read by a
# statement, a test, iteration, `in`, `is` or an item by a literal index.
for name, use in {
    "mp_stmt": "sys.meta_path  # noqa: B018\n",
    "mp_test": "if sys.meta_path:\n    pass\n",
    "mp_in": "_x = 1 in sys.meta_path\n",
    "mp_iter": "for _f in sys.meta_path:\n    pass\n",
    "mp_item0": "_x = sys.meta_path[0]\n",
    "mp_is": "_x = sys.meta_path is None\n",
    "ph_iter": "for _h in sys.path_hooks:\n    pass\n",
}.items():
    add(f"r11_ok_read_{name}", "import sys\n\n" + use, "watched", f"harmless: a read of an import hook ({name})")

# Limit 9's round 9b sentence widened to `sys` and `builtins` (droppable, the owner's call): handed
# on in a function body, the module is not followed, so a change made through the value it hands
# out is not seen. Without the limit this row is unread, and live's two function-body handoffs
# (`dir(builtins)`, `getattr(builtins, NAME)`) fail every claim.
add_late(
    "r11_limit9_builtins_returned",
    "import products.{p}.backend.temporal.h  # noqa: F401\n",
    "limit 9: builtins returned from a function, its __import__ replaced through the call's result",
    outcome="kept",
    files={
        "backend/temporal/ev.py": "import builtins\n\n\ndef b():\n    return builtins\n",
        "backend/temporal/h.py": "from products.{p}.backend.temporal import hook as _hk\n"
        "from products.{p}.backend.temporal.ev import b\n\nb().__import__ = _hk.fake_import(__import__)\n",
    },
)


# Item 13 (t30, Nov 24): a registration point reads enum members of a core module that looks a
# library module up by `sys.modules.get("<literal>")` in a function (PostHog's `posthog/errors.py`
# and `posthog/hogql_queries/query_runner.py`). The lookup names one library module and reaches no
# name of the file, so the members stay read and the answer complete.
ERRORS = (
    "import sys\nfrom enum import StrEnum\n\n\nclass Cat(StrEnum):\n    USER = 'user'\n    OTHER = 'other'\n\n\n"
    "def rebuild(err):\n    exceptions = sys.modules.get('clickhouse_connect.driver.exceptions')\n"
    "    if exceptions is None or not isinstance(err, (exceptions.DatabaseError, exceptions.StreamFailureError)):\n"
    "        return None\n    return err\n"
)
_P = "r11_ok_enum_member_registration"
CASES[_P] = {
    **case(
        _P,
        "",
        "watched",
        "harmless: a registration point reads enum members of a module that looks a library up in sys.modules",
    ),
    "core": {
        "posthog/management/commands/start_temporal_worker.py": (
            f"\nfrom products.{_P}.backend.facade.temporal import WORKFLOWS as {_P.upper()}_WORKFLOWS\n\n"
            f'{_P.upper()}_QUEUES = [("{_P}", {_P.upper()}_WORKFLOWS)]\n'
            "\nfrom posthog.r11_errors import Cat\n\n\ndef _slo(e):\n    if e in (Cat.USER, Cat.OTHER):\n"
            "        return Cat.USER, 1\n    return Cat.OTHER, 2\n"
        ),
        "posthog/r11_errors.py": ERRORS,
    },
}
