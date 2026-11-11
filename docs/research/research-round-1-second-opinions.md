# Research round 1: second opinions from other models

> Four second opinions from other models during research round 1 and the v0 data model: an independent linter proposal (Nov 3, 2026), a review of the v0 data model, advice on where the config lives, and a mapping of a separate linter survey onto the model (all Nov 4, 2026).
> Partly replaced: the review's fixes are in `docs/research/data-model-2.md` section 15; later second-opinion rounds are in `docs/research/prior-art-second-opinion.md`, `docs/research/depends-on-second-opinion.md`, `docs/research/exports-and-shapes-second-opinion.md` and `docs/research/second-opinion-*.md`.

The architect asked another model for independent views at four points.
Each section starts with the question in one paragraph, then gives the answer, cleaned of local paths and file-and-line references.
The architect checked each claim against the PostHog code before using it.

## 1. An independent linter proposal (Nov 3)

**Question.** The second opinion got the same brief as the other researchers (`docs/research/research-round-1.md`) and was asked to propose the linter it would build, concretely and with opinions: the core model (entities, relations, rule kinds) with a short config example for two or three real PostHog rules; how to extract Django coupling that is not an import, and whether `django.setup()` can be avoided; the engine (language, what to reuse, speed strategy); how known violations are recorded and ratcheted; whether forking tach is a sensible base; and what to leave out, with the top three risks. The limit was about 1,500 words.

**Answer.**

Build a **Rust boundary checker with a typed dependency graph, Django-specific extractors, and a separate runtime verification mode**.
One policy model should replace the overlapping boundary checks; replacing every invariant test and semgrep rule would make the project unmanageable.

The key lesson from the architecture doctrine (`products/architecture.md`) is that three claims need separate evidence: "this import is permitted", "this public API exposes permitted objects", and "this product's changes can safely skip outside tests".
Passing the first does not establish the others.

*1. Model and configuration.*
The graph should contain:

- **Owners:** products, core packages, independently installable packages.
- **Entities:** modules, symbols, models, fields, registrations, migrations and test entrypoints.
- **Surfaces:** facade, contracts, presentation, wiring and internal implementation.
- **Typed relations:** imports, re-exports, signature references, inheritance, model relations, reverse descriptors, registry lookups, registrations, dispatches and migration dependencies.

Every relation carries a source location, an extraction method and a resolution status: resolved, finite candidate set, or unknown.
Model ownership follows the defining module, not Django's app label; the existing reverse-accessor checker already needs this distinction for legacy labels.

Rules should support dependency allowlists, consumer-specific public surfaces, direct versus transitive restrictions, API-shape constraints, relation constraints and required test-input coverage.
Avoid a general-purpose query language at first.

Illustrative proposed TOML:

```toml
version = 1

[owners.products]
discover = "products/*"
module = "products.{name}"
surfaces.facade = "backend.facade.**"
surfaces.presentation = "backend.presentation.**"

[[rules]]
id = "presentation-through-facade"
kind = "imports"
source = "product:{p}/presentation"
target = "product:*/backend"
allow = ["product:{p}/facade", "product:{p}/presentation"]
reach = "direct"

[[rules]]
id = "contracts-without-django"
kind = "imports"
source_modules = [
  "products.*.backend.facade.contracts",
  "products.*.backend.facade.enums",
]
deny_modules = ["django.**"]
reach = "transitive"

[[rules]]
id = "sealed-reverse-relations"
kind = "django.relation"
when = "cross-owner-and-involves-product"
require = { related_name = "+", related_query_name = "absent" }
```

`{p}` binds an owner consistently; `*` ranges across owners.
Module selectors include descendants.
These express existing rules from `pyproject.toml` and the doctrine, with the existing exceptions migrated separately.

Presentation may call a facade that imports implementation: a transitive prohibition there would be wrong.
Contract purity, however, requires a transitive restriction.
That distinction must be explicit.

Surface permissions must also distinguish consumers.
A routing entrypoint's permission to import views must not expose those views to every product.

*2. Django coupling without imports.*
Use static extraction first, without importing application code:

- Resolve `AppConfig.name`, `label`, `Meta.app_label`, model inheritance and aliases.
- Extract FK/O2O/M2M targets from symbols and strings. Calculate reverse descriptors and query names, including abstract-base substitutions, default related names, proxy targets and through models.
- Resolve literal and constant-propagated `apps.get_model` calls. Distinguish the historical registry in migrations from the live registry.
- Extract signal senders and receivers, Celery task declarations and named dispatch, Temporal registrations and named execution, URL includes, dotted settings references and app lists.
- Read migration `dependencies`, `run_before`, replacements and swappable dependencies as their own graph.

Use bounded constant evaluation: strings, assignments, containers and simple concatenation.
Never execute arbitrary Python to improve resolution.
Framework adapters recognize qualified symbols, not merely functions named `get_model`.

The existing reverse-accessor implementation highlights an essential edge case: `related_name="+"` does not seal a relation when an explicit `related_query_name` remains.

**Can this eliminate `django.setup()`? From the editor path, yes; from complete verification, no.**
Dynamic model factories, metaclasses, custom fields, routers and conditional registration defeat complete static reconstruction.

Keep a small Python exporter that runs during an already initialized CI test process and compares the actual registry with the static predictions.
Store its observations with settings, dependency and source fingerprints.
Stale observations cannot certify changed code; observed registrations cannot prove absence under other configurations.

Unknown references remain visible.
For isolation certification, relevant unknowns block certification or retain conservative test coverage.
They must never silently become "no dependency".

