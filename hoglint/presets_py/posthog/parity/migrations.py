"""Comparison 7: the migration-database invariant test against hoglint's `migration-database` rule.

The old test boots Django and reads the migration loader's graph; nothing in the monorepo answers
it without Django. The driver runs the test's own reading (the loader with no connection, so no
database server is needed, and the router for each app's aliases). Three things are compared:

- the crossings the test fails on, against the rule's findings;
- every edge of the loader's graph (`run_before` and squashes resolved), against hoglint's
  `migration-parent` edges, which the Django pack builds without Django;
- each app's database aliases, against the databases hoglint gives the app's migrations.

An edge hoglint lacks is explained when the app's migrations live in a third-party package
hoglint does not read (`[discovery] external` names the packages it reads).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from hoglint.presets_py.posthog.parity import old_tools
from hoglint.presets_py.posthog.parity.hoglint_check import HoglintFacts
from hoglint.presets_py.posthog.parity.verdict import (
    HOGLINT_ONLY,
    OLD_ONLY,
    Comparison,
    Difference,
    Effect,
    Match,
    Reason,
)

TOOL = "migration-databases"
RULE = "migration-database"
UNREAD_PACKAGE = Reason(
    "unread-third-party-app",
    "The app's migrations sit in a third-party package hoglint does not read: hoglint reads the "
    "first-party tree and the packages `[discovery] external` names, so it has no migration of "
    "this app. A dependency of such an app on a routed app would not be judged.",
    Effect.LESS,
)


def app_of(key: str) -> str:
    return key.split(".", 1)[0]


def compare(old: dict, facts: HoglintFacts) -> Comparison:
    comparison = Comparison(TOOL)
    findings = {(row.identity[0], row.identity[1]) for row in facts.findings if row.rule == RULE}
    for crossing in old["crossings"]:
        comparison.old_items += 1
        child, parent = crossing.split(" -> ")
        if (child, parent) in findings:
            comparison.matches.append(Match(f"crossing {crossing}", RULE))
        else:
            comparison.differences.append(Difference(f"crossing {crossing}", OLD_ONLY))
    crossings = {tuple(c.split(" -> ")) for c in old["crossings"]}
    for child, parent in sorted(findings - crossings):
        comparison.differences.append(Difference(f"crossing {child} -> {parent}", HOGLINT_ONLY))

    ours = {(edge.child, edge.parent) for edge in facts.migration_edges}
    theirs = {(child, parent) for child, parent in old["edges"]}
    comparison.old_items += len(theirs)
    comparison.hoglint_items = len(ours)
    comparison.unlisted_matches = len(theirs & ours)
    read_apps = {app_of(key) for pair in ours for key in pair}
    modules = old.get("app_modules", {})
    for child, parent in sorted(theirs - ours):
        app = app_of(child)
        module = modules.get(app, "")
        reason = None
        if app not in read_apps and facts.module_of(module) is None:
            reason = UNREAD_PACKAGE
        comparison.differences.append(Difference(f"edge {child} -> {parent}", OLD_ONLY, reason, f"app module {module}"))
    for child, parent in sorted(ours - theirs):
        comparison.differences.append(Difference(f"edge {child} -> {parent}", HOGLINT_ONLY))

    databases: dict[str, set[str]] = defaultdict(set)
    for edge in facts.migration_edges:
        databases[app_of(edge.child)].update(edge.child_databases)
        databases[app_of(edge.parent)].update(edge.parent_databases)
    for app, aliases in sorted(old["aliases"].items()):
        if app not in databases:
            continue
        comparison.old_items += 1
        item = f"databases of {app}: {', '.join(aliases)}"
        if set(aliases) == databases[app]:
            comparison.unlisted_matches += 1
        else:
            comparison.differences.append(
                Difference(item, OLD_ONLY, None, f"hoglint: {', '.join(sorted(databases[app]))}")
            )
    comparison.notes.append(
        f"the old test fails on {len(old['crossings'])} crossings; the loader's graph has {len(theirs)} edges, "
        f"hoglint's {len(ours)}"
    )
    return comparison


def run(root: Path, facts: HoglintFacts) -> Comparison:
    comparison = compare(old_tools.driver(root, "migration-graph"), facts)
    comparison.notes.append(
        "no reader of the migration graph without Django exists in the monorepo; the driver boots Django "
        "with DJANGO_SETTINGS_MODULE=posthog.settings and TEST=1 and needs no database server"
    )
    return comparison
