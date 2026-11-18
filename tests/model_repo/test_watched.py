"""`hoglint watched` on the model repository, with the preset in the derived form (`derive.py`):
the Isolated level claimed in `product.yaml`, coverage rules with no inputs, the structural zones.
The `depot` product holds one case per part of the watched set, each next to a clean neighbor (CASES.md,
`WA` cases); `racks` holds an unknown. Other copies hold a claim that cannot be read, a failed producer,
and the preset as it is, whose turbo claims `watched` reads too."""

from __future__ import annotations

import io
import json
import contextlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hoglint.cli import Commands, Run
from hoglint.globs import Braces, compile_glob
from hoglint.ledger.listing import WorktreeStatus
from hoglint.packs.django.app_setup import AppSetupImports
from native.require import native_module

from model_repo.derive import derive_preset
from model_repo.harness import PRESET, PRESET_IN_TREE, copy_tree, stub_site

DEPOT = "products/depot"


def lay_derived(repo: Path) -> None:
    (repo / PRESET_IN_TREE).write_text(derive_preset(PRESET.read_text()))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)


def watched(repo: Path, work: Path) -> tuple[dict[str, Any], Commands]:
    """The answer of `hoglint watched` on `repo`, run in this process as the CLI runs it."""
    native_module()
    WorktreeStatus.taken.pop(repo.resolve(), None)
    work.mkdir(parents=True, exist_ok=True)
    commands = Commands(repo, work)
    out = io.StringIO()
    try:
        with stub_site(), contextlib.redirect_stdout(out), commands.stores.as_current():
            assert commands.watched(None, None, None) == 0
    finally:
        commands.snapshots.close()
    return json.loads(out.getvalue()), commands


@pytest.fixture(scope="module")
def derived(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], Path]:
    work = tmp_path_factory.mktemp("watched")
    repo = work / "repo"
    copy_tree(repo)
    lay_derived(repo)
    answer, _ = watched(repo, work / "run")
    return answer, repo


def depot_files(answer: dict[str, Any]) -> dict[str, str]:
    return {path.removeprefix(f"{DEPOT}/"): why for path, why in answer["groups"]["depot"]["watched"].items()}


def test_a_product_yaml_key_claims_the_level(derived: tuple[dict[str, Any], Path]) -> None:
    answer, _ = derived
    groups = answer["groups"]
    assert answer["complete"], answer["problems"]
    assert groups["depot"]["claimed"] == f"{DEPOT}/product.yaml"
    assert groups["depot"]["passes"], groups["depot"]["reasons"]
    assert groups["shelf"]["claimed"] == "products/shelf/product.yaml"
    # `isolated: false` claims nothing; the turbo.json that narrows inputs no longer claims.
    assert groups["racks"]["claimed"] is None
    assert groups["inventory"]["claimed"] is None


@pytest.mark.parametrize(
    ("path", "why"),
    [
        # WA01: the items of a list a registration point registers, through a list that is an item.
        ("backend/temporal/__init__.py", "core runs this location by name"),
        ("backend/temporal/restock.py", "this file defines the item"),
        ("backend/temporal/activities.py", "this file defines the item"),
        ("backend/temporal/audit/__init__.py", "this file defines the item"),
        ("backend/temporal/audit/workflow.py", "this file defines the item"),
        # WA02: what a hook callback runs, in the four forms a callable takes.
        ("backend/logic/restock_rules.py", "ready() defines"),
        ("backend/logic/audit_trail.py", "a hook callback"),
        ("backend/logic/transfer.py", "a hook callback"),
        ("backend/logic/wording.py", "a hook callback"),
        ("backend/logic/alias_target.py", "a hook callback"),  # the hook imported under another name
        # WA03: the zones, non-Python files too.
        ("backend/facade/README.md", "structural: zone facade"),
        ("backend/presentation/views.py", "structural: zone presentation"),
        ("backend/migrations/max_migration.txt", "structural: zone migrations"),
        # WA04: the packages above a watched file.
        ("__init__.py", "structural: the package above a watched file"),
        ("backend/logic/__init__.py", "structural: the package above a watched file"),
    ],
)
def test_each_part_watches_its_file(derived: tuple[dict[str, Any], Path], path: str, why: str) -> None:
    files = depot_files(derived[0])
    assert path in files
    assert why in files[path]


