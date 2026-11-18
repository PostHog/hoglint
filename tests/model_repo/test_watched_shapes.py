"""`hoglint watched` on the shapes a registered list or a declaration takes (CASES.md `WA08` to
`WA13`): one small product per shape, laid over a copy of the model repository with the preset in
the derived form, in one run. Each product's probe files define what core runs: each is watched, or
the product cannot pass because the link cannot read an item. Unknown is never clean. An unread
item of a core list a registration point registers is a problem of the whole answer, in a run of
its own."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from model_repo.harness import copy_tree
from model_repo.test_watched import lay_derived, watched

CLASS = "class {name}:\n    def run(self, x):\n        return x\n"
FUNCTION = "def {name}(x, mode=None):\n    return x\n"
SKELETON = {
    "__init__.py": "",
    "product.yaml": "name: {p}\nisolated: true\n",
    "backend/__init__.py": "",
    "backend/facade/__init__.py": "",
    "backend/facade/contracts.py": (
        "from posthog.dataclasses import frozen\n\n\n@frozen\nclass Info:\n    team_id: int\n"
    ),
    "backend/facade/api.py": (
        "from products.{p}.backend.facade.contracts import Info\n\n\n"
        "def info(team_id: int) -> Info:\n    return Info(team_id=team_id)\n"
    ),
    "backend/temporal/__init__.py": "",
}
WORKER = "posthog/management/commands/start_temporal_worker.py"
RECORDS = "posthog/temporal/common/registration.py"
RECORD_CLASSES = (
    "class QueueBinding:\n"
    "    def __init__(self, queue_setting, workflows=(), activities=()):\n"
    "        self.queue_setting = queue_setting\n\n\n"
    "class TemporalRegistration:\n"
    "    def __init__(self, queues=(), schedules=(), log_sources=()):\n"
    "        self.queues = queues\n"
)
RECORD_IMPORT = "from posthog.temporal.common.registration import QueueBinding, TemporalRegistration\n"
TEMPORAL = "products.{p}.backend.temporal"


def registered(p: str) -> str:
    """The worker registers the list the facade hands out."""
    return f'\nfrom products.{p}.backend.facade.temporal import WORKFLOWS as {p.upper()}_WORKFLOWS\n\n{p.upper()}_QUEUES = [("{p}", {p.upper()}_WORKFLOWS)]\n'


# Each shape: product files (relative to products/<p>/; `{p}` is the product), what core appends
# (path -> text), the probe files core runs, and whether the product cannot pass (unread).
SHAPES: dict[str, dict[str, Any]] = {
    # WA08: a list a registration point registers.
    "lists_literal": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="LiteralWorkflow"),
            "backend/facade/temporal.py": f"from {TEMPORAL}.flows import LiteralWorkflow\n\nWORKFLOWS = [LiteralWorkflow]\n",
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "lists_comprehension": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="CompWorkflow"),
            "backend/facade/temporal.py": (
                f"from {TEMPORAL}.flows import CompWorkflow\n\nWORKFLOWS = [w for w in (CompWorkflow,) if w]\n"
            ),
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "lists_chain": {
        "files": {
            "backend/temporal/first.py": CLASS.format(name="FirstWorkflow") + "\nFIRST = [FirstWorkflow]\n",
            "backend/temporal/second.py": CLASS.format(name="SecondWorkflow") + "\nSECOND = [SecondWorkflow]\n",
            "backend/facade/temporal.py": (
                f"import itertools\n\nfrom {TEMPORAL}.first import FIRST\nfrom {TEMPORAL}.second import SECOND\n\n"
                "WORKFLOWS = list(itertools.chain(FIRST, SECOND))\n"
            ),
        },
        "probes": ["backend/temporal/first.py", "backend/temporal/second.py"],
    },
    "lists_bare_chain": {
        "files": {
            "backend/temporal/first.py": CLASS.format(name="BareWorkflow") + "\nFIRST = [BareWorkflow]\n",
            "backend/facade/temporal.py": (
                f"from itertools import chain\n\nfrom {TEMPORAL}.first import FIRST\n\nWORKFLOWS = chain(FIRST, ())\n"
            ),
        },
        "probes": ["backend/temporal/first.py"],
    },
    "lists_conditional": {
        "files": {
            "backend/temporal/a.py": CLASS.format(name="CondA"),
            "backend/temporal/b.py": CLASS.format(name="CondB"),
            "backend/facade/temporal.py": (
                f"from django.conf import settings\n\nfrom {TEMPORAL}.a import CondA\nfrom {TEMPORAL}.b import CondB\n\n"
                "WORKFLOWS = [CondA, CondB] if settings.DEBUG else [CondA]\n"
            ),
        },
        "probes": ["backend/temporal/a.py", "backend/temporal/b.py"],
    },
    "lists_partial": {
        "files": {
            "backend/temporal/acts.py": FUNCTION.format(name="run_partial"),
            "backend/facade/temporal.py": (
                f"from functools import partial\n\nfrom {TEMPORAL}.acts import run_partial\n\n"
                'WORKFLOWS = [partial(run_partial, mode="fast")]\n'
            ),
        },
        "probes": ["backend/temporal/acts.py"],
    },
    "lists_union": {
        "files": {
            "backend/temporal/a.py": CLASS.format(name="UnionA"),
            "backend/temporal/b.py": CLASS.format(name="UnionB"),
            "backend/facade/temporal.py": (
                f"from {TEMPORAL}.a import UnionA\nfrom {TEMPORAL}.b import UnionB\n\nWORKFLOWS = {{UnionA}} | {{UnionB}}\n"
            ),
        },
        "probes": ["backend/temporal/a.py", "backend/temporal/b.py"],
    },
    "lists_copy": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="CopyWorkflow") + "\nBASE = [CopyWorkflow]\n",
            "backend/facade/temporal.py": f"from {TEMPORAL}.flows import BASE\n\nWORKFLOWS = BASE.copy()\n",
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "lists_class_attribute": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="AttrWorkflow"),
            "backend/facade/temporal.py": (
                f"from {TEMPORAL}.flows import AttrWorkflow\n\n\nclass Registration:\n    WORKFLOWS = [AttrWorkflow]\n"
            ),
        },
        "core": {
            WORKER: (
                "\nfrom products.lists_class_attribute.backend.facade.temporal import Registration as AttrRegistration\n"
                '\nATTR_QUEUES = [("lists_class_attribute", AttrRegistration.WORKFLOWS)]\n'
            )
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "lists_getattr": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="GetattrWorkflow"),
            "backend/facade/temporal.py": f"from {TEMPORAL}.flows import GetattrWorkflow\n\nWORKFLOWS = [GetattrWorkflow]\n",
        },
        "core": {
            WORKER: (
                "\nfrom products.lists_getattr.backend.facade import temporal as getattr_temporal\n"
                '\nGETATTR_QUEUES = [("lists_getattr", getattr(getattr_temporal, "WORKFLOWS"))]\n'
            )
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "lists_core_hop": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="HopWorkflow"),
            "backend/facade/temporal.py": f"from {TEMPORAL}.flows import HopWorkflow\n\nWORKFLOWS = [HopWorkflow]\n",
        },
        "core": {
            "posthog/temporal/product_lists.py": (
                "from products.lists_core_hop.backend.facade.temporal import WORKFLOWS as HOP_WORKFLOWS\n\n"
                "HOP_ALL = [*HOP_WORKFLOWS]\n"
            ),
            WORKER: '\nfrom posthog.temporal.product_lists import HOP_ALL\n\nHOP_QUEUES = [("hop", HOP_ALL)]\n',
        },
        "probes": ["backend/temporal/flows.py"],
    },
    # WA09: a declaration a registry loads.
    "decl_inline": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="InlineWorkflow"),
            "backend/facade/temporal.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import InlineWorkflow\n\n"
                'TEMPORAL = TemporalRegistration(queues=(QueueBinding(queue_setting="IN", workflows=(InlineWorkflow,)),))\n'
            ),
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "decl_queues_module": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="QueuesWorkflow"),
            "backend/temporal/queues.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import QueuesWorkflow\n\n"
                'QUEUES = (QueueBinding(queue_setting="QM", workflows=(QueuesWorkflow,)),)\n'
            ),
            "backend/facade/temporal.py": (
                RECORD_IMPORT
                + f"from {TEMPORAL}.queues import QUEUES\n\nTEMPORAL = TemporalRegistration(queues=QUEUES)\n"
            ),
        },
        "probes": ["backend/temporal/queues.py", "backend/temporal/flows.py"],
    },
    "decl_queues_name": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="LocalWorkflow"),
            "backend/facade/temporal.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import LocalWorkflow\n\n"
                '_QUEUES = (QueueBinding(queue_setting="QN", workflows=(LocalWorkflow,)),)\n'
                "TEMPORAL = TemporalRegistration(queues=_QUEUES)\n"
            ),
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "decl_record_name": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="SplitWorkflow"),
            "backend/facade/temporal.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import SplitWorkflow\n\n"
                '_MAIN = QueueBinding(queue_setting="RN", workflows=(SplitWorkflow,))\n'
                "TEMPORAL = TemporalRegistration(queues=(_MAIN,))\n"
            ),
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "decl_star_args": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="StarWorkflow"),
            "backend/facade/temporal.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import StarWorkflow\n\n"
                '_ARGS = ((QueueBinding(queue_setting="SA", workflows=(StarWorkflow,)),),)\n'
                "TEMPORAL = TemporalRegistration(*_ARGS)\n"
            ),
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "decl_star_kwargs": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="KwWorkflow"),
            "backend/facade/temporal.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import KwWorkflow\n\n"
                '_KW = {"queues": (QueueBinding(queue_setting="SK", workflows=(KwWorkflow,)),)}\n'
                "TEMPORAL = TemporalRegistration(**_KW)\n"
            ),
        },
        "probes": ["backend/temporal/flows.py"],
    },
    "decl_module_attribute": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="AssignWorkflow"),
            "backend/temporal/registration.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import AssignWorkflow\n\n"
                'TEMPORAL = TemporalRegistration(queues=(QueueBinding(queue_setting="MA", workflows=(AssignWorkflow,)),))\n'
            ),
            "backend/facade/temporal.py": (
                f"from {TEMPORAL} import registration as _registration\n\nTEMPORAL = _registration.TEMPORAL\n"
            ),
        },
        "probes": ["backend/temporal/registration.py", "backend/temporal/flows.py"],
    },
    "decl_imported_name": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="NameWorkflow"),
            "backend/temporal/registration.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import NameWorkflow\n\n"
                'TEMPORAL = TemporalRegistration(queues=(QueueBinding(queue_setting="IN2", workflows=(NameWorkflow,)),))\n'
            ),
            "backend/facade/temporal.py": (
                f"from {TEMPORAL}.registration import TEMPORAL as _TEMPORAL\n\nTEMPORAL = _TEMPORAL\n"
            ),
        },
        "probes": ["backend/temporal/registration.py", "backend/temporal/flows.py"],
    },
    "decl_replace": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="ReplaceWorkflow"),
            "backend/temporal/sched.py": "async def reconcile_replace(client):\n    return client\n",
            "backend/facade/temporal.py": (
                "import dataclasses\n\n" + RECORD_IMPORT + f"from {TEMPORAL}.flows import ReplaceWorkflow\n"
                f"from {TEMPORAL}.sched import reconcile_replace\n\n"
                '_BASE = TemporalRegistration(queues=(QueueBinding(queue_setting="RP", workflows=(ReplaceWorkflow,)),))\n'
                "TEMPORAL = dataclasses.replace(_BASE, schedules=(reconcile_replace,))\n"
            ),
        },
        "probes": ["backend/temporal/flows.py", "backend/temporal/sched.py"],
    },
    # A call of a function the product defines is its callee's (class (a), unchanged): the file
    # that defines it is watched, and what it builds sits behind that file.
    "decl_function_call": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="BuiltWorkflow"),
            "backend/temporal/builder.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import BuiltWorkflow\n\n\n"
                'def build_queues():\n    return (QueueBinding(queue_setting="FC", workflows=(BuiltWorkflow,)),)\n'
            ),
            "backend/facade/temporal.py": (
                RECORD_IMPORT
                + f"from {TEMPORAL}.builder import build_queues\n\nTEMPORAL = TemporalRegistration(queues=build_queues())\n"
            ),
        },
        "probes": ["backend/temporal/builder.py"],
    },
    # WA10: what the link cannot read stays unread, so the product cannot pass.
    "unread_lazy_facade": {
        "files": {
            "backend/temporal/catalog.py": f"from {TEMPORAL}.flows import LazyWorkflow\n\nWORKFLOWS = [LazyWorkflow]\n",
            "backend/temporal/flows.py": CLASS.format(name="LazyWorkflow"),
            "backend/facade/temporal.py": (
                f"def __getattr__(name):\n    from {TEMPORAL} import catalog\n\n    return getattr(catalog, name)\n"
            ),
        },
        "unread": True,
    },
    "unread_library_call": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="ReducedWorkflow"),
            "backend/facade/temporal.py": (
                f"import functools\nimport operator\n\nfrom {TEMPORAL}.flows import ReducedWorkflow\n\n"
                "WORKFLOWS = functools.reduce(operator.add, [[ReducedWorkflow], []])\n"
            ),
        },
        "unread": True,
    },
    "unread_mapped_comprehension": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="MappedWorkflow"),
            "backend/facade/temporal.py": (
                f"from {TEMPORAL}.flows import MappedWorkflow\n\nWORKFLOWS = [w.inner for w in (MappedWorkflow,)]\n"
            ),
        },
        "unread": True,
    },
    "unread_block": {
        "files": {
            "backend/temporal/a.py": CLASS.format(name="BlockA"),
            "backend/temporal/b.py": CLASS.format(name="BlockB"),
            "backend/facade/temporal.py": (
                f"from django.conf import settings\n\nfrom {TEMPORAL}.a import BlockA\nfrom {TEMPORAL}.b import BlockB\n\n"
                "WORKFLOWS = [BlockA]\nif settings.DEBUG:\n    WORKFLOWS += [BlockB]\n"
            ),
        },
        "unread": True,
    },
    "unread_imported_declaration": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="ImportedWorkflow"),
            "backend/temporal/registration.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import ImportedWorkflow\n\n"
                'TEMPORAL = TemporalRegistration(queues=(QueueBinding(queue_setting="ID", workflows=(ImportedWorkflow,)),))\n'
            ),
            "backend/facade/temporal.py": f"from {TEMPORAL}.registration import TEMPORAL\n",
        },
        "unread": True,
    },
    "unread_generator_argument": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="GenWorkflow"),
            "backend/facade/temporal.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.flows import GenWorkflow\n\n"
                '_PAIRS = (("GEN", (GenWorkflow,)),)\n'
                "TEMPORAL = TemporalRegistration(queues=tuple(QueueBinding(q, workflows=w) for q, w in _PAIRS))\n"
            ),
        },
        "unread": True,
    },
}


def flows(*names: str) -> dict[str, str]:
    """One module per class under `backend/temporal/`, named after the class in lower case."""
    return {f"backend/temporal/{name.lower()}.py": CLASS.format(name=name) for name in names}


def facade(body: str, *names: str, head: str = "") -> dict[str, str]:
    """The facade's `temporal.py`: `head`, an import of each class from its module, then `body`."""
    imports = "".join(f"from {TEMPORAL}.{name.lower()} import {name}\n" for name in names)
    return {"backend/facade/temporal.py": head + imports + "\n" + body}


