"""Round 6's systematic cases (CASES.md `WA17`), as data for `test_watched_shapes.py`: one case per
binding form of the Language Reference ("Binding of names"), one per reader argument position, each
with the registered list itself and with a holder of it, and D126's records in a registration
point. Each case has a product of its own (a registration point of its own for the records), its
expected outcome, and the reason the answer must name: `watched` (each probe file is watched and
the product passes), `unread` (the product cannot pass, for the reason named), `problem` (the
answer is incomplete, for the reason named), or `kept` (the product passes, as `why` says)."""

from __future__ import annotations

from typing import Any

FLOWS = "class {name}:\n    def run(self, x):\n        return x\n"
PRESETS = "ee/hogai/core/agent_modes/presets"
PACKAGES = {
    "ee/hogai/core/__init__.py": "# core\n",
    "ee/hogai/core/agent_modes/__init__.py": "# agent modes\n",
    f"{PRESETS}/__init__.py": "# presets\n",
}


def facade_case(p: str, body: str, outcome: str, note: str, head: str = "", more: str = "") -> dict[str, Any]:
    """A product facade that binds WORKFLOWS (`body` holds the binding and the shape; `{p}` is the
    product); the worker registers it. AFlow is registered, BFlow only by the shape."""
    facade = (
        head
        + f"from products.{p}.backend.temporal.a import AFlow\n"
        + f"from products.{p}.backend.temporal.b import BFlow\n\n"
        + body.replace("{p}", p)
    )
    files = {
        "backend/temporal/__init__.py": "",
        "backend/temporal/a.py": FLOWS.format(name="AFlow"),
        "backend/temporal/b.py": FLOWS.format(name="BFlow"),
        "backend/facade/temporal.py": facade,
    }
    if more:
        files["backend/temporal/more.py"] = more.replace("{p}", p)
    return {
        "files": files,
        "probes": ["backend/temporal/a.py"],
        "outcome": outcome,
        "reason": f"products.{p}.backend.facade.temporal.WORKFLOWS",
        "note": note,
    }


# Each binding form: (name, how it binds WORKFLOWS (the holder `_H` too), the outcome for WORKFLOWS a
# tuple, the outcome through the holder). A form that writes no assignment row binds the name to
# something the reading cannot read: unread, the holder's W too. An assignment the reading reads
# binds the tuple again (unread: two bindings) and the holder only drops it (watched). An import
# binds the name to what it imports, which the link follows (`imported`: the imported module's
# file is watched instead); an import of a module makes the name a module, so the worker fails at
# start (`harmless`, kept).
BINDING_FORMS: list[tuple[str, str, str, str]] = [
    ("assign", "WORKFLOWS = (AFlow, BFlow)\n", "unread", "watched"),
    ("annassign", "WORKFLOWS: tuple = (AFlow, BFlow)\n", "unread", "watched"),
    ("augassign", "WORKFLOWS += (BFlow,)\n", "watched", "watched"),
    ("unpack", "(WORKFLOWS,) = ((AFlow, BFlow),)\n", "unread", "watched"),
    ("star_target", "*WORKFLOWS, = (AFlow, BFlow)\n", "unread", "watched"),
    ("for_target", "for WORKFLOWS in [(AFlow, BFlow)]:\n    pass\n", "unread", "unread"),
    (
        "with_target",
        "import contextlib  # noqa: E402\n\nwith contextlib.nullcontext((AFlow, BFlow)) as WORKFLOWS:\n    pass\n",
        "unread",
        "unread",
    ),
    ("except_as", "try:\n    raise ValueError\nexcept ValueError as WORKFLOWS:\n    pass\n", "unread", "unread"),
    ("except_star_as", "try:\n    raise ValueError\nexcept* ValueError as WORKFLOWS:\n    pass\n", "unread", "unread"),
    ("import_as", "import products.{p}.backend.temporal.more as WORKFLOWS  # noqa: E402\n", "harmless", "unread"),
    (
        "from_import",
        "from products.{p}.backend.temporal.more import OTHER as WORKFLOWS  # noqa: E402\n",
        "imported",
        "unread",
    ),
    ("class", "\n\nclass WORKFLOWS:\n    pass\n", "unread", "unread"),
    ("def", "\n\ndef WORKFLOWS():\n    return ()\n", "unread", "unread"),
    (
        "def_decorated",
        "\n\ndef _call(f):\n    return f()\n\n\n@_call\ndef WORKFLOWS():\n    return (AFlow, BFlow)\n",
        "unread",
        "unread",
    ),
    ("type", "type WORKFLOWS = tuple\n", "unread", "watched"),
    ("walrus", "(WORKFLOWS := (AFlow, BFlow))\n", "unread", "unread"),
    ("match_capture", "match (AFlow, BFlow):\n    case WORKFLOWS:\n        pass\n", "unread", "unread"),
    ("match_as", "match (AFlow, BFlow):\n    case [*_] as WORKFLOWS:\n        pass\n", "unread", "unread"),
    ("match_star", "match [AFlow, BFlow]:\n    case [*WORKFLOWS]:\n        pass\n", "unread", "unread"),
    ("match_rest", 'match {"a": AFlow}:\n    case {**WORKFLOWS}:\n        pass\n', "unread", "unread"),
    ("del", "del WORKFLOWS\nWORKFLOWS = (AFlow, BFlow)\n", "unread", "unread"),
    ("class_global", "\n\nclass _C:\n    global WORKFLOWS\n    WORKFLOWS = (AFlow, BFlow)\n", "unread", "unread"),
    (
        "class_global_for",
        "\n\nclass _C:\n    global WORKFLOWS\n    for WORKFLOWS in [(AFlow, BFlow)]:\n        pass\n",
        "unread",
        "unread",
    ),
    (
        "function_global_for",
        "\n\ndef _f():\n    global WORKFLOWS\n    for WORKFLOWS in [(AFlow, BFlow)]:\n        pass\n\n\n_f()\n",
        "unread",
        "unread",
    ),
    (
        "function_global_assign",
        "\n\ndef _f():\n    global WORKFLOWS\n    WORKFLOWS = (AFlow, BFlow)\n\n\n_f()\n",
        "unread",
        "watched",
    ),
]
MORE = "from products.{p}.backend.temporal.b import BFlow\n\nOTHER = (BFlow,)\n"


