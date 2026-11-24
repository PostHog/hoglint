# Exports and shapes, generated code, packages in core

> Rounds 3 and 4 of the design discussion: facades as exports with a shape, generated code, packages inside the legacy trees, registries and one fact store, Nov 6, 2026. Synthesis after two second-opinion rounds, plus the architect's views and a widening pass.
> Partly replaced: contract surfaces are in `docs/design/09-contract-surfaces.md`, levels in `docs/design/03-levels-and-isolated.md`, rules in `docs/design/04-rules.md`; the second opinion's full text is `docs/research/exports-and-shapes-second-opinion.md`.

The questions (Nov 6):
1. Is "facade" a core concept, or made up through config?
2. Generated code is a wide surface in the agentic era. How does it fit into Hoglint?
3. Packages inside `posthog/` and `ee/`: keep for example ingress and egress pure (Django-free?), and declare proto-products to help migrate code out. Is there test-isolation leverage?

Order of work in round 3: the architect wrote a view first; a second opinion from another model answered independently (round 1); both answers were judged too narrow (owner, Nov 6), so the architect did a widening pass; then the second opinion answered again (round 2).
Round 4 followed up on four questions: the relocation list, model-free Django, dynamic loading, and one graph.

All measurements are of the PostHog monorepo at `d1051a3875f` (Nov 6, 2026).
"The draft" is the design draft of that date (not published).

## Round 3 result

### 1. Facade becomes a core concept: export = door + shape

The concept is named `exports`, because "surface" is already the edge field for the name a caller wrote.
Both sides agree; the second opinion withdrew "a label is enough" in round 2.

- **Core:** a named **export** of a package: exported symbols (or paths), an audience selector, and a mandatory **shape policy**. An export without a shape policy is a config error. Core validates, resolves re-exports, and explains which export allowed an edge.
- Shape is checked recursively (fields, signatures, bases, through re-exports): `Result(model: Team)` fails even when the outer type is a dataclass. Language packs recognize shapes (frozen dataclass, ABC, Django model, manager, queryset); presets pick the allowed ones.
- **Preset:** the names (`facade`), and PostHog's shape: frozen dataclasses for data, core-owned ABCs for behavior, never ORM.
- **Why:** the owner's catch-all-drawer doctrine. Location alone made Packwerk's `app/public` a drawer. Packwerk later moved privacy out of its core ("the value proposition of packwerk is in dependencies"). A door without a shape repeats that.
- **Presentation is not one surface.** 56 non-test files in 26 products import another product's presentation code (43 product pairs); 42 of them are access-control serializer and viewset mixins. Split it into `registration` (audience: composition), `presentation-adapter` (named reusable mixins, own shape policy) and private implementation; the rest is ledgered.

### 2. Generated code: provenance in core, contracts as the long-term facade for data

Inventory (second opinion, physical lines):

| Pipeline / output family | Files / lines |
|---|---|
| OpenAPI → frontend clients | 278 / 297k |
| OpenAPI + MCP configs → MCP tools | 176 / 327k |
| TS query schema → `posthog/schema.py` + `schema_enums.py` | 2 / 40k, imported by 1,341 Python files (794 non-test) |
| hogli projections | 6 producers, 13 outputs / 11k |
| warehouse source configs | 1,418 / 15k |
| protobuf | 50 / 13k |
| ANTLR | 12 / 26k |
| Django migrations | 3,526 / 366k |
| ClickHouse migrations | 358 files |
| SQLx metadata | 293 files |
| local kea `*LogicType.ts` | 884 files |

The boundary problem: ownership disappears through generation.
Product query types are authored in the core frontend and land in one core module that every product imports.
Projection outputs carry product data into other packages with no import edge.

