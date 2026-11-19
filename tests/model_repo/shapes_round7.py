"""The adversary's recheck-5 rows (CASES.md `WA18`), as data for `test_watched_shapes.py`: the
use reading's listed steps (`g`), records (`r`), binding forms (`t`), the namespace and limit 9
(`n`), over-fail checks (`o`), the presets' shape (`p`) and module access in registration points
(`h`). Each row has a product of its own, and each (h) and (p) row a registration point of its own.
The answer each must give: `watched`, `unread` (the product cannot pass) or `problem` (the answer
is incomplete), each for the `reason` named, or `kept` (a written limit, as `why` says). Generated
from the round's attack script by running each row alone; edit as data."""

from __future__ import annotations

from typing import Any

ROUND7: dict[str, dict[str, Any]] = {
    "r7_g_deepcopy_memo": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_deepcopy_memo\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_deepcopy_memo", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_deepcopy_memo.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gdeepcopymemoa.py": "class GDeepcopyMemoA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gdeepcopymemob.py": "class GDeepcopyMemoB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import copy\n"
            "from "
            "products.r7_g_deepcopy_memo.backend.temporal.gdeepcopymemoa "
            "import GDeepcopyMemoA\n"
            "from "
            "products.r7_g_deepcopy_memo.backend.temporal.gdeepcopymemob "
            "import GDeepcopyMemoB\n"
            "\n"
            "WORKFLOWS = [GDeepcopyMemoA]\n"
            "_MEMO = {}\n"
            "_COPY = copy.deepcopy(WORKFLOWS, _MEMO)\n"
            "_MEMO[id(_MEMO)][0].append(GDeepcopyMemoB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_deepcopy_memo.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_DEEPCOPY_MEMO_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_DEEPCOPY_MEMO = "
            '[("r7_g_deepcopy_memo", '
            "R7_G_DEEPCOPY_MEMO_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gdeepcopymemoa.py", "backend/temporal/gdeepcopymemob.py"],
        "outcome": "unread",
        "reason": "products.r7_g_deepcopy_memo.backend.facade.temporal.WORKFLOWS",
        "note": "copy.deepcopy(W, memo): the memo keeps W itself (_keep_alive); memo[id(memo)][0].append(B)",
    },
    "r7_g_getattr_append": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_getattr_append\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_getattr_append", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_getattr_append.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/ggetattrappenda.py": "class GGetattrAppendA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/ggetattrappendb.py": "class GGetattrAppendB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_getattr_append.backend.temporal.ggetattrappenda "
            "import GGetattrAppendA\n"
            "from "
            "products.r7_g_getattr_append.backend.temporal.ggetattrappendb "
            "import GGetattrAppendB\n"
            "\n"
            "WORKFLOWS = [GGetattrAppendA]\n"
            'getattr(WORKFLOWS, "append")(GGetattrAppendB)\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_getattr_append.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_GETATTR_APPEND_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_GETATTR_APPEND "
            '= [("r7_g_getattr_append", '
            "R7_G_GETATTR_APPEND_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/ggetattrappenda.py", "backend/temporal/ggetattrappendb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_getattr_append.backend.facade.temporal.WORKFLOWS",
        "note": "getattr(W, 'append')(B) as a statement (no mutation row)",
    },
    "r7_g_map_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_map_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_map_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_map_holder.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gmapholdera.py": "class GMapHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gmapholderb.py": "class GMapHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_map_holder.backend.temporal.gmapholdera "
            "import GMapHolderA\n"
            "from "
            "products.r7_g_map_holder.backend.temporal.gmapholderb "
            "import GMapHolderB\n"
            "\n"
            "WORKFLOWS = [GMapHolderA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    items.append(GMapHolderB)\n"
            "    return items\n"
            "\n"
            "\n"
            "list(map(_grab, [WORKFLOWS]))\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_map_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_MAP_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_MAP_HOLDER = "
            '[("r7_g_map_holder", '
            "R7_G_MAP_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gmapholdera.py", "backend/temporal/gmapholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_map_holder.backend.facade.temporal.WORKFLOWS",
        "note": "list(map(_grab, [W])): map hands each element (W) to a first-party function",
    },
    "r7_g_filter_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_filter_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_filter_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_filter_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gfilterholdera.py": "class GFilterHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gfilterholderb.py": "class GFilterHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_filter_holder.backend.temporal.gfilterholdera "
            "import GFilterHolderA\n"
            "from "
            "products.r7_g_filter_holder.backend.temporal.gfilterholderb "
            "import GFilterHolderB\n"
            "\n"
            "WORKFLOWS = [GFilterHolderA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    items.append(GFilterHolderB)\n"
            "    return items\n"
            "\n"
            "\n"
            "list(filter(_grab, [WORKFLOWS]))\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_filter_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_FILTER_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_FILTER_HOLDER = "
            '[("r7_g_filter_holder", '
            "R7_G_FILTER_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gfilterholdera.py", "backend/temporal/gfilterholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_filter_holder.backend.facade.temporal.WORKFLOWS",
        "note": "list(filter(_grab, [W]))",
    },
    "r7_g_sorted_key_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_sorted_key_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_sorted_key_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_sorted_key_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gsortedkeyholdera.py": "class GSortedKeyHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gsortedkeyholderb.py": "class GSortedKeyHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_sorted_key_holder.backend.temporal.gsortedkeyholdera "
            "import GSortedKeyHolderA\n"
            "from "
            "products.r7_g_sorted_key_holder.backend.temporal.gsortedkeyholderb "
            "import GSortedKeyHolderB\n"
            "\n"
            "WORKFLOWS = [GSortedKeyHolderA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    items.append(GSortedKeyHolderB)\n"
            "    return items\n"
            "\n"
            "\n"
            "_S = sorted([WORKFLOWS], key=_grab)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_sorted_key_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_SORTED_KEY_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_SORTED_KEY_HOLDER "
            "= "
            '[("r7_g_sorted_key_holder", '
            "R7_G_SORTED_KEY_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gsortedkeyholdera.py", "backend/temporal/gsortedkeyholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_sorted_key_holder.backend.facade.temporal.WORKFLOWS",
        "note": "sorted([W], key=_grab): the key gets W",
    },
    "r7_g_max_key_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_max_key_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_max_key_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_max_key_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gmaxkeyholdera.py": "class GMaxKeyHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gmaxkeyholderb.py": "class GMaxKeyHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_max_key_holder.backend.temporal.gmaxkeyholdera "
            "import GMaxKeyHolderA\n"
            "from "
            "products.r7_g_max_key_holder.backend.temporal.gmaxkeyholderb "
            "import GMaxKeyHolderB\n"
            "\n"
            "WORKFLOWS = [GMaxKeyHolderA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    items.append(GMaxKeyHolderB)\n"
            "    return items\n"
            "\n"
            "\n"
            "_M = max([WORKFLOWS], key=_grab)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_max_key_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_MAX_KEY_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_MAX_KEY_HOLDER "
            '= [("r7_g_max_key_holder", '
            "R7_G_MAX_KEY_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gmaxkeyholdera.py", "backend/temporal/gmaxkeyholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_max_key_holder.backend.facade.temporal.WORKFLOWS",
        "note": "max([W], key=_grab): the key gets W",
    },
    "r7_g_iter_sentinel": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_iter_sentinel\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_iter_sentinel", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_iter_sentinel.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gitersentinela.py": "class GIterSentinelA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gitersentinelb.py": "class GIterSentinelB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_iter_sentinel.backend.temporal.gitersentinela "
            "import GIterSentinelA\n"
            "from "
            "products.r7_g_iter_sentinel.backend.temporal.gitersentinelb "
            "import GIterSentinelB\n"
            "\n"
            "WORKFLOWS = [GIterSentinelA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GIterSentinelB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_N = next(iter(_Hook, WORKFLOWS), None)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_iter_sentinel.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_ITER_SENTINEL_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_ITER_SENTINEL = "
            '[("r7_g_iter_sentinel", '
            "R7_G_ITER_SENTINEL_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gitersentinela.py", "backend/temporal/gitersentinelb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_iter_sentinel.backend.facade.temporal.WORKFLOWS",
        "note": "iter(_Hook, W): each call's result is compared with W (_Hook.__eq__(W))",
    },
    "r7_g_json_cls": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_json_cls\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_json_cls", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_json_cls.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gjsonclsa.py": "class GJsonClsA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gjsonclsb.py": "class GJsonClsB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import json\n"
            "from products.r7_g_json_cls.backend.temporal.gjsonclsa "
            "import GJsonClsA\n"
            "from products.r7_g_json_cls.backend.temporal.gjsonclsb "
            "import GJsonClsB\n"
            "\n"
            "WORKFLOWS = [GJsonClsA]\n"
            "\n"
            "\n"
            "class _Enc(json.JSONEncoder):\n"
            "    def encode(self, o):\n"
            "        o.append(GJsonClsB)\n"
            "        return '[]'\n"
            "\n"
            "\n"
            "_J = json.dumps(WORKFLOWS, cls=_Enc)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_json_cls.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_JSON_CLS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_JSON_CLS = "
            '[("r7_g_json_cls", '
            "R7_G_JSON_CLS_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/gjsonclsa.py", "backend/temporal/gjsonclsb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_json_cls.backend.facade.temporal.WORKFLOWS",
        "note": "json.dumps(W, cls=_Enc): a first-party encoder's encode(W)",
    },
    "r7_g_tstring": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_tstring\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_tstring", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_tstring.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gtstringa.py": "class GTstringA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gtstringb.py": "class GTstringB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_g_tstring.backend.temporal.gtstringa "
            "import GTstringA\n"
            "from products.r7_g_tstring.backend.temporal.gtstringb "
            "import GTstringB\n"
            "\n"
            "WORKFLOWS = [GTstringA]\n"
            '_T = t"{WORKFLOWS}"\n'
            "_T.values[0].append(GTstringB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_tstring.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_TSTRING_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_TSTRING = "
            '[("r7_g_tstring", '
            "R7_G_TSTRING_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/gtstringa.py", "backend/temporal/gtstringb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_tstring.backend.facade.temporal.WORKFLOWS",
        "note": "t-string (3.14): _T = t'{W}' keeps W; _T.values[0].append(B)",
    },
    "r7_g_slice_hook": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_slice_hook\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_slice_hook", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_slice_hook.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gslicehooka.py": "class GSliceHookA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gslicehookb.py": "class GSliceHookB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_slice_hook.backend.temporal.gslicehooka "
            "import GSliceHookA\n"
            "from "
            "products.r7_g_slice_hook.backend.temporal.gslicehookb "
            "import GSliceHookB\n"
            "\n"
            "WORKFLOWS = [GSliceHookA]\n"
            "\n"
            "\n"
            "class _Sl:\n"
            "    def __getitem__(self, s):\n"
            "        s.start.append(GSliceHookB)\n"
            "        return s\n"
            "\n"
            "\n"
            "_X = _Sl()[WORKFLOWS:]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_slice_hook.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_SLICE_HOOK_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_SLICE_HOOK = "
            '[("r7_g_slice_hook", '
            "R7_G_SLICE_HOOK_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gslicehooka.py", "backend/temporal/gslicehookb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_slice_hook.backend.facade.temporal.WORKFLOWS",
        "note": "_Sl()[W:]: a slice part hands W to a first-party __getitem__",
    },
    "r7_g_case_value_hook": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_case_value_hook\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_case_value_hook", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_case_value_hook.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gcasevaluehooka.py": "class GCaseValueHookA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gcasevaluehookb.py": "class GCaseValueHookB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_case_value_hook.backend.temporal.gcasevaluehooka "
            "import GCaseValueHookA\n"
            "from "
            "products.r7_g_case_value_hook.backend.temporal.gcasevaluehookb "
            "import GCaseValueHookB\n"
            "\n"
            "WORKFLOWS = [GCaseValueHookA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GCaseValueHookB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "class _C:\n"
            "    L = WORKFLOWS\n"
            "\n"
            "\n"
            "match _Hook():\n"
            "    case _C.L:\n"
            "        pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_case_value_hook.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_CASE_VALUE_HOOK_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_CASE_VALUE_HOOK "
            "= "
            '[("r7_g_case_value_hook", '
            "R7_G_CASE_VALUE_HOOK_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gcasevaluehooka.py", "backend/temporal/gcasevaluehookb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_case_value_hook.backend.facade.temporal.WORKFLOWS",
        "note": "match _Hook(): case _C.L: (a value pattern: subject == W runs _Hook.__eq__(W))",
    },
    "r7_g_match_capture": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_match_capture\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_match_capture", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_match_capture.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gmatchcapturea.py": "class GMatchCaptureA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gmatchcaptureb.py": "class GMatchCaptureB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_match_capture.backend.temporal.gmatchcapturea "
            "import GMatchCaptureA\n"
            "from "
            "products.r7_g_match_capture.backend.temporal.gmatchcaptureb "
            "import GMatchCaptureB\n"
            "\n"
            "WORKFLOWS = [GMatchCaptureA]\n"
            "match WORKFLOWS:\n"
            "    case _x:\n"
            "        _x.append(GMatchCaptureB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_match_capture.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_MATCH_CAPTURE_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_MATCH_CAPTURE = "
            '[("r7_g_match_capture", '
            "R7_G_MATCH_CAPTURE_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gmatchcapturea.py", "backend/temporal/gmatchcaptureb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_match_capture.backend.facade.temporal.WORKFLOWS",
        "note": "match W: case _x: _x.append(B) (a capture binds W itself)",
    },
    "r7_g_match_as_class": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_match_as_class\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_match_as_class", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_match_as_class.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gmatchasclassa.py": "class GMatchAsClassA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gmatchasclassb.py": "class GMatchAsClassB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_match_as_class.backend.temporal.gmatchasclassa "
            "import GMatchAsClassA\n"
            "from "
            "products.r7_g_match_as_class.backend.temporal.gmatchasclassb "
            "import GMatchAsClassB\n"
            "\n"
            "WORKFLOWS = [GMatchAsClassA]\n"
            "match WORKFLOWS:\n"
            "    case list() as _x:\n"
            "        _x.append(GMatchAsClassB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_match_as_class.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_MATCH_AS_CLASS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_MATCH_AS_CLASS "
            '= [("r7_g_match_as_class", '
            "R7_G_MATCH_AS_CLASS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gmatchasclassa.py", "backend/temporal/gmatchasclassb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_match_as_class.backend.facade.temporal.WORKFLOWS",
        "note": "match W: case list() as _x: _x.append(B)",
    },
    "r7_g_match_seq_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_match_seq_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_match_seq_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_match_seq_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gmatchseqholdera.py": "class GMatchSeqHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gmatchseqholderb.py": "class GMatchSeqHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_match_seq_holder.backend.temporal.gmatchseqholdera "
            "import GMatchSeqHolderA\n"
            "from "
            "products.r7_g_match_seq_holder.backend.temporal.gmatchseqholderb "
            "import GMatchSeqHolderB\n"
            "\n"
            "WORKFLOWS = [GMatchSeqHolderA]\n"
            "_L = [WORKFLOWS]\n"
            "match _L:\n"
            "    case [_x]:\n"
            "        _x.append(GMatchSeqHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_match_seq_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_MATCH_SEQ_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_MATCH_SEQ_HOLDER "
            "= "
            '[("r7_g_match_seq_holder", '
            "R7_G_MATCH_SEQ_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gmatchseqholdera.py", "backend/temporal/gmatchseqholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_match_seq_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_L = [W]; match _L: case [_x]: _x.append(B)",
    },
    "r7_g_match_map_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_match_map_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_match_map_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_match_map_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gmatchmapholdera.py": "class GMatchMapHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gmatchmapholderb.py": "class GMatchMapHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_match_map_holder.backend.temporal.gmatchmapholdera "
            "import GMatchMapHolderA\n"
            "from "
            "products.r7_g_match_map_holder.backend.temporal.gmatchmapholderb "
            "import GMatchMapHolderB\n"
            "\n"
            "WORKFLOWS = [GMatchMapHolderA]\n"
            '_D = {"w": WORKFLOWS}\n'
            "match _D:\n"
            '    case {"w": _x}:\n'
            "        _x.append(GMatchMapHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_match_map_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_MATCH_MAP_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_MATCH_MAP_HOLDER "
            "= "
            '[("r7_g_match_map_holder", '
            "R7_G_MATCH_MAP_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gmatchmapholdera.py", "backend/temporal/gmatchmapholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_match_map_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_D = {'w': W}; match _D: case {'w': _x}: _x.append(B)",
    },
    "r7_g_match_star_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_match_star_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_match_star_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_match_star_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gmatchstarholdera.py": "class GMatchStarHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gmatchstarholderb.py": "class GMatchStarHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_match_star_holder.backend.temporal.gmatchstarholdera "
            "import GMatchStarHolderA\n"
            "from "
            "products.r7_g_match_star_holder.backend.temporal.gmatchstarholderb "
            "import GMatchStarHolderB\n"
            "\n"
            "WORKFLOWS = [GMatchStarHolderA]\n"
            "_L = [WORKFLOWS]\n"
            "match _L:\n"
            "    case [*_r]:\n"
            "        _r[0].append(GMatchStarHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_match_star_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_MATCH_STAR_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_MATCH_STAR_HOLDER "
            "= "
            '[("r7_g_match_star_holder", '
            "R7_G_MATCH_STAR_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gmatchstarholdera.py", "backend/temporal/gmatchstarholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_match_star_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_L = [W]; match _L: case [*_r]: _r[0].append(B)",
    },
    "r7_g_cls_holder_attr": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_cls_holder_attr\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_cls_holder_attr", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_cls_holder_attr.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gclsholderattra.py": "class GClsHolderAttrA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gclsholderattrb.py": "class GClsHolderAttrB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_cls_holder_attr.backend.temporal.gclsholderattra "
            "import GClsHolderAttrA\n"
            "from "
            "products.r7_g_cls_holder_attr.backend.temporal.gclsholderattrb "
            "import GClsHolderAttrB\n"
            "\n"
            "WORKFLOWS = [GClsHolderAttrA]\n"
            "\n"
            "\n"
            "class _H:\n"
            "    L = [WORKFLOWS]\n"
            "\n"
            "\n"
            "_H.L[0].append(GClsHolderAttrB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_cls_holder_attr.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_CLS_HOLDER_ATTR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_CLS_HOLDER_ATTR "
            "= "
            '[("r7_g_cls_holder_attr", '
            "R7_G_CLS_HOLDER_ATTR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gclsholderattra.py", "backend/temporal/gclsholderattrb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_cls_holder_attr.backend.facade.temporal.WORKFLOWS",
        "note": "class _H: L = [W]; _H.L[0].append(B) (a holder as a class attribute)",
    },
    "r7_g_cls_holder_instance": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_cls_holder_instance\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_cls_holder_instance", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_cls_holder_instance.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gclsholderinstancea.py": "class GClsHolderInstanceA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gclsholderinstanceb.py": "class GClsHolderInstanceB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_cls_holder_instance.backend.temporal.gclsholderinstancea "
            "import GClsHolderInstanceA\n"
            "from "
            "products.r7_g_cls_holder_instance.backend.temporal.gclsholderinstanceb "
            "import GClsHolderInstanceB\n"
            "\n"
            "WORKFLOWS = [GClsHolderInstanceA]\n"
            "\n"
            "\n"
            "class _H:\n"
            "    L = [WORKFLOWS]\n"
            "\n"
            "\n"
            "_H().L[0].append(GClsHolderInstanceB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_cls_holder_instance.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_CLS_HOLDER_INSTANCE_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_CLS_HOLDER_INSTANCE "
            "= "
            '[("r7_g_cls_holder_instance", '
            "R7_G_CLS_HOLDER_INSTANCE_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gclsholderinstancea.py", "backend/temporal/gclsholderinstanceb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_cls_holder_instance.backend.facade.temporal.WORKFLOWS",
        "note": "class _H: L = [W]; _H().L[0].append(B)",
    },
    "r7_g_cls_dict_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_cls_dict_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_cls_dict_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_cls_dict_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gclsdictholdera.py": "class GClsDictHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gclsdictholderb.py": "class GClsDictHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_cls_dict_holder.backend.temporal.gclsdictholdera "
            "import GClsDictHolderA\n"
            "from "
            "products.r7_g_cls_dict_holder.backend.temporal.gclsdictholderb "
            "import GClsDictHolderB\n"
            "\n"
            "WORKFLOWS = [GClsDictHolderA]\n"
            "\n"
            "\n"
            "class _H:\n"
            '    D = {"w": WORKFLOWS}\n'
            "\n"
            "\n"
            '_H.D["w"].append(GClsDictHolderB)\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_cls_dict_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_CLS_DICT_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_CLS_DICT_HOLDER "
            "= "
            '[("r7_g_cls_dict_holder", '
            "R7_G_CLS_DICT_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gclsdictholdera.py", "backend/temporal/gclsdictholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_cls_dict_holder.backend.facade.temporal.WORKFLOWS",
        "note": "class _H: D = {'w': W}; _H.D['w'].append(B)",
    },
    "r7_g_holder_in_class_body": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_holder_in_class_body\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_holder_in_class_body", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_holder_in_class_body.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gholderinclassbodya.py": "class GHolderInClassBodyA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gholderinclassbodyb.py": "class GHolderInClassBodyB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_holder_in_class_body.backend.temporal.gholderinclassbodya "
            "import GHolderInClassBodyA\n"
            "from "
            "products.r7_g_holder_in_class_body.backend.temporal.gholderinclassbodyb "
            "import GHolderInClassBodyB\n"
            "\n"
            "WORKFLOWS = [GHolderInClassBodyA]\n"
            "_L = [WORKFLOWS]\n"
            "\n"
            "\n"
            "class _C:\n"
            "    _L[0].append(GHolderInClassBodyB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_holder_in_class_body.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_HOLDER_IN_CLASS_BODY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_HOLDER_IN_CLASS_BODY "
            "= "
            '[("r7_g_holder_in_class_body", '
            "R7_G_HOLDER_IN_CLASS_BODY_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gholderinclassbodya.py", "backend/temporal/gholderinclassbodyb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_holder_in_class_body.backend.facade.temporal.WORKFLOWS",
        "note": "_L = [W] at module level; class _C: _L[0].append(B)",
    },
    "r7_g_holder_for_class_body": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_holder_for_class_body\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_holder_for_class_body", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_holder_for_class_body.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gholderforclassbodya.py": "class GHolderForClassBodyA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gholderforclassbodyb.py": "class GHolderForClassBodyB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_holder_for_class_body.backend.temporal.gholderforclassbodya "
            "import GHolderForClassBodyA\n"
            "from "
            "products.r7_g_holder_for_class_body.backend.temporal.gholderforclassbodyb "
            "import GHolderForClassBodyB\n"
            "\n"
            "WORKFLOWS = [GHolderForClassBodyA]\n"
            "_L = [WORKFLOWS]\n"
            "\n"
            "\n"
            "class _C:\n"
            "    for _x in _L:\n"
            "        _x.append(GHolderForClassBodyB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_holder_for_class_body.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_HOLDER_FOR_CLASS_BODY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_HOLDER_FOR_CLASS_BODY "
            "= "
            '[("r7_g_holder_for_class_body", '
            "R7_G_HOLDER_FOR_CLASS_BODY_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gholderforclassbodya.py", "backend/temporal/gholderforclassbodyb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_holder_for_class_body.backend.facade.temporal.WORKFLOWS",
        "note": "_L = [W]; class _C: for _x in _L: _x.append(B)",
    },
    "r7_g_holder_bind_class_body": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_holder_bind_class_body\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_holder_bind_class_body", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_holder_bind_class_body.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gholderbindclassbodya.py": "class GHolderBindClassBodyA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gholderbindclassbodyb.py": "class GHolderBindClassBodyB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_holder_bind_class_body.backend.temporal.gholderbindclassbodya "
            "import GHolderBindClassBodyA\n"
            "from "
            "products.r7_g_holder_bind_class_body.backend.temporal.gholderbindclassbodyb "
            "import GHolderBindClassBodyB\n"
            "\n"
            "WORKFLOWS = [GHolderBindClassBodyA]\n"
            "_L = [WORKFLOWS]\n"
            "\n"
            "\n"
            "class _C:\n"
            "    X = _L[0]\n"
            "\n"
            "\n"
            "_C.X.append(GHolderBindClassBodyB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_holder_bind_class_body.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_HOLDER_BIND_CLASS_BODY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_HOLDER_BIND_CLASS_BODY "
            "= "
            '[("r7_g_holder_bind_class_body", '
            "R7_G_HOLDER_BIND_CLASS_BODY_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gholderbindclassbodya.py", "backend/temporal/gholderbindclassbodyb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_holder_bind_class_body.backend.facade.temporal.WORKFLOWS",
        "note": "_L = [W]; class _C: X = _L[0]; _C.X.append(B)",
    },
    "r7_g_holder_of_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_holder_of_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_holder_of_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_holder_of_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gholderofholdera.py": "class GHolderOfHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gholderofholderb.py": "class GHolderOfHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_holder_of_holder.backend.temporal.gholderofholdera "
            "import GHolderOfHolderA\n"
            "from "
            "products.r7_g_holder_of_holder.backend.temporal.gholderofholderb "
            "import GHolderOfHolderB\n"
            "\n"
            "WORKFLOWS = [GHolderOfHolderA]\n"
            "_LL = [[WORKFLOWS]]\n"
            "_LL[0][0].append(GHolderOfHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_holder_of_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_HOLDER_OF_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_HOLDER_OF_HOLDER "
            "= "
            '[("r7_g_holder_of_holder", '
            "R7_G_HOLDER_OF_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gholderofholdera.py", "backend/temporal/gholderofholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_holder_of_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_LL = [[W]]; _LL[0][0].append(B)",
    },
    "r7_g_dict_tuple_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_dict_tuple_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_dict_tuple_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_dict_tuple_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gdicttupleholdera.py": "class GDictTupleHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gdicttupleholderb.py": "class GDictTupleHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_dict_tuple_holder.backend.temporal.gdicttupleholdera "
            "import GDictTupleHolderA\n"
            "from "
            "products.r7_g_dict_tuple_holder.backend.temporal.gdicttupleholderb "
            "import GDictTupleHolderB\n"
            "\n"
            "WORKFLOWS = [GDictTupleHolderA]\n"
            '_D = {"a": (WORKFLOWS,)}\n'
            '_D["a"][0].append(GDictTupleHolderB)\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_dict_tuple_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_DICT_TUPLE_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_DICT_TUPLE_HOLDER "
            "= "
            '[("r7_g_dict_tuple_holder", '
            "R7_G_DICT_TUPLE_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gdicttupleholdera.py", "backend/temporal/gdicttupleholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_dict_tuple_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_D = {'a': (W,)}; _D['a'][0].append(B)",
    },
    "r7_g_either_second_step": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_either_second_step\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_either_second_step", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_either_second_step.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/geithersecondstepa.py": "class GEitherSecondStepA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/geithersecondstepb.py": "class GEitherSecondStepB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_either_second_step.backend.temporal.geithersecondstepa "
            "import GEitherSecondStepA\n"
            "from "
            "products.r7_g_either_second_step.backend.temporal.geithersecondstepb "
            "import GEitherSecondStepB\n"
            "\n"
            "WORKFLOWS = [GEitherSecondStepA]\n"
            "_L = [WORKFLOWS]\n"
            "_X = next(iter(_L))\n"
            "_Y = [_X]\n"
            "_Y[0].append(GEitherSecondStepB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_either_second_step.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_EITHER_SECOND_STEP_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_EITHER_SECOND_STEP "
            "= "
            '[("r7_g_either_second_step", '
            "R7_G_EITHER_SECOND_STEP_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/geithersecondstepa.py", "backend/temporal/geithersecondstepb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_either_second_step.backend.facade.temporal.WORKFLOWS",
        "note": "_X = next(iter(_L)); _Y = [_X]; _Y[0].append(B)",
    },
    "r7_g_comp_over_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_comp_over_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_comp_over_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_comp_over_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gcompoverholdera.py": "class GCompOverHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gcompoverholderb.py": "class GCompOverHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_comp_over_holder.backend.temporal.gcompoverholdera "
            "import GCompOverHolderA\n"
            "from "
            "products.r7_g_comp_over_holder.backend.temporal.gcompoverholderb "
            "import GCompOverHolderB\n"
            "\n"
            "WORKFLOWS = [GCompOverHolderA]\n"
            "_L = [WORKFLOWS]\n"
            "[x for x in _L][0].append(GCompOverHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_comp_over_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_COMP_OVER_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_COMP_OVER_HOLDER "
            "= "
            '[("r7_g_comp_over_holder", '
            "R7_G_COMP_OVER_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gcompoverholdera.py", "backend/temporal/gcompoverholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_comp_over_holder.backend.facade.temporal.WORKFLOWS",
        "note": "[x for x in _L][0].append(B)",
    },
    "r7_g_gen_over_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_gen_over_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_gen_over_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_gen_over_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/ggenoverholdera.py": "class GGenOverHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/ggenoverholderb.py": "class GGenOverHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_gen_over_holder.backend.temporal.ggenoverholdera "
            "import GGenOverHolderA\n"
            "from "
            "products.r7_g_gen_over_holder.backend.temporal.ggenoverholderb "
            "import GGenOverHolderB\n"
            "\n"
            "WORKFLOWS = [GGenOverHolderA]\n"
            "_L = [WORKFLOWS]\n"
            "_G = (x for x in _L)\n"
            "next(_G).append(GGenOverHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_gen_over_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_GEN_OVER_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_GEN_OVER_HOLDER "
            "= "
            '[("r7_g_gen_over_holder", '
            "R7_G_GEN_OVER_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/ggenoverholdera.py", "backend/temporal/ggenoverholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_gen_over_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_G = (x for x in _L); next(_G).append(B)",
    },
    "r7_g_ifexp_in_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_ifexp_in_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_ifexp_in_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_ifexp_in_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gifexpinholdera.py": "class GIfexpInHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gifexpinholderb.py": "class GIfexpInHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_ifexp_in_holder.backend.temporal.gifexpinholdera "
            "import GIfexpInHolderA\n"
            "from "
            "products.r7_g_ifexp_in_holder.backend.temporal.gifexpinholderb "
            "import GIfexpInHolderB\n"
            "\n"
            "WORKFLOWS = [GIfexpInHolderA]\n"
            "_L = [WORKFLOWS if True else None]\n"
            "_L[0].append(GIfexpInHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_ifexp_in_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_IFEXP_IN_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_IFEXP_IN_HOLDER "
            "= "
            '[("r7_g_ifexp_in_holder", '
            "R7_G_IFEXP_IN_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gifexpinholdera.py", "backend/temporal/gifexpinholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_ifexp_in_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_L = [W if c else None]; _L[0].append(B)",
    },
    "r7_g_or_in_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_or_in_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_or_in_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_or_in_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gorinholdera.py": "class GOrInHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gorinholderb.py": "class GOrInHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_or_in_holder.backend.temporal.gorinholdera "
            "import GOrInHolderA\n"
            "from "
            "products.r7_g_or_in_holder.backend.temporal.gorinholderb "
            "import GOrInHolderB\n"
            "\n"
            "WORKFLOWS = [GOrInHolderA]\n"
            "_L = [None or WORKFLOWS]\n"
            "_L[0].append(GOrInHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_or_in_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OR_IN_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OR_IN_HOLDER = "
            '[("r7_g_or_in_holder", '
            "R7_G_OR_IN_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gorinholdera.py", "backend/temporal/gorinholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_or_in_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_L = [None or W]; _L[0].append(B)",
    },
    "r7_g_max_star_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_max_star_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_max_star_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_max_star_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gmaxstarholdera.py": "class GMaxStarHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gmaxstarholderb.py": "class GMaxStarHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_max_star_holder.backend.temporal.gmaxstarholdera "
            "import GMaxStarHolderA\n"
            "from "
            "products.r7_g_max_star_holder.backend.temporal.gmaxstarholderb "
            "import GMaxStarHolderB\n"
            "\n"
            "WORKFLOWS = [GMaxStarHolderA]\n"
            "_L = [WORKFLOWS, []]\n"
            "max(*_L).append(GMaxStarHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_max_star_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_MAX_STAR_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_MAX_STAR_HOLDER "
            "= "
            '[("r7_g_max_star_holder", '
            "R7_G_MAX_STAR_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gmaxstarholdera.py", "backend/temporal/gmaxstarholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_max_star_holder.backend.facade.temporal.WORKFLOWS",
        "note": "max(*_L) with _L = [W, []] gives W",
    },
    "r7_g_min_star_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_min_star_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_min_star_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_min_star_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gminstarholdera.py": "class GMinStarHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gminstarholderb.py": "class GMinStarHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_min_star_holder.backend.temporal.gminstarholdera "
            "import GMinStarHolderA\n"
            "from "
            "products.r7_g_min_star_holder.backend.temporal.gminstarholderb "
            "import GMinStarHolderB\n"
            "\n"
            "WORKFLOWS = [GMinStarHolderA]\n"
            "_L = [WORKFLOWS, [GMinStarHolderA, "
            "GMinStarHolderA]]\n"
            "min(*_L).append(GMinStarHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_min_star_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_MIN_STAR_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_MIN_STAR_HOLDER "
            "= "
            '[("r7_g_min_star_holder", '
            "R7_G_MIN_STAR_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gminstarholdera.py", "backend/temporal/gminstarholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_min_star_holder.backend.facade.temporal.WORKFLOWS",
        "note": "min(*_L) with _L = [W, [A, A]] gives W",
    },
    "r7_g_sum_star_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_sum_star_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_sum_star_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_sum_star_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gsumstarholdera.py": "class GSumStarHolderA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gsumstarholderb.py": "class GSumStarHolderB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_sum_star_holder.backend.temporal.gsumstarholdera "
            "import GSumStarHolderA\n"
            "from "
            "products.r7_g_sum_star_holder.backend.temporal.gsumstarholderb "
            "import GSumStarHolderB\n"
            "\n"
            "WORKFLOWS = [GSumStarHolderA]\n"
            "_L = [(), WORKFLOWS]\n"
            "sum(*_L).append(GSumStarHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_sum_star_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_SUM_STAR_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_SUM_STAR_HOLDER "
            "= "
            '[("r7_g_sum_star_holder", '
            "R7_G_SUM_STAR_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gsumstarholdera.py", "backend/temporal/gsumstarholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_sum_star_holder.backend.facade.temporal.WORKFLOWS",
        "note": "sum(*_L) with _L = [(), W] gives W",
    },
    "r7_g_sum_start_kw": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_sum_start_kw\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_sum_start_kw", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_sum_start_kw.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gsumstartkwa.py": "class GSumStartKwA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gsumstartkwb.py": "class GSumStartKwB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_sum_start_kw.backend.temporal.gsumstartkwa "
            "import GSumStartKwA\n"
            "from "
            "products.r7_g_sum_start_kw.backend.temporal.gsumstartkwb "
            "import GSumStartKwB\n"
            "\n"
            "WORKFLOWS = [GSumStartKwA]\n"
            "sum((), start=WORKFLOWS).append(GSumStartKwB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_sum_start_kw.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_SUM_START_KW_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_SUM_START_KW = "
            '[("r7_g_sum_start_kw", '
            "R7_G_SUM_START_KW_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gsumstartkwa.py", "backend/temporal/gsumstartkwb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_sum_start_kw.backend.facade.temporal.WORKFLOWS",
        "note": "sum((), start=W).append(B)",
    },
    "r7_g_dict_kwargs_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_dict_kwargs_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_dict_kwargs_holder", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_dict_kwargs_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gdictkwargsholdera.py": "class GDictKwargsHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gdictkwargsholderb.py": "class GDictKwargsHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_dict_kwargs_holder.backend.temporal.gdictkwargsholdera "
            "import GDictKwargsHolderA\n"
            "from "
            "products.r7_g_dict_kwargs_holder.backend.temporal.gdictkwargsholderb "
            "import GDictKwargsHolderB\n"
            "\n"
            "WORKFLOWS = [GDictKwargsHolderA]\n"
            '_D = {"w": WORKFLOWS}\n'
            "_E = dict(**_D)\n"
            '_E["w"].append(GDictKwargsHolderB)\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_dict_kwargs_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_DICT_KWARGS_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_DICT_KWARGS_HOLDER "
            "= "
            '[("r7_g_dict_kwargs_holder", '
            "R7_G_DICT_KWARGS_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gdictkwargsholdera.py", "backend/temporal/gdictkwargsholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_dict_kwargs_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_E = dict(**_D); _E['w'].append(B)",
    },
    "r7_g_sorted_star_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_sorted_star_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_sorted_star_holder", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_sorted_star_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gsortedstarholdera.py": "class GSortedStarHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gsortedstarholderb.py": "class GSortedStarHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_sorted_star_holder.backend.temporal.gsortedstarholdera "
            "import GSortedStarHolderA\n"
            "from "
            "products.r7_g_sorted_star_holder.backend.temporal.gsortedstarholderb "
            "import GSortedStarHolderB\n"
            "\n"
            "WORKFLOWS = [GSortedStarHolderA]\n"
            "_LL = [[WORKFLOWS]]\n"
            "_S = sorted(*_LL)\n"
            "_S[0].append(GSortedStarHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_sorted_star_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_SORTED_STAR_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_SORTED_STAR_HOLDER "
            "= "
            '[("r7_g_sorted_star_holder", '
            "R7_G_SORTED_STAR_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gsortedstarholdera.py", "backend/temporal/gsortedstarholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_sorted_star_holder.backend.facade.temporal.WORKFLOWS",
        "note": "sorted(*_LL) with _LL = [[W]]; _S[0].append(B)",
    },
    "r7_g_op_name_alias": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_name_alias\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_name_alias", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_op_name_alias.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopnamealiasa.py": "class GOpNameAliasA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gopnamealiasb.py": "class GOpNameAliasB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_name_alias.backend.temporal.gopnamealiasa "
            "import GOpNameAliasA\n"
            "from "
            "products.r7_g_op_name_alias.backend.temporal.gopnamealiasb "
            "import GOpNameAliasB\n"
            "\n"
            "WORKFLOWS = [GOpNameAliasA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpNameAliasB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_Y = _Hook()\n"
            "_X = _Y\n"
            "WORKFLOWS == _X\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_name_alias.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_NAME_ALIAS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_NAME_ALIAS = "
            '[("r7_g_op_name_alias", '
            "R7_G_OP_NAME_ALIAS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopnamealiasa.py", "backend/temporal/gopnamealiasb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_name_alias.backend.facade.temporal.WORKFLOWS",
        "note": "_Y = _Hook(); _X = _Y; W == _X (_X is bound to a name only, so 'plain')",
    },
    "r7_g_op_class_alias_isinstance": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_class_alias_isinstance\nisolated: true\n",
            "package.json": '{"name": '
            '"@posthog/products-r7_g_op_class_alias_isinstance", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_class_alias_isinstance.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopclassaliasisinstancea.py": "class "
            "GOpClassAliasIsinstanceA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopclassaliasisinstanceb.py": "class "
            "GOpClassAliasIsinstanceB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_class_alias_isinstance.backend.temporal.gopclassaliasisinstancea "
            "import GOpClassAliasIsinstanceA\n"
            "from "
            "products.r7_g_op_class_alias_isinstance.backend.temporal.gopclassaliasisinstanceb "
            "import GOpClassAliasIsinstanceB\n"
            "\n"
            "WORKFLOWS = [GOpClassAliasIsinstanceA]\n"
            "\n"
            "\n"
            "class _Meta(type):\n"
            "    def __instancecheck__(cls, obj):\n"
            "        "
            "obj.append(GOpClassAliasIsinstanceB)\n"
            "        return True\n"
            "\n"
            "\n"
            "class _Any(metaclass=_Meta):\n"
            "    pass\n"
            "\n"
            "\n"
            "_K = _Any\n"
            "isinstance(WORKFLOWS, _K)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_class_alias_isinstance.backend.facade.temporal "
            "import WORKFLOWS "
            "as "
            "R7_G_OP_CLASS_ALIAS_ISINSTANCE_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_CLASS_ALIAS_ISINSTANCE "
            "= "
            '[("r7_g_op_class_alias_isinstance", '
            "R7_G_OP_CLASS_ALIAS_ISINSTANCE_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopclassaliasisinstancea.py", "backend/temporal/gopclassaliasisinstanceb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_class_alias_isinstance.backend.facade.temporal.WORKFLOWS",
        "note": "_K = _Any; isinstance(W, _K) (the metaclass hook)",
    },
    "r7_g_op_isinstance_tuple": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_isinstance_tuple\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_isinstance_tuple", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_isinstance_tuple.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopisinstancetuplea.py": "class GOpIsinstanceTupleA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopisinstancetupleb.py": "class GOpIsinstanceTupleB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_isinstance_tuple.backend.temporal.gopisinstancetuplea "
            "import GOpIsinstanceTupleA\n"
            "from "
            "products.r7_g_op_isinstance_tuple.backend.temporal.gopisinstancetupleb "
            "import GOpIsinstanceTupleB\n"
            "\n"
            "WORKFLOWS = [GOpIsinstanceTupleA]\n"
            "\n"
            "\n"
            "class _Meta(type):\n"
            "    def __instancecheck__(cls, obj):\n"
            "        obj.append(GOpIsinstanceTupleB)\n"
            "        return True\n"
            "\n"
            "\n"
            "class _Any(metaclass=_Meta):\n"
            "    pass\n"
            "\n"
            "\n"
            "isinstance(WORKFLOWS, (_Any, list))\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_isinstance_tuple.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_ISINSTANCE_TUPLE_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_ISINSTANCE_TUPLE "
            "= "
            '[("r7_g_op_isinstance_tuple", '
            "R7_G_OP_ISINSTANCE_TUPLE_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopisinstancetuplea.py", "backend/temporal/gopisinstancetupleb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_isinstance_tuple.backend.facade.temporal.WORKFLOWS",
        "note": "isinstance(W, (_Any, list)): a tuple display's element meets W itself",
    },
    "r7_g_op_match_class_alias": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_match_class_alias\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_match_class_alias", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_match_class_alias.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopmatchclassaliasa.py": "class GOpMatchClassAliasA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopmatchclassaliasb.py": "class GOpMatchClassAliasB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_match_class_alias.backend.temporal.gopmatchclassaliasa "
            "import GOpMatchClassAliasA\n"
            "from "
            "products.r7_g_op_match_class_alias.backend.temporal.gopmatchclassaliasb "
            "import GOpMatchClassAliasB\n"
            "\n"
            "WORKFLOWS = [GOpMatchClassAliasA]\n"
            "\n"
            "\n"
            "class _Meta(type):\n"
            "    def __instancecheck__(cls, obj):\n"
            "        obj.append(GOpMatchClassAliasB)\n"
            "        return True\n"
            "\n"
            "\n"
            "class _Any(metaclass=_Meta):\n"
            "    pass\n"
            "\n"
            "\n"
            "_K = _Any\n"
            "match WORKFLOWS:\n"
            "    case _K():\n"
            "        pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_match_class_alias.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_MATCH_CLASS_ALIAS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_MATCH_CLASS_ALIAS "
            "= "
            '[("r7_g_op_match_class_alias", '
            "R7_G_OP_MATCH_CLASS_ALIAS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopmatchclassaliasa.py", "backend/temporal/gopmatchclassaliasb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_match_class_alias.backend.facade.temporal.WORKFLOWS",
        "note": "_K = _Any; match W: case _K(): pass",
    },
    "r7_g_op_class_attr_alias": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_class_attr_alias\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_class_attr_alias", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_class_attr_alias.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopclassattraliasa.py": "class GOpClassAttrAliasA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopclassattraliasb.py": "class GOpClassAttrAliasB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_class_attr_alias.backend.temporal.gopclassattraliasa "
            "import GOpClassAttrAliasA\n"
            "from "
            "products.r7_g_op_class_attr_alias.backend.temporal.gopclassattraliasb "
            "import GOpClassAttrAliasB\n"
            "\n"
            "WORKFLOWS = [GOpClassAttrAliasA]\n"
            "\n"
            "\n"
            "class _Meta(type):\n"
            "    def __instancecheck__(cls, obj):\n"
            "        obj.append(GOpClassAttrAliasB)\n"
            "        return True\n"
            "\n"
            "\n"
            "class _Any(metaclass=_Meta):\n"
            "    pass\n"
            "\n"
            "\n"
            "class _C:\n"
            "    K = _Any\n"
            "\n"
            "\n"
            "isinstance(WORKFLOWS, _C.K)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_class_attr_alias.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_CLASS_ATTR_ALIAS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_CLASS_ATTR_ALIAS "
            "= "
            '[("r7_g_op_class_attr_alias", '
            "R7_G_OP_CLASS_ATTR_ALIAS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopclassattraliasa.py", "backend/temporal/gopclassattraliasb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_class_attr_alias.backend.facade.temporal.WORKFLOWS",
        "note": "class _C: K = _Any; isinstance(W, _C.K)",
    },
    "r7_g_op_rebound_if": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_rebound_if\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_rebound_if", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_op_rebound_if.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopreboundifa.py": "class GOpReboundIfA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gopreboundifb.py": "class GOpReboundIfB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_rebound_if.backend.temporal.gopreboundifa "
            "import GOpReboundIfA\n"
            "from "
            "products.r7_g_op_rebound_if.backend.temporal.gopreboundifb "
            "import GOpReboundIfB\n"
            "\n"
            "WORKFLOWS = [GOpReboundIfA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpReboundIfB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_X = 1\n"
            "if True:\n"
            "    _X = _Hook()\n"
            "WORKFLOWS == _X\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_rebound_if.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_REBOUND_IF_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_REBOUND_IF = "
            '[("r7_g_op_rebound_if", '
            "R7_G_OP_REBOUND_IF_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopreboundifa.py", "backend/temporal/gopreboundifb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_rebound_if.backend.facade.temporal.WORKFLOWS",
        "note": "_X = 1; if True: _X = _Hook(); W == _X (the survey reads top-level bindings only)",
    },
    "r7_g_op_rebound_for": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_rebound_for\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_rebound_for", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_rebound_for.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopreboundfora.py": "class GOpReboundForA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gopreboundforb.py": "class GOpReboundForB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_rebound_for.backend.temporal.gopreboundfora "
            "import GOpReboundForA\n"
            "from "
            "products.r7_g_op_rebound_for.backend.temporal.gopreboundforb "
            "import GOpReboundForB\n"
            "\n"
            "WORKFLOWS = [GOpReboundForA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpReboundForB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_X = 1\n"
            "for _X in [_Hook()]:\n"
            "    pass\n"
            "WORKFLOWS == _X\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_rebound_for.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_REBOUND_FOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_REBOUND_FOR "
            '= [("r7_g_op_rebound_for", '
            "R7_G_OP_REBOUND_FOR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopreboundfora.py", "backend/temporal/gopreboundforb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_rebound_for.backend.facade.temporal.WORKFLOWS",
        "note": "_X = 1; for _X in [_Hook()]: pass; W == _X",
    },
    "r7_g_op_rebound_walrus": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_rebound_walrus\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_rebound_walrus", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_rebound_walrus.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopreboundwalrusa.py": "class GOpReboundWalrusA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopreboundwalrusb.py": "class GOpReboundWalrusB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_rebound_walrus.backend.temporal.gopreboundwalrusa "
            "import GOpReboundWalrusA\n"
            "from "
            "products.r7_g_op_rebound_walrus.backend.temporal.gopreboundwalrusb "
            "import GOpReboundWalrusB\n"
            "\n"
            "WORKFLOWS = [GOpReboundWalrusA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpReboundWalrusB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_X = 1\n"
            "(_X := _Hook())\n"
            "WORKFLOWS == _X\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_rebound_walrus.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_REBOUND_WALRUS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_REBOUND_WALRUS "
            "= "
            '[("r7_g_op_rebound_walrus", '
            "R7_G_OP_REBOUND_WALRUS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopreboundwalrusa.py", "backend/temporal/gopreboundwalrusb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_rebound_walrus.backend.facade.temporal.WORKFLOWS",
        "note": "_X = 1; (_X := _Hook()); W == _X",
    },
    "r7_g_op_rebound_augassign": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_rebound_augassign\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_rebound_augassign", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_rebound_augassign.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopreboundaugassigna.py": "class GOpReboundAugassignA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopreboundaugassignb.py": "class GOpReboundAugassignB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_rebound_augassign.backend.temporal.gopreboundaugassigna "
            "import GOpReboundAugassignA\n"
            "from "
            "products.r7_g_op_rebound_augassign.backend.temporal.gopreboundaugassignb "
            "import GOpReboundAugassignB\n"
            "\n"
            "WORKFLOWS = [GOpReboundAugassignA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __radd__(self, other):\n"
            "        return self\n"
            "\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpReboundAugassignB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_X = 1\n"
            "_X += _Hook()\n"
            "WORKFLOWS == _X\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_rebound_augassign.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_REBOUND_AUGASSIGN_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_REBOUND_AUGASSIGN "
            "= "
            '[("r7_g_op_rebound_augassign", '
            "R7_G_OP_REBOUND_AUGASSIGN_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopreboundaugassigna.py", "backend/temporal/gopreboundaugassignb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_rebound_augassign.backend.facade.temporal.WORKFLOWS",
        "note": "_X = 1; _X += _Hook() (now a _Hook); W == _X",
    },
    "r7_g_op_rebound_global_func": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_rebound_global_func\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_rebound_global_func", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_rebound_global_func.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopreboundglobalfunca.py": "class GOpReboundGlobalFuncA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopreboundglobalfuncb.py": "class GOpReboundGlobalFuncB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_rebound_global_func.backend.temporal.gopreboundglobalfunca "
            "import GOpReboundGlobalFuncA\n"
            "from "
            "products.r7_g_op_rebound_global_func.backend.temporal.gopreboundglobalfuncb "
            "import GOpReboundGlobalFuncB\n"
            "\n"
            "WORKFLOWS = [GOpReboundGlobalFuncA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpReboundGlobalFuncB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_X = 1\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global _X\n"
            "    _X = _Hook()\n"
            "\n"
            "\n"
            "_f()\n"
            "WORKFLOWS == _X\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_rebound_global_func.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_REBOUND_GLOBAL_FUNC_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_REBOUND_GLOBAL_FUNC "
            "= "
            '[("r7_g_op_rebound_global_func", '
            "R7_G_OP_REBOUND_GLOBAL_FUNC_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopreboundglobalfunca.py", "backend/temporal/gopreboundglobalfuncb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_rebound_global_func.backend.facade.temporal.WORKFLOWS",
        "note": "_X = 1; a function rebinds global _X = _Hook(), called at module level; W == _X",
    },
    "r7_g_op_builtin_shadow": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_builtin_shadow\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_builtin_shadow", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_builtin_shadow.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopbuiltinshadowa.py": "class GOpBuiltinShadowA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopbuiltinshadowb.py": "class GOpBuiltinShadowB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_builtin_shadow.backend.temporal.gopbuiltinshadowa "
            "import GOpBuiltinShadowA\n"
            "from "
            "products.r7_g_op_builtin_shadow.backend.temporal.gopbuiltinshadowb "
            "import GOpBuiltinShadowB\n"
            "\n"
            "WORKFLOWS = [GOpBuiltinShadowA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpBuiltinShadowB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "for print in [_Hook()]:  # noqa: A001\n"
            "    pass\n"
            "WORKFLOWS == print\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_builtin_shadow.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_BUILTIN_SHADOW_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_BUILTIN_SHADOW "
            "= "
            '[("r7_g_op_builtin_shadow", '
            "R7_G_OP_BUILTIN_SHADOW_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopbuiltinshadowa.py", "backend/temporal/gopbuiltinshadowb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_builtin_shadow.backend.facade.temporal.WORKFLOWS",
        "note": "a builtin name shadowed by a for target: W == print",
    },
    "r7_g_op_loop_item": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_loop_item\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_loop_item", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_g_op_loop_item.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/goploopitema.py": "class GOpLoopItemA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/goploopitemb.py": "class GOpLoopItemB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_loop_item.backend.temporal.goploopitema "
            "import GOpLoopItemA\n"
            "from "
            "products.r7_g_op_loop_item.backend.temporal.goploopitemb "
            "import GOpLoopItemB\n"
            "\n"
            "WORKFLOWS = [GOpLoopItemA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpLoopItemB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_HOOKS = [_Hook()]\n"
            "for _h in _HOOKS:\n"
            "    WORKFLOWS == _h\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_loop_item.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_LOOP_ITEM_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_LOOP_ITEM = "
            '[("r7_g_op_loop_item", '
            "R7_G_OP_LOOP_ITEM_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/goploopitema.py", "backend/temporal/goploopitemb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_loop_item.backend.facade.temporal.WORKFLOWS",
        "note": "for _h in _HOOKS: W == _h (an item of a module-level name that is a first-party instance)",
    },
    "r7_g_op_item_subscript": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_item_subscript\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_item_subscript", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_item_subscript.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopitemsubscripta.py": "class GOpItemSubscriptA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopitemsubscriptb.py": "class GOpItemSubscriptB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_item_subscript.backend.temporal.gopitemsubscripta "
            "import GOpItemSubscriptA\n"
            "from "
            "products.r7_g_op_item_subscript.backend.temporal.gopitemsubscriptb "
            "import GOpItemSubscriptB\n"
            "\n"
            "WORKFLOWS = [GOpItemSubscriptA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpItemSubscriptB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_HOOKS = [_Hook()]\n"
            "WORKFLOWS == _HOOKS[0]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_item_subscript.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_ITEM_SUBSCRIPT_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_ITEM_SUBSCRIPT "
            "= "
            '[("r7_g_op_item_subscript", '
            "R7_G_OP_ITEM_SUBSCRIPT_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopitemsubscripta.py", "backend/temporal/gopitemsubscriptb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_item_subscript.backend.facade.temporal.WORKFLOWS",
        "note": "W == _HOOKS[0], _HOOKS = [_Hook()]",
    },
    "r7_g_op_tuple_display": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_tuple_display\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_tuple_display", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_tuple_display.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/goptupledisplaya.py": "class GOpTupleDisplayA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/goptupledisplayb.py": "class GOpTupleDisplayB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_tuple_display.backend.temporal.goptupledisplaya "
            "import GOpTupleDisplayA\n"
            "from "
            "products.r7_g_op_tuple_display.backend.temporal.goptupledisplayb "
            "import GOpTupleDisplayB\n"
            "\n"
            "WORKFLOWS = [GOpTupleDisplayA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpTupleDisplayB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_h = _Hook()\n"
            "(WORKFLOWS,) == (_h,)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_tuple_display.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_TUPLE_DISPLAY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_TUPLE_DISPLAY "
            "= "
            '[("r7_g_op_tuple_display", '
            "R7_G_OP_TUPLE_DISPLAY_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/goptupledisplaya.py", "backend/temporal/goptupledisplayb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_tuple_display.backend.facade.temporal.WORKFLOWS",
        "note": "(W,) == (_h,): the displays' elements meet, W == _h",
    },
    "r7_g_op_holder_display": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_holder_display\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_holder_display", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_holder_display.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopholderdisplaya.py": "class GOpHolderDisplayA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopholderdisplayb.py": "class GOpHolderDisplayB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_holder_display.backend.temporal.gopholderdisplaya "
            "import GOpHolderDisplayA\n"
            "from "
            "products.r7_g_op_holder_display.backend.temporal.gopholderdisplayb "
            "import GOpHolderDisplayB\n"
            "\n"
            "WORKFLOWS = [GOpHolderDisplayA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpHolderDisplayB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_h = _Hook()\n"
            "_L = [WORKFLOWS]\n"
            "_L == [_h]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_holder_display.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_HOLDER_DISPLAY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_HOLDER_DISPLAY "
            "= "
            '[("r7_g_op_holder_display", '
            "R7_G_OP_HOLDER_DISPLAY_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopholderdisplaya.py", "backend/temporal/gopholderdisplayb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_holder_display.backend.facade.temporal.WORKFLOWS",
        "note": "_L = [W]; _L == [_h]",
    },
    "r7_g_op_in_display_alias": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_op_in_display_alias\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_op_in_display_alias", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_op_in_display_alias.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gopindisplayaliasa.py": "class GOpInDisplayAliasA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gopindisplayaliasb.py": "class GOpInDisplayAliasB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_op_in_display_alias.backend.temporal.gopindisplayaliasa "
            "import GOpInDisplayAliasA\n"
            "from "
            "products.r7_g_op_in_display_alias.backend.temporal.gopindisplayaliasb "
            "import GOpInDisplayAliasB\n"
            "\n"
            "WORKFLOWS = [GOpInDisplayAliasA]\n"
            "\n"
            "\n"
            "class _Hook:\n"
            "    def __eq__(self, other):\n"
            "        other.append(GOpInDisplayAliasB)\n"
            "        return True\n"
            "\n"
            "    __hash__ = None\n"
            "\n"
            "\n"
            "_h = _Hook()\n"
            "_h2 = _h\n"
            "WORKFLOWS in [_h2]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_op_in_display_alias.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_OP_IN_DISPLAY_ALIAS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_OP_IN_DISPLAY_ALIAS "
            "= "
            '[("r7_g_op_in_display_alias", '
            "R7_G_OP_IN_DISPLAY_ALIAS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gopindisplayaliasa.py", "backend/temporal/gopindisplayaliasb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_op_in_display_alias.backend.facade.temporal.WORKFLOWS",
        "note": "_h2 = _h; W in [_h2] (_h2 == W)",
    },
    "r7_g_aug_target_hook": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_aug_target_hook\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_aug_target_hook", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_aug_target_hook.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gaugtargethooka.py": "class GAugTargetHookA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gaugtargethookb.py": "class GAugTargetHookB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_aug_target_hook.backend.temporal.gaugtargethooka "
            "import GAugTargetHookA\n"
            "from "
            "products.r7_g_aug_target_hook.backend.temporal.gaugtargethookb "
            "import GAugTargetHookB\n"
            "\n"
            "WORKFLOWS = [GAugTargetHookA]\n"
            "\n"
            "\n"
            "class _Acc:\n"
            "    def __iadd__(self, other):\n"
            "        other.append(GAugTargetHookB)\n"
            "        return self\n"
            "\n"
            "\n"
            "_ACC = _Acc()\n"
            "_ACC += WORKFLOWS\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_aug_target_hook.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_AUG_TARGET_HOOK_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_AUG_TARGET_HOOK "
            "= "
            '[("r7_g_aug_target_hook", '
            "R7_G_AUG_TARGET_HOOK_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gaugtargethooka.py", "backend/temporal/gaugtargethookb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_aug_target_hook.backend.facade.temporal.WORKFLOWS",
        "note": "_ACC = _Acc(); _ACC += W (_Acc.__iadd__(W))",
    },
    "r7_g_aug_target_hook_holder": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_aug_target_hook_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_aug_target_hook_holder", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_aug_target_hook_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gaugtargethookholdera.py": "class GAugTargetHookHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gaugtargethookholderb.py": "class GAugTargetHookHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_aug_target_hook_holder.backend.temporal.gaugtargethookholdera "
            "import GAugTargetHookHolderA\n"
            "from "
            "products.r7_g_aug_target_hook_holder.backend.temporal.gaugtargethookholderb "
            "import GAugTargetHookHolderB\n"
            "\n"
            "WORKFLOWS = [GAugTargetHookHolderA]\n"
            "\n"
            "\n"
            "class _Acc:\n"
            "    def __iadd__(self, other):\n"
            "        "
            "other[0].append(GAugTargetHookHolderB)\n"
            "        return self\n"
            "\n"
            "\n"
            "_ACC = _Acc()\n"
            "_ACC += [WORKFLOWS]\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_aug_target_hook_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_AUG_TARGET_HOOK_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_AUG_TARGET_HOOK_HOLDER "
            "= "
            '[("r7_g_aug_target_hook_holder", '
            "R7_G_AUG_TARGET_HOOK_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gaugtargethookholdera.py", "backend/temporal/gaugtargethookholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_aug_target_hook_holder.backend.facade.temporal.WORKFLOWS",
        "note": "_ACC += [W] (_Acc.__iadd__([W]))",
    },
    "r7_g_aug_holder_radd": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_aug_holder_radd\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_aug_holder_radd", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_aug_holder_radd.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gaugholderradda.py": "class GAugHolderRaddA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gaugholderraddb.py": "class GAugHolderRaddB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_g_aug_holder_radd.backend.temporal.gaugholderradda "
            "import GAugHolderRaddA\n"
            "from "
            "products.r7_g_aug_holder_radd.backend.temporal.gaugholderraddb "
            "import GAugHolderRaddB\n"
            "\n"
            "WORKFLOWS = [GAugHolderRaddA]\n"
            "\n"
            "\n"
            "class _R:\n"
            "    def __radd__(self, other):\n"
            "        other[0].append(GAugHolderRaddB)\n"
            "        return other\n"
            "\n"
            "\n"
            "_T = (WORKFLOWS,)\n"
            "_T += _R()\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_aug_holder_radd.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_AUG_HOLDER_RADD_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_AUG_HOLDER_RADD "
            "= "
            '[("r7_g_aug_holder_radd", '
            "R7_G_AUG_HOLDER_RADD_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gaugholderradda.py", "backend/temporal/gaugholderraddb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_aug_holder_radd.backend.facade.temporal.WORKFLOWS",
        "note": "_T = (W,); _T += _R() (_R.__radd__(_T))",
    },
    "r7_g_stdlib_dict_patch": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_stdlib_dict_patch\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_stdlib_dict_patch", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_stdlib_dict_patch.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gstdlibdictpatcha.py": "class GStdlibDictPatchA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gstdlibdictpatchb.py": "class GStdlibDictPatchB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "import copy\n"
            "from "
            "products.r7_g_stdlib_dict_patch.backend.temporal.gstdlibdictpatcha "
            "import GStdlibDictPatchA\n"
            "from "
            "products.r7_g_stdlib_dict_patch.backend.temporal.gstdlibdictpatchb "
            "import GStdlibDictPatchB\n"
            "\n"
            "WORKFLOWS = [GStdlibDictPatchA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    items.append(GStdlibDictPatchB)\n"
            "    return items\n"
            "\n"
            "\n"
            'copy.__dict__["copy"] = _grab\n'
            "copy.copy(WORKFLOWS)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_stdlib_dict_patch.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_STDLIB_DICT_PATCH_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_STDLIB_DICT_PATCH "
            "= "
            '[("r7_g_stdlib_dict_patch", '
            "R7_G_STDLIB_DICT_PATCH_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gstdlibdictpatcha.py", "backend/temporal/gstdlibdictpatchb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_stdlib_dict_patch.backend.facade.temporal.WORKFLOWS",
        "note": "copy.__dict__['copy'] = _grab; copy.copy(W)",
    },
    "r7_g_stdlib_alias_patch": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_stdlib_alias_patch\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_stdlib_alias_patch", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_stdlib_alias_patch.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gstdlibaliaspatcha.py": "class GStdlibAliasPatchA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gstdlibaliaspatchb.py": "class GStdlibAliasPatchB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "import copy\n"
            "import copy as _c2\n"
            "from "
            "products.r7_g_stdlib_alias_patch.backend.temporal.gstdlibaliaspatcha "
            "import GStdlibAliasPatchA\n"
            "from "
            "products.r7_g_stdlib_alias_patch.backend.temporal.gstdlibaliaspatchb "
            "import GStdlibAliasPatchB\n"
            "\n"
            "WORKFLOWS = [GStdlibAliasPatchA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    items.append(GStdlibAliasPatchB)\n"
            "    return items\n"
            "\n"
            "\n"
            "_c2.copy = _grab\n"
            "copy.copy(WORKFLOWS)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_stdlib_alias_patch.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_STDLIB_ALIAS_PATCH_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_STDLIB_ALIAS_PATCH "
            "= "
            '[("r7_g_stdlib_alias_patch", '
            "R7_G_STDLIB_ALIAS_PATCH_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gstdlibaliaspatcha.py", "backend/temporal/gstdlibaliaspatchb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_stdlib_alias_patch.backend.facade.temporal.WORKFLOWS",
        "note": "import copy as _c2; _c2.copy = _grab; copy.copy(W)",
    },
    "r7_g_stdlib_dunder_import_patch": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_stdlib_dunder_import_patch\nisolated: true\n",
            "package.json": '{"name": '
            '"@posthog/products-r7_g_stdlib_dunder_import_patch", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_stdlib_dunder_import_patch.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gstdlibdunderimportpatcha.py": "class "
            "GStdlibDunderImportPatchA:\n"
            "    def run(self, "
            "x):\n"
            "        return x\n",
            "backend/temporal/gstdlibdunderimportpatchb.py": "class "
            "GStdlibDunderImportPatchB:\n"
            "    def run(self, "
            "x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "import copy\n"
            "from "
            "products.r7_g_stdlib_dunder_import_patch.backend.temporal.gstdlibdunderimportpatcha "
            "import GStdlibDunderImportPatchA\n"
            "from "
            "products.r7_g_stdlib_dunder_import_patch.backend.temporal.gstdlibdunderimportpatchb "
            "import GStdlibDunderImportPatchB\n"
            "\n"
            "WORKFLOWS = [GStdlibDunderImportPatchA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    "
            "items.append(GStdlibDunderImportPatchB)\n"
            "    return items\n"
            "\n"
            "\n"
            '__import__("copy").copy = _grab\n'
            "copy.copy(WORKFLOWS)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_stdlib_dunder_import_patch.backend.facade.temporal "
            "import "
            "WORKFLOWS as "
            "R7_G_STDLIB_DUNDER_IMPORT_PATCH_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_STDLIB_DUNDER_IMPORT_PATCH "
            "= "
            '[("r7_g_stdlib_dunder_import_patch", '
            "R7_G_STDLIB_DUNDER_IMPORT_PATCH_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gstdlibdunderimportpatcha.py", "backend/temporal/gstdlibdunderimportpatchb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_stdlib_dunder_import_patch.backend.facade.temporal.WORKFLOWS",
        "note": "__import__('copy').copy = _grab; copy.copy(W)",
    },
    "r7_g_stdlib_setattr_alias": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_stdlib_setattr_alias\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_stdlib_setattr_alias", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_stdlib_setattr_alias.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gstdlibsetattraliasa.py": "class GStdlibSetattrAliasA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gstdlibsetattraliasb.py": "class GStdlibSetattrAliasB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "import copy\n"
            "import copy as _c2\n"
            "from "
            "products.r7_g_stdlib_setattr_alias.backend.temporal.gstdlibsetattraliasa "
            "import GStdlibSetattrAliasA\n"
            "from "
            "products.r7_g_stdlib_setattr_alias.backend.temporal.gstdlibsetattraliasb "
            "import GStdlibSetattrAliasB\n"
            "\n"
            "WORKFLOWS = [GStdlibSetattrAliasA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    items.append(GStdlibSetattrAliasB)\n"
            "    return items\n"
            "\n"
            "\n"
            'setattr(_c2, "copy", _grab)\n'
            "copy.copy(WORKFLOWS)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_stdlib_setattr_alias.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_STDLIB_SETATTR_ALIAS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_STDLIB_SETATTR_ALIAS "
            "= "
            '[("r7_g_stdlib_setattr_alias", '
            "R7_G_STDLIB_SETATTR_ALIAS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gstdlibsetattraliasa.py", "backend/temporal/gstdlibsetattraliasb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_stdlib_setattr_alias.backend.facade.temporal.WORKFLOWS",
        "note": "setattr(_c2, 'copy', _grab) (_c2 is copy); copy.copy(W)",
    },
    "r7_g_json_dict_patch": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_json_dict_patch\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_g_json_dict_patch", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_json_dict_patch.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gjsondictpatcha.py": "class GJsonDictPatchA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/gjsondictpatchb.py": "class GJsonDictPatchB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import json\n"
            "from "
            "products.r7_g_json_dict_patch.backend.temporal.gjsondictpatcha "
            "import GJsonDictPatchA\n"
            "from "
            "products.r7_g_json_dict_patch.backend.temporal.gjsondictpatchb "
            "import GJsonDictPatchB\n"
            "\n"
            "WORKFLOWS = [GJsonDictPatchA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    items.append(GJsonDictPatchB)\n"
            "    return items\n"
            "\n"
            "\n"
            'json.__dict__["dumps"] = _grab\n'
            "_J = json.dumps(WORKFLOWS)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_json_dict_patch.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_G_JSON_DICT_PATCH_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_JSON_DICT_PATCH "
            "= "
            '[("r7_g_json_dict_patch", '
            "R7_G_JSON_DICT_PATCH_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gjsondictpatcha.py", "backend/temporal/gjsondictpatchb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_json_dict_patch.backend.facade.temporal.WORKFLOWS",
        "note": "json.__dict__['dumps'] = _grab; json.dumps(W)",
    },
    "r7_g_from_import_after_patch": {
        "group": "g",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_g_from_import_after_patch\nisolated: true\n",
            "package.json": '{"name": '
            '"@posthog/products-r7_g_from_import_after_patch", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_g_from_import_after_patch.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/gfromimportafterpatcha.py": "class "
            "GFromImportAfterPatchA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/gfromimportafterpatchb.py": "class "
            "GFromImportAfterPatchB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "import copy as _c2\n"
            "from "
            "products.r7_g_from_import_after_patch.backend.temporal.gfromimportafterpatcha "
            "import GFromImportAfterPatchA\n"
            "from "
            "products.r7_g_from_import_after_patch.backend.temporal.gfromimportafterpatchb "
            "import GFromImportAfterPatchB\n"
            "\n"
            "WORKFLOWS = [GFromImportAfterPatchA]\n"
            "\n"
            "\n"
            "def _grab(items, *args, **kwargs):\n"
            "    items.append(GFromImportAfterPatchB)\n"
            "    return items\n"
            "\n"
            "\n"
            "_c2.copy = _grab\n"
            "from copy import copy as _cp  # noqa: E402\n"
            "\n"
            "_cp(WORKFLOWS)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_g_from_import_after_patch.backend.facade.temporal "
            "import WORKFLOWS "
            "as "
            "R7_G_FROM_IMPORT_AFTER_PATCH_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_G_FROM_IMPORT_AFTER_PATCH "
            "= "
            '[("r7_g_from_import_after_patch", '
            "R7_G_FROM_IMPORT_AFTER_PATCH_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/gfromimportafterpatcha.py", "backend/temporal/gfromimportafterpatchb.py"],
        "outcome": "unread",
        "reason": "products.r7_g_from_import_after_patch.backend.facade.temporal.WORKFLOWS",
        "note": "_c2.copy = _grab, then from copy import copy as _cp; _cp(W)",
    },
    "r7_r_rec_bool": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_bool\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_bool", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_bool.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecboola.py": "class RRecBoolA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecboolb.py": "class RRecBoolB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_rec_bool.backend.temporal.rec import "
            "Rec\n"
            "from products.r7_r_rec_bool.backend.temporal.rrecboola "
            "import RRecBoolA\n"
            "from products.r7_r_rec_bool.backend.temporal.rrecboolb "
            "import RRecBoolB\n"
            "\n"
            "WORKFLOWS = [RRecBoolA]\n"
            "BINDING = Rec(WORKFLOWS)\n"
            "if BINDING:\n"
            "    pass\n",
            "backend/temporal/rec.py": "class Rec:\n"
            "    def __init__(self, workflows):\n"
            "        self.workflows = workflows\n"
            "\n"
            "    def __bool__(self):\n"
            "        from "
            "products.r7_r_rec_bool.backend.temporal.rrecboolb import "
            "RRecBoolB\n"
            "\n"
            "        self.workflows.append(RRecBoolB)\n"
            "        return True\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_bool.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_BOOL_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_BOOL = "
            '[("r7_r_rec_bool", '
            "R7_R_REC_BOOL_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/rrecboola.py", "backend/temporal/rrecboolb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_bool.backend.facade.temporal.WORKFLOWS",
        "note": "BINDING = Rec(W); if BINDING: (Rec.__bool__ appends)",
    },
    "r7_r_rec_print": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_print\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_print", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_print.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecprinta.py": "class RRecPrintA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecprintb.py": "class RRecPrintB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_rec_print.backend.temporal.rec import "
            "Rec\n"
            "from products.r7_r_rec_print.backend.temporal.rrecprinta "
            "import RRecPrintA\n"
            "from products.r7_r_rec_print.backend.temporal.rrecprintb "
            "import RRecPrintB\n"
            "\n"
            "WORKFLOWS = [RRecPrintA]\n"
            "BINDING = Rec(WORKFLOWS)\n"
            "print(BINDING)\n",
            "backend/temporal/rec.py": "class Rec:\n"
            "    def __init__(self, workflows):\n"
            "        self.workflows = workflows\n"
            "\n"
            "    def __repr__(self):\n"
            "        from "
            "products.r7_r_rec_print.backend.temporal.rrecprintb import "
            "RRecPrintB\n"
            "\n"
            "        self.workflows.append(RRecPrintB)\n"
            "        return 'Rec'\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_print.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_PRINT_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_PRINT = "
            '[("r7_r_rec_print", '
            "R7_R_REC_PRINT_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecprinta.py", "backend/temporal/rrecprintb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_print.backend.facade.temporal.WORKFLOWS",
        "note": "print(BINDING) (Rec.__repr__ appends)",
    },
    "r7_r_rec_eq": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_eq\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_eq", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_eq.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rreceqa.py": "class RRecEqA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rreceqb.py": "class RRecEqB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_rec_eq.backend.temporal.rec import Rec\n"
            "from products.r7_r_rec_eq.backend.temporal.rreceqa import "
            "RRecEqA\n"
            "from products.r7_r_rec_eq.backend.temporal.rreceqb import "
            "RRecEqB\n"
            "\n"
            "WORKFLOWS = [RRecEqA]\n"
            "BINDING = Rec(WORKFLOWS)\n"
            "BINDING == 1\n",
            "backend/temporal/rec.py": "class Rec:\n"
            "    def __init__(self, workflows):\n"
            "        self.workflows = workflows\n"
            "\n"
            "    def __eq__(self, other):\n"
            "        from products.r7_r_rec_eq.backend.temporal.rreceqb "
            "import RRecEqB\n"
            "\n"
            "        self.workflows.append(RRecEqB)\n"
            "        return False\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_eq.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_EQ_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_EQ = "
            '[("r7_r_rec_eq", '
            "R7_R_REC_EQ_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/rreceqa.py", "backend/temporal/rreceqb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_eq.backend.facade.temporal.WORKFLOWS",
        "note": "BINDING == 1 (Rec.__eq__ appends; the other operand is plain)",
    },
    "r7_r_rec_len": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_len\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_len", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_len.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rreclena.py": "class RRecLenA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rreclenb.py": "class RRecLenB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_rec_len.backend.temporal.rec import Rec\n"
            "from products.r7_r_rec_len.backend.temporal.rreclena import "
            "RRecLenA\n"
            "from products.r7_r_rec_len.backend.temporal.rreclenb import "
            "RRecLenB\n"
            "\n"
            "WORKFLOWS = [RRecLenA]\n"
            "BINDING = Rec(WORKFLOWS)\n"
            "_N = len(BINDING)\n",
            "backend/temporal/rec.py": "class Rec:\n"
            "    def __init__(self, workflows):\n"
            "        self.workflows = workflows\n"
            "\n"
            "    def __len__(self):\n"
            "        from products.r7_r_rec_len.backend.temporal.rreclenb "
            "import RRecLenB\n"
            "\n"
            "        self.workflows.append(RRecLenB)\n"
            "        return 0\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_len.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_LEN_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_LEN = "
            '[("r7_r_rec_len", '
            "R7_R_REC_LEN_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/rreclena.py", "backend/temporal/rreclenb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_len.backend.facade.temporal.WORKFLOWS",
        "note": "len(BINDING) (Rec.__len__ appends)",
    },
    "r7_r_rec_fstring": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_fstring\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_fstring", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_fstring.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecfstringa.py": "class RRecFstringA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecfstringb.py": "class RRecFstringB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_rec_fstring.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_fstring.backend.temporal.rrecfstringa "
            "import RRecFstringA\n"
            "from "
            "products.r7_r_rec_fstring.backend.temporal.rrecfstringb "
            "import RRecFstringB\n"
            "\n"
            "WORKFLOWS = [RRecFstringA]\n"
            "BINDING = Rec(WORKFLOWS)\n"
            '_S = f"{BINDING}"\n',
            "backend/temporal/rec.py": "class Rec:\n"
            "    def __init__(self, workflows):\n"
            "        self.workflows = workflows\n"
            "\n"
            "    def __format__(self, spec):\n"
            "        from "
            "products.r7_r_rec_fstring.backend.temporal.rrecfstringb "
            "import RRecFstringB\n"
            "\n"
            "        self.workflows.append(RRecFstringB)\n"
            "        return 'Rec'\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_fstring.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_FSTRING_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_FSTRING = "
            '[("r7_r_rec_fstring", '
            "R7_R_REC_FSTRING_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecfstringa.py", "backend/temporal/rrecfstringb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_fstring.backend.facade.temporal.WORKFLOWS",
        "note": "f'{BINDING}' (Rec.__format__ appends)",
    },
    "r7_r_rec_iter_star": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_iter_star\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_iter_star", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_iter_star.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rreciterstara.py": "class RRecIterStarA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rreciterstarb.py": "class RRecIterStarB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_rec_iter_star.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_iter_star.backend.temporal.rreciterstara "
            "import RRecIterStarA\n"
            "from "
            "products.r7_r_rec_iter_star.backend.temporal.rreciterstarb "
            "import RRecIterStarB\n"
            "\n"
            "WORKFLOWS = [RRecIterStarA]\n"
            "BINDING = Rec(WORKFLOWS)\n"
            "_A = [*BINDING]\n",
            "backend/temporal/rec.py": "class Rec:\n"
            "    def __init__(self, workflows):\n"
            "        self.workflows = workflows\n"
            "\n"
            "    def __iter__(self):\n"
            "        from "
            "products.r7_r_rec_iter_star.backend.temporal.rreciterstarb "
            "import RRecIterStarB\n"
            "\n"
            "        self.workflows.append(RRecIterStarB)\n"
            "        return iter(self.workflows)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_iter_star.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_ITER_STAR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_ITER_STAR = "
            '[("r7_r_rec_iter_star", '
            "R7_R_REC_ITER_STAR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rreciterstara.py", "backend/temporal/rreciterstarb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_iter_star.backend.facade.temporal.WORKFLOWS",
        "note": "[*BINDING] (Rec.__iter__ appends)",
    },
    "r7_r_cls_meta_bool": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_cls_meta_bool\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_cls_meta_bool", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_cls_meta_bool.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rclsmetaboola.py": "class RClsMetaBoolA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rclsmetaboolb.py": "class RClsMetaBoolB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_cls_meta_bool.backend.temporal.rclsmetaboola "
            "import RClsMetaBoolA\n"
            "from "
            "products.r7_r_cls_meta_bool.backend.temporal.rclsmetaboolb "
            "import RClsMetaBoolB\n"
            "\n"
            "WORKFLOWS = [RClsMetaBoolA]\n"
            "\n"
            "\n"
            "class _Meta(type):\n"
            "    def __bool__(cls):\n"
            "        cls.L.append(RClsMetaBoolB)\n"
            "        return True\n"
            "\n"
            "\n"
            "class _H(metaclass=_Meta):\n"
            "    L = WORKFLOWS\n"
            "\n"
            "\n"
            "if _H:\n"
            "    pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_cls_meta_bool.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_CLS_META_BOOL_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_CLS_META_BOOL = "
            '[("r7_r_cls_meta_bool", '
            "R7_R_CLS_META_BOOL_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rclsmetaboola.py", "backend/temporal/rclsmetaboolb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_cls_meta_bool.backend.facade.temporal.WORKFLOWS",
        "note": "class _H(metaclass=_Meta): L = W; if _H: (the metaclass __bool__ appends)",
    },
    "r7_r_cls_init": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_cls_init\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_cls_init", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_cls_init.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rclsinita.py": "class RClsInitA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rclsinitb.py": "class RClsInitB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_cls_init.backend.temporal.rclsinita "
            "import RClsInitA\n"
            "from products.r7_r_cls_init.backend.temporal.rclsinitb "
            "import RClsInitB\n"
            "\n"
            "WORKFLOWS = [RClsInitA]\n"
            "\n"
            "\n"
            "class _H:\n"
            "    L = WORKFLOWS\n"
            "\n"
            "    def __init__(self):\n"
            "        self.L.append(RClsInitB)\n"
            "\n"
            "\n"
            "_H()\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_cls_init.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_CLS_INIT_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_CLS_INIT = "
            '[("r7_r_cls_init", '
            "R7_R_CLS_INIT_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/rclsinita.py", "backend/temporal/rclsinitb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_cls_init.backend.facade.temporal.WORKFLOWS",
        "note": "class _H: L = W; __init__ appends to self.L; _H() at module level (limit 1 check)",
    },
    "r7_r_rec_of_rec": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_of_rec\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_of_rec", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_of_rec.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecofreca.py": "class RRecOfRecA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecofrecb.py": "class RRecOfRecB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_rec_of_rec.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_of_rec.backend.temporal.rrecofreca "
            "import RRecOfRecA\n"
            "from "
            "products.r7_r_rec_of_rec.backend.temporal.rrecofrecb "
            "import RRecOfRecB\n"
            "\n"
            "WORKFLOWS = [RRecOfRecA]\n"
            "R1 = Rec(workflows=WORKFLOWS)\n"
            "BINDING = Rec(workflows=R1)\n"
            "BINDING.workflows.workflows.append(RRecOfRecB)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_of_rec.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_OF_REC_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_OF_REC = "
            '[("r7_r_rec_of_rec", '
            "R7_R_REC_OF_REC_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecofreca.py", "backend/temporal/rrecofrecb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_of_rec.backend.facade.temporal.WORKFLOWS",
        "note": "a record of a record; BINDING.workflows.workflows.append(B)",
    },
    "r7_r_rec_alias_import": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_alias_import\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_alias_import", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_alias_import.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecaliasimporta.py": "class RRecAliasImportA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/rrecaliasimportb.py": "class RRecAliasImportB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_rec_alias_import.backend.temporal.rec "
            "import Rec as Rk\n"
            "from "
            "products.r7_r_rec_alias_import.backend.temporal.rrecaliasimporta "
            "import RRecAliasImportA\n"
            "from "
            "products.r7_r_rec_alias_import.backend.temporal.rrecaliasimportb "
            "import RRecAliasImportB\n"
            "\n"
            "WORKFLOWS = [RRecAliasImportA]\n"
            "BINDING = Rk(workflows=WORKFLOWS)\n"
            "BINDING.workflows.append(RRecAliasImportB)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_alias_import.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_ALIAS_IMPORT_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_ALIAS_IMPORT "
            "= "
            '[("r7_r_rec_alias_import", '
            "R7_R_REC_ALIAS_IMPORT_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecaliasimporta.py", "backend/temporal/rrecaliasimportb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_alias_import.backend.facade.temporal.WORKFLOWS",
        "note": "from ...rec import Rec as Rk; BINDING = Rk(workflows=W); a change through it",
    },
    "r7_r_rec_alias_import_ctor": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_alias_import_ctor\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_alias_import_ctor", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_alias_import_ctor.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecaliasimportctora.py": "class RRecAliasImportCtorA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/rrecaliasimportctorb.py": "class RRecAliasImportCtorB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_rec_alias_import_ctor.backend.temporal.rec "
            "import Rec as Rk\n"
            "from "
            "products.r7_r_rec_alias_import_ctor.backend.temporal.rrecaliasimportctora "
            "import RRecAliasImportCtorA\n"
            "from "
            "products.r7_r_rec_alias_import_ctor.backend.temporal.rrecaliasimportctorb "
            "import RRecAliasImportCtorB\n"
            "\n"
            "WORKFLOWS = [RRecAliasImportCtorA]\n"
            "BINDING = Rk(WORKFLOWS)\n",
            "backend/temporal/rec.py": "class Rec:\n"
            "    def __init__(self, workflows):\n"
            "        from "
            "products.r7_r_rec_alias_import_ctor.backend.temporal.rrecaliasimportctorb "
            "import RRecAliasImportCtorB\n"
            "\n"
            "        workflows.append(RRecAliasImportCtorB)\n"
            "        self.workflows = workflows\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_alias_import_ctor.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_ALIAS_IMPORT_CTOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_ALIAS_IMPORT_CTOR "
            "= "
            '[("r7_r_rec_alias_import_ctor", '
            "R7_R_REC_ALIAS_IMPORT_CTOR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecaliasimportctora.py", "backend/temporal/rrecaliasimportctorb.py"],
        "outcome": "kept",
        "why": "limit 1: the body of a record class's constructor",
        "note": "Rec imported as Rk; Rk's __init__ appends (limit 1 check)",
    },
    "r7_r_rec_reimport_func": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_reimport_func\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_reimport_func", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_reimport_func.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecreimportfunca.py": "class RRecReimportFuncA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/rrecreimportfuncb.py": "class RRecReimportFuncB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_rec_reimport_func.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_reimport_func.backend.temporal.rec2 "
            "import maker as Rec  # noqa: F811\n"
            "from "
            "products.r7_r_rec_reimport_func.backend.temporal.rrecreimportfunca "
            "import RRecReimportFuncA\n"
            "from "
            "products.r7_r_rec_reimport_func.backend.temporal.rrecreimportfuncb "
            "import RRecReimportFuncB\n"
            "\n"
            "WORKFLOWS = [RRecReimportFuncA]\n"
            "BINDING = Rec(WORKFLOWS)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
            "backend/temporal/rec2.py": "def maker(workflows):\n"
            "    from "
            "products.r7_r_rec_reimport_func.backend.temporal.rrecreimportfuncb "
            "import RRecReimportFuncB\n"
            "\n"
            "    workflows.append(RRecReimportFuncB)\n"
            "    return workflows\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_reimport_func.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_REIMPORT_FUNC_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_REIMPORT_FUNC "
            "= "
            '[("r7_r_rec_reimport_func", '
            "R7_R_REC_REIMPORT_FUNC_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecreimportfunca.py", "backend/temporal/rrecreimportfuncb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_reimport_func.backend.facade.temporal.WORKFLOWS",
        "note": "Rec imported as a class, then bound again by an import of a function that appends",
    },
    "r7_r_rec_rebound_later": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_rebound_later\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_rebound_later", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_rebound_later.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecreboundlatera.py": "class RRecReboundLaterA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/rrecreboundlaterb.py": "class RRecReboundLaterB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_rec_rebound_later.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_rebound_later.backend.temporal.rrecreboundlatera "
            "import RRecReboundLaterA\n"
            "from "
            "products.r7_r_rec_rebound_later.backend.temporal.rrecreboundlaterb "
            "import RRecReboundLaterB\n"
            "\n"
            "WORKFLOWS = [RRecReboundLaterA]\n"
            "BINDING = Rec(workflows=WORKFLOWS)\n"
            "BINDING.workflows.append(RRecReboundLaterB)\n"
            "Rec = None\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_rebound_later.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_REBOUND_LATER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_REBOUND_LATER "
            "= "
            '[("r7_r_rec_rebound_later", '
            "R7_R_REC_REBOUND_LATER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecreboundlatera.py", "backend/temporal/rrecreboundlaterb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_rebound_later.backend.facade.temporal.WORKFLOWS",
        "note": "a record class bound again later in the file",
    },
    "r7_r_rec_for": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_for\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_for", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_for.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecfora.py": "class RRecForA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecforb.py": "class RRecForB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_rec_for.backend.temporal.rec import Rec\n"
            "from products.r7_r_rec_for.backend.temporal.rrecfora import "
            "RRecForA\n"
            "from products.r7_r_rec_for.backend.temporal.rrecforb import "
            "RRecForB\n"
            "\n"
            "WORKFLOWS = [RRecForA]\n"
            "for BINDING in [Rec(workflows=WORKFLOWS)]:\n"
            "    pass\n"
            "BINDING.workflows.append(RRecForB)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_for.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_FOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_FOR = "
            '[("r7_r_rec_for", '
            "R7_R_REC_FOR_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/rrecfora.py", "backend/temporal/rrecforb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_for.backend.facade.temporal.WORKFLOWS",
        "note": "for BINDING in [Rec(workflows=W)]; a change through it",
    },
    "r7_r_rec_with": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_with\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_with", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_with.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecwitha.py": "class RRecWithA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecwithb.py": "class RRecWithB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import contextlib\n"
            "\n"
            "from products.r7_r_rec_with.backend.temporal.rec import "
            "Rec\n"
            "from products.r7_r_rec_with.backend.temporal.rrecwitha "
            "import RRecWithA\n"
            "from products.r7_r_rec_with.backend.temporal.rrecwithb "
            "import RRecWithB\n"
            "\n"
            "WORKFLOWS = [RRecWithA]\n"
            "with contextlib.nullcontext(Rec(workflows=WORKFLOWS)) as "
            "BINDING:\n"
            "    pass\n"
            "BINDING.workflows.append(RRecWithB)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_with.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_WITH_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_WITH = "
            '[("r7_r_rec_with", '
            "R7_R_REC_WITH_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/rrecwitha.py", "backend/temporal/rrecwithb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_with.backend.facade.temporal.WORKFLOWS",
        "note": "with nullcontext(Rec(workflows=W)) as BINDING; a change through it",
    },
    "r7_r_rec_class_body": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_class_body\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_class_body", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_class_body.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecclassbodya.py": "class RRecClassBodyA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecclassbodyb.py": "class RRecClassBodyB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_rec_class_body.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_class_body.backend.temporal.rrecclassbodya "
            "import RRecClassBodyA\n"
            "from "
            "products.r7_r_rec_class_body.backend.temporal.rrecclassbodyb "
            "import RRecClassBodyB\n"
            "\n"
            "WORKFLOWS = [RRecClassBodyA]\n"
            "\n"
            "\n"
            "class _C:\n"
            "    rec = Rec(workflows=WORKFLOWS)\n"
            "\n"
            "\n"
            "_C.rec.workflows.append(RRecClassBodyB)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_class_body.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_CLASS_BODY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_CLASS_BODY "
            '= [("r7_r_rec_class_body", '
            "R7_R_REC_CLASS_BODY_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecclassbodya.py", "backend/temporal/rrecclassbodyb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_class_body.backend.facade.temporal.WORKFLOWS",
        "note": "class _C: rec = Rec(workflows=W); _C.rec.workflows.append(B)",
    },
    "r7_r_rec_kwargs_holder": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_kwargs_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_kwargs_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_kwargs_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rreckwargsholdera.py": "class RRecKwargsHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/rreckwargsholderb.py": "class RRecKwargsHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_rec_kwargs_holder.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_kwargs_holder.backend.temporal.rreckwargsholdera "
            "import RRecKwargsHolderA\n"
            "from "
            "products.r7_r_rec_kwargs_holder.backend.temporal.rreckwargsholderb "
            "import RRecKwargsHolderB\n"
            "\n"
            "WORKFLOWS = [RRecKwargsHolderA]\n"
            '_D = {"workflows": WORKFLOWS}\n'
            "BINDING = Rec(**_D)\n"
            "BINDING.workflows.append(RRecKwargsHolderB)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_kwargs_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_KWARGS_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_KWARGS_HOLDER "
            "= "
            '[("r7_r_rec_kwargs_holder", '
            "R7_R_REC_KWARGS_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rreckwargsholdera.py", "backend/temporal/rreckwargsholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_kwargs_holder.backend.facade.temporal.WORKFLOWS",
        "note": "Rec(**_D) where _D holds W; a change through it",
    },
    "r7_r_rec_copy": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_copy\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_copy", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_copy.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rreccopya.py": "class RRecCopyA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rreccopyb.py": "class RRecCopyB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import copy\n"
            "\n"
            "from products.r7_r_rec_copy.backend.temporal.rec import "
            "Rec\n"
            "from products.r7_r_rec_copy.backend.temporal.rreccopya "
            "import RRecCopyA\n"
            "from products.r7_r_rec_copy.backend.temporal.rreccopyb "
            "import RRecCopyB\n"
            "\n"
            "WORKFLOWS = [RRecCopyA]\n"
            "BINDING = Rec(workflows=WORKFLOWS)\n"
            "_C2 = copy.copy(BINDING)\n"
            "_C2.workflows.append(RRecCopyB)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_copy.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_COPY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_COPY = "
            '[("r7_r_rec_copy", '
            "R7_R_REC_COPY_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/rreccopya.py", "backend/temporal/rreccopyb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_copy.backend.facade.temporal.WORKFLOWS",
        "note": "copy.copy(BINDING).workflows.append(B)",
    },
    "r7_r_rec_asdict": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_asdict\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_asdict", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_asdict.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecasdicta.py": "class RRecAsdictA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecasdictb.py": "class RRecAsdictB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import dataclasses\n"
            "\n"
            "from products.r7_r_rec_asdict.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_asdict.backend.temporal.rrecasdicta "
            "import RRecAsdictA\n"
            "from "
            "products.r7_r_rec_asdict.backend.temporal.rrecasdictb "
            "import RRecAsdictB\n"
            "\n"
            "WORKFLOWS = [RRecAsdictA]\n"
            "BINDING = Rec(workflows=WORKFLOWS)\n"
            'dataclasses.asdict(BINDING)["workflows"].append(RRecAsdictB)\n',
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_asdict.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_ASDICT_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_ASDICT = "
            '[("r7_r_rec_asdict", '
            "R7_R_REC_ASDICT_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecasdicta.py", "backend/temporal/rrecasdictb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_asdict.backend.facade.temporal.WORKFLOWS",
        "note": "dataclasses.asdict(BINDING)['workflows'].append(B) (asdict copies: harmless)",
    },
    "r7_r_rec_dunder_dict": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_dunder_dict\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_dunder_dict", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_dunder_dict.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecdunderdicta.py": "class RRecDunderDictA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecdunderdictb.py": "class RRecDunderDictB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_rec_dunder_dict.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_dunder_dict.backend.temporal.rrecdunderdicta "
            "import RRecDunderDictA\n"
            "from "
            "products.r7_r_rec_dunder_dict.backend.temporal.rrecdunderdictb "
            "import RRecDunderDictB\n"
            "\n"
            "WORKFLOWS = [RRecDunderDictA]\n"
            "BINDING = Rec(workflows=WORKFLOWS)\n"
            'BINDING.__dict__["workflows"].append(RRecDunderDictB)\n',
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_dunder_dict.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_DUNDER_DICT_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_DUNDER_DICT "
            "= "
            '[("r7_r_rec_dunder_dict", '
            "R7_R_REC_DUNDER_DICT_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecdunderdicta.py", "backend/temporal/rrecdunderdictb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_dunder_dict.backend.facade.temporal.WORKFLOWS",
        "note": "BINDING.__dict__['workflows'].append(B)",
    },
    "r7_r_rec_getattr": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_getattr\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_getattr", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_r_rec_getattr.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecgetattra.py": "class RRecGetattrA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecgetattrb.py": "class RRecGetattrB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_r_rec_getattr.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_getattr.backend.temporal.rrecgetattra "
            "import RRecGetattrA\n"
            "from "
            "products.r7_r_rec_getattr.backend.temporal.rrecgetattrb "
            "import RRecGetattrB\n"
            "\n"
            "WORKFLOWS = [RRecGetattrA]\n"
            "BINDING = Rec(workflows=WORKFLOWS)\n"
            'getattr(BINDING, "workflows").append(RRecGetattrB)\n',
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_getattr.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_GETATTR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_GETATTR = "
            '[("r7_r_rec_getattr", '
            "R7_R_REC_GETATTR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecgetattra.py", "backend/temporal/rrecgetattrb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_getattr.backend.facade.temporal.WORKFLOWS",
        "note": "getattr(BINDING, 'workflows').append(B)",
    },
    "r7_r_rec_holder_handed": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_holder_handed\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_holder_handed", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_holder_handed.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecholderhandeda.py": "class RRecHolderHandedA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/rrecholderhandedb.py": "class RRecHolderHandedB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_rec_holder_handed.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_holder_handed.backend.temporal.rrecholderhandeda "
            "import RRecHolderHandedA\n"
            "from "
            "products.r7_r_rec_holder_handed.backend.temporal.rrecholderhandedb "
            "import RRecHolderHandedB\n"
            "\n"
            "WORKFLOWS = [RRecHolderHandedA]\n"
            "BINDING = Rec(workflows=WORKFLOWS)\n"
            "\n"
            "\n"
            "def _keep(x):\n"
            "    x[0].workflows.append(RRecHolderHandedB)\n"
            "\n"
            "\n"
            "_ALL = [BINDING]\n"
            "_keep(_ALL)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_holder_handed.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_HOLDER_HANDED_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_HOLDER_HANDED "
            "= "
            '[("r7_r_rec_holder_handed", '
            "R7_R_REC_HOLDER_HANDED_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecholderhandeda.py", "backend/temporal/rrecholderhandedb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_holder_handed.backend.facade.temporal.WORKFLOWS",
        "note": "_ALL = [BINDING]; _keep(_ALL) (the holder handed on)",
    },
    "r7_r_rec_star_import": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_star_import\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_star_import", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_star_import.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecstarimporta.py": "class RRecStarImportA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/rrecstarimportb.py": "class RRecStarImportB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_r_rec_star_import.backend.temporal.rec "
            "import *  # noqa: F403\n"
            "from "
            "products.r7_r_rec_star_import.backend.temporal.rrecstarimporta "
            "import RRecStarImportA\n"
            "from "
            "products.r7_r_rec_star_import.backend.temporal.rrecstarimportb "
            "import RRecStarImportB\n"
            "\n"
            "WORKFLOWS = [RRecStarImportA]\n"
            "BINDING = Rec(WORKFLOWS)\n",
            "backend/temporal/rec.py": "class Rec:\n"
            "    def __init__(self, workflows):\n"
            "        from "
            "products.r7_r_rec_star_import.backend.temporal.rrecstarimportb "
            "import RRecStarImportB\n"
            "\n"
            "        workflows.append(RRecStarImportB)\n"
            "        self.workflows = workflows\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_star_import.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_STAR_IMPORT_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_STAR_IMPORT "
            "= "
            '[("r7_r_rec_star_import", '
            "R7_R_REC_STAR_IMPORT_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/rrecstarimporta.py", "backend/temporal/rrecstarimportb.py"],
        "outcome": "unread",
        "reason": "products.r7_r_rec_star_import.backend.facade.temporal.WORKFLOWS",
        "note": "Rec bound by a first-party star import; its __init__ appends",
    },
    "r7_r_rec_property_decl": {
        "group": "r",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_r_rec_property_decl\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_r_rec_property_decl", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_r_rec_property_decl.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/rrecpropertydecla.py": "class RRecPropertyDeclA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/rrecpropertydeclb.py": "class RRecPropertyDeclB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from posthog.temporal.common.registration import "
            "TemporalRegistration\n"
            "from "
            "products.r7_r_rec_property_decl.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_r_rec_property_decl.backend.temporal.rrecpropertydecla "
            "import RRecPropertyDeclA\n"
            "from "
            "products.r7_r_rec_property_decl.backend.temporal.rrecpropertydeclb "
            "import RRecPropertyDeclB\n"
            "\n"
            "WORKFLOWS = [RRecPropertyDeclA]\n"
            "BINDING = Rec(WORKFLOWS)\n"
            "TEMPORAL = "
            "TemporalRegistration(queues=(BINDING,))\n",
            "backend/temporal/rec.py": "class Rec:\n"
            "    queue_setting = 'q'\n"
            "\n"
            "    def __init__(self, workflows):\n"
            "        self._w = workflows\n"
            "\n"
            "    @property\n"
            "    def workflows(self):\n"
            "        from "
            "products.r7_r_rec_property_decl.backend.temporal.rrecpropertydeclb "
            "import RRecPropertyDeclB\n"
            "\n"
            "        self._w.append(RRecPropertyDeclB)\n"
            "        return self._w\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_r_rec_property_decl.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_R_REC_PROPERTY_DECL_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_R_REC_PROPERTY_DECL "
            "= "
            '[("r7_r_rec_property_decl", '
            "R7_R_REC_PROPERTY_DECL_WORKFLOWS, "
            "[])]\n",
            "posthog/temporal/common/registration.py": "class QueueBinding:\n"
            "    def __init__(self, queue_setting, "
            "workflows=(), activities=()):\n"
            "        self.queue_setting = "
            "queue_setting\n"
            "\n"
            "\n"
            "class TemporalRegistration:\n"
            "    def __init__(self, queues=(), "
            "schedules=(), log_sources=()):\n"
            "        self.queues = queues\n",
        },
        "probes": ["backend/temporal/rrecpropertydecla.py", "backend/temporal/rrecpropertydeclb.py"],
        "outcome": "kept",
        "why": "limit 1: a function (the property core reads) changes the list",
        "note": "TEMPORAL holds a first-party record whose .workflows property appends B when core reads it",
    },
    "r7_o_rec_defs_readonly": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_rec_defs_readonly\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_rec_defs_readonly", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_o_rec_defs_readonly.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/orecdefsreadonlya.py": "class ORecDefsReadonlyA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/defs.py": "from "
            "products.r7_o_rec_defs_readonly.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_o_rec_defs_readonly.backend.temporal.orecdefsreadonlya "
            "import ORecDefsReadonlyA\n"
            "\n"
            "WORKFLOWS = [ORecDefsReadonlyA]\n"
            'x_agent = Rec(name="x", toolkit=WORKFLOWS)\n',
            "backend/facade/temporal.py": "from "
            "products.r7_o_rec_defs_readonly.backend.temporal.defs "
            "import WORKFLOWS  # noqa: F401\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, replace\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    name: str\n"
            "    toolkit: object = None\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_rec_defs_readonly.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_REC_DEFS_READONLY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_REC_DEFS_READONLY "
            "= "
            '[("r7_o_rec_defs_readonly", '
            "R7_O_REC_DEFS_READONLY_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/orecdefsreadonlya.py"],
        "outcome": "watched",
        "note": "defs.py (no registry's file): x_agent = Rec(name='x', toolkit=W), read only",
    },
    "r7_o_rec_defs_replace": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_rec_defs_replace\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_rec_defs_replace", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_o_rec_defs_replace.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/orecdefsreplacea.py": "class ORecDefsReplaceA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/defs.py": "from dataclasses import replace\n"
            "\n"
            "from "
            "products.r7_o_rec_defs_replace.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_o_rec_defs_replace.backend.temporal.orecdefsreplacea "
            "import ORecDefsReplaceA\n"
            "\n"
            "WORKFLOWS = [ORecDefsReplaceA]\n"
            'x_agent = Rec(name="x", toolkit=WORKFLOWS)\n'
            'y_agent = replace(x_agent, name="y")\n',
            "backend/facade/temporal.py": "from "
            "products.r7_o_rec_defs_replace.backend.temporal.defs "
            "import WORKFLOWS  # noqa: F401\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, replace\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    name: str\n"
            "    toolkit: object = None\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_rec_defs_replace.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_REC_DEFS_REPLACE_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_REC_DEFS_REPLACE "
            "= "
            '[("r7_o_rec_defs_replace", '
            "R7_O_REC_DEFS_REPLACE_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/orecdefsreplacea.py"],
        "outcome": "watched",
        "note": "defs.py: y_agent = replace(x_agent, name='y') (the live presets' shape)",
    },
    "r7_o_rec_defs_list": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_rec_defs_list\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_rec_defs_list", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_o_rec_defs_list.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/orecdefslista.py": "class ORecDefsListA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/defs.py": "from products.r7_o_rec_defs_list.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_o_rec_defs_list.backend.temporal.orecdefslista "
            "import ORecDefsListA\n"
            "\n"
            "WORKFLOWS = [ORecDefsListA]\n"
            'x_agent = Rec(name="x", toolkit=WORKFLOWS)\n'
            "MODES = [x_agent]\n"
            "BY_NAME = {m.name: m for m in MODES}\n",
            "backend/facade/temporal.py": "from "
            "products.r7_o_rec_defs_list.backend.temporal.defs "
            "import WORKFLOWS  # noqa: F401\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, replace\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    name: str\n"
            "    toolkit: object = None\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_rec_defs_list.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_REC_DEFS_LIST_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_REC_DEFS_LIST = "
            '[("r7_o_rec_defs_list", '
            "R7_O_REC_DEFS_LIST_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/orecdefslista.py"],
        "outcome": "unread",
        "reason": "products.r7_o_rec_defs_list.backend.temporal.defs.WORKFLOWS",
        "note": "defs.py: MODES = [x_agent]; {m.name: m for m in MODES} (read only)",
    },
    "r7_o_rec_defs_repr": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_rec_defs_repr\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_rec_defs_repr", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_o_rec_defs_repr.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/orecdefsrepra.py": "class ORecDefsReprA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/defs.py": "from products.r7_o_rec_defs_repr.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_o_rec_defs_repr.backend.temporal.orecdefsrepra "
            "import ORecDefsReprA\n"
            "\n"
            "WORKFLOWS = [ORecDefsReprA]\n"
            'x_agent = Rec(name="x", toolkit=WORKFLOWS)\n'
            "print(x_agent)\n",
            "backend/facade/temporal.py": "from "
            "products.r7_o_rec_defs_repr.backend.temporal.defs "
            "import WORKFLOWS  # noqa: F401\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, replace\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    name: str\n"
            "    toolkit: object = None\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_rec_defs_repr.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_REC_DEFS_REPR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_REC_DEFS_REPR = "
            '[("r7_o_rec_defs_repr", '
            "R7_O_REC_DEFS_REPR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/orecdefsrepra.py"],
        "outcome": "unread",
        "reason": "products.r7_o_rec_defs_repr.backend.temporal.defs.WORKFLOWS",
        "note": "defs.py: print(x_agent) (a dataclass's own repr)",
    },
    "r7_o_enumerate_for": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_enumerate_for\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_enumerate_for", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_o_enumerate_for.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/oenumeratefora.py": "class OEnumerateForA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_o_enumerate_for.backend.temporal.oenumeratefora "
            "import OEnumerateForA\n"
            "\n"
            "WORKFLOWS = [OEnumerateForA]\n"
            "for _i, _w in enumerate(WORKFLOWS):\n"
            "    pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_enumerate_for.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_ENUMERATE_FOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_ENUMERATE_FOR = "
            '[("r7_o_enumerate_for", '
            "R7_O_ENUMERATE_FOR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/oenumeratefora.py"],
        "outcome": "watched",
        "note": "for _i, _w in enumerate(W): pass",
    },
    "r7_o_reversed_for": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_reversed_for\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_reversed_for", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_o_reversed_for.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/oreversedfora.py": "class OReversedForA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_o_reversed_for.backend.temporal.oreversedfora "
            "import OReversedForA\n"
            "\n"
            "WORKFLOWS = [OReversedForA]\n"
            "for _w in reversed(WORKFLOWS):\n"
            "    pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_reversed_for.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_REVERSED_FOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_REVERSED_FOR = "
            '[("r7_o_reversed_for", '
            "R7_O_REVERSED_FOR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/oreversedfora.py"],
        "outcome": "watched",
        "note": "for _w in reversed(W): pass",
    },
    "r7_o_zip_for": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_zip_for\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_zip_for", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_o_zip_for.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/ozipfora.py": "class OZipForA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_o_zip_for.backend.temporal.ozipfora import "
            "OZipForA\n"
            "\n"
            "WORKFLOWS = [OZipForA]\n"
            'for _n, _w in zip(["a"], WORKFLOWS):\n'
            "    pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_zip_for.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_ZIP_FOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_ZIP_FOR = "
            '[("r7_o_zip_for", '
            "R7_O_ZIP_FOR_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/ozipfora.py"],
        "outcome": "watched",
        "note": "for _n, _w in zip(names, W): pass",
    },
    "r7_o_enumerate_comp": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_enumerate_comp\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_enumerate_comp", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_o_enumerate_comp.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/oenumeratecompa.py": "class OEnumerateCompA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_o_enumerate_comp.backend.temporal.oenumeratecompa "
            "import OEnumerateCompA\n"
            "\n"
            "WORKFLOWS = [OEnumerateCompA]\n"
            "_IDX = {w: i for i, w in enumerate(WORKFLOWS)}\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_enumerate_comp.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_ENUMERATE_COMP_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_ENUMERATE_COMP "
            '= [("r7_o_enumerate_comp", '
            "R7_O_ENUMERATE_COMP_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/oenumeratecompa.py"],
        "outcome": "watched",
        "note": "{w: i for i, w in enumerate(W)}",
    },
    "r7_o_chain_for": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_chain_for\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_chain_for", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_o_chain_for.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/ochainfora.py": "class OChainForA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import itertools\n"
            "from products.r7_o_chain_for.backend.temporal.ochainfora "
            "import OChainForA\n"
            "\n"
            "WORKFLOWS = [OChainForA]\n"
            "for _w in itertools.chain(WORKFLOWS, []):\n"
            "    pass\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_chain_for.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_CHAIN_FOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_CHAIN_FOR = "
            '[("r7_o_chain_for", '
            "R7_O_CHAIN_FOR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/ochainfora.py"],
        "outcome": "watched",
        "note": "for _w in itertools.chain(W, []): pass",
    },
    "r7_o_map_lambda": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_map_lambda\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_map_lambda", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_o_map_lambda.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/omaplambdaa.py": "class OMapLambdaA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_o_map_lambda.backend.temporal.omaplambdaa "
            "import OMapLambdaA\n"
            "\n"
            "WORKFLOWS = [OMapLambdaA]\n"
            "_NAMES = list(map(lambda w: w.__name__, WORKFLOWS))\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_map_lambda.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_MAP_LAMBDA_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_MAP_LAMBDA = "
            '[("r7_o_map_lambda", '
            "R7_O_MAP_LAMBDA_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/omaplambdaa.py"],
        "outcome": "watched",
        "note": "list(map(lambda w: w.__name__, W))",
    },
    "r7_o_deepcopy": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_deepcopy\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_deepcopy", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_o_deepcopy.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/odeepcopya.py": "class ODeepcopyA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import copy\n"
            "from products.r7_o_deepcopy.backend.temporal.odeepcopya "
            "import ODeepcopyA\n"
            "\n"
            "WORKFLOWS = [ODeepcopyA]\n"
            "_C = copy.deepcopy(WORKFLOWS)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_deepcopy.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_DEEPCOPY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_DEEPCOPY = "
            '[("r7_o_deepcopy", '
            "R7_O_DEEPCOPY_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/odeepcopya.py"],
        "outcome": "unread",
        "reason": "products.r7_o_deepcopy.backend.facade.temporal.WORKFLOWS",
        "note": "copy.deepcopy(W)",
    },
    "r7_o_tstring": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_tstring\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_tstring", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_o_tstring.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/otstringa.py": "class OTstringA:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_o_tstring.backend.temporal.otstringa "
            "import OTstringA\n"
            "\n"
            "WORKFLOWS = [OTstringA]\n"
            '_T = t"{WORKFLOWS}"\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_tstring.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_TSTRING_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_TSTRING = "
            '[("r7_o_tstring", '
            "R7_O_TSTRING_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/otstringa.py"],
        "outcome": "unread",
        "reason": "products.r7_o_tstring.backend.facade.temporal.WORKFLOWS",
        "note": "t'{W}' (read only)",
    },
    "r7_o_rec_facade_readonly": {
        "group": "o",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_o_rec_facade_readonly\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_o_rec_facade_readonly", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_o_rec_facade_readonly.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/orecfacadereadonlya.py": "class ORecFacadeReadonlyA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_o_rec_facade_readonly.backend.temporal.rec "
            "import Rec\n"
            "from "
            "products.r7_o_rec_facade_readonly.backend.temporal.orecfacadereadonlya "
            "import ORecFacadeReadonlyA\n"
            "\n"
            "WORKFLOWS = [ORecFacadeReadonlyA]\n"
            "BINDING = Rec(workflows=WORKFLOWS)\n",
            "backend/temporal/rec.py": "from dataclasses import dataclass, field\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Rec:\n"
            "    workflows: list = "
            "field(default_factory=list)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_o_rec_facade_readonly.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_O_REC_FACADE_READONLY_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_O_REC_FACADE_READONLY "
            "= "
            '[("r7_o_rec_facade_readonly", '
            "R7_O_REC_FACADE_READONLY_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/orecfacadereadonlya.py"],
        "outcome": "watched",
        "note": "the facade binds BINDING = Rec(workflows=W), read only",
    },
    "r7_p_rec_ctl": {
        "group": "p",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_p_rec_ctl\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_p_rec_ctl", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_p_rec_ctl.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/a.py": "class AFlow:\n    pass\n",
            "backend/temporal/b.py": "class BFlow:\n    pass\n",
            "backend/facade/temporal.py": "from products.r7_p_rec_ctl.backend.temporal.a import AFlow\n"
            "\n"
            "WORKFLOWS = [AFlow]\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/factory.py": "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class AgentModeDefinition:\n"
            "    mode: str\n"
            "    toolkit_class: type = object\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_ctl.py": "from dataclasses import replace  # "
            "noqa: F401\n"
            "\n"
            "from "
            "ee.hogai.core.agent_modes.factory "
            "import AgentModeDefinition\n"
            "from "
            "products.r7_p_rec_ctl.backend.facade.temporal "
            "import WORKFLOWS as P_WORKFLOWS\n"
            "\n"
            "\n"
            "class Toolkit:\n"
            "    WORKFLOWS = [*P_WORKFLOWS]\n"
            "\n"
            "\n"
            "x_agent = "
            'AgentModeDefinition(mode="x", '
            "toolkit_class=Toolkit)\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_ctl_reader.py": "from "
            "ee.hogai.core.agent_modes.presets.r7_p_rec_ctl "
            "import Toolkit\n"
            "\n"
            "ALL = [*Toolkit.WORKFLOWS]\n",
        },
        "probes": ["backend/temporal/a.py"],
        "outcome": "watched",
        "note": "control: x_agent = AgentModeDefinition(mode='x', toolkit_class=Toolkit)",
    },
    "r7_p_rec_replace": {
        "group": "p",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_p_rec_replace\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_p_rec_replace", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_p_rec_replace.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/a.py": "class AFlow:\n    pass\n",
            "backend/temporal/b.py": "class BFlow:\n    pass\n",
            "backend/facade/temporal.py": "from products.r7_p_rec_replace.backend.temporal.a "
            "import AFlow\n"
            "\n"
            "WORKFLOWS = [AFlow]\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/factory.py": "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class AgentModeDefinition:\n"
            "    mode: str\n"
            "    toolkit_class: type = object\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_replace.py": "from dataclasses import "
            "replace  # noqa: F401\n"
            "\n"
            "from "
            "ee.hogai.core.agent_modes.factory "
            "import AgentModeDefinition\n"
            "from "
            "products.r7_p_rec_replace.backend.facade.temporal "
            "import WORKFLOWS as "
            "P_WORKFLOWS\n"
            "\n"
            "\n"
            "class Toolkit:\n"
            "    WORKFLOWS = "
            "[*P_WORKFLOWS]\n"
            "\n"
            "\n"
            "x_agent = "
            'AgentModeDefinition(mode="x", '
            "toolkit_class=Toolkit)\n"
            "sub_agent = replace(x_agent, "
            'mode="y")\n',
            "ee/hogai/core/agent_modes/presets/r7_p_rec_replace_reader.py": "from "
            "ee.hogai.core.agent_modes.presets.r7_p_rec_replace "
            "import Toolkit\n"
            "\n"
            "ALL = "
            "[*Toolkit.WORKFLOWS]\n",
        },
        "probes": ["backend/temporal/a.py"],
        "outcome": "watched",
        "note": "sub_agent = replace(x_agent, mode='y') (the live survey.py shape)",
    },
    "r7_p_rec_list_comp": {
        "group": "p",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_p_rec_list_comp\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_p_rec_list_comp", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_p_rec_list_comp.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/a.py": "class AFlow:\n    pass\n",
            "backend/temporal/b.py": "class BFlow:\n    pass\n",
            "backend/facade/temporal.py": "from products.r7_p_rec_list_comp.backend.temporal.a "
            "import AFlow\n"
            "\n"
            "WORKFLOWS = [AFlow]\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/factory.py": "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class AgentModeDefinition:\n"
            "    mode: str\n"
            "    toolkit_class: type = object\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_list_comp.py": "from dataclasses import "
            "replace  # noqa: F401\n"
            "\n"
            "from "
            "ee.hogai.core.agent_modes.factory "
            "import "
            "AgentModeDefinition\n"
            "from "
            "products.r7_p_rec_list_comp.backend.facade.temporal "
            "import WORKFLOWS as "
            "P_WORKFLOWS\n"
            "\n"
            "\n"
            "class Toolkit:\n"
            "    WORKFLOWS = "
            "[*P_WORKFLOWS]\n"
            "\n"
            "\n"
            "x_agent = "
            'AgentModeDefinition(mode="x", '
            "toolkit_class=Toolkit)\n"
            "MODES = [x_agent]\n"
            "BY_MODE = {m.mode: m for "
            "m in MODES}\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_list_comp_reader.py": "from "
            "ee.hogai.core.agent_modes.presets.r7_p_rec_list_comp "
            "import Toolkit\n"
            "\n"
            "ALL = "
            "[*Toolkit.WORKFLOWS]\n",
        },
        "probes": ["backend/temporal/a.py"],
        "outcome": "problem",
        "reason": "ee.hogai.core.agent_modes.presets.r7_p_rec_list_comp.Toolkit.WORKFLOWS",
        "note": "MODES = [x_agent]; {m.mode: m for m in MODES} (read only)",
    },
    "r7_p_rec_replace_change": {
        "group": "p",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_p_rec_replace_change\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_p_rec_replace_change", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_p_rec_replace_change.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/a.py": "class AFlow:\n    pass\n",
            "backend/temporal/b.py": "class BFlow:\n    pass\n",
            "backend/facade/temporal.py": "from "
            "products.r7_p_rec_replace_change.backend.temporal.a "
            "import AFlow\n"
            "\n"
            "WORKFLOWS = [AFlow]\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/factory.py": "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class AgentModeDefinition:\n"
            "    mode: str\n"
            "    toolkit_class: type = object\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_replace_change.py": "from "
            "dataclasses "
            "import replace  "
            "# noqa: F401\n"
            "\n"
            "from "
            "ee.hogai.core.agent_modes.factory "
            "import "
            "AgentModeDefinition\n"
            "from "
            "products.r7_p_rec_replace_change.backend.facade.temporal "
            "import "
            "WORKFLOWS as "
            "P_WORKFLOWS\n"
            "\n"
            "\n"
            "class Toolkit:\n"
            "    WORKFLOWS = "
            "[*P_WORKFLOWS]\n"
            "\n"
            "\n"
            "x_agent = "
            'AgentModeDefinition(mode="x", '
            "toolkit_class=Toolkit)\n"
            "sub_agent = "
            "replace(x_agent, "
            'mode="y")\n'
            "from "
            "products.r7_p_rec_replace_change.backend.temporal.b "
            "import BFlow  # "
            "noqa: E402\n"
            "\n"
            "sub_agent.toolkit_class.WORKFLOWS.append(BFlow)\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_replace_change_reader.py": "from "
            "ee.hogai.core.agent_modes.presets.r7_p_rec_replace_change "
            "import "
            "Toolkit\n"
            "\n"
            "ALL = "
            "[*Toolkit.WORKFLOWS]\n",
        },
        "probes": ["backend/temporal/a.py", "backend/temporal/b.py"],
        "outcome": "problem",
        "reason": "ee.hogai.core.agent_modes.presets.r7_p_rec_replace_change.Toolkit.WORKFLOWS",
        "note": "a change through replace(x_agent, ...).toolkit_class.WORKFLOWS",
    },
    "r7_p_rec_bool": {
        "group": "p",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_p_rec_bool\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_p_rec_bool", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_p_rec_bool.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/a.py": "class AFlow:\n    pass\n",
            "backend/temporal/b.py": "class BFlow:\n    pass\n",
            "backend/facade/temporal.py": "from products.r7_p_rec_bool.backend.temporal.a import "
            "AFlow\n"
            "\n"
            "WORKFLOWS = [AFlow]\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/factory.py": "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class AgentModeDefinition:\n"
            "    mode: str\n"
            "    toolkit_class: type = object\n"
            "\n"
            "    def __bool__(self):\n"
            "        from "
            "products.r7_p_rec_bool.backend.temporal.b import "
            "BFlow\n"
            "\n"
            "        "
            "self.toolkit_class.WORKFLOWS.append(BFlow)\n"
            "        return True\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_bool.py": "from dataclasses import replace  # "
            "noqa: F401\n"
            "\n"
            "from "
            "ee.hogai.core.agent_modes.factory "
            "import AgentModeDefinition\n"
            "from "
            "products.r7_p_rec_bool.backend.facade.temporal "
            "import WORKFLOWS as P_WORKFLOWS\n"
            "\n"
            "\n"
            "class Toolkit:\n"
            "    WORKFLOWS = [*P_WORKFLOWS]\n"
            "\n"
            "\n"
            "x_agent = "
            'AgentModeDefinition(mode="x", '
            "toolkit_class=Toolkit)\n"
            "if x_agent:\n"
            "    pass\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_bool_reader.py": "from "
            "ee.hogai.core.agent_modes.presets.r7_p_rec_bool "
            "import Toolkit\n"
            "\n"
            "ALL = "
            "[*Toolkit.WORKFLOWS]\n",
        },
        "probes": ["backend/temporal/a.py", "backend/temporal/b.py"],
        "outcome": "problem",
        "reason": "ee.hogai.core.agent_modes.presets.r7_p_rec_bool.Toolkit.WORKFLOWS",
        "note": "if x_agent: (the core record class's __bool__ appends BFlow to toolkit_class.WORKFLOWS)",
    },
    "r7_p_rec_eq": {
        "group": "p",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_p_rec_eq\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_p_rec_eq", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_p_rec_eq.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/a.py": "class AFlow:\n    pass\n",
            "backend/temporal/b.py": "class BFlow:\n    pass\n",
            "backend/facade/temporal.py": "from products.r7_p_rec_eq.backend.temporal.a import AFlow\n"
            "\n"
            "WORKFLOWS = [AFlow]\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/factory.py": "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class AgentModeDefinition:\n"
            "    mode: str\n"
            "    toolkit_class: type = object\n"
            "\n"
            "    def __eq__(self, other):\n"
            "        from "
            "products.r7_p_rec_eq.backend.temporal.b import "
            "BFlow\n"
            "\n"
            "        self.toolkit_class.WORKFLOWS.append(BFlow)\n"
            "        return False\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_eq.py": "from dataclasses import replace  # "
            "noqa: F401\n"
            "\n"
            "from ee.hogai.core.agent_modes.factory "
            "import AgentModeDefinition\n"
            "from "
            "products.r7_p_rec_eq.backend.facade.temporal "
            "import WORKFLOWS as P_WORKFLOWS\n"
            "\n"
            "\n"
            "class Toolkit:\n"
            "    WORKFLOWS = [*P_WORKFLOWS]\n"
            "\n"
            "\n"
            'x_agent = AgentModeDefinition(mode="x", '
            "toolkit_class=Toolkit)\n"
            "x_agent == 1\n",
            "ee/hogai/core/agent_modes/presets/r7_p_rec_eq_reader.py": "from "
            "ee.hogai.core.agent_modes.presets.r7_p_rec_eq "
            "import Toolkit\n"
            "\n"
            "ALL = [*Toolkit.WORKFLOWS]\n",
        },
        "probes": ["backend/temporal/a.py", "backend/temporal/b.py"],
        "outcome": "problem",
        "reason": "ee.hogai.core.agent_modes.presets.r7_p_rec_eq.Toolkit.WORKFLOWS",
        "note": "x_agent == 1 (the core record class's __eq__ appends BFlow)",
    },
    "r7_t_cls_with": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_with\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_with", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_cls_with.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclswitha.py": "class TClsWithA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclswithb.py": "class TClsWithB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import contextlib\n"
            "from products.r7_t_cls_with.backend.temporal.tclswitha "
            "import TClsWithA\n"
            "from products.r7_t_cls_with.backend.temporal.tclswithb "
            "import TClsWithB\n"
            "\n"
            "WORKFLOWS = (TClsWithA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    with contextlib.nullcontext((TClsWithA, TClsWithB)) as "
            "WORKFLOWS:\n"
            "        pass\n",
            "backend/temporal/more.py": "from products.r7_t_cls_with.backend.temporal.tclswitha "
            "import TClsWithA\n"
            "from products.r7_t_cls_with.backend.temporal.tclswithb "
            "import TClsWithB\n"
            "\n"
            "MORE = (TClsWithA, TClsWithB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_with.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_WITH_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_WITH = "
            '[("r7_t_cls_with", '
            "R7_T_CLS_WITH_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/tclswitha.py", "backend/temporal/tclswithb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_with.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; with binds W",
    },
    "r7_t_fn_with": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_with\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_with", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_with.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnwitha.py": "class TFnWithA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnwithb.py": "class TFnWithB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import contextlib\n"
            "from products.r7_t_fn_with.backend.temporal.tfnwitha import "
            "TFnWithA\n"
            "from products.r7_t_fn_with.backend.temporal.tfnwithb import "
            "TFnWithB\n"
            "\n"
            "WORKFLOWS = (TFnWithA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    with contextlib.nullcontext((TFnWithA, TFnWithB)) as "
            "WORKFLOWS:\n"
            "        pass\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from products.r7_t_fn_with.backend.temporal.tfnwitha import "
            "TFnWithA\n"
            "from products.r7_t_fn_with.backend.temporal.tfnwithb import "
            "TFnWithB\n"
            "\n"
            "MORE = (TFnWithA, TFnWithB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_with.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_WITH_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_WITH = "
            '[("r7_t_fn_with", '
            "R7_T_FN_WITH_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/tfnwitha.py", "backend/temporal/tfnwithb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_with.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; with binds W; _f() at module level",
    },
    "r7_t_cls_import_as": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_import_as\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_import_as", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_cls_import_as.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsimportasa.py": "class TClsImportAsA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclsimportasb.py": "class TClsImportAsB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_import_as.backend.temporal.tclsimportasa "
            "import TClsImportAsA\n"
            "from "
            "products.r7_t_cls_import_as.backend.temporal.tclsimportasb "
            "import TClsImportAsB\n"
            "\n"
            "WORKFLOWS = (TClsImportAsA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    from "
            "products.r7_t_cls_import_as.backend.temporal.more "
            "import MORE as WORKFLOWS\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_cls_import_as.backend.temporal.tclsimportasa "
            "import TClsImportAsA\n"
            "from "
            "products.r7_t_cls_import_as.backend.temporal.tclsimportasb "
            "import TClsImportAsB\n"
            "\n"
            "MORE = (TClsImportAsA, TClsImportAsB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_import_as.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_IMPORT_AS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_IMPORT_AS = "
            '[("r7_t_cls_import_as", '
            "R7_T_CLS_IMPORT_AS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclsimportasa.py", "backend/temporal/tclsimportasb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_import_as.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; import_as binds W",
    },
    "r7_t_fn_import_as": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_import_as\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_import_as", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_import_as.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnimportasa.py": "class TFnImportAsA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnimportasb.py": "class TFnImportAsB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_fn_import_as.backend.temporal.tfnimportasa "
            "import TFnImportAsA\n"
            "from "
            "products.r7_t_fn_import_as.backend.temporal.tfnimportasb "
            "import TFnImportAsB\n"
            "\n"
            "WORKFLOWS = (TFnImportAsA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    from "
            "products.r7_t_fn_import_as.backend.temporal.more "
            "import MORE as WORKFLOWS\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_fn_import_as.backend.temporal.tfnimportasa "
            "import TFnImportAsA\n"
            "from "
            "products.r7_t_fn_import_as.backend.temporal.tfnimportasb "
            "import TFnImportAsB\n"
            "\n"
            "MORE = (TFnImportAsA, TFnImportAsB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_import_as.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_IMPORT_AS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_IMPORT_AS = "
            '[("r7_t_fn_import_as", '
            "R7_T_FN_IMPORT_AS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfnimportasa.py", "backend/temporal/tfnimportasb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_import_as.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; import_as binds W; _f() at module level",
    },
    "r7_t_cls_def": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_def\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_def", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_cls_def.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsdefa.py": "class TClsDefA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclsdefb.py": "class TClsDefB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_t_cls_def.backend.temporal.tclsdefa import "
            "TClsDefA\n"
            "from products.r7_t_cls_def.backend.temporal.tclsdefb import "
            "TClsDefB\n"
            "\n"
            "WORKFLOWS = (TClsDefA,)\n"
            "\n"
            "\n"
            "def _call(f):\n"
            "    return f()\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    @_call\n"
            "    def WORKFLOWS():\n"
            "        return (TClsDefA, TClsDefB)\n",
            "backend/temporal/more.py": "from products.r7_t_cls_def.backend.temporal.tclsdefa import "
            "TClsDefA\n"
            "from products.r7_t_cls_def.backend.temporal.tclsdefb import "
            "TClsDefB\n"
            "\n"
            "MORE = (TClsDefA, TClsDefB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_def.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_DEF_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_DEF = "
            '[("r7_t_cls_def", '
            "R7_T_CLS_DEF_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/tclsdefa.py", "backend/temporal/tclsdefb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_def.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; def binds W",
    },
    "r7_t_fn_def": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_def\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_def", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_def.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfndefa.py": "class TFnDefA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfndefb.py": "class TFnDefB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_t_fn_def.backend.temporal.tfndefa import "
            "TFnDefA\n"
            "from products.r7_t_fn_def.backend.temporal.tfndefb import "
            "TFnDefB\n"
            "\n"
            "WORKFLOWS = (TFnDefA,)\n"
            "\n"
            "\n"
            "def _call(f):\n"
            "    return f()\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    @_call\n"
            "    def WORKFLOWS():\n"
            "        return (TFnDefA, TFnDefB)\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from products.r7_t_fn_def.backend.temporal.tfndefa import "
            "TFnDefA\n"
            "from products.r7_t_fn_def.backend.temporal.tfndefb import "
            "TFnDefB\n"
            "\n"
            "MORE = (TFnDefA, TFnDefB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_def.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_DEF_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_DEF = "
            '[("r7_t_fn_def", '
            "R7_T_FN_DEF_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/tfndefa.py", "backend/temporal/tfndefb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_def.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; def binds W; _f() at module level",
    },
    "r7_t_cls_class": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_class\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_class", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_cls_class.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsclassa.py": "class TClsClassA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclsclassb.py": "class TClsClassB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_t_cls_class.backend.temporal.tclsclassa "
            "import TClsClassA\n"
            "from products.r7_t_cls_class.backend.temporal.tclsclassb "
            "import TClsClassB\n"
            "\n"
            "WORKFLOWS = (TClsClassA,)\n"
            "\n"
            "\n"
            "class _TMeta(type):\n"
            "    def __iter__(cls):\n"
            "        return iter((TClsClassA, TClsClassB))\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    class WORKFLOWS(metaclass=_TMeta):\n"
            "        pass\n",
            "backend/temporal/more.py": "from products.r7_t_cls_class.backend.temporal.tclsclassa "
            "import TClsClassA\n"
            "from products.r7_t_cls_class.backend.temporal.tclsclassb "
            "import TClsClassB\n"
            "\n"
            "MORE = (TClsClassA, TClsClassB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_class.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_CLASS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_CLASS = "
            '[("r7_t_cls_class", '
            "R7_T_CLS_CLASS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclsclassa.py", "backend/temporal/tclsclassb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_class.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; class binds W",
    },
    "r7_t_fn_class": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_class\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_class", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_class.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnclassa.py": "class TFnClassA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnclassb.py": "class TFnClassB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_t_fn_class.backend.temporal.tfnclassa "
            "import TFnClassA\n"
            "from products.r7_t_fn_class.backend.temporal.tfnclassb "
            "import TFnClassB\n"
            "\n"
            "WORKFLOWS = (TFnClassA,)\n"
            "\n"
            "\n"
            "class _TMeta(type):\n"
            "    def __iter__(cls):\n"
            "        return iter((TFnClassA, TFnClassB))\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    class WORKFLOWS(metaclass=_TMeta):\n"
            "        pass\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from products.r7_t_fn_class.backend.temporal.tfnclassa "
            "import TFnClassA\n"
            "from products.r7_t_fn_class.backend.temporal.tfnclassb "
            "import TFnClassB\n"
            "\n"
            "MORE = (TFnClassA, TFnClassB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_class.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_CLASS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_CLASS = "
            '[("r7_t_fn_class", '
            "R7_T_FN_CLASS_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/tfnclassa.py", "backend/temporal/tfnclassb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_class.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; class binds W; _f() at module level",
    },
    "r7_t_cls_walrus": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_walrus\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_walrus", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_cls_walrus.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclswalrusa.py": "class TClsWalrusA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclswalrusb.py": "class TClsWalrusB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_walrus.backend.temporal.tclswalrusa "
            "import TClsWalrusA\n"
            "from "
            "products.r7_t_cls_walrus.backend.temporal.tclswalrusb "
            "import TClsWalrusB\n"
            "\n"
            "WORKFLOWS = (TClsWalrusA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    (WORKFLOWS := (TClsWalrusA, TClsWalrusB))\n",
            "backend/temporal/more.py": "from products.r7_t_cls_walrus.backend.temporal.tclswalrusa "
            "import TClsWalrusA\n"
            "from products.r7_t_cls_walrus.backend.temporal.tclswalrusb "
            "import TClsWalrusB\n"
            "\n"
            "MORE = (TClsWalrusA, TClsWalrusB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_walrus.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_WALRUS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_WALRUS = "
            '[("r7_t_cls_walrus", '
            "R7_T_CLS_WALRUS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclswalrusa.py", "backend/temporal/tclswalrusb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_walrus.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; walrus binds W",
    },
    "r7_t_fn_walrus": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_walrus\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_walrus", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_walrus.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnwalrusa.py": "class TFnWalrusA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnwalrusb.py": "class TFnWalrusB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_t_fn_walrus.backend.temporal.tfnwalrusa "
            "import TFnWalrusA\n"
            "from products.r7_t_fn_walrus.backend.temporal.tfnwalrusb "
            "import TFnWalrusB\n"
            "\n"
            "WORKFLOWS = (TFnWalrusA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    (WORKFLOWS := (TFnWalrusA, TFnWalrusB))\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from products.r7_t_fn_walrus.backend.temporal.tfnwalrusa "
            "import TFnWalrusA\n"
            "from products.r7_t_fn_walrus.backend.temporal.tfnwalrusb "
            "import TFnWalrusB\n"
            "\n"
            "MORE = (TFnWalrusA, TFnWalrusB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_walrus.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_WALRUS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_WALRUS = "
            '[("r7_t_fn_walrus", '
            "R7_T_FN_WALRUS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfnwalrusa.py", "backend/temporal/tfnwalrusb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_walrus.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; walrus binds W; _f() at module level",
    },
    "r7_t_cls_capture": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_capture\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_capture", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_cls_capture.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclscapturea.py": "class TClsCaptureA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclscaptureb.py": "class TClsCaptureB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_capture.backend.temporal.tclscapturea "
            "import TClsCaptureA\n"
            "from "
            "products.r7_t_cls_capture.backend.temporal.tclscaptureb "
            "import TClsCaptureB\n"
            "\n"
            "WORKFLOWS = (TClsCaptureA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    match (TClsCaptureA, TClsCaptureB):\n"
            "        case WORKFLOWS:\n"
            "            pass\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_cls_capture.backend.temporal.tclscapturea "
            "import TClsCaptureA\n"
            "from "
            "products.r7_t_cls_capture.backend.temporal.tclscaptureb "
            "import TClsCaptureB\n"
            "\n"
            "MORE = (TClsCaptureA, TClsCaptureB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_capture.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_CAPTURE_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_CAPTURE = "
            '[("r7_t_cls_capture", '
            "R7_T_CLS_CAPTURE_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclscapturea.py", "backend/temporal/tclscaptureb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_capture.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; capture binds W",
    },
    "r7_t_fn_capture": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_capture\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_capture", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_capture.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfncapturea.py": "class TFnCaptureA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfncaptureb.py": "class TFnCaptureB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_fn_capture.backend.temporal.tfncapturea "
            "import TFnCaptureA\n"
            "from "
            "products.r7_t_fn_capture.backend.temporal.tfncaptureb "
            "import TFnCaptureB\n"
            "\n"
            "WORKFLOWS = (TFnCaptureA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    match (TFnCaptureA, TFnCaptureB):\n"
            "        case WORKFLOWS:\n"
            "            pass\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from products.r7_t_fn_capture.backend.temporal.tfncapturea "
            "import TFnCaptureA\n"
            "from products.r7_t_fn_capture.backend.temporal.tfncaptureb "
            "import TFnCaptureB\n"
            "\n"
            "MORE = (TFnCaptureA, TFnCaptureB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_capture.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_CAPTURE_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_CAPTURE = "
            '[("r7_t_fn_capture", '
            "R7_T_FN_CAPTURE_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfncapturea.py", "backend/temporal/tfncaptureb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_capture.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; capture binds W; _f() at module level",
    },
    "r7_t_cls_as_pattern": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_as_pattern\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_as_pattern", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_cls_as_pattern.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsaspatterna.py": "class TClsAsPatternA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclsaspatternb.py": "class TClsAsPatternB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_as_pattern.backend.temporal.tclsaspatterna "
            "import TClsAsPatternA\n"
            "from "
            "products.r7_t_cls_as_pattern.backend.temporal.tclsaspatternb "
            "import TClsAsPatternB\n"
            "\n"
            "WORKFLOWS = (TClsAsPatternA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    match (TClsAsPatternA, TClsAsPatternB):\n"
            "        case tuple() as WORKFLOWS:\n"
            "            pass\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_cls_as_pattern.backend.temporal.tclsaspatterna "
            "import TClsAsPatternA\n"
            "from "
            "products.r7_t_cls_as_pattern.backend.temporal.tclsaspatternb "
            "import TClsAsPatternB\n"
            "\n"
            "MORE = (TClsAsPatternA, TClsAsPatternB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_as_pattern.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_AS_PATTERN_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_AS_PATTERN "
            '= [("r7_t_cls_as_pattern", '
            "R7_T_CLS_AS_PATTERN_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclsaspatterna.py", "backend/temporal/tclsaspatternb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_as_pattern.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; as_pattern binds W",
    },
    "r7_t_fn_as_pattern": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_as_pattern\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_as_pattern", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_as_pattern.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnaspatterna.py": "class TFnAsPatternA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnaspatternb.py": "class TFnAsPatternB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_fn_as_pattern.backend.temporal.tfnaspatterna "
            "import TFnAsPatternA\n"
            "from "
            "products.r7_t_fn_as_pattern.backend.temporal.tfnaspatternb "
            "import TFnAsPatternB\n"
            "\n"
            "WORKFLOWS = (TFnAsPatternA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    match (TFnAsPatternA, TFnAsPatternB):\n"
            "        case tuple() as WORKFLOWS:\n"
            "            pass\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_fn_as_pattern.backend.temporal.tfnaspatterna "
            "import TFnAsPatternA\n"
            "from "
            "products.r7_t_fn_as_pattern.backend.temporal.tfnaspatternb "
            "import TFnAsPatternB\n"
            "\n"
            "MORE = (TFnAsPatternA, TFnAsPatternB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_as_pattern.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_AS_PATTERN_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_AS_PATTERN = "
            '[("r7_t_fn_as_pattern", '
            "R7_T_FN_AS_PATTERN_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfnaspatterna.py", "backend/temporal/tfnaspatternb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_as_pattern.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; as_pattern binds W; _f() at module level",
    },
    "r7_t_cls_star_pattern": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_star_pattern\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_star_pattern", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_cls_star_pattern.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsstarpatterna.py": "class TClsStarPatternA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/tclsstarpatternb.py": "class TClsStarPatternB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_star_pattern.backend.temporal.tclsstarpatterna "
            "import TClsStarPatternA\n"
            "from "
            "products.r7_t_cls_star_pattern.backend.temporal.tclsstarpatternb "
            "import TClsStarPatternB\n"
            "\n"
            "WORKFLOWS = (TClsStarPatternA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    match (TClsStarPatternA, TClsStarPatternB):\n"
            "        case [*WORKFLOWS]:\n"
            "            pass\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_cls_star_pattern.backend.temporal.tclsstarpatterna "
            "import TClsStarPatternA\n"
            "from "
            "products.r7_t_cls_star_pattern.backend.temporal.tclsstarpatternb "
            "import TClsStarPatternB\n"
            "\n"
            "MORE = (TClsStarPatternA, TClsStarPatternB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_star_pattern.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_STAR_PATTERN_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_STAR_PATTERN "
            "= "
            '[("r7_t_cls_star_pattern", '
            "R7_T_CLS_STAR_PATTERN_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclsstarpatterna.py", "backend/temporal/tclsstarpatternb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_star_pattern.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; star_pattern binds W",
    },
    "r7_t_fn_star_pattern": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_star_pattern\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_star_pattern", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_fn_star_pattern.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnstarpatterna.py": "class TFnStarPatternA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnstarpatternb.py": "class TFnStarPatternB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_fn_star_pattern.backend.temporal.tfnstarpatterna "
            "import TFnStarPatternA\n"
            "from "
            "products.r7_t_fn_star_pattern.backend.temporal.tfnstarpatternb "
            "import TFnStarPatternB\n"
            "\n"
            "WORKFLOWS = (TFnStarPatternA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    match (TFnStarPatternA, TFnStarPatternB):\n"
            "        case [*WORKFLOWS]:\n"
            "            pass\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_fn_star_pattern.backend.temporal.tfnstarpatterna "
            "import TFnStarPatternA\n"
            "from "
            "products.r7_t_fn_star_pattern.backend.temporal.tfnstarpatternb "
            "import TFnStarPatternB\n"
            "\n"
            "MORE = (TFnStarPatternA, TFnStarPatternB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_star_pattern.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_STAR_PATTERN_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_STAR_PATTERN "
            "= "
            '[("r7_t_fn_star_pattern", '
            "R7_T_FN_STAR_PATTERN_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfnstarpatterna.py", "backend/temporal/tfnstarpatternb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_star_pattern.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; star_pattern binds W; _f() at module level",
    },
    "r7_t_cls_unpack": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_unpack\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_unpack", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_cls_unpack.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsunpacka.py": "class TClsUnpackA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclsunpackb.py": "class TClsUnpackB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_unpack.backend.temporal.tclsunpacka "
            "import TClsUnpackA\n"
            "from "
            "products.r7_t_cls_unpack.backend.temporal.tclsunpackb "
            "import TClsUnpackB\n"
            "\n"
            "WORKFLOWS = (TClsUnpackA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    (WORKFLOWS,) = ((TClsUnpackA, TClsUnpackB),)\n",
            "backend/temporal/more.py": "from products.r7_t_cls_unpack.backend.temporal.tclsunpacka "
            "import TClsUnpackA\n"
            "from products.r7_t_cls_unpack.backend.temporal.tclsunpackb "
            "import TClsUnpackB\n"
            "\n"
            "MORE = (TClsUnpackA, TClsUnpackB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_unpack.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_UNPACK_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_UNPACK = "
            '[("r7_t_cls_unpack", '
            "R7_T_CLS_UNPACK_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclsunpacka.py", "backend/temporal/tclsunpackb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_unpack.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; unpack binds W",
    },
    "r7_t_fn_unpack": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_unpack\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_unpack", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_unpack.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnunpacka.py": "class TFnUnpackA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnunpackb.py": "class TFnUnpackB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_t_fn_unpack.backend.temporal.tfnunpacka "
            "import TFnUnpackA\n"
            "from products.r7_t_fn_unpack.backend.temporal.tfnunpackb "
            "import TFnUnpackB\n"
            "\n"
            "WORKFLOWS = (TFnUnpackA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    (WORKFLOWS,) = ((TFnUnpackA, TFnUnpackB),)\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from products.r7_t_fn_unpack.backend.temporal.tfnunpacka "
            "import TFnUnpackA\n"
            "from products.r7_t_fn_unpack.backend.temporal.tfnunpackb "
            "import TFnUnpackB\n"
            "\n"
            "MORE = (TFnUnpackA, TFnUnpackB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_unpack.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_UNPACK_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_UNPACK = "
            '[("r7_t_fn_unpack", '
            "R7_T_FN_UNPACK_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfnunpacka.py", "backend/temporal/tfnunpackb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_unpack.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; unpack binds W; _f() at module level",
    },
    "r7_t_cls_list_target": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_list_target\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_list_target", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_cls_list_target.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclslisttargeta.py": "class TClsListTargetA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclslisttargetb.py": "class TClsListTargetB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_list_target.backend.temporal.tclslisttargeta "
            "import TClsListTargetA\n"
            "from "
            "products.r7_t_cls_list_target.backend.temporal.tclslisttargetb "
            "import TClsListTargetB\n"
            "\n"
            "WORKFLOWS = (TClsListTargetA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    [WORKFLOWS] = [(TClsListTargetA, "
            "TClsListTargetB)]\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_cls_list_target.backend.temporal.tclslisttargeta "
            "import TClsListTargetA\n"
            "from "
            "products.r7_t_cls_list_target.backend.temporal.tclslisttargetb "
            "import TClsListTargetB\n"
            "\n"
            "MORE = (TClsListTargetA, TClsListTargetB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_list_target.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_LIST_TARGET_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_LIST_TARGET "
            "= "
            '[("r7_t_cls_list_target", '
            "R7_T_CLS_LIST_TARGET_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclslisttargeta.py", "backend/temporal/tclslisttargetb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_list_target.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; list_target binds W",
    },
    "r7_t_fn_list_target": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_list_target\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_list_target", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_fn_list_target.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnlisttargeta.py": "class TFnListTargetA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnlisttargetb.py": "class TFnListTargetB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_fn_list_target.backend.temporal.tfnlisttargeta "
            "import TFnListTargetA\n"
            "from "
            "products.r7_t_fn_list_target.backend.temporal.tfnlisttargetb "
            "import TFnListTargetB\n"
            "\n"
            "WORKFLOWS = (TFnListTargetA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    [WORKFLOWS] = [(TFnListTargetA, "
            "TFnListTargetB)]\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_fn_list_target.backend.temporal.tfnlisttargeta "
            "import TFnListTargetA\n"
            "from "
            "products.r7_t_fn_list_target.backend.temporal.tfnlisttargetb "
            "import TFnListTargetB\n"
            "\n"
            "MORE = (TFnListTargetA, TFnListTargetB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_list_target.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_LIST_TARGET_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_LIST_TARGET "
            '= [("r7_t_fn_list_target", '
            "R7_T_FN_LIST_TARGET_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfnlisttargeta.py", "backend/temporal/tfnlisttargetb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_list_target.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; list_target binds W; _f() at module level",
    },
    "r7_t_cls_star_target": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_star_target\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_star_target", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_cls_star_target.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsstartargeta.py": "class TClsStarTargetA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclsstartargetb.py": "class TClsStarTargetB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_star_target.backend.temporal.tclsstartargeta "
            "import TClsStarTargetA\n"
            "from "
            "products.r7_t_cls_star_target.backend.temporal.tclsstartargetb "
            "import TClsStarTargetB\n"
            "\n"
            "WORKFLOWS = (TClsStarTargetA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    *WORKFLOWS, = (TClsStarTargetA, "
            "TClsStarTargetB)\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_cls_star_target.backend.temporal.tclsstartargeta "
            "import TClsStarTargetA\n"
            "from "
            "products.r7_t_cls_star_target.backend.temporal.tclsstartargetb "
            "import TClsStarTargetB\n"
            "\n"
            "MORE = (TClsStarTargetA, TClsStarTargetB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_star_target.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_STAR_TARGET_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_STAR_TARGET "
            "= "
            '[("r7_t_cls_star_target", '
            "R7_T_CLS_STAR_TARGET_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclsstartargeta.py", "backend/temporal/tclsstartargetb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_star_target.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; star_target binds W",
    },
    "r7_t_fn_star_target": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_star_target\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_star_target", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_fn_star_target.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnstartargeta.py": "class TFnStarTargetA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnstartargetb.py": "class TFnStarTargetB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_fn_star_target.backend.temporal.tfnstartargeta "
            "import TFnStarTargetA\n"
            "from "
            "products.r7_t_fn_star_target.backend.temporal.tfnstartargetb "
            "import TFnStarTargetB\n"
            "\n"
            "WORKFLOWS = (TFnStarTargetA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    *WORKFLOWS, = (TFnStarTargetA, TFnStarTargetB)\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_fn_star_target.backend.temporal.tfnstartargeta "
            "import TFnStarTargetA\n"
            "from "
            "products.r7_t_fn_star_target.backend.temporal.tfnstartargetb "
            "import TFnStarTargetB\n"
            "\n"
            "MORE = (TFnStarTargetA, TFnStarTargetB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_star_target.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_STAR_TARGET_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_STAR_TARGET "
            '= [("r7_t_fn_star_target", '
            "R7_T_FN_STAR_TARGET_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfnstartargeta.py", "backend/temporal/tfnstartargetb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_star_target.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; star_target binds W; _f() at module level",
    },
    "r7_t_cls_multi_target": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_multi_target\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_multi_target", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_cls_multi_target.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsmultitargeta.py": "class TClsMultiTargetA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/tclsmultitargetb.py": "class TClsMultiTargetB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_multi_target.backend.temporal.tclsmultitargeta "
            "import TClsMultiTargetA\n"
            "from "
            "products.r7_t_cls_multi_target.backend.temporal.tclsmultitargetb "
            "import TClsMultiTargetB\n"
            "\n"
            "WORKFLOWS = (TClsMultiTargetA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    _x, WORKFLOWS = 1, (TClsMultiTargetA, "
            "TClsMultiTargetB)\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_cls_multi_target.backend.temporal.tclsmultitargeta "
            "import TClsMultiTargetA\n"
            "from "
            "products.r7_t_cls_multi_target.backend.temporal.tclsmultitargetb "
            "import TClsMultiTargetB\n"
            "\n"
            "MORE = (TClsMultiTargetA, TClsMultiTargetB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_multi_target.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_MULTI_TARGET_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_MULTI_TARGET "
            "= "
            '[("r7_t_cls_multi_target", '
            "R7_T_CLS_MULTI_TARGET_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclsmultitargeta.py", "backend/temporal/tclsmultitargetb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_multi_target.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; multi_target binds W",
    },
    "r7_t_fn_multi_target": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_multi_target\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_multi_target", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_fn_multi_target.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnmultitargeta.py": "class TFnMultiTargetA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnmultitargetb.py": "class TFnMultiTargetB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_fn_multi_target.backend.temporal.tfnmultitargeta "
            "import TFnMultiTargetA\n"
            "from "
            "products.r7_t_fn_multi_target.backend.temporal.tfnmultitargetb "
            "import TFnMultiTargetB\n"
            "\n"
            "WORKFLOWS = (TFnMultiTargetA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    _x, WORKFLOWS = 1, (TFnMultiTargetA, "
            "TFnMultiTargetB)\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_fn_multi_target.backend.temporal.tfnmultitargeta "
            "import TFnMultiTargetA\n"
            "from "
            "products.r7_t_fn_multi_target.backend.temporal.tfnmultitargetb "
            "import TFnMultiTargetB\n"
            "\n"
            "MORE = (TFnMultiTargetA, TFnMultiTargetB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_multi_target.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_MULTI_TARGET_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_MULTI_TARGET "
            "= "
            '[("r7_t_fn_multi_target", '
            "R7_T_FN_MULTI_TARGET_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfnmultitargeta.py", "backend/temporal/tfnmultitargetb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_multi_target.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; multi_target binds W; _f() at module level",
    },
    "r7_t_cls_for": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_for\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_for", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_cls_for.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsfora.py": "class TClsForA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclsforb.py": "class TClsForB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_t_cls_for.backend.temporal.tclsfora import "
            "TClsForA\n"
            "from products.r7_t_cls_for.backend.temporal.tclsforb import "
            "TClsForB\n"
            "\n"
            "WORKFLOWS = (TClsForA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    for WORKFLOWS in [(TClsForA, TClsForB)]:\n"
            "        pass\n",
            "backend/temporal/more.py": "from products.r7_t_cls_for.backend.temporal.tclsfora import "
            "TClsForA\n"
            "from products.r7_t_cls_for.backend.temporal.tclsforb import "
            "TClsForB\n"
            "\n"
            "MORE = (TClsForA, TClsForB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_for.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_FOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_FOR = "
            '[("r7_t_cls_for", '
            "R7_T_CLS_FOR_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/tclsfora.py", "backend/temporal/tclsforb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_for.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; for binds W",
    },
    "r7_t_fn_for": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_for\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_for", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_for.backend.facade.contracts import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnfora.py": "class TFnForA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnforb.py": "class TFnForB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_t_fn_for.backend.temporal.tfnfora import "
            "TFnForA\n"
            "from products.r7_t_fn_for.backend.temporal.tfnforb import "
            "TFnForB\n"
            "\n"
            "WORKFLOWS = (TFnForA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    for WORKFLOWS in [(TFnForA, TFnForB)]:\n"
            "        pass\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from products.r7_t_fn_for.backend.temporal.tfnfora import "
            "TFnForA\n"
            "from products.r7_t_fn_for.backend.temporal.tfnforb import "
            "TFnForB\n"
            "\n"
            "MORE = (TFnForA, TFnForB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_for.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_FOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_FOR = "
            '[("r7_t_fn_for", '
            "R7_T_FN_FOR_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/tfnfora.py", "backend/temporal/tfnforb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_for.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; for binds W; _f() at module level",
    },
    "r7_t_cls_augassign": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_augassign\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_augassign", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_cls_augassign.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsaugassigna.py": "class TClsAugassignA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclsaugassignb.py": "class TClsAugassignB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_augassign.backend.temporal.tclsaugassigna "
            "import TClsAugassignA\n"
            "from "
            "products.r7_t_cls_augassign.backend.temporal.tclsaugassignb "
            "import TClsAugassignB\n"
            "\n"
            "WORKFLOWS = (TClsAugassignA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    WORKFLOWS += (TClsAugassignB,)\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_cls_augassign.backend.temporal.tclsaugassigna "
            "import TClsAugassignA\n"
            "from "
            "products.r7_t_cls_augassign.backend.temporal.tclsaugassignb "
            "import TClsAugassignB\n"
            "\n"
            "MORE = (TClsAugassignA, TClsAugassignB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_augassign.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_AUGASSIGN_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_AUGASSIGN = "
            '[("r7_t_cls_augassign", '
            "R7_T_CLS_AUGASSIGN_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclsaugassigna.py", "backend/temporal/tclsaugassignb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_augassign.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; augassign binds W",
    },
    "r7_t_fn_augassign": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_augassign\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_augassign", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_fn_augassign.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfnaugassigna.py": "class TFnAugassignA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfnaugassignb.py": "class TFnAugassignB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_fn_augassign.backend.temporal.tfnaugassigna "
            "import TFnAugassignA\n"
            "from "
            "products.r7_t_fn_augassign.backend.temporal.tfnaugassignb "
            "import TFnAugassignB\n"
            "\n"
            "WORKFLOWS = (TFnAugassignA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    WORKFLOWS += (TFnAugassignB,)\n"
            "\n"
            "\n"
            "_f()\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_fn_augassign.backend.temporal.tfnaugassigna "
            "import TFnAugassignA\n"
            "from "
            "products.r7_t_fn_augassign.backend.temporal.tfnaugassignb "
            "import TFnAugassignB\n"
            "\n"
            "MORE = (TFnAugassignA, TFnAugassignB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_augassign.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_AUGASSIGN_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_AUGASSIGN = "
            '[("r7_t_fn_augassign", '
            "R7_T_FN_AUGASSIGN_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfnaugassigna.py", "backend/temporal/tfnaugassignb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_augassign.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; augassign binds W; _f() at module level",
    },
    "r7_t_cls_del_getattr": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_cls_del_getattr\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_cls_del_getattr", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_cls_del_getattr.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tclsdelgetattra.py": "class TClsDelGetattrA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tclsdelgetattrb.py": "class TClsDelGetattrB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_cls_del_getattr.backend.temporal.tclsdelgetattra "
            "import TClsDelGetattrA\n"
            "from "
            "products.r7_t_cls_del_getattr.backend.temporal.tclsdelgetattrb "
            "import TClsDelGetattrB\n"
            "\n"
            "WORKFLOWS = (TClsDelGetattrA,)\n"
            "\n"
            "\n"
            "class _C:\n"
            "    global WORKFLOWS\n"
            "    del WORKFLOWS\n"
            "\n"
            "\n"
            "def __getattr__(name):\n"
            '    if name == "WORKFLOWS":\n'
            "        return (TClsDelGetattrA, TClsDelGetattrB)\n"
            "    raise AttributeError(name)\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_cls_del_getattr.backend.temporal.tclsdelgetattra "
            "import TClsDelGetattrA\n"
            "from "
            "products.r7_t_cls_del_getattr.backend.temporal.tclsdelgetattrb "
            "import TClsDelGetattrB\n"
            "\n"
            "MORE = (TClsDelGetattrA, TClsDelGetattrB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_cls_del_getattr.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_CLS_DEL_GETATTR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_CLS_DEL_GETATTR "
            "= "
            '[("r7_t_cls_del_getattr", '
            "R7_T_CLS_DEL_GETATTR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tclsdelgetattra.py", "backend/temporal/tclsdelgetattrb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_cls_del_getattr.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; class _C: global W; del_getattr binds W",
    },
    "r7_t_fn_del_getattr": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_fn_del_getattr\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_fn_del_getattr", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_fn_del_getattr.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tfndelgetattra.py": "class TFnDelGetattrA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tfndelgetattrb.py": "class TFnDelGetattrB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_fn_del_getattr.backend.temporal.tfndelgetattra "
            "import TFnDelGetattrA\n"
            "from "
            "products.r7_t_fn_del_getattr.backend.temporal.tfndelgetattrb "
            "import TFnDelGetattrB\n"
            "\n"
            "WORKFLOWS = (TFnDelGetattrA,)\n"
            "\n"
            "\n"
            "def _f():\n"
            "    global WORKFLOWS\n"
            "    del WORKFLOWS\n"
            "\n"
            "\n"
            "_f()\n"
            "\n"
            "\n"
            "def __getattr__(name):\n"
            '    if name == "WORKFLOWS":\n'
            "        return (TFnDelGetattrA, TFnDelGetattrB)\n"
            "    raise AttributeError(name)\n",
            "backend/temporal/more.py": "from "
            "products.r7_t_fn_del_getattr.backend.temporal.tfndelgetattra "
            "import TFnDelGetattrA\n"
            "from "
            "products.r7_t_fn_del_getattr.backend.temporal.tfndelgetattrb "
            "import TFnDelGetattrB\n"
            "\n"
            "MORE = (TFnDelGetattrA, TFnDelGetattrB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_fn_del_getattr.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_FN_DEL_GETATTR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_FN_DEL_GETATTR "
            '= [("r7_t_fn_del_getattr", '
            "R7_T_FN_DEL_GETATTR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/tfndelgetattra.py", "backend/temporal/tfndelgetattrb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_fn_del_getattr.backend.facade.temporal.WORKFLOWS",
        "note": "tuple W; def _f(): global W; del_getattr binds W; _f() at module level",
    },
    "r7_t_nonlocal": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_nonlocal\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_nonlocal", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_t_nonlocal.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/tnonlocala.py": "class TNonlocalA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/tnonlocalb.py": "class TNonlocalB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from products.r7_t_nonlocal.backend.temporal.tnonlocala "
            "import TNonlocalA\n"
            "from products.r7_t_nonlocal.backend.temporal.tnonlocalb "
            "import TNonlocalB\n"
            "\n"
            "WORKFLOWS = [TNonlocalA]\n"
            "\n"
            "\n"
            "def _o():\n"
            "    w = WORKFLOWS\n"
            "\n"
            "    def _i():\n"
            "        nonlocal w\n"
            "        w.append(TNonlocalB)\n"
            "\n"
            "    _i()\n"
            "\n"
            "\n"
            "_o()\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_nonlocal.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_NONLOCAL_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_NONLOCAL = "
            '[("r7_t_nonlocal", '
            "R7_T_NONLOCAL_WORKFLOWS, [])]\n"
        },
        "probes": ["backend/temporal/tnonlocala.py", "backend/temporal/tnonlocalb.py"],
        "outcome": "kept",
        "why": "limit 1: an inner function changes the outer function's alias of the list",
        "note": "nonlocal: an inner function changes the outer's alias of W; _o() at module level (limit 1 check)",
    },
    "r7_t_import_then_def": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_import_then_def\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_import_then_def", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_import_then_def.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/timportthendefa.py": "class TImportThenDefA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/timportthendefb.py": "class TImportThenDefB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_import_then_def.backend.temporal.timportthendefa "
            "import TImportThenDefA\n"
            "from "
            "products.r7_t_import_then_def.backend.temporal.timportthendefb "
            "import TImportThenDefB\n"
            "\n"
            "from "
            "products.r7_t_import_then_def.backend.temporal.defs "
            "import WORKFLOWS  # noqa: F401\n"
            "\n"
            "\n"
            "def _call(f):\n"
            "    return f()\n"
            "\n"
            "\n"
            "@_call\n"
            "def WORKFLOWS():  # noqa: F811\n"
            "    return (TImportThenDefA, TImportThenDefB)\n",
            "backend/temporal/defs.py": "from "
            "products.r7_t_import_then_def.backend.temporal.timportthendefa "
            "import TImportThenDefA\n"
            "\n"
            "WORKFLOWS = (TImportThenDefA,)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_import_then_def.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_IMPORT_THEN_DEF_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_IMPORT_THEN_DEF "
            "= "
            '[("r7_t_import_then_def", '
            "R7_T_IMPORT_THEN_DEF_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/timportthendefa.py", "backend/temporal/timportthendefb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_import_then_def.backend.facade.temporal.WORKFLOWS",
        "note": "the facade binds W by an import only, then @_call def W(): return (A, B)",
    },
    "r7_t_import_then_class": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_import_then_class\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_import_then_class", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_import_then_class.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/timportthenclassa.py": "class TImportThenClassA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/timportthenclassb.py": "class TImportThenClassB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_import_then_class.backend.temporal.timportthenclassa "
            "import TImportThenClassA\n"
            "from "
            "products.r7_t_import_then_class.backend.temporal.timportthenclassb "
            "import TImportThenClassB\n"
            "\n"
            "from "
            "products.r7_t_import_then_class.backend.temporal.defs "
            "import WORKFLOWS  # noqa: F401\n"
            "\n"
            "\n"
            "class _TMeta(type):\n"
            "    def __iter__(cls):\n"
            "        return iter((TImportThenClassA, "
            "TImportThenClassB))\n"
            "\n"
            "\n"
            "class WORKFLOWS(metaclass=_TMeta):  # noqa: F811\n"
            "    pass\n",
            "backend/temporal/defs.py": "from "
            "products.r7_t_import_then_class.backend.temporal.timportthenclassa "
            "import TImportThenClassA\n"
            "\n"
            "WORKFLOWS = (TImportThenClassA,)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_import_then_class.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_IMPORT_THEN_CLASS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_IMPORT_THEN_CLASS "
            "= "
            '[("r7_t_import_then_class", '
            "R7_T_IMPORT_THEN_CLASS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/timportthenclassa.py", "backend/temporal/timportthenclassb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_import_then_class.backend.facade.temporal.WORKFLOWS",
        "note": "the facade binds W by an import only, then class W(metaclass=...) iterating (A, B)",
    },
    "r7_t_import_then_for": {
        "group": "t",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_t_import_then_for\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_t_import_then_for", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_t_import_then_for.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/timportthenfora.py": "class TImportThenForA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/timportthenforb.py": "class TImportThenForB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_t_import_then_for.backend.temporal.timportthenfora "
            "import TImportThenForA\n"
            "from "
            "products.r7_t_import_then_for.backend.temporal.timportthenforb "
            "import TImportThenForB\n"
            "\n"
            "from "
            "products.r7_t_import_then_for.backend.temporal.defs "
            "import WORKFLOWS  # noqa: F401\n"
            "\n"
            "for WORKFLOWS in [(TImportThenForA, "
            "TImportThenForB)]:  # noqa: B007\n"
            "    pass\n",
            "backend/temporal/defs.py": "from "
            "products.r7_t_import_then_for.backend.temporal.timportthenfora "
            "import TImportThenForA\n"
            "\n"
            "WORKFLOWS = (TImportThenForA,)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_t_import_then_for.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_T_IMPORT_THEN_FOR_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_T_IMPORT_THEN_FOR "
            "= "
            '[("r7_t_import_then_for", '
            "R7_T_IMPORT_THEN_FOR_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/timportthenfora.py", "backend/temporal/timportthenforb.py"],
        "outcome": "unread",
        "reason": "products.r7_t_import_then_for.backend.facade.temporal.WORKFLOWS",
        "note": "the facade binds W by an import only, then a for target",
    },
    "r7_n_gc_referrers_item": {
        "group": "n",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_n_gc_referrers_item\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_n_gc_referrers_item", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_n_gc_referrers_item.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/ngcreferrersitema.py": "class NGcReferrersItemA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/ngcreferrersitemb.py": "class NGcReferrersItemB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "import gc\n"
            "from "
            "products.r7_n_gc_referrers_item.backend.temporal.ngcreferrersitema "
            "import NGcReferrersItemA\n"
            "from "
            "products.r7_n_gc_referrers_item.backend.temporal.ngcreferrersitemb "
            "import NGcReferrersItemB\n"
            "\n"
            "WORKFLOWS = [NGcReferrersItemA]\n"
            "for _o in gc.get_referrers(WORKFLOWS[0]):\n"
            "    if type(_o) is list and _o[:1] == "
            "[NGcReferrersItemA]:\n"
            "        _o.append(NGcReferrersItemB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_n_gc_referrers_item.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_N_GC_REFERRERS_ITEM_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_N_GC_REFERRERS_ITEM "
            "= "
            '[("r7_n_gc_referrers_item", '
            "R7_N_GC_REFERRERS_ITEM_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/ngcreferrersitema.py", "backend/temporal/ngcreferrersitemb.py"],
        "outcome": "unread",
        "reason": "products.r7_n_gc_referrers_item.backend.facade.temporal.WORKFLOWS",
        "note": "gc.get_referrers(W[0]) finds W (a visible use of the list: its element)",
    },
    "r7_n_cls_dunder_dict": {
        "group": "n",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_n_cls_dunder_dict\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_n_cls_dunder_dict", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_n_cls_dunder_dict.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/nclsdunderdicta.py": "class NClsDunderDictA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/nclsdunderdictb.py": "class NClsDunderDictB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "from "
            "products.r7_n_cls_dunder_dict.backend.temporal.nclsdunderdicta "
            "import NClsDunderDictA\n"
            "from "
            "products.r7_n_cls_dunder_dict.backend.temporal.nclsdunderdictb "
            "import NClsDunderDictB\n"
            "\n"
            "WORKFLOWS = [NClsDunderDictA]\n"
            "\n"
            "\n"
            "class _H:\n"
            "    L = WORKFLOWS\n"
            "\n"
            "\n"
            '_H.__dict__["L"].append(NClsDunderDictB)\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_n_cls_dunder_dict.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_N_CLS_DUNDER_DICT_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_N_CLS_DUNDER_DICT "
            "= "
            '[("r7_n_cls_dunder_dict", '
            "R7_N_CLS_DUNDER_DICT_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/nclsdunderdicta.py", "backend/temporal/nclsdunderdictb.py"],
        "outcome": "unread",
        "reason": "products.r7_n_cls_dunder_dict.backend.facade.temporal.WORKFLOWS",
        "note": "class _H: L = W; _H.__dict__['L'].append(B) (a visible use of a holder)",
    },
    "r7_n_getmodule_cls": {
        "group": "n",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_n_getmodule_cls\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_n_getmodule_cls", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_n_getmodule_cls.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/ngetmoduleclsa.py": "class NGetmoduleClsA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/ngetmoduleclsb.py": "class NGetmoduleClsB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import inspect\n"
            "from "
            "products.r7_n_getmodule_cls.backend.temporal.ngetmoduleclsa "
            "import NGetmoduleClsA\n"
            "from "
            "products.r7_n_getmodule_cls.backend.temporal.ngetmoduleclsb "
            "import NGetmoduleClsB\n"
            "\n"
            "WORKFLOWS = [NGetmoduleClsA]\n"
            "\n"
            "\n"
            "class _Mark:\n"
            "    pass\n"
            "\n"
            "\n"
            "inspect.getmodule(_Mark).WORKFLOWS.append(NGetmoduleClsB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_n_getmodule_cls.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_N_GETMODULE_CLS_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_N_GETMODULE_CLS = "
            '[("r7_n_getmodule_cls", '
            "R7_N_GETMODULE_CLS_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/ngetmoduleclsa.py", "backend/temporal/ngetmoduleclsb.py"],
        "outcome": "kept",
        "why": "limit 9: inspect.getmodule with no visible use of the list or a holder",
        "note": "inspect.getmodule(_Mark).W.append(B) (no use of W or a holder)",
    },
    "r7_n_getmodule_holder": {
        "group": "n",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_n_getmodule_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_n_getmodule_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_n_getmodule_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/ngetmoduleholdera.py": "class NGetmoduleHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/ngetmoduleholderb.py": "class NGetmoduleHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "import inspect\n"
            "from "
            "products.r7_n_getmodule_holder.backend.temporal.ngetmoduleholdera "
            "import NGetmoduleHolderA\n"
            "from "
            "products.r7_n_getmodule_holder.backend.temporal.ngetmoduleholderb "
            "import NGetmoduleHolderB\n"
            "\n"
            "WORKFLOWS = [NGetmoduleHolderA]\n"
            "\n"
            "\n"
            "class _H:\n"
            "    L = WORKFLOWS\n"
            "\n"
            "\n"
            "inspect.getmodule(_H).WORKFLOWS.append(NGetmoduleHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_n_getmodule_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_N_GETMODULE_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_N_GETMODULE_HOLDER "
            "= "
            '[("r7_n_getmodule_holder", '
            "R7_N_GETMODULE_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/ngetmoduleholdera.py", "backend/temporal/ngetmoduleholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_n_getmodule_holder.backend.facade.temporal.WORKFLOWS",
        "note": "inspect.getmodule(_H) where _H holds W (the holder handed on)",
    },
    "r7_n_gc_referents_holder": {
        "group": "n",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_n_gc_referents_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_n_gc_referents_holder", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_n_gc_referents_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/ngcreferentsholdera.py": "class NGcReferentsHolderA:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/temporal/ngcreferentsholderb.py": "class NGcReferentsHolderB:\n"
            "    def run(self, x):\n"
            "        return x\n",
            "backend/facade/temporal.py": "import gc\n"
            "from "
            "products.r7_n_gc_referents_holder.backend.temporal.ngcreferentsholdera "
            "import NGcReferentsHolderA\n"
            "from "
            "products.r7_n_gc_referents_holder.backend.temporal.ngcreferentsholderb "
            "import NGcReferentsHolderB\n"
            "\n"
            "WORKFLOWS = [NGcReferentsHolderA]\n"
            "_L = [WORKFLOWS]\n"
            "gc.get_referents(_L)[0].append(NGcReferentsHolderB)\n",
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_n_gc_referents_holder.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_N_GC_REFERENTS_HOLDER_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_N_GC_REFERENTS_HOLDER "
            "= "
            '[("r7_n_gc_referents_holder", '
            "R7_N_GC_REFERENTS_HOLDER_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/ngcreferentsholdera.py", "backend/temporal/ngcreferentsholderb.py"],
        "outcome": "unread",
        "reason": "products.r7_n_gc_referents_holder.backend.facade.temporal.WORKFLOWS",
        "note": "gc.get_referents(_L)[0].append(B) (the holder handed on)",
    },
    "r7_n_frame_back_func": {
        "group": "n",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_n_frame_back_func\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_n_frame_back_func", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_n_frame_back_func.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/nframebackfunca.py": "class NFrameBackFuncA:\n    def run(self, x):\n        return x\n",
            "backend/temporal/nframebackfuncb.py": "class NFrameBackFuncB:\n    def run(self, x):\n        return x\n",
            "backend/facade/temporal.py": "import sys\n"
            "from "
            "products.r7_n_frame_back_func.backend.temporal.nframebackfunca "
            "import NFrameBackFuncA\n"
            "from "
            "products.r7_n_frame_back_func.backend.temporal.nframebackfuncb "
            "import NFrameBackFuncB\n"
            "\n"
            "WORKFLOWS = [NFrameBackFuncA]\n"
            "\n"
            "\n"
            "def _ns():\n"
            "    return sys._getframe(1).f_globals\n"
            "\n"
            "\n"
            '_ns()["WORKFLOWS"].append(NFrameBackFuncB)\n',
        },
        "core": {
            "posthog/management/commands/start_temporal_worker.py": "\n"
            "from "
            "products.r7_n_frame_back_func.backend.facade.temporal "
            "import WORKFLOWS as "
            "R7_N_FRAME_BACK_FUNC_WORKFLOWS\n"
            "\n"
            "QUEUES_R7_N_FRAME_BACK_FUNC "
            "= "
            '[("r7_n_frame_back_func", '
            "R7_N_FRAME_BACK_FUNC_WORKFLOWS, "
            "[])]\n"
        },
        "probes": ["backend/temporal/nframebackfunca.py", "backend/temporal/nframebackfuncb.py"],
        "outcome": "kept",
        "why": "limit 1 (`def ns(): return globals()`) and limit 9 (a frame's globals)",
        "note": "a function returns its caller's frame globals; _ns()['W'].append(B)",
    },
    "r7_h_ctl_live": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ctl_live\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ctl_live", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ctl_live.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ctl_live.py": "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ctl_live.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "control: the live shape",
    },
    "r7_h_import_module_sys": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_import_module_sys\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_import_module_sys", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_import_module_sys.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_import_module_sys.py": "import importlib\n"
            "import os\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            'importlib.import_module("sys").modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_import_module_sys",
        "note": "importlib.import_module('sys').modules[computed]",
    },
    "r7_h_sysmod_sys_key": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_sysmod_sys_key\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_sysmod_sys_key", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_sysmod_sys_key.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_sysmod_sys_key.py": "import os\n"
            "import sys\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            'sys.modules["sys"].modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_sysmod_sys_key",
        "note": "sys.modules['sys'].modules[computed]",
    },
    "r7_h_from_sys_modules": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_from_sys_modules\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_from_sys_modules", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_from_sys_modules.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_from_sys_modules.py": "import os\n"
            "from sys import "
            "modules as _m\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            '_m[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_from_sys_modules",
        "note": "from sys import modules as _m; _m[computed]",
    },
    "r7_h_importlib_util_sys": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_importlib_util_sys\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_importlib_util_sys", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_importlib_util_sys.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_importlib_util_sys.py": "import "
            "importlib.util\n"
            "import os\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            'importlib.util.sys.modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_importlib_util_sys",
        "note": "importlib.util.sys.modules[computed]",
    },
    "r7_h_bootstrap_sys": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_bootstrap_sys\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_bootstrap_sys", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_bootstrap_sys.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_bootstrap_sys.py": "import importlib\n"
            "import os\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            'importlib._bootstrap.sys.modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_bootstrap_sys",
        "note": "importlib._bootstrap.sys.modules[computed]",
    },
    "r7_h_os_path_sys": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_os_path_sys\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_os_path_sys", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_os_path_sys.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_os_path_sys.py": "import os\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            'os.path.sys.modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_os_path_sys",
        "note": "os.path.sys.modules[computed]",
    },
    "r7_h_logging_sys": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_logging_sys\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_logging_sys", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_logging_sys.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_logging_sys.py": "import logging\n"
            "import os\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            'logging.sys.modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_logging_sys",
        "note": "logging.sys.modules[computed]",
    },
    "r7_h_inspect_sys": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_inspect_sys\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_inspect_sys", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_inspect_sys.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_inspect_sys.py": "import inspect\n"
            "import os\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            'inspect.sys.modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_inspect_sys",
        "note": "inspect.sys.modules[computed]",
    },
    "r7_h_getattr_os_sys": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_getattr_os_sys\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_getattr_os_sys", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_getattr_os_sys.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_getattr_os_sys.py": "import os\n"
            "\n"
            "\n"
            "def check():\n"
            "    return getattr(os, "
            '"sys").modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_getattr_os_sys",
        "note": "getattr(os, 'sys').modules[computed]",
    },
    "r7_h_moduletype_getattribute": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_moduletype_getattribute\nisolated: true\n",
            "package.json": '{"name": '
            '"@posthog/products-r7_h_moduletype_getattribute", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_moduletype_getattribute.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_moduletype_getattribute.py": "import "
            "os\n"
            "import "
            "types\n"
            "\n"
            "\n"
            "def "
            "check():\n"
            "    "
            "return "
            "types.ModuleType.__getattribute__(os, "
            '"sys").modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_moduletype_getattribute",
        "note": "types.ModuleType.__getattribute__(os, 'sys').modules[computed]",
    },
    "r7_h_pkgutil_importlib": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_pkgutil_importlib\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_pkgutil_importlib", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_pkgutil_importlib.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_pkgutil_importlib.py": "import os\n"
            "import pkgutil\n"
            "\n"
            "\n"
            "def load():\n"
            "    return "
            'pkgutil.importlib.import_module(os.environ["X"])\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_pkgutil_importlib",
        "note": "pkgutil.importlib.import_module(computed)",
    },
    "r7_h_inspect_importlib": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_inspect_importlib\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_inspect_importlib", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_inspect_importlib.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_inspect_importlib.py": "import inspect\n"
            "import os\n"
            "\n"
            "\n"
            "def load():\n"
            "    return "
            'inspect.importlib.import_module(os.environ["X"])\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_inspect_importlib",
        "note": "inspect.importlib.import_module(computed)",
    },
    "r7_h_import_sys_as": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_import_sys_as\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_import_sys_as", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_import_sys_as.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_import_sys_as.py": "import os\n"
            "import sys as _s\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            '_s.modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_import_sys_as",
        "note": "import sys as _s; _s.modules[computed]",
    },
    "r7_h_sys_in_holder": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_sys_in_holder\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_sys_in_holder", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_sys_in_holder.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_sys_in_holder.py": "import os\n"
            "import sys\n"
            "\n"
            "_M = [sys]\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            '_M[0].modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_sys_in_holder",
        "note": "_M = [sys]; _M[0].modules[computed]",
    },
    "r7_h_sys_second_name": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_sys_second_name\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_sys_second_name", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_sys_second_name.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_sys_second_name.py": "import os\n"
            "import sys\n"
            "\n"
            "_s = sys\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            '_s.modules[os.environ["X"]]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_sys_second_name",
        "note": "_s = sys; _s.modules[computed]",
    },
    "r7_h_builtins_dunder_import": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_builtins_dunder_import\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_builtins_dunder_import", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_builtins_dunder_import.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_builtins_dunder_import.py": "import "
            "builtins\n"
            "import "
            "os\n"
            "\n"
            "\n"
            "def "
            "check():\n"
            "    "
            "return "
            'builtins.__import__(os.environ["X"])\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_builtins_dunder_import",
        "note": "builtins.__import__(computed)",
    },
    "r7_h_sysmod_keys_load": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_sysmod_keys_load\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_sysmod_keys_load", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_sysmod_keys_load.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_sysmod_keys_load.py": "import importlib\n"
            "import sys\n"
            "\n"
            "\n"
            "def load():\n"
            "    for k in "
            "list(sys.modules.keys()):\n"
            "        if "
            'k.startswith("products."):\n'
            "            "
            "importlib.import_module(k "
            '+ ".check")\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_sysmod_keys_load",
        "note": "the keys of sys.modules handed to a loader",
    },
    "r7_h_sysmod_get_used": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_sysmod_get_used\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_sysmod_get_used", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_sysmod_get_used.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_sysmod_get_used.py": "import sys\n"
            "\n"
            "\n"
            "def check():\n"
            "    m = "
            'sys.modules.get("products")\n'
            "    return m.backend "
            "if m is not None else "
            "None\n",
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_sysmod_get_used",
        "note": "m = sys.modules.get('x'); m.attr (get used other than against None)",
    },
    "r7_h_sysmod_keys_back": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_sysmod_keys_back\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_sysmod_keys_back", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_sysmod_keys_back.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_sysmod_keys_back.py": "import sys\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            "[sys.modules[k] for "
            "k in "
            "sys.modules.keys() "
            "if "
            'k.startswith("products.")]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_sysmod_keys_back",
        "note": ".keys() of sys.modules turned back into modules",
    },
    "r7_h_sysmod_items": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_sysmod_items\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_sysmod_items", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_sysmod_items.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_sysmod_items.py": "import sys\n"
            "\n"
            "\n"
            "def check():\n"
            "    return [m for k, m in "
            "sys.modules.items() if "
            'k.startswith("products.")]\n',
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_sysmod_items",
        "note": "sys.modules.items()",
    },
    "r7_h_sysmod_dict_copy": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_sysmod_dict_copy\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_sysmod_dict_copy", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_sysmod_dict_copy.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_sysmod_dict_copy.py": "import sys\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            "dict(sys.modules)\n",
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_sysmod_dict_copy",
        "note": "dict(sys.modules)",
    },
    "r7_h_sysmod_get_default": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_sysmod_get_default\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_sysmod_get_default", '
            '"scripts": {"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_sysmod_get_default.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_sysmod_get_default.py": "import os\n"
            "import sys\n"
            "\n"
            "\n"
            "def check():\n"
            "    return "
            'sys.modules.get(os.environ["X"], '
            "sys)\n",
        },
        "probes": [],
        "outcome": "problem",
        "reason": "presets.r7_h_sysmod_get_default",
        "note": "sys.modules.get(computed, sys)",
    },
    "r7_h_ok_logger": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_logger\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_logger", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ok_logger.backend.facade.contracts import "
            "Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_logger.py": "import logging\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_logger.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "logger = "
            "logging.getLogger(__name__)\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "logging.getLogger(__name__), next to the live loader",
    },
    "r7_h_ok_sys_version": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_sys_version\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_sys_version", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_ok_sys_version.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_sys_version.py": "import sys\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_sys_version.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "NEW = sys.version_info "
            ">= (3, 12)\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "sys.version_info >= (3, 12)",
    },
    "r7_h_ok_sys_platform": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_sys_platform\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_sys_platform", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_ok_sys_platform.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_sys_platform.py": "import sys\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_sys_platform.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in "
            "MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "IS_MAC = sys.platform "
            '== "darwin"\n',
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "sys.platform == 'darwin'",
    },
    "r7_h_ok_sys_argv": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_sys_argv\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_sys_argv", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ok_sys_argv.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_sys_argv.py": "import sys\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_sys_argv.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            'IS_TEST = "test" in '
            "sys.argv\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "'test' in sys.argv",
    },
    "r7_h_ok_sys_path": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_sys_path\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_sys_path", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ok_sys_path.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_sys_path.py": "import sys\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_sys_path.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "ROOT = sys.path[0]\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "sys.path[0]",
    },
    "r7_h_ok_sys_stdout": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_sys_stdout\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_sys_stdout", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ok_sys_stdout.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_sys_stdout.py": "import sys\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_sys_stdout.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "\n"
            "def say():\n"
            "    "
            'sys.stdout.write("loaded\\n")\n',
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "sys.stdout.write in a function",
    },
    "r7_h_ok_sysmod_len": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_sysmod_len\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_sysmod_len", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ok_sysmod_len.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_sysmod_len.py": "import sys\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_sysmod_len.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "COUNT = "
            "len(sys.modules)\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "len(sys.modules)",
    },
    "r7_h_ok_sysmod_set": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_sysmod_set\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_sysmod_set", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ok_sysmod_set.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_sysmod_set.py": "import sys\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_sysmod_set.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "LOADED = "
            "set(sys.modules)\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "set(sys.modules) (its keys)",
    },
    "r7_h_ok_modules_len": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_modules_len\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_modules_len", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_ok_modules_len.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_modules_len.py": "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_modules_len.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "COUNT = len(MODULES)\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "len(MODULES)",
    },
    "r7_h_ok_metadata": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_metadata\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_metadata", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ok_metadata.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_metadata.py": "import importlib.metadata\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_metadata.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "VERSION = "
            'importlib.metadata.version("pip")\n',
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "importlib.metadata.version('pip')",
    },
    "r7_h_ok_resources": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_resources\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_resources", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ok_resources.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_resources.py": "import importlib.resources\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_resources.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "\n"
            "def data():\n"
            "    return "
            'importlib.resources.files("posthog")\n',
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "importlib.resources.files('posthog') in a function",
    },
    "r7_h_ok_type_checking": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_type_checking\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_type_checking", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_ok_type_checking.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_type_checking.py": "from typing import "
            "TYPE_CHECKING\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_type_checking.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in "
            "MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    import types\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "if TYPE_CHECKING: import types",
    },
    "r7_h_ok_dataclass": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_dataclass\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_dataclass", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from products.r7_h_ok_dataclass.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_dataclass.py": "from dataclasses import "
            "dataclass\n"
            "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_dataclass.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in MODULES:\n"
            "        "
            "importlib.import_module(m)\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Cfg:\n"
            "    name: str = 'x'\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "watched",
        "note": "a dataclass next to the live loader",
    },
    "r7_h_ok_modules_sorted": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_modules_sorted\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_modules_sorted", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_ok_modules_sorted.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_modules_sorted.py": "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_modules_sorted.backend.temporal.health_checks.check"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in "
            "sorted(MODULES):\n"
            "        "
            "importlib.import_module(m)\n",
        },
        "probes": ["backend/temporal/health_checks/check.py"],
        "outcome": "problem",
        "reason": "presets.r7_h_ok_modules_sorted",
        "note": "for m in sorted(MODULES): import_module(m)",
    },
    "r7_h_ok_modules_concat": {
        "group": "h",
        "files": {
            "__init__.py": "",
            "product.yaml": "name: r7_h_ok_modules_concat\nisolated: true\n",
            "package.json": '{"name": "@posthog/products-r7_h_ok_modules_concat", "scripts": '
            '{"backend:contract-check": "true"}}\n',
            "backend/__init__.py": "",
            "backend/facade/__init__.py": "",
            "backend/facade/contracts.py": "from posthog.dataclasses import frozen\n"
            "\n"
            "\n"
            "@frozen\n"
            "class Info:\n"
            "    team_id: int\n",
            "backend/facade/api.py": "from "
            "products.r7_h_ok_modules_concat.backend.facade.contracts "
            "import Info\n"
            "\n"
            "\n"
            "def info(team_id: int) -> Info:\n"
            "    return Info(team_id=team_id)\n",
            "backend/temporal/__init__.py": "",
            "backend/temporal/health_checks/__init__.py": "",
            "backend/temporal/health_checks/check.py": "def detect(team_id):\n    return team_id\n",
            "backend/temporal/health_checks/other.py": "def detect(team_id):\n    return team_id\n",
        },
        "core": {
            "ee/__init__.py": "",
            "ee/hogai/__init__.py": "",
            "ee/hogai/core/__init__.py": "",
            "ee/hogai/core/agent_modes/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/__init__.py": "",
            "ee/hogai/core/agent_modes/presets/r7_h_ok_modules_concat.py": "import importlib\n"
            "\n"
            "MODULES = "
            '["products.r7_h_ok_modules_concat.backend.temporal.health_checks.check"] '
            "+ "
            '["products.r7_h_ok_modules_concat.backend.temporal.health_checks.other"]\n'
            "\n"
            "\n"
            "def load():\n"
            "    for m in "
            "MODULES:\n"
            "        "
            "importlib.import_module(m)\n",
        },
        "probes": ["backend/temporal/health_checks/check.py", "backend/temporal/health_checks/other.py"],
        "outcome": "problem",
        "reason": "presets.r7_h_ok_modules_concat",
        "note": "MODULES = [PATH] + [OTHER] (two literal lists)",
    },
}
