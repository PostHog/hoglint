"""Drivers of the old tools, each run in its own process in the monorepo root by `old_tools`.

Each driver calls the old tool's own code and prints what it found as JSON. Nothing here judges:
the comparisons do. A driver that changes the old tool's config (the runs with the debt lists
taken out) changes it in memory or in a copy outside the tree, never the file in the tree.

The old tools live only in the monorepo's interpreter, so their modules are imported by name.

Usage: python -m hoglint.presets_py.posthog.parity.drivers <driver>
"""

from __future__ import annotations

import os
import sys
import copy
import json
import tempfile
import importlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
TACH_TEST_EXCLUDES = ["tests", "test", "**/test_*.py", "**/*_test.py"]
ISOLATION = "hogli_commands.product.isolation"


def old(module: str) -> Any:
    return importlib.import_module(module)


def tach_diagnostics(config_path: Path, dependencies: bool, interfaces: bool, excludes: list[str]) -> list[dict]:
    """The error lines of one `tach check` pass, as its CLI runs it with `--exclude`."""
    extension = old("tach.extension")
    config, _ = extension.parse_project_config(config_path)
    config.exclude = old("tach.parsing").combine_exclude_paths(excludes or None, config.exclude)
    out = []
    for diagnostic in extension.check(ROOT, config, dependencies=dependencies, interfaces=interfaces):
        if diagnostic.is_error():
            path = str(diagnostic.pyfile_path() or "")
            out.append({"path": path, "line": diagnostic.pyline_number(), "message": diagnostic.to_string()})
    return out


def tach_check() -> dict[str, Any]:
    """The two passes `hogli lint:tach` runs, then each pass with its debt taken out of a copy of
    tach.toml: every `depends_on` emptied, and every expose pattern that the product lint calls a
    legacy leak (not facade, presentation or routes, in a block without the permanent marker)
    removed from product interfaces."""
    tomlkit = old("tomlkit")
    isolation = old(ISOLATION)
    text = (ROOT / "tach.toml").read_text()
    runs: dict[str, Any] = {
        "ci-dependencies": tach_diagnostics(ROOT / "tach.toml", True, False, TACH_TEST_EXCLUDES),
        "ci-interfaces": tach_diagnostics(ROOT / "tach.toml", False, True, []),
    }
    scratch = Path(tempfile.mkdtemp(prefix="parity-tach-"))

    without_depends = tomlkit.parse(text)
    emptied = 0
    for module in without_depends["modules"]:
        if module.get("depends_on"):
            emptied += 1
            module["depends_on"] = []
    deps_path = scratch / "depends" / "tach.toml"
    deps_path.parent.mkdir()
    deps_path.write_text(tomlkit.dumps(without_depends))
    runs["exposed-dependencies"] = tach_diagnostics(deps_path, True, False, TACH_TEST_EXCLUDES)

    without_leaks = tomlkit.parse(text)
    blocks = list(isolation._iter_interface_blocks_raw(text))
    tables = without_leaks["interfaces"]
    if len(blocks) != len(tables):
        raise RuntimeError(f"the product lint reads {len(blocks)} interface blocks, tomlkit {len(tables)}")
    removed = []
    for (_, from_patterns, permanent), table in zip(blocks, tables):
        if permanent or not all(isolation.names_from_pattern(p) for p in from_patterns):
            continue
        patterns = [str(p) for p in table["expose"]]
        kept = [p for p in patterns if isolation.pattern_targets_public_surface(p)]
        removed += [f"{'|'.join(from_patterns)}: {p}" for p in patterns if p not in kept]
        table["expose"] = kept
    leaks_path = scratch / "interfaces" / "tach.toml"
    leaks_path.parent.mkdir()
    leaks_path.write_text(tomlkit.dumps(without_leaks))
    runs["exposed-interfaces"] = tach_diagnostics(leaks_path, False, True, [])
    return {"runs": runs, "emptied_depends_on": emptied, "removed_patterns": removed}


