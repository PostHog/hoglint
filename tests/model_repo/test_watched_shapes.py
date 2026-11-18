"""`hoglint watched` on the shapes a registered list or a declaration takes (CASES.md `WA08` to
`WA10`): one small product per shape, laid over a copy of the model repository with the preset in
the derived form, in one run. Each product's probe files define what core runs: each is watched, or
the product cannot pass because the link cannot read an item. Unknown is never clean."""

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
    "unread_unpacked": {
        "files": {
            "backend/temporal/flows.py": CLASS.format(name="UnpackedWorkflow"),
            "backend/facade/temporal.py": (
                f"from {TEMPORAL}.flows import UnpackedWorkflow\n\nWORKFLOWS, ACTIVITIES = [UnpackedWorkflow], []\n"
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


def lay(repo: Path) -> None:
    core: dict[str, str] = {RECORDS: RECORD_CLASSES}
    for p, shape in SHAPES.items():
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
    lay(repo)
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