Result:
- **Core:** `generated` provenance (producer; inputs, including templates, generator config, locks and formatter version; outputs). Rules: exactly one producer per generated file (inventory); generated executable code is subject to ordinary boundary rules, because it makes real imports; a finding witnessed in a generated file is attributed to the source.
- **Advisory `unexplained-generated-change` in `compare`:** an output changed while no declared input or producer changed. Not a gate; exact regeneration (each pipeline's drift check) stays authoritative.
- **Graph:** one fact store, separate views. The impact view is input / producer → generation → output → consumer → test. The runtime cycle view excludes generation edges. Consumers: `compare` (base and head), test selection (impact view), owner routing (source provenance; mixed-source outputs keep several owners).
- It replaces PostHog's generated-files registration test, whose name heuristic misses `schema.py`, `schema_enums.py`, warehouse `_config.py` files, protobuf and kea types. Hoglint reads the projection registry; the projection runner keeps rendering and drift checks. Hoglint never generates.
- **Contract-first, long term:** product-owned schema sources that generate frozen DTOs are the right form for data contracts consumed by several languages (frontend, MCP, AI). Not for behavior. Split `posthog.schema` per product incrementally, guided by symbol provenance (needs a generator-emitted symbol → source map).
- **Not covered yet:** TS and Rust resolution, symbol maps, CI trigger facts. A Python-only L1 cannot certify cross-language boundaries; such rules report incomplete.

### 3. Packages inside `posthog/`, `ee/`, `common/`

- Package identity is independent of directory and orthogonal to zone. Declare named packages selectively: infrastructure (egress, ingress, kafka_client, personhog_client, clickhouse_client, object_tags) and proto-products (`stage = "proto-product"`, with an explicit role: for example `ee/billing`, `posthog/session_recordings`, `ee/hogai`). The legacy remainder stays `posthog` and `ee`.
- Purity rules that fit (non-test files):

  | Package | Files | Django imports | core models | products |
  |---|---:|---:|---:|---:|
  | egress | 50 | 5 (settings, cache) | 0 | 0 |
  | ingress | 37 | 16 (HTTP adapter) | 0 | 0 |
  | kafka_client | 7 | 2 | 0 | 0 |

  Rule: no products, no `posthog.models`. Django-free is wrong for the whole of ingress or egress; narrow contracts inside them can be Django-free.
- **A Django-free import or test lane is impossible inside `posthog/`:** `posthog/__init__.py` imports `posthog.celery`, which imports Django. `common/__init__.py` is empty, and the `common/` guide calls it a holding pen and prefers `packages/` for shared leaves. So a pure package that wants a plain pytest lane moves to `packages/`, or the initializer drops its eager Celery import.
- **Test selection leverage** (the architect, from PostHog's CI test selector): the selector deliberately never routes the product cascade through `posthog` or `ee`, because "most products depend on core and core depends on most products". A named core package with no product edges is not in that cycle, so the cascade could route through it. That is the real CI leverage, and it comes from direction, not from purity.
- **Ledger across a move:** reviewed relocation mappings (`[[relocations]] from/to`, validated against both revisions; they transfer identity and never exempt new violations). Package-level debt survives automatically through stable package ids. The architect's package-relative identity fails real restructurings. (Round 4 dropped relocations; see below.)
- Proto-products get roles, so moving a feature into `ee/` cannot dodge the foundation rule.
- `rank` (infrastructure < foundation < feature < composition) is shorthand at most, compiled into direction rules. It cannot express same-rank bans, framework limits, audiences or cycles, and unranked proto-products would be an escape hatch.

### 4. Foundation lens

Still 0 foundation → feature imports.
Access control shows that a foundation may offer a presentation adapter next to its facade.
Generated outputs keep source ownership; they never become "core" or "foundation".
High fan-in (`posthog.schema`, ingress, egress) does not make something foundation.

### 5. What both missed (widening)

1. **Compatibility analysis:** diff surfaces between base and head (removed operations, new required fields, changed defaults). Boundary legality misses breaking changes that travel through generated clients and MCP tools.
2. **Hoglint as a migration planner:** `hoglint plan <proposed package>` reports crossing edges, affected tests, generator entanglement and isolation blockers before files move. Useful now for legacy extraction.
3. **Uncertainty as output:** unresolved symbols, dynamic registration (ingress loading discovers product consumers at runtime) and missing language packs are coverage gaps; "no violations" never authorizes a test skip. The draft had `incomplete`; extend it to dynamic registration.
4. **Shape unchanged does not mean behavior unchanged:** contract-based test skipping needs explicit assumptions (provider conformance tests, integration tests).
5. **One graph for the repo:** lint, test selection, codegen impact and review routing read the same fact store. Pants infers dependencies from imports with a Rust parser and selects tests from them.

## Round 4 result

The owner's follow-ups: relocations, model-free Django, dynamic loading, one graph.
The architect wrote a view first (below), then the second opinion answered.
Both agree on all four.

1. **Drop `[[relocations]]`.** Growth keys summed across consumers already make consumer moves free (data model section 9, `docs/research/data-model.md`); rules must declare location-free growth keys, because per-line defaults do not get this. Targets need an identity that outlives the path:
   - a model's table name (the architect; `app_label` changes on a move into products, so the second opinion's `(app_label, model_name)` holds only for moves within one app);
   - the package id;
   - git rename detection plus declaration matching for other symbols;
   - when ambiguous, a stable id on the declaration, never an old → new list.

   A split that cannot be followed fails: move and refactor go in separate PRs.
2. **"Uses Django, never handles models" is a Django level per package** (django pack: settings < framework < orm). Handling models means importing concrete or generic `Model`, managers, querysets, `get_model`, and constructing, receiving or returning them (recursively). `Any` or untyped is unknown, so incomplete. The second opinion's correction: ORM-independent is not database-free (ingress uses connections and transactions), and it does not by itself give database-free tests or a test skip. Measured: egress 0 ORM files, kafka_client 0, ingress 1 (a `type[Model]` router helper; to qualify, move alias resolution to the callers), personhog_client 2 (constructs unsaved models).
3. **Registries as a core concept on `load_product_modules`** (6 call sites, 5 channels, 108 product modules). Hardcoded lists: the product app list with 91 entries (= the 91 product `apps.py` files), a health-check module list with 16 entries across 6 products, `CELERY_IMPORTS` with 4 product paths, a `pkgutil` AI-tool loader (16 `max_tools` entrypoints), and Temporal scheduling composition with 35 imports from 26 products.
   Rules:
   - registration edges are visible (replacing channel 3 of the existing watched-channels lint);
   - core names no specific product outside a registry (the lists are ledgered);
   - a computed dynamic import only inside a declared loader;
   - registered modules satisfy the registry's export and shape;
   - unique keys, explicit order where it matters, import errors propagate.

   Keep hardcoded: framework bootstrap, security middleware order, worker and deploy selection, persisted names, historical migrations.
   Open disagreement at that point: the architect wanted convention by file name (already used; `product.yaml` stays ownership-only); the second opinion wanted registrations declared in a `product.yaml` runtime section, with installed apps derived before Django initializes. The prior-art round of Nov 9 (`docs/research/prior-art-modules-deployment.md`) recommended the convention.
4. **One fact store, several views:** boundary; ORM and migrations; runtime registration, signals and dotted paths; test relationships; codegen; ownership. The CI test selector gets one versioned impact query and can route through named infrastructure packages. Missing extractors widen selection. Shared storage alone does not mean fewer tests.

---

## The architect's round 3 view

Written Nov 6, before the second opinion.

### 1. Facade

As drafted, the facade was config only.
The preset declares a zone named `facade`, and the `public-surface` rule compares strings (`allow = "dst.zone in ['facade', 'presentation']"`).
Core supplies the generic parts: the hierarchical zone type, the `entrypoint` primitive, `endpoint = "surface"` with re-export resolution, and the `signature` primitive.

Proposal: promote "public surface" to a core concept named `exports`, and keep the word "facade" in the preset.
- Every module system has it: Go `internal/`, Rust `pub(crate)`, JPMS `exports p to q` (qualified exports), Packwerk `public/`, Nx entry points, Python `__all__`.
- Core: a package declares named exports, each with paths or symbols and an optional audience selector. Derived fields `dst.exported` (true, false or unknown) and `dst.export` (name). `entrypoint` defaults to `allow = "dst.exported"` and judges the surface the caller wrote.
- Core can validate what config cannot: an export that matches no file is a config error; `explain` names the export that allowed an edge; growth can key on (package, export).
- Stays config: export names and paths, audiences, and the facade-specific rules (signature without models, contracts without Django, facade without DRF).
- PostHog: a `facade` export (audience any) and a `presentation` export. About 30 product → other-product presentation imports existed (mostly serializer reuse, concentrated in three products), plus 11 from the core URL module. Narrowing its audience to composition would ledger those; decide separately.

### 2. Generated code

Principle: a generated file is a projection of its inputs.
It belongs to the package of its inputs and changes only when its inputs or producer change.

The surface:
- A registration test with pipeline roots and external producers, plus the projection registry in the hogli commands. Drift checks per pipeline (`build:openapi`, `build:projections --check`).
- `products/*/frontend/generated`: 275 files, about 282k lines, in 80 products. MCP generated code: about 170k lines. `frontend/src/generated`: 3 files, 14k lines. Node.js generated code: 6.8k lines.
- `posthog/schema.py`: 36k lines, generated from `frontend/src/queries/schema/*.ts`. Imported by 794 non-test Python files; 43 products import it.
- Migrations: 3,884 files, generated then hand-edited.

Proposal: `generated` as a core field (producer, inputs), set by config and by a provider that reads the projection registry statically.
1. `generated-registered` (inventory): every file that looks generated has a known producer. Replaces the registration test's lists.
2. `generated-not-hand-edited` (`compare`, diff-based): an output changed while neither its producer nor any declared input changed. No generator run; catches the common agent mistake. Exact freshness stays with each pipeline's drift check. Weak where inputs are broad (the OpenAPI inputs are all API code).
3. Provenance labels: a generated module takes package labels from its inputs. The path already gives the right answer for `products/*/frontend/generated`. `posthog/schema.py` is the exception: 43 products depend on one core hub for their own query types, which are authored in the core frontend. Visibility needs symbol provenance (the generator emits a symbol → source sidecar); the structural fix is per-product schema sources and outputs.
4. Attribution: a finding witnessed in a generated file is attributed to the producer input, never to the generated file. Generated files count for edges (they create real imports), not for authored-code rules.

Rules 1 and 2 are file-level and work on TS without a parser.
Edge provenance in TS waits for a TS fact producer (L1 is Python-only).
Migrations are their own kind, read by the django pack, and not under the hand-edit rule.

### 3. Packages inside `posthog/` and `ee/`

Purity, measured on non-test files (Django = any `django` or `rest_framework` import):
- egress: 50 files; Django in 5, all `django.conf.settings` or `django.core.cache`; 0 core models, 0 products.
- kafka_client: 7 files; 2 settings; 0 models, 0 products.
- ingress: 38 files; 16 Django (`HttpRequest`, db connections: it is an HTTP adapter); 0 models, 0 products.
- personhog_client: 4 files import core models (converters). clickhouse/client: 3 core-model files, 1 product.
- hogql, 262 files: 46 core-model, 48 product. temporal, 389: 98 product. ee/hogai, 300: 72 product.

"Django-free" fits egress and kafka_client (settings only) but not ingress.
"Model-free and product-free" fits all three.

Proposal:
- Packages inside core are ordinary packages (a package is not tied to `products/`). Turn the infrastructure zone into packages: egress, ingress, kafka_client, personhog_client, clickhouse_client, db_backends, `common/*`.
- Rules: infrastructure never imports products (existing) or `posthog.models` (new). A per-package framework allowlist: egress and kafka_client use only `django.conf.settings` and `django.core.cache` from Django.
- Proto-products: legacy directories with an owner and a future product name (for example `posthog/session_recordings`, `ee/billing`). `tier = "proto"`, `target = "products/<name>"`. Effects: an own node in the cycle rule and in `compare` (the big `posthog` node splits); callers can move onto its exports before the move; owners come from `owners.yaml`.
- Ledger identity as (package, path relative to the package root, symbol), so moving a proto-product into `products/` keeps its ledger lines. The draft used dotted module paths, which change on a move.
- Legacy remainder: one package `posthog` (and `ee`), with no rules beyond the cycle rule.
- Test isolation: little leverage. The test selector picks tests from the file-level import map whatever the package; proto packages mostly sit inside the big SCC. The value is direction, purity and migration readiness, not CI time. (The synthesis later corrected this: a named core package with no product edges gives routing leverage.)

Foundation lens: infrastructure sits below foundation products.
One ordered field could replace three direction rules (Lakos levelization): `rank`: infrastructure 0 < foundation 1 < feature 2 < composition 3; legacy and proto unranked.
Rule: an edge never goes to a higher rank (both known).

---

## Widening pass

Written Nov 6, after both first answers were judged too narrow: both had asked "where does this fit in the draft".
This is the outside view, from prior art and the owner's own doctrine.

### Facade: a folder is the wrong core concept

- The owner's catch-all-drawer doctrine: publicness by location rots into a catch-all drawer of models, services and jobs. PostHog facades already showed it (ORM re-exports, a manager class, a test mixin in a facade's testing module).
- Packwerk moved privacy out of core into an extension (Packwerk PR #247). A Packwerk maintainer: "the value proposition of packwerk is in dependencies, and privacy as an option is a nice cherry on top". Packwerk discussion #219 weighed export annotations over a public folder.
- Bazel and Pants: visibility is declared per target, for an audience (`package_group`). Pants 2.16 visibility applies to real imports (inferred dependencies).
- Wider answer: the core concept is a contract surface = door (which symbols, for which audience) + shape (what may cross: data boxes, core-owned ABCs, never ORM). An export with no shape policy is a config error. The word "facade" and its folder stay in the preset. Hoglint's `signature` primitive is the shape check that the doctrine said no static tool has.

### Generated code: in PostHog the strongest contracts are already generated

- `frontend/src/queries/schema/*.ts` → `schema.json` → `posthog/schema.py` (pydantic) is the de facto contract between frontend, backend, MCP and PostHog AI. Serializers → OpenAPI → TS clients and MCP tools is the second one.
- So generated code is not only a hygiene problem (hand edits, side generators); it is where contracts live, and where they are authored decides who owns them (product query types authored in the core frontend).
- Bazel: a generated file has the visibility of the rule that produces it. That is prior art for "a generated output takes the labels of its producer or inputs".
- Wider answer: `generates` as an edge kind in the graph (input → output). Then `compare`, test selection and ownership see codegen; a change to a schema source reaches every consumer through the generated module. Contract-first surfaces (schema → generated DTOs) give shape by construction: the long-term form of a facade.
- Agentic angle: agents edit outputs instead of inputs and add side generators. A non-blocking hook for writes to generated paths exists in the wild. Hoglint can do the diff-based version in `compare`.

### Core packages: the graph is a build graph

- Pants infers dependencies from imports with a Rust parser (2.17) and selects tests from them; Bazel query answers reverse dependencies. Hoglint's L2 is the same graph.
- Concrete test leverage of a pure package: a package with no Django imports runs its tests under plain pytest, with no settings and no database (egress tests used Django's `SimpleTestCase` only for settings overrides). That is a fast test lane, not just a lint rule.
- Wider answer: one dependency graph for the repo (lint, test selection, codegen triggers, review routing), with packages anywhere, not only under `products/`.

---

## The architect's round 4 view

Written Nov 6, before the second opinion's round 4.
The owner's follow-ups: (1) the owner did not like the `[[relocations]]` list; how would it work? (2) Can a lint say a package may use Django but not handle Django models? (3) Lint dynamic loading so it happens one sanctioned way: products register themselves, and no general list hardcodes specific products. (4) The owner liked "one dependency graph".

### 1. Drop `[[relocations]]`

The data model already makes consumer moves free: the growth gate sums per growth key, and growth keys do not contain the consumer's location ("moving or splitting a consumer module carries its lines and raises no debt", `docs/research/data-model.md`; the existing crossings growth check works the same).
Line identity changes, so the regenerated ledger shows lines moving; that is churn, not growth.

What is left is a move of the target.
Fix it with a stable target identity, no list:
- models: identity = `db_table` from `Meta` (231 product models still use a `posthog_` table; moves through `SeparateDatabaseAndState` keep it);
- packages: the package id (stable across a proto-product's move);
- other symbols: `compare` maps old → new with git rename detection (`-M`) on the target's file.
- A file split that rename detection cannot follow fails; land the move (a pure `git mv`) and the refactor as separate PRs. No hand-written mapping.

### 2. "Uses Django, never handles models": yes, as a Django capability level

Django pack levels: settings (`django.conf.settings`) < framework (http, cache, signals, utils, `db.connections`, transaction) < orm-generic (`Model` only as a type for routing, router) < orm (concrete models, managers, querysets, `.objects`, `get_model`).
A package field `django = "framework"`; the rule denies any edge above the level.
Measured: egress, ingress and kafka_client import no `posthog.models`; one ingress helper uses `type[Model]` generically for database routing (orm-generic).

Why it is useful: no tenant-data access, tests need no database (egress had 0 of 14 test files using the database), the package stays off the model import graph (startup budget), and it can be extracted later.
Limit: an untyped parameter that receives a model instance is invisible; annotated ones are caught by `signature`.

### 3. Registries: one sanctioned loader, products register themselves

Measured:
- Good: `posthog.products.load_product_modules("<name>")` discovers `products/*/backend/<name>.py` by convention. 6 call sites: routes (two), webhook consumers, data freshness, integration usages, and home-page highlights. The existing watched-channels lint already treats it as a channel the lints cannot see.
- Bad: the product app list in the web settings hardcodes 91 entries, exactly the 91 product directories with an `apps.py`. The health-check registry hardcodes about 16 product module paths ("Add product health check modules here"). A non-product consumer list names `ee` by hand (a documented exception).
- 74 non-test, non-migration files call `import_module`, `__import__`, `import_string` or `apps.get_model`.

Proposal: a registry as a core concept.

```toml
[registries.webhook_consumers]
loader    = "posthog.products.load_product_modules"
module    = "products/{product}/backend/webhook_consumers.py"
attribute = "WEBHOOK_CONSUMERS"
shape     = "abc:posthog.ingress.contracts.WebhookConsumer"
```

- The loader call resolves to `registers` edges to every matching module (candidates), so dynamic loading is visible to cycles, test selection and isolation (replacing watched channel 3).
- A registered module is an export whose audience is the loader and whose shape is the registry's core-owned ABC (the jacket doctrine: ABCs with `issubclass` bouncers at registries).
- Rules: (a) core never names a specific product, by import or dotted-path string, outside a declared registry (the hardcoded lists are ledgered); (b) a dynamic import (`import_module` and the like) with a non-literal argument only inside a declared loader; (c) every module matching a registry convention exports the attribute in the registry shape.

### 4. One graph

Hoglint's fact store becomes the repo dependency graph: import, dotted-path, registers (registries), generates (codegen) and relation edges, with views.
The CI test selector reads it instead of `tach map`, and registry edges close the gap the watched-channels lint covers by hand.

---

