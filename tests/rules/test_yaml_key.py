from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hoglint.adapters.yaml_key import YamlKeyAdapter, YamlKeyError

OPTIONS = {"file": "product.yaml", "key": "isolated"}
FILES = {
    "products/claims/product.yaml": "name: Claims\nisolated: true\n",
    "products/declines/product.yaml": "name: Declines\nisolated: false\n",
    "products/silent/product.yaml": "name: Silent\nowners:\n    - team-a\n",
    "products/claims/nested/product.yaml": "name: Nested\n",
}


def make_repo(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)


@pytest.mark.parametrize(
    ("directory", "claimed_by"),
    [
        ("products/claims", "products/claims/product.yaml"),
        ("products/claims/backend/facade", "products/claims/product.yaml"),
        ("products/declines/backend", None),
        ("products/silent/backend", None),
        # The nearest file decides: one without the key claims nothing for the files under it.
        ("products/claims/nested/backend", None),
        ("posthog/api", None),
        ("", None),
    ],
)
def test_the_nearest_file_with_the_key_true_claims(tmp_path: Path, directory: str, claimed_by: str | None) -> None:
    make_repo(tmp_path, FILES)
    adapter = YamlKeyAdapter()
    assert adapter.narrowed(adapter.open_run(OPTIONS, tmp_path), directory) == claimed_by


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("isolated: maybe\n", "not true or false"),
        ('isolated: "true"\n', "not true or false"),
        ("- isolated\n", "no YAML mapping"),
        ("isolated: [\n", "does not read as YAML"),
    ],
)
def test_a_value_that_is_no_boolean_is_a_claim_error(tmp_path: Path, text: str, error: str) -> None:
    make_repo(tmp_path, {"products/odd/product.yaml": text})
    with pytest.raises(YamlKeyError, match=error):
        YamlKeyAdapter().open_run(OPTIONS, tmp_path)
