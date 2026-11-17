"""What the hook (`check --index`) and CI (`compare`) print on the model repository, held to stored
golden outputs: a clean commit, one new finding, 64 findings in six rules, a failed claim, a new
unknown, a stale ledger line, an exception without a reason, and growth that a complete new
exception carries to review. The 64-finding hook output stays under a harness's cap, and what a
reader must see comes first. Rewrite the golden files with `HOGLINT_REFRESH_GENERATED=1` and read
the diff."""

from __future__ import annotations

import io
import os
import ast
import shutil
import datetime
import contextlib
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from hoglint.cli import Commands
from hoglint.ledger.listing import WorktreeStatus
from hoglint.report import CAP, View
from native.require import native_module

from model_repo.harness import TREE, copy_tree, stub_site

GOLDEN = Path(__file__).parent / "hook"
DAY = datetime.date(2026, 11, 17)
# The claims the model repository plants that fail Isolated: the base drops them, so its check is
# clean, and one scenario brings a claim back.
FAILING_CLAIMS = ("catalog", "gatekeeper", "inventory", "legacy", "reports")
# What the first 2,000 characters of a blocked hook output hold: a harness may show no more.
PREVIEW = 2000


def git(repo: Path, *args: str) -> None:
    command = ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*command, *args], check=True, capture_output=True)


def run(repo: Path, work: Path, action: Callable[[Commands], int]) -> str:
    """One command in this process, as the CLI runs it: its exit code and what it printed."""
    WorktreeStatus.taken.pop(repo.resolve(), None)
    work.mkdir(parents=True, exist_ok=True)
    commands = Commands(repo, work, today=DAY)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), commands.stores.as_current():
            code = action(commands)
    finally:
        commands.snapshots.close()
    return f"exit {code}\n{out.getvalue()}"


def hook(commands: Commands) -> int:
    return commands.check(index=True, write_ledger=False, changed=False, view=View("hoglint check --index"))


def compare(commands: Commands) -> int:
    return commands.compare("base", View("hoglint compare"))


def write(repo: Path, path: str, text: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)


ONE_FINDING = (
    "posthog/api/price_badge.py",
    "from products.catalog.backend.logic.pricing import price_for\n\n\ndef badge(entry_id: int) -> str:\n"
    "    return f'{price_for(entry_id)} items'\n",
)
EXCEPTION = """schema = 1

[[exception]]
rule  = "public-surface"
match = {{ "dst.symbol" = "products.catalog.backend.logic.pricing.price_for", "src.module" = "posthog.api.price_badge" }}
{reason}owners = ["team-devex"]
scope = "prod"
review_by = 2027-03-01
"""
# The product logic modules whose public names the 64-finding change imports past their facades.
LOGIC = (
    "catalog/backend/logic/pricing.py",
    "catalog/backend/logic/limits.py",
    "catalog/backend/logic/exporters.py",
    "catalog/backend/logic/refs.py",
    "legacy/backend/logic/widgets.py",
    "inventory/backend/logic/stock.py",
    "shelf/backend/logic/labels.py",
)


def public_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith("_")
    ]


def sixty(repo: Path) -> None:
    """A change in several files: core and a product's presentation reach past facades, a facade
    hands out a model, production imports test code, a product queries another's model."""
    imports = []
    for relative in LOGIC:
        module = "products." + relative.removesuffix(".py").replace("/", ".")
        imports += [f"from {module} import {name}" for name in public_names(repo / "products" / relative)]
    imports = imports[:34]
    names = ", ".join(line.rsplit(" ", 1)[1] for line in imports)
    write(repo, "posthog/api/bulk_report.py", "\n".join(imports) + f"\n\n\nNAMES = [{names}]\n")
    presentation = [line for line in imports if "catalog" in line or "legacy" in line][:10]
    shown = ", ".join(line.rsplit(" ", 1)[1] for line in presentation)
    write(
        repo,
        "products/reports/backend/presentation/bulk.py",
        "\n".join(presentation)
        + "\nfrom products.reports.backend.logic.build import build\nfrom products.reports.backend.models import ReportRun\n\n\n"
        + f"def bulk_view(request):\n    return [build, ReportRun, {shown}]\n",
    )
    write(
        repo,
        "products/reports/backend/logic/bulk_models.py",
        "from products.catalog.backend.facade.models import CatalogEntry\n\n\n"
        "def stale(team_id: int):\n    return CatalogEntry.objects.filter(team_id=team_id)\n\n\n"
        "def first(team_id: int):\n    return CatalogEntry.objects.get(team_id=team_id)\n\n\n"
        "def bump(entry_id: int) -> None:\n    CatalogEntry.objects.filter(pk=entry_id).update(title='x')\n",
    )
    write(
        repo,
        "products/reports/backend/logic/fixtures_in_prod.py",
        "from products.catalog.backend.tests.factories import *  # noqa: F403\n"
        "from products.reports.backend.tests.test_widget_query import *  # noqa: F403\n",
    )
    api = repo / "products/inventory/backend/facade/api.py"
    api.write_text(
        api.read_text() + "\n\nfrom products.inventory.backend.models import StockItem\n\n\n"
        "def raw_item(team_id: int, item_id: int) -> StockItem:\n    return stock.get_item(team_id, item_id)\n\n\n"
        "def raw_items(team_id: int, items: list[StockItem]) -> list[StockItem]:\n    return items\n\n\n"
        "def loose(team_id: int, payload) -> dict:\n    return {}\n"
    )