def import_linter() -> dict[str, Any]:
    """Each contract checked as configured and again with its `ignore_imports` taken out (in
    memory), with the first ignore entry that hides each violation."""
    old("importlinter.configuration").configure()
    use_cases = old("importlinter.application.use_cases")
    settings = old("importlinter.application.app_config").settings
    registry = old("importlinter.domain.contract").registry
    options = use_cases.read_user_options()
    use_cases._register_contract_types(options)
    session = options.session_options
    graph = settings.GRAPH_BUILDER.build(
        root_package_names=session["root_packages"],
        include_external_packages=use_cases._get_include_external_packages(options),
        exclude_type_checking_imports=use_cases._get_exclude_type_checking_imports(options),
        cache_dir=None,
    )
    contracts = []
    for contract_options in options.contracts_options:
        ignores = list(contract_options.get("ignore_imports") or [])
        kept = check_contract(registry, graph, session, contract_options)
        stripped = {key: value for key, value in contract_options.items() if key != "ignore_imports"}
        exposed = check_contract(registry, graph, session, stripped)
        hidden_by: dict[tuple[str, str], str] = {}
        for expression in ignores:
            for found in graph.find_matching_direct_imports(import_expression=expression):
                hidden_by.setdefault((found["importer"], found["imported"]), expression)
        violations = []
        for chain in contract_chains(exposed.metadata):
            hidden = [hidden_by.get((step["importer"], step["imported"])) for step in chain]
            violations.append({"chain": chain, "hidden_by": hidden})
        contracts.append(
            {
                "name": contract_options["name"],
                "kept_as_configured": kept.kept,
                "ignore_imports": ignores,
                "violations": violations,
            }
        )
    return {"contracts": contracts}


def check_contract(registry: Any, graph: Any, session: dict, contract_options: dict) -> Any:
    contract_class = registry.get_contract_class(contract_options["type"])
    contract = contract_class(name=contract_options["name"], session_options=session, contract_options=contract_options)
    return contract.check(copy.deepcopy(graph), verbose=False)


def contract_chains(metadata: dict) -> list[list[dict]]:
    """Every chain of a broken contract, each a list of {importer, imported, line_numbers} steps. An
    independence contract gives a route: its chain, and each further head or tail as a chain of
    its own."""
    out = []
    for data in metadata.get("invalid_chains", []):
        for chain in data.get("chains", []):
            if isinstance(chain, dict):
                out.append([link(step) for step in chain["chain"]])
                out += [[link(step)] for step in (*chain["extra_firsts"], *chain["extra_lasts"])]
            else:
                out.append([link(step) for step in chain])
    return out


def link(step: dict) -> dict:
    return {
        "importer": step["importer"],
        "imported": step["imported"],
        "line_numbers": [n for n in step.get("line_numbers") or () if n is not None],
    }


def statuses() -> Iterator[tuple[Path, Any]]:
    """Each backend product and its isolation status, computed through the lint's check context so
    the driven wiring locations are read as `hogli product:lint` reads them."""
    checks = old("hogli_commands.product.checks")
    paths = old("hogli_commands.product.paths")
    structure = paths.load_structure()
    for product_dir in paths.backend_product_dirs():
        backend = product_dir / "backend"
        context = checks.CheckContext(
            name=product_dir.name,
            product_dir=product_dir,
            backend_dir=backend,
            has_facade_contracts=checks.has_contracts_module(backend),
            structure=structure,
            detailed=False,
        )
        yield product_dir, context.isolation_status()


def product_lint() -> dict[str, Any]:
    """The product lint's rung of every backend product, and the parts of the structure it reads."""
    products = {}
    for product_dir, status in statuses():
        products[product_dir.name] = {
            "rung": str(status.rung),
            "isolation_enabled": status.isolation_enabled,
            "skip_withheld_reason": status.skip_withheld_reason,
            "has_facade_contracts": status.has_facade_contracts,
            "has_real_facade": status.has_real_facade,
            "has_tach_interface": status.has_tach_interface,
            "has_legacy_leaks": status.has_legacy_leaks,
            "bypass_entries": list(status.bypass_entries),
            "has_contract_check_script": status.has_contract_check_script,
            "has_narrowed_turbo": status.has_narrowed_turbo,
        }
    return {"products": products}


