# Generality checks: Sentry, Saleor, Zulip, and the projection layer

> Generality checks of the hoglint spike engine on three public Django repos (Sentry, Saleor, Zulip), and the projection layer (L3) designed from them, Nov 4, 2026. The config sketches for the three repos are in `docs/research/generality-2.md`.
> Partly replaced: the projection layer and rules are now in `docs/design/04-rules.md`, and the engine in `docs/design/05-engine.md`.

The v0 data model (`docs/research/data-model.md`) and the spikes were built against PostHog.
To find PostHog assumptions in the "generic" code, the architect ran the spike engine (Rust L1 + Python L2) on three other public Django repos, with no oracle (no Django boot), and sketched each repo's own boundary rules as hoglint config.
Each check below lists what ran, the PostHog assumptions and bugs it found, the repo's own rules, what fits the model, and what is new.
After the checks, the architect drafted a projection layer, and a second opinion from another model designed one independently from the same evidence; the merged result is "Projection layer v1" below.

## Sentry (Nov 4)

**Ran.** Rust L1 on 8,247 files in 0.8 s, with no code change. Import map: 51,766 edges, but only after rewriting `src/x` -> `x` (no source roots); 1 of 73,592 bindings unresolved. 326 models, 419 relations. Migrations: 279 nodes / 323 edges (with a replacement `run()`). No oracle.

**Assumptions and bugs.** Discovery excludes and `FIRST_PARTY` defaults are hard-coded, with no `--exclude`. The module name is the repo-relative path (no source roots). The app registry reader misses an annotated `INSTALLED_APPS: tuple[...] = (...)`. The owner rule is "`products.<x>`, else core". The migrations `run()` hard-codes the PostHog settings and `db_routing.yaml`. The settings reader skips chained assignments (4 unresolved swappable dependencies). Namespace-package apps raise. `SETTINGS_PROXIES` and `SOCIAL_AUTH_` are PostHog adapter tables.

**Sentry's rules.** Silo placement on every model (decorators) and no cross-silo FK (runtime tests). Statically there are 0 cross-silo FKs, which matches their empty exemption set. `HybridCloudForeignKey` (284 uses) is the legal cross-silo reference. The RPC services layout is `<domain>/services/<svc>/{service,model,impl,serial}.py` (10 `impl` imports from outside, 18 `model.py` files import Django models: unenforced). Silo decorators on endpoints (643). flake8 import rules S005, S007, S017. A mypy stronglist/weaklist ratchet, and a CODEOWNERS coverage baseline.

**Fits.** Package globs, optional zones, forbid, layers/public for the RPC zones, symbol kinds, the ledger, levels (the stronglist).

**New.** Source roots. Symbol attributes from decorators (silo: 8 of 33 domains mix silos, so package attributes are wrong). Same-attribute rules over relation edges with symbol attributes. Edge-attribute rules. Decorators that are module-level instances. Registry membership vs an in-code tuple. File-level ownership from CODEOWNERS for flat drawers. A cross-repo consumer (getsentry).

**Lacks.** Manifests, `depends_on`, public surfaces, facades, one app per package, a database alias per app.

The main boundary is a symbol attribute, not a directory.

## Saleor (Nov 4)

**Ran.** Rust L1 on 4,374 files, 0 parse failures, 0.11 s, with no extractor change. Generic L2: 17,732 import edges, 114 models, 248 relations, a migration graph of 1,446 nodes / 1,907 edges / 27 apps. No oracle (no Django boot); PostHog's site-packages facts were borrowed.

**PostHog assumptions found in the "generic" code:**