def clean(repo: Path) -> None:
    write(
        repo,
        "products/inventory/backend/logic/totals.py",
        "def total(items: list[int]) -> int:\n    return sum(items)\n",
    )


def one_finding(repo: Path) -> None:
    write(repo, *ONE_FINDING)


def claim_fails(repo: Path) -> None:
    shutil.copy(TREE / "products/inventory/turbo.json", repo / "products/inventory/turbo.json")


def blocking_incomplete(repo: Path) -> None:
    loader = "import importlib\n\n\ndef load(product: str):\n    return importlib.import_module(f'products.{product}.backend.facade.api')\n"
    write(repo, "posthog/api/plugin_loader.py", loader)


def stale_ledger_line(repo: Path) -> None:
    write(repo, "posthog/api/widget_report.py", "from products.legacy.backend.facade.api import *  # noqa: F403\n")


def exception_no_reason(repo: Path) -> None:
    write(repo, *ONE_FINDING)
    write(repo, "hoglint.exceptions.toml", EXCEPTION.format(reason=""))


def exception_complete(repo: Path) -> None:
    write(repo, *ONE_FINDING)
    reason = 'reason = "the badge needs the price until the facade has it"\n'
    write(repo, "hoglint.exceptions.toml", EXCEPTION.format(reason=reason))


SCENARIOS: dict[str, Callable[[Path], None]] = {
    "clean": clean,
    "one-finding": one_finding,
    "sixty-findings": sixty,
    "claim-fails": claim_fails,
    "blocking-incomplete": blocking_incomplete,
    "stale-ledger-line": stale_ledger_line,
    "exception-no-reason": exception_no_reason,
    "exception-complete": exception_complete,
}
# The scenarios whose compare output is held too.
COMPARED = ("sixty-findings", "exception-complete")


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    native_module()
    root = tmp_path_factory.mktemp("hook-output")
    repo = root / "base"
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(root / "cache"))
        copy_tree(repo)
        for product in FAILING_CLAIMS:
            (repo / "products" / product / "turbo.json").unlink()
        write(repo, "hoglint.exceptions.toml", "schema = 1\n")
        assert run(repo, root / "work", lambda c: c.check(False, True, False, True)).startswith("exit 0")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "base")
        git(repo, "branch", "base")
        yield repo


@pytest.fixture(scope="module")
def outputs(base: Path) -> dict[str, str]:
    """Each scenario's hook output, and the compare output of the compared ones."""
    found: dict[str, str] = {}
    with pytest.MonkeyPatch.context() as patch, stub_site():
        patch.setenv("XDG_CACHE_HOME", str(base.parent / "cache"))
        for name, change in SCENARIOS.items():
            repo = base.parent / name
            shutil.copytree(base, repo, symlinks=True)
            change(repo)
            git(repo, "add", "-A")
            found[f"{name}.hook"] = run(repo, base.parent / f"work-{name}", hook)
            if name in COMPARED:
                git(repo, "commit", "-qm", name)
                found[f"{name}.compare"] = run(repo, base.parent / f"work-{name}-ci", compare)
    return found


def test_the_hook_and_compare_print_their_golden_outputs(outputs: dict[str, str]) -> None:
    if os.environ.get("HOGLINT_REFRESH_GENERATED"):
        GOLDEN.mkdir(exist_ok=True)
        for name, text in outputs.items():
            (GOLDEN / f"{name}.txt").write_text(text)
    moved = [name for name, text in outputs.items() if (GOLDEN / f"{name}.txt").read_text() != text]
    assert moved == []


def test_a_large_change_leads_with_what_a_reader_must_see_and_stays_under_the_cap(outputs: dict[str, str]) -> None:
    printed = outputs["sixty-findings.hook"].split("\n", 1)[1]
    assert len(printed) <= CAP, len(printed)
    preview = printed[:PREVIEW]
    assert preview.startswith("hoglint: this commit is blocked: 64 new findings.")
    assert "New findings by rule:\n    40  public-surface\n" in preview
    assert "Do not edit hoglint.ledger, hoglint.exceptions.toml, hoglint.toml or a preset" in preview
    assert "  see: hoglint explain public-surface\n" in preview
    assert "hoglint check --index --all" in printed and "hoglint help hook" in printed
