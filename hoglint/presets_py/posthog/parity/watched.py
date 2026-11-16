"""Comparison 6: what the product lint asks a product to keep in its contract-check inputs, against
the paths hoglint's wiring rule asks for.

Only products with a `backend:contract-check` script are compared: for the others a change runs
the whole suite, and neither tool asks for anything. The product lint asks for locations (a file,
or a directory with a trailing slash), each for a reason it names: a wiring location, the model
surface, a permanent exposure, a carve-out, a channel module, routes, webhook consumers. hoglint
asks for files, each for the cover that needs it. A location is asked by both when hoglint asks
for a file inside it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from hoglint.presets_py.posthog.parity import old_tools
from hoglint.presets_py.posthog.parity.hoglint_check import HoglintFacts
from hoglint.presets_py.posthog.parity.tach_map import SourceNames
from hoglint.presets_py.posthog.parity.verdict import (
    HOGLINT_ONLY,
    OLD_ONLY,
    Comparison,
    Difference,
    Effect,
    Match,
    Reason,
)

TOOL = "watched-inputs"


def lint_only(name: str, text: str) -> Reason:
    return Reason(name, text, Effect.LESS)


# What the product lint asks for that hoglint's wiring rule has no cover for, by the lint's kind.
LINT_ONLY: dict[str, Reason] = {
    "model-surface": lint_only(
        "model-surface",
        "The product lint asks every narrowed product to watch its models and migrations, because a "
        "model is reachable without an import. hoglint's wiring rule has no cover for them; model use "
        "is judged by model-crossings, get-model and reverse-accessor instead.",
    ),
    "wiring-location": lint_only(
        "wiring-location-by-presence",
        "The product lint watches every present wiring location (tasks, temporal, max tools) by its "
        "presence, until the channel that reaches it is scanned. hoglint asks for a file there only "
        "when a test outside drives it, the request path reaches it, or it is a receiver or a hook "
        "callback.",
    ),
    "permanent-exposure": lint_only(
        "permanent-exposure",
        "A module tach declares as a permanent interface to core. hoglint's public-surface allows "
        "core to import it, but its wiring rule does not ask for it to be watched.",
    ),
    "carve-out": lint_only(
        "carve-out-module",
        "The module that defines a carve-out class the facade hands out. hoglint's wiring rule does not ask for it.",
    ),
    "routes": lint_only(
        "routes-module",
        "The product lint asks a narrowed product to watch its routes module, which core imports to "
        "build the API router. hoglint's wiring rule does not ask for it.",
    ),
    "webhook-consumers": lint_only(
        "webhook-consumers-module",
        "The product lint asks a product with webhook consumers to watch the module core loads by "
        "name. hoglint's wiring rule does not ask for it.",
    ),
    "channel:app-setup": lint_only(
        "app-setup-channel",
        "`backend/apps.py` and the product modules it imports: Django runs the app config in every "
        "process. The preset's request path does not follow INSTALLED_APPS, and no other cover "
        "asks for these files.",
    ),
    "channel:discovered-by-name": lint_only(
        "discovered-by-name-channel",
        "A module core loads by name through `load_product_modules(...)`. hoglint knows these as "
        "registry slots, but its wiring rule does not ask for them.",
    ),
}
# What hoglint asks for that the product lint does not, by the wiring rule's cover.
HOGLINT_ONLY_REASONS: dict[str, Reason] = {
    "rules.request-path.dst.path": Reason(
        "request-path",
        "A product module core's request pipeline reaches by module-level imports (D103, the owner, "
        "Nov 12). The product lint follows the paths settings name, not the imports behind them.",
        Effect.MORE,
    ),
    "rules.drives.dst.path": Reason(
        "driven-by-outside-test",
        "A wiring file a test outside the product executes, by hoglint's drives rule.",
        Effect.MORE,
    ),
}
# The two covers the preset writes as selections, by the start of their text.
RECEIVER_OR_HOOK = Reason(
    "receiver-or-hook-callback",
    "A receiver of another package's signal, or a callback installed into another package's hook, "
    "outside tests. hoglint's wiring rule finds it; the product lint's channel scan does not.",
    Effect.MORE,
)
PATCH_TARGET = Reason(
    "patch-target",
    "A module a test outside the product names by its dotted path, as a patch target does. "
    "hoglint's wiring rule finds it; the product lint's channel scan does not.",
    Effect.MORE,
)
COVER_STARTS = (("edge.kind in ['signal', 'installs']", RECEIVER_OR_HOOK), ("edge.kind == 'dotted-path'", PATCH_TARGET))


def hoglint_reason(why: str) -> Reason | None:
    if why in HOGLINT_ONLY_REASONS:
        return HOGLINT_ONLY_REASONS[why]
    for start, reason in COVER_STARTS:
        if why.startswith(start):
            return reason
    return None


ENTRY_MODULE = Reason(
    "entry-module-only",
    "The callback or receiver is defined here and installed or connected from another module of "
    "the product, which hoglint asks for. hoglint watches the module that installs it, not the "
    "module that defines it (hook callbacks: entry modules only; receivers are treated the same).",
    Effect.LESS,
    decided="the owner, Nov 9",
)
HOOK_NOT_LISTED = Reason(
    "hook-not-in-config",
    "The callback goes into a hook the root hoglint.toml does not list under `[django] hooks`, so "
    "hoglint records no install for it.",
    Effect.LESS,
)
SETTINGS_KEY = Reason(
    "settings-key-not-followed",
    "Settings name the module only under keys the preset's request path does not follow (app "
    "registration, task and shell imports, the API schema, logging and the like). The product lint "
    "watches every module settings name by dotted path.",
    Effect.LESS,
)
HELPER_CONNECT = Reason(
    "receiver-through-helper",
    "The receiver is connected through a helper function that forwards to `connect`. hoglint reads "
    "`@receiver` and `.connect(...)`, so it records no signal edge for it.",
    Effect.LESS,
)
UNRESOLVED_PATCH = Reason(
    "patch-string-unresolved",
    "The outside test names the module only through strings that hoglint does not resolve to "
    "first-party code: the string goes on into a third-party name the module imports, or names "
    "something the module lacks. hoglint keeps no first-party edge for such a string.",
    Effect.LESS,
)


def module_of_location(product: str, location: str) -> str:
    parts = f"products/{product}/{location}".removesuffix(".py").split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def channel_reason(
    product: str, requirement: dict, facts: HoglintFacts, read_source: Callable[[str], str]
) -> tuple[Reason, str] | None:
    """Why hoglint asks for nothing inside a channel module the product lint asks for, checked."""
    kind, source = requirement["kind"], requirement.get("source", "")
    module = module_of_location(product, requirement["location"])
    home = f"products/{product}/"
    if kind == "channel:settings-path":
        named = facts.settings_names.get(module, [])
        if named and not any(facts.follows_setting(setting) for _, setting in named):
            return SETTINGS_KEY, "; ".join(sorted({f"{path}: {setting}" for path, setting in named}))
        return None
    if kind == "channel:hook-callback":
        installs = [w for w in facts.installs if source in (w.surface, *w.targets)]
        entries = [w.path for w in installs if w.path.startswith(home) and w.path in facts.watched]
        if entries:
            return ENTRY_MODULE, f"installed from {', '.join(sorted(set(entries)))}"
        if not installs and source not in facts.hooks:
            return HOOK_NOT_LISTED, source
        return None
    if kind == "channel:foreign-receiver":
        connected = [w.path for w in facts.signals if w.callback.startswith(module + ".") and w.path in facts.watched]
        if connected:
            return ENTRY_MODULE, f"connected from {', '.join(sorted(set(connected)))}"
        foreign = [
            w
            for w in facts.signals
            if w.callback.startswith(module + ".")
            and any(facts.package_of_name(target) not in (None, product) for target in w.targets)
        ]
        if not source.startswith(("'", '"')) and not foreign:
            return HELPER_CONNECT, source
        return None
    if kind == "channel:outside-test":
        names = SourceNames(source, read_source(source))
        strings = [value for value, _ in names.strings if value == module or value.startswith(module + ".")]
        if strings and not any(facts.known(value) for value in strings):
            return UNRESOLVED_PATCH, f"{source}: " + ", ".join(sorted(set(strings)))
        return None
    return None


def location_path(product: str, location: str) -> str:
    return f"products/{product}/{location}"


def inside(path: str, product: str, location: str) -> bool:
    """Whether a file path is the location, or sits inside it. A module location without a suffix
    (a permanent exposure) is its file or its package."""
    full = location_path(product, location)
    if location.endswith("/"):
        return path.startswith(full)
    if location.endswith(".py"):
        return path == full
    return path == f"{full}.py" or path.startswith(f"{full}/")


def compare(requirements: dict[str, dict], facts: HoglintFacts, read_source: Callable[[str], str]) -> Comparison:
    comparison = Comparison(TOOL)
    by_product: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for path, (why, covered) in facts.watched.items():
        parts = path.split("/")
        if len(parts) > 2 and parts[0] == "products" and parts[1] in requirements:
            by_product[parts[1]].append((path, why, covered))
    for product in sorted(requirements):
        required = requirements[product]["required"]
        ours = by_product.get(product, [])
        comparison.hoglint_items += len(ours)
        for requirement in required:
            location, kind = requirement["location"], requirement["kind"]
            item = f"{product}: {location} ({kind})"
            comparison.old_items += 1
            asked = [path for path, _, _ in ours if inside(path, product, location)]
            if asked:
                comparison.matches.append(Match(item, ", ".join(sorted(asked))))
                continue
            found = channel_reason(product, requirement, facts, read_source) if kind.startswith("channel:") else None
            reason, evidence = found if found is not None else (LINT_ONLY.get(kind), requirement.get("source", ""))
            comparison.differences.append(Difference(item, OLD_ONLY, reason, evidence))
        for path, why, covered in sorted(ours):
            if any(inside(path, product, r["location"]) for r in required):
                continue
            state = "watched" if covered else "NOT watched"
            reason = hoglint_reason(why)
            comparison.differences.append(Difference(f"{path} ({why}, {state})", HOGLINT_ONLY, reason))
    unwatched = sorted(path for path, (_, covered) in facts.watched.items() if not covered)
    comparison.notes.append(f"hoglint finds {len(unwatched)} required paths unwatched: {', '.join(unwatched)}")
    return comparison


def run(root: Path, facts: HoglintFacts) -> Comparison:
    requirements = old_tools.driver(root, "watch-requirements")["products"]
    return compare(requirements, facts, lambda path: (root / path).read_text())
