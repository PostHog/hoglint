# Adversarial reviews of the design

> Six adversarial reviews of the Hoglint design draft, verified against the files and adjudicated by a second opinion from another model, Nov 6, 2026 (follow-up measurement Nov 9).
> Partly replaced: the fixes are in `docs/design/` (mainly `02-policy-exceptions-ledger.md`, `04-rules.md`, `05-engine.md`, `11-repo-and-packaging.md`) and the decisions in `docs/history/decision-log.md` and its continuation files.

This file has the method, the blockers and design fixes with their resolution, and the reports of three reviewers (configuration, core purity, gaming).
`docs/research/adversarial-reviews-2.md` has the other three reviewers (ledger, implementation, replacing older tools), the second opinion's adjudication, and the follow-up producer measurement.

## Method

- Six adversarial reviewers, one per area: the configuration language, gaming and blind spots, the ledger, implementation and shipping, replacing the older tools, and core purity.
- Each reviewer had to attack the design and report at most five findings, each with a severity, a concrete counterexample, the reason the design fails it, and the smallest fix.
- Each reviewer also had to say what holds up.
- The architect verified every finding against the files: the design draft, the data model (`docs/research/data-model.md`), the spikes, and the PostHog monorepo at `d1051a3875f`. Each finding got a verdict: confirmed, plausible (a design judgment, not checkable), or refuted.
- A second opinion from another model then adjudicated the findings that survived verification, and graded each as a blocker before build, a fix in design, or overstated.

The reviewers read the design draft of that date (not published).

## Blockers before build

1. **Unknown semantics** (configuration finding 1, gaming finding 2; found twice, independently).
   Fields set on only part of the repo on purpose (role, zone, stage, external) read as unknown everywhere else, so deny rules flood.
   The foundation-direction rule floods on core targets, and facade-no-drf floods on imports of core in 62 products.
   Unknown in `select` and `from` was undefined.
   **Fix:** each field declares its domain (`required_on`); outside it the value is a known `none`.
   Define unknown per position.
   An unknown selection is incomplete and denies Isolated; it is not a violation.
   Validate field names, enum values, zones and kinds before any run, so typos are config errors.
2. **Preset merge semantics** (configuration finding 2).
   A root redefinition can silently drop Isolated requirements.
   The draft itself redefined `public-surface` without `endpoint`, `kinds` and `identity`.
   **Fix:** duplicate definitions are errors.
   Changes go through explicit replace and list add/remove operations.
   `compare` diffs the expanded config, with the source of each key.
3. **Policy laundering** (ledger finding 2, configuration finding 3D).
   Reassigning a module's package or zone in `hoglint.toml` shrinks debt or opens a door with no exception entry.
   **Fix:** compare old and new policy on the same base code.
   Boundary relabels and weakened protections need the same review as an exceptions addition.
   Check growth under both tool and policy versions.
   This changed the agreed rule "`hoglint.toml` changes are neutral" to "architecture edits have no fixed good or bad direction, but relaxations need review".
   It needed the owner's decision.
4. **Growth identity and the merge queue** (ledger findings 3 and 5).
   Only one rule (model-crossings) declared a growth key, and growth compared ledger files.
   **Fix:** an explicit growth identity on every ledgered rule.
   Growth compares scans of base and head code.
   Head-ledger equality is a separate check.
   Both run on the merge queue's merge candidate.
   The reviewer called the result a "deadlock"; that was overstated, because scanner changes allow recovery.
5. **ORM and DRF shapes in core L1** (core finding 1).
   `lock`, `write` and `drf-model-serializer` were assigned at L1, in "core".
   **Fix:** core emits neutral use positions; the django pack interprets them.
6. **Packaging** (implementation finding 1, partly overstated).
   A Rust workspace member built locally conflicts with the dev environment's cargo 1.91, while the parser crates need 1.94.
   **Fix:** a prebuilt wheel, pinned the way PostHog already pins its `hogql-parser-rs` wheel.
   A pure-Python member may depend on it.

## Design fixes

7. **Installed callbacks have no edge** (gaming finding 1).
   `register_*` hooks and receivers wired from `AppConfig.ready` run one product's logic inside another product's suites.
   **Fix:** extend `registers` (or add `installs`) for declared hook APIs.
   An unresolved callback closure widens test selection.
8. **Dynamic imports and test paths** (gaming findings 2 and 3).
   **Fix:** emit dynamic-import facts, resolve declared loaders, and mark the rest incomplete.
   Prohibit production imports of test-path modules unless reviewed.
   A test path alone never grants an exemption.