def binding_cases() -> dict[str, dict[str, Any]]:
    cases = {}
    for form, binding, direct, through_holder in BINDING_FORMS:
        p = f"r6f_{form}"
        body = "WORKFLOWS = (AFlow,)\n" + binding
        case = facade_case(p, body, direct, f"a tuple bound again by {form}", more=MORE)
        if direct == "imported":
            case["outcome"] = "watched"
            case["probes"] = ["backend/temporal/more.py"]
        if direct == "harmless":
            case["outcome"] = "kept"
            case["why"] = "harmless: the name becomes a module, so the worker fails at start"
        cases[p] = case
        p = f"r6f_{form}_h"
        held = binding.replace("WORKFLOWS", "_H")
        body = "WORKFLOWS = [AFlow]\n_H = [WORKFLOWS]\n" + held
        cases[p] = facade_case(p, body, through_holder, f"a holder of the list bound again by {form}", more=MORE)
    return cases


# Each reader position: (name, the call with `X` for the value, imports, what the result can be).
# `W`: the result may be the list itself (a change through it makes the list unread); `holds`: it
# may hold the list (a change of an element of it makes the list unread); `read`: nothing of it.
READER_POSITIONS: list[tuple[str, str, str, str]] = [
    ("list", "list(X)", "", "copy"),
    ("tuple", "tuple(X)", "", "copy"),
    ("set", "set(X)", "", "copy"),
    ("frozenset", "frozenset(X)", "", "copy"),
    ("sorted", "sorted(X)", "", "copy"),
    ("dict", "dict(X)", "", "copy"),
    ("copy_copy", "copy.copy(X)", "import copy\n", "copy"),
    ("deepcopy", "copy.deepcopy(X)", "import copy\n", "read"),
    ("len", "len(X)", "", "read"),
    ("bool", "bool(X)", "", "read"),
    ("any", "any(X)", "", "read"),
    ("all", "all(X)", "", "read"),
    ("repr", "repr(X)", "", "read"),
    ("str", "str(X)", "", "read"),
    ("id", "id(X)", "", "read"),
    ("hash", "hash(X)", "", "read"),
    ("print", "print(X)", "", "read"),
    ("json_dumps", "json.dumps(X, default=repr)", "import json\n", "read"),
    ("pformat", "pprint.pformat(X)", "import pprint\n", "read"),
    ("counter", "collections.Counter(X)", "import collections\n", "read"),
    ("fromkeys", "dict.fromkeys(X)", "", "read"),
    ("format", '"{}".format(X)', "", "read"),
    ("percent", '"%s" % (X,)', "", "read"),
    ("fstring", 'f"{X}"', "", "read"),
    ("iter", "iter(X)", "", "iterator"),
    ("reversed", "reversed(X)", "", "iterator"),
    ("enumerate", "enumerate(X)", "", "iterator"),
    ("zip", "zip(X)", "", "iterator"),
    ("map", "map(str, X)", "", "iterator"),
    ("filter", "filter(None, X)", "", "iterator"),
    ("chain", "itertools.chain(X)", "import itertools\n", "iterator"),
    ("min", "min(X)", "", "item"),
    ("max", "max(X)", "", "item"),
    ("sum", "sum(X)", "", "item"),
    # `iter` gives a holder and `next` of a holder may be the list: fail closed.
    ("next", "next(iter(X))", "", "may be"),
    ("min_default", "min((), default=X)", "", "may be"),
    ("max_pair", "max(X, [])", "", "may be"),
    ("max_second", "max([], X)", "", "may be"),
    ("sum_start", "sum((), X)", "", "may be"),
    ("sum_start_keyword", "sum((), start=X)", "", "may be"),
    ("next_default", "next(iter(()), X)", "", "may be"),
]
# What a change after the reader gives, by what the result can be: with the list itself (`_X.append`
# changes the result) and with a holder (`_X[0].append` changes an element of the result).
DIRECT = {"copy": "watched", "read": "watched", "iterator": "watched", "item": "watched", "may be": "unread"}
THROUGH_HOLDER = {"copy": "unread", "read": "watched", "iterator": "unread", "item": "unread", "may be": "unread"}


