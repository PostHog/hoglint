# Prior art: module boundary enforcement and what went wrong

> A survey of boundary tools (Packwerk, Sentry silo mode, import-linter and grimp, ruff and ty, Bazel, Nx, ArchUnit, Django-aware tools) and their failures, written for research round 1, Nov 3, 2026.
> Partly replaced: a deeper second prior-art round is `docs/research/prior-art.md`; the round's synthesis is `docs/research/research-round-1.md`.

Evidence tags: [src] fetched or seen in search results; [mem] from the researcher's memory and not verified here.
Gaps are named at the end.

## Shopify Packwerk (and Gusto)

Packwerk enforces constant references between packages (dependency checks), plus privacy checks until v3.0.
It is static, needs no app boot, and uses Rails autoload name resolution.
Existing violations go into `package_todo.yml` files ("declare bankruptcy": block new, allow old).
Retrospective: https://railsatscale.com/2024-01-26-a-packwerk-retrospective/ (same text: https://shopify.engineering/a-packwerk-retrospective) [src]

What went wrong:

- Privacy checks forced an `app/public` directory. Developers copied directory trees to fake a public surface. Public code was undocumented and never meant to be public. Packwerk turned into "an API design tool". Privacy was removed in 3.0, and the debt is still not paid off.
- Todo files grew with every feature and became "monstrously large". Stale entries were not removed when a violation was fixed. The patch for that sat unnoticed for years, which suggests few users ever finished a todo file. The flag rate outran the fix capacity.
- Blind spots. Anything not seen as a constant reference (require, autoload, routes, fixtures, initializers, anything outside `app/`) is invisible. A package with zero violations still failed at runtime with name errors.
- Layout choice. Packwerk accepts any grouping, so teams grouped by domain label, not by runtime function. Fixes then added indirection (heavy inversion of control) with no isolation gain.
- Real isolation of the base "Platform" package took many months. Booting it alone and running its tests in isolation in CI found problems that static analysis missed.
- Their conclusion: measure progress with running code, not static metrics. Treat the tool as "a sharp knife". They have discussed dropping it.

Gusto moved most of a large codebase into packages within about six months after the 2020 release, and built extra tooling on top.
Details: https://medium.com/gusto-engineering/the-current-state-of-gustos-modularity-tooling-c99f345fd0a7 (the page returned 403, so only the search summary was available; they say they empathize with the privacy problems) [src, thin].

## Sentry silo mode (hybrid cloud, Django monolith)

Silo mode enforces which silo (CONTROL, REGION/CELL, MONOLITH) a model, endpoint, or function may run in.
It is runtime, not static.
Docs: https://develop.sentry.dev/application/architecture/ [src]. Code: `src/sentry/silo/base.py` (https://github.com/getsentry/sentry/blob/master/src/sentry/silo/base.py) [src].

- Decorators (`control_silo_function`, `cell_silo_function`, `all_silo_function`, plus model decorators) wrap callables. The check runs at call time against `SILO_MODE`.
- It raises `AvailabilityError` only when `in_test_environment()` is true. In production the wrapper just calls through. So the guard is a test-time tripwire, with zero production risk and zero production cost.
- MONOLITH mode allows everything, so the dev and default path is never blocked.
- Cross-silo foreign keys use `HybridCloudForeignKey`: the FK target lives in the other silo and has no DB constraint [src, via documentation pages].
- Cost: coverage depends on tests actually executing the path. Every model and function needs a decorator. No static check was found in this file [src]. If one exists elsewhere, it was not verified.
- The useful idea: Django coupling that imports cannot show (FKs, querysets) is made explicit by a declared annotation on the model, and then checked.

## Kraken Technologies and import-linter

import-linter enforces layers and independence contracts on the import graph.
It is static, built on grimp.
Talk summary: https://blog.europython.eu/kraken-technologies-how-we-organize-our-very-large-pythonmonolith/ [src]

- About 27,600 modules, over 40 contracts, run in CI on every PR. Teams add their own layer contracts deep inside their area.
- Adoption: `ignore_imports` lists existing violations per contract. The count of ignored imports is the debt metric, plotted as a burndown. Fixes sometimes needed inversion of control, which adds local complexity. 15 stubborn imports remain, "the most tangled".
- Their point: talking about layering was not enough, so a check was needed.
- The summary does not say whether the talk mentions Django, caching or speed. Nothing reported broke.
- An independent guide on Django: import contracts say nothing about FK coupling, and an FK from A to B's model blocks extracting B: https://platform-django-template.readthedocs.io/en/latest/4-guides/module-boundary-enforcement.html [src]