def probes(*names: str) -> list[str]:
    return [f"backend/temporal/{name.lower()}.py" for name in names]


def worker(text: str) -> dict[str, str]:
    return {WORKER: "\n" + text}


def hop(p: str, name: str) -> dict[str, Any]:
    """A product whose facade hands out `WORKFLOWS = [<name>]`, for the core-hop shapes."""
    return {**flows(name), **facade(f"WORKFLOWS = [{name}]\n", name)}


# Round 3 of the adversarial run on watched (Nov 18): WA11 lists, WA12 declarations, WA13 unread.
ROUND3: dict[str, dict[str, Any]] = {
    # WA11: a filter only drops items, so the dropped one is watched too (over-watch, never a miss).
    "lists_comp_filter": {
        "files": {
            **flows("KeepWf", "DropWf"),
            **facade("_ALL = [KeepWf, DropWf]\nWORKFLOWS = [w for w in _ALL if w is not DropWf]\n", "KeepWf", "DropWf"),
        },
        "probes": probes("KeepWf", "DropWf"),
    },
    "lists_values": {
        "files": {
            **flows("ValWf"),
            **facade('_BY_NAME = {"v": ValWf}\nWORKFLOWS = list(_BY_NAME.values())\n', "ValWf"),
        },
        "probes": probes("ValWf"),
    },
    "lists_values_bare": {
        "files": {
            **flows("ValBareWf"),
            **facade('_BY_NAME = {"v": ValBareWf}\nWORKFLOWS = _BY_NAME.values()\n', "ValBareWf"),
        },
        "probes": probes("ValBareWf"),
    },
    "lists_values_star": {
        "files": {
            **flows("ValStarWf"),
            **facade('_BY_NAME = {"v": ValStarWf}\nWORKFLOWS = [*_BY_NAME.values()]\n', "ValStarWf"),
        },
        "probes": probes("ValStarWf"),
    },
    "lists_append": {
        "files": {**flows("AppA", "AppB"), **facade("WORKFLOWS = [AppA]\nWORKFLOWS.append(AppB)\n", "AppA", "AppB")},
        "probes": probes("AppA", "AppB"),
    },
    "lists_extend": {
        "files": {**flows("ExtA", "ExtB"), **facade("WORKFLOWS = [ExtA]\nWORKFLOWS.extend([ExtB])\n", "ExtA", "ExtB")},
        "probes": probes("ExtA", "ExtB"),
    },
    "lists_setitem": {
        "files": {
            **flows("SetA", "SetB"),
            **facade('WORKFLOWS = {"a": SetA}\nWORKFLOWS["b"] = SetB\n', "SetA", "SetB"),
        },
        "probes": probes("SetA", "SetB"),
    },
    "lists_update": {
        "files": {
            **flows("UpdA", "UpdB"),
            **facade('WORKFLOWS = {"a": UpdA}\nWORKFLOWS.update({"b": UpdB})\n', "UpdA", "UpdB"),
        },
        "probes": probes("UpdA", "UpdB"),
    },
    "lists_unpacked": {
        "files": {**flows("UnpackedWf"), **facade("WORKFLOWS, ACTIVITIES = [UnpackedWf], []\n", "UnpackedWf")},
        "probes": probes("UnpackedWf"),
    },
    "lists_getattr_default": {
        "files": {**flows("DefaultWf"), **facade("WORKFLOWS = [DefaultWf]\n", "DefaultWf")},
        "core": worker(
            "from products.lists_getattr_default.backend.facade import temporal as default_temporal\n"
            'DEFAULT_QUEUES = [("default", getattr(default_temporal, "WORKFLOWS", ()))]\n'
        ),
        "probes": probes("DefaultWf"),
    },
    "lists_import_module": {
        "files": {**flows("LoadedWf"), **facade("WORKFLOWS = [LoadedWf]\n", "LoadedWf")},
        "core": worker(
            "import importlib\n\nLOADED_QUEUES = "
            '[("loaded", importlib.import_module("products.lists_import_module.backend.facade.temporal").WORKFLOWS)]\n'
        ),
        "probes": probes("LoadedWf"),
    },
    "lists_class_base": {
        "files": {
            **flows("BaseA", "BaseB"),
            **facade("class Registration:\n    _BASE = [BaseA]\n    WORKFLOWS = _BASE + [BaseB]\n", "BaseA", "BaseB"),
        },
        "core": worker(
            "from products.lists_class_base.backend.facade.temporal import Registration as BaseRegistration\n"
            'BASE_QUEUES = [("base", BaseRegistration.WORKFLOWS)]\n'
        ),
        "probes": probes("BaseA", "BaseB"),
    },
    "lists_class_base_module": {
        "files": {
            **flows("ModBaseA", "ModBaseB"),
            **facade(
                "_BASE = [ModBaseA]\n\n\nclass Registration:\n    WORKFLOWS = _BASE + [ModBaseB]\n",
                "ModBaseA",
                "ModBaseB",
            ),
        },
        "core": worker(
            "from products.lists_class_base_module.backend.facade.temporal import Registration as ModBaseRegistration\n"
            'MOD_BASE_QUEUES = [("modbase", ModBaseRegistration.WORKFLOWS)]\n'
        ),
        "probes": probes("ModBaseA", "ModBaseB"),
    },
    "lists_class_reexport": {
        "files": {
            **flows("ReexportedWf"),
            "backend/temporal/registration.py": (
                f"from {TEMPORAL}.reexportedwf import ReexportedWf\n\n\nclass Registration:\n    WORKFLOWS = [ReexportedWf]\n"
            ),
            "backend/facade/temporal.py": f"from {TEMPORAL}.registration import Registration\n\n__all__ = ['Registration']\n",
        },
        "core": worker(
            "from products.lists_class_reexport.backend.facade.temporal import Registration as ReexportRegistration\n"
            'REEXPORT_QUEUES = [("reexport", ReexportRegistration.WORKFLOWS)]\n'
        ),
        "probes": probes("ReexportedWf"),
    },
    "lists_class_nested": {
        "files": {
            **flows("NestedWf"),
            **facade("class Outer:\n    class Inner:\n        WORKFLOWS = [NestedWf]\n", "NestedWf"),
        },
        "core": worker(
            "from products.lists_class_nested.backend.facade.temporal import Outer as NestedOuter\n"
            'NESTED_QUEUES = [("nested", NestedOuter.Inner.WORKFLOWS)]\n'
        ),
        "probes": probes("NestedWf"),
    },
    "lists_class_alias": {
        "files": {
            **flows("AliasedWf"),
            "backend/temporal/registration.py": (
                f"from {TEMPORAL}.aliasedwf import AliasedWf\n\n\nclass Registration:\n    WORKFLOWS = [AliasedWf]\n"
            ),
            "backend/facade/temporal.py": f"from {TEMPORAL}.registration import Registration\n\nWORKFLOWS = Registration.WORKFLOWS\n",
        },
        "probes": probes("AliasedWf"),
    },
    "lists_class_module": {
        "files": {
            **flows("ModuleClassWf"),
            **facade("class Registration:\n    WORKFLOWS = [ModuleClassWf]\n", "ModuleClassWf"),
        },
        "core": worker(
            "from products.lists_class_module.backend.facade import temporal as class_module_temporal\n"
            'CLASS_MODULE_QUEUES = [("classmodule", class_module_temporal.Registration.WORKFLOWS)]\n'
        ),
        "probes": probes("ModuleClassWf"),
    },
    "lists_class_getattr": {
        "files": {**flows("GotWf"), **facade("class Registration:\n    WORKFLOWS = [GotWf]\n", "GotWf")},
        "core": worker(
            "from products.lists_class_getattr.backend.facade.temporal import Registration as GotRegistration\n"
            'GOT_QUEUES = [("got", getattr(GotRegistration, "WORKFLOWS"))]\n'
        ),
        "probes": probes("GotWf"),
    },
    "lists_hop_deep": {
        "files": hop("lists_hop_deep", "DeepWf"),
        "core": {
            "posthog/temporal/hop_deep3.py": "from products.lists_hop_deep.backend.facade.temporal import WORKFLOWS as DEEP_WORKFLOWS\n\nDEEP3 = [*DEEP_WORKFLOWS]\n",
            "posthog/temporal/hop_deep2.py": "from posthog.temporal.hop_deep3 import DEEP3\n\nDEEP2 = DEEP3 + []\n",
            "posthog/temporal/hop_deep1.py": "from posthog.temporal.hop_deep2 import DEEP2\n\nDEEP1 = (*DEEP2,)\n",
            **worker('from posthog.temporal.hop_deep1 import DEEP1\nDEEP_QUEUES = [("deep", DEEP1)]\n'),
        },
        "probes": probes("DeepWf"),
    },
    "lists_hop_cycle": {
        "files": hop("lists_hop_cycle", "CycleWf"),
        "core": {
            "posthog/temporal/hop_cycle_a.py": (
                "from products.lists_hop_cycle.backend.facade.temporal import WORKFLOWS as CYCLE_WORKFLOWS\n"
                "from posthog.temporal.hop_cycle_b import CYCLE_B\n\nCYCLE_A = [*CYCLE_WORKFLOWS, *CYCLE_B]\n"
            ),
            "posthog/temporal/hop_cycle_b.py": "from posthog.temporal.hop_cycle_a import CYCLE_A\n\nCYCLE_B = [*CYCLE_A]\n",
            **worker('from posthog.temporal.hop_cycle_a import CYCLE_A\nCYCLE_QUEUES = [("cycle", CYCLE_A)]\n'),
        },
        "probes": probes("CycleWf"),
    },
    "lists_hop_item": {
        "files": {
            **flows("HopItemWf"),
            **facade("WORKFLOWS = [HopItemWf]\n__all__ = ['HopItemWf', 'WORKFLOWS']\n", "HopItemWf"),
        },
        "core": {
            "posthog/temporal/hop_item.py": "from products.lists_hop_item.backend.facade.temporal import HopItemWf\n\nHOP_ITEMS = [HopItemWf]\n",
            **worker('from posthog.temporal.hop_item import HOP_ITEMS\nHOP_ITEM_QUEUES = [("hopitem", HOP_ITEMS)]\n'),
        },
        "probes": probes("HopItemWf"),
    },
    "lists_hop_own": {
        "files": hop("lists_hop_own", "OwnWf"),
        "core": {
            "posthog/temporal/hop_own.py": (
                "from products.lists_hop_own.backend.facade.temporal import WORKFLOWS as OWN_WORKFLOWS\n\n\n"
                "class CoreOwnWorkflow:\n    pass\n\n\nHOP_OWN = [CoreOwnWorkflow, *OWN_WORKFLOWS]\n"
            ),
            **worker('from posthog.temporal.hop_own import HOP_OWN\nHOP_OWN_QUEUES = [("hopown", HOP_OWN)]\n'),
        },
        "probes": probes("OwnWf"),
    },
    "lists_hop_class": {
        "files": hop("lists_hop_class", "HopClassWf"),
        "core": {
            "posthog/temporal/hop_class.py": (
                "from products.lists_hop_class.backend.facade.temporal import WORKFLOWS as HOP_CLASS_WORKFLOWS\n\n\n"
                "class AllLists:\n    ALL = [*HOP_CLASS_WORKFLOWS]\n"
            ),
            **worker(
                'from posthog.temporal.hop_class import AllLists\nHOP_CLASS_QUEUES = [("hopclass", AllLists.ALL)]\n'
            ),
        },
        "probes": probes("HopClassWf"),
    },
    "lists_hop_dict": {
        "files": hop("lists_hop_dict", "HopDictWf"),
        "core": {
            "posthog/temporal/hop_dict.py": (
                "from products.lists_hop_dict.backend.facade.temporal import WORKFLOWS as HOP_DICT_WORKFLOWS\n\n"
                'HOP_BY = {"p": HOP_DICT_WORKFLOWS}\n'
            ),
            **worker('from posthog.temporal.hop_dict import HOP_BY\nHOP_DICT_QUEUES = [("hopdict", HOP_BY["p"])]\n'),
        },
        "probes": probes("HopDictWf"),
    },
    "lists_hop_reexport": {
        "files": hop("lists_hop_reexport", "HopReWf"),
        "core": {
            "posthog/temporal/hop_reexport.py": (
                "from products.lists_hop_reexport.backend.facade.temporal import WORKFLOWS as HOP_RE_WORKFLOWS\n\n"
                "__all__ = ['HOP_RE_WORKFLOWS']\n"
            ),
            **worker(
                'from posthog.temporal.hop_reexport import HOP_RE_WORKFLOWS\nHOP_RE_QUEUES = [("hopre", HOP_RE_WORKFLOWS)]\n'
            ),
        },
        "probes": probes("HopReWf"),
    },
    "lists_hop_comp": {
        "files": hop("lists_hop_comp", "HopCompWf"),
        "core": {
            "posthog/temporal/hop_comp.py": (
                "from products.lists_hop_comp.backend.facade.temporal import WORKFLOWS as HOP_COMP_WORKFLOWS\n\n"
                "HOP_COMP = [w for w in HOP_COMP_WORKFLOWS if w]\n"
            ),
            **worker('from posthog.temporal.hop_comp import HOP_COMP\nHOP_COMP_QUEUES = [("hopcomp", HOP_COMP)]\n'),
        },
        "probes": probes("HopCompWf"),
    },
    # A core function that returns the product list is its callee's (class (a), like rows 21, 33
    # and 40): the product passes, and the file behind the function may leave selection.
    "lists_hop_function": {
        "files": hop("lists_hop_function", "HopFnWf"),
        "core": {
            "posthog/temporal/hop_function.py": (
                "from products.lists_hop_function.backend.facade.temporal import WORKFLOWS as HOP_FN_WORKFLOWS\n\n\n"
                "def all_workflows():\n    return [*HOP_FN_WORKFLOWS]\n"
            ),
            **worker(
                'from posthog.temporal.hop_function import all_workflows\nHOP_FN_QUEUES = [("hopfn", all_workflows())]\n'
            ),
        },
        "probes": [],
    },
    # A facade that rebinds a name it imports reads the import (this shape crashed the run).
    "lists_rebind_itself": {
        "files": {
            **flows("ItselfWf"),
            "backend/temporal/catalog.py": f"from {TEMPORAL}.itselfwf import ItselfWf\n\nWORKFLOWS = [ItselfWf]\n",
            "backend/facade/temporal.py": f"from {TEMPORAL}.catalog import WORKFLOWS\n\nWORKFLOWS = WORKFLOWS\n",
        },
        "probes": ["backend/temporal/catalog.py", *probes("ItselfWf")],
    },
    "lists_rebind_import": {
        "files": {
            **flows("RebindA", "RebindB"),
            "backend/temporal/catalog.py": f"from {TEMPORAL}.rebinda import RebindA\n\nWORKFLOWS = [RebindA]\n",
            **facade(
                "WORKFLOWS = WORKFLOWS + [RebindB]\n", "RebindB", head=f"from {TEMPORAL}.catalog import WORKFLOWS\n"
            ),
        },
        "probes": probes("RebindA", "RebindB"),
    },
    "lists_rebind_twice": {
        "files": {
            **flows("TwiceA", "TwiceB"),
            **facade("WORKFLOWS = [TwiceA]\nWORKFLOWS = WORKFLOWS + [TwiceB]\n", "TwiceA", "TwiceB"),
        },
        "probes": probes("TwiceA", "TwiceB"),
    },
    "lists_rebind_same": {
        "files": {**flows("SameWf"), **facade("WORKFLOWS = [SameWf]\nWORKFLOWS = WORKFLOWS\n", "SameWf")},
        "probes": probes("SameWf"),
    },
    # Constants a registration point imports next to the list hold no item (were over-fails).
    "lists_queue_getenv": {
        "files": {
            **flows("EnvWf"),
            **facade(
                'WORKFLOWS = [EnvWf]\nDEFAULT_QUEUE = "q-env"\nTASK_QUEUE = os.getenv("Q_ENV", DEFAULT_QUEUE)\n',
                "EnvWf",
                head="import os\n",
            ),
        },
        "core": worker(
            "from products.lists_queue_getenv.backend.facade.temporal import WORKFLOWS as ENV_WORKFLOWS, TASK_QUEUE as ENV_QUEUE\n"
            "ENV_QUEUES = [(ENV_QUEUE, ENV_WORKFLOWS, [])]\n"
        ),
        "probes": probes("EnvWf"),
    },
    "lists_timeout_timedelta": {
        "files": {
            **flows("TdWf"),
            **facade(
                "WORKFLOWS = [TdWf]\nTIMEOUT_S = 30\nTIMEOUT = timedelta(seconds=TIMEOUT_S)\n",
                "TdWf",
                head="from datetime import timedelta\n",
            ),
        },
        "core": worker(
            "from products.lists_timeout_timedelta.backend.facade.temporal import WORKFLOWS as TD_WORKFLOWS, TIMEOUT as TD_TIMEOUT\n"
            'TD_QUEUES = [("td", TD_WORKFLOWS, [TD_TIMEOUT])]\n'
        ),
        "probes": probes("TdWf"),
    },
    "lists_queue_join": {
        "files": {
            **flows("JoinWf"),
            **facade('WORKFLOWS = [JoinWf]\nPREFIX = "q"\nTASK_QUEUE = "-".join([PREFIX, "queue"])\n', "JoinWf"),
        },
        "core": worker(
            "from products.lists_queue_join.backend.facade.temporal import WORKFLOWS as JOIN_WORKFLOWS, TASK_QUEUE as JOIN_QUEUE\n"
            "JOIN_QUEUES = [(JOIN_QUEUE, JOIN_WORKFLOWS, [])]\n"
        ),
        "probes": probes("JoinWf"),
    },
    "lists_queue_unpacked": {
        "files": {**flows("UnpWf"), **facade('WORKFLOWS = [UnpWf]\nTASK_QUEUE, TIMEOUT_S = "q-unp", 30\n', "UnpWf")},
        "core": worker(
            "from products.lists_queue_unpacked.backend.facade.temporal import WORKFLOWS as UNP_WORKFLOWS, TASK_QUEUE as UNP_QUEUE\n"
            "UNP_QUEUES = [(UNP_QUEUE, UNP_WORKFLOWS, [])]\n"
        ),
        "probes": probes("UnpWf"),
    },
    # WA12: declarations.
    "decl_cycle": {
        "files": {
            **flows("CycA", "CycB"),
            "backend/temporal/queues.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.cyca import CycA\nfrom {TEMPORAL}.more import MORE\n\n"
                'QUEUES = (QueueBinding(queue_setting="CA", workflows=(CycA,)), *MORE)\n'
            ),
            "backend/temporal/more.py": (
                RECORD_IMPORT + f"from {TEMPORAL}.cycb import CycB\nfrom {TEMPORAL}.queues import QUEUES\n\n"
                'MORE = (QueueBinding(queue_setting="CB", workflows=(CycB,)), *QUEUES)\n'
            ),
            "backend/facade/temporal.py": RECORD_IMPORT
            + f"from {TEMPORAL}.queues import QUEUES\n\nTEMPORAL = TemporalRegistration(queues=QUEUES)\n",
        },
        "probes": probes("CycA", "CycB"),
    },
    "decl_rebound_after": {
        "files": {
            **flows("LaterWf"),
            **facade(
                '_MAIN = QueueBinding(queue_setting="LA", workflows=(LaterWf,))\nTEMPORAL = TemporalRegistration(queues=(_MAIN,))\n_MAIN = None\n',
                "LaterWf",
                head=RECORD_IMPORT,
            ),
        },
        "probes": probes("LaterWf"),
    },
    "decl_rebind_import": {
        "files": {
            **flows("ReA", "ReB"),
            "backend/temporal/queues.py": RECORD_IMPORT
            + f'from {TEMPORAL}.rea import ReA\n\nQUEUES = (QueueBinding(queue_setting="RA", workflows=(ReA,)),)\n',
            **facade(
                'QUEUES = QUEUES + (QueueBinding(queue_setting="RB", workflows=(ReB,)),)\nTEMPORAL = TemporalRegistration(queues=QUEUES)\n',
                "ReB",
                head=RECORD_IMPORT + f"from {TEMPORAL}.queues import QUEUES\n",
            ),
        },
        "probes": probes("ReA", "ReB"),
    },
    "decl_class_attribute": {
        "files": {
            **flows("ClsMainWf"),
            **facade(
                'class Queues:\n    MAIN = QueueBinding(queue_setting="CM", workflows=(ClsMainWf,))\n\n\nTEMPORAL = TemporalRegistration(queues=(Queues.MAIN,))\n',
                "ClsMainWf",
                head=RECORD_IMPORT,
            ),
        },
        "probes": probes("ClsMainWf"),
    },
    "decl_class_module": {
        "files": {
            **flows("ClsModWf"),
            "backend/temporal/queues.py": RECORD_IMPORT
            + f'from {TEMPORAL}.clsmodwf import ClsModWf\n\n\nclass Queues:\n    MAIN = QueueBinding(queue_setting="CQ", workflows=(ClsModWf,))\n',
            "backend/facade/temporal.py": RECORD_IMPORT
            + f"from {TEMPORAL}.queues import Queues\n\nTEMPORAL = TemporalRegistration(queues=(Queues.MAIN,))\n",
        },
        "probes": probes("ClsModWf"),
    },
    "decl_core_block": {
        "files": {
            **flows("CoreBlockWf"),
            **facade(
                "TEMPORAL = TemporalRegistration(queues=(QueueBinding(queue_setting=CORE_QUEUE, workflows=(CoreBlockWf,)),))\n",
                "CoreBlockWf",
                head=RECORD_IMPORT + "from posthog.temporal.common.core_queue import CORE_QUEUE\n",
            ),
        },
        "core": {
            "posthog/temporal/common/core_queue.py": 'from django.conf import settings\n\nif settings.DEBUG:\n    CORE_QUEUE = "dev"\nelse:\n    CORE_QUEUE = "prod"\n'
        },
        "probes": probes("CoreBlockWf"),
    },
    "decl_core_twice": {
        "files": {
            **flows("CoreTwiceWf"),
            **facade(
                "TEMPORAL = TemporalRegistration(queues=(QueueBinding(queue_setting=CORE_TWICE, workflows=(CoreTwiceWf,)),))\n",
                "CoreTwiceWf",
                head=RECORD_IMPORT + "from posthog.temporal.common.core_twice import CORE_TWICE\n",
            ),
        },
        "core": {"posthog/temporal/common/core_twice.py": 'CORE_TWICE = "a"\nCORE_TWICE = "b"\n'},
        "probes": probes("CoreTwiceWf"),
    },
    "decl_annotated": {
        "files": {
            **flows("AnnWf"),
            **facade(
                '_MAIN: QueueBinding = QueueBinding(queue_setting="AN", workflows=(AnnWf,))\nTEMPORAL = TemporalRegistration(queues=(_MAIN,))\n',
                "AnnWf",
                head=RECORD_IMPORT,
            ),
        },
        "probes": probes("AnnWf"),
    },
    # WA13: unread, so the product cannot pass.
    "unread_unpacked_call": {
        "files": {
            **flows("PairWf"),
            **facade("def _pair():\n    return [PairWf], []\n\n\nWORKFLOWS, ACTIVITIES = _pair()\n", "PairWf"),
        },
        "unread": True,
    },
    "unread_computed_getattr": {
        "files": {**flows("NamedWf"), **facade("WORKFLOWS = [NamedWf]\n", "NamedWf")},
        "core": worker(
            "from products.unread_computed_getattr.backend.facade import temporal as named_temporal\n"
            'NAMED = "WORKFLOWS"\nNAMED_QUEUES = [("named", getattr(named_temporal, NAMED))]\n'
        ),
        "unread": True,
    },
    "unread_vars": {
        "files": {**flows("VarsWf"), **facade("WORKFLOWS = [VarsWf]\n", "VarsWf")},
        "core": worker(
            "from products.unread_vars.backend.facade import temporal as vars_temporal\n"
            'VARS_QUEUES = [("vars", vars(vars_temporal)["WORKFLOWS"])]\n'
        ),
        "unread": True,
    },
    "unread_instance": {
        "files": {**flows("InstWf"), **facade("class Registration:\n    WORKFLOWS = [InstWf]\n", "InstWf")},
        "core": worker(
            "from products.unread_instance.backend.facade.temporal import Registration as InstRegistration\n"
            'INST_QUEUES = [("inst", InstRegistration().WORKFLOWS)]\n'
        ),
        "unread": True,
    },
    "unread_global": {
        "files": {
            **flows("GlobA", "GlobB"),
            **facade(
                '_MAIN = QueueBinding(queue_setting="GL", workflows=(GlobA,))\n\n\ndef _late():\n    global _MAIN\n'
                '    _MAIN = QueueBinding(queue_setting="GL", workflows=(GlobB,))\n\n\n_late()\nTEMPORAL = TemporalRegistration(queues=(_MAIN,))\n',
                "GlobA",
                "GlobB",
                head=RECORD_IMPORT,
            ),
        },
        "unread": True,
    },
    "unread_for": {
        "files": {
            **flows("ForWf"),
            **facade(
                'for _MAIN in (QueueBinding(queue_setting="FO", workflows=(ForWf,)),):\n    pass\nTEMPORAL = TemporalRegistration(queues=(_MAIN,))\n',
                "ForWf",
                head=RECORD_IMPORT,
            ),
        },
        "unread": True,
    },
    "unread_subscript": {
        "files": {
            **flows("SubWf"),
            **facade(
                '_BINDINGS = {"main": QueueBinding(queue_setting="SU", workflows=(SubWf,))}\nTEMPORAL = TemporalRegistration(queues=(_BINDINGS["main"],))\n',
                "SubWf",
                head=RECORD_IMPORT,
            ),
        },
        "unread": True,
    },
    "unread_bound_twice": {
        "files": {
            **flows("TwoA", "TwoB"),
            **facade(
                '_MAIN = QueueBinding(queue_setting="TW", workflows=(TwoA,))\n_MAIN = QueueBinding(queue_setting="TW", workflows=(TwoB,))\nTEMPORAL = TemporalRegistration(queues=(_MAIN,))\n',
                "TwoA",
                "TwoB",
                head=RECORD_IMPORT,
            ),
        },
        "unread": True,
    },
    "unread_frozenset": {
        "files": {**flows("FrozWf"), **facade("WORKFLOWS = frozenset({FrozWf})\n", "FrozWf")},
        "unread": True,
    },
    "unread_mapped_call": {
        "files": {
            **flows("MapWf"),
            **facade(
                "def _wrap(w):\n    return w\n\n\n_ALL = [MapWf]\nWORKFLOWS = [_wrap(w) for w in _ALL]\n", "MapWf"
            ),
        },
        "unread": True,
    },
}
SHAPES.update(ROUND3)


