from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import orjson
from hoglint.core.config import CoreConfig
from hoglint.native import bridge
from hoglint.native.analysis import LITE_KINDS
from hoglint.native.config import link_config
from hoglint.native.snapshot import BlobSnapshot

SOURCES = {
    "app/__init__.py": "",
    "app/base.py": "class Base:\n    pass\n",
    "app/models.py": "from app.base import Base\n\nclass Child(Base):\n    pass\n\nclass Grandchild(Child):\n    pass\n\nclass Other:\n    pass\n",
    "app/wiring.py": "from app import signals\n\nsignals.saved.connect(handler)\nsignals.saved.disconnect(handler)\n",
}


def linked(root: Path) -> Any:
    try:
        native = bridge.load()
    except ImportError:
        pytest.skip("hoglint_native is not built (tools/hoglint/bin/build)")
    repo = root / "repo"
    for path, text in SOURCES.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    snapshot = BlobSnapshot.index(repo)
    paths = sorted(snapshot.blobs)
    blobs = [(path, *snapshot.blobs[path]) for path in paths]
    extract = {"first_party": ["app"], "kinds": [], "watch_callees": ["connect"], "lite_kinds": list(LITE_KINDS)}
    config = link_config(CoreConfig(), tuple(paths), {})
    link, _ = native.link_blobs(str(repo), blobs, [], json.dumps(extract), json.dumps(config), None, 1)
    return link


def test_pack_queries_answer_from_the_link(tmp_path: Path) -> None:
    link = linked(tmp_path)
    assert link.subclasses("app.base.Base") == [
        ("app.base", "Base"),
        ("app.models", "Child"),
        ("app.models", "Grandchild"),
    ]
    rows = [(link.paths()[file], orjson.loads(row)[5]) for file, row in link.rows_named("calls", ["connect"])]
    assert rows == [("app/wiring.py", "signals.saved.connect")]
