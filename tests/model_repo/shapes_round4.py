"""The adversary's round-4 rows (CASES.md `WA14`): each attacked shape, as data for
`test_watched_shapes.py`, with the answer it must give: `watched` (each probe file is watched and
the product passes), `unread` (the product cannot pass: something the link cannot read), `problem`
(an unread item of a core list a registration point registers, or a module it loads by a name it
computes: the whole answer is incomplete),
or `kept` (the product passes; a written limit or the call rule keeps the file out, as `why`
says). Generated from the round's attack script; edit as data. The rows that load a module sit in a
registration point of their own (`ee/hogai/core/agent_modes/presets/<row>.py`): in the worker file
the computed path of one row made every string of the file a `dotted-path` edge, which other rows
read as their own."""

from __future__ import annotations

from typing import Any

ROUND4: dict[str, dict[str, Any]] = {
    "r4_a_tuple_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/atupleuwf.py": "class ATupleUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_tuple_u.backend.temporal.atupleuwf import ATupleUWf\n\nWORKFLOWS = [ATupleUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_tuple_u.py": "from products.r4_a_tuple_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = tuple(map(_wrap, PW))\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_tuple_u import HOP as R4_A_TUPLE_U_HOP\nQUEUES_R4_A_TUPLE_U = [("r4_a_tuple_u", R4_A_TUPLE_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/atupleuwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = tuple(map(_wrap, PW))'",
    },
    "r4_a_tuple": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/atuplewf.py": "class ATupleWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_tuple.backend.temporal.atuplewf import ATupleWf\n\nWORKFLOWS = [ATupleWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_tuple.py": "from products.r4_a_tuple.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = tuple(PW)\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_tuple import HOP as R4_A_TUPLE_HOP\nQUEUES_R4_A_TUPLE = [("r4_a_tuple", R4_A_TUPLE_HOP, [])]\n',
        },
        "probes": ["backend/temporal/atuplewf.py"],
        "outcome": "watched",
        "note": "core hop: 'HOP = tuple(PW)'",
    },
    "r4_a_sorted_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/asorteduwf.py": "class ASortedUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_sorted_u.backend.temporal.asorteduwf import ASortedUWf\n\nWORKFLOWS = [ASortedUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_sorted_u.py": "from products.r4_a_sorted_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = sorted(map(_wrap, PW), key=id)\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_sorted_u import HOP as R4_A_SORTED_U_HOP\nQUEUES_R4_A_SORTED_U = [("r4_a_sorted_u", R4_A_SORTED_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/asorteduwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = sorted(map(_wrap, PW), key=id)'",
    },
    "r4_a_sorted": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/asortedwf.py": "class ASortedWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_sorted.backend.temporal.asortedwf import ASortedWf\n\nWORKFLOWS = [ASortedWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_sorted.py": "from products.r4_a_sorted.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = sorted(PW, key=id)\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_sorted import HOP as R4_A_SORTED_HOP\nQUEUES_R4_A_SORTED = [("r4_a_sorted", R4_A_SORTED_HOP, [])]\n',
        },
        "probes": ["backend/temporal/asortedwf.py"],
        "outcome": "watched",
        "note": "core hop: 'HOP = sorted(PW, key=id)'",
    },
    "r4_a_set_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/asetuwf.py": "class ASetUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_set_u.backend.temporal.asetuwf import ASetUWf\n\nWORKFLOWS = [ASetUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_set_u.py": "from products.r4_a_set_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = set(map(_wrap, PW))\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_set_u import HOP as R4_A_SET_U_HOP\nQUEUES_R4_A_SET_U = [("r4_a_set_u", R4_A_SET_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/asetuwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = set(map(_wrap, PW))'",
    },
    "r4_a_set": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/asetwf.py": "class ASetWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_set.backend.temporal.asetwf import ASetWf\n\nWORKFLOWS = [ASetWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_set.py": "from products.r4_a_set.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = set(PW)\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_set import HOP as R4_A_SET_HOP\nQUEUES_R4_A_SET = [("r4_a_set", R4_A_SET_HOP, [])]\n',
        },
        "probes": ["backend/temporal/asetwf.py"],
        "outcome": "watched",
        "note": "core hop: 'HOP = set(PW)'",
    },
    "r4_a_frozenset_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/afrozensetuwf.py": "class AFrozensetUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_frozenset_u.backend.temporal.afrozensetuwf import AFrozensetUWf\n\nWORKFLOWS = [AFrozensetUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_frozenset_u.py": "from products.r4_a_frozenset_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = frozenset(map(_wrap, PW))\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_frozenset_u import HOP as R4_A_FROZENSET_U_HOP\nQUEUES_R4_A_FROZENSET_U = [("r4_a_frozenset_u", R4_A_FROZENSET_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/afrozensetuwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = frozenset(map(_wrap, PW))'",
    },
    "r4_a_starlist_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/astarlistuwf.py": "class AStarlistUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_starlist_u.backend.temporal.astarlistuwf import AStarlistUWf\n\nWORKFLOWS = [AStarlistUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_starlist_u.py": "from products.r4_a_starlist_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = [*map(_wrap, PW)]\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_starlist_u import HOP as R4_A_STARLIST_U_HOP\nQUEUES_R4_A_STARLIST_U = [("r4_a_starlist_u", R4_A_STARLIST_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/astarlistuwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = [*map(_wrap, PW)]'",
    },
    "r4_a_cond_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/aconduwf.py": "class ACondUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_cond_u.backend.temporal.aconduwf import ACondUWf\n\nWORKFLOWS = [ACondUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_cond_u.py": "from products.r4_a_cond_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\n_FLAG = True\nHOP = list(map(_wrap, PW)) if _FLAG else []\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_cond_u import HOP as R4_A_COND_U_HOP\nQUEUES_R4_A_COND_U = [("r4_a_cond_u", R4_A_COND_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/aconduwf.py"],
        "outcome": "problem",
        "note": "core hop: '_FLAG = True\\nHOP = list(map(_wrap, PW)) if _FLAG else []'",
    },
    "r4_a_cond": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/acondwf.py": "class ACondWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_cond.backend.temporal.acondwf import ACondWf\n\nWORKFLOWS = [ACondWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_cond.py": "from products.r4_a_cond.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\n_FLAG = True\nHOP = PW if _FLAG else []\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_cond import HOP as R4_A_COND_HOP\nQUEUES_R4_A_COND = [("r4_a_cond", R4_A_COND_HOP, [])]\n',
        },
        "probes": ["backend/temporal/acondwf.py"],
        "outcome": "watched",
        "note": "core hop: '_FLAG = True\\nHOP = PW if _FLAG else []'",
    },
    "r4_a_walrus_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/awalrusuwf.py": "class AWalrusUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_walrus_u.backend.temporal.awalrusuwf import AWalrusUWf\n\nWORKFLOWS = [AWalrusUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_walrus_u.py": "from products.r4_a_walrus_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\n(HOP := list(map(_wrap, PW)))\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_walrus_u import HOP as R4_A_WALRUS_U_HOP\nQUEUES_R4_A_WALRUS_U = [("r4_a_walrus_u", R4_A_WALRUS_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/awalrusuwf.py"],
        "outcome": "problem",
        "note": "core hop: '(HOP := list(map(_wrap, PW)))'",
    },
    "r4_a_walrus": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/awalruswf.py": "class AWalrusWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_walrus.backend.temporal.awalruswf import AWalrusWf\n\nWORKFLOWS = [AWalrusWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_walrus.py": "from products.r4_a_walrus.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\n(HOP := [*PW])\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_walrus import HOP as R4_A_WALRUS_HOP\nQUEUES_R4_A_WALRUS = [("r4_a_walrus", R4_A_WALRUS_HOP, [])]\n',
        },
        "probes": ["backend/temporal/awalruswf.py"],
        "outcome": "problem",
        "note": "core hop: '(HOP := [*PW])'",
    },
    "r4_a_or_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/aoruwf.py": "class AOrUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_or_u.backend.temporal.aoruwf import AOrUWf\n\nWORKFLOWS = [AOrUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_or_u.py": "from products.r4_a_or_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\n_MAYBE = None\nHOP = _MAYBE or list(map(_wrap, PW))\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_or_u import HOP as R4_A_OR_U_HOP\nQUEUES_R4_A_OR_U = [("r4_a_or_u", R4_A_OR_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/aoruwf.py"],
        "outcome": "problem",
        "note": "core hop: '_MAYBE = None\\nHOP = _MAYBE or list(map(_wrap, PW))'",
    },
    "r4_a_or": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/aorwf.py": "class AOrWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_or.backend.temporal.aorwf import AOrWf\n\nWORKFLOWS = [AOrWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_or.py": "from products.r4_a_or.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\n_MAYBE = None\nHOP = _MAYBE or PW\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_or import HOP as R4_A_OR_HOP\nQUEUES_R4_A_OR = [("r4_a_or", R4_A_OR_HOP, [])]\n',
        },
        "probes": ["backend/temporal/aorwf.py"],
        "outcome": "watched",
        "note": "core hop: '_MAYBE = None\\nHOP = _MAYBE or PW'",
    },
    "r4_a_annot_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/aannotuwf.py": "class AAnnotUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_annot_u.backend.temporal.aannotuwf import AAnnotUWf\n\nWORKFLOWS = [AAnnotUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_annot_u.py": "from products.r4_a_annot_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP: list = list(map(_wrap, PW))\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_annot_u import HOP as R4_A_ANNOT_U_HOP\nQUEUES_R4_A_ANNOT_U = [("r4_a_annot_u", R4_A_ANNOT_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/aannotuwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP: list = list(map(_wrap, PW))'",
    },
    "r4_a_annot": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/aannotwf.py": "class AAnnotWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_annot.backend.temporal.aannotwf import AAnnotWf\n\nWORKFLOWS = [AAnnotWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_annot.py": "from products.r4_a_annot.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP: list = [*PW]\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_annot import HOP as R4_A_ANNOT_HOP\nQUEUES_R4_A_ANNOT = [("r4_a_annot", R4_A_ANNOT_HOP, [])]\n',
        },
        "probes": ["backend/temporal/aannotwf.py"],
        "outcome": "watched",
        "note": "core hop: 'HOP: list = [*PW]'",
    },
    "r4_a_dictcomp_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/adictcompuwf.py": "class ADictcompUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_dictcomp_u.backend.temporal.adictcompuwf import ADictcompUWf\n\nWORKFLOWS = [ADictcompUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_dictcomp_u.py": "from products.r4_a_dictcomp_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = list({id(w): _wrap(w) for w in PW}.values())\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_dictcomp_u import HOP as R4_A_DICTCOMP_U_HOP\nQUEUES_R4_A_DICTCOMP_U = [("r4_a_dictcomp_u", R4_A_DICTCOMP_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/adictcompuwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = list({id(w): _wrap(w) for w in PW}.values())'",
    },
    "r4_a_dictcomp_wv_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/adictcompwvuwf.py": "class ADictcompWvUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_dictcomp_wv_u.backend.temporal.adictcompwvuwf import ADictcompWvUWf\n\nWORKFLOWS = [ADictcompWvUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_dictcomp_wv_u.py": "from products.r4_a_dictcomp_wv_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = {id(w): _wrap(w) for w in PW}\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_dictcomp_wv_u import HOP as R4_A_DICTCOMP_WV_U_HOP\nQUEUES_R4_A_DICTCOMP_WV_U = [("r4_a_dictcomp_wv_u", list(R4_A_DICTCOMP_WV_U_HOP.values()), [])]\n',
        },
        "probes": ["backend/temporal/adictcompwvuwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = {id(w): _wrap(w) for w in PW}'",
    },
    "r4_a_dictcomp_wv": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/adictcompwvwf.py": "class ADictcompWvWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_dictcomp_wv.backend.temporal.adictcompwvwf import ADictcompWvWf\n\nWORKFLOWS = [ADictcompWvWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_dictcomp_wv.py": "from products.r4_a_dictcomp_wv.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = {id(w): w for w in PW}\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_dictcomp_wv import HOP as R4_A_DICTCOMP_WV_HOP\nQUEUES_R4_A_DICTCOMP_WV = [("r4_a_dictcomp_wv", list(R4_A_DICTCOMP_WV_HOP.values()), [])]\n',
        },
        "probes": ["backend/temporal/adictcompwvwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = {id(w): w for w in PW}'",
    },
    "r4_a_default_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/adefaultuwf.py": "class ADefaultUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_default_u.backend.temporal.adefaultuwf import ADefaultUWf\n\nWORKFLOWS = [ADefaultUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_default_u.py": "from products.r4_a_default_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\ndef _all(ws=list(map(_wrap, PW))):\n    return ws\n\n\nHOP = _all()\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_default_u import HOP as R4_A_DEFAULT_U_HOP\nQUEUES_R4_A_DEFAULT_U = [("r4_a_default_u", R4_A_DEFAULT_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/adefaultuwf.py"],
        "outcome": "kept",
        "why": "class (a): a call of a function core defines",
        "note": "core hop: 'def _all(ws=list(map(_wrap, PW))):\\n    return ws\\n\\n\\nHOP = _all()'",
    },
    "r4_a_genexp_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/agenexpuwf.py": "class AGenexpUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_genexp_u.backend.temporal.agenexpuwf import AGenexpUWf\n\nWORKFLOWS = [AGenexpUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_genexp_u.py": "from products.r4_a_genexp_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = tuple(_wrap(w) for w in PW)\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_genexp_u import HOP as R4_A_GENEXP_U_HOP\nQUEUES_R4_A_GENEXP_U = [("r4_a_genexp_u", R4_A_GENEXP_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/agenexpuwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = tuple(_wrap(w) for w in PW)'",
    },
    "r4_a_mul_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/amuluwf.py": "class AMulUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_mul_u.backend.temporal.amuluwf import AMulUWf\n\nWORKFLOWS = [AMulUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_mul_u.py": "from products.r4_a_mul_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = list(map(_wrap, PW)) * 1\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_mul_u import HOP as R4_A_MUL_U_HOP\nQUEUES_R4_A_MUL_U = [("r4_a_mul_u", R4_A_MUL_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/amuluwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = list(map(_wrap, PW)) * 1'",
    },
    "r4_a_aug_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/aauguwf.py": "class AAugUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_aug_u.backend.temporal.aauguwf import AAugUWf\n\nWORKFLOWS = [AAugUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_aug_u.py": "from products.r4_a_aug_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = []\nHOP += map(_wrap, PW)\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_aug_u import HOP as R4_A_AUG_U_HOP\nQUEUES_R4_A_AUG_U = [("r4_a_aug_u", R4_A_AUG_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/aauguwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = []\\nHOP += map(_wrap, PW)'",
    },
    "r4_a_extend_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/aextenduwf.py": "class AExtendUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_extend_u.backend.temporal.aextenduwf import AExtendUWf\n\nWORKFLOWS = [AExtendUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_extend_u.py": "from products.r4_a_extend_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = []\nHOP.extend(map(_wrap, PW))\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_extend_u import HOP as R4_A_EXTEND_U_HOP\nQUEUES_R4_A_EXTEND_U = [("r4_a_extend_u", R4_A_EXTEND_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/aextenduwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = []\\nHOP.extend(map(_wrap, PW))'",
    },
    "r4_a_filter_u": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/afilteruwf.py": "class AFilterUWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_filter_u.backend.temporal.afilteruwf import AFilterUWf\n\nWORKFLOWS = [AFilterUWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_filter_u.py": "from products.r4_a_filter_u.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = list(filter(None, map(_wrap, PW)))\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_filter_u import HOP as R4_A_FILTER_U_HOP\nQUEUES_R4_A_FILTER_U = [("r4_a_filter_u", R4_A_FILTER_U_HOP, [])]\n',
        },
        "probes": ["backend/temporal/afilteruwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = list(filter(None, map(_wrap, PW)))'",
    },
    "r4_a_reversed": {
        "group": "a",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/areversedwf.py": "class AReversedWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_a_reversed.backend.temporal.areversedwf import AReversedWf\n\nWORKFLOWS = [AReversedWf]\n",
        },
        "core": {
            "posthog/temporal/r4_a_reversed.py": "from products.r4_a_reversed.backend.facade.temporal import WORKFLOWS as PW\n\n\ndef _wrap(w):\n    return w\n\n\nHOP = list(reversed(PW))\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_a_reversed import HOP as R4_A_REVERSED_HOP\nQUEUES_R4_A_REVERSED = [("r4_a_reversed", R4_A_REVERSED_HOP, [])]\n',
        },
        "probes": ["backend/temporal/areversedwf.py"],
        "outcome": "problem",
        "note": "core hop: 'HOP = list(reversed(PW))'",
    },
    "r4_b_im_alias": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bimaliaswf.py": "class BImAliasWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_im_alias.backend.temporal.bimaliaswf import BImAliasWf\n\nWORKFLOWS = [BImAliasWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nimport importlib as R4_B_IM_ALIAS_IL\nQUEUES_R4_B_IM_ALIAS = [("r4_b_im_alias", R4_B_IM_ALIAS_IL.import_module("products.r4_b_im_alias.backend.facade.temporal").WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/bimaliaswf.py"],
        "outcome": "watched",
        "note": "import importlib as il; il.import_module(...)",
    },
    "r4_b_im_from": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bimfromwf.py": "class BImFromWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_im_from.backend.temporal.bimfromwf import BImFromWf\n\nWORKFLOWS = [BImFromWf]\n",
        },
        "core": {
            "ee/hogai/core/__init__.py": "# core\n",
            "ee/hogai/core/agent_modes/__init__.py": "# agent modes\n",
            "ee/hogai/core/agent_modes/presets/__init__.py": "# presets\n",
            "ee/hogai/core/agent_modes/presets/r4_b_im_from.py": '\nfrom importlib import import_module as R4_B_IM_FROM_IM\nQUEUES_R4_B_IM_FROM = [("r4_b_im_from", R4_B_IM_FROM_IM("products.r4_b_im_from.backend.facade.temporal").WORKFLOWS, [])]\n',
        },
        "probes": ["backend/temporal/bimfromwf.py"],
        "outcome": "watched",
        "note": "from importlib import import_module as im",
    },
    "r4_b_im_var": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bimvarwf.py": "class BImVarWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_im_var.backend.temporal.bimvarwf import BImVarWf\n\nWORKFLOWS = [BImVarWf]\n",
        },
        "core": {
            "ee/hogai/core/__init__.py": "# core\n",
            "ee/hogai/core/agent_modes/__init__.py": "# agent modes\n",
            "ee/hogai/core/agent_modes/presets/__init__.py": "# presets\n",
            "ee/hogai/core/agent_modes/presets/r4_b_im_var.py": '\nimport importlib\nR4_B_IM_VAR_MOD = "products.r4_b_im_var.backend.facade.temporal"\nQUEUES_R4_B_IM_VAR = [("r4_b_im_var", importlib.import_module(R4_B_IM_VAR_MOD).WORKFLOWS, [])]\n',
        },
        "probes": ["backend/temporal/bimvarwf.py"],
        "outcome": "problem",
        "note": "import_module(NAME) with NAME a constant",
    },
    "r4_b_im_fstr": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bimfstrwf.py": "class BImFstrWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_im_fstr.backend.temporal.bimfstrwf import BImFstrWf\n\nWORKFLOWS = [BImFstrWf]\n",
        },
        "core": {
            "ee/hogai/core/__init__.py": "# core\n",
            "ee/hogai/core/agent_modes/__init__.py": "# agent modes\n",
            "ee/hogai/core/agent_modes/presets/__init__.py": "# presets\n",
            "ee/hogai/core/agent_modes/presets/r4_b_im_fstr.py": '\nimport importlib\nR4_B_IM_FSTR_P = "r4_b_im_fstr"\nQUEUES_R4_B_IM_FSTR = [("r4_b_im_fstr", importlib.import_module(f"products.{R4_B_IM_FSTR_P}.backend.facade.temporal").WORKFLOWS, [])]\n',
        },
        "probes": ["backend/temporal/bimfstrwf.py"],
        "outcome": "problem",
        "note": "import_module(f'products.{P}...')",
    },
    "r4_b_dunder_import": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bdunderimportwf.py": "class BDunderImportWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_dunder_import.backend.temporal.bdunderimportwf import BDunderImportWf\n\nWORKFLOWS = [BDunderImportWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nQUEUES_R4_B_DUNDER_IMPORT = [("r4_b_dunder_import", __import__("products.r4_b_dunder_import.backend.facade.temporal", fromlist=["WORKFLOWS"]).WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/bdunderimportwf.py"],
        "outcome": "watched",
        "note": "__import__(mod, fromlist=[...]).WORKFLOWS",
    },
    "r4_b_getattr_alias": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bgetattraliaswf.py": "class BGetattrAliasWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_getattr_alias.backend.temporal.bgetattraliaswf import BGetattrAliasWf\n\nWORKFLOWS = [BGetattrAliasWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_getattr_alias.backend.facade import temporal as R4_B_GETATTR_ALIAS_T\nR4_B_GETATTR_ALIAS_G = getattr\nQUEUES_R4_B_GETATTR_ALIAS = [("r4_b_getattr_alias", R4_B_GETATTR_ALIAS_G(R4_B_GETATTR_ALIAS_T, "WORKFLOWS"), [])]\n'
        },
        "probes": ["backend/temporal/bgetattraliaswf.py"],
        "outcome": "unread",
        "note": "g = getattr; g(mod, 'WORKFLOWS')",
    },
    "r4_b_getattr_aliasname": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bgetattraliasnamewf.py": "class BGetattrAliasnameWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_getattr_aliasname.backend.temporal.bgetattraliasnamewf import BGetattrAliasnameWf\n\nWORKFLOWS = [BGetattrAliasnameWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_getattr_aliasname.backend.facade import temporal as R4_B_GETATTR_ALIASNAME_T\nR4_B_GETATTR_ALIASNAME_G = getattr\nR4_B_GETATTR_ALIASNAME_N = "WORKFLOWS"\nQUEUES_R4_B_GETATTR_ALIASNAME = [("r4_b_getattr_aliasname", R4_B_GETATTR_ALIASNAME_G(R4_B_GETATTR_ALIASNAME_T, R4_B_GETATTR_ALIASNAME_N), [])]\n'
        },
        "probes": ["backend/temporal/bgetattraliasnamewf.py"],
        "outcome": "unread",
        "note": "g = getattr; g(mod, NAME)",
    },
    "r4_b_getattr_builtins": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bgetattrbuiltinswf.py": "class BGetattrBuiltinsWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_getattr_builtins.backend.temporal.bgetattrbuiltinswf import BGetattrBuiltinsWf\n\nWORKFLOWS = [BGetattrBuiltinsWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_getattr_builtins.backend.facade import temporal as R4_B_GETATTR_BUILTINS_T\nimport builtins\nR4_B_GETATTR_BUILTINS_N = "WORKFLOWS"\nQUEUES_R4_B_GETATTR_BUILTINS = [("r4_b_getattr_builtins", builtins.getattr(R4_B_GETATTR_BUILTINS_T, R4_B_GETATTR_BUILTINS_N), [])]\n'
        },
        "probes": ["backend/temporal/bgetattrbuiltinswf.py"],
        "outcome": "unread",
        "note": "builtins.getattr(mod, NAME)",
    },
    "r4_b_vars_alias": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bvarsaliaswf.py": "class BVarsAliasWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_vars_alias.backend.temporal.bvarsaliaswf import BVarsAliasWf\n\nWORKFLOWS = [BVarsAliasWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_vars_alias.backend.facade import temporal as R4_B_VARS_ALIAS_T\nR4_B_VARS_ALIAS_V = vars\nQUEUES_R4_B_VARS_ALIAS = [("r4_b_vars_alias", R4_B_VARS_ALIAS_V(R4_B_VARS_ALIAS_T)["WORKFLOWS"], [])]\n'
        },
        "probes": ["backend/temporal/bvarsaliaswf.py"],
        "outcome": "unread",
        "note": "v = vars; v(mod)['WORKFLOWS']",
    },
    "r4_b_attrgetter": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/battrgetterwf.py": "class BAttrgetterWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_attrgetter.backend.temporal.battrgetterwf import BAttrgetterWf\n\nWORKFLOWS = [BAttrgetterWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_attrgetter.backend.facade import temporal as R4_B_ATTRGETTER_T\nfrom operator import attrgetter\nQUEUES_R4_B_ATTRGETTER = [("r4_b_attrgetter", attrgetter("WORKFLOWS")(R4_B_ATTRGETTER_T), [])]\n'
        },
        "probes": ["backend/temporal/battrgetterwf.py"],
        "outcome": "unread",
        "note": "operator.attrgetter('WORKFLOWS')(mod)",
    },
    "r4_b_attrgetter_name": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/battrgetternamewf.py": "class BAttrgetterNameWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_attrgetter_name.backend.temporal.battrgetternamewf import BAttrgetterNameWf\n\nWORKFLOWS = [BAttrgetterNameWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_attrgetter_name.backend.facade import temporal as R4_B_ATTRGETTER_NAME_T\nimport operator\nR4_B_ATTRGETTER_NAME_N = "WORKFLOWS"\nQUEUES_R4_B_ATTRGETTER_NAME = [("r4_b_attrgetter_name", operator.attrgetter(R4_B_ATTRGETTER_NAME_N)(R4_B_ATTRGETTER_NAME_T), [])]\n'
        },
        "probes": ["backend/temporal/battrgetternamewf.py"],
        "outcome": "unread",
        "note": "operator.attrgetter(NAME)(mod)",
    },
    "r4_b_globals": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bglobalswf.py": "class BGlobalsWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_globals.backend.temporal.bglobalswf import BGlobalsWf\n\nWORKFLOWS = [BGlobalsWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_globals.backend.facade.temporal import WORKFLOWS as R4_B_GLOBALS_W\nQUEUES_R4_B_GLOBALS = [("r4_b_globals", globals()["R4_B_GLOBALS_" + "W"], [])]\n'
        },
        "probes": ["backend/temporal/bglobalswf.py"],
        "outcome": "watched",
        "note": "globals()[...] of the imported list",
    },
    "r4_b_sysmod": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bsysmodwf.py": "class BSysmodWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_sysmod.backend.temporal.bsysmodwf import BSysmodWf\n\nWORKFLOWS = [BSysmodWf]\n",
        },
        "core": {
            "ee/hogai/core/__init__.py": "# core\n",
            "ee/hogai/core/agent_modes/__init__.py": "# agent modes\n",
            "ee/hogai/core/agent_modes/presets/__init__.py": "# presets\n",
            "ee/hogai/core/agent_modes/presets/r4_b_sysmod.py": '\nimport sys\nimport products.r4_b_sysmod.backend.facade.temporal  # noqa: F401\nQUEUES_R4_B_SYSMOD = [("r4_b_sysmod", sys.modules["products.r4_b_sysmod.backend.facade.temporal"].WORKFLOWS, [])]\n',
        },
        "probes": ["backend/temporal/bsysmodwf.py"],
        "outcome": "problem",
        "note": "sys.modules['<facade>'].WORKFLOWS",
    },
    "r4_b_sysmod_name": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bsysmodnamewf.py": "class BSysmodNameWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_sysmod_name.backend.temporal.bsysmodnamewf import BSysmodNameWf\n\nWORKFLOWS = [BSysmodNameWf]\n",
        },
        "core": {
            "ee/hogai/core/__init__.py": "# core\n",
            "ee/hogai/core/agent_modes/__init__.py": "# agent modes\n",
            "ee/hogai/core/agent_modes/presets/__init__.py": "# presets\n",
            "ee/hogai/core/agent_modes/presets/r4_b_sysmod_name.py": '\nimport sys\nimport products.r4_b_sysmod_name.backend.facade.temporal  # noqa: F401\nR4_B_SYSMOD_NAME_M = "products.r4_b_sysmod_name.backend.facade.temporal"\nQUEUES_R4_B_SYSMOD_NAME = [("r4_b_sysmod_name", sys.modules[R4_B_SYSMOD_NAME_M].WORKFLOWS, [])]\n',
        },
        "probes": ["backend/temporal/bsysmodnamewf.py"],
        "outcome": "problem",
        "note": "sys.modules[NAME].WORKFLOWS",
    },
    "r4_b_moddict": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bmoddictwf.py": "class BModdictWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_moddict.backend.temporal.bmoddictwf import BModdictWf\n\nWORKFLOWS = [BModdictWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_moddict.backend.facade import temporal as R4_B_MODDICT_T\nQUEUES_R4_B_MODDICT = [("r4_b_moddict", R4_B_MODDICT_T.__dict__["WORKFLOWS"], [])]\n'
        },
        "probes": ["backend/temporal/bmoddictwf.py"],
        "outcome": "unread",
        "note": "mod.__dict__['WORKFLOWS']",
    },
    "r4_b_moddict_name": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bmoddictnamewf.py": "class BModdictNameWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_moddict_name.backend.temporal.bmoddictnamewf import BModdictNameWf\n\nWORKFLOWS = [BModdictNameWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_moddict_name.backend.facade import temporal as R4_B_MODDICT_NAME_T\nR4_B_MODDICT_NAME_N = "WORKFLOWS"\nQUEUES_R4_B_MODDICT_NAME = [("r4_b_moddict_name", R4_B_MODDICT_NAME_T.__dict__[R4_B_MODDICT_NAME_N], [])]\n'
        },
        "probes": ["backend/temporal/bmoddictnamewf.py"],
        "outcome": "unread",
        "note": "mod.__dict__[NAME]",
    },
    "r4_b_getattribute": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/bgetattributewf.py": "class BGetattributeWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_getattribute.backend.temporal.bgetattributewf import BGetattributeWf\n\nWORKFLOWS = [BGetattributeWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_getattribute.backend.facade import temporal as R4_B_GETATTRIBUTE_T\nR4_B_GETATTRIBUTE_N = "WORKFLOWS"\nQUEUES_R4_B_GETATTRIBUTE = [("r4_b_getattribute", R4_B_GETATTRIBUTE_T.__getattribute__(R4_B_GETATTRIBUTE_N), [])]\n'
        },
        "probes": ["backend/temporal/bgetattributewf.py"],
        "outcome": "unread",
        "note": "mod.__getattribute__(NAME)",
    },
    "r4_b_iutil": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/biutilwf.py": "class BIutilWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_iutil.backend.temporal.biutilwf import BIutilWf\n\nWORKFLOWS = [BIutilWf]\n",
        },
        "core": {
            "ee/hogai/core/__init__.py": "# core\n",
            "ee/hogai/core/agent_modes/__init__.py": "# agent modes\n",
            "ee/hogai/core/agent_modes/presets/__init__.py": "# presets\n",
            "ee/hogai/core/agent_modes/presets/r4_b_iutil.py": '\nimport importlib.util\nR4_B_IUTIL_SPEC = importlib.util.find_spec("products.r4_b_iutil.backend.facade.temporal")\nR4_B_IUTIL_MOD = importlib.util.module_from_spec(R4_B_IUTIL_SPEC)\nR4_B_IUTIL_SPEC.loader.exec_module(R4_B_IUTIL_MOD)\nQUEUES_R4_B_IUTIL = [("r4_b_iutil", R4_B_IUTIL_MOD.WORKFLOWS, [])]\n',
        },
        "probes": ["backend/temporal/biutilwf.py"],
        "outcome": "problem",
        "note": "importlib.util.find_spec / module_from_spec / exec_module",
    },
    "r4_b_inspect": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/binspectwf.py": "class BInspectWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_inspect.backend.temporal.binspectwf import BInspectWf\n\nWORKFLOWS = [BInspectWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_inspect.backend.facade import temporal as R4_B_INSPECT_T\nimport inspect\nQUEUES_R4_B_INSPECT = [("r4_b_inspect", dict(inspect.getmembers(R4_B_INSPECT_T))["WORKFLOWS"], [])]\n'
        },
        "probes": ["backend/temporal/binspectwf.py"],
        "outcome": "unread",
        "note": "dict(inspect.getmembers(mod))['WORKFLOWS']",
    },
    "r4_b_reload": {
        "group": "b",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/breloadwf.py": "class BReloadWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_b_reload.backend.temporal.breloadwf import BReloadWf\n\nWORKFLOWS = [BReloadWf]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_b_reload.backend.facade import temporal as R4_B_RELOAD_T\nimport importlib\nQUEUES_R4_B_RELOAD = [("r4_b_reload", importlib.reload(R4_B_RELOAD_T).WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/breloadwf.py"],
        "outcome": "unread",
        "note": "importlib.reload(mod).WORKFLOWS",
    },
    "r4_c_twofacade": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ctfa.py": "class CtfA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_twofacade.backend.facade.other import WORKFLOWS\n\nWORKFLOWS = WORKFLOWS\n",
            "backend/facade/other.py": "from products.r4_c_twofacade.backend.facade.temporal import WORKFLOWS\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_twofacade.backend.facade.temporal import WORKFLOWS as R4_C_TWOFACADE_WORKFLOWS\n\nQUEUES_R4_C_TWOFACADE = [("r4_c_twofacade", R4_C_TWOFACADE_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ctfa.py"],
        "outcome": "unread",
        "note": "two facades: a: from b import W; W = W   b: from a import W (no items)",
    },
    "r4_c_twofacade_items": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ctia.py": "class CtiA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_twofacade_items.backend.facade.other import WORKFLOWS\n\nWORKFLOWS = WORKFLOWS\n",
            "backend/facade/other.py": "from products.r4_c_twofacade_items.backend.temporal.ctia import CtiA\nfrom products.r4_c_twofacade_items.backend.facade.temporal import WORKFLOWS\n\nWORKFLOWS = WORKFLOWS + [CtiA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_twofacade_items.backend.facade.temporal import WORKFLOWS as R4_C_TWOFACADE_ITEMS_WORKFLOWS\n\nQUEUES_R4_C_TWOFACADE_ITEMS = [("r4_c_twofacade_items", R4_C_TWOFACADE_ITEMS_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ctia.py"],
        "outcome": "watched",
        "note": "two facades rebinding each other's W, one adds an item",
    },
    "r4_c_xprod_a": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/cxa.py": "class CxA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_xprod_a.backend.temporal.cxa import CxA\nfrom products.r4_c_xprod_b.backend.facade.temporal import WORKFLOWS\n\nWORKFLOWS = WORKFLOWS + [CxA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_xprod_a.backend.facade.temporal import WORKFLOWS as R4_C_XPROD_A_WORKFLOWS\n\nQUEUES_R4_C_XPROD_A = [("r4_c_xprod_a", R4_C_XPROD_A_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/cxa.py"],
        "outcome": "watched",
        "note": "two products' facades: a: from b import W; W = W + [A]   b: from a import W; W = W",
    },
    "r4_c_xprod_b": {
        "group": "c",
        "files": {
            "backend/facade/temporal.py": "from products.r4_c_xprod_a.backend.facade.temporal import WORKFLOWS\n\nWORKFLOWS = WORKFLOWS\n"
        },
        "core": {},
        "probes": [],
        "outcome": "watched",
        "note": "(partner of r4_c_xprod_a, not registered)",
    },
    "r4_c_reexport": {
        "group": "c",
        "files": {
            "backend/facade/temporal.py": "from products.r4_c_reexport.backend.facade.other import WORKFLOWS\n",
            "backend/facade/other.py": "from products.r4_c_reexport.backend.facade.temporal import WORKFLOWS\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_reexport.backend.facade.temporal import WORKFLOWS as R4_C_REEXPORT_WORKFLOWS\n\nQUEUES_R4_C_REEXPORT = [("r4_c_reexport", R4_C_REEXPORT_WORKFLOWS, [])]\n'
        },
        "probes": [],
        "outcome": "unread",
        "note": "re-export cycle: a: from b import W   b: from a import W",
    },
    "r4_c_reexport_all": {
        "group": "c",
        "files": {
            "backend/facade/temporal.py": "from products.r4_c_reexport_all.backend.facade.other import WORKFLOWS\n\n__all__ = ['WORKFLOWS']\n",
            "backend/facade/other.py": "from products.r4_c_reexport_all.backend.facade.temporal import WORKFLOWS\n\n__all__ = ['WORKFLOWS']\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_reexport_all.backend.facade.temporal import WORKFLOWS as R4_C_REEXPORT_ALL_WORKFLOWS\n\nQUEUES_R4_C_REEXPORT_ALL = [("r4_c_reexport_all", R4_C_REEXPORT_ALL_WORKFLOWS, [])]\n'
        },
        "probes": [],
        "outcome": "unread",
        "note": "re-export cycle with __all__ on both sides",
    },
    "r4_c_reexport3": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/cr3a.py": "class Cr3A:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_reexport3.backend.facade.b import WORKFLOWS\n",
            "backend/facade/b.py": "from products.r4_c_reexport3.backend.facade.c import WORKFLOWS\n",
            "backend/facade/c.py": "from products.r4_c_reexport3.backend.temporal.cr3a import Cr3A\nfrom products.r4_c_reexport3.backend.facade.temporal import WORKFLOWS\n\nWORKFLOWS = WORKFLOWS + [Cr3A]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_reexport3.backend.facade.temporal import WORKFLOWS as R4_C_REEXPORT3_WORKFLOWS\n\nQUEUES_R4_C_REEXPORT3 = [("r4_c_reexport3", R4_C_REEXPORT3_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/cr3a.py"],
        "outcome": "unread",
        "note": "three-module re-export cycle, the last rebinds with an item",
    },
    "r4_c_star": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/csa.py": "class CsA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_star.backend.facade.other import *  # noqa\n",
            "backend/facade/other.py": "from products.r4_c_star.backend.temporal.csa import CsA\nfrom products.r4_c_star.backend.facade.temporal import *  # noqa\n\nWORKFLOWS = [CsA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_star.backend.facade.temporal import WORKFLOWS as R4_C_STAR_WORKFLOWS\n\nQUEUES_R4_C_STAR = [("r4_c_star", R4_C_STAR_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/csa.py"],
        "outcome": "watched",
        "note": "star-import cycle, W defined in the second module",
    },
    "r4_c_star_nodef": {
        "group": "c",
        "files": {
            "backend/facade/temporal.py": "from products.r4_c_star_nodef.backend.facade.other import *  # noqa\n",
            "backend/facade/other.py": "from products.r4_c_star_nodef.backend.facade.temporal import *  # noqa\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_star_nodef.backend.facade.temporal import WORKFLOWS as R4_C_STAR_NODEF_WORKFLOWS\n\nQUEUES_R4_C_STAR_NODEF = [("r4_c_star_nodef", R4_C_STAR_NODEF_WORKFLOWS, [])]\n'
        },
        "probes": [],
        "outcome": "unread",
        "note": "star-import cycle, W defined nowhere",
    },
    "r4_c_star_self": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/cssa.py": "class CssA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_star_self.backend.temporal.cssa import CssA\nfrom products.r4_c_star_self.backend.facade.temporal import *  # noqa\n\nWORKFLOWS = [CssA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_star_self.backend.facade.temporal import WORKFLOWS as R4_C_STAR_SELF_WORKFLOWS\n\nQUEUES_R4_C_STAR_SELF = [("r4_c_star_self", R4_C_STAR_SELF_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/cssa.py"],
        "outcome": "watched",
        "note": "module star-imports itself",
    },
    "r4_c_star_rebind": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/csra.py": "class CsrA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_star_rebind.backend.facade.other import *  # noqa\n\nWORKFLOWS = WORKFLOWS\n",
            "backend/facade/other.py": "from products.r4_c_star_rebind.backend.temporal.csra import CsrA\nfrom products.r4_c_star_rebind.backend.facade.temporal import *  # noqa\n\nWORKFLOWS = WORKFLOWS + [CsrA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_star_rebind.backend.facade.temporal import WORKFLOWS as R4_C_STAR_REBIND_WORKFLOWS\n\nQUEUES_R4_C_STAR_REBIND = [("r4_c_star_rebind", R4_C_STAR_REBIND_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/csra.py"],
        "outcome": "unread",
        "note": "star-import cycle, both sides rebind W from the star",
    },
    "r4_c_star_selfrebind": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/csba.py": "class CsbA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_star_selfrebind.backend.facade.other import *  # noqa\n\nWORKFLOWS = WORKFLOWS\n",
            "backend/facade/other.py": "from products.r4_c_star_selfrebind.backend.temporal.csba import CsbA\n\nWORKFLOWS = [CsbA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_star_selfrebind.backend.facade.temporal import WORKFLOWS as R4_C_STAR_SELFREBIND_WORKFLOWS\n\nQUEUES_R4_C_STAR_SELFREBIND = [("r4_c_star_selfrebind", R4_C_STAR_SELFREBIND_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/csba.py"],
        "outcome": "unread",
        "note": "star import, then W = W (valid Python, no cycle)",
    },
    "r4_c_star_selfadd": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/csda.py": "class CsdA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/csdb.py": "class CsdB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_star_selfadd.backend.temporal.csdb import CsdB\nfrom products.r4_c_star_selfadd.backend.facade.other import *  # noqa\n\nWORKFLOWS = WORKFLOWS + [CsdB]\n",
            "backend/facade/other.py": "from products.r4_c_star_selfadd.backend.temporal.csda import CsdA\n\nWORKFLOWS = [CsdA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_star_selfadd.backend.facade.temporal import WORKFLOWS as R4_C_STAR_SELFADD_WORKFLOWS\n\nQUEUES_R4_C_STAR_SELFADD = [("r4_c_star_selfadd", R4_C_STAR_SELFADD_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/csda.py", "backend/temporal/csdb.py"],
        "outcome": "unread",
        "note": "star import, then W = W + [B] (valid Python, no cycle)",
    },
    "r4_c_cls_self": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ccsa.py": "class CcsA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_cls_self.backend.temporal.ccsa import CcsA\n\nclass Registration:\n    WORKFLOWS = Registration.WORKFLOWS + [CcsA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_cls_self.backend.facade.temporal import Registration as R4CcsRegistration\nQUEUES_R4_CCS = [("r4_c_cls_self", R4CcsRegistration.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ccsa.py"],
        "outcome": "watched",
        "note": "class attribute names itself: W = Registration.W + [A]",
    },
    "r4_c_cls_bare": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ccba.py": "class CcbA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_cls_bare.backend.temporal.ccba import CcbA\n\nWORKFLOWS = [CcbA]\n\n\nclass Registration:\n    WORKFLOWS = WORKFLOWS\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_cls_bare.backend.facade.temporal import Registration as R4CcbRegistration\nQUEUES_R4_CCB = [("r4_c_cls_bare", R4CcbRegistration.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ccba.py"],
        "outcome": "watched",
        "note": "class attribute W = W (reads the module's W)",
    },
    "r4_c_cls_sub": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ccua.py": "class CcuA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_cls_sub.backend.temporal.ccua import CcuA\n\nclass Registration:\n    WORKFLOWS = Sub.WORKFLOWS\n\n\nclass Sub(Registration):\n    WORKFLOWS = Registration.WORKFLOWS + [CcuA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_cls_sub.backend.facade.temporal import Registration as R4CcuRegistration\nQUEUES_R4_CCU = [("r4_c_cls_sub", R4CcuRegistration.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ccua.py"],
        "outcome": "watched",
        "note": "class attribute names its subclass's, which names it back",
    },
    "r4_c_cls_mutual": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ccma.py": "class CcmA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_cls_mutual.backend.facade.other import Other\n\n\nclass Registration:\n    WORKFLOWS = Other.WORKFLOWS\n",
            "backend/facade/other.py": "from products.r4_c_cls_mutual.backend.temporal.ccma import CcmA\nfrom products.r4_c_cls_mutual.backend.facade.temporal import Registration\n\n\nclass Other:\n    WORKFLOWS = Registration.WORKFLOWS + [CcmA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_cls_mutual.backend.facade.temporal import Registration as R4CcmRegistration\nQUEUES_R4_CCM = [("r4_c_cls_mutual", R4CcmRegistration.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ccma.py"],
        "outcome": "watched",
        "note": "class attributes of two modules name each other",
    },
    "r4_c_decl_two": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/cdta.py": "class CdtA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/queues.py": 'from posthog.temporal.common.registration import QueueBinding, TemporalRegistration\nfrom products.r4_c_decl_two.backend.temporal.cdta import CdtA\nfrom products.r4_c_decl_two.backend.facade.temporal import QUEUES\n\nQUEUES = QUEUES + (QueueBinding(queue_setting="CDT", workflows=(CdtA,)),)\n',
            "backend/facade/temporal.py": "from posthog.temporal.common.registration import QueueBinding, TemporalRegistration\nfrom products.r4_c_decl_two.backend.temporal.queues import QUEUES\n\nQUEUES = QUEUES\nTEMPORAL = TemporalRegistration(queues=QUEUES)\n",
        },
        "core": {
            "posthog/temporal/common/registration.py": "class QueueBinding:\n    def __init__(self, queue_setting, workflows=(), activities=()):\n        self.queue_setting = queue_setting\n\n\nclass TemporalRegistration:\n    def __init__(self, queues=(), schedules=(), log_sources=()):\n        self.queues = queues\n"
        },
        "probes": ["backend/temporal/cdta.py"],
        "outcome": "watched",
        "note": "declaration: facade rebinds QUEUES = QUEUES from a module that imports it back",
    },
    "r4_c_decl_star": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/cdsa.py": "class CdsA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/queues.py": 'from posthog.temporal.common.registration import QueueBinding, TemporalRegistration\nfrom products.r4_c_decl_star.backend.temporal.cdsa import CdsA\nfrom products.r4_c_decl_star.backend.facade.temporal import *  # noqa\n\nQUEUES = (QueueBinding(queue_setting="CDS", workflows=(CdsA,)),)\n',
            "backend/facade/temporal.py": "from posthog.temporal.common.registration import QueueBinding, TemporalRegistration\nfrom products.r4_c_decl_star.backend.temporal.queues import *  # noqa\n\nTEMPORAL = TemporalRegistration(queues=QUEUES)\n",
        },
        "core": {
            "posthog/temporal/common/registration.py": "class QueueBinding:\n    def __init__(self, queue_setting, workflows=(), activities=()):\n        self.queue_setting = queue_setting\n\n\nclass TemporalRegistration:\n    def __init__(self, queues=(), schedules=(), log_sources=()):\n        self.queues = queues\n"
        },
        "probes": ["backend/temporal/cdsa.py"],
        "outcome": "unread",
        "note": "declaration names QUEUES from a star-import cycle",
    },
    "r4_c_hop_rebind": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/chra.py": "class ChrA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_hop_rebind.backend.temporal.chra import ChrA\n\nWORKFLOWS = [ChrA]\n",
        },
        "core": {
            "posthog/temporal/r4_c_hop_rebind_a.py": "from posthog.temporal.r4_c_hop_rebind_b import H\n\nH = H\n",
            "posthog/temporal/r4_c_hop_rebind_b.py": "from products.r4_c_hop_rebind.backend.facade.temporal import WORKFLOWS as PW\nfrom posthog.temporal.r4_c_hop_rebind_a import H\n\nH = [*PW, *H]\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom posthog.temporal.r4_c_hop_rebind_a import H as R4_CHR_H\nQUEUES_R4_CHR = [("r4_c_hop_rebind", R4_CHR_H, [])]\n',
        },
        "probes": ["backend/temporal/chra.py"],
        "outcome": "watched",
        "note": "core hops: a: from b import H; H = H   b: from a import H; H = [*PW, *H]",
    },
    "r4_c_hop_reexport": {
        "group": "c",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/chxa.py": "class ChxA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_c_hop_reexport.backend.temporal.chxa import ChxA\n\nWORKFLOWS = [ChxA]\n",
        },
        "core": {
            "posthog/temporal/r4_c_hop_reexport_a.py": "from posthog.temporal.r4_c_hop_reexport_b import H\n",
            "posthog/temporal/r4_c_hop_reexport_b.py": "from posthog.temporal.r4_c_hop_reexport_a import H\n",
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_c_hop_reexport.backend.facade.temporal import WORKFLOWS as R4_CHX_W\nfrom posthog.temporal.r4_c_hop_reexport_a import H as R4_CHX_H\nQUEUES_R4_CHX = [("r4_c_hop_reexport", R4_CHX_W, R4_CHX_H)]\n',
        },
        "probes": ["backend/temporal/chxa.py"],
        "outcome": "problem",
        "note": "core re-export cycle registered next to a product list",
    },
    "r4_d_aug": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dauga.py": "class DAugA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/daugb.py": "class DAugB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_aug.backend.temporal.dauga import DAugA\nfrom products.r4_d_aug.backend.temporal.daugb import DAugB\n\nWORKFLOWS = [DAugA]\nWORKFLOWS += [DAugB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_aug.backend.facade.temporal import WORKFLOWS as R4_D_AUG_WORKFLOWS\n\nQUEUES_R4_D_AUG = [("r4_d_aug", R4_D_AUG_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dauga.py", "backend/temporal/daugb.py"],
        "outcome": "watched",
        "note": "W += [B]",
    },
    "r4_d_aug_u": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/daugua.py": "class DAugUA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/daugub.py": "class DAugUB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_aug_u.backend.temporal.daugua import DAugUA\nfrom products.r4_d_aug_u.backend.temporal.daugub import DAugUB\n\ndef _wrap(w):\n    return w\n\n\nWORKFLOWS = [DAugUA]\nWORKFLOWS += map(_wrap, [DAugUB])\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_aug_u.backend.facade.temporal import WORKFLOWS as R4_D_AUG_U_WORKFLOWS\n\nQUEUES_R4_D_AUG_U = [("r4_d_aug_u", R4_D_AUG_U_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/daugua.py", "backend/temporal/daugub.py"],
        "outcome": "unread",
        "note": "W += map(_wrap, [B])",
    },
    "r4_d_slice": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dslicea.py": "class DSliceA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dsliceb.py": "class DSliceB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_slice.backend.temporal.dslicea import DSliceA\nfrom products.r4_d_slice.backend.temporal.dsliceb import DSliceB\n\nWORKFLOWS = [DSliceA]\nWORKFLOWS[:] = [DSliceB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_slice.backend.facade.temporal import WORKFLOWS as R4_D_SLICE_WORKFLOWS\n\nQUEUES_R4_D_SLICE = [("r4_d_slice", R4_D_SLICE_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dsliceb.py"],
        "outcome": "watched",
        "note": "W[:] = [B]",
    },
    "r4_d_slice_end": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dsliceenda.py": "class DSliceEndA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dsliceendb.py": "class DSliceEndB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_slice_end.backend.temporal.dsliceenda import DSliceEndA\nfrom products.r4_d_slice_end.backend.temporal.dsliceendb import DSliceEndB\n\nWORKFLOWS = [DSliceEndA]\nWORKFLOWS[len(WORKFLOWS):] = [DSliceEndB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_slice_end.backend.facade.temporal import WORKFLOWS as R4_D_SLICE_END_WORKFLOWS\n\nQUEUES_R4_D_SLICE_END = [("r4_d_slice_end", R4_D_SLICE_END_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dsliceenda.py", "backend/temporal/dsliceendb.py"],
        "outcome": "watched",
        "note": "W[len(W):] = [B]",
    },
    "r4_d_setattr_self": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dsetattrselfa.py": "class DSetattrSelfA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dsetattrselfb.py": "class DSetattrSelfB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'import sys\nfrom products.r4_d_setattr_self.backend.temporal.dsetattrselfa import DSetattrSelfA\nfrom products.r4_d_setattr_self.backend.temporal.dsetattrselfb import DSetattrSelfB\n\nWORKFLOWS = [DSetattrSelfA]\nsetattr(sys.modules[__name__], "WORKFLOWS", WORKFLOWS + [DSetattrSelfB])\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_setattr_self.backend.facade.temporal import WORKFLOWS as R4_D_SETATTR_SELF_WORKFLOWS\n\nQUEUES_R4_D_SETATTR_SELF = [("r4_d_setattr_self", R4_D_SETATTR_SELF_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dsetattrselfa.py", "backend/temporal/dsetattrselfb.py"],
        "outcome": "unread",
        "note": "setattr(sys.modules[__name__], 'W', W + [B])",
    },
    "r4_d_setattr_ext": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dsetattrexta.py": "class DSetattrExtA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dsetattrextb.py": "class DSetattrExtB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_setattr_ext.backend.temporal.dsetattrexta import DSetattrExtA\nfrom products.r4_d_setattr_ext.backend.temporal.dsetattrextb import DSetattrExtB\n\nWORKFLOWS = [DSetattrExtA]\n",
            "backend/facade/__init__.py": 'from products.r4_d_setattr_ext.backend.facade import temporal\nfrom products.r4_d_setattr_ext.backend.temporal.dsetattrextb import DSetattrExtB\n\nsetattr(temporal, "WORKFLOWS", temporal.WORKFLOWS + [DSetattrExtB])\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_setattr_ext.backend.facade.temporal import WORKFLOWS as R4_D_SETATTR_EXT_WORKFLOWS\n\nQUEUES_R4_D_SETATTR_EXT = [("r4_d_setattr_ext", R4_D_SETATTR_EXT_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dsetattrexta.py", "backend/temporal/dsetattrextb.py"],
        "outcome": "kept",
        "why": "limit 1: another module changes the list",
        "note": "another product module: setattr(temporal, 'W', temporal.W + [B])",
    },
    "r4_d_attr_ext": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dattrexta.py": "class DAttrExtA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dattrextb.py": "class DAttrExtB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_attr_ext.backend.temporal.dattrexta import DAttrExtA\nfrom products.r4_d_attr_ext.backend.temporal.dattrextb import DAttrExtB\n\nWORKFLOWS = [DAttrExtA]\n",
            "backend/facade/__init__.py": "from products.r4_d_attr_ext.backend.facade import temporal\nfrom products.r4_d_attr_ext.backend.temporal.dattrextb import DAttrExtB\n\ntemporal.WORKFLOWS = temporal.WORKFLOWS + [DAttrExtB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_attr_ext.backend.facade.temporal import WORKFLOWS as R4_D_ATTR_EXT_WORKFLOWS\n\nQUEUES_R4_D_ATTR_EXT = [("r4_d_attr_ext", R4_D_ATTR_EXT_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dattrexta.py", "backend/temporal/dattrextb.py"],
        "outcome": "kept",
        "why": "limit 1: another module changes the list",
        "note": "another product module: temporal.W = temporal.W + [B]",
    },
    "r4_d_append_ext": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dappendexta.py": "class DAppendExtA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dappendextb.py": "class DAppendExtB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_append_ext.backend.temporal.dappendexta import DAppendExtA\nfrom products.r4_d_append_ext.backend.temporal.dappendextb import DAppendExtB\n\nWORKFLOWS = [DAppendExtA]\n",
            "backend/facade/__init__.py": "from products.r4_d_append_ext.backend.facade import temporal\nfrom products.r4_d_append_ext.backend.temporal.dappendextb import DAppendExtB\n\ntemporal.WORKFLOWS.append(DAppendExtB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_append_ext.backend.facade.temporal import WORKFLOWS as R4_D_APPEND_EXT_WORKFLOWS\n\nQUEUES_R4_D_APPEND_EXT = [("r4_d_append_ext", R4_D_APPEND_EXT_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dappendexta.py", "backend/temporal/dappendextb.py"],
        "outcome": "kept",
        "why": "limit 1: another module changes the list",
        "note": "another product module: temporal.W.append(B)",
    },
    "r4_d_del": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ddela.py": "class DDelA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/ddelb.py": "class DDelB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_del.backend.temporal.ddela import DDelA\nfrom products.r4_d_del.backend.temporal.ddelb import DDelB\n\nWORKFLOWS = [DDelA, DDelB]\ndel WORKFLOWS[0]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_del.backend.facade.temporal import WORKFLOWS as R4_D_DEL_WORKFLOWS\n\nQUEUES_R4_D_DEL = [("r4_d_del", R4_D_DEL_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ddela.py", "backend/temporal/ddelb.py"],
        "outcome": "watched",
        "note": "del W[0] (A over-watched)",
    },
    "r4_d_insert": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dinserta.py": "class DInsertA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dinsertb.py": "class DInsertB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_insert.backend.temporal.dinserta import DInsertA\nfrom products.r4_d_insert.backend.temporal.dinsertb import DInsertB\n\nWORKFLOWS = [DInsertA]\nWORKFLOWS.insert(0, DInsertB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_insert.backend.facade.temporal import WORKFLOWS as R4_D_INSERT_WORKFLOWS\n\nQUEUES_R4_D_INSERT = [("r4_d_insert", R4_D_INSERT_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dinserta.py", "backend/temporal/dinsertb.py"],
        "outcome": "watched",
        "note": "W.insert(0, B)",
    },
    "r4_d_remove": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dremovea.py": "class DRemoveA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dremoveb.py": "class DRemoveB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_remove.backend.temporal.dremovea import DRemoveA\nfrom products.r4_d_remove.backend.temporal.dremoveb import DRemoveB\n\nWORKFLOWS = [DRemoveA, DRemoveB]\nWORKFLOWS.remove(DRemoveA)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_remove.backend.facade.temporal import WORKFLOWS as R4_D_REMOVE_WORKFLOWS\n\nQUEUES_R4_D_REMOVE = [("r4_d_remove", R4_D_REMOVE_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dremovea.py", "backend/temporal/dremoveb.py"],
        "outcome": "watched",
        "note": "W.remove(A) (A over-watched)",
    },
    "r4_d_if": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/difa.py": "class DIfA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/difb.py": "class DIfB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from django.conf import settings\nfrom products.r4_d_if.backend.temporal.difa import DIfA\nfrom products.r4_d_if.backend.temporal.difb import DIfB\n\nWORKFLOWS = [DIfA]\nif settings.DEBUG:\n    WORKFLOWS.append(DIfB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_if.backend.facade.temporal import WORKFLOWS as R4_D_IF_WORKFLOWS\n\nQUEUES_R4_D_IF = [("r4_d_if", R4_D_IF_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/difa.py", "backend/temporal/difb.py"],
        "outcome": "unread",
        "note": "if settings.DEBUG: W.append(B)",
    },
    "r4_d_else": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/delsea.py": "class DElseA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/delseb.py": "class DElseB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from django.conf import settings\nfrom products.r4_d_else.backend.temporal.delsea import DElseA\nfrom products.r4_d_else.backend.temporal.delseb import DElseB\n\nWORKFLOWS = [DElseA]\nif settings.DEBUG:\n    pass\nelse:\n    WORKFLOWS.extend([DElseB])\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_else.backend.facade.temporal import WORKFLOWS as R4_D_ELSE_WORKFLOWS\n\nQUEUES_R4_D_ELSE = [("r4_d_else", R4_D_ELSE_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/delsea.py", "backend/temporal/delseb.py"],
        "outcome": "unread",
        "note": "else: W.extend([B])",
    },
    "r4_d_try": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dtrya.py": "class DTryA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dtryb.py": "class DTryB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_try.backend.temporal.dtrya import DTryA\nfrom products.r4_d_try.backend.temporal.dtryb import DTryB\n\nWORKFLOWS = [DTryA]\ntry:\n    WORKFLOWS.append(DTryB)\nexcept Exception:\n    pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_try.backend.facade.temporal import WORKFLOWS as R4_D_TRY_WORKFLOWS\n\nQUEUES_R4_D_TRY = [("r4_d_try", R4_D_TRY_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dtrya.py", "backend/temporal/dtryb.py"],
        "outcome": "unread",
        "note": "try: W.append(B)",
    },
    "r4_d_except": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dexcepta.py": "class DExceptA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dexceptb.py": "class DExceptB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_except.backend.temporal.dexcepta import DExceptA\nfrom products.r4_d_except.backend.temporal.dexceptb import DExceptB\n\nWORKFLOWS = [DExceptA]\ntry:\n    import nonexistent_r4  # noqa: F401\nexcept ImportError:\n    WORKFLOWS.append(DExceptB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_except.backend.facade.temporal import WORKFLOWS as R4_D_EXCEPT_WORKFLOWS\n\nQUEUES_R4_D_EXCEPT = [("r4_d_except", R4_D_EXCEPT_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dexcepta.py", "backend/temporal/dexceptb.py"],
        "outcome": "unread",
        "note": "except ImportError: W.append(B)",
    },
    "r4_d_tc": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dtca.py": "class DTcA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dtcb.py": "class DTcB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from typing import TYPE_CHECKING\nfrom products.r4_d_tc.backend.temporal.dtca import DTcA\nfrom products.r4_d_tc.backend.temporal.dtcb import DTcB\n\nWORKFLOWS = [DTcA]\nif TYPE_CHECKING:\n    WORKFLOWS.append(DTcB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_tc.backend.facade.temporal import WORKFLOWS as R4_D_TC_WORKFLOWS\n\nQUEUES_R4_D_TC = [("r4_d_tc", R4_D_TC_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dtca.py", "backend/temporal/dtcb.py"],
        "outcome": "unread",
        "note": "if TYPE_CHECKING: W.append(B) (never runs)",
    },
    "r4_d_for": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dfora.py": "class DForA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dforb.py": "class DForB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_for.backend.temporal.dfora import DForA\nfrom products.r4_d_for.backend.temporal.dforb import DForB\n\nWORKFLOWS = [DForA]\nfor _w in [DForB]:\n    WORKFLOWS.append(_w)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_for.backend.facade.temporal import WORKFLOWS as R4_D_FOR_WORKFLOWS\n\nQUEUES_R4_D_FOR = [("r4_d_for", R4_D_FOR_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dfora.py", "backend/temporal/dforb.py"],
        "outcome": "unread",
        "note": "for w in [B]: W.append(w)",
    },
    "r4_d_with": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dwitha.py": "class DWithA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dwithb.py": "class DWithB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import contextlib\nfrom products.r4_d_with.backend.temporal.dwitha import DWithA\nfrom products.r4_d_with.backend.temporal.dwithb import DWithB\n\nWORKFLOWS = [DWithA]\nwith contextlib.suppress(Exception):\n    WORKFLOWS.append(DWithB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_with.backend.facade.temporal import WORKFLOWS as R4_D_WITH_WORKFLOWS\n\nQUEUES_R4_D_WITH = [("r4_d_with", R4_D_WITH_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dwitha.py", "backend/temporal/dwithb.py"],
        "outcome": "unread",
        "note": "with suppress(): W.append(B)",
    },
    "r4_d_match": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dmatcha.py": "class DMatchA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dmatchb.py": "class DMatchB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_match.backend.temporal.dmatcha import DMatchA\nfrom products.r4_d_match.backend.temporal.dmatchb import DMatchB\n\nWORKFLOWS = [DMatchA]\nmatch 1:\n    case _:\n        WORKFLOWS.append(DMatchB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_match.backend.facade.temporal import WORKFLOWS as R4_D_MATCH_WORKFLOWS\n\nQUEUES_R4_D_MATCH = [("r4_d_match", R4_D_MATCH_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dmatcha.py", "backend/temporal/dmatchb.py"],
        "outcome": "unread",
        "note": "match: case _: W.append(B)",
    },
    "r4_d_alias": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/daliasa.py": "class DAliasA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/daliasb.py": "class DAliasB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_alias.backend.temporal.daliasa import DAliasA\nfrom products.r4_d_alias.backend.temporal.daliasb import DAliasB\n\nWORKFLOWS = [DAliasA]\n_ALL = WORKFLOWS\n_ALL.append(DAliasB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_alias.backend.facade.temporal import WORKFLOWS as R4_D_ALIAS_WORKFLOWS\n\nQUEUES_R4_D_ALIAS = [("r4_d_alias", R4_D_ALIAS_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/daliasa.py", "backend/temporal/daliasb.py"],
        "outcome": "unread",
        "note": "alias: _ALL = W; _ALL.append(B)",
    },
    "r4_d_alias_iadd": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/daliasiadda.py": "class DAliasIaddA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/daliasiaddb.py": "class DAliasIaddB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_alias_iadd.backend.temporal.daliasiadda import DAliasIaddA\nfrom products.r4_d_alias_iadd.backend.temporal.daliasiaddb import DAliasIaddB\n\nWORKFLOWS = [DAliasIaddA]\n_ALL = WORKFLOWS\n_ALL += [DAliasIaddB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_alias_iadd.backend.facade.temporal import WORKFLOWS as R4_D_ALIAS_IADD_WORKFLOWS\n\nQUEUES_R4_D_ALIAS_IADD = [("r4_d_alias_iadd", R4_D_ALIAS_IADD_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/daliasiadda.py", "backend/temporal/daliasiaddb.py"],
        "outcome": "unread",
        "note": "alias: _ALL = W; _ALL += [B] (in place)",
    },
    "r4_d_setdefault": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dsetdefaulta.py": "class DSetdefaultA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dsetdefaultb.py": "class DSetdefaultB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_d_setdefault.backend.temporal.dsetdefaulta import DSetdefaultA\nfrom products.r4_d_setdefault.backend.temporal.dsetdefaultb import DSetdefaultB\n\nWORKFLOWS = {"a": DSetdefaultA}\nWORKFLOWS.setdefault("b", DSetdefaultB)\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_setdefault.backend.facade.temporal import WORKFLOWS as R4_D_SETDEFAULT_WORKFLOWS\n\nQUEUES_R4_D_SETDEFAULT = [("r4_d_setdefault", R4_D_SETDEFAULT_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dsetdefaulta.py", "backend/temporal/dsetdefaultb.py"],
        "outcome": "unread",
        "note": "dict W.setdefault('b', B)",
    },
    "r4_d_ior": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/diora.py": "class DIorA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/diorb.py": "class DIorB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_d_ior.backend.temporal.diora import DIorA\nfrom products.r4_d_ior.backend.temporal.diorb import DIorB\n\nWORKFLOWS = {"a": DIorA}\nWORKFLOWS |= {"b": DIorB}\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_ior.backend.facade.temporal import WORKFLOWS as R4_D_IOR_WORKFLOWS\n\nQUEUES_R4_D_IOR = [("r4_d_ior", R4_D_IOR_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/diora.py", "backend/temporal/diorb.py"],
        "outcome": "watched",
        "note": "dict W |= {'b': B}",
    },
    "r4_d_setadd": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dsetadda.py": "class DSetaddA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dsetaddb.py": "class DSetaddB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_setadd.backend.temporal.dsetadda import DSetaddA\nfrom products.r4_d_setadd.backend.temporal.dsetaddb import DSetaddB\n\nWORKFLOWS = DSetaddA\nWORKFLOWS.add(DSetaddB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_setadd.backend.facade.temporal import WORKFLOWS as R4_D_SETADD_WORKFLOWS\n\nQUEUES_R4_D_SETADD = [("r4_d_setadd", R4_D_SETADD_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dsetadda.py", "backend/temporal/dsetaddb.py"],
        "outcome": "watched",
        "note": "set W.add(B)",
    },
    "r4_d_extend_u": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dextendua.py": "class DExtendUA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dextendub.py": "class DExtendUB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_extend_u.backend.temporal.dextendua import DExtendUA\nfrom products.r4_d_extend_u.backend.temporal.dextendub import DExtendUB\n\ndef _more():\n    return [DExtendUB]\n\n\nWORKFLOWS = [DExtendUA]\nWORKFLOWS.extend(_more())\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_extend_u.backend.facade.temporal import WORKFLOWS as R4_D_EXTEND_U_WORKFLOWS\n\nQUEUES_R4_D_EXTEND_U = [("r4_d_extend_u", R4_D_EXTEND_U_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dextendua.py", "backend/temporal/dextendub.py"],
        "outcome": "kept",
        "why": "class (a): a call of a function the product defines",
        "note": "W.extend(_more()) (a first-party call)",
    },
    "r4_d_fn": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dfna.py": "class DFnA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dfnb.py": "class DFnB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_fn.backend.temporal.dfna import DFnA\nfrom products.r4_d_fn.backend.temporal.dfnb import DFnB\n\nWORKFLOWS = [DFnA]\n\n\ndef _add():\n    WORKFLOWS.append(DFnB)\n\n\n_add()\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_fn.backend.facade.temporal import WORKFLOWS as R4_D_FN_WORKFLOWS\n\nQUEUES_R4_D_FN = [("r4_d_fn", R4_D_FN_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dfna.py", "backend/temporal/dfnb.py"],
        "outcome": "kept",
        "why": "limit 1: a function changes the list",
        "note": "W.append(B) inside a function called at module level (limit 1)",
    },
    "r4_d_imported": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dimporteda.py": "class DImportedA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dimportedb.py": "class DImportedB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_imported.backend.temporal.dimporteda import DImportedA\nfrom products.r4_d_imported.backend.temporal.dimportedb import DImportedB\n\nfrom products.r4_d_imported.backend.temporal.catalog import WORKFLOWS\n\nWORKFLOWS.append(DImportedB)\n",
            "backend/temporal/catalog.py": "from products.r4_d_imported.backend.temporal.dimporteda import DImportedA\n\nWORKFLOWS = [DImportedA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_imported.backend.facade.temporal import WORKFLOWS as R4_D_IMPORTED_WORKFLOWS\n\nQUEUES_R4_D_IMPORTED = [("r4_d_imported", R4_D_IMPORTED_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dimporteda.py", "backend/temporal/dimportedb.py"],
        "outcome": "kept",
        "why": "limit 1: another module's list changed here",
        "note": "from catalog import W; W.append(B) (limit 1)",
    },
    "r4_d_dunder_iadd": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ddunderiadda.py": "class DDunderIaddA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/ddunderiaddb.py": "class DDunderIaddB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_dunder_iadd.backend.temporal.ddunderiadda import DDunderIaddA\nfrom products.r4_d_dunder_iadd.backend.temporal.ddunderiaddb import DDunderIaddB\n\nWORKFLOWS = [DDunderIaddA]\nWORKFLOWS.__iadd__([DDunderIaddB])\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_dunder_iadd.backend.facade.temporal import WORKFLOWS as R4_D_DUNDER_IADD_WORKFLOWS\n\nQUEUES_R4_D_DUNDER_IADD = [("r4_d_dunder_iadd", R4_D_DUNDER_IADD_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ddunderiadda.py", "backend/temporal/ddunderiaddb.py"],
        "outcome": "unread",
        "note": "W.__iadd__([B])",
    },
    "r4_d_setlit": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dsetlita.py": "class DSetlitA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_setlit.backend.temporal.dsetlita import DSetlitA\n\nWORKFLOWS = DSetlitA\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_setlit.backend.facade.temporal import WORKFLOWS as R4_D_SETLIT_WORKFLOWS\n\nQUEUES_R4_D_SETLIT = [("r4_d_setlit", R4_D_SETLIT_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dsetlita.py"],
        "outcome": "watched",
        "note": "control: set literal W = {A}",
    },
    "r4_d_setlit_ior": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dsetlitiora.py": "class DSetlitIorA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dsetlitiorb.py": "class DSetlitIorB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_setlit_ior.backend.temporal.dsetlitiora import DSetlitIorA\nfrom products.r4_d_setlit_ior.backend.temporal.dsetlitiorb import DSetlitIorB\n\nWORKFLOWS = DSetlitIorA\nWORKFLOWS |= DSetlitIorB\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_setlit_ior.backend.facade.temporal import WORKFLOWS as R4_D_SETLIT_IOR_WORKFLOWS\n\nQUEUES_R4_D_SETLIT_IOR = [("r4_d_setlit_ior", R4_D_SETLIT_IOR_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dsetlitiora.py", "backend/temporal/dsetlitiorb.py"],
        "outcome": "watched",
        "note": "set W |= {B}",
    },
    "r4_d_dictlit_upd": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ddictlitupda.py": "class DDictlitUpdA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/ddictlitupdb.py": "class DDictlitUpdB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_d_dictlit_upd.backend.temporal.ddictlitupda import DDictlitUpdA\nfrom products.r4_d_dictlit_upd.backend.temporal.ddictlitupdb import DDictlitUpdB\n\nWORKFLOWS = {"a": DDictlitUpdA}\nWORKFLOWS.update(b=DDictlitUpdB)\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_dictlit_upd.backend.facade.temporal import WORKFLOWS as R4_D_DICTLIT_UPD_WORKFLOWS\n\nQUEUES_R4_D_DICTLIT_UPD = [("r4_d_dictlit_upd", R4_D_DICTLIT_UPD_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ddictlitupda.py", "backend/temporal/ddictlitupdb.py"],
        "outcome": "watched",
        "note": "dict W.update(b=B) (keyword form)",
    },
    "r4_d_setitem_idx": {
        "group": "d",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/dsetitemidxa.py": "class DSetitemIdxA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/dsetitemidxb.py": "class DSetitemIdxB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_d_setitem_idx.backend.temporal.dsetitemidxa import DSetitemIdxA\nfrom products.r4_d_setitem_idx.backend.temporal.dsetitemidxb import DSetitemIdxB\n\nWORKFLOWS = [DSetitemIdxA]\nWORKFLOWS[0] = DSetitemIdxB\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_d_setitem_idx.backend.facade.temporal import WORKFLOWS as R4_D_SETITEM_IDX_WORKFLOWS\n\nQUEUES_R4_D_SETITEM_IDX = [("r4_d_setitem_idx", R4_D_SETITEM_IDX_WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/dsetitemidxa.py", "backend/temporal/dsetitemidxb.py"],
        "outcome": "watched",
        "note": "W[0] = B",
    },
    "r4_e_override": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/eoverridea.py": "class EOverrideA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/eoverrideb.py": "class EOverrideB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_override.backend.temporal.eoverridea import EOverrideA\nfrom products.r4_e_override.backend.temporal.eoverrideb import EOverrideB\n\nclass Base:\n    WORKFLOWS = [EOverrideA]\n\n\nclass Registration(Base):\n    WORKFLOWS = Base.WORKFLOWS + [EOverrideB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_override.backend.facade.temporal import Registration as R4_E_OVERRIDE_C\nQUEUES_R4_E_OVERRIDE = [("r4_e_override", R4_E_OVERRIDE_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/eoverridea.py", "backend/temporal/eoverrideb.py"],
        "outcome": "watched",
        "note": "subclass overrides: W = Base.W + [B]",
    },
    "r4_e_override_star": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/eoverridestara.py": "class EOverrideStarA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/eoverridestarb.py": "class EOverrideStarB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_override_star.backend.temporal.eoverridestara import EOverrideStarA\nfrom products.r4_e_override_star.backend.temporal.eoverridestarb import EOverrideStarB\n\nclass Base:\n    WORKFLOWS = [EOverrideStarA]\n\n\nclass Registration(Base):\n    WORKFLOWS = [*Base.WORKFLOWS, EOverrideStarB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_override_star.backend.facade.temporal import Registration as R4_E_OVERRIDE_STAR_C\nQUEUES_R4_E_OVERRIDE_STAR = [("r4_e_override_star", R4_E_OVERRIDE_STAR_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/eoverridestara.py", "backend/temporal/eoverridestarb.py"],
        "outcome": "watched",
        "note": "subclass overrides: W = [*Base.W, B]",
    },
    "r4_e_inherit": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/einherita.py": "class EInheritA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_inherit.backend.temporal.einherita import EInheritA\n\nclass Base:\n    WORKFLOWS = [EInheritA]\n\n\nclass Registration(Base):\n    pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_inherit.backend.facade.temporal import Registration as R4_E_INHERIT_C\nQUEUES_R4_E_INHERIT = [("r4_e_inherit", R4_E_INHERIT_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/einherita.py"],
        "outcome": "kept",
        "why": "limit 5: the class body does not bind the attribute",
        "note": "subclass inherits W (body binds nothing)",
    },
    "r4_e_inherit_mod": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/einheritmoda.py": "class EInheritModA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_inherit_mod.backend.temporal.einheritmoda import EInheritModA\n\nfrom products.r4_e_inherit_mod.backend.temporal.base import Base\n\n\nclass Registration(Base):\n    pass\n",
            "backend/temporal/base.py": "from products.r4_e_inherit_mod.backend.temporal.einheritmoda import EInheritModA\n\n\nclass Base:\n    WORKFLOWS = [EInheritModA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_inherit_mod.backend.facade.temporal import Registration as R4_E_INHERIT_MOD_C\nQUEUES_R4_E_INHERIT_MOD = [("r4_e_inherit_mod", R4_E_INHERIT_MOD_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/einheritmoda.py"],
        "outcome": "kept",
        "why": "limit 5: the class body does not bind the attribute",
        "note": "subclass inherits W from a base in another module",
    },
    "r4_e_base_read_sub": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ebasereadsuba.py": "class EBaseReadSubA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/ebasereadsubb.py": "class EBaseReadSubB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_base_read_sub.backend.temporal.ebasereadsuba import EBaseReadSubA\nfrom products.r4_e_base_read_sub.backend.temporal.ebasereadsubb import EBaseReadSubB\n\nclass Registration:\n    WORKFLOWS = [EBaseReadSubA]\n\n\nclass Sub(Registration):\n    WORKFLOWS = [EBaseReadSubB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_base_read_sub.backend.facade.temporal import Registration as R4_E_BASE_READ_SUB_C\nQUEUES_R4_E_BASE_READ_SUB = [("r4_e_base_read_sub", R4_E_BASE_READ_SUB_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ebasereadsuba.py", "backend/temporal/ebasereadsubb.py"],
        "outcome": "kept",
        "why": "core reads the base; the subclass's item is not registered",
        "note": "core reads the base; a subclass overrides (B over-watched)",
    },
    "r4_e_property": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/epropertya.py": "class EPropertyA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_property.backend.temporal.epropertya import EPropertyA\n\nclass Registration:\n    @property\n    def WORKFLOWS(self):\n        return [EPropertyA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_property.backend.facade.temporal import Registration as R4_E_PROPERTY_C\nQUEUES_R4_E_PROPERTY = [("r4_e_property", R4_E_PROPERTY_C().WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/epropertya.py"],
        "outcome": "unread",
        "note": "property on an instance",
    },
    "r4_e_classmethod": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/eclassmethoda.py": "class EClassmethodA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_classmethod.backend.temporal.eclassmethoda import EClassmethodA\n\nclass Registration:\n    @classmethod\n    def workflows(cls):\n        return [EClassmethodA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_classmethod.backend.facade.temporal import Registration as R4_E_CLASSMETHOD_C\nQUEUES_R4_E_CLASSMETHOD = [("r4_e_classmethod", R4_E_CLASSMETHOD_C.workflows(), [])]\n'
        },
        "probes": ["backend/temporal/eclassmethoda.py"],
        "outcome": "kept",
        "why": "class (a): a call of a function the product defines",
        "note": "classmethod returns the list",
    },
    "r4_e_staticmethod": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/estaticmethoda.py": "class EStaticmethodA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_staticmethod.backend.temporal.estaticmethoda import EStaticmethodA\n\nclass Registration:\n    @staticmethod\n    def workflows():\n        return [EStaticmethodA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_staticmethod.backend.facade.temporal import Registration as R4_E_STATICMETHOD_C\nQUEUES_R4_E_STATICMETHOD = [("r4_e_staticmethod", R4_E_STATICMETHOD_C.workflows(), [])]\n'
        },
        "probes": ["backend/temporal/estaticmethoda.py"],
        "outcome": "kept",
        "why": "class (a): a call of a function the product defines",
        "note": "staticmethod returns the list",
    },
    "r4_e_metaprop": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/emetapropa.py": "class EMetapropA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_metaprop.backend.temporal.emetapropa import EMetapropA\n\nclass _Meta(type):\n    @property\n    def WORKFLOWS(cls):\n        return [EMetapropA]\n\n\nclass Registration(metaclass=_Meta):\n    pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_metaprop.backend.facade.temporal import Registration as R4_E_METAPROP_C\nQUEUES_R4_E_METAPROP = [("r4_e_metaprop", R4_E_METAPROP_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/emetapropa.py"],
        "outcome": "kept",
        "why": "limit 5: the class body does not bind the attribute",
        "note": "metaclass property W",
    },
    "r4_e_metaattr": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/emetaattra.py": "class EMetaattrA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_metaattr.backend.temporal.emetaattra import EMetaattrA\n\nclass _Meta(type):\n    WORKFLOWS = [EMetaattrA]\n\n\nclass Registration(metaclass=_Meta):\n    pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_metaattr.backend.facade.temporal import Registration as R4_E_METAATTR_C\nQUEUES_R4_E_METAATTR = [("r4_e_metaattr", R4_E_METAATTR_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/emetaattra.py"],
        "outcome": "kept",
        "why": "limit 5: the class body does not bind the attribute",
        "note": "metaclass attribute W",
    },
    "r4_e_init_subclass": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/einitsubclassa.py": "from products.r4_e_init_subclass.backend.temporal.registry import Base\n\n\nclass EInitSubclassA(Base):\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_init_subclass.backend.temporal.einitsubclassa import EInitSubclassA\n\nfrom products.r4_e_init_subclass.backend.temporal.registry import Base\nfrom products.r4_e_init_subclass.backend.temporal import einitsubclassa  # noqa: F401\n\n\nclass Registration:\n    WORKFLOWS = Base.WORKFLOWS\n",
            "backend/temporal/registry.py": "class Base:\n    WORKFLOWS: list = []\n\n    def __init_subclass__(cls, **kw):\n        super().__init_subclass__(**kw)\n        Base.WORKFLOWS.append(cls)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_init_subclass.backend.facade.temporal import Registration as R4_E_INIT_SUBCLASS_C\nQUEUES_R4_E_INIT_SUBCLASS = [("r4_e_init_subclass", R4_E_INIT_SUBCLASS_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/einitsubclassa.py"],
        "outcome": "kept",
        "why": "limit 1: a function fills the list",
        "note": "__init_subclass__ registry: Base.W filled by subclasses",
    },
    "r4_e_init_subclass_mod": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/einitsubclassmoda.py": "from products.r4_e_init_subclass_mod.backend.temporal.registry import Base\n\n\nclass EInitSubclassModA(Base):\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_init_subclass_mod.backend.temporal.einitsubclassmoda import EInitSubclassModA\n\nfrom products.r4_e_init_subclass_mod.backend.temporal.registry import Base\nfrom products.r4_e_init_subclass_mod.backend.temporal import einitsubclassmoda  # noqa: F401\n\nWORKFLOWS = Base.WORKFLOWS\n",
            "backend/temporal/registry.py": "class Base:\n    WORKFLOWS: list = []\n\n    def __init_subclass__(cls, **kw):\n        super().__init_subclass__(**kw)\n        Base.WORKFLOWS.append(cls)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_init_subclass_mod.backend.facade.temporal import WORKFLOWS as R4_E_INIT_SUBCLASS_MOD_C\nQUEUES_R4_E_INIT_SUBCLASS_MOD = [("r4_e_init_subclass_mod", R4_E_INIT_SUBCLASS_MOD_C, [])]\n'
        },
        "probes": ["backend/temporal/einitsubclassmoda.py"],
        "outcome": "kept",
        "why": "limit 1: a function fills the list",
        "note": "__init_subclass__ registry, facade W = Base.W (module list)",
    },
    "r4_e_decorator": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/edecoratora.py": "from products.r4_e_decorator.backend.temporal.registry import register\n\n\n@register\nclass EDecoratorA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_decorator.backend.temporal.edecoratora import EDecoratorA\n\nfrom products.r4_e_decorator.backend.temporal.registry import REGISTERED\nfrom products.r4_e_decorator.backend.temporal import edecoratora  # noqa: F401\n\nWORKFLOWS = REGISTERED\n",
            "backend/temporal/registry.py": "REGISTERED: list = []\n\n\ndef register(cls):\n    REGISTERED.append(cls)\n    return cls\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_decorator.backend.facade.temporal import WORKFLOWS as R4_E_DECORATOR_C\nQUEUES_R4_E_DECORATOR = [("r4_e_decorator", R4_E_DECORATOR_C, [])]\n'
        },
        "probes": ["backend/temporal/edecoratora.py"],
        "outcome": "kept",
        "why": "limit 1: a function fills the list",
        "note": "decorator registry: @register appends in a function (limit 1)",
    },
    "r4_e_classvar": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/eclassvara.py": "class EClassvarA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from typing import ClassVar\nfrom products.r4_e_classvar.backend.temporal.eclassvara import EClassvarA\n\nclass Registration:\n    WORKFLOWS: ClassVar[list] = [EClassvarA]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_classvar.backend.facade.temporal import Registration as R4_E_CLASSVAR_C\nQUEUES_R4_E_CLASSVAR = [("r4_e_classvar", R4_E_CLASSVAR_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/eclassvara.py"],
        "outcome": "watched",
        "note": "WORKFLOWS: ClassVar[list] = [A]",
    },
    "r4_e_after_body": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/eafterbodya.py": "class EAfterBodyA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/eafterbodyb.py": "class EAfterBodyB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_after_body.backend.temporal.eafterbodya import EAfterBodyA\nfrom products.r4_e_after_body.backend.temporal.eafterbodyb import EAfterBodyB\n\nclass Registration:\n    WORKFLOWS = [EAfterBodyA]\n\n\nRegistration.WORKFLOWS = Registration.WORKFLOWS + [EAfterBodyB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_after_body.backend.facade.temporal import Registration as R4_E_AFTER_BODY_C\nQUEUES_R4_E_AFTER_BODY = [("r4_e_after_body", R4_E_AFTER_BODY_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/eafterbodya.py", "backend/temporal/eafterbodyb.py"],
        "outcome": "unread",
        "note": "Registration.W = Registration.W + [B] after the body",
    },
    "r4_e_after_append": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/eafterappenda.py": "class EAfterAppendA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/eafterappendb.py": "class EAfterAppendB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_after_append.backend.temporal.eafterappenda import EAfterAppendA\nfrom products.r4_e_after_append.backend.temporal.eafterappendb import EAfterAppendB\n\nclass Registration:\n    WORKFLOWS = [EAfterAppendA]\n\n\nRegistration.WORKFLOWS.append(EAfterAppendB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_after_append.backend.facade.temporal import Registration as R4_E_AFTER_APPEND_C\nQUEUES_R4_E_AFTER_APPEND = [("r4_e_after_append", R4_E_AFTER_APPEND_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/eafterappenda.py", "backend/temporal/eafterappendb.py"],
        "outcome": "unread",
        "note": "Registration.W.append(B) at module level",
    },
    "r4_e_setattr_cls": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/esetattrclsa.py": "class ESetattrClsA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/esetattrclsb.py": "class ESetattrClsB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_e_setattr_cls.backend.temporal.esetattrclsa import ESetattrClsA\nfrom products.r4_e_setattr_cls.backend.temporal.esetattrclsb import ESetattrClsB\n\nclass Registration:\n    WORKFLOWS = [ESetattrClsA]\n\n\nsetattr(Registration, "WORKFLOWS", [ESetattrClsB])\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_setattr_cls.backend.facade.temporal import Registration as R4_E_SETATTR_CLS_C\nQUEUES_R4_E_SETATTR_CLS = [("r4_e_setattr_cls", R4_E_SETATTR_CLS_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/esetattrclsb.py"],
        "outcome": "unread",
        "note": "setattr(Registration, 'W', [B])",
    },
    "r4_e_enum": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/eenuma.py": "class EEnumA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/eenumb.py": "class EEnumB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from enum import Enum\nfrom products.r4_e_enum.backend.temporal.eenuma import EEnumA\nfrom products.r4_e_enum.backend.temporal.eenumb import EEnumB\n\nclass Flows(Enum):\n    A = EEnumA\n    B = EEnumB\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_enum.backend.facade.temporal import Flows as R4_E_ENUM_C\nQUEUES_R4_E_ENUM = [("r4_e_enum", [f.value for f in R4_E_ENUM_C], [])]\n'
        },
        "probes": ["backend/temporal/eenuma.py", "backend/temporal/eenumb.py"],
        "outcome": "unread",
        "note": "enum members hold the classes; core lists their values",
    },
    "r4_e_body_append": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ebodyappenda.py": "class EBodyAppendA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/ebodyappendb.py": "class EBodyAppendB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_body_append.backend.temporal.ebodyappenda import EBodyAppendA\nfrom products.r4_e_body_append.backend.temporal.ebodyappendb import EBodyAppendB\n\nclass Registration:\n    WORKFLOWS = [EBodyAppendA]\n    WORKFLOWS.append(EBodyAppendB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_body_append.backend.facade.temporal import Registration as R4_E_BODY_APPEND_C\nQUEUES_R4_E_BODY_APPEND = [("r4_e_body_append", R4_E_BODY_APPEND_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ebodyappenda.py", "backend/temporal/ebodyappendb.py"],
        "outcome": "unread",
        "note": "W.append(B) inside the class body",
    },
    "r4_e_body_rebind": {
        "group": "e",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ebodyrebinda.py": "class EBodyRebindA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/ebodyrebindb.py": "class EBodyRebindB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r4_e_body_rebind.backend.temporal.ebodyrebinda import EBodyRebindA\nfrom products.r4_e_body_rebind.backend.temporal.ebodyrebindb import EBodyRebindB\n\nclass Registration:\n    WORKFLOWS = [EBodyRebindA]\n    WORKFLOWS = WORKFLOWS + [EBodyRebindB]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": '\nfrom products.r4_e_body_rebind.backend.facade.temporal import Registration as R4_E_BODY_REBIND_C\nQUEUES_R4_E_BODY_REBIND = [("r4_e_body_rebind", R4_E_BODY_REBIND_C.WORKFLOWS, [])]\n'
        },
        "probes": ["backend/temporal/ebodyrebinda.py", "backend/temporal/ebodyrebindb.py"],
        "outcome": "watched",
        "note": "class body: W = [A]; W = W + [B]",
    },
    "r4_f_fstring": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ffstringwf.py": "class FFstringWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_fstring.backend.temporal.ffstringwf import FFstringWf\n\nWORKFLOWS = [FFstringWf]\nPREFIX = "r4"\nTASK_QUEUE = f"{PREFIX}-q"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_fstring.backend.facade.temporal import WORKFLOWS as R4_F_FSTRING_W, TASK_QUEUE as R4_F_FSTRING_K\nQUEUES_R4_F_FSTRING = [(R4_F_FSTRING_K, R4_F_FSTRING_W, [])]\n"
        },
        "probes": ["backend/temporal/ffstringwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE = f'{PREFIX}-q'",
    },
    "r4_f_settings": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fsettingswf.py": "class FSettingsWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from django.conf import settings\nfrom products.r4_f_settings.backend.temporal.fsettingswf import FSettingsWf\n\nWORKFLOWS = [FSettingsWf]\nTASK_QUEUE = settings.R4_QUEUE\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_settings.backend.facade.temporal import WORKFLOWS as R4_F_SETTINGS_W, TASK_QUEUE as R4_F_SETTINGS_K\nQUEUES_R4_F_SETTINGS = [(R4_F_SETTINGS_K, R4_F_SETTINGS_W, [])]\n"
        },
        "probes": ["backend/temporal/fsettingswf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE = settings.X",
    },
    "r4_f_settings_getattr": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fsettingsgetattrwf.py": "class FSettingsGetattrWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from django.conf import settings\nfrom products.r4_f_settings_getattr.backend.temporal.fsettingsgetattrwf import FSettingsGetattrWf\n\nWORKFLOWS = [FSettingsGetattrWf]\nTASK_QUEUE = getattr(settings, "R4_QUEUE", "q")\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_settings_getattr.backend.facade.temporal import WORKFLOWS as R4_F_SETTINGS_GETATTR_W, TASK_QUEUE as R4_F_SETTINGS_GETATTR_K\nQUEUES_R4_F_SETTINGS_GETATTR = [(R4_F_SETTINGS_GETATTR_K, R4_F_SETTINGS_GETATTR_W, [])]\n"
        },
        "probes": ["backend/temporal/fsettingsgetattrwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE = getattr(settings, 'X', 'q')",
    },
    "r4_f_cond": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fcondwf.py": "class FCondWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_cond.backend.temporal.fcondwf import FCondWf\n\nWORKFLOWS = [FCondWf]\nDEBUG = False\nTASK_QUEUE = "a" if DEBUG else "b"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_cond.backend.facade.temporal import WORKFLOWS as R4_F_COND_W, TASK_QUEUE as R4_F_COND_K\nQUEUES_R4_F_COND = [(R4_F_COND_K, R4_F_COND_W, [])]\n"
        },
        "probes": ["backend/temporal/fcondwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE = 'a' if DEBUG else 'b'",
    },
    "r4_f_chained": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fchainedwf.py": "class FChainedWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_chained.backend.temporal.fchainedwf import FChainedWf\n\nWORKFLOWS = [FChainedWf]\nTASK_QUEUE = _Q = "q"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_chained.backend.facade.temporal import WORKFLOWS as R4_F_CHAINED_W, TASK_QUEUE as R4_F_CHAINED_K\nQUEUES_R4_F_CHAINED = [(R4_F_CHAINED_K, R4_F_CHAINED_W, [])]\n"
        },
        "probes": ["backend/temporal/fchainedwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE = _Q = 'q'",
    },
    "r4_f_starunpack": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fstarunpackwf.py": "class FStarunpackWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_starunpack.backend.temporal.fstarunpackwf import FStarunpackWf\n\nWORKFLOWS = [FStarunpackWf]\nTASK_QUEUE, *_REST = "q", "x", "y"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_starunpack.backend.facade.temporal import WORKFLOWS as R4_F_STARUNPACK_W, TASK_QUEUE as R4_F_STARUNPACK_K\nQUEUES_R4_F_STARUNPACK = [(R4_F_STARUNPACK_K, R4_F_STARUNPACK_W, [])]\n"
        },
        "probes": ["backend/temporal/fstarunpackwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE, *REST = 'q', 'x', 'y'",
    },
    "r4_f_or": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/forwf.py": "class FOrWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'import os\nfrom products.r4_f_or.backend.temporal.forwf import FOrWf\n\nWORKFLOWS = [FOrWf]\nTASK_QUEUE = os.environ.get("R4_Q") or "q"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_or.backend.facade.temporal import WORKFLOWS as R4_F_OR_W, TASK_QUEUE as R4_F_OR_K\nQUEUES_R4_F_OR = [(R4_F_OR_K, R4_F_OR_W, [])]\n"
        },
        "probes": ["backend/temporal/forwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE = os.environ.get('X') or 'q'",
    },
    "r4_f_enum": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fenumwf.py": "class FEnumWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from enum import StrEnum\nfrom products.r4_f_enum.backend.temporal.fenumwf import FEnumWf\n\nWORKFLOWS = [FEnumWf]\nclass Queue(StrEnum):\n    MAIN = "main"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_enum.backend.facade.temporal import WORKFLOWS as R4_F_ENUM_W, Queue as R4_F_ENUM_K\nQUEUES_R4_F_ENUM = [(R4_F_ENUM_K, R4_F_ENUM_W, [])]\n"
        },
        "probes": ["backend/temporal/fenumwf.py"],
        "outcome": "watched",
        "note": "class Queue(StrEnum); core uses Queue.MAIN",
    },
    "r4_f_enumval": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fenumvalwf.py": "class FEnumvalWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from enum import StrEnum\nfrom products.r4_f_enumval.backend.temporal.fenumvalwf import FEnumvalWf\n\nWORKFLOWS = [FEnumvalWf]\nclass _Queue(StrEnum):\n    MAIN = "main"\n\n\nTASK_QUEUE = _Queue.MAIN.value\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_enumval.backend.facade.temporal import WORKFLOWS as R4_F_ENUMVAL_W, TASK_QUEUE as R4_F_ENUMVAL_K\nQUEUES_R4_F_ENUMVAL = [(R4_F_ENUMVAL_K, R4_F_ENUMVAL_W, [])]\n"
        },
        "probes": ["backend/temporal/fenumvalwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE = Queue.MAIN.value",
    },
    "r4_f_cfg_inst": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fcfginstwf.py": "class FCfgInstWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from dataclasses import dataclass\nfrom products.r4_f_cfg_inst.backend.temporal.fcfginstwf import FCfgInstWf\n\nWORKFLOWS = [FCfgInstWf]\n@dataclass(frozen=True)\nclass Cfg:\n    queue: str = "q"\n\n\nCFG = Cfg()\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_cfg_inst.backend.facade.temporal import WORKFLOWS as R4_F_CFG_INST_W, CFG as R4_F_CFG_INST_K\nQUEUES_R4_F_CFG_INST = [(R4_F_CFG_INST_K, R4_F_CFG_INST_W, [])]\n"
        },
        "probes": ["backend/temporal/fcfginstwf.py"],
        "outcome": "watched",
        "note": "CFG = Cfg() (a first-party dataclass instance)",
    },
    "r4_f_dict_mut": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fdictmutwf.py": "class FDictMutWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_dict_mut.backend.temporal.fdictmutwf import FDictMutWf\n\nWORKFLOWS = [FDictMutWf]\nSETTINGS = {"a": 1}\nSETTINGS["b"] = 2\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_dict_mut.backend.facade.temporal import WORKFLOWS as R4_F_DICT_MUT_W, SETTINGS as R4_F_DICT_MUT_K\nQUEUES_R4_F_DICT_MUT = [(R4_F_DICT_MUT_K, R4_F_DICT_MUT_W, [])]\n"
        },
        "probes": ["backend/temporal/fdictmutwf.py"],
        "outcome": "watched",
        "note": "SETTINGS = {...}; SETTINGS['b'] = 2",
    },
    "r4_f_list_mut": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/flistmutwf.py": "class FListMutWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_list_mut.backend.temporal.flistmutwf import FListMutWf\n\nWORKFLOWS = [FListMutWf]\nNAMES = []\nNAMES.append("x")\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_list_mut.backend.facade.temporal import WORKFLOWS as R4_F_LIST_MUT_W, NAMES as R4_F_LIST_MUT_K\nQUEUES_R4_F_LIST_MUT = [(R4_F_LIST_MUT_K, R4_F_LIST_MUT_W, [])]\n"
        },
        "probes": ["backend/temporal/flistmutwf.py"],
        "outcome": "watched",
        "note": "NAMES = []; NAMES.append('x')",
    },
    "r4_f_update_mut": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fupdatemutwf.py": "class FUpdateMutWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from datetime import timedelta\nfrom products.r4_f_update_mut.backend.temporal.fupdatemutwf import FUpdateMutWf\n\nWORKFLOWS = [FUpdateMutWf]\nOPTS = {}\nOPTS.update(timeout=timedelta(seconds=3))\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_update_mut.backend.facade.temporal import WORKFLOWS as R4_F_UPDATE_MUT_W, OPTS as R4_F_UPDATE_MUT_K\nQUEUES_R4_F_UPDATE_MUT = [(R4_F_UPDATE_MUT_K, R4_F_UPDATE_MUT_W, [])]\n"
        },
        "probes": ["backend/temporal/fupdatemutwf.py"],
        "outcome": "watched",
        "note": "OPTS = {}; OPTS.update(timeout=timedelta(...))",
    },
    "r4_f_aug_const": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/faugconstwf.py": "class FAugConstWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_aug_const.backend.temporal.faugconstwf import FAugConstWf\n\nWORKFLOWS = [FAugConstWf]\nTASK_QUEUE = "a"\nTASK_QUEUE += "-b"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_aug_const.backend.facade.temporal import WORKFLOWS as R4_F_AUG_CONST_W, TASK_QUEUE as R4_F_AUG_CONST_K\nQUEUES_R4_F_AUG_CONST = [(R4_F_AUG_CONST_K, R4_F_AUG_CONST_W, [])]\n"
        },
        "probes": ["backend/temporal/faugconstwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE = 'a'; TASK_QUEUE += '-b'",
    },
    "r4_f_final": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ffinalwf.py": "class FFinalWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from typing import Final\nfrom products.r4_f_final.backend.temporal.ffinalwf import FFinalWf\n\nWORKFLOWS = [FFinalWf]\nTASK_QUEUE: Final = "q"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_final.backend.facade.temporal import WORKFLOWS as R4_F_FINAL_W, TASK_QUEUE as R4_F_FINAL_K\nQUEUES_R4_F_FINAL = [(R4_F_FINAL_K, R4_F_FINAL_W, [])]\n"
        },
        "probes": ["backend/temporal/ffinalwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE: Final = 'q'",
    },
    "r4_f_tuple_consts": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/ftupleconstswf.py": "class FTupleConstsWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_tuple_consts.backend.temporal.ftupleconstswf import FTupleConstsWf\n\nWORKFLOWS = [FTupleConstsWf]\nQUEUE_NAMES = ("a", "b")\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_tuple_consts.backend.facade.temporal import WORKFLOWS as R4_F_TUPLE_CONSTS_W, QUEUE_NAMES as R4_F_TUPLE_CONSTS_K\nQUEUES_R4_F_TUPLE_CONSTS = [(R4_F_TUPLE_CONSTS_K, R4_F_TUPLE_CONSTS_W, [])]\n"
        },
        "probes": ["backend/temporal/ftupleconstswf.py"],
        "outcome": "watched",
        "note": "QUEUE_NAMES = ('a', 'b')",
    },
    "r4_f_retry": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fretrywf.py": "class FRetryWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from datetime import timedelta\n\nfrom temporalio.common import RetryPolicy\nfrom products.r4_f_retry.backend.temporal.fretrywf import FRetryWf\n\nWORKFLOWS = [FRetryWf]\nRETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1))\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_retry.backend.facade.temporal import WORKFLOWS as R4_F_RETRY_W, RETRY as R4_F_RETRY_K\nQUEUES_R4_F_RETRY = [(R4_F_RETRY_K, R4_F_RETRY_W, [])]\n"
        },
        "probes": ["backend/temporal/fretrywf.py"],
        "outcome": "watched",
        "note": "RETRY = RetryPolicy(...) (third-party class)",
    },
    "r4_f_global_const": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fglobalconstwf.py": "class FGlobalConstWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_global_const.backend.temporal.fglobalconstwf import FGlobalConstWf\n\nWORKFLOWS = [FGlobalConstWf]\nTASK_QUEUE = "a"\n\n\ndef _set():\n    global TASK_QUEUE\n    TASK_QUEUE = "b"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_global_const.backend.facade.temporal import WORKFLOWS as R4_F_GLOBAL_CONST_W, TASK_QUEUE as R4_F_GLOBAL_CONST_K\nQUEUES_R4_F_GLOBAL_CONST = [(R4_F_GLOBAL_CONST_K, R4_F_GLOBAL_CONST_W, [])]\n"
        },
        "probes": ["backend/temporal/fglobalconstwf.py"],
        "outcome": "watched",
        "note": "TASK_QUEUE rebound by global to a constant",
    },
    "r4_f_slice_const": {
        "group": "f",
        "files": {
            "backend/temporal/__init__.py": "",
            "backend/temporal/fsliceconstwf.py": "class FSliceConstWf:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": 'from products.r4_f_slice_const.backend.temporal.fsliceconstwf import FSliceConstWf\n\nWORKFLOWS = [FSliceConstWf]\nNAMES = ["a"]\nNAMES[:] = ["b"]\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\nfrom products.r4_f_slice_const.backend.facade.temporal import WORKFLOWS as R4_F_SLICE_CONST_W, NAMES as R4_F_SLICE_CONST_K\nQUEUES_R4_F_SLICE_CONST = [(R4_F_SLICE_CONST_K, R4_F_SLICE_CONST_W, [])]\n"
        },
        "probes": ["backend/temporal/fsliceconstwf.py"],
        "outcome": "watched",
        "note": "NAMES[:] = ['b'] (constants)",
    },
}