- `FIRST_PARTY_ROOTS` and `SETTINGS_MODULE` constants (in the core and relations modules); `migrations.run` hard-codes the PostHog settings and `db_routing.yaml`.
- Rust discovery hard-codes excludes (`docs/`, dot directories, PostHog's evals); no `--exclude`; it needs `git ls-files`.
- The app resolver raises `LookupError` on an installed app without facts (a crash, not `unresolved`).
- The settings reader misses `INSTALLED_APPS += [...]`, conditional context, and entry-point apps.
- Worst: `reverse_accessor_edges` used `model_id.startswith("products.")` as the boundary: 0 lines on Saleor, although 144 relations cross a domain. A silent "clean" that means "cannot see your packages".
- The L1 `MAX_STR=100` cap truncates migration names: 2 parent edges lost. Migration keys, labels and dotted paths need an uncapped string role (a generic bug).
- `module_of_path` assumes repo root = source root (a src layout breaks).

**Saleor's policy** (prose in their `AGENTS.md`): non-GraphQL code never imports graphql (73 production edges violated it at the time); `core.development` only from tests and `populatedb`; no signals except `post_migrate`; plugin events go through `call_event`; imports at the top level.

**Fits.** Edge rules with `skip_from` tests, forbid/`except_from`, symbol kinds, a registry with all-candidates, generated debt, the `function_local` context.

**New concepts.** A repo-wide layer axis (graphql vs domain) that cuts across packages. Packages from several globs joined by a capture (`saleor/{d}` + `saleor/graphql/{d}`). Receiver typing from annotations (`manager: PluginsManager`).

**Not present in Saleor.** Manifests, `depends_on`, public zones, facades, levels, a per-app database, one directory per package.

Domain graph: one SCC of 52 of 58 nodes; `depends_on` debt would be the whole matrix.

**Requirements.** An ownership projection that every channel calls, with an explicit "unowned"; multi-glob packages; a layer axis with selectors `layer:X & package:Y`; rules with no packages at all; excludes and first-party roots in config for L1 and L2; the target repo's own venv facts.

## Zulip (Nov 4)

**Ran.** Rust L1 on 2,046 files in 0.19 s, with no code change. Import map: 6,141 edges. 67 models / 192 relations (26 models lost: apps are added via `INSTALLED_APPS += EXTRA_INSTALLED_APPS`). Migrations: 305 nodes / 320 edges after a wrapper fix. No oracle.

**PostHog assumptions and bugs in the "generic" code:**

- Discovery skips every `docs/` directory (Zulip's `docs/` is a package); a hard-coded evals exclude; no exclude flag; `git ls-files *.py` misses 79 shebang scripts; default first-party roots are hard-coded.
- `module_of_path` / `file_module_path` cut the last 3 characters blindly (`tools/lint` -> `tools.l`): a generic bug.
- Relations: `FIRST_PARTY_ROOTS` and `SETTINGS_MODULE` hard-coded; the settings reader misses augmented assignments of cross-module names and dev/prod variants; owner = `products.<x>`, else core -> 0 reverse-accessor lines.
- Migrations: reads `posthog/settings` + `ee/settings.py` (`KeyError`), needs `db_routing.yaml`, has the oauth2 proxy table built in; the AppConfig reader reads only `=`, so `name: str = "zerver"` drops zerver (a generic bug).
- The L1 value cap drops 2 long migration names.

**Zulip's rules.** Prose (`send_event_on_commit` callers live in `zerver/actions`) plus 113 regex rules in `tools/linter_lib/custom_check.py`.

**Fits.** Zones as layers inside `zerver`; forbid with symbol, use-shape and except paths (a typed upgrade of their regexes); symbol kinds by decorator; counted debt.

**New concepts.** A call-site rule (a symbol is only called from a zone). Optional packages (`corporate`, `zilencer`) reachable only via function-local or settings-gated imports. Settings variants. Extensionless scripts. Registry keys from decorator arguments. Dotted-path templates.

**Unused.** Manifests, `depends_on`, public, facades, signatures, exports, levels, surface coverage, the database attribute, product globs.

The layers are not clean: lib -> actions has 40 edges, models -> lib 29; `zerver/lib` is two layers mixed (it needs sub-zones or per-file overrides).
`zerver/models/__init__.py` is a re-export hub (74 names, 482 importers): the surface/via split in the edge record covers it.

**Requirements.** A package can be any folder, and one package is fine; zones without a product template (globs, nesting, per-file overrides); same-package layer rules; public surfaces optional; context-gated allows as policy; discovery config with no built-in names.

## The architect's projection draft (Nov 4, before reading the second opinion)

Problem: L3 v0 = "package (glob) + zone (per-package template) + levels".
PostHog fits.
Saleor needs a cross-package layer axis and two-tree packages.
Zulip needs layers inside one package and no packages.
Sentry's main boundary is a symbol attribute (silo), not a directory.

### One concept: labels

Every module and every symbol carries a set of labels `key=value`.
Package, zone, layer, owner, silo and database are all just label keys.
Nothing is built in.

Label sources (config, evaluated in order, later wins for the same key):

- `path`: a glob with named captures -> label values. `products/{name}/**` -> package={name}. `saleor/graphql/{d}/**` -> package={d}, layer=graphql. Per-file overrides are just more specific globs.
- `symbol`: a predicate on a symbol (decorated_by, subclass_of, kind) -> labels on that symbol. `@cell_silo_model` -> silo=cell.
- `provider`: a plugin that labels modules: django-apps (app=label), owners (CODEOWNERS, owners-yaml, `product.yaml`) -> owner=team, manifest files (`product.yaml` presence -> package).
- `inherit`: a symbol inherits module labels unless it has its own.

Edges see labels on both ends: `src.package`, `dst.layer`, `dst.silo`.

### Selectors

`key=value`, `key=*`, `!key`, `&`, `|`, plus edge relations:

- `cross(key)`: src.key != dst.key (both set).
- `same(key)`: src.key == dst.key.
- `into(key=value)`, `from(key=value)`.

Every primitive selects edges with `from`, `to`, `where` (cross/same), `kinds`, `contexts_in/out`, and then applies its predicate.
The primitives are unchanged; only the selection is general.

### Examples

PostHog preset (products, facade, levels):

```toml
[[label]] path = "products/{package}/**"
[[label]] path = "products/*/backend/facade/**"        ; set = { zone = "facade" }
[[label]] path = "products/*/backend/presentation/**"  ; set = { zone = "presentation" }
[[label]] path = "{posthog,ee,common}/**"              ; set = { package = "core" }
[[rule]] id = "public"  ; primitive = "entrypoint" ; where = "cross(package)" ; to = "!zone=facade & !zone=presentation" ; kinds = ["import","reference","model-label"]
[[rule]] id = "layers.presentation" ; primitive = "edge" ; from = "zone=presentation" ; where = "same(package)" ; to = "!zone=facade & !zone=presentation"
```

Saleor:

```toml
[[label]] path = "saleor/{package}/**"
[[label]] path = "saleor/graphql/{package}/**" ; set = { layer = "graphql" }
[[rule]] id = "domain-not-graphql" ; primitive = "edge" ; from = "!layer=graphql" ; to = "layer=graphql" ; contexts_out = ["test"]
```

Zulip:

```toml
[[label]] path = "zerver/actions/**" ; set = { layer = "actions" }
[[label]] path = "zerver/lib/{util,types,...}.py" ; set = { layer = "lib-low" }
[[rule]] id = "send-event-only-from-actions" ; primitive = "use-shape" ; to = "symbol=zerver.tornado.django_api.send_event_on_commit" ; shape = "call" ; from = "!layer=actions"
```

Sentry:

```toml
source_roots = ["src"]
[[label]] symbol = { decorated_by = "sentry.db.models.base.cell_silo_model" } ; set = { silo = "cell" }
[[rule]] id = "no-cross-silo-fk" ; primitive = "edge" ; kinds = ["relation"] ; where = "cross(silo)"
```

### What changes

- Simpler: package and zone stop being special; `same-attribute` and layers become `where = same/cross(key)`; ownership routing is `owner` labels; the PostHog template becomes a preset file (`presets/posthog-products.toml`).
- Harder: label precedence must be explicit and explainable (`hoglint explain path/file.py` prints the labels and which source set each); the performance of label evaluation over about 25k modules and symbol predicates (cheap after L2, needs measuring).
- Levels: a preset concept on top of labels (a level is a label computed from rule results: `level=isolated`), evaluated in the second phase.
- Coverage (turbo inputs) and drives stay PostHog plugins.

### Risks

1. Overgeneralizing before a second user: keep PostHog as the only shipped preset; generality is the mechanism, not more features.
2. A label system that silently leaves things unlabeled: every rule declares the keys it needs; a module missing a required key is reported (`unlabeled`), never treated as clean (the Saleor `products.` 0-lines lesson).
3. Precedence surprises: last-wins plus `explain` output, and a check that no two sources of equal specificity set the same key differently.

## The second opinion's projection design (Nov 4)

**Question.** The second opinion read the v0 data model (core sections and spikes), the PostHog fit-test config sketch, and the three generality checks with their config sketches. It was asked to design the projection layer (L3: how modules and symbols get grouped, and how rules select edges) so that PostHog, Sentry, Saleor and Zulip are each plain config, with PostHog's products/facade/levels as a preset rather than a built-in. The requirements came from the checks: packages from globs with captures and from several trees joined on a capture; a layer axis across packages; zones and layers inside one package with nesting and per-file overrides; symbol attributes from decorators and bases; file-level ownership; optional public surfaces, manifests and levels; source roots; context-gated allows. It was asked for the fewest concepts, a short config per repo, how each primitive selects edges, what becomes simpler or harder, and the top 3 risks (including overgeneralizing before a second user exists). The limit was about 1,200 words.

**Answer.**
Make L3 **typed labels on existing entities, plus selectors over labelled edges**.
Package is one grouping axis; PostHog's product architecture is a preset.
Do not generalize it into a mandatory package → layer → zone hierarchy.

*Three concepts.*

1. **Assignments** attach typed values to files/modules, symbols, or named groups. Sources are path captures, constants, manifests, ownership files, and resolved decorator/base facts. Standard fields include `package`, `layer`, `zone`, `kind`, and `owners`; custom fields include `silo` and `database`.

   `package` is optional and single-valued; `owners` is set-valued and independent of architectural membership. Symbols inherit file assignments; symbol-specific attributes require explicit derivation. Never infer the package from the Django app label.

   Captured paths instantiate stable group IDs. Several patterns that assign the same ID join their trees; each match keeps its root, captures, and relative path for templates. Zones support explicit parent relationships: selecting `facade` includes its children. Layers are independent labels, usable across packages or within one.

   Conflicting assignments fail unless an explicit override resolves them. No implicit first-match precedence. Unmatched entities stay explicitly `unassigned`, never silently `core`.

2. **Selectors and predicates** select entities and edges using conjunction, disjunction, negation, equality, set membership, and endpoint comparisons. Examples: `layer=graphql AND package=order`, `src.package != dst.package`, `src.silo == dst.silo`.

   Edge selection specifies kind, context, source, and the destination role: **named surface**, **resolved origin**, or **re-export hop**. Source attributes come from the relevant enclosing symbol; relation rules use the participating models, including inherited-field provenance.

   Missing attributes yield unknown, not false. Required attributes get completeness checks; unknowns cannot silently escape a deny selector. Candidate targets keep the rule's explicit aggregation policy.

3. **Views** select edges and optionally group endpoints by fields: module, package, `(package, zone)`, layer, or silo. Keep the original edge IDs and witnesses. Select before aggregating, so contexts and symbol attributes survive.

Templates and presets merely expand assignments, views, and existing rules.
Public surfaces, dependency declarations, manifests, and levels are opt-in policy.

Source roots belong to discovery and resolution, before L3.
Reject ambiguous module identities.
L3 policy changes rerun projections and rules; source-root changes also invalidate resolution.
Following the spikes, keep cached L1 and full L2 recomputation at first.

*Illustrative config.* These are proposed YAML fragments, not an implemented schema. `where` expressions abbreviate structured selectors.

PostHog:

```yaml
source_roots: ["."]
presets: [django, posthog-products]
packages:
  - match: "products/{product}/**"
    id: "{product}"
    template: product
    manifest: product.yaml
  - id: core
    paths: [posthog/**, ee/**, common/**]
owners:
  reader: owners-yaml
  file: packages/owners-yaml/owners.yaml
rules:
  presentation:
    primitive: edge
    kinds: [import]
    from: "zone=presentation"
    to: "package.product=true AND zone!=facade AND zone!=presentation"
    deny: true
```

The versioned PostHog preset supplies the product zones, the public and dependency manifest readers, layout checks, levels, database routing, and the ledger policy.
Its presentation rule covers other products too.
Repository-specific registries stay adapters; the core contains no `products.*` tests.

Sentry:

```yaml
source_roots: [src, "."]
packages:
  - match: "src/sentry/{domain}/**"
    id: "{domain}"
owners:
  reader: codeowners
  file: .github/CODEOWNERS
attributes:
  symbol.silo:
    decorators:
      sentry.db.models.base.cell_silo_model: cell
      sentry.db.models.base.control_silo_model: control
    required_on: "kind=model"
  symbol.kind:
    bases: {django.db.models.Model: model}
rules:
  silo-fk:
    primitive: edge
    kinds: [relation]
    require: "src.model.silo == dst.model.silo"
```

Endpoint decorators extend the same mapping; resolved decorator instances must work.
RPC service grouping can use a second capture, `{domain}/{service}`, without splitting domain packages.
CODEOWNERS supplies file responsibility, not domain identity.

Saleor:

```yaml
source_roots: ["."]
packages:
  - match: ["saleor/{d}/**", "saleor/graphql/{d}/**"]
    capture_exclude: {d: [graphql, tests, static]}
    id: "{d}"
assign:
  - paths: ["saleor/graphql/**"]
    set: {layer: graphql}
  - paths: ["saleor/**"]
    except: ["saleor/graphql/**"]
    set: {layer: domain}
rules:
  domain-api:
    primitive: edge
    kinds: [import]
    from: "layer=domain"
    to: "layer=graphql"
    contexts_out: [test]
    deny: true
```

Explicit package overrides combine core/utilities or plugins/webhooks where desired.
No dependency matrix or public API declaration is required.

Zulip:

```yaml
source_roots: ["."]
packages:
  - {id: zerver, paths: ["zerver/**"]}
  - {id: corporate, paths: ["corporate/**"], optional: true}
zones:
  lib: {paths: ["zerver/lib/**"]}
  lib.foundation: {parent: lib, paths: ["zerver/lib/types.py"]}
  actions: {paths: ["zerver/actions/**"]}
overrides:
  - path: zerver/lib/types.py
    set: {layer: foundation}
rules:
  optional-import:
    primitive: edge
    kinds: [import]
    from: "package=zerver"
    to: "package.optional=true"
    deny_unless:
      any:
        - context: function_local
        - guard_implies: settings.CORPORATE_ENABLED
```

Guard evidence needs the canonical condition, polarity, and settings variant from L1/L2.
An arbitrary conditional is not enough; an unknown implication does not grant permission.
Map each optional destination to its guard.
Function-local permission is an explicit policy choice.

*Existing primitives.*

| Primitive | Selection under this design |
|---|---|
| `edge` | Selected couplings; predicates compare endpoint attributes, declarations, or contexts. Covers layers, silos, migration aliases, model labels, drives, and optional imports. |
| `entrypoint` | Selected boundary crossings; test the **surface** against the destination group's public selector. The boundary may be a package or an RPC service. |
| `export` | Selected exporting modules/bindings; inspect re-export hops and origin kinds. |
| `signature` | `exposes` edges from selected callables; inspect slot, nested types, and origins. |
| `use-shape` | References to selected symbols; constrain the refined shape and the caller location. Call-site restrictions need no new primitive. |
| `reach` | Traverse a context-filtered graph between selected endpoints; do not remove intermediate nodes merely because they fail endpoint selectors. |
| `cycle` | SCCs in the chosen filtered or grouped view; keep witnesses. |
| `layout` | Selected entities and paths; check presence, attributes, placement, or required alternatives. |
| `coverage` | Compare selected paths or reachable surfaces with adapter-provided input sets. |
| `level` | Evaluate selected groups against ordered rule requirements; reject dependency cycles. |
| `inventory` | Require classifications for selected edges; reject missing and stale entries. |

*Tradeoffs and risks.*
Simpler: one projection implementation serves every edge channel; layers need no packages; ownership and domain membership stop competing.
Harder: conflict diagnostics, attribute provenance, guard reasoning, and explaining grouped witnesses.
Provide `explain(entity)` and `explain(edge, rule)` from the outset.

1. **Overgeneralizing before a second user exists.** Implement these four fixtures and PostHog parity first; defer arbitrary expressions, propagation, and new graph algorithms.
2. **Silent false cleanliness.** Unassigned packages, missing silos, unresolved guards, and absent producers must stay visible. These checks already exposed boundary-dependent edge loss.
3. **Config that promises unavailable facts.** Decorator instances, receiver typing, settings variants, and cross-repository consumers require resolver or adapter capabilities. Validate capabilities and report incomplete results; L3 cannot manufacture evidence.

## Projection layer v1 (merged, Nov 4)

Sources: the architect's draft (written first) and the second opinion's design (independent, same evidence).
Both converge on typed labels on entities plus selectors over labelled edges, with PostHog's products/facade/levels as a preset.

### Concepts

1. Assignments: typed fields on files/modules, symbols and named groups.
   - Sources: path globs with named captures; constants; providers (manifests, Django apps, CODEOWNERS, owners-yaml); resolved decorator/base facts on symbols.
   - Standard fields: `package` (optional, single-valued), `layer`, `zone` (with explicit parents: selecting `facade` includes its children), `kind`, `owners` (set-valued, independent of package). Custom fields are declared in config (`silo`, `database`).
   - Several patterns may assign the same group id (Saleor `saleor/{d}` + `saleor/graphql/{d}`); each match keeps its root, captures and relative path.
   - Symbols inherit file fields; symbol-specific fields need an explicit derivation.
   - Conflicts fail unless an explicit override resolves them (from the second opinion; this replaces the draft's last-wins). No implicit precedence.
   - Unmatched entities are `unassigned`, never a default like `core`. Never infer the package from a Django app label.
2. Selectors: `=`, `!=`, set membership, AND / OR / NOT, endpoint comparisons (`src.package != dst.package`, `src.silo == dst.silo`).
   - Three-valued: a missing field is unknown, not false. A required field gets a completeness check; an unknown never escapes a deny selector.
   - Edge selection names the endpoint role: named surface, resolved origin, or re-export hop (data model section 4).
   - Source fields come from the enclosing symbol; relation rules use the participating models, including inherited-field provenance.
3. Views: select edges, then optionally group endpoints by fields (module, package, (package, zone), layer, silo). Select before aggregating, so contexts and symbol fields survive; keep edge ids and witnesses.

Presets (TOML files shipped or kept in the repo) only expand assignments, views and rules.
Public surfaces, dependency declarations, manifests and levels are opt-in policy.
Source roots belong to discovery and resolution, before L3; ambiguous module identities are rejected.

### Primitives under labels

The set is unchanged.
Call-site restrictions (Zulip `send_event_on_commit`) are use-shape with a caller selector: no new primitive.
`reach` traverses a context-filtered graph and does not drop intermediate nodes that fail endpoint selectors.
`level` evaluates groups against ordered rule requirements and rejects dependency cycles.

### Deferred

An arbitrary expression language, attribute propagation, settings-guard implication (Zulip's optional packages behind `settings.CORPORATE_ENABLED`; this needs canonical condition facts), receiver typing (Saleor `manager: PluginsManager`), cross-repo consumers (Sentry/getsentry).
Config may name them only once the fact exists; otherwise the rule is `incomplete`.

### Day-one requirements

- `hoglint explain <entity>` (the fields, and which source set each) and `hoglint explain <edge> <rule>`.
- Capability validation: a rule that asks for facts no producer supplies is `incomplete`, never clean.
- Fixtures: small synthetic repos in the four shapes (PostHog products, Saleor two-tree + cross layer, Zulip layers-only, Sentry symbol silos) as tests, plus PostHog parity.

### Generic bugs from the checks (to fix in phase 1)

- Module identity: a blind 3-character suffix cut (`tools/lint` -> `tools.l`); no source roots (Sentry `src/`); shebang scripts not discovered; ambiguous identities not rejected.
- Discovery: hard-coded excludes (any `docs/`, PostHog evals) and first-party defaults; add `--exclude` and config.
- Settings reading: annotated (`INSTALLED_APPS: tuple[...] = (...)`), augmented (`+=` of imported names) and chained (`A = B = "x"`) assignments; the settings module(s) and variants come from config.
- App discovery: annotated AppConfig attributes (`name: str = "zerver"`); namespace-package apps; an installed app without facts -> unresolved, not a crash.
- The L1 string cap: migration keys, labels and dotted paths need an uncapped role.
- Owner and boundary code paths: no `products.` / `core` split anywhere in channel code.
- Adapter tables (oauth2 settings proxies, `SOCIAL_AUTH_`) move to per-repo config.
