# Exports and shapes: second opinion

> Three rounds of a second opinion from another model on facades, generated code, packages in core, relocations, ORM independence, registries and one fact store, Nov 6, 2026.
> Partly replaced: the synthesis is `docs/research/exports-and-shapes.md`, and the resulting design is in `docs/design/09-contract-surfaces.md` and `docs/design/04-rules.md`.

Round 1 answered the three questions independently.
Round 2 answered the architect's view and the widening pass.
Round 4 answered the four follow-up questions (round 3 in the synthesis is the same discussion; the numbering follows the synthesis).
All work was read-only on the PostHog monorepo at `d1051a3875f`.
The dev environment was not set up, so counts come from tracked files and textual Python imports (including type-only imports unless noted): direct-import counts, not resolved transitive proofs.

## Round 1

**Recommendation: keep facade policy in the preset; add generic generation provenance; introduce explicitly named packages inside the legacy trees; keep the foundation split.**

### 1. Facade: core enforcement, preset vocabulary

The draft's division is right.
Core owns `entrypoint`, surface-versus-origin resolution, re-export traversal, `signature`, and `reach`.
The Django pack identifies models, managers and querysets.
PostHog config decides that `backend/facade/**` is public, that contracts cannot reach Django, and that facade signatures cannot expose ORM objects.
No additional facade primitive is needed.

Also, `facade-no-drf` means **DRF-free**, not "framework-free": the draft still permits Django.

Presentation deserves a narrower policy.
**56 non-test files in 26 products import another product's presentation code**, across **43 product pairs**:

- **42 files** import access-control presentation: reusable serializer and viewset mixins.
- Other consumers reuse helpers and views from other products' presentation code.
- Some presentation dependencies are reciprocal.
- Core URL composition also legitimately imports presentation entrypoints.

So do not declare every presentation module public.
Distinguish **registration entrypoints**, **reusable presentation adapters**, and private presentation implementation.
Keep the existing nonconforming imports as debt.

Illustrative preset shape:

```toml
[rules.public-surface]
primitive = "entrypoint"
endpoint = "surface"
select = "src.package != dst.package and dst.tier == 'product'"
allow = """
  dst.zone == 'facade'
  or dst.surface == 'presentation-adapter'
  or (src.zone == 'composition' and dst.surface == 'registration')
"""
```

`surface` here is a configurable label; assignments identify the particular access-control mixins and registration modules.
Do not solve business-helper reuse by broadly blessing view modules.

### 2. Generated code: track provenance, preserve boundaries

Inventory sizes are physical lines, rounded; families include supporting `__init__` files where applicable.

| Pipeline / output family | Files / lines | Consumers |
|---|---:|---|
| OpenAPI → frontend API clients, types, Zod | 278 / 297k | Main frontend and product frontends |
| OpenAPI + MCP configs → tools, types, UI apps | 176 / 327k | MCP executor, CLI, handwritten tools and UI dispatch |
| TS query schemas → `schema.py` + `schema_enums.py` | 2 / 39,749 | **1,341 Python files import `posthog.schema`**; 1,443 import either output |
| Query JSON, schema-version and validator intermediates | 4 / 78k | Frontend query validation and generation |
| Registered Python projections | **6 producers, 13 outputs / 11,140** | Frontend, MCP, desktop, mobile, agent packages, Python |
| Warehouse source configs | 1,418 / 14.7k | Source implementations and registry; warehouse consumers |
| Protobuf pipeline roots | 50 / 13.2k | Personhog client, ingestion workers, usage ingestion, proxy service |
| ANTLR outputs | 12 / 26.3k | Native and WASM HogQL parser |
| Django migration trees | 3,526 / 366k | Django migration loader; historical dependencies |
| ClickHouse migration Python | 358 / 10k | Migration runner |
| SQLx metadata | 293 / 10.7k | Rust compile-time query checking |

Additional surfaces: generated, untracked `*LogicType.ts` files, frontend product registration and typed analytics output, admin CSS, canonical AI prices, ingestion-warning JSON, and generated parity fixtures.
Migrations contain both generated and handwritten code; they are not reproducible outputs of the current models.

The warehouse count includes **1,399 source-specific `_config.py` files** plus the older generated-config directory.

The boundary problem is **ownership disappearing through generation**.
A Python consumer sees `posthog.schema`, not the TS declarations that produced its symbols.
Projection outputs likewise carry product-owned data into another package without a Python import edge.
Generated clients and registries can hide dependencies or introduce real ones; excluding generated files would erase both.