@pytest.mark.parametrize(
    "path",
    [
        "backend/temporal/restock_helpers.py",  # WA01: behind an item, not an item
        "backend/temporal/schedule.py",  # WA01: in the location, registered by no list
        "backend/logic/cold.py",  # WA02: imported where no hook gets the callable
        "backend/logic/README.md",  # WA03: in no watched zone
        "backend/tools/__init__.py",  # WA04: above no watched file
        "backend/tools/cold_tool.py",
    ],
)
def test_a_clean_neighbor_stays_out(derived: tuple[dict[str, Any], Path], path: str) -> None:
    assert path not in depot_files(derived[0])


LIST_MODULE_TREE = {
    # A list a module of the location builds from other modules' items, which the facade hands
    # out under another name.
    "backend/temporal/metadata_settings.py": (
        "from products.depot.backend.temporal.enrich import EnrichWorkflow, enrich_activity\n"
        "from products.depot.backend.temporal.stats import compute_stats\n\n"
        "WORKFLOWS = [EnrichWorkflow]\n"
        "ACTIVITIES = [enrich_activity, compute_stats]\n"
    ),
    "backend/temporal/enrich.py": (
        "class EnrichWorkflow:\n    def run(self, table):\n        return table\n\n\n"
        "def enrich_activity(table):\n    return table\n"
    ),
    "backend/temporal/stats.py": "def compute_stats(table):\n    return table\n",
    "backend/temporal/sweep.py": "class SweepWorkflow:\n    def run(self, bin_id):\n        return bin_id\n",
    # A list the facade builds itself.
    "backend/facade/temporal.py": (
        "from products.depot.backend.temporal import ACTIVITIES, WORKFLOWS\n"
        "from products.depot.backend.temporal.metadata_settings import (\n"
        "    ACTIVITIES as METADATA_ACTIVITIES,\n"
        "    WORKFLOWS as METADATA_WORKFLOWS,\n"
        ")\n"
        "from products.depot.backend.temporal.sweep import SweepWorkflow\n\n"
        "SWEEP_WORKFLOWS = [SweepWorkflow]\n\n"
        '__all__ = ["ACTIVITIES", "METADATA_ACTIVITIES", "METADATA_WORKFLOWS", "SWEEP_WORKFLOWS", "WORKFLOWS"]\n'
    ),
}
LIST_MODULE_CORE = (
    "\nfrom products.depot.backend.facade.temporal import (\n"
    "    METADATA_ACTIVITIES as DEPOT_METADATA_ACTIVITIES,\n"
    "    METADATA_WORKFLOWS as DEPOT_METADATA_WORKFLOWS,\n"
    "    SWEEP_WORKFLOWS as DEPOT_SWEEP_WORKFLOWS,\n"
    ")\n\n"
    "METADATA_QUEUES = [\n"
    '    ("depot-metadata", DEPOT_METADATA_WORKFLOWS + DEPOT_SWEEP_WORKFLOWS, DEPOT_METADATA_ACTIVITIES),\n'
    "]\n"
)


