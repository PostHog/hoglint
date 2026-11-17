from __future__ import annotations

import os
import sys
import json
import shlex
import shutil
import textwrap
import subprocess
from pathlib import Path

import pytest

from hoglint.cli import ENGINE_LOCATORS, BaseEngines, EngineInstalls, LockPin
from hoglint.compare import Comparer, RawPolicy, Scans
from hoglint.config.model import DEFAULT_INSTALL
from hoglint.facts import find_binary
from hoglint.ledger.envelope import NATIVE, EngineError, EngineSources, Envelope
from hoglint.ledger.file import LedgerFile, LineKey
from hoglint.ledger.growth import AttributionCheck, Renames
from hoglint.ledger.records import FindingRecord, StoredFindings
from hoglint.ledger.scan import GrowthColumns, ScanSummary
from hoglint.model import FindingStatus
from hoglint.native import bridge
from native.require import native_required

RULE = "model-crossings"
GROWTH = {RULE: (0, 2)}  # identity (target, consumer module, shape); growth by (target, shape)


def coupling(key: tuple[str, str, str]) -> tuple[str, str, str, str, tuple[str, ...]]:
    """The coupling behind a model-crossings line: the rule, both ends, the edge kind, the shape label."""
    return RULE, key[1], key[0], "reference", (key[2],)


def finding(key: LineKey) -> FindingRecord:
    return FindingRecord(key[0], key[1], FindingStatus.NEW, None, (), "", (), True, False)


def scan(lines: dict[tuple[str, str, str], int], envelope: Envelope | None = None) -> ScanSummary:
    summary = ScanSummary(
        envelope=envelope or Envelope(),
        schema=1,
        lines={(RULE, key): count for key, count in lines.items()},
        growth=GROWTH,
    )
    summary.findings = StoredFindings.of([finding(key) for key in summary.lines])
    summary.couplings = {coupling(key): count for key, count in lines.items()}
    summary.witnesses = {(RULE, key): [coupling(key)] for key in lines}
    return summary


INVOICE = "billing.models.Invoice"
MOVED = {"alerts.util": "billing.util"}
MOVED_BACK = {"billing.util": "alerts.util"}


@pytest.mark.parametrize(
    "base, head_base, renames, base_use, code, admitted",
    [
        # a consumer split in two keeps the sum per (target, shape): no growth
        (
            {(INVOICE, "core.a", "write"): 2},
            {(INVOICE, "core.b", "write"): 1, (INVOICE, "core.c", "write"): 1},
            {},
            "write",
            0,
            0,
        ),
        # a new use is code growth
        ({(INVOICE, "core.a", "write"): 1}, {(INVOICE, "core.a", "write"): 2}, {}, "write", 1, 0),
        # a consumer swap: one use leaves, another arrives on the same key; passes on purpose
        ({(INVOICE, "core.a", "write"): 1}, {(INVOICE, "core.b", "write"): 1}, {}, "write", 0, 0),
        # moved into a product: the same coupling existed under the old module name
        ({}, {(INVOICE, "alerts.util", "write"): 1}, MOVED, "write", 0, 1),
        # a move that brings a new use along fails
        ({}, {(INVOICE, "alerts.util", "write"): 2}, MOVED, "write", 1, 0),
        # a move that also turns an annotation into a write fails (adversary fixture fx3)
        ({}, {(INVOICE, "alerts.util", "write"): 1}, MOVED, "annotation", 1, 0),
    ],
)
def test_growth_by_attribution_and_move_admission(
    base: dict, head_base: dict, renames: dict[str, str], base_use: str, code: int, admitted: int
) -> None:
    base_scan = scan(base)
    base_scan.couplings[coupling((INVOICE, "billing.util", base_use))] = 1
    head_base_scan = scan(head_base)
    result = AttributionCheck(base_scan, head_base_scan, head_base_scan, Renames(renames)).run()
    assert (len(result.code_growth), len(result.admitted_moves)) == (code, admitted)


# Growth by (target, consumer's package, shape): the package is derived from the consumer module.
GROWTH_BY_PACKAGE: dict[str, GrowthColumns] = {RULE: (0, ("package", 1), 2)}
PACKAGES = {
    "alerts.a": "alerts",
    "alerts.b": "alerts",
    "crm.b": "crm",
    "alerts.util": "alerts",
    "billing.util": "billing",
}


