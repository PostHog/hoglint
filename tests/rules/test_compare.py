from __future__ import annotations

import os
import sys
import shutil
import textwrap
import subprocess
from pathlib import Path

import pytest

from hoglint.compare import Comparer, RawPolicy, Scans
from hoglint.facts import Extractor
from hoglint.ledger.file import LedgerFile
from hoglint.ledger.growth import AttributionCheck, Renames
from hoglint.ledger.scan import ScanSummary

RULE = "model-crossings"
GROWTH = {RULE: (0, 2)}  # identity (target, consumer module, shape); growth by (target, shape)


def scan(
    lines: dict[tuple[str, str, str], int], couplings: dict[tuple[str, str, str], int] | None = None
) -> ScanSummary:
    summary = ScanSummary(schema=1, lines={(RULE, key): count for key, count in lines.items()}, growth=GROWTH)
    summary.findings = set(summary.lines)
    summary.couplings = dict(couplings or {})
    summary.witnesses = {(RULE, key): [(key[1], key[0], "reference")] for key in lines}
    return summary


INVOICE = "billing.models.Invoice"


@pytest.mark.parametrize(
    "base, head_base, renames, code, admitted",
    [
        # a consumer split in two keeps the sum per (target, shape): no growth
        (
            {(INVOICE, "core.a", "write"): 2},
            {(INVOICE, "core.b", "write"): 1, (INVOICE, "core.c", "write"): 1},
            {},
            0,
            0,
        ),
        # a new use is code growth
        ({(INVOICE, "core.a", "write"): 1}, {(INVOICE, "core.a", "write"): 2}, {}, 1, 0),
        # a consumer swap: one use leaves, another arrives on the same key; passes on purpose
        ({(INVOICE, "core.a", "write"): 1}, {(INVOICE, "core.b", "write"): 1}, {}, 0, 0),
        # moved into a product: the same coupling existed under the old module name
        ({}, {(INVOICE, "alerts.util", "write"): 1}, {"alerts.util": "billing.util"}, 0, 1),
        # a move that brings a new use along fails
        ({}, {(INVOICE, "alerts.util", "write"): 2}, {"alerts.util": "billing.util"}, 1, 0),
    ],
)
def test_growth_by_attribution_and_move_admission(
    base: dict, head_base: dict, renames: dict[str, str], code: int, admitted: int
) -> None:
    base_couplings = {("billing.util", INVOICE, "reference"): 1}
    head_base_scan = scan(head_base)
    head_base_scan.couplings = {(key[1], key[0], "reference"): count for key, count in head_base.items()}
    result = AttributionCheck(scan(base, base_couplings), head_base_scan, head_base_scan, Renames(renames)).run()
    assert (len(result.code_growth), len(result.admitted_moves)) == (code, admitted)


def test_rule_growth_passes_and_is_listed() -> None:
    base = scan({(INVOICE, "core.a", "write"): 1})
    head = scan({(INVOICE, "core.a", "write"): 1, (INVOICE, "core.a", "lock"): 1})
    result = AttributionCheck(base, base, head, Renames({})).run()
    assert not result.failed
    assert [(g.key, g.before, g.after) for g in result.rule_growth] == [((INVOICE, "lock"), 0, 1)]


def test_compare_pairs_swaps_and_labels_policy_changes() -> None:
    base_ledger = LedgerFile(lines={(RULE, (INVOICE, "core.a", "write")): 1}, exists=True)
    head_ledger = LedgerFile(lines={(RULE, (INVOICE, "core.b", "write")): 1}, exists=True)
    base = scan({(INVOICE, "core.a", "write"): 1})
    head = scan({(INVOICE, "core.b", "write"): 1})
    relaxed = scan({})
    scans = Scans(head=head, base_head=relaxed, base=base, head_base=head)
    base_policy = RawPolicy({"rules": {RULE: {"primitive": "use-shape"}}}, [])
    head_policy = RawPolicy(
        {"rules": {RULE: {"primitive": "use-shape", "allow_shapes": ["write"]}}},
        [{"rule": RULE, "reason": "x"}],
    )
    report = Comparer(scans, base_ledger, head_ledger, base_policy, head_policy, {"core.b"}, Renames({})).report()
    assert [(s.removed[1][1], s.added[1][1]) for s in report.swaps] == [("core.a", "core.b")]
    assert report.ledger_added == [] and report.ledger_removed == []
    labels = {change.entry.split(" ")[0]: change.label for change in report.policy}
    assert labels == {"exception": "relax", f"rules.{RULE}": "relax"}
    assert [key for key, _ in report.touched_debt] == [(RULE, (INVOICE, "core.b", "write"))]


def has_extractor() -> bool:
    try:
        Extractor.find_binary()
    except FileNotFoundError:
        return False
    return shutil.which("git") is not None


CONFIG = """
schema = 1

[discovery]
first_party = ["products", "core"]

[fields]
package = { type = "id", required_on = ["products/**", "core/**"] }
tier = { type = "enum", values = ["core", "product"], required_on = ["products/**", "core/**"] }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}", tier = "product" }

[[assign]]
match = "core/**"
set = { package = "core", tier = "core" }

[shapes.contract]
data = ["frozen-dataclass"]

[exports.facade]
within = "products/{product}"
paths = ["facade.py"]
shape = "contract"

[rules.public-surface]
primitive = "entrypoint"
kinds = ["import"]
select = "src.package != dst.package and dst.tier == 'product'"
identity = "module-symbol"
growth_key = ["dst.symbol"]
"""

FILES = {
    "hoglint.toml": CONFIG,
    "products/__init__.py": "",
    "products/billing/__init__.py": "",
    "products/billing/logic.py": "def charge():\n    return 1\n",
    "products/billing/facade.py": "from products.billing.logic import charge\n",
    "products/billing/util.py": "from products.billing.logic import charge\n",
    "products/alerts/__init__.py": "",
    "products/alerts/logic.py": "from products.billing.logic import charge\n",
    "core/__init__.py": "",
    "core/api.py": "from products.billing.facade import charge\n",
}


def hoglint(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    package_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    return subprocess.run(
        [sys.executable, "-m", "hoglint", "--repo", str(repo), *args], capture_output=True, text=True, env=env
    )


@pytest.mark.skipif(not has_extractor(), reason="needs the hoglint-facts binary")
def test_cli_check_write_ledger_and_compare_on_a_real_repo(tmp_path: Path) -> None:
    for name, content in FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(textwrap.dedent(content))

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "commit.gpgsign=false", *args], check=True, capture_output=True
        )

    git("init", "-q")
    assert hoglint(tmp_path, "check").returncode == 1
    assert hoglint(tmp_path, "check", "--write-ledger").returncode == 0
    git("add", "-A")
    git("commit", "-qm", "base")
    git("branch", "base")

    git("mv", "products/billing/util.py", "products/alerts/util.py")
    assert hoglint(tmp_path, "check", "--write-ledger").returncode == 0
    git("add", "-A")
    moved = hoglint(tmp_path, "compare", "--base", "base")
    assert moved.returncode == 0, moved.stdout + moved.stderr
    assert "admitted moves (review) (1)" in moved.stdout

    (tmp_path / "core" / "api.py").write_text("from products.billing.logic import charge\n")
    assert hoglint(tmp_path, "check", "--write-ledger").returncode == 0
    grown = hoglint(tmp_path, "compare", "--base", "base")
    assert grown.returncode == 1
    assert "code growth (fails) (1)" in grown.stdout