Recommended split:

- **Core:** producer, input and output provenance; unique-producer and inventory checks; coverage checks; ordinary boundary rules on generated executable code. Generation edges belong in a separate build or impact view, not the runtime cycle graph.
- **PostHog preset and adapters:** known pipelines, generated-file discovery conventions, permitted output locations, CI tasks and formatter coverage.
- **Django pack:** migration dependency and model-reference facts. Keep historical migration edges separate from current runtime architecture.
- **Still missing:** TS, JS, Rust and C++ resolution, symbol-level source → output maps, generator manifests, CI trigger facts, and dynamic registration facts. A Python-only L1 cannot certify cross-language boundary compliance.

Proposed shape, not hoglint syntax of that date:

```toml
[providers.codegen]
adapter = "posthog-codegen"
sources = ["hogli-projections", "openapi", "query-schema", "protobuf"]

[rules.generated-producer]
primitive = "inventory"
select = "generated == true"
require = "exactly_one_producer"

[rules.generation-watched]
primitive = "coverage"
covers = "codegen.inputs_and_outputs"
inputs = { adapter = "ci", task = "generation-check" }
```

Import the existing projection registry; do not keep a second output list.

This can replace the registration, existence and uniqueness, CI-filter coverage and formatter-exclusion assertions in PostHog's generated-files invariant test.
That test's naming heuristic misses `schema.py`, `schema_enums.py`, warehouse `_config.py`, protobuf filenames and kea types; whole-directory allowances also do not establish individual provenance.

**Keep the projection runner:** rendering, exact output-set validation, formatting and byte-for-byte drift checking are execution responsibilities.
hoglint should not become a generator or infer freshness from imports.
Agent-written code is not "generated" in this reproducible-pipeline sense.

### 3. Packages inside legacy trees: yes, selectively

Counts are non-test Python files, then files directly importing Django / `posthog.models` / products:

| Candidate | Files | Django / models / products | Suitable policy |
|---|---:|---:|---|
| `posthog/ingress` | 37 | 16 / 0 / 0 | No product or domain-model imports |
| `posthog/egress` | 50 | 5 / 0 / 0 | Same; allow Django infrastructure |
| `posthog/kafka_client` | 7 | 2 / 0 / 0 | Product-free infrastructure |
| `posthog/personhog_client` | 8 | 2 / 2 / 0 | Product-free; model-free would need work |
| `posthog/clickhouse/client` | 9 | 3 / 3 / 1 | Infrastructure with existing product-edge debt |
| `posthog/object_tags` | 4 | 0 / 0 / 0 | Clean module-level dependency policy |
| `posthog/integrations` | 13 | 1 / 2 / 0 | Layer contracts separately from ORM adapters |
| `common/hogvm` | 16 | 0 / 2 / 0 | Both model imports are `TYPE_CHECKING` |
| `posthog/hogql` | 261 | 19 / 46 / 48 | Proto-package and ratchets, not purity |
| `ee/billing` | 27 | 11 / 11 / 5 | Proto-product |
| `ee/hogai` | 297 | 31 / 77 / 73 | Migration ownership and graph visibility |

Do not impose Django-free rules on the whole of ingress or egress.
Even ingress verification uses the Django cache and database exceptions.
Narrow contracts and algorithms are better candidates.

Two important limits:

- Ingress loading dynamically discovers product consumers. Zero literal product imports does not mean zero runtime product coupling; classify that loader as composition.
- `posthog/__init__.py` imports Celery, which imports Django. So even `object_tags` is not independently Django-free to import. A runtime `reach` proof needs package-initialization edges.

Package identity should be independent of directory and orthogonal to zone:

```toml
[[assign]]
match = "posthog/egress/**"
set = { package = "egress", zone = "infrastructure" }

[[assign]]
match = "ee/billing/**"
set = { package = "billing", stage = "proto-product", role = "feature" }

[rules.infrastructure-direction]
primitive = "edge"
from = "src.zone == 'infrastructure'"
deny = "dst.tier == 'product' or dst.stage == 'proto-product'"
```

An existing destination product can share that stable package id during migration.
Do not turn every directory into a package automatically, or duplicate ownership already assigned to a product.

What this buys:

- **Cycles and `compare`:** named dependencies and cyclic-edge ratchets before files move. Extend beyond the draft's product-only graph; keep paths through legacy code.
- **Ledger continuity:** stable package-pair debt survives moves. **Module-symbol debt does not** under the agreed identity; it needs explicit, reviewed relocation mapping.
- **Test selection:** useful reverse dependencies, once the runner consumes these packages. PostHog's CI test selector deliberately dropped `posthog`, `ee` and `common` endpoints from its graph projection.
- **Isolated:** a future earned capability requiring contract coverage, wiring and model checks, test-task integration and complete facts. A package declaration alone earns no skip.

It does **not** create an independent Django app, database boundary, lightweight import path or hermetic test suite.

### 4. Foundation and feature: keep it, and keep it orthogonal

The scan still finds **zero direct foundation → feature imports**; the chain remains notifications → access control → event definitions.

Access control's presentation mixins show that a foundation may provide reusable framework adapters as well as a facade.
Generated outputs must keep source ownership rather than becoming "core" or "foundation" automatically.
Proto-products need explicit roles, so moving a feature into `ee/` cannot evade foundation direction.

Do not promote schema aggregates, ingress, egress, or high-fan-in legacy packages to foundation merely because many callers use them.
Extend the direction check to named feature proto-products, and eventually to generated-symbol provenance, without changing the three foundation products.

## Round 2

### 1. Facade: the architect's revised position wins

The second opinion withdrew "a configurable label is enough".
A label permits declaring a door while forgetting the constraint that prevents the catch-all drawer.

Minimal generic core: **named surface + exported symbols + audience + mandatory shape-policy reference**.
Core validates and explains the combination; language adapters recognize shapes; presets choose permitted shapes.

Illustrative syntax:

```toml
[[surfaces]]
package = "example"
name = "facade"
symbols = ["example.facade:fetch", "example.contracts:Result"]
audience = "*"
shape = "posthog-contract"

[shapes.posthog-contract]
data = "frozen-dataclass"
behavior = { kind = "abc", owner = "core" }
deny = ["django-model", "django-manager", "django-queryset"]
unknown = "error"
```

Shape checking must follow re-exports and recursively inspect fields, signatures, and bases.
Checking only the exported name or the outer return type misses `Result(model: Team)`.
Functions may implement operations; behavior objects crossing the boundary must satisfy approved ABC contracts.
Registration and presentation adapters need their own explicit policies.

### 2. Generated code

**a. Keep the diff check, advisory only.**
This changes the view of its usefulness, not of its evidentiary strength.
Call it `unexplained-generated-change`, not `generated-not-hand-edited`.

An output-only change can legitimately repair previously stale output.
Conversely, touching an unrelated declared input can conceal a hand edit.
Include templates, generator configuration, dependency locks, and formatter versions among generation inputs.
Exact regeneration remains authoritative.

**b. Contract-first: yes for data contracts; not the whole facade.**
Product-owned schemas generating frozen DTOs are a good long-term direction where several languages consume the same contract.
They do not generate behavioral semantics, authorization, transactions, or ABC implementations.

The existing pipelines show reach, not compliance: generated pydantic models are not automatically frozen dataclasses, and serializer-derived schemas are implementation-first.
hoglint should enforce ownership, output shape, and provenance; it should not require every small Python-only contract to acquire a schema compiler.

Prefer product-owned sources and outputs, with explicitly shared contracts owned separately.
Split `posthog.schema` incrementally using symbol provenance; changing filenames alone does not restore ownership.

**c. One fact store, distinct graph views.**
Exact impact direction:

```text
input / producer / toolchain → generation action → output → consumer → test
```

The last two arrows reverse dependency edges.
Runtime-cycle analysis excludes generation edges.

| Consumer | Reads |
|---|---|
| `compare` | Base and head imports, surfaces, generation manifests, changed inputs and outputs |
| Test selection | Transitive impact view, including generation and runtime registration |
| Ownership and review routing | Declared contract owner plus source → output provenance; generator changes also route to tooling owners |

Mixed-owner generated files have several provenance sources.
Never give them one owner merely because one generator emitted them.

### 3. Packages in legacy trees

**a. Outside `posthog/` is necessary under the current initializer for ordinary Django-free imports, but not for all architectural leverage.**
The alternative is to remove the initializer's eager dependency.

Static measurement:

- `common/__init__.py`: **0 bytes, 0 imports**.
- `common/hogvm/__init__.py` and `common/hogvm/python/__init__.py`: likewise empty.
- `posthog/__init__.py` imports `posthog.celery`, which directly imports Django.

This establishes initializer behavior, not the purity of every `common` child.
The `common/` guide calls it a transitional holding pen: prefer `packages/` for genuinely shared leaves, or the owning product.