9. **Move admission** (ledger finding 1).
   **Fix:** a reviewed boundary-growth admission, only when the same couplings already existed (matched identities, unchanged counts).
   New uses bundled into a move fail.
   No relocation list.
10. **Index mode and cache key** (implementation findings 2 and 3).
    **Fix:** read staged blobs (config too), never the working tree.
    The cache key is content + extractor build + schema + options.
11. **Replacing the older tools** (cutover findings 1, 3 and 4).
    The design lessons: a reader of an old tool's file must fail closed when that file is gone; a shadow phase must compare verdicts and selected tests, not only facts; Hoglint's own inputs count as graph changes; switching and deleting are separate steps with a tested rollback.

## Overstated or wrong

- The ledger reviewer opened with "the existing crossings growth gate never ran in CI". Verification refuted it: the reviewer searched the wrong directory, and the CI config did set the gate's base on pull requests (since Oct 7). The reviewer's replay ("8 of 65 ledger-touching commits would have failed") used a commit from before the gate existed, so it says nothing about the gate as it ran.
- Core findings 3, 4 and 5 (exports, registries and provenance should leave core): overstated. Keep them as core declarations, move framework semantics into packs, add cross-repo fixtures and a language-qualified entity seam.

## Own errors the review caught in the draft

- The draft's `public-surface` rule lost the `dst.tier == 'product'` guard.
- The fields `stage`, `django`, `registry` and `generated` were used without a declaration.
- Nested globs (`posthog/**` against `posthog/egress/**`) conflict under the projection model's own rule that conflicts fail.

All three were the architect's errors in writing the draft.

---

## Reviewer: the configuration language

Scope: parts of the design draft (not published); the projection model (`docs/research/generality.md`); `docs/research/exports-and-shapes.md`; `docs/research/depends-on.md`.
Repo counts are non-test `.py` files at `d1051a3875f`.

### 1. Sparse fields read as unknown: the preset's own deny rules flood, and unknown in `select` is undefined (blocker; confirmed)

Feature attacked: three-valued selectors ("a missing field is unknown, not false", "unknown never escapes a deny").
Many fields are set on only part of the repo on purpose: zone, role, stage, external, registry.

**Counterexample A: the foundation rule as written.**

```toml
# presets/posthog.toml
role = { type = "enum", values = ["foundation", "feature"] }   # products only
[rules.foundation-direction]
primitive = "edge"
view      = "packages"
from      = "src.role == 'foundation'"
deny      = "dst.role == 'feature'"
```

- An edge from a foundation product into core has `dst.role` unknown, because no assignment sets role on `posthog/**`.
- So the deny evaluates to unknown, which produces "finding: unassigned".
- The three foundation products import `posthog.*` in 51 files between them.
- The draft said this rule had 0 edges. Under the draft's own semantics, it has one finding per foundation-to-core pair, plus one per external import if externals have no role.
- What the author wanted: core has no role, so it is "not a feature".

**Counterexample B: facade-no-drf feeds the Isolated level.**

`deny = "dst.external == 'rest_framework' or dst.zone == 'presentation'"`

- Take a facade that imports `posthog.models.Team`. The destination is first-party, so `external` is missing and evaluates to unknown.
- Core is unzoned on purpose ("Core is mixed. Only inspected parts get a zone; the rest stays unassigned").
- So the deny is `unknown or unknown`, which is unknown, which is a finding.
- 206 facade files in 62 of the 77 products that have a `backend/facade/` directory import `posthog.*`.
- facade-no-drf is in `levels.isolated.requires`, and that level sets `unknown = "fails"`. So these 62 products can never be Isolated.

Other rules break the same way:
- **infrastructure-direction:** `deny = "dst.tier == 'product' or dst.stage == 'proto-product'"`. stage is set only on proto-products. 61 of the 102 infrastructure files import `posthog.*`, and each such edge evaluates to `false or unknown`. The draft estimated about 1 edge.
- **owners-yaml-leaf:** flags every import inside the leaf package, because `external` is missing on those edges.
- **contracts-no-django:** `deny = "dst.external == 'django'"` is unknown on every first-party node the reach visits.

**Counterexample C: unknown in the select position.**