def lay(repo: Path, shapes: dict[str, dict[str, Any]]) -> None:
    core: dict[str, str] = {RECORDS: RECORD_CLASSES}
    for p, shape in shapes.items():
        files = {path: text.format(p=p) for path, text in SKELETON.items()}
        files.update({path: text.replace("{p}", p) for path, text in shape["files"].items()})
        for path, text in files.items():
            target = repo / "products" / p / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        # A facade with no declaration hands out a list the worker registers.
        declares = "TEMPORAL" in files["backend/facade/temporal.py"]
        appended = shape.get("core") or ({} if declares else {WORKER: registered(p)})
        for path, text in appended.items():
            core[path] = core.get(path, "") + text
    for path, text in core.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        before = target.read_text() if target.exists() and path != RECORDS else ""
        target.write_text(before + text)


@pytest.fixture(scope="module")
def answer(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    work = tmp_path_factory.mktemp("shapes")
    repo = work / "repo"
    copy_tree(repo)
    lay(repo, SHAPES)
    lay_derived(repo)
    found, _ = watched(repo, work / "run")
    return found


def test_the_answer_is_complete(answer: dict[str, Any]) -> None:
    assert answer["complete"], answer["problems"]


@pytest.mark.parametrize("p", [p for p, shape in SHAPES.items() if not shape.get("unread")])
def test_each_file_core_runs_is_watched(answer: dict[str, Any], p: str) -> None:
    group = answer["groups"][p]
    assert group["passes"], group["reasons"]
    missing = [probe for probe in SHAPES[p]["probes"] if f"products/{p}/{probe}" not in group["watched"]]
    assert missing == []


@pytest.mark.parametrize("p", [p for p, shape in SHAPES.items() if shape.get("unread")])
def test_a_shape_the_link_cannot_read_fails_the_level(answer: dict[str, Any], p: str) -> None:
    group = answer["groups"][p]
    assert not group["passes"]
    assert any("cannot read every item" in reason for reason in group["reasons"]), group["reasons"]


HOP_UNREAD = {
    "hop_unread": {
        "files": hop("hop_unread", "UnreadHopWf"),
        "core": {
            "posthog/temporal/hop_unread.py": (
                "from products.hop_unread.backend.facade.temporal import WORKFLOWS as UNREAD_HOP_WORKFLOWS\n\n\n"
                "def _wrap(w):\n    return w\n\n\nHOP_MAPPED = list(map(_wrap, UNREAD_HOP_WORKFLOWS))\n"
            ),
            **worker('from posthog.temporal.hop_unread import HOP_MAPPED\nHOP_UNREAD_QUEUES = [("hu", HOP_MAPPED)]\n'),
        },
    },
}


def test_an_unread_item_of_a_core_list_core_registers_is_a_problem(tmp_path: Path) -> None:
    """A core list a registration point registers may hold any product's list; an item of it the
    link cannot read is owned by no group, so it makes the answer incomplete (limit 6)."""
    repo = tmp_path / "repo"
    copy_tree(repo)
    lay(repo, HOP_UNREAD)
    lay_derived(repo)
    found, _ = watched(repo, tmp_path / "run")
    assert not found["complete"]
    assert any("posthog.temporal.hop_unread.HOP_MAPPED" in problem for problem in found["problems"]), found["problems"]
    assert not found["groups"]["hop_unread"]["passes"]
