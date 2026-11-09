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
    "app/use.py": "import app.models\nimport json\n\napp.models.Child.objects.all()\njson.Child.x\n",
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
    imports = link.import_rows_named(["Base", "signals"])
    assert [link.paths()[file] for file, _ in imports] == ["app/models.py", "app/wiring.py"]
    assert imports == [
        (file, tuple(row))
        for file in range(len(link.paths()))
        for row in orjson.loads(link.raw(file, "imports") or b"[]")
        if row[6] in ("Base", "signals")
    ]
    wiring = link.paths().index("app/wiring.py")
    assert link.mentions(wiring, ["callees"], ["saved.connect"], []) is True
    assert link.mentions(wiring, ["uses"], [], [".disconnect"]) is True
    assert link.mentions(wiring, ["imports", "callees"], ["Base"], [".delete"]) is False
    scopes = link.scope_files()
    uses = [
        (link.paths()[scopes[scope]], line, hit)
        for scope, _, line, _, _, (_, hit, _) in link.uses_to([("app.models", "Child")])
    ]
    assert uses == [("app/use.py", 4, ("class", [("app.models", "Child")]))]
    oids = dict(zip(link.paths(), link.blob_keys()))
    assert oids == {path: oid for path, (oid, _) in BlobSnapshot.index(tmp_path / "repo").blobs.items()}
    calls = link.raw(wiring, "calls")
    assert link.release_facts() is True
    assert link.raw(wiring, "calls") == calls
    with pytest.raises(ValueError, match="released its fact rows"):
        link.resolve_name("app.models", "Child")