def watch_requirements() -> dict[str, Any]:
    """What the product lint asks a product with `backend:contract-check` to keep in its inputs,
    each as a product-relative location and the reason the lint names, read with the lint's own
    constants and helpers (`has_narrowed_turbo_inputs` and the coverage checks it gates)."""
    isolation = old(ISOLATION)
    products = {}
    for product_dir, status in statuses():
        if not status.has_contract_check_script:
            continue
        backend = product_dir / "backend"
        driven = set(status.driven_wiring_locations)
        required: list[dict[str, str]] = []
        for prefix in isolation.GARAGE_PREFIXES:
            if prefix in isolation.COMPUTED_WIRING_LOCATIONS and prefix not in driven:
                continue
            if (product_dir / prefix.rstrip("/")).exists():
                required.append({"location": prefix, "kind": "wiring-location"})
        for prefix in isolation.MODEL_SURFACE_PREFIXES:
            if prefix == "backend/migrations/" or (product_dir / prefix.rstrip("/")).exists():
                required.append({"location": prefix, "kind": "model-surface"})
        for module in status.permanent_exposures:
            required.append({"location": module.replace(".", "/"), "kind": "permanent-exposure"})
        for module in sorted(isolation.facade_carveout_modules(backend, product_dir.name)):
            required.append({"location": module, "kind": "carve-out"})
        for channel in status.channel_modules:
            required.append({"location": channel.path, "kind": f"channel:{channel.kind}", "source": channel.source})
        if (backend / "webhook_consumers.py").exists():
            required.append({"location": "backend/webhook_consumers.py", "kind": "webhook-consumers"})
        if isolation.has_routes_module(backend):
            required.append({"location": "backend/routes.py", "kind": "routes"})
        products[product_dir.name] = {"required": required}
    return {"products": products}


def migration_graph() -> dict[str, Any]:
    """What `test_migration_dependencies_share_a_database.py` reads: Django's migration loader graph
    (`run_before` edges and squashes resolved), the database aliases each app migrates on, and the
    crossings the test fails on. Boots Django with the test settings; the loader opens no
    connection, so no database server is needed."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posthog.settings")
    os.environ.setdefault("TEST", "1")
    old("django").setup()
    db = old("django.db")
    loader = old("django.db.migrations.loader").MigrationLoader(connection=None, ignore_no_migrations=True)
    graph = loader.graph
    apps = sorted({app for app, _ in graph.nodes})
    aliases = {app: sorted(alias for alias in db.connections if db.router.allow_migrate(alias, app)) for app in apps}
    edges = sorted(
        [f"{app}.{name}", f"{parent.key[0]}.{parent.key[1]}"]
        for (app, name), node in graph.node_map.items()
        for parent in node.parents
    )
    crossings = sorted(
        f"{child} -> {parent}"
        for child, parent in edges
        if parent.split(".")[0] != child.split(".")[0]
        and not set(aliases[child.split(".")[0]]) <= set(aliases[parent.split(".")[0]])
    )
    configs = old("django.apps").apps.get_app_configs()
    return {
        "aliases": aliases,
        "edges": edges,
        "crossings": crossings,
        "app_modules": {config.label: config.name for config in configs},
    }


def crossings() -> dict[str, Any]:
    """The crossings scanner's lines as it would write its baseline today."""
    scanner = old("hogli_commands.product.crossings")
    return {"lines": scanner.scanned_baseline_lines(scanner.all_crossing_uses())}


DRIVERS: dict[str, Callable[[], dict[str, Any]]] = {
    "tach-check": tach_check,
    "import-linter": import_linter,
    "product-lint": product_lint,
    "watch-requirements": watch_requirements,
    "crossings": crossings,
    "migration-graph": migration_graph,
}


def main() -> None:
    sys.stdout.write(json.dumps(DRIVERS[sys.argv[1]]()))


if __name__ == "__main__":
    main()
