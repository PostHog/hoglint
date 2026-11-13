"""The model repository: a miniature of the monorepo's layout with one invented example per case,
checked with the real preset in the native engine. See CASES.md for the cases and expected.toml for
the reviewed result."""

from __future__ import annotations

from pathlib import Path

import pytest

from native.require import native_module

from model_repo.harness import UNTIED, Comparison, Dumps, Expected, WantedRow, case_ids, catalog_rows

EXPECTED = Expected.read()


@pytest.fixture(scope="module")
def checked(tmp_path_factory: pytest.TempPathFactory) -> Dumps:
    native_module()
    work = tmp_path_factory.mktemp("model-repo")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("XDG_CACHE_HOME", str(work / "cache"))
        dumps = Dumps(work)
        dumps.run()
    return dumps


def test_every_expected_row_is_tied_to_a_case_of_the_catalog() -> None:
    known = case_ids()
    untied = [entry for entry in EXPECTED.rows if entry.case == UNTIED or entry.case not in known]
    assert untied == []
    assert {wanted.case for wanted in EXPECTED.wanted} <= known


def test_the_result_equals_the_reviewed_expected_file(checked: Dumps) -> None:
    comparison = Comparison(EXPECTED, checked.result())
    assert comparison.equal(), "\n" + comparison.render()


def wanted_case(wanted: WantedRow) -> object:
    mark = pytest.mark.xfail(strict=True, reason=f"{wanted.decision}: not built yet")
    return pytest.param(wanted, id=f"{wanted.case}-{'-'.join(wanted.match)[-40:]}", marks=mark)


@pytest.mark.parametrize("wanted", [wanted_case(wanted) for wanted in EXPECTED.wanted])
def test_a_wanted_verdict_is_given(checked: Dumps, wanted: WantedRow) -> None:
    assert wanted.found_in(checked.result().rows)


def catalog_paths() -> list[str]:
    """The paths in the file column of the catalog's cases: code spans that hold a slash."""
    paths = []
    for cells in catalog_rows():
        paths.extend(span for span in cells[1].split("`")[1::2] if "/" in span)
    return paths


def test_the_catalog_names_only_files_of_the_tree() -> None:
    tree = Path(__file__).parent / "repo"
    named = [cell for cell in catalog_paths() if not (tree / cell).exists()]
    assert named == []
