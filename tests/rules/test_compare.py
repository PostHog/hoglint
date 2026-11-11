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
from hoglint.ledger.envelope import NATIVE, EngineSources, Envelope
from hoglint.ledger.file import LedgerFile, LineKey
from hoglint.ledger.growth import AttributionCheck, Renames
from hoglint.ledger.records import FindingRecord, StoredFindings
from hoglint.ledger.scan import ScanSummary
from hoglint.model import FindingStatus
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
GROWTH_BY_PACKAGE = {RULE: (0, ("package", 1), 2)}
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
        # a coupling removed in one product no longer pays for a new one in another
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


def has_extractor() -> bool:
    try:
        Extractor.find_binary()
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
    assert hoglint(repo, "check", "--write-ledger", package_root=head_engine).returncode == 0
    git_in(repo, "add", "-A")
    compared = hoglint(repo, "compare", "--base", "base", package_root=head_engine, env=env)
    assert compared.returncode == code, compared.stdout + compared.stderr
    assert says in compared.stdout


@needs_extractor
@pytest.mark.parametrize("engine", ["python", "native"])
def test_compare_runs_a_changed_base_engine_as_the_engine_it_was_asked_for(tmp_path: Path, engine: str) -> None:
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
    compared = hoglint(repo, "--engine", engine, "compare", "--base", "base", package_root=repo / "tools/hoglint")
    assert "so the full base scan ran" in compared.stdout, compared.stdout + compared.stderr
    assert "base scan failed" not in compared.stdout


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
    assert hoglint(repo, "check", "--write-ledger").returncode == 0
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-qm", "base")
    git_in(repo, "branch", "base")
    published = tmp_path / "summary.json"
    ci = {"XDG_CACHE_HOME": str(tmp_path / "ci-cache"), "CI": "true"}
    key = hoglint(repo, "summary", "export", "--file", str(published), env=ci).stdout.strip()
    assert key.startswith("hoglint-summary-p")
    assert hoglint(repo, "summary", "key", "--ref", "base", env=ci).stdout.strip() == key

    (repo / "core" / "api.py").write_text("from products.billing.logic import charge\n")
    assert hoglint(repo, "check", "--write-ledger").returncode == 0
    git_in(repo, "add", "-A")
    pr = {"XDG_CACHE_HOME": str(tmp_path / "pr-cache"), "CI": "true"}
    if imported:
        assert hoglint(repo, "summary", "import", "--file", str(published), env=pr).stdout.strip() == key
    compared = hoglint(repo, "compare", "--base", "base", env=pr)
    assert says.format(key=key) in compared.stdout, compared.stdout + compared.stderr
    assert "code growth (fails) (1)" in compared.stdout