## grimp (import-linter's engine) and its Rust rewrite

Changelog: https://github.com/python-grimp/grimp/blob/main/CHANGELOG.rst [src]

- Caching arrived in 2.3 (hash-named cache files later). Parallel scanning was tried with multiprocessing, then joblib.
- 3.3 moved layers checking to Rust. 3.6 rewrote the graph in Rust, mainly for speed, with a note that "some operations may be slower". 3.9 moved import parsing to Rust. 3.11 moved scanning to Rust multithreading.
- 3.10 was yanked for poor performance on very large graphs.
- An open PR experiment, "Replace ast with rust" (python-grimp/grimp#213), showed a mixed CodSpeed result, about 15% slower on one large-graph benchmark, before the later parser work [src, thin].
- Takeaway: a Rust graph is not automatically faster. Regressions showed up at scale and need benchmarks on a big graph. A podcast claim of "about 6x" is unverified.
- The changelog has no entries on string imports or dynamic imports. Lazy imports got explicit support in 3.16-3.17.

## ruff `analyze graph` and ty

- `ruff analyze graph` is an experimental subcommand that outputs file-level dependency or dependent graphs. It has `--direction`, a `type_checking_imports` setting and `include-dependencies` in `[tool.ruff.import-map]`. https://docs.rs/ruff_graph/0.0.5/ruff_graph/ [src]. It maps files to files. It does not enforce rules.
- No ruff or ty roadmap item or issue for import boundaries, visibility or module rules was found [src: searches empty]. Astral's ty post names future "semantic capabilities across the toolchain" (dead code, unused dependencies, type-aware linting): https://astral.sh/blog/ty [src]. ty is beta and targets stable in 2026, with Django as a named library goal [src, secondary].
- Ruff has TID (flake8-tidy-imports) banned-api rules, which are per-import patterns, not graph rules [mem].
- Implication: no upstream owner is building this. Building on ruff means depending on an experimental, unversioned crate API.

## Instagram, Dropbox

- Dropbox "Atlas": breaking a Python monolith of roughly 3M lines into a managed platform. Reported lesson: composition matters early, and the work was slow. https://dropbox.tech/infrastructure/atlas--our-journey-from-a-python-monolith-to-a-managed-platform [src, secondary]. No boundary tool from them was found.
- Instagram: no source found. From memory only: Instagram built Cinder and lazy imports for startup time, and Pysa and Fixit for static analysis [mem]. No module-boundary tool was found. Treat this as a gap.

## Bazel, Buck, Pants, Nx, ArchUnit, Go, Rust

- Bazel visibility: declared per target, enforced by the build, scoped narrowly by guidance, with `package_group` for shared lists. Docs: https://bazel.build/concepts/visibility [src]. Failure: Gazelle-generated per-target visibility overrode package defaults and broke builds across a monorepo (https://github.com/bazelbuild/rules_python/issues/1682) [src]. The docs warn against default public visibility in large codebases. Lesson: generated config and hand-written config fight each other. Visibility is strong because the dependency graph is declared in the build, so nothing is implicit. Buck2 and Pants specifics were not verified. Pants infers dependencies from imports [mem].
- Nx `enforce-module-boundaries` (an ESLint rule with tag-based `depConstraints`): reported 3 minutes on one file in a 1,000-file project (https://github.com/nrwl/nx/issues/9076, lightrun mirror) and a cache-reread regression after 13.8.3. A benchmark claims Oxlint plus the rule takes 14.9 s against 2.7 s without it [src, secondary]. Lesson: the rule re-derives the project graph per file unless cached. A linter must reuse one graph for all files.
- ArchUnit `FreezingArchRule`: a violation store for legacy code. Pain points: stale freeze files after rules are renamed or removed (issue https://github.com/TNG/ArchUnit/issues/1264, a user with 70+ files); the rule description is the store key, so editing it orphans the store; an early bug let counts of known violations grow silently. Flink's guidance: fix before freezing, pin the description with `.as()`, make new store creation opt-in. https://apache.googlesource.com/flink/+show/refs/heads/master/flink-architecture-tests/README.md [src]
- Go `internal/` and Rust `pub(crate)`: enforced by the compiler, by a path rule or by item visibility. They never needed a debt list because they were there from day one [mem]. This works because there is one resolution mechanism.

## Django-aware static tools

- The django-stubs mypy plugin needs the settings module and boots Django, so a broken settings import crashes mypy. A relative string reference (`'ModelA'`) once made the plugin return None (https://github.com/typeddjango/django-stubs/issues/196) [src, old]. pylint-django has an open RFC for a checker on string FK targets (https://github.com/pylint-dev/pylint-django/issues/243) [src]. Pyrefly documents plugin-free Django typing (https://pyrefly.org/en/docs/django/) [src]. No tool was found that resolves `"app.Model"` strings into graph edges without the app registry. This is a gap and an opening.
- tach (Rust, config in `tach.toml`, interfaces): lint-only, no runtime effect [src]. No tach issue on string references was found. A fork had appeared because upstream maintenance was in doubt, so check upstream activity (see `docs/research/tach-fork.md`).

## Ideas worth stealing

1. Debt as a ratcheting count, not a copied list: Kraken tracks ignored imports as a burndown metric, and ArchUnit fails if counts of known violations rise. Make the baseline auto-prune: fail when an entry no longer matches (Packwerk and ArchUnit both rotted here).
2. Declare Django edges as data, not imports. Sentry's per-model annotation shows the model file can carry the declaration. Add a pass that reads `ForeignKey("app.Model")`, `apps.get_model("app", "Model")`, receiver senders and migration `dependencies` via the AST and turns the string into an edge. Resolve app labels from a static label-to-package map built from `apps.py` and `INSTALLED_APPS`, not from `django.setup()`.
3. One graph, built once, reused by every rule (the Nx failure in reverse). Cache per file by content hash, as grimp does, so pre-commit re-parses only changed files and unions them with the cached graph.
4. Two tiers like Sentry: a fast static tier for pre-commit and the editor, and a test-time runtime tripwire for what static analysis cannot see (registered tasks, dynamic lookups). The runtime check can be off in production.
5. Drive rules from one declaration of publicness (Bazel visibility, Go `internal/`): the default is private, a package lists what it exposes, and generated config cannot override it.
6. Print the fix with each violation. Packwerk's gap was "no feedback on how to resolve". Say "import from `products.x.facade` instead" or "add to contract Y".
7. Benchmark on the real repo graph in CI (the grimp 3.10 yank, the 15% slower parser PR). Gate on wall time.
8. Add a "runs in isolation" check beside the static one (Shopify): import each product's package alone in a clean process. This catches initializers, settings and autodiscovery that static analysis misses.

## Traps to avoid

1. Privacy and publicness as a directory convention (Packwerk `app/public`): it was gamed with duplicate trees and has not been repaid years later. Derive publicness from explicit exports, not folder names.
2. A baseline that only grows. Todo files were "monstrously large" and not removed on fix. Require prune-on-fix, a size cap per area, and owners per entry.
3. Zero violations treated as proof of isolation. Shopify shipped "clean" packages that crashed at runtime. Keep the isolation check, and list the blind spots (string references, signals, Celery or Temporal by name, URL includes, settings) in the tool output.
4. Booting Django for the core path. django-stubs crashes when settings fail to import, and `django.setup()` kills pre-commit speed. Keep it optional, in an opt-in slow tier.
5. Per-file graph rebuilds (Nx) and untested Rust rewrites (the grimp 3.6 and 3.10 regressions). Profile on the real tree before claiming speed.
6. Two sources of truth (Bazel Gazelle against package defaults). If config is generated from code (owners, `product.yaml`), it must not be hand-overridable in a second place.
7. Building on experimental upstream APIs (`ruff analyze graph`, ty). No boundary roadmap exists upstream, and the crate API is unstable. Vendor the parser (`ruff_python_parser`) or pin it tightly.
8. Layout-agnostic rules. Packwerk accepted any grouping, so bad groupings got a lot of indirection. Encode which tiers exist (core, product, isolation level) so the tool rejects shapes, and do not only check edges.
9. Decorating everything for runtime checks (Sentry style) across about 100 products creates annotation sprawl. Annotate only at crossing points (models and facades).

## Gaps

No Instagram source.
The Gusto article was blocked.
No Sentry static checks were verified.
No Buck2 or Pants specifics.
No ruff or ty issue on boundaries was found (searches only, not the issue tracker itself).
Performance numbers for import-linter at Kraken are not published.