def test_the_module_that_builds_a_registered_list_is_watched(
    derived: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    """Core registers lists that a module builds from other modules' items: the list's module is
    watched next to the items' modules. A list the facade builds adds only its items' modules, as
    the facade is watched already."""
    repo = tmp_path / "repo"
    copy_tree(repo)
    for path, text in LIST_MODULE_TREE.items():
        (repo / DEPOT / path).write_text(text)
    command = repo / "posthog/management/commands/start_temporal_worker.py"
    command.write_text(command.read_text() + LIST_MODULE_CORE)
    lay_derived(repo)
    answer, _ = watched(repo, tmp_path / "run")
    assert answer["complete"], answer["problems"]
    assert answer["groups"]["depot"]["passes"], answer["groups"]["depot"]["reasons"]
    files = depot_files(answer)
    added = set(files) - set(depot_files(derived[0]))
    names = ["metadata_settings.py", "enrich.py", "stats.py", "sweep.py"]
    assert added == {f"backend/temporal/{name}" for name in names}
    assert all("core registers an item of a list" in files[path] for path in added)
    assert "backend/facade/temporal.py" in files


DECLARATION_TREE = {
    "posthog/temporal/common/registration.py": (
        "class QueueBinding:\n"
        "    def __init__(self, queue_setting, workflows=(), activities=()):\n"
        "        self.queue_setting = queue_setting\n\n\n"
        "class TemporalRegistration:\n"
        "    def __init__(self, queues=(), schedules=(), log_sources=()):\n"
        "        self.queues = queues\n"
    ),
    "products/shelf/backend/facade/temporal.py": (
        "from posthog.temporal.common.registration import QueueBinding, TemporalRegistration\n\n"
        "from products.shelf.backend.temporal import ACTIVITIES, WORKFLOWS\n"
        "from products.shelf.backend.temporal.logs import resolve_log_source\n"
        "from products.shelf.backend.temporal.schedule import reconcile_restock_schedule\n\n"
        "TEMPORAL = TemporalRegistration(\n"
        '    queues=(QueueBinding(queue_setting="SHELF_TASK_QUEUE", workflows=WORKFLOWS, activities=ACTIVITIES),),\n'
        "    schedules=(reconcile_restock_schedule,),\n"
        "    log_sources=(resolve_log_source,),\n"
        ")\n"
    ),
    "products/shelf/backend/temporal/__init__.py": (
        "from products.shelf.backend.temporal.restock import RestockShelfWorkflow, restock_activity\n\n"
        "WORKFLOWS = (RestockShelfWorkflow,)\n"
        "ACTIVITIES = (restock_activity,)\n"
    ),
    "products/shelf/backend/temporal/restock.py": (
        "from products.shelf.backend.temporal.restock_helpers import batch\n\n\n"
        "class RestockShelfWorkflow:\n    def run(self, shelf_id):\n        return batch(shelf_id)\n\n\n"
        "def restock_activity(shelf_id):\n    return shelf_id\n"
    ),
    "products/shelf/backend/temporal/restock_helpers.py": "def batch(shelf_id):\n    return [shelf_id]\n",
    "products/shelf/backend/temporal/schedule.py": "async def reconcile_restock_schedule(client):\n    return client\n",
    "products/shelf/backend/temporal/logs.py": "def resolve_log_source(workflow_type, workflow_id):\n    return None\n",
    # A declaration bound by an import is built where no loader reads it as one.
    "products/racks/backend/temporal/registration.py": (
        "from posthog.temporal.common.registration import TemporalRegistration\n\nTEMPORAL = TemporalRegistration()\n"
    ),
    "products/racks/backend/facade/temporal.py": (
        "from products.racks.backend.temporal import WORKFLOWS\n"
        "from products.racks.backend.temporal.registration import TEMPORAL\n\n"
        '__all__ = ["TEMPORAL", "WORKFLOWS"]\n'
    ),
}


def test_a_declaration_a_registry_loads_is_read_like_a_registered_list(tmp_path: Path) -> None:
    """A registry's loader reads `TEMPORAL` in each facade: the modules that define the workflows,
    activities and callbacks the declaration names are watched, with the module that builds the
    list they come from; core's record classes and a helper behind an item are not. A declaration
    bound by an import is unread."""
    repo = tmp_path / "repo"
    copy_tree(repo)
    for path, text in DECLARATION_TREE.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    lay_derived(repo)
    answer, _ = watched(repo, tmp_path / "run")
    assert answer["complete"], answer["problems"]
    shelf = answer["groups"]["shelf"]
    assert shelf["passes"], shelf["reasons"]
    registered = {path for path, why in shelf["watched"].items() if "core loads a declaration" in why}
    temporal = "products/shelf/backend/temporal"
    assert registered == {
        f"{temporal}/restock.py",
        f"{temporal}/schedule.py",
        f"{temporal}/logs.py",
    }
    # The facade and the list's module are watched by the registry's and the location's covers first.
    assert {"products/shelf/backend/facade/temporal.py", f"{temporal}/__init__.py"} <= set(shelf["watched"])
    assert f"{temporal}/restock_helpers.py" not in shelf["watched"]
    unread = "products.racks.backend.facade.temporal.TEMPORAL"
    assert any(unread in reason for reason in answer["groups"]["racks"]["reasons"])


def test_an_unread_list_item_is_the_lists_products_unknown(derived: tuple[dict[str, Any], Path]) -> None:
    racks = derived[0]["groups"]["racks"]
    assert not racks["passes"]
    assert any("cannot read every item" in reason for reason in racks["reasons"])


def test_a_hook_declares_which_arguments_hand_it_a_callable(derived: tuple[dict[str, Any], Path]) -> None:
    """WA07: a hook whose table declares no callable argument takes a local URL as data, so `depot`
    passes; a declared callable argument that is a parameter cannot be told, so `racks` cannot."""
    groups = derived[0]["groups"]
    assert groups["depot"]["passes"], groups["depot"]["reasons"]
    unknowns = [reason for reason in groups["racks"]["reasons"] if "cannot place" in reason]
    assert any("register_restock_hook" in reason for reason in unknowns), groups["racks"]["reasons"]


def test_the_same_tree_gives_the_same_bytes(derived: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    answer, repo = derived
    again, _ = watched(repo, tmp_path / "again")
    assert json.dumps(again, sort_keys=True) == json.dumps(answer, sort_keys=True)


def plain_reading(repo: Path, run: Run) -> set[str]:
    """Every product's watched files read the plain way: the paths the level's coverage rules
    need (a location by its `__init__.py`, else the modules directly inside it), every tracked file
    of a product under a watched zone's paths, and each package `__init__.py` above one of those in
    the same product."""
    config, modules = run.config, set(run.graph.modules.values())
    level = config.levels["isolated"]
    found: set[str] = set()
    for rule_id in level.requires:
        for path in run.result.runs[rule_id].required:
            if not path.endswith("/"):
                found.add(path)
            elif f"{path}__init__.py" in modules:
                found.add(f"{path}__init__.py")
            else:
                found.update(m for m in modules if m.startswith(path) and "/" not in m[len(path) :])
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True)
    zones = config.zones.by_name()
    globs = [compile_glob(glob, Braces.ALTERNATION) for zone in level.watch_zones for glob in zones[zone].paths]
    for path in tracked.stdout.splitlines():
        parts = path.split("/")
        if parts[0] == "products" and len(parts) > 2 and any(g.match("/".join(parts[2:])) for g in globs):
            found.add(path)
    products = {path for path in found if path.startswith("products/") and path.count("/") > 1}
    for path in sorted(products):
        product = "/".join(path.split("/")[:2])
        directory = path.rsplit("/", 1)[0]
        while directory.startswith(product):
            if f"{directory}/__init__.py" in modules:
                products.add(f"{directory}/__init__.py")
            directory = directory.rsplit("/", 1)[0]
    return products


def test_watched_is_the_required_paths_the_zones_the_entries_and_the_packages_above(
    derived: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    """The guard on the derivation: the sets equal a plain reading of their parts."""
    answer, repo = derived
    WorktreeStatus.taken.pop(repo.resolve(), None)
    (tmp_path / "parts").mkdir()
    commands = Commands(repo, tmp_path / "parts")
    try:
        with stub_site(), commands.stores.as_current():
            run = commands.runner.run(repo)
    finally:
        commands.snapshots.close()
    found = {path for group in answer["groups"].values() for path in group["watched"]}
    assert found == plain_reading(repo, run)


def test_a_claim_that_does_not_read_makes_the_answer_incomplete(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    copy_tree(repo)
    (repo / DEPOT / "product.yaml").write_text("name: depot\nisolated: maybe\n")
    lay_derived(repo)
    answer, _ = watched(repo, tmp_path / "run")
    assert not answer["complete"]
    assert any("is not true or false" in problem for problem in answer["problems"])


def test_a_failed_producer_makes_the_answer_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: AppSetupImports) -> list:
        raise RuntimeError("planted")

    monkeypatch.setattr(AppSetupImports, "produce", fail)
    repo = tmp_path / "repo"
    copy_tree(repo)
    lay_derived(repo)
    answer, _ = watched(repo, tmp_path / "run")
    assert not answer["complete"]
    assert any("planted" in problem for problem in answer["problems"])


def test_turbo_claims_and_inputs_read_the_same_way(tmp_path: Path) -> None:
    """The preset as it is: the narrowed turbo.json claims, and the coverage rules with inputs
    record the same paths."""
    repo = tmp_path / "repo"
    copy_tree(repo)
    answer, _ = watched(repo, tmp_path / "run")
    assert answer["groups"]["shelf"]["claimed"] == "products/shelf/turbo.json"
    assert "products/shelf/backend/logic/count.py" in answer["groups"]["shelf"]["watched"]


def test_a_failed_key_claim_has_no_input_list_to_add_to(tmp_path: Path) -> None:
    """The claim texts follow the adapter: a claim read from a key names no list to add a file to."""
    repo = tmp_path / "repo"
    copy_tree(repo)
    (repo / "products/racks/product.yaml").write_text("name: racks\nisolated: true\n")
    lay_derived(repo)
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "-C", str(repo), *identity, "commit", "-q", "-m", "tree"], check=True)
    WorktreeStatus.taken.pop(repo.resolve(), None)
    (tmp_path / "run").mkdir()
    commands = Commands(repo, tmp_path / "run")
    out = io.StringIO()
    try:
        with stub_site(), contextlib.redirect_stdout(out), commands.stores.as_current():
            assert commands.check(False, False, False) == 1
    finally:
        commands.snapshots.close()
    text = out.getvalue()
    assert "racks claims isolated in products/racks/product.yaml" in text
    assert "To remove the claim in products/racks/product.yaml, stop and tell the person." in text
    assert "add the file to the watched inputs" not in text