The draft defined unknown only for deny and allow.
But several rules put the boundary test in `select` or `from`: public-surface (`select = "src.package != dst.package and dst.tier == 'product'"`), model-crossings, drives, and reverse-accessor, which has no deny at all.
Each of the two possible readings breaks something:
- **If unknown selects:** `from = "src.zone == 'infrastructure'"` selects every unzoned core module. 523 core files import products, and each of those edges is then checked by infrastructure-direction.
- **If unknown does not select:** no assignment matches `bin/`. Two scripts there use other products' code, one of them writing to another product's model, and both drop out of public-surface and model-crossings without a finding. This is the Saleor silent-clean failure (`docs/research/generality.md`) in a new position.

The draft already worked around the gap in two ways:
- `src.registry == unknown` used unknown to mean "not in a registry". So "absent by design" and "nobody assigned it" were the same value.
- `orm-independent` added a per-rule `on_unknown = "incomplete"`. So each rule picked its own unknown policy.

**Smallest fix.**
- Each field declares its domain: `required_on = "<entity selector>"` (this key already existed for Sentry's silo), and `absent = "none"` for entities outside that domain.
- A missing value inside the domain is unknown. Outside the domain it is the known value `none`. `external` is a built-in field whose value is `none` for first-party destinations.
- The spec defines unknown in every position:
  - `select`, `from`, `audience`: an edge is selected and reported as unassigned only when the field is required on that entity; otherwise it is not selected;
  - `unless` and `allow`: unknown never exempts;
  - `deny` and `require`: unknown is a finding.
- Drop the per-rule `on_unknown` key.
- `hoglint check --config` prints, for each rule, how many edges evaluate to unknown and on which field. It fails when a field yields unknown outside its required domain.

### 2. Presets have no merge rule, so a one-line root edit can shrink the Isolated level (blocker; confirmed)

Feature attacked: `extends`, presets expanding into the root config, levels, and the review split between `hoglint.toml` and the exceptions file.

```toml
# hoglint.toml (root). Author intent: add one requirement to Isolated.
[levels.isolated]
requires = ["no-new-package-cycles"]
```

- The draft said only "extends pulls in packs and presets" and "presets only expand assignments, views and rules". Nothing said what happens when the root defines a table that a preset also defines.
- **Replace semantics:** `requires` now holds one rule. The eight rules that make the test skip safe are gone.
- **Deep merge:** the result depends on whether arrays replace or append. If arrays replace, the outcome is the same as above. If arrays append, the root can never remove a requirement or narrow a selector.
- The draft classified every `hoglint.toml` change as neutral in both directions. Only additions to the exceptions file were "always bad" and needed tool-owner review. So the edit that removes the skip's safety got the lighter review path.

The draft already did this to itself.
The draft showed `[rules.public-surface]` with only `primitive`, `select` and `allow`.
Replace semantics silently drop five things from the draft's earlier version: `endpoint = "surface"`, `kinds`, `identity = "module-symbol"`, `on_unresolved`, and the `dst.tier == 'product'` guard.
Merge semantics keep the old `kinds` and `endpoint` under a new `select`.
Neither outcome was stated.

The same gap affects two sources defining one field (the preset declares `database`, which its own comment says the django pack fills), and disabling a preset rule (the only way was to redefine it).

**Smallest fix.**
- Redefining a table that an `extends` source defines is a config error.
- Changes go through explicit operations, for example `[patch.levels.isolated] requires.add = [...]` and `requires.remove = [...]`, or `[patch.rules.X] set = { ... }`.
- `hoglint explain config` prints the expanded config and the source of each key.
- `compare` diffs the expanded config, not the files. Any change that removes a level requirement, narrows a select, or adds an `unless` is classified like an exception addition, not as neutral.

### 3. Assignment conflicts: the draft's own config is invalid, `override` has no target, and relabeling bypasses the exceptions file (major; confirmed)

Feature attacked: `[[assign]]` with globs, "no implicit precedence", `override = true`, and hierarchical zones.

**A: nested globs conflict.**

```toml
[[assign]]
match = ["posthog/**", "ee/**", "common/**"]
set   = { package = "core", tier = "core" }

[[assign]]
match = "posthog/egress/**"
set   = { package = "egress", zone = "infrastructure" }
```

The projection model says "Conflicts fail unless an explicit override resolves them. No implicit precedence."
So this is a config error on every egress file, and every named core package needs `override = true`.
What the author wanted: the more specific glob wins.

**B: sibling zones.**
A `tests` zone (`**/tests/**`, `**/test_*.py`) next to a `backend` zone with sub-zones means 3,168 test files under product backends match two zone chains, 75 of them under a backend sub-zone.
Either each is a conflict error, or an unstated rule picks one zone: if the deepest zone wins, `tests` never applies inside backend; if `tests` wins, a test under `backend/facade/tests/` stops being facade for every zone rule.
Tests already have their own axis: `context = test`.

**C: two overrides with no order.**
A preset override sets `role = "foundation"` on a product, and a root override sets `role = "feature"` on a subdirectory of it.
Nothing orders them.
File order across presets is undefined.

**D: override bypasses the exceptions file.**

```toml
[[assign]]
match    = "products/x/backend/evaluation/**"
set      = { zone = "facade" }
override = true
```

public-surface now allows every import of x's evaluation internals.
This adds no exception entry and no ledger line, and the `hoglint.toml` diff counted as neutral.
The exceptions file is shrink-only and needs review on additions; relabeling gets around it.

**Smallest fix.**
- Path assignments resolve by specificity: per field, the match with the longest literal path prefix wins. Equal specificity with different values is an error that names both assignments.
- `override` stays only for conflicts between a glob and a provider or decorator source. It names what it overrides: assignments get an `id`, and `overrides = ["id"]` lists the ids.
- Zones: each file gets one zone from one parent chain. A match on two chains is an error that shows both paths. Drop `tests` as a zone.
- An assignment that changes `zone`, `package` or `role` under a product root counts in `compare` as a policy relaxation, like an exception.

### 4. Exports: the draft's public-surface flags every product-to-core import, and a mandatory shape cannot describe all the doors (major; confirmed)

Feature attacked: exports with audiences and shape policies, and how they interact with zones, core packages and registries.

```toml
[exports.facade]
within   = "products/{product}"
paths    = ["backend/facade/**"]
audience = "*"
shape    = "contract"

[rules.public-surface]
primitive = "entrypoint"
select    = "src.package != dst.package"
allow     = "dst.exported"
```

Without the `dst.tier == 'product'` guard, every edge from a product into package `core` is selected: 4,457 product files import `posthog.*`, and core has no export, so each edge is a finding.

Giving core an export does not work either.
`contract` denies Django models, and `posthog.models` holds Django models.
The only way through is a shape with no deny and `unknown = "allow"`, which turns the "mandatory" shape into a formality.

Presentation adapters hit the same wall.
An access-control viewset mixin subclasses DRF's `GenericViewSet`, and its module imports `django.db.models.Model` and the core `User` model.
The shape vocabulary (frozen-dataclass, abc, django-model, manager, queryset) has no term for a DRF mixin, so the recursive check either fails or the adapter export gets a permissive shape.

Interaction with zones: the door was declared twice, as a zone (driving facade-no-drf, contracts-no-django and facade-signature) and as an export path (driving public-surface).
If a new facade directory is added to the export only, other packages may call code that no facade rule checks.

Interaction with registries: registries used a second shape syntax (`abc:posthog...`), and a registry module is not an export.
A `registers` edge into a product's registry module is a public-surface finding if `registers` is in the rule's kinds, and the draft rule omitted `kinds`.

**Smallest fix.**
- An export binds to a package selector and a zone. It does not take paths. Zones stay the only path vocabulary.
- public-surface selects only destinations whose package declares at least one export. Packages without an export are listed as uncovered, not flooded.
- Exports and registries share one named shape table. `unchecked` is an explicit shape: `compare` shows it, and it fails the Isolated level.

### 5. Nothing checks selectors or ids against the declared schema (major; confirmed)

Counterexamples from the draft itself:
- **Undeclared fields.** The draft used `dst.stage`, `src.django`, `src.registry` and `generated`. None was in `[fields]`. Under finding 1, an undeclared field is unknown everywhere.
- **Zone values not in `[zones]`.** The root config set `zone = "infrastructure"` and `zone = "composition"`, but neither was declared. Either the config is invalid, or the zone field accepts any string. If it accepts any string, a typo such as `from = "src.zone == 'infrastucture'"` selects nothing, and the rule stays clean forever.
- **Kinds not in `[kinds]`.** `orm-independent` read `dst.kind in ['model', 'manager', 'queryset']`, but `[kinds]` declared only `model`.
- **Ordered values compared with `==`.** The draft defined the Django levels as `settings < framework < orm`, then wrote `select = "src.django == 'framework'"`. Label a module `settings`, which is stricter, and orm-independent stops checking it. A stricter label gives weaker enforcement. The language had no `<` or `<=`.
- **Implicit package join.** Assigning a legacy directory to a proto-product package that carries its future product name joins it with the real product of that name. The package then has members with tier `product` and tier `core`, and the design did not define a group's tier when its members disagree. Edges between the two halves become intra-package, so public-surface and model-crossings no longer see them.
- **Scalar field read as a set.** `migration-database` read `src.aliases subset_of dst.aliases`, but the preset declared `database` as a single string, and no `aliases` field was declared.

**Smallest fix:** validate the config before any run, with messages that name the file and line:
- an unknown field name in a selector is an error;
- a literal that is not in its enum, zone or kind declaration is an error;
- `==` or `!=` on a set field, and `subset_of` on a scalar, are errors;
- ordered enums get `<` and `<=`; `==` on an ordered field in `select` or `from` is a warning;
- a package id produced by two assignment roots is an error, unless it is declared, for example `[packages.billing] roots = [...]`;
- package-scoped fields (tier, role, stage) must agree across a package's members;
- a rule whose select matches zero edges on the current tree is a warning in `check`, and an error in CI for any rule a level requires.

### What holds up

The split into facts, policy and debt holds under attack.
Assignments label entities, rules judge labelled edges, and the ledger only accepts findings.
The four repo configs show that this generalizes without engine changes.
Each rule names the endpoint role it judges (surface, via hop or origin), which removes a real class of re-export ambiguity.
There is no `ignore` option for unresolved edges, and incomplete exits 2.
`owners` is kept apart from `package`, so a team change never moves a boundary.
Every problem sits in the resolution rules between these pieces: unknown in each position, preset merge, assignment precedence, and schema checks.
Each fix is a spec decision plus a validator, not a redesign.

---

## Reviewer: core purity

Question: is the Hoglint core generic, and is it the right core?
Scope: parts of the design draft (not published); the generality study (`docs/research/generality.md`: projection model, Saleor, Zulip, Sentry); `docs/research/exports-and-shapes.md`; `docs/research/research-round-1-prior-art.md`.

Test repos: Saleor, Zulip and Sentry, plus one non-Django shape.
That shape is a uv-workspace library monorepo: `libs/acme-core/src/acme/core/`, `libs/acme-http/src/acme/http/`, `apps/api/src/acme_api/`.
`acme` is a PEP 420 namespace.
Each member has its own `tests/` with no `__init__.py`, because pytest runs with `importmode=importlib`.
The dependencies of each member are in its `pyproject.toml`.

### 1. Use shapes are Django ORM and DRF vocabulary, assigned in the core Rust extractor (blocker; confirmed)

- The `Shape` fact on `reference` edges was assigned by the core resolver at L1, "from syntax only".
- The vocabulary was `instance-single | instance-many | write | lock | scalar-chain | subquery | meta | nested-class-attr | drf-model-serializer | drf-field-queryset | passed-as-class | construct | ...`.
- The spike classifier hard-coded ORM and DRF syntax: `select_for_update` → lock, `_meta`, `DoesNotExist`, `Prefetch`, enclosing `*Field`. It read any `model = X` assignment as `drf-model-serializer`.
- SQLAlchemy and FastAPI: `session.execute(select(M).where(...).with_for_update())` classifies as `passed-as-class(select)`, so the lock is invisible. A generic CRUD router `model = Item` reports as a DRF serializer.
- The Zulip sketch used `deny_shapes = ["call"]`, but `call` was not in the vocabulary. A function called by name fell into `construct`. Saleor's sketch used `call(connect)` and `decorate`, also not in the vocabulary. The generality checks wrote rules against shapes that did not exist, so the gap did not show.

Why it fails: core "never imports Django" held for imports but not for semantics.
Every repo got PostHog's crossings-ledger taxonomy as the meaning of "how is this symbol used".
The shape strings also went into ledger identities (`instance-many(filter)`), so a later fix changes persisted keys.

Smallest fix: L1 in core emits a neutral use record: position (annotation, call-func, call-arg, keyword, base, decorator, isinstance arg, raise/except), the attribute and method chain after the symbol, and the enclosing call.
Core ships only generic shapes: `call`, `construct`, `subclass`, `decorate`, `annotation`, `isinstance`, `attribute`.
The chain-to-named-shape table, with its precedence, moves to the django pack (ORM shapes, `lock` and `write` precedence, the def-use refinement); DRF shapes go to a DRF table in the same pack.
Version the table as a producer, so a table change counts as rule growth, not code growth.

### 2. Module identity is "one dotted name per file under literal source roots", and package identity has no manifest provider (major; namespace part confirmed)

- The projection model said "reject ambiguous module identities". In the workspace repo every member's `tests/test_client.py` is `tests.test_client`, and `acme` exists under N roots. Both are legitimate Python (importlib mode, PEP 420), and both get rejected.
- The resolver behind the tach-map parity target resolved a namespace package without `__init__` to nothing, so every `from acme.core import x` is lost or unresolved.
- `source_roots` was a literal list. Nothing said it accepts `libs/*/src` or reads workspace members.
- PostHog has the same problem: its uv workspace has pure-Python members, one in src layout. With `source_roots = ["."]`, an import of a workspace member resolves to site-packages or nothing. That is the tach blind spot recorded in `docs/research/research-round-1-contrarian.md` ("tach resolves a uv distribution as third-party and records no edge"), and Hoglint inherited it.
- Core shipped `owners-yaml`, a PostHog format, as a provider, and nothing for `pyproject.toml` (`[project].name`, `dependencies`, `[tool.uv.sources]`, workspace members). In a library monorepo, the main boundary rule is "import only what your distribution declares", and no provider fills the declared-deps set.

Smallest fix: entity identity becomes the repo-relative path, and the dotted name is a resolution alias.
Ambiguity becomes a finding on the import that resolves ambiguously, not on the file.
Namespace packages resolve by merging all roots (PEP 420 order).
`source_roots` accepts globs and a `workspace` source that reads uv, poetry or pants members.
Move `owners-yaml` to the PostHog preset as a provider plugin.
Add a core `pyproject` provider that sets `package` (distribution name) and `declared_deps`.

### 3. `exports` re-declares zones, three primitives and `kinds`, and the mandatory shape is PostHog doctrine (major; plausible; adjudicated as overstated)

- **Duplication.** The public surface could be written two ways: `allow = "dst.zone in ['facade','presentation']"` or `allow = "dst.exported"`. The export's parts already exist as primitives: the audience is an `entrypoint` predicate; "hands out a denied kind through re-exports" is the `export` primitive; "signature mentions a denied type" is `signature`; shapes duplicate `[kinds]` with a second, built-in vocabulary. Registries then add a third syntax.
- **Closed shape vocabulary.** Sentry's real door is `rpc_service` / `rpc_model`, and `RpcModel` is a pydantic `BaseModel`, not a frozen dataclass. The vocabulary had no name for it. An older Sentry sketch had RPC zones, but the later Sentry tab dropped RPC, so the new concept was never tested against the one outside repo with a real door.
- **Mandate.** Saleor and Zulip have no shaped door: Saleor's public surface is its GraphQL schema, and Zulip's `zerver/models/__init__.py` is a re-export hub with 482 importers. If they declare any export, they must invent a shape policy or write `any`. The mandate is then ceremony that the owner's catch-all-drawer doctrine imposes on every repo.

Smallest fix proposed: make `export` a preset-level macro that compiles to a zone label, an `entrypoint` rule and an `export` + `signature` pair with a recursive deny; make shapes ordinary `kinds`; make "an export without a shape is an error" a preset validation (`require_shape = true`) that PostHog turns on; if core needs one new thing, it is a recursive type walk for `signature`.
The second opinion kept exports as a core declaration (see the adjudication in `docs/research/adversarial-reviews-2.md`).

### 4. Registries are `load_product_modules` in core, and cannot express the registries the generality checks found (major; plausible; adjudicated as overstated)

- Saleor: plugins are dotted strings in settings plus `importlib.metadata` entry points: a key list, not a loader with a module template.
- Sentry: registry membership by an in-code tuple, decorator push-registration, and settings-driven backends through `import_string`. Under "computed import only inside a declared loader", every `import_string` site becomes a finding unless each one is declared as a loader.
- Zulip: registry keys from decorator args (worker queues).
- Django itself: `autodiscover_modules("admin")` and Celery `autodiscover_tasks` pull `<app>/x.py` per installed app, not per path glob, and the loader is in site-packages.
- The draft contradicted itself: the edge-kind table said `registry` edges come from a preset adapter; another section made registry a core concept.

The core shape was PostHog's pull-by-filename convention, one of at least four registration patterns: file-convention pull, settings or tuple key list, decorator push, and packaging entry points.
Smallest fix proposed: core holds only the `registers` edge kind (keys resolve with `candidates` semantics) and the rule pattern "dynamic import only from a site that produces `registers` edges"; producers live in packs (django autodiscover, settings and entry-point key lists, decorator push, and `load_product_modules` in the PostHog preset); the registered thing's shape reuses `kinds`.

### 5. Generated provenance is another path-template mechanism, and the language pack it relies on does not exist (minor; plausible; adjudicated as overstated)

- Most of PostHog's generated inventory is TypeScript (278 + 176 files of OpenAPI and MCP clients). Saleor's `schema.graphql` is a non-Python output of Python inputs.
- The draft had no language pack: the L1 extractor, the resolver and dotted module identity all sat in core, so core *was* the Python pack. Entity ids were Python dotted names, which a TS or Rust pack cannot fill without a new identity scheme and ledger churn.
- Each part of provenance maps onto something core already has: one producer per file is an `inventory` rule; "finding goes to its source" is owner routing; producer to output is a `generates` edge kind; inputs are a provider.
- Count the ways core says "these files, under a template root": `[[assign]] match`, `[zones] within/paths`, `[exports] within/paths`, `[registries] module = ...`, generated outputs. That is five. A second repo learns five selection mechanisms and three shape syntaxes before it writes one rule.

Smallest fix: provenance becomes a `generated` provider plus a `generates` edge kind.
Put the language seam into the core now: entity id = (language, repo path, symbol path), with Python as the first language pack behind that seam.
Cross-language rules stay `incomplete` until a pack exists.
Exports, registries and generated outputs select files through `[[assign]]` and selectors, never through their own `within/paths`.

### What holds up

The lower core is sound and repo-neutral: facts to edges with surface, via and origin; the resolution and context states; three-valued selectors with "unknown never escapes a deny"; select-then-group views; the ledger with exact equality plus attribution growth; and the clean, violations, incomplete result.
All of it survived Saleor, Zulip and Sentry unchanged, and each part answers a failure in the first prior-art round (Packwerk stale todos, ArchUnit orphaned stores, Shopify "zero violations is not isolated").
Moving packages, zones, tiers and roles out of core into labels was the right move.
The django pack boundary is mostly right.
The problems sit where newer work (the shape vocabulary from the crossings spikes, and the draft's later additions) went into core straight from PostHog's code, without the same four-repo test.

---

## Reviewer: gaming and blind spots

Stance: an agent under deadline that must land a coupling with no finding and no ledger growth.
Scope: parts of the design draft (not published); the data model; `docs/research/exports-and-shapes.md`; the existing watched-channels lint.

### 1. Installed callbacks run product code inside other suites with no edge (blocker; confirmed)

```python
# products/x/backend/apps.py  (x is Isolated; apps.py and logic/ are not test inputs)
def ready(self):
    from products.y.backend.facade.hooks import register_thing
    def _cb(team_id):
        from products.x.backend.logic.impl import do_it
        return do_it(team_id)
    register_thing(_cb)          # y's code, in y's suites, now runs x.logic
```

What the design did: silent miss.
- No edge kind meant "package A's code calls a callable that package B installed". `drives` covered only tests that execute wiring locations.
- Signals appeared only in a wish list. `registers` was pull-only.
- The pattern is the sanctioned "flip the direction" remedy, so it keeps growing. A callback channel like this can run one product's logic in another product's test suites, so a test skip must account for it.

Smallest fix:
- Add an `installs` edge. L1 records each callable or object passed as an argument to a call that resolves into another package, plus `@receiver` / `.connect` senders and assignments to a foreign module attribute.
- L2 derives the reversed `runs-in` edge, from the host package to the callback, and the drives call view supplies the callback's own-package call closure.
- `wiring-watched` covers drives plus the `runs-in` closure, and the Isolated level reads it.
- An argument the extractor cannot name gives `candidates` (every callable in that scope), never nothing.
- Treat `AppConfig.ready()` and every module it imports as a declared installation site.

The second opinion agreed, and added: extending `registers` may suffice; recognize declared APIs, not every `register_*` name; an unresolved callback closure must widen test selection.

### 2. Computed imports produce no fact, so `on_unresolved` never fires; `select` has no unknown semantics (major; confirmed)

```python
mod = importlib.import_module(".".join(["products", "x", "backend", "logic"]))
mod.recompute(team)
# or: import_string(settings.SOME_HANDLER), or getattr(import_module(f"products.{p}.backend.logic"), name)
```

- L1 `StringRef` read literals only, and `Body.calls` recorded callee names, not argument shapes. So a computed `import_module` made no binding, no StringRef and no edge.
- The design treated unknowns well only after an edge exists. An absent edge never reaches `unresolved`, and the run cannot even report `incomplete`.
- Second hole: if a `select` with an unknown value drops the edge (the usual WHERE semantics), every unresolved cross-package reference falls out before `on_unresolved` applies.
- 35 production files called `import_module`, `import_string` or `__import__` with a non-literal argument, with computed import arguments.

Smallest fix: an L1 fact `DynamicImport{site, callee, arg_expr, literal_prefix?}` for `import_module`, `import_string`, `__import__` and `getattr` on a module object.
L2 emits an edge: with a literal prefix it resolves to `candidates` (every module under that prefix); without one, it is `unresolved`.
Outside a declared registry loader the rule is `on_unresolved = "block"`, so the ledger cannot accept it.
Spec line: unknown in `select`/`from` counts as selected, and `on_unresolved` is evaluated before selection narrows.

### 3. Test context comes from the path alone (major; confirmed)

A tool that derives test context from the path alone lets production code under a test-like name escape production rules.

The draft's contexts table took `test` out of dependency checks.
Only `public-surface` still looked at these modules, and only at their imports; a write, lock or construct on a foreign model there was never a finding.
Production code that imports a test-path module would then escape every production rule.

Smallest fix proposed: derive context from reachability (one BFS over file imports from production roots); a test-path module reached from production gets context `prod` plus a `test-path-in-prod` finding.
The second opinion found full reachability excessive as a first fix: prohibit production-to-test imports unless reviewed, and keep production treatment across those edges.

### 4. Lazy `__getattr__` export maps decide shape and origin by guessing (major; plausible)

```python
# products/x/backend/facade/api.py
_WHERE = {n: "models.thing" for n in ("Thing",)}
def __getattr__(name):
    return getattr(importlib.import_module(_base() + _WHERE[name]), name)

# consumer, in another package
from products.x.backend.facade.api import Thing
Thing.objects.filter(team=t).update(state="x")
```

- `entrypoint` judges the surface, which is `facade`, so it passes.
- Exports were "named symbols or paths", and the shape check walked what the facade declares. A lazy name is never declared.
- An earlier spike resolved map values by a heuristic ("bare, prefix constant, backend-relative") without reading the `__getattr__` body.
- Nothing said what an unread map yields. If the resolver falls back to the module, model-crossings sees the facade module as `dst` (not a model), and facade-signature sees nothing.
- 13 product facades and 5 core modules defined a module-level `__getattr__`, in at least six spellings: a prefix plus module, a conditional mix of absolute and relative paths, an absolute map, an f-string, a relative import plus a `globals()` cache, and an import in the body.

Smallest fix: treat a module-level `__getattr__` as a dynamic export, resolved by reading the body's `import_module` argument against the map.
When the map cannot be read, every name imported from that module resolves `unresolved`, never to the module.
Run the export shape check on the origin of every edge whose surface is an export, so it covers the names consumers actually import.

### 5. Growth keys without the consumer package let debt change hands (minor; confirmed by design)

Delete one disallowed use of `(Thing, instance-many(filter))` anywhere, and in the same PR add the same use from a brand-new consumer package.
Or consolidate two counted uses into one helper call and spend the freed unit elsewhere.
`growth_key = ["dst.symbol", "shape"]` sums over all consumers, so growth is 0 and equality passes after a regenerate.
`compare` listed the added lines, but nothing failed them.

Smallest fix: add `src.package` to the crossings growth key; a move stays free inside a package, and a cross-package move is free only with git rename evidence for the consumer file.

### Routes the design closes well

- Literal dotted paths in patch targets, Celery beat entries and settings become `dotted-path` edges, judged in test context too.
- `apps.get_model` with literal labels resolves; the rest becomes `unresolved-*` findings.
- Re-export hops cannot launder an import, because `entrypoint` reads the surface and `export` reads the via chain.
- `TYPE_CHECKING` imports still count for `entrypoint`, and function-local and conditional imports stay edges.
- One-function def-use catches writes and locks through local variables.
- Runtime registry keys become `candidates` for the whole registry, and bare-name `drives` over-counts instead of under-counting.
- Exact ledger equality blocks hand edits, and attribution separates scanner changes from code growth.
- Unassigned zones inside a product produce a finding rather than a pass.

The gaps all sit where code runs without anything naming it: installed callbacks, computed imports, production code that looks like a test, and names that exist only at runtime.