def reader_cases() -> dict[str, dict[str, Any]]:
    cases = {}
    for name, call, imports, result in READER_POSITIONS:
        p = f"r6r_{name}"
        body = "WORKFLOWS = [AFlow]\n_X = " + call.replace("X", "WORKFLOWS") + "\n_X.append(BFlow)\n"
        cases[p] = facade_case(p, body, DIRECT[result], f"{call} of the list ({result})", head=imports)
        p = f"r6r_{name}_h"
        body = "WORKFLOWS = [AFlow]\n_L = [WORKFLOWS]\n_X = " + call.replace("X", "_L") + "\n_X[0].append(BFlow)\n"
        cases[p] = facade_case(p, body, THROUGH_HOLDER[result], f"{call} of a holder ({result})", head=imports)
    return cases


def record_case(p: str, tail: str, outcome: str, note: str) -> dict[str, Any]:
    """D126 in a registration point (the presets shape): a class body binds a core list, a record
    built by a confirmed first-party class holds the class, and a second registration point reads
    the class's list."""
    body = (
        "from posthog.temporal.common.registration import QueueBinding\n"
        f"from products.{p}.backend.facade.temporal import WORKFLOWS as P_WORKFLOWS\n\n\n"
        "class Toolkit:\n    WORKFLOWS = [*P_WORKFLOWS]\n\n\n"
        'rec = QueueBinding("q", workflows=Toolkit)\n' + tail.replace("{p}", p)
    )
    reader = f"from ee.hogai.core.agent_modes.presets.{p} import Toolkit\n\nALL = [*Toolkit.WORKFLOWS]\n"
    return {
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/a.py": FLOWS.format(name="AFlow"),
            "backend/temporal/b.py": FLOWS.format(name="BFlow"),
            "backend/facade/temporal.py": f"from products.{p}.backend.temporal.a import AFlow\n\nWORKFLOWS = [AFlow]\n",
        },
        "core": {**PACKAGES, f"{PRESETS}/{p}.py": body, f"{PRESETS}/{p}_reader.py": reader},
        "probes": ["backend/temporal/a.py"],
        "outcome": outcome,
        "reason": f"presets.{p}.Toolkit.WORKFLOWS",
        "note": note,
    }


RECORDS: dict[str, dict[str, Any]] = {
    "r6d_record": record_case("r6d_record", "", "watched", "rec = Record(workflows=Toolkit), never changed"),
    "r6d_record_change": record_case(
        "r6d_record_change",
        "from products.{p}.backend.temporal.b import BFlow  # noqa: E402\n\nrec.workflows.WORKFLOWS.append(BFlow)\n",
        "problem",
        "a change through rec.workflows.WORKFLOWS",
    ),
    "r6d_record_handed": record_case(
        "r6d_record_handed",
        "\n\ndef _keep(x):\n    return x\n\n\n_keep(rec)\n",
        "problem",
        "the record handed to a call",
    ),
    "r6d_record_inline": record_case(
        "r6d_record_inline",
        '\n\ndef _keep(x):\n    return x\n\n\n_keep(QueueBinding("q", workflows=Toolkit))\n',
        "problem",
        "a record built inline and handed to a call",
    ),
    "r6d_record_rebound": record_case(
        "r6d_record_rebound",
        'rec = QueueBinding("q", workflows=Toolkit)\n',
        "problem",
        "the record bound a second time",
    ),
}

CASES: dict[str, dict[str, Any]] = {**binding_cases(), **reader_cases(), **RECORDS}