def package_scan(lines: dict[tuple[str, str, str], int]) -> ScanSummary:
    summary = scan(lines)
    summary.growth = GROWTH_BY_PACKAGE
    summary.labels = {"package": {key[1]: PACKAGES[key[1]] for key in lines}}
    return summary


@pytest.mark.parametrize(
    "base, head_base, renames, code, admitted",
    [
        # a coupling removed in one product never pays for a new one in another
        ({(INVOICE, "alerts.a", "write"): 1}, {(INVOICE, "crm.b", "write"): 1}, {}, 1, 0),
        # the same swap inside one product passes
        ({(INVOICE, "alerts.a", "write"): 1}, {(INVOICE, "alerts.b", "write"): 1}, {}, 0, 0),
        # a module moved inside a product passes
        ({(INVOICE, "alerts.a", "write"): 1}, {(INVOICE, "alerts.b", "write"): 1}, {"alerts.b": "alerts.a"}, 0, 0),
        # a module moved to another product with its couplings is an admitted move
        ({(INVOICE, "alerts.util", "write"): 1}, {(INVOICE, "billing.util", "write"): 1}, MOVED_BACK, 0, 1),
        # a new use bundled into that move fails
        ({(INVOICE, "alerts.util", "write"): 1}, {(INVOICE, "billing.util", "write"): 2}, MOVED_BACK, 1, 0),
    ],
)
def test_growth_is_judged_per_source_package(
    base: dict, head_base: dict, renames: dict[str, str], code: int, admitted: int
) -> None:
    head_base_scan = package_scan(head_base)
    result = AttributionCheck(package_scan(base), head_base_scan, head_base_scan, Renames(renames)).run()
    assert (len(result.code_growth), len(result.admitted_moves)) == (code, admitted)


@pytest.mark.parametrize(
    "head_lines, head_envelope, rule_growth, shrink, causes",
    [
        # better detection finds old debt: rule growth, listed, passes
        ({"write": 1, "lock": 1}, Envelope(engine="new"), [("lock", 0, 1)], [], []),
        # a new engine no longer sees debt the head code still has
        ({}, Envelope(engine="new"), [], [("write", 1, 0)], ["scanner"]),
        # a policy change does the same
        ({}, Envelope(policy="new"), [], [("write", 1, 0)], ["policy"]),
    ],
)
def test_engine_and_policy_changes_list_growth_and_shrink_with_their_cause(
    head_lines: dict[str, int], head_envelope: Envelope, rule_growth: list, shrink: list, causes: list[str]
) -> None:
    head_base = scan({(INVOICE, "core.a", "write"): 1})
    head = scan({(INVOICE, "core.a", use): count for use, count in head_lines.items()}, head_envelope)
    result = AttributionCheck(head_base, head_base, head, Renames({})).run()
    assert not result.failed
    assert [(g.key[1], g.before, g.after) for g in result.rule_growth] == rule_growth
    assert [(g.key[1], g.before, g.after) for g in result.shrink] == shrink
    assert result.shrink_causes == causes


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


ONE = (INVOICE, "core.a", "write")
TWO = (INVOICE, "core.b", "write")


@pytest.mark.parametrize(
    "base, base_head, head_base, head, why",
    [
        # only the policy changes: the same two findings appear on base code and on head code
        ({}, {ONE: 1, TWO: 1}, {}, {ONE: 1, TWO: 1}, "2 findings appear"),
        # the code changes too: ONE appears on both snapshots, TWO on head code only
        ({}, {ONE: 1}, {}, {ONE: 1, TWO: 1}, "2 findings appear"),
        ({ONE: 1, TWO: 1}, {}, {ONE: 1, TWO: 1}, {}, "2 findings disappear"),
    ],
)
def test_a_policy_entry_counts_each_finding_once_over_both_snapshots(
    base: dict, base_head: dict, head_base: dict, head: dict, why: str
) -> None:
    scans = Scans(head=scan(head), base_head=scan(base_head), base=scan(base), head_base=scan(head_base))
    ledger = LedgerFile(lines={}, exists=True)
    base_policy = RawPolicy({"rules": {RULE: {"primitive": "use-shape"}}}, [])
    head_policy = RawPolicy(
        {"rules": {RULE: {"primitive": "use-shape", "allow_shapes": ["read"]}}, "shapes": {"toolkit": {}}}, []
    )
    report = Comparer(scans, ledger, ledger, base_policy, head_policy, set(), Renames({})).report()
    assert {change.entry: change.why for change in report.policy} == {f"rules.{RULE}": why, "shapes.toolkit": why}


