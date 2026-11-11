# Is tach a good base to fork for the boundary linter?

> A study of tach (maintenance, architecture, a benchmark on the PostHog monorepo, and the cost of each wanted feature in a fork), written for research round 1, Nov 3, 2026.
> Partly replaced: the decision it fed (no fork, an own engine) is in `docs/history/decision-log.md`, and the engine is `docs/design/05-engine.md`; the round's synthesis is `docs/research/research-round-1.md`.

**Verdict: take ideas only. Keep running stock tach (pinned) until the new linter replaces it. Do not fork it and do not depend on it as a library.**
The parts of tach that PostHog actually uses (parallel per-file import extraction, a module tree, regex interfaces) are about 2k lines of Rust that are easy to rewrite on the ruff crates.
Every capability on the wish list (zones, symbol kinds, Django references, registries, one debt file) needs a whole-program index phase that tach's architecture does not have.

## 1. Maintenance status

- **The repo moved.** `gauge-sh/tach` now redirects to `tach-org/tach` (MIT, about 2.9k stars, not archived). Gauge stopped work around mid-2025: issue [#791 "Why is Tach no longer maintained?"](https://github.com/tach-org/tach/issues/791). Maintenance passed to a single volunteer maintainer in November 2025.
- **One maintainer, spare time.** `docs/contributing/making-changes.md` says: "Tach now only has one maintainer, and my time is split between several projects that I work on during my free time". The same file says "This project does not accept any AI generated code", and only collaborators can open PRs.
- **Releases keep coming, features do not.** v0.32.0 (2025-11-19) through v0.35.3 (2026-10-06), about one release a month. Recent work is mostly CI, Python versions, platform wheels and dependabot. In a shallow clone, `git log --since=2026-05-01 -- src` returns 2 commits (a syntax-error severity fix and a fix for a `cached` crate bump). Outside contributors' feature PRs get closed: `tach deadcode` (#944), `tach mcp` (#939), PEP 695 aliases (#930), three pytest-plugin fixes (#957-959). The repo has 53 open issues.
- **Ruff tracking is fine.** The ruff crates come in as git dependencies pinned to tag `0.16.7` (`Cargo.toml`), bumped by dependabot. Ruff upstream is at 0.17.3, so tach is one minor version behind. `ruff_linter` is pulled in only for `Locator` (`src/processors/file_module.rs`).
- **PostHog pin.** `tach~=0.34`, locked to 0.34.1, so the PostHog repo is one minor version behind upstream.

Bottom line: tach is safe to keep using as a black box.
It is not a project whose direction PostHog can influence.
A fork would never merge back.

## 2. Architecture (tach 0.35.3)

- **Languages.** About 12.8k lines of Rust in `src/` (a pyo3 `cdylib` + `lib`, shipped as `tach/extension.abi3.so`, 9.2 MB) plus about 4.5k lines of Python in `python/tach` (CLI, interactive `init` TUI, pytest plugin, report). The CLI starts in Python (`tach --version` takes 0.16 s), then calls into Rust.
- **Parser.** `ruff_python_parser` + `ruff_python_ast` visitors (`src/python/parsing.rs`). No semantic model: `ruff_python_semantic` is not a dependency.
- **Import graph.** No graph is built for `check`. `src/commands/check/check_internal.rs` walks the source roots (rayon `par_bridge`) and runs a `FileProcessor` (parse, extract imports) and then a `FileChecker` (dependency + interface rules) per file (`src/diagnostics/pipeline.rs`). Imports are normalized to absolute dotted strings (`src/processors/import.rs`). Each import is tagged `is_global_scope` (used by the severity of the `local_imports` rule). `TYPE_CHECKING` blocks are optionally skipped. "String imports" (any literal with ≥2 dots that resolves to a file) are optional too. Only cycle detection works on a graph, and it runs over the *declared* module config (`petgraph`), not over observed imports. `tach map` builds a reverse file map. PostHog's turbo cascade depends on it (`.github/scripts/turbo-discover.js`, `tach_map.py`).
- **Module resolution.** `ModuleTree::find_nearest` does a longest-prefix match of a dotted path against the configured modules. Module paths and `depends_on` entries can be globs (`src/resolvers/glob.rs`, `src/modules/build.rs`).
- **Config model** (`src/config/`). The units are modules (`depends_on`, `cannot_depend_on`, `layer`, `visibility`, `utility`, `unchecked`), interfaces (`expose` regexes on the member path, `from`, `visibility`, `exclusive`, `data_types = primitive`) and ordered `layers` with `closed`. `tach.domain.toml` files allow per-directory config with `//abs` and `<domain_root>` paths (`src/config/domain.rs`). Many config structs carry `#[pyclass]` (62 pyo3 attributes across 17 files), so the core types are tied to the Python binding.
- **Interfaces** (`src/checks/interface.rs`). The check is a string match on the imported path. It does not follow symbol identity or re-exports. `import products.x` followed by attribute access is `TopLevelModule` and always passes.
- **"Type" awareness.** `src/interfaces/data_types.rs` pre-parses interface files only, to check whether exposed members have primitive annotations. This is the one pre-pass in the codebase, and a useful precedent.
- **Django.** `src/processors/django/fkey.rs` matches `ForeignKey(...)` calls only (no `OneToOneField`, no `ManyToManyField`) whose first argument or `to=` is a string literal. It emits the raw string (`"app_label.Model"`) as if it were a module path. `get_known_apps` parses `INSTALLED_APPS` into `known_apps`, but nothing reads that field (`src/processors/dependency.rs`). No app label maps to a package, so `"user_interviews.UserInterview"` (label from `products/user_interviews/backend/apps.py`) cannot resolve to `products.user_interviews`. PostHog does not enable the plugin anyway (no `[plugins]` in `tach.toml`).
- **Caching.** `check` has none. `.tach/computation-cache` (`src/cache.rs`) serves only `tach test` (the pytest impact plugin). Every `check` re-parses the whole repo.
- **Suppression.** `# tach-ignore(reason)` directives, with `unused_ignore_directives` and `require_ignore_directive_reasons` rules (`src/checks/ignore_directive.rs`). There is no baseline file. In PostHog the `depends_on` lists are the de facto baseline.
- **Other features.** An external-dependencies check, `report`, `show --web` (uploads to Gauge's server), an LSP server (`src/lsp/`), `sync` and `init`. About half the code is unrelated to boundary checking.

## 3. Benchmark on PostHog (a developer laptop, 23.7k `.py` files, tach 0.34.1)

The invocation is the one PostHog's `hogli lint:tach` and CI use: two passes, `tach check --dependencies --exclude tests,test,**/test_*.py,**/*_test.py` and then `tach check --interfaces`.

| Run | Wall |
| --- | --- |
| dependencies pass, first run in the session | 1.40 s (132 MB RSS) |
| dependencies pass, 5 warm runs | 1.25-1.38 s |
| interfaces pass, 5 warm runs | 1.47-1.66 s |
| `tach check --dependencies --interfaces` (one pass) | 1.33 s |
| same, `RAYON_NUM_THREADS=1` / `=4` | 4.52 s / 1.86 s |
| `hogli lint:tach` (both passes + hogli startup) | 3.19 s |
| `tach map` (turbo cascade) | 2.50 s |

Cold and warm are the same from tach's side, because `check` keeps no state on disk.
The "first run" already showed only 324 page faults, so the files were in the OS page cache.
A true cold-cache number needs `sudo purge`, which this run did not do.
Takeaway: parse plus import extraction over the whole repo costs about 4.5 CPU-seconds, or about 1.3 s wall on all cores of a developer laptop.
That is the floor any full scan based on the ruff parser will hit.
A pre-commit or editor target needs incremental per-file results, which tach does not provide.

## 4. What each wanted feature would take in a fork

**(a) Zones from one template.** Partly expressible already. Glob modules (`products.*.backend.facade`) plus nested modules and `find_nearest` give per-zone units. But `depends_on` cannot say "my own product's `contracts`": there is no self or capture variable. Domain files have `<domain_root>`, but that needs one copy per product. A fork needs a `[[templates]]` section that expands per glob match with a captured product name. That means work in `config/project.rs`, `config/domain.rs` (reuse the `resolve` trait) and `modules/build.rs`. Interfaces would move from "per product" to "per zone", so the whole PostHog `tach.toml` (100 modules, 45 interfaces) gets rewritten. **Moderate, about 1 to 2 weeks.** This is the only item that fits tach's model.

**(b) Rules that depend on what a symbol is.** tach has no name binding, no class index and no cross-file pass. The pipeline is strictly per file (`FileProcessor` → `FileChecker`). "Is this a Django model" needs base-class chains through abstract bases and mixins in other modules. "Frozen dataclass" and "`@shared_task`" need decorator resolution through import aliases. The fork would need `ruff_python_semantic` per file, a global definition index built in a new pre-pass (like `TypeCheckCache`, but repo-wide), and a resolver. **A new subsystem; tach contributes nothing beyond the parser call.**

**(c) Django non-import references.** Extending `fkey.rs` to O2O/M2M, `apps.get_model(...)` and `swapper`-style settings is easy. The hard part is mapping app labels to packages. That needs `AppConfig.label` parsing from every `apps.py`, plus a model-name → defining-module index (back to the global pre-pass in (b)). PostHog has about 452 string FKs in `products/`, 17 O2O/M2M string references and about 390 non-migration `apps.get_model` calls. Reverse accessors need the model graph plus `related_name` analysis. PostHog's `hogli` read them from the live registry (`product/reverse_accessors.py`, which needs `django.setup()`). Static recovery is possible once (b) exists. **Medium on top of (b).**

**(d) Key-based registries** (Celery task names, QueryRunner dispatch by `kind`). This needs a define-then-use join: pass 1 collects the `name=`/`kind` keys → owning module, pass 2 checks the `send_task("…")` and dispatch sites. tach has no reduce phase between files. The `diagnostics.par_extend(...)` in `check_internal.rs` is map-only. **A pipeline redesign (two-phase), not an extension.**

**(e) One shrink-only debt file.** The hook point exists: `IgnoreDirectivePostProcessor` filters diagnostics after the checks. A fork would add stable fingerprints (rule + file + symbol/target, not line) on `CodeDiagnostic` (`src/diagnostics/diagnostics.rs`), a filter, and a "stale entry" error. **Small, about 3 to 5 days.** It would still not replace the `hogli`, semgrep and invariant-test baselines unless those rules move into the same engine.

## 5. Verdict

- **Fork it: no.** A fork would keep roughly half a codebase PostHog does not need (pytest plugin, external check, report, `show --web` to Gauge's server (its liveness is unchecked), TUI). Its core types are tied to pyo3. Items (b)-(d) need an index-then-check architecture that has to be written from scratch, and they are the reason a new linter is worth building. After that rewrite, what is left of tach is the file walker and the import normalizer.
- **Depend on it as a library: no.** It is not on crates.io (`crate tach does not exist`). The lib crate exposes no stable API, and depending on it via git drags in pyo3 and `ruff_linter`. The ruff crates themselves are now on crates.io (`ruff_python_parser` 0.0.20, published since 2026-06), so depend on those directly.
- **Take ideas only: yes.** Worth copying: the `FileProcessor`/`FileChecker` split with rayon fan-out, the longest-prefix module tree, glob modules, `<domain_root>`/`//` relative config, `is_global_scope` tagging with a separate local-import severity, ignore directives with required reasons and an unused-ignore rule, `TYPE_CHECKING` handling, and the reverse file map that `turbo-discover` consumes.
- **Meanwhile:** keep stock tach pinned for `lint:tach` and `tach map` until the new tool emits an equivalent map. The new tool must ship a `tach map`-compatible output, or the CI cascade loses its importer graph.
