from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hoglint.rules.turbo import TurboInputs

ROOT_TURBO = {"tasks": {"check": {"inputs": ["backend/**/*.py", "!backend/**/__pycache__/**"]}}}
PACKAGES: dict[str, tuple[bool, dict[str, object] | None]] = {
    # directory: (runs the task, package turbo.json)
    "products/plain": (True, None),
    "products/default": (True, {"extends": ["//"], "tasks": {"check": {"inputs": ["$TURBO_DEFAULT$", "!secret/**"]}}}),
    "products/extends": (True, {"extends": ["//"], "tasks": {"check": {"inputs": ["$TURBO_EXTENDS$", "extra/**"]}}}),
    "products/noscript": (False, None),
    "products/rooted": (
        True,
        {"extends": ["//"], "tasks": {"check": {"inputs": ["$TURBO_ROOT$/shared/**", "../../up/**"]}}},
    ),
    "products/narrow": (True, {"extends": ["//"], "tasks": {"check": {"inputs": ["facade/**"]}}}),
}
FILES = [
    "products/plain/backend/a.py",
    "products/plain/backend/__pycache__/a.py",
    "products/plain/other.py",
    "products/default/anything.txt",
    "products/default/secret/key.py",
    "products/extends/backend/a.py",
    "products/extends/extra/b.txt",
    "products/noscript/backend/a.py",
    "shared/x.py",
    "up/y.py",
    "products/narrow/facade/api.py",
    "products/narrow/sub/logic.py",
    "posthog/models.py",
]


def make_repo(root: Path) -> None:
    (root / "turbo.json").write_text(json.dumps(ROOT_TURBO))
    for directory, (runs, turbo) in PACKAGES.items():
        (root / directory).mkdir(parents=True)
        scripts = {"check": "true"} if runs else {"lint": "true"}
        (root / directory / "package.json").write_text(json.dumps({"name": directory, "scripts": scripts}))
        if turbo is not None:
            (root / directory / "turbo.json").write_text(json.dumps(turbo))
    # Not a package: a turbo.json here must not widen the narrow package's inputs.
    (root / "products/narrow/sub").mkdir(parents=True)
    (root / "products/narrow/sub/turbo.json").write_text(json.dumps({"tasks": {"check": {"inputs": ["**"]}}}))
    for path in FILES:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    (root / "products/default/untracked.py").write_text("")


@pytest.fixture(scope="module")
def turbo(tmp_path_factory: pytest.TempPathFactory) -> TurboInputs:
    root = tmp_path_factory.mktemp("turbo")
    make_repo(root)
    return TurboInputs(root, "check")


@pytest.mark.parametrize(
    "path, watched",
    [
        ("products/plain/backend/a.py", True),
        ("products/plain/backend/__pycache__/a.py", False),
        ("products/plain/other.py", False),
        ("products/default/anything.txt", True),
        ("products/default/secret/key.py", False),
        ("products/default/untracked.py", False),
        ("products/extends/backend/a.py", True),
        ("products/extends/extra/b.txt", True),
        ("products/noscript/backend/a.py", False),
        ("shared/x.py", True),
        ("up/y.py", True),
        ("products/narrow/facade/api.py", True),
        ("products/narrow/sub/logic.py", False),
        ("posthog/models.py", False),
    ],
)
def test_turbo_inputs_follow_turbo_semantics(turbo: TurboInputs, path: str, watched: bool) -> None:
    assert turbo.covered(path)[0] is watched
