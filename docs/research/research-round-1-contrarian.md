# Contrarian view: is one new boundary linter the right answer?

> A deliberately contrarian study for research round 1: the case against building one new boundary linter for the PostHog monorepo, and the alternatives, Nov 3, 2026.
> Partly replaced: the round decided to build the linter anyway (`docs/research/research-round-1.md`); several of its points (generation, a cached registry oracle, the skip question) shaped the design in `docs/design/`.

Short answer: no, not as a replacement for everything.
The five-tool spread is real, but most of the cost is in hand-synced config and duplicate rules, not in the tools.
A new linter inherits the hardest problem (Django couplings with no import edge) and solves none of it better than what exists.
The better move is to generate the config from one source, delete the duplicates, and put the Django-only checks behind one cached registry snapshot.

## What the rules are actually for

`products/architecture.md` is explicit: the isolation machinery exists so that skipping the full Django suite for an Isolated product is *sound* ("What makes the skip sound").
Most rules are proof obligations for that skip, not style.
That matters for the linter question, because the repo already measured how much of real test coupling an import graph can see.
A PostHog test-selection study, validated against testmon data, found that the import graph alone covers about 33% of real test dependencies; URL dispatch, signals, middleware, routers and same-app fallback make up much of the rest.
A boundary linter, in any language, on ruff or ty or tach, is an import-graph tool plus pattern matching.
It cannot see the channels that `architecture.md` already calls "declared residual" (instance attribute access, FK traversal on the consumer's own model, `get_model` with a variable label).

Rule families at the time:

- **R1 cross-product imports**: tach `[[interfaces]]` (42 blocks, one canonical alternation for about 23 Isolated products), `depends_on` (100 modules).
- **R2 intra-product layering**: five import-linter contracts (presentation → facade, routes → presentation, webhook consumers → facade, facade ↛ presentation/DRF, contracts ↛ Django), mirrored in `product:lint` because import-linter cannot see modules without `__init__.py`.
- **R3 non-import Django channels**: `model_crossing_uses_baseline.txt` (about 1,000 lines: 231 `reverse-accessor`, 110 `drives`, 98 `instance-many`, 13 `get_model`, …), the migration-dependency database invariant, the setup receivers baseline.
- **R4 facade shape**: `facade-accepts` (235), `facade-returns` (131), `facade-wiring` (62), `facade-logic` (7) in the same ledger.
- **R5 config consistency**: `validate_facade_alternation`, `validate_tach_references`, `isolation_baseline.txt` vs the tree, `backend:contract-check` inputs vs wiring locations.
- **R6 review-only**: management commands calling only the facade, approved-interface membership, "move the code, do not permit it".

## Speed is not the bottleneck people think

Measured on a warm checkout on a developer laptop:

- `ruff analyze graph products posthog ee common`: about 1.0 s wall.
- `tach check --dependencies --interfaces`: about 1.5 s.
- `lint-imports`: about 1.3 s (cached).
- `hogli product:lint --all`: about 8 s (the ledger growth step is skipped without the CI environment variable).

The import-graph half is already pre-commit fast.
The slow part is the subset that needs `django.setup()`: `reverse_accessors.py` spawns a subprocess for it, and the migration and receivers invariants run inside pytest.
A new linter does not make `django.setup()` cheaper; it either re-implements Django's model graph statically (fragile, see below) or calls setup too.

## Alternatives

### 1. Installable packages / uv workspace members per product

- **Catches**: R1 for real, since an undeclared dependency fails at import in an isolated venv. Partially R2, if internals live in a separate distribution.
- **Cost**: prohibitive at the time. From `tach.toml`: `posthog` depends on 74 products, `ee` on 35, and 84 of 95 product modules depend on `posthog`. 74 products sit in a direct two-cycle with core. Packages need an acyclic graph, so this is the whole isolation program, not a tool swap.
- **Risk**: the repo already hit the side effect. `turbo-discover.js` notes that tach resolves a uv distribution as third-party and records no edge, so `packages/owners-yaml` needs a hand-written consumer scan and its own import-linter "leaf" contract. Turning products into distributions blinds the tach map that drives selective CI.
- **Verdict**: the right end state for leaf libraries (`owners-yaml` shows the pattern works), the wrong tool for products until the core cycle is gone.

### 2. Import hooks / runtime guards in the test suite

- **Catches**: the R3 residuals no static tool can see: `apps.get_model` with a variable label, `import_module(f"products.{x}...")`, Celery and Temporal lookups by name, FK traversal on held instances. A `sys.meta_path` finder in the root `conftest.py` can record (the importer frame's product → an imported product's internal) and fail or ledger it. A patched `apps.get_model`, plus a hook on queryset evaluation, can attribute ORM hits to the calling product.
- **Cost**: moderate. It only covers executed paths; frame inspection on every import and every queryset adds test overhead (needs measuring, likely a few percent if limited to `products.*` targets). Run it in one nightly or shadow shard, not on every PR.
- **Risk**: coverage-shaped blind spots. Earlier CI-selection work found that testmon saw 1,020 tests "touching no production code", mostly falsely, because of mocks and property tests. The same holes apply.
- **Verdict**: the only option that shrinks the "declared residual". Worth it as an auditor that feeds the existing ledger, not as a gate.

### 3. Django-level enforcement

- **Catches**: R3 reverse accessors and cross-product FKs at the source. A system check (`django.core.checks.register`) over `apps.get_models()` can reject a cross-boundary relation without `related_name="+"`; it runs in `manage.py check`, at test startup, and in `runserver`. `ProductDBRouter.allow_relation` already refuses cross-database relations for the three routed apps; a `BoundaryRouter` could refuse relation assignment across product boundaries in tests. Django gives no hook to validate `apps.get_model` callers at boot, but registries (MaxTool, the Temporal worker, `load_product_modules`) already validate with `issubclass` at the door, as the doctrine requires.
- **Cost**: low. The reverse-accessor logic already exists in `reverse_accessors.py`; moving it into a system check removes the subprocess and the ledger round trip for new violations (the ledger stays for old ones).
- **Risk**: every check runs on every Django boot unless tagged (`tags=["boundary"]`, a `--deploy`-style opt-in). Runtime router checks change production behavior if not gated to `TEST`.
- **Verdict**: cheap, idiomatic, and it is where Django-shaped rules belong. Do this.

### 4. Type-checker enforcement

- **Catches**: R4 facade shape, in theory: a facade signature with a Django type is a typing fact. A mypy plugin hook (`get_function_signature_hook`) could flag `Model`, `QuerySet` or DRF types on public facade callables.
- **Cost**: mypy already runs with `mypy_django_plugin`, `mypy_drf_plugin` and `pydantic.mypy`, repo-wide only (subset runs are banned in the repo's agent instructions). A plugin is cheap to write, but it adds to the slowest check in the repo. ty has no plugin API, and `pyproject.toml` turns off most Django-related ty rules (`unresolved-attribute`, `invalid-base`, …), so ty cannot carry this yet.
- **Private modules**: renaming internals to `_internal/` and relying on ruff `PLC2701` (import-private-name, preview) is tempting, but as far as the researcher could tell, it exempts imports within the same top-level package, which is `products` for everything. Verify before relying on it. It would also churn every import path in 100 products.
- **Verdict**: no. `facade-accepts/returns` in the crossings scan already reads signatures with the AST; a type plugin buys precision on aliases at the cost of mypy runtime.

### 5. Extend existing tools instead of a new one

- **ruff**: `TID251 banned-api` with `per-file-ignores` could express R2 per product, but needs about 100 generated entries. `ruff analyze graph` gives a 1-second file graph that could replace `tach map` for `turbo-discover` (one fewer tool on the selection path). It has no interfaces concept, so it cannot replace `tach check`.
- **semgrep**: already the home of 30+ custom rules. Good for R3 literal shapes (`apps.get_model("<other_label>", ...)`, `ForeignKey("<other_label>.X")` without `related_name="+"`) if the label list is generated. Bad for anything that needs the module graph.
- **tach**: already models interfaces per module, utility modules and layers. Its gaps are known (the missing-`__init__.py` blind spot is import-linter's, not tach's). tach has a layers feature that could express "presentation above facade" inside a product if each product's `backend.presentation`/`backend.facade` became tach modules, removing most import-linter contracts. That is about 200 module blocks, which is only sane if generated.
- **Verdict**: extending is cheaper than replacing for every rule in R1 and R2. What hurts is that the config is hand-written.

### 6. Generate the boundary config from one source

- **Catches**: all of R5, by construction. `validate_facade_alternation`, `validate_tach_references`, `isolation_baseline.txt` agreement, and the `backend:contract-check` input checks exist only because humans keep five files in sync. 89 products already carry a `product.yaml`.
- **Shape**: add `isolation:` (rung), `exposes:` (exceptions, such as one product's exposed Temporal symbols), and `wiring:` to `product.yaml`. A `hogli product:sync` writes the tach `[[interfaces]]` and the canonical alternation, the import-linter contracts (per product, which also removes the wildcard `allow_indirect_imports` caveat), the semgrep label lists, and the `package.json` inputs. The lint becomes "generated files match", a check that `test_generated_files_are_registered.py` already knows how to do.
- **Cost**: medium, one-off. No new runtime, no new parser.
- **Risk**: a generator can hide decisions that show up as a reviewed diff in `tach.toml`. Keep the generated output checked in, so CODEOWNERS on the output still bites, and keep `MODEL_CROSSINGS` as the reviewed doctrine list.
- **Verdict**: the highest value per unit of effort. It deletes R5 as a rule family.

### 7. Do less

Rules that could be deleted or collapsed:

- **Duplicate R2 checks**: `product:lint` re-reads the presentation and routes rules because import-linter misses directories without `__init__.py`. Require the marker (`product:lint` already knows where it is needed) and delete the duplicate reader.
- **Core `depends_on` lists**: `posthog` lists 74 products and `ee` lists 35. A dependency list that names nearly everything carries almost no information; the interface pass is the real rule. Drop `--dependencies` for `posthog`/`ee` (or mark them as unchecked roots) and keep interfaces. Check first that nothing in selection reads `depends_on`; `tach map` is file-level and should not.
- **The facade ↛ DRF import-linter contract**: it checks direct imports only, while `facade-returns`/`facade-accepts` in the ledger already flag DRF types on the boundary. One of the two is enough.
- **A per-subtree invariant for one product**: a one-product special case. Either teach the lint per-subtree coverage or drop the narrowing.
- **Maturity report**: `maturity.py` (about 50 KB) is reporting, not enforcement. Keep it out of the "rules" count when deciding the tool's scope.

The deeper "do less" option: the soundness rules (R3 `drives`, wiring locations in the inputs, model surface in the inputs) exist because the skip decision is made from contract files.
If backend selection already runs through a more lenient test selector plus Django-aware heuristics, ask whether the Turbo contract-check skip still pays for its proof burden on about 23 Isolated products, or whether those products could be selected by the same verdict as everything else.
If the skip goes, roughly half of R3 becomes advisory design hygiene instead of a correctness gate.

## Recommendation

1. Do not build a general Django boundary linter. Its unique value (one config, one run) is reachable with generation, and its hard part (non-import couplings) is not solvable statically.
2. Make `product.yaml` the single source, and generate the tach and import-linter config, the semgrep label lists and the Turbo inputs from it. Ship this first; it removes most config formats from human hands.
3. Move the registry-shaped checks (reverse accessors, cross-product relations, migration database dependencies) into tagged Django system checks over one cached registry snapshot (`apps.get_models()` dumped to JSON once per commit in CI and on demand locally), so pre-commit reads the snapshot instead of calling `django.setup()`.
4. Delete the duplicates listed above.
5. Add a runtime auditor shard (an import hook plus a patched `get_model`) that feeds the existing crossings ledger, to shrink the declared residual over time.
6. Keep the crossings scanner as the home for Django-specific AST rules. It is effectively the "new linter" already; it just needs fewer neighbors.

## What would change this view

- A measurement showing the static set (tach + import-linter + crossings scan) misses a large share of real cross-product coupling that a new static tool *could* see, for example by diffing a runtime import/ORM audit against the existing ledger. If the miss is static-visible, a better static tool earns its keep.
- Editor-time demand: if engineers need boundary errors in the LSP (squiggles on the import), then a ruff or ty integration is the only way there, and generation alone does not deliver it.
- tach upstream stalling or refusing needed features (per-module layers at this scale, string-reference awareness). Then owning the tool becomes cheaper than working around it.
- The core cycle shrinking: if `posthog` → products drops from 74 to a handful, per-product distributions become feasible and make most of R1 and R2 structural, which beats any linter.
- Evidence that `django.setup()`-based checks cannot be made fast through a cached snapshot (for example, the snapshot goes stale in ways that cause false passes). Then a static re-implementation of the model graph would be justified despite its fragility.
