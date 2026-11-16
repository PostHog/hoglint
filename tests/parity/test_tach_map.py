"""tach's map against hoglint's: a pair only one side has is explained only by a check of that pair."""

from __future__ import annotations

from core.support import FileFacts, build
from hoglint.presets_py.posthog.parity.tach_map import LAZY_NAME_MAP, STRING_NAMES_NOTHING, HoglintMap, compare
from hoglint.presets_py.posthog.parity.verdict import HOGLINT_ONLY, OLD_ONLY, Verdict

VIEWS = "web/views.py"
LOGIC = "checkout/logic.py"


def hoglint_map() -> HoglintMap:
    files = [
        FileFacts("checkout/__init__.py"),
        FileFacts(LOGIC).define("charge").define("refund"),
        FileFacts("checkout/lazy.py")
        .define("__getattr__")
        .assign("_BASE", {"s": "checkout."})
        .assign("_LAZY", {"dict": [[{"s": "refund"}, {"s": "logic"}]]}),
        FileFacts(VIEWS).import_from("checkout.logic", "charge"),
    ]
    return HoglintMap(build(files), [f.record["path"] for f in files], (".",), False)


def test_each_pair_one_map_lacks_gets_the_reason_its_source_shows() -> None:
    sources = {
        "web/log.py": 'LOGGER = "checkout.logic.audit"\n',
        "web/both.py": 'from checkout.logic import gone\nNAME = "checkout.logic.audit"\n',
        "web/known.py": 'TARGET = "checkout.logic.refund"\n',
    }
    tach = {(VIEWS, LOGIC), *((path, LOGIC) for path in sources)}

    comparison = compare(tach, hoglint_map(), sources.__getitem__)

    found = {(d.item, d.side): d.reason for d in comparison.differences}
    assert found == {
        (f"web/log.py -> {LOGIC}", OLD_ONLY): STRING_NAMES_NOTHING,
        # An import names the target, so hoglint must have the pair: no reason fits.
        (f"web/both.py -> {LOGIC}", OLD_ONLY): None,
        # The string names a symbol hoglint knows, so it is no name that only looks like a path.
        (f"web/known.py -> {LOGIC}", OLD_ONLY): None,
        (f"checkout/lazy.py -> {LOGIC}", HOGLINT_ONLY): LAZY_NAME_MAP,
    }
    assert comparison.matched == 1
    assert comparison.verdict is Verdict.UNEXPLAINED