*3. Engine and speed.*
Use Rust, reusing Ruff's parser and AST behind a narrow internal adapter.
Pin the revision: Ruff describes its parser as an internal component, so do not assume API stability ([Ruff parser manifest](https://github.com/astral-sh/ruff/blob/main/crates/ruff_python_parser/Cargo.toml)).

Build a modest symbol resolver covering imports, re-exports, aliases, constant bindings, annotations and class ancestry.
Do not build a complete Python type checker.
Do not make ty or grimp foundational dependencies, and do not use `ruff analyze graph` output as the authoritative representation: the checker needs field-, symbol- and registration-level evidence.

The speed strategy:

- Parse files in parallel once; persist compact extracted facts keyed by content hash and extractor version.
- Maintain indexes for symbols, model labels, registration names and reverse dependencies.
- Re-resolve affected consumers when exports, constants, inheritance or registry entries change.
- Invalidate resolution on source-root, package-layout, settings and policy changes.
- Share one analysis library between the CLI and an optional LSP process with unsaved-buffer overlays.

Diff mode changes the **recomputation set**, not the correctness scope.
Editing an exported symbol must reconsider unchanged consumers.
Pre-commit should analyze the staged snapshot, not accidentally combine staged callers with unstaged providers.

Set measurable targets (under 200 ms for a typical warm editor edit and under one second for a warm small commit), but treat these as acceptance criteria, not predictions.
Benchmark extraction, resolution and invalidation independently.

*4. Debt and adoption.*
Use one generated debt ledger, separate from policy.
Each finding records:

`rule + source symbol + target + relation kind + structural occurrence fingerprint`

Add owner, rationale and introduction metadata.
Avoid line-number identities and aggregate counts alone: deleting one violation must not purchase permission for a different violation.

Enforce both directions:

- New findings fail.
- Removed findings must leave the ledger.
- Updating the ledger can only remove entries by default.
- CI compares against the trusted base ledger, so hand-editing cannot authorize growth.

Renames require an explicit, reviewable identity mapping.
New extractor versions may introduce existing-but-newly-detected debt through a dedicated migration, not an unrestricted baseline refresh.

Adopt in shadow mode against the current tools.
Classify disagreements before retiring each check.
Convert broad legacy exposure patterns into exact consumer exceptions where possible.
Existing baselines remain acceptance fixtures during the migration.

Debt exemptions permit continued development; they do not automatically confer eligibility for selective testing.

*5. Fork tach?*
**For a prototype, yes; as the permanent foundation, probably not.**

tach is a credible starting point: its Rust implementation already depends on Ruff parsing, graph, caching, parallelism and LSP libraries.
This is not a comparison against an inherently slow or editor-unfriendly tool ([tach manifest](https://github.com/tach-org/tach/blob/main/Cargo.toml)).

The mismatch is the central abstraction.
This proposal needs heterogeneous entities, evidence provenance, unresolved dynamic references, API-shape analysis and runtime reconciliation.
Extending import edges with more and more elaborate special cases risks reproducing the existing fragmentation inside one executable.

Time-box a tach-based spike that implements reverse relations and consumer-scoped exports.
Keep the fork only if these become first-class facts and policies without distorting import semantics.
Otherwise reuse suitable MIT-licensed components and test cases in a separate engine.

*6. Deliberate exclusions and three risks.*
Leave out general security and style linting, arbitrary SQL dependency inference, whole-program runtime type proof, automatic architecture refactoring, and claims that a function contains "no business logic".
Keep runtime registration validation.
Expose dependency evidence to test selection, but do not equate an empty graph with safe test skipping: HTTP-driven tests remain a documented counterexample.

The top three risks:

1. **False confidence from incomplete extraction.** Counter it with explicit unknowns, runtime reconciliation and conservative certification.
2. **Reimplementing Django incorrectly.** Use version adapters and differential-test them against real registries, especially inheritance and reverse relations.
3. **Incorrect incremental invalidation.** Compare warm incremental results with cold full scans in CI, including deletions, renames, configuration changes and unresolved references that become resolvable.

## 2. A review of the v0 data model (Nov 4)

**Question.** The second opinion read the draft data model (v0) and the fit test of 54 rules, and grounded itself in the doctrine docs, `tach.toml`, the import-linter contracts, the `product:lint` code (`crossings.py`, `checks.py`, `reverse_accessors.py`), the crossings baseline, the repo invariant tests and `turbo-discover.js`. It was asked to find what is wrong, not what is fine (few crucial findings over many small ones), each with the section, the concrete repo case that breaks it and the smallest change. Focus: rules the model cannot express or expresses with different semantics; missing or wrong edge kinds, contexts or projections; whether re-deriving the crossings ledger would give the same lines; incremental and invalidation holes; and anything that makes the `apps.get_models()` oracle insufficient. The limit was about 1,200 words.

**Answer.** Seven findings. All seven held when the architect checked them against the code, and section 15 of `docs/research/data-model-2.md` records the resulting changes.

1. **Section 9: the growth gate changes the existing policy in both directions.**
   The draft's "per line, never totals" would count a moved or split consumer module as growth, where an aggregated count does not. Conversely, an owner/reason admission or an arbitrary policy change permits growth in the draft; the existing PR gate admits growth only when it comes with a reviewed scanner change.

   **Smallest change:** separate exact scan/ledger equality from growth accounting. Give each rule a growth aggregation key and an authorization policy; preserve `(crossing, detailed_kind)` aggregation and the scanner-change gate for crossings.

2. **Sections 3-4, 8, 11: the records cannot reproduce the ledger's detailed identities and counts.**
   The ledger distinguishes `lock`, `passed-as-class(cast)`, `write(...)`, `instance-many(...)`, and other parameterized kinds absent from the proposed shape vocabulary. These are not cosmetic: `classify_use()` makes locking and writes override a scalar terminal, so `select_for_update().count()` must not become an allowed `scalar-chain`.

   Identity also varies by channel. Reverse rows name `owner.Model.field`; facade acceptance rows include the parameter; `facade-logic` names a module location and counts bodies. An existing `<product>:backend/facade/<file>.py … facade-logic n` line cannot be derived from declaration annotations or a generic `module-symbol` identity. Rule 26 also contradicts "rules never see L1" by consuming declarations directly.

   **Smallest change:** keep a structured classification with detail fields and explicit precedence; introduce declaration and body facts that a projection can reach; define channel-specific identity and count projections rather than calling all crossings `module-symbol`.

3. **Sections 4-5, rule 48: migration dependencies have different graph and database semantics.**
   The repo invariant `test_migration_dependencies_share_a_database.py` checks the **resolved `MigrationLoader` graph**, including reversed `run_before` edges, and requires `aliases(child) ⊆ aliases(parent)`. It does not compare one database string for equality. The repository has migrations with `run_before` and widespread squash `replaces` declarations. Neither is represented.

   **Smallest change:** add migration identities and resolved parent edges, keeping declaration provenance; project migration alias sets per app from router and settings behavior and apply subset semantics. Validate this graph separately: `apps.get_models()` cannot check migration topology or routing.

4. **Section 6 and rules 10-13: the zones view discards edges that the layer contracts enforce.**
   `zones` contains only same-package edges, but the import-linter config explicitly forbids facade imports of the external `rest_framework`, and existing deferred edges include a facade importing `rest_framework`. That edge disappears before rule 13 can inspect it. The wildcard presentation and routes contracts also cover backend targets across products, not just the source product.

   **Smallest change:** keep cross-package and external destinations in the layer view, or evaluate these rules on `refs`. Specify direct-import semantics and exact allowlists independently of package ownership.

5. **Sections 3-4, rule 42: a registry-key edge is not enough for `drives(...)`, and its invalidation dependencies are missing.**
   `module_drives()` in `crossings.py` counts module imports such as `import …hogql_queries.web_overview as m` without any registry string. Query-kind drives additionally require execution evidence: constructing a query for formatting alone does not count; execution through imported helpers can count; patched helpers suppress it. `_CallGraph` reads helper bodies across modules.

   Changing a helper body to call a dispatcher can therefore change an unchanged test's drive rows **without changing exports or inventory**, which leaves the draft's L2 cache wrongly valid. "All targets" also needs aggregation: unresolved drives count once per reachable wiring location, not once per candidate symbol.

   **Smallest change:** add derived drive edges with trigger, execution evidence and location target; keep the necessary call, mock and scope facts; track transitive producer dependencies on bodies, dispatch tables and registry membership.

6. **Sections 4-5, rules 43 and 50: reverse relations need more information than accessor names and model labels.**
   `reverse_accessor_edges()` in `reverse_accessors.py` distinguishes defining-module ownership from Django labels: `access_control.Role` keeps the legacy `ee` label. It also canonicalizes proxy targets, excludes inherited concrete-parent fields, and keeps `related_name="+"` relations when an explicit query name still permits traversal. The draft's "unless `related_name="+"`" is wrong.

   Separately, the FeatureFlag classification checks **all reverse relations**, including same-product ones, and rejects stale classifications. Rule 50's string-target and inherited-relation selection misses direct class targets and expresses only missing classifications.

   **Smallest change:** model effective relations with declaring model, concrete target, owner, hidden/accessor/query names and creation flags. Compare these in the runtime oracle, not merely labels and accessor names; make the classification equality two-way over the full relation inventory.

7. **Section 6 and rule 49: import reachability does not reproduce the health-check invariant.**
   The repo invariant `test_dagster_package_isolation.py` boots Django, imports the processing module, **calls `ensure_registry_loaded()`**, then inspects `sys.modules`. That function dynamically imports the strings in `HEALTH_CHECK_MODULES`. A product health-check module can pull in `posthog.dags` without any static import path from the processing module. Rule 49 selects only `import` edges, and excludes even the draft's available `dotted-path` edges.

   **Smallest change:** keep this runtime probe as a separate oracle or plugin. A static approximation needs an activation-aware view that contains registry loading and dynamic imports; neither the file-import view nor the model-registry oracle establishes the current invariant.

## 3. Where the config lives (Nov 4)

**Question.** PostHog was building one boundary linter to replace tach, the import-linter contracts, the `hogli product:lint` isolation checks and several repo invariants. The boundary config was spread over `tach.toml`, `[tool.importlinter]` in `pyproject.toml`, constants in the `product:lint` code (`MODEL_CROSSINGS`, carve-outs), and two baseline files; the 89 `products/*/product.yaml` files held only `name` and `owners`, and `packages/owners-yaml` read them. In 3 months, 130 commits by 53 authors had touched `tach.toml`, 70% of them 6 lines or fewer. The second opinion was asked three questions, each for a recommendation with repo evidence: where per-package boundary policy should live (`product.yaml`, one central config, or nowhere because it is derived from code), considering review routing, churn, agents editing allowlists and the other readers of `product.yaml`; whether a declared per-package `depends_on` list is worth keeping once cross-package references must land on public surfaces and levels are computed, with real cases of what `depends_on` catches that an entrypoint rule would not; and the config file format and location for a tool built in Rust (parser) plus Python (rules). The limit was about 900 words.

**Answer.**

*1. Derive normal boundaries; keep exceptional policy in one central linter config. Leave `product.yaml` for product metadata.*

Derive package membership, conventional public surfaces, actual dependencies and isolation levels from code.
Declare only decisions the code cannot justify itself: exceptional exports, sanctioned model crossings, legacy exemptions and deliberate dependency prohibitions.

This follows the architecture doctrine: `MODEL_CROSSINGS` only shrinks, adding a class requires a doctrine change, and new baseline entries require a scanner change or an approved exception.
Those are repository architecture decisions, not something a product should grant itself by editing its manifest.
Conversely, making every product restate standard facade paths or its computed isolation level creates redundant policy.

Review routing matters more than physical proximity:

- Product-local metadata naturally routes to the product team. That is useful for ownership, but not enough for exceptions whose CI and coupling costs affect everyone.
- Route the boundary config to the tool's owners and involve the affected product owners. Isolation exemptions need required review; central placement alone does not enforce review.

The churn figures (130 commits, 53 authors, 70% at most six lines) argue for **removing routine declarations**, not for distributing the same bookkeeping across 89 manifests.
Small changes are not necessarily harmless: a one-line exception can reopen a boundary.
Agents should get diagnostics that explain the required code move, not an automatic "add this allowlist entry" fix.
Baseline regeneration should stay decrease-only, with growth checked against the base revision.

Adding fields to `product.yaml` would **not inherently break its current readers**.
The owners-yaml alias parser reads `owners` and an optional boolean `sensitive`, and ignores other fields.
Its strict `owners.yaml` JSON schema is not the product-manifest schema.
`ProductYamlCheck` validates known fields without rejecting extras; the maturity report also loads metadata.
CI uses manifest changes to trigger ownership validation.
So compatibility is manageable, but boundary policy would acquire another schema owner and unrelated ownership-validation work.
Avoid that coupling.

*2. Drop the exhaustive per-package `depends_on`; keep explicit architectural prohibitions.*

The list has one distinct capability: it rejects a **new production dependency even when the import uses a valid public surface**.
An entrypoint rule cannot do that.
Concrete examples from the git history:

- Commit `649e788ef48` added the access-control product to another product's dependencies, for public-facade object-access checks.
- Commit `861859df2e8` added one product to another's dependencies for a widget.

Without those additions, the dependency check would reject the new relationships; public-surface checks alone would accept them.
Existing code makes the distinction equally clear: some products import other products' facades, and tach separately authorizes those package edges.

That is real protection, but it is **dependency admission control**, not encapsulation or proof of isolation.
The observed changes suggest that much of the maintenance is approving legitimate facade use.
Unless reviewers intentionally govern every new relationship, exhaustive lists become an easily amended inventory.

Keep purposeful rules instead: leaf packages, forbidden dependency directions, and specific independence constraints.
The existing owners-yaml leaf contract, for example, forbids dependencies on the application, Django and DRF: even a public application facade would violate its standalone-distribution requirement.
That is import-linter protection, not tach protection; the replacement must keep it.

Also keep the existing distinction between tests and production: `lint:tach` excludes tests from dependency admission but includes them in interface enforcement.

Compute and report new graph edges for reviewers.
Do not mistake that graph, or facade-only imports, for sound isolation: the doctrine also requires constrained facade shapes, sealed presentation, watched models and migrations, and runtime wiring checks.
Presentation should stay an entrypoint for designated wiring, not a generally importable sibling-product API.

*3. Use a dedicated root `boundaries.toml`, with one authoritative parser and schema.*

TOML fits the existing `tach.toml` and `pyproject.toml` conventions; Python tooling already uses `tomllib`.
YAML is viable, but reusing its syntax does not justify attaching architectural policy to ownership manifests.

Prefer a dedicated file over `[tool.boundaries]` in `pyproject.toml` because:

- The scope includes core, products and standalone packages, across the Rust/Python implementation split.
- Review routing can target architectural policy specifically.

Use versioned, typed records with unknown-key errors, exact exception targets and reasons.
Parse once into a normalized representation shared with the Python rules.
Keep large generated ratchet ledgers separate from hand-authored policy, while validating both through the same tool.
No per-package file should be required for a package that follows the defaults.

## 4. Mapping a separate linter survey onto the model (Nov 4)

**Question.** A separate conversation with another model had produced three answers: a survey of linter tools, a linter architecture, and an agent-facing interface. The architect read them after the blind round (so they did not anchor the blind reports) and mapped what they add to the model, what they confirm, and what does not apply. This section is the architect's mapping, not the survey itself.

**New for the model (not in the round 1 synthesis):**

1. An edge keeps `referenced_surface` and `resolved_targets` separately. Facade entrypoint rules judge the surface the caller named; "facade re-exports internals or models" rules judge the origin. A resolver that collapses re-exports breaks one of the two. This ties to the Packwerk lesson (publicness comes from API shape).
2. Three IR levels: source facts -> resolved relationships -> architectural views. Ownership (`product.yaml`, owners) is a projection, not baked into edges: a `product.yaml` change re-projects, with no re-parse.
3. A graph view is part of rule semantics. A component path A->B->C is not a module import chain. Witnesses must say which. tach `depends_on` is component-level; crossings are module- or site-level.
4. Finding identity matches the policy promise (component pair, module pair, or access site). The crossings baseline keys were (class, consumer module, kind, count): module-level with a count. A use moved inside one module is free. Make that an explicit choice per rule.
5. Policy, fact and debt are kept apart. The import-linter `ignore_imports` list mixed both: "presentation -> facade allowed" (policy) and "TODO: existing violations" (debt) lived in one list.
6. Waivers apply after evaluation; accepted edges stay in the graph. PostHog's 6 import-linter contracts were all `forbidden`, 5 with `allow_indirect_imports = true` (direct-only), so the risk of a waiver hiding a transitive path was small, but it matters once reachability rules exist.
7. The run outcome is tri-state: clean / violations / incomplete, with completeness scoped per rule (what facts it requires). This extends the per-edge resolution status from the first second opinion to the run level.
8. Edge context: production / test / type-only / migration / `ready()` / admin. Contexts instead of blanket excludes. The tach test exclusion (fixed Aug 28) was this failure. 1,582 `TYPE_CHECKING` sites in `posthog/`, `ee/` and `products/`: type-only coupling needs an explicit rule decision.
9. Signature exposure: a facade returning `QuerySet[Model]` or a model is a weak boundary even with a clean import path. Edge kind `exposes` (an annotation mentions a type). This needs annotation resolution.
10. Incrementality: failed lookups invalidate too (a new file makes an unresolved import resolve). Oracle: incremental run == clean run on the same snapshot.
11. An agent interface from the same model: check / explain / query-policy / compare. `compare` reports new exemptions and policy edits separately from fixes. This fits the 130 `tach.toml` commits by 53 authors and agents editing allowlists, and could feed a reviewer's view of policy-edit PRs.
12. Test layers: extraction, resolution, policy over synthetic graphs (no parser), and a real-repo diff corpus. Property: shifting lines does not change finding identity.

**Agrees with the synthesis (confirmation, not new):**

- The fact model + policy + reporting split; Django as an analysis adapter that adds facts.
- Explicit resolution states over confidence scores (same as the first second opinion).
- No total-count gating (same as the two-way equality ledger).
- The Packwerk, Sentry and Kraken lessons (same as `docs/research/research-round-1-prior-art.md`).
- Its first answer (import-linter + Django system checks + Fixit) is close to `docs/research/research-round-1-contrarian.md`.

**Disagrees or does not apply:**

- "Python first, language later." The timings say static analysis is already fast, so this is fair for the semantics spike; the Rust case rests on editor and pre-commit latency and a whole-repo index, not on CI.
- It assumes Django checks need `setup()`. The designs here read Django statically and use `setup()` only as the oracle.
- The Google 10% false-positive figure, the ETH context-file study, Generative Compilation, and an agent A/B/C evaluation: parked.
- It has no repo facts; all its numbers are generic.