A plain pytest lane also requires excluding the root configuration and conftest and the Django plugin setup.
Named packages inside `posthog/` still buy dependency enforcement, ownership, and migration tracking.

**b. Pick reviewed relocation mapping.**
Stable package ids preserve package-level debt automatically.
Module and symbol findings transfer only through explicit mappings:

```toml
[[relocations]]
from = "posthog.old.contracts:Result"
to = "products.example.backend.contracts:Result"
```

Validate mappings against both revisions, and transfer identity, not exemptions from new policy violations.
Package-relative paths help root-only moves but fail the restructuring that real migrations entail.
(Round 4 dropped this list.)

**c. Rank is useful shorthand, not a replacement.**
It expresses the four-level ordering compactly.
It cannot express forbidden same-rank dependencies, model or framework restrictions, audiences, or cycles.
Leaving proto-products unranked also creates an escape hatch.
Keep roles; optionally compile rank into direction rules; treat unranked coverage explicitly.

### 4. What we still miss

1. **Unchanged shape does not imply unchanged behavior.** An implementation fix can break a consumer without changing DTOs. Contract-based test skipping needs explicit assumptions about mocks, provider conformance, and integration tests.
2. **Make uncertainty an output.** Unresolved symbols, dynamic registration, and missing language adapters must produce coverage gaps. "No violations found" cannot authorize test skipping.
3. **Add compatibility analysis.** Surface diffs can flag removed operations, newly required fields, and changed defaults. Boundary legality alone misses breaking changes that travel through PostHog's generated clients and MCP.
4. **Make hoglint a migration planner.** A proposed package boundary should report crossing edges, affected tests, generator entanglement, and remaining isolation blockers before files move. That is useful for legacy extraction right away, beyond another lint gate.

## Round 4

### 1. Drop `[[relocations]]`. Consumer moves are already covered.

The data model's section 9 (`docs/research/data-model.md`) separates exact ledger equality from growth:

- Regenerate ledger rows after a move.
- Compare counts by `(crossing, detailed kind)`, **summed across consumers**. Moving or splitting consumers therefore adds no debt.
- This is specific to those growth keys; rules defaulting to per-line growth do not inherit that guarantee.

The crossing target was `product.Class`, so moving its defining module **within the same product** also survives.
Moving ownership from `posthog` into a product does not survive automatically.

For targets, separate persistent identity from current path and owner:

```yaml
growth_key: [rule, target.entity_id, shape.kind, shape.detail]
# Consumer location remains in exact ledger rows.
```

Use an unchanged Django `(app_label, model_name)` as model identity where possible; ownership still comes from the defining module.
For other symbols, infer one-to-one continuity across base and head from git renames plus declaration matching.
Persist the inferred identity in generated ledger metadata.
If matching is ambiguous, require an explicit stable id attached to the declaration, not an old → new list.

**Do not** use a repository-wide debt total, silently accept fuzzy matches, or transfer allowances between rules.
A move that creates newly forbidden dependencies must still report them.
Arbitrary simultaneous moves, renames and rewrites cannot be recognized reliably without some persistent identity.

(The synthesis chose the table name over `(app_label, model_name)`, because `app_label` changes on a move into a product.)

### 2. Yes: enforce "ORM-independent", with an honest distinction between violations and unknowns

"Handles models" should include:

- Importing concrete models, generic `Model`, managers or querysets, including aliases, re-exports and type-only imports.
- Constructing, receiving, returning, storing or operating on those values; recursively inspect containers, DTO fields, callbacks and generic bounds.
- Model lookup through `get_model`, model registries and equivalent reflective access.
- ORM-derived querysets even when their result shape is scalar, such as `.values()`.

Django settings, cache, HTTP types and exceptions remain allowed.
Database connections and transactions need a separate policy: **ORM-independent does not mean database-free**.

Source-scan counts, excluding tests and migrations:

| Package | Python files | Files importing Django | Files importing ORM or model modules |
|---|---:|---:|---:|
| `egress` | 50 | 5 | 0 |
| `ingress` | 38 | 16 | 1 |
| `kafka_client` | 7 | 2 | 0 |
| `object_tags` | 4 | 0 | 0 |
| `personhog_client` | 8 | 2 | 2 |
| `storage` | 20 | 14 | 18 |

The synthesis understated ingress: its database helper accepts `Sequence[type[Model]]` and calls the database router.
Move model-to-alias resolution into the callers if the whole package should qualify.
Personhog converters construct unsaved models; they still handle models.