def has_extractor() -> bool:
    try:
        find_binary()
    except FileNotFoundError:
        return False
    return shutil.which("git") is not None


# With HOGLINT_REQUIRE_NATIVE=1 a test that needs the binary runs, and fails without it.
needs_extractor = pytest.mark.skipif(
    not has_extractor() and not native_required(), reason="needs the hoglint-facts binary"
)


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


def hoglint(
    repo: Path, *args: str, package_root: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Runs the hoglint package under `package_root` on `repo`, from outside both so the working
    directory never shadows that package, with a cache of its own so tests never touch the user's."""
    root = package_root or Path(__file__).resolve().parents[2]
    cache = repo.parent / f"{repo.name}-cache"
    full_env = {**os.environ, "PYTHONPATH": str(root), "XDG_CACHE_HOME": str(cache), **(env or {})}
    command = [sys.executable, "-m", "hoglint", "--repo", str(repo), *args]
    return subprocess.run(command, capture_output=True, text=True, env=full_env, cwd=repo.parent)


def write_files(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(textwrap.dedent(content))


def git_in(repo: Path, *args: str) -> str:
    command = ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@t"]
    return subprocess.run([*command, *args], check=True, capture_output=True, text=True).stdout


@needs_extractor
def test_cli_check_write_ledger_and_compare_on_a_real_repo(tmp_path: Path) -> None:
    for name, content in FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(textwrap.dedent(content))

    git_in(tmp_path, "init", "-q")
    assert hoglint(tmp_path, "check").returncode == 1
    assert hoglint(tmp_path, "check", "--write-ledger", "--record-growth").returncode == 0
    git_in(tmp_path, "add", "-A")
    git_in(tmp_path, "commit", "-qm", "base")
    git_in(tmp_path, "branch", "base")

    git_in(tmp_path, "mv", "products/billing/util.py", "products/alerts/util.py")
    assert hoglint(tmp_path, "check", "--write-ledger", "--record-growth").returncode == 0
    git_in(tmp_path, "add", "-A")
    moved = hoglint(tmp_path, "compare", "--base", "base")
    assert moved.returncode == 0, moved.stdout + moved.stderr
    assert "admitted moves (review) (1)" in moved.stdout

    (tmp_path / "core" / "api.py").write_text("from products.billing.logic import charge\n")
    assert hoglint(tmp_path, "check", "--write-ledger", "--record-growth").returncode == 0
    grown = hoglint(tmp_path, "compare", "--base", "base")
    assert grown.returncode == 1
    assert "code growth (fails) (1)" in grown.stdout


@needs_extractor
def test_the_ledger_shrinks_freely_and_grows_only_with_a_person(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    # Growth per product (D67), so a use that leaves alerts does not pay for a new one in core.
    config = CONFIG.replace('growth_key = ["dst.symbol"]', 'growth_key = ["dst.symbol", "src.package"]')
    write_files(repo, {**FILES, "hoglint.toml": config})
    git_in(repo, "init", "-q")
    assert hoglint(repo, "check", "--write-ledger").returncode == 1
    assert not (repo / "hoglint.ledger").exists()
    assert hoglint(repo, "check", "--write-ledger", "--record-growth").returncode == 0
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    ledger = (repo / "hoglint.ledger").read_text()

    # A fix and a new finding in one change: the shrink takes the fixed line and adds nothing.
    (repo / "products/alerts/logic.py").write_text("from products.billing.facade import charge\n")
    (repo / "core/api.py").write_text("from products.billing.logic import charge\n")
    shrunk = hoglint(repo, "ledger", "shrink")
    assert shrunk.returncode == 0 and "removed or lowered 1 lines" in shrunk.stdout, shrunk.stdout + shrunk.stderr
    lines = (repo / "hoglint.ledger").read_text().splitlines()
    assert lines == [line for line in ledger.splitlines() if "alerts.logic" not in line] != ledger.splitlines()
    assert hoglint(repo, "check").returncode == 1

    # Recording the new line needs the switch, and the hook still blocks it with HEAD's policy.
    refused = hoglint(repo, "check", "--write-ledger")
    assert refused.returncode == 1 and "not written: the scan adds 1 lines" in refused.stdout
    assert (repo / "hoglint.ledger").read_text().splitlines() == lines
    assert hoglint(repo, "check", "--write-ledger", "--record-growth").returncode == 0
    git_in(repo, "add", "-A")
    hook = hoglint(repo, "check", "--index")
    assert hook.returncode == 1 and "This change adds 1 line to hoglint.ledger, with no policy change." in hook.stdout

    # A policy change in the same commit moves lines by design: compare judges them, not the hook.
    (repo / "hoglint.toml").write_text(config + 'doc = "changed"\n')
    assert hoglint(repo, "check", "--write-ledger").returncode == 0
    git_in(repo, "add", "-A")
    assert hoglint(repo, "check", "--index").returncode == 0


EXCEPTION = """schema = 1

[[exception]]
rule = "public-surface"
match = {{ "dst.symbol" = "products.billing.logic.charge", "src.module" = "core.api" }}
reason = "core reads the charge until the facade has it"
owners = ["team-billing"]
scope = "prod"
{review_by}"""


@needs_extractor
@pytest.mark.parametrize(
    "review_by, hook, ci, says",
    [
        ("", 1, 1, "missing review_by"),
        ('review_by = "2027-03-01"\n', 0, 0, "code growth a new exception allows (needs a person's review) (1)"),
    ],
    ids=["no review date", "complete"],
)
def test_growth_a_complete_new_exception_allows_passes_compare_for_review(
    tmp_path: Path, review_by: str, hook: int, ci: int, says: str
) -> None:
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    git_in(repo, "init", "-q")
    assert hoglint(repo, "check", "--write-ledger", "--record-growth").returncode == 0
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    git_in(repo, "branch", "base")
    (repo / "core/api.py").write_text("from products.billing.logic import charge\n")
    (repo / "hoglint.exceptions.toml").write_text(EXCEPTION.format(review_by=review_by))
    git_in(repo, "add", "-A")
    checked = hoglint(repo, "check", "--index")
    assert checked.returncode == hook, checked.stdout + checked.stderr
    git_in(repo, "commit", "-qm", "head")
    compared = hoglint(repo, "compare", "--base", "base")
    assert compared.returncode == ci, compared.stdout + compared.stderr
    assert says in compared.stdout
    assert "code growth (fails)" not in compared.stdout or ci == 1


@needs_extractor
def test_compare_blocks_a_config_name_the_change_made_stale(tmp_path: Path) -> None:
    seeded = CONFIG + '[fields.seed]\ntype = "bool"\n\n[[assign]]\nmatch = "core/api.py"\nset = { seed = true }\n'
    repo = tmp_path / "repo"
    write_files(repo, {**FILES, "hoglint.toml": seeded})
    git_in(repo, "init", "-q")
    assert hoglint(repo, "check", "--write-ledger", "--record-growth").returncode == 0
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    git_in(repo, "branch", "base")
    git_in(repo, "mv", "core/api.py", "core/views.py")
    git_in(repo, "commit", "-qm", "move the seeded file")
    compared = hoglint(repo, "compare", "--base", "base")
    assert compared.returncode == 2, compared.stdout + compared.stderr
    introduced = compared.stdout.split("incomplete, introduced (blocks)")[1].split("\n\n")[0]
    assert "config:stale-name: core/api.py is named by [[assign]] core/api.py" in introduced


ENGINE_PACKAGE = "tools/hoglint/hoglint"
FAKE_CRATE = {
    "tools/hoglint/Cargo.toml": "[package]\nname = 'fake'\n",
    "tools/hoglint/src/main.rs": "fn main() {}\n",
    "tools/hoglint/engine-sources.txt": "[facts]\nsrc/**/*.rs\n[native]\n[engine]\nprotocol 1\nhoglint/**/*.py\n",
}


@needs_extractor
@pytest.mark.parametrize(
    "base_engine, code, says",
    [
        # the base commit has no engine: nothing to attribute against, the growth check is skipped
        ("absent", 0, "growth check skipped: the base commit predates hoglint"),
        # the base engine exists but cannot run: incomplete, never a skip
        ("broken", 2, "base scan failed (blocks): the base engine failed"),
    ],
)
def test_compare_tells_a_base_without_engine_from_a_base_engine_that_fails(
    tmp_path: Path, base_engine: str, code: int, says: str
) -> None:
    repo = tmp_path / "repo"
    write_files(repo, {**FILES, "hoglint.toml": CONFIG.replace("[fields]", 'exclude = ["tools/**"]\n\n[fields]')})
    package = Path(__file__).resolve().parents[2] / "hoglint"
    env = {}
    if base_engine == "broken":
        shutil.copytree(package, repo / ENGINE_PACKAGE, ignore=shutil.ignore_patterns("__pycache__"))
        (repo / ENGINE_PACKAGE / "__main__.py").write_text("raise SystemExit('broken base engine')\n")
        write_files(repo, FAKE_CRATE)
        fake_binary = tmp_path / "base-facts"
        base_sources = EngineSources.read(repo / "tools/hoglint")
        assert base_sources is not None
        fake_binary.write_text(f"#!/bin/sh\necho {base_sources.digest(NATIVE)}\n")
        fake_binary.chmod(0o755)
        env["HOGLINT_BASE_FACTS_BIN"] = str(fake_binary)
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    git_in(repo, "branch", "base")

    shutil.rmtree(repo / "tools", ignore_errors=True)
    shutil.copytree(package, repo / ENGINE_PACKAGE, ignore=shutil.ignore_patterns("__pycache__"))
    head_engine = repo / "tools/hoglint"
    assert hoglint(repo, "check", "--write-ledger", "--record-growth", package_root=head_engine).returncode == 0
    git_in(repo, "add", "-A")
    compared = hoglint(repo, "compare", "--base", "base", package_root=head_engine, env=env)
    assert compared.returncode == code, compared.stdout + compared.stderr
    assert says in compared.stdout


@needs_extractor
def test_compare_runs_a_changed_base_engine_as_the_engine_it_was_asked_for(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    crate = Path(__file__).resolve().parents[2]
    shutil.copytree(crate, repo / "tools/hoglint", ignore=shutil.ignore_patterns("__pycache__", "target", ".*"))
    write_files(repo, {**FILES, "hoglint.toml": CONFIG.replace("[fields]", 'exclude = ["tools/**"]\n\n[fields]')})
    git_in(repo, "init", "-q")
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    git_in(repo, "branch", "base")
    views = repo / "tools/hoglint/hoglint/views.py"
    views.write_text(views.read_text() + "\n# an engine edit\n")
    git_in(repo, "commit", "-qam", "engine edit")
    compared = hoglint(repo, "--engine", "native", "compare", "--base", "base", package_root=repo / "tools/hoglint")
    assert "so the full base scan ran" in compared.stdout, compared.stdout + compared.stderr
    assert "base scan failed" not in compared.stdout


@pytest.mark.parametrize(
    "lock, error",
    [
        (None, ""),
        # a lock the pin cannot be read from leaves the base engine unknown, never absent
        ("[[package]\n", "does not read"),
        (
            '[[package]]\nname = "hoglint"\nversion = "0.3.0"\n[[package]]\nname = "hoglint"\nversion = "0.4.0"\n',
            "pins",
        ),
    ],
    ids=["no lock", "broken lock", "two versions"],
)
def test_a_lock_without_one_readable_pin_is_unknown_unless_it_is_absent(
    tmp_path: Path, lock: str | None, error: str
) -> None:
    if lock is not None:
        (tmp_path / "uv.lock").write_text(lock)
    if not error:
        assert LockPin.read(tmp_path / "uv.lock") is None
        return
    with pytest.raises(EngineError, match=error):
        LockPin.read(tmp_path / "uv.lock")


RUNNING_VERSION = "0.2.0"
# The `hoglint/__main__.py` of a pinned engine the stub installs: it records what compare asks of
# the engine, which of this engine's locators reached it (its own directory on PYTHONPATH is not
# one) and which interpreter runs it, then runs the engine package it sits in.
PINNED_MAIN = """\
import os
import sys
import json
from pathlib import Path

here = Path(__file__).resolve().parents[1]
inherited = sorted(name for name in {locators!r} if name in os.environ and Path(os.environ[name]).resolve() != here)
with open({log!r}, "a") as handle:
    handle.write(json.dumps([*sys.argv[1:], inherited, sys.executable]) + "\\n")
if {silent!r} and "scan" in sys.argv:
    sys.exit(0)
from hoglint.cli import entry

sys.exit(entry())
"""
# Run by the install template with the version and the target: it installs this engine as that
# version, with its native module and an extractor that reports another build id, and logs it.
STUB_INSTALL = """\
import sys
import json
import shutil
from pathlib import Path

version, target = sys.argv[1], Path(sys.argv[2])
with open({log!r}, "a") as handle:
    handle.write(json.dumps(["install", version]) + "\\n")
shutil.copytree(Path({site!r}) / "hoglint", target / "hoglint")
shutil.copytree(Path({native!r}) / "hoglint_native", target / "hoglint_native")
(target / "hoglint" / "__main__.py").write_text({main!r})
binary = target / "hoglint" / "bin" / "hoglint-facts"
binary.parent.mkdir()
shutil.copy({binary!r}, binary)
info = target / f"hoglint-{{version}}.dist-info"
info.mkdir()
(info / "METADATA").write_text(f"Metadata-Version: 2.1\\nName: hoglint\\nVersion: {{version}}\\n")
(info / "RECORD").write_text(f"hoglint/bin/hoglint-facts,,{{binary.stat().st_size}}\\n")
"""


def installed_engine(root: Path) -> Path:
    """The engine package as a wheel installs it: no crate next to it, and the distribution's
    metadata. Returns the directory that holds both."""
    site = root / "site"
    package = Path(__file__).resolve().parents[2] / "hoglint"
    shutil.copytree(package, site / "hoglint", ignore=shutil.ignore_patterns("__pycache__"))
    info = site / f"hoglint-{RUNNING_VERSION}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: hoglint\nVersion: {RUNNING_VERSION}\n")
    return site


def stub_install(root: Path, site: Path, silent: bool) -> str:
    """An install template for `[engine] install`, and the stub it runs; both log to root/engine.log."""
    pinned_binary = root / "pinned-facts"
    pinned_binary.write_text(
        f'#!/bin/sh\n[ "$1" = "--build-id" ] && echo pinned-build && exit 0\nexec {find_binary()} "$@"\n'
    )
    pinned_binary.chmod(0o755)
    log, native = str(root / "engine.log"), str(bridge.built_module_dir())
    main = PINNED_MAIN.format(log=log, silent=silent, locators=ENGINE_LOCATORS)
    stub = root / "install.py"
    stub.write_text(STUB_INSTALL.format(log=log, site=str(site), native=native, main=main, binary=str(pinned_binary)))
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(stub))} {{version}} {{target}}"


def engine_calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_compare_installs_the_pinned_engine_and_runs_it_under_this_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ENGINE_LOCATORS:
        monkeypatch.setenv(name, str(tmp_path / "this-engine"))
    target = tmp_path / "a cache" / "engines" / "0.1.0"
    # the target path holds a space, and stays one word
    installs = [
        "uv",
        "pip",
        "install",
        "--no-deps",
        "--python",
        sys.executable,
        "--target",
        str(target),
        "hoglint==0.1.0",
    ]
    assert EngineInstalls.command(DEFAULT_INSTALL, "0.1.0", target) == installs
    engine = BaseEngines.installed_engine(target)
    assert engine.command == (sys.executable, "-m", "hoglint")
    assert {name: engine.env.get(name) for name in ENGINE_LOCATORS} == {
        name: str(target) if name == "PYTHONPATH" else None for name in ENGINE_LOCATORS
    }


@needs_extractor
@pytest.mark.parametrize(
    "pin, silent, code, says, asked",
    [
        # the base pins the running version: this process scans it, nothing is installed
        (RUNNING_VERSION, False, 1, "code growth (fails) (1)", []),
        # another version: it is installed, gives its id, then scans base and head
        ("0.1.0", False, 1, "code growth (fails) (1)", ["install", "engine-id", "scan", "scan"]),
        # no pin: the base predates hoglint
        (None, False, 0, "growth check skipped: the base commit predates hoglint", []),
        # an engine that prints no summary is a base engine that failed
        (
            "0.1.0",
            True,
            2,
            "base scan failed (blocks): the base engine failed (exit 0)",
            ["install", "engine-id", "scan"],
        ),
    ],
    ids=["same version", "other version", "no pin", "no summary"],
)
def test_an_installed_engine_scans_the_base_with_the_version_the_base_pins(
    tmp_path: Path, pin: str | None, silent: bool, code: int, says: str, asked: list[str]
) -> None:
    site = installed_engine(tmp_path)
    install = stub_install(tmp_path, site, silent)
    repo = tmp_path / "repo"
    lock = f'version = 1\n\n[[package]]\nname = "hoglint"\nversion = "{pin}"\n' if pin else "version = 1\n"
    write_files(repo, {**FILES, "uv.lock": lock, "hoglint.toml": CONFIG + f"\n[engine]\ninstall = '{install}'\n"})
    git_in(repo, "init", "-q")
    assert hoglint(repo, "check", "--write-ledger", "--record-growth", package_root=site).returncode == 0
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    git_in(repo, "branch", "base")
    (repo / "core" / "api.py").write_text("from products.billing.logic import charge\n")
    assert hoglint(repo, "check", "--write-ledger", "--record-growth", package_root=site).returncode == 0
    git_in(repo, "commit", "-qam", "head")

    compared = hoglint(repo, "compare", "--base", "base", package_root=site)
    assert compared.returncode == code, compared.stdout + compared.stderr
    assert says in compared.stdout
    log = tmp_path / "engine.log"
    calls = engine_calls(log)
    assert ["scan" if "scan" in call else call[0] for call in calls] == asked
    # The pinned engine runs under the head's interpreter, with none of the head engine's locators.
    assert all(call == ["install", pin] or call[-2:] == [[], sys.executable] for call in calls)
    if pin is None or asked != ["install", "engine-id", "scan", "scan"]:
        return
    # The install is kept by version, and the pinned engine's summaries under its own id, so the
    # next compare installs nothing and reads them.
    again = hoglint(repo, "compare", "--base", "base", package_root=site)
    assert "base summary: the stored summary" in again.stdout, again.stdout + again.stderr
    assert engine_calls(log)[len(calls) :] == [["engine-id", [], sys.executable]]
    assert list((repo.parent / "repo-cache").rglob("native-pinned-build"))
    # A kept install that lost a file to the cache's prune is installed again, never used.
    calls = engine_calls(log)
    (repo.parent / "repo-cache" / "hoglint" / "engines" / pin / "hoglint" / "bin" / "hoglint-facts").unlink()
    pruned = hoglint(repo, "compare", "--base", "base", package_root=site)
    assert pruned.returncode == code, pruned.stdout + pruned.stderr
    assert engine_calls(log)[len(calls) :] == [["install", pin], ["engine-id", [], sys.executable]]


@needs_extractor
@pytest.mark.parametrize(
    "imported, says",
    [
        # a summary exported by CI and imported by key replaces the base scan
        (True, "base summary: the stored summary {key}"),
        # without it, compare runs the base scan and says no CI summary was found
        (False, "base summary: no CI summary was found for {key}, so the full base scan ran"),
    ],
)
def test_compare_reads_a_ci_summary_by_its_key(tmp_path: Path, imported: bool, says: str) -> None:
    repo = tmp_path / "repo"
    write_files(repo, FILES)
    git_in(repo, "init", "-q")
    assert hoglint(repo, "check", "--write-ledger", "--record-growth").returncode == 0
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    git_in(repo, "branch", "base")
    published = tmp_path / "summary.json"
    ci = {"XDG_CACHE_HOME": str(tmp_path / "ci-cache"), "CI": "true"}
    key = hoglint(repo, "summary", "export", "--file", str(published), env=ci).stdout.strip()
    assert key.startswith("hoglint-summary-p")
    assert hoglint(repo, "summary", "key", "--ref", "base", env=ci).stdout.strip() == key

    (repo / "core" / "api.py").write_text("from products.billing.logic import charge\n")
    assert hoglint(repo, "check", "--write-ledger", "--record-growth").returncode == 0
    git_in(repo, "add", "-A")
    pr = {"XDG_CACHE_HOME": str(tmp_path / "pr-cache"), "CI": "true"}
    if imported:
        assert hoglint(repo, "summary", "import", "--file", str(published), env=pr).stdout.strip() == key
    compared = hoglint(repo, "compare", "--base", "base", env=pr)
    assert says.format(key=key) in compared.stdout, compared.stdout + compared.stderr
    assert "code growth (fails) (1)" in compared.stdout
