"""Comparison 6: what the product lint asks a product to keep in its contract-check inputs, against
the paths hoglint's wiring rule asks for.

Only products with a `backend:contract-check` script are compared: for the others a change runs
the whole suite, and neither tool asks for anything. The product lint asks for locations (a file,
or a directory with a trailing slash), each for a reason it names: a wiring location, the model
surface, a permanent exposure, a carve-out, a channel module, routes, webhook consumers. hoglint
asks for files, each for the cover that needs it, and for the wiring locations core runs by name,
each a directory one watched file in it watches. A location is asked by both when hoglint asks for
a file or a directory inside it.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from hoglint.packs.posthog.parity import old_tools
from hoglint.packs.posthog.parity.hoglint_check import HoglintFacts
from hoglint.packs.posthog.parity.tach_map import SourceNames
from hoglint.packs.posthog.parity.verdict import HOGLINT_ONLY, OLD_ONLY, Comparison, Difference, Effect, Match, Reason

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
SETTINGS_NAMED = Reason(
    "settings-named-module",
    "A product module a settings module names by dotted path. The product lint scans only "
    "`posthog/settings/`; hoglint reads every module the preset marks as settings.",
    Effect.MORE,
)
REGISTRY_SLOT = Reason(
    "registry-slot",
    "A file in a registry slot the preset declares, which the product lint does not find as a "
    "`load_product_modules` name (a slot another loader reads).",
    Effect.MORE,
)
NAMED_WIRING = Reason(
    "wiring-run-by-name",
    "A wiring location core runs by name that the product lint's list leaves out (a `max_tools/` "
    "directory; the lint lists `max_tools.py` only).",
    Effect.MORE,
)
MODEL_FILE = Reason(
    "model-surface-file",
    "A file of the models or migrations zone outside the locations the product lint names.",
    Effect.MORE,
)
PERMANENT_FILE = Reason(
    "permanent-interface-file",
    "A file the preset declares a permanent interface that tach's marked blocks do not list.",
    Effect.MORE,
)
APP_CONFIG_IMPORT = Reason(
    "app-config-import",
    "A module the AppConfig module imports at setup (at module level or in the body of `ready()`) "
    "that the product lint does not ask for.",
    Effect.MORE,
)
# The wiring rule's covers that the preset writes as selections, by the start of their text. A
# longer start comes before a shorter one it begins with.
COVER_STARTS = (
    ("edge.kind in ['signal', 'installs']", RECEIVER_OR_HOOK),
    ("edge.kind == 'dotted-path' and src.settings == true", SETTINGS_NAMED),
    ("edge.kind == 'dotted-path'", PATCH_TARGET),
    ("edge.kind == 'import' and src.app_config == true", APP_CONFIG_IMPORT),
    ("zone in ['models', 'migrations']", MODEL_FILE),
    ("registry != none", REGISTRY_SLOT),
    ("permanent == true", PERMANENT_FILE),
    ("runs_by_name == true", NAMED_WIRING),
)


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
NAMES_NOTHING = Reason(
    "string-names-nothing",
    "The outside test's strings name no name the module defines or imports (a task name in an "
    "assertion, a fake module name), so they reach none of its code. The product lint matches the "
    "string's module prefix; no coupling stands behind it.",
    Effect.SAME,
)
ABSENT_LOCATION = Reason(
    "location-absent",
    "The product lint asks for the location although the product has no file there yet. hoglint "
    "asks for each file once it exists, in the change that adds it.",
    Effect.SAME,
)
APP_SETUP_IN_FUNCTION = Reason(
    "app-setup-import-in-function",
    "`backend/apps.py` imports the module only inside a function that does not run at setup (a "
    "callback `ready()` defines or hands on, or another function). The product lint reads every "
    "import in the file; hoglint's AppConfig cover reads the imports at module level and in the "
    "body of `ready()`, and a receiver or hook such a module connects is its own entry module.",
    Effect.LESS,
    decided="the architect, Nov 16 (imports at module level and in the body of `ready()`)",
)


def module_of_location(product: str, location: str) -> str:
    parts = f"products/{product}/{location}".removesuffix(".py").split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def is_type_checking(node: ast.AST) -> bool:
    """`if TYPE_CHECKING:`: its body never runs."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def imported_only_in_callbacks(apps_source: str, module: str, package: str) -> bool:
    """Whether every import of `module` (or a name of it) in an AppConfig module of `package`
    that runs sits inside a function that does not run at setup, and there is at least one. The
    module body, a class body and the body of a module-level class's `ready()` run at setup. An
    import under `if TYPE_CHECKING:` never runs."""
    tree = ast.parse(apps_source)
    later: list[bool] = []

    def names(node: ast.Import | ast.ImportFrom) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        base = node.module or ""
        if node.level:
            parent = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
            base = f"{parent}.{base}" if base else parent
        return [base, *(f"{base}.{alias.name}" for alias in node.names)]

    def runs_later(parent: ast.AST, child: ast.AST, in_callback: bool) -> bool:
        if in_callback or not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return in_callback
        ready = child.name == "ready" and isinstance(parent, ast.ClassDef) and parent in tree.body
        return not ready

    def visit(node: ast.AST, in_callback: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if is_type_checking(child):
                assert isinstance(child, ast.If)
                for branch in child.orelse:
                    visit(branch, in_callback)
                continue
            if isinstance(child, (ast.Import, ast.ImportFrom)) and module in names(child):
                later.append(in_callback)
            visit(child, runs_later(node, child, in_callback))

    visit(tree, False)
    return bool(later) and all(later)


def names_nothing(value: str, module: str, module_source: str) -> bool:
    """Whether a dotted string goes on from `module` into a name the module neither defines nor
    imports, so it reaches none of its code."""
    rest = value.removeprefix(module + ".")
    if rest == value or not rest:
        return False
    first = rest.split(".", 1)[0]
    tree = ast.parse(module_source)
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return first not in bound


def channel_reason(
    product: str, requirement: dict, facts: HoglintFacts, read_source: Callable[[str], str]
) -> tuple[Reason, str] | None:
    """Why hoglint asks for nothing inside a channel module the product lint asks for, checked."""
    kind, source = requirement["kind"], requirement.get("source", "")
    module = module_of_location(product, requirement["location"])
    home = f"products/{product}/"
    if kind == "channel:app-setup":
        apps = f"{home}backend/apps.py"
        if imported_only_in_callbacks(read_source(apps), module, f"products.{product}.backend"):
            return APP_SETUP_IN_FUNCTION, f"{apps} imports {module} only inside a function that runs later"
        return None
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
        if not strings or any(facts.known(value) for value in strings):
            return None
        evidence = f"{source}: " + ", ".join(sorted(set(strings)))
        module_source = read_source(location_path(product, requirement["location"]))
        if all(names_nothing(value, module, module_source) for value in strings):
            return NAMES_NOTHING, evidence
        return UNRESOLVED_PATCH, evidence
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


def compare(
    requirements: dict[str, dict],
    facts: HoglintFacts,
    read_source: Callable[[str], str],
    any_file: Callable[[str], bool] = lambda directory: True,
) -> Comparison:
    """`any_file` says whether a directory of the tree holds a file."""
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
            if found is None and location.endswith("/") and not any_file(location_path(product, location)):
                found = ABSENT_LOCATION, f"no file under {location_path(product, location)}"
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

    def any_file(directory: str) -> bool:
        return (root / directory).is_dir() and any(path.is_file() for path in (root / directory).rglob("*"))

    return compare(requirements, facts, lambda path: (root / path).read_text(), any_file)