```yaml
packages:
  egress:
    framework: django
    deny_shapes: [django.Model, django.Manager, django.QuerySet]
    unknown_boundary_types: incomplete
```

Untyped parameters and `Any` are **unknown**, not proof of model handling or of safety.
The Kafka client has both an untyped serializer parameter and `Any` payloads; ingress has `Mapping[str, Any]`.
Start with a "no resolved ORM dependencies" gate, then certify stricter boundaries using checked JSON or value contracts and type-flow information.

**Benefit:** keeps transport infrastructure independent of domain schemas and lazy ORM behavior.
**Not a benefit by itself:** Django-free startup, database-free tests, or safe test skipping. Those need separate evidence.

### 3. Yes: one registration protocol, several typed lifecycle channels

Measured inventory:

| Mechanism | Size |
|---|---|
| `load_product_modules` | 6 production call sites, 5 distinct channels |
| Files matching those channels | 75 routes, 11 freshness, 10 webhook consumers, 4 integration usages, 8 highlights |
| Product app list | 91 hardcoded app configurations |
| Health-check registry | 16 module paths across 6 products |
| `CELERY_IMPORTS` | 12 paths, including 4 product paths |
| Ingress extra discovery | 1 non-product path |
| AI tool discovery | A separate `pkgutil` loader; 16 `max_tools` module or package entrypoints |
| Temporal scheduling composition | 35 product import statements across 26 products |

Also present: ordered middleware, OpenAPI hooks and enum paths, three lazy Team-extension declarations, and Dagster location composition.
These have different semantics; not every product reference is a discovery list.

The existing watched-channels lint already recognizes three hidden-dependency channels: foreign signal receivers, dotted paths outside tests, and shared-loader discovery.
It is an extractor, not another runtime registry.

Proposed shape:

```yaml
# Product-owned product.yaml
runtime:
  django_app: products.growth.backend.apps.GrowthConfig
  registrations:
    health_checks:
      - products.growth.backend.temporal.health_checks.sdk_outdated

# Core-owned protocol configuration
channels:
  health_checks:
    loader: posthog.products.load_registrations
    phase: lazy
    contract: HealthCheckRegistration
```

Discover manifests without importing product code, and derive installed apps before Django initializes.
Products declare contributions; core declares channel contracts and lifecycle.

hoglint should enforce:

- Product-loading operations use the sanctioned protocol; framework loaders get explicit adapters.
- Registration targets exist, belong to their declaring package and satisfy the channel's export and shape policy.
- Keys are unique; ordering is explicit where meaningful; internal import failures propagate.
- Every registration emits a dependency edge. Unknown dynamic targets make the relevant analysis incomplete.
- New hand-maintained product discovery lists are rejected.

**Keep explicit:** framework bootstrap, security-sensitive middleware order, deployment and worker selection, stable persisted task and model names, and historical migration references.
These encode operational or compatibility decisions.
Product membership lists can be generated; the decisions cannot be inferred from directory presence.

**Do not** eagerly import every registration at startup, ban all dynamic imports, or pretend a Python linter can prove arbitrary reflection harmless.

(The manifest form here was the open disagreement with the architect; the Nov 9 prior-art round recommended the file-name convention instead. See `docs/research/prior-art-modules-deployment.md`.)

### 4. One fact store: yes. One undifferentiated graph: no.

| Edge family | Main consumers |
|---|---|
| Imports, references, inheritance, exports, re-exports | Boundary lint, dependency impact |
| Model labels, relations, reverse accessors, migration dependencies | ORM rules, migration checks |
| Registration, dispatch, signal subscription, dotted-path and patch targets | Runtime impact, test selection |
| Test execution and coverage relationships | Test selection, with provenance and confidence |
| Generator inputs → producer → outputs → consumers | Codegen impact, drift attribution |
| File and package ownership and task inputs | Review routing, CI selection |

Keep snapshot, context, resolution status and provenance on facts.
Runtime-cycle views exclude generation edges; test-impact views include them.
Accepted debt stays visible.

For PostHog's CI test selector, this replaces overlapping tach, library-import, schema and hidden-channel discovery with one versioned impact query.
Base and head graphs keep deleted-file dependencies.
Named infrastructure packages allow traversal through useful parts of core, which the existing product graph drops entirely.

**Do not** promise fewer tests merely from shared storage: composition hubs still legitimately fan out.
Missing extractors or unresolved dispatch must widen selection.
The CI runner keeps scheduling, sharding and execution; hoglint supplies explainable dependency facts.

Counts above are read-only source measurements, not a semantic certification.
