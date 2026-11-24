# Data model v0 (part 2: sections 11-20)

> The v0 data model of Hoglint, part 2: rule mapping, open decisions, review changes and spike results, Nov 4, 2026. Part 1 (sections 0-10) is `docs/research/data-model.md`.
> Partly replaced: later work refined it, and the current state is in `docs/design/`: `02-policy-exceptions-ledger.md` (ledger), `04-rules.md` (rules), `05-engine.md` (engine: an in-process PyO3 design replaced the Rust L1 + Python L2 split of sections 18-20), `06-fact-cache-and-summaries.md` (cache).

## 11. Rule mapping (54 rules)

The fit test listed 54 boundary rules that PostHog enforced with tach, import-linter, `hogli product:lint` and repo invariant tests.
This table maps each one onto the model.
Columns: primitive, edge kinds, view, identity, on_unresolved.
Dropped rows (O) and plugins (P) are at the end.

| # | rule | primitive | kinds | view | identity | unresolved |
|---|---|---|---|---|---|---|
| 1 | depends_on | edge (src pkg declares dst) | import, reference, dotted-path, model-label | packages | package-pair | finding |
| 2 | interfaces expose | entrypoint | all cross-package | refs | module-symbol | finding |
| 3 | expose only facade/presentation | layout (manifest vs template) | - | - | entity | - |
| 4 | utility modules | config of 1 | - | - | - | - |
| 5 | tests: interface yes, deps no | config of 1, 2 (contexts) | - | - | - | - |
| 6 | eval trees excluded | discovery config | - | - | - | - |
| 7 | permanent interface while a frozen ClickHouse migration imports it | inventory (exception justified by inbound edge) | import | refs | entity | block |
| 9 | owners-yaml leaf | edge | import | file-imports | module-pair | block |
| 10 | presentation uses facade (any product's backend, not only its own) | edge | import (direct) | zones | module-pair | finding |
| 11 | routes import presentation only | edge | import (direct) | zones | module-pair | finding |
| 12 | webhooks import facade only | edge | import (direct) | zones | module-pair | finding |
| 13 | facade not presentation/DRF (external destination) | edge | import (direct) | zones | module-pair | finding |
| 14 | contracts no Django (transitive) | reach | import (prod, not type_only) | file-imports | module-pair | incomplete |
| 15 | product.yaml schema | layout (config schema) | - | - | entity | - |
| 17 | required root files per level | layout | - | - | entity | - |
| 21 | misplaced backend files | layout | - | - | entity | - |
| 22 | file/folder conflicts | layout | - | - | entity | - |
| 23 | interface required when strict | level | reads 2 | - | entity | - |
| 24 | isolation chain | level | reads 23, 36, coverage | - | entity | - |
| 25 | facade returns/accepts | signature | exposes | refs | scope-symbol | finding |
| 26 | bodies only in capability submodules | edge | declares | refs | (zone file, module), counted | - |
| 27 | facade re-exports a behavior class | export | import (via) | refs | module-symbol | finding |
| 28 | wiring has approved base/decorator | layout over kind() | inherit, decorate | refs | entity | finding |
| 30 | model surface in contract-check inputs | coverage | - | - | entity | - |
| 31 | wiring locations watched | coverage | registry (fallback: presence) | - | entity | - |
| 32 | hogql_queries watched while driven | coverage | registry | refs | entity | candidates count |
| 33 | carve-outs watched | coverage | - | - | entity | - |
| 34 | permanent exposures watched | coverage | - | - | entity | - |
| 35 | routes, webhooks in inputs | coverage | - | - | entity | - |
| 36 | has_real_facade | layout (nonempty zone) | - | - | entity | - |
| 37 | MODEL_CROSSINGS | config exceptions | - | - | - | - |
| 38 | CARVE_OUTS | config exceptions | - | - | - | - |
| 39 | isolation_baseline equality | level + ledger | - | - | entity | - |
| 40 | crossing-class use shapes | use-shape | reference | refs | (target, module, kind+detail), counted | finding |
| 41 | get_model outside owner | edge | model-label (get-model) | refs | (target, module, get_model), counted | finding |
| 42 | drives(kind) from tests | edge | drives (context test) | refs | (location, module, trigger), counted | once per reachable location |
| 43 | reverse-accessor | edge | reverse-accessor (owner by module) | refs | (target, model.field, accessor) | block |
| 44 | facade-* ledger rows | ledger of 25-28 | - | - | - | - |
| 45 | ledger grows only with scanner change | ledger gate | - | - | - | - |
| 46 | two-way equality | ledger gate | - | - | - | - |
| 47 | one product's watched subtree (special case) | zone config | - | - | - | - |
| 48 | migration deps share a database | edge (aliases(child) ⊆ aliases(parent)) | migration-parent | migrations | (child, parent) | block |
| 49 | health checks do not import `posthog.dags` | runtime probe (plugin); static reach over import + dotted-path as early warning only | import, dotted-path | file-imports | module-pair | incomplete |
| 50 | every relation onto FeatureFlag classified | inventory, two-way (missing and stale classification both fail), same-product relations included | relation, reverse-accessor | refs | (model.field) | block |
| 55 | no direct KafkaProducer | use-shape (construct) | reference | refs | site | finding |
| 56 | no direct persons ORM | use-shape | reference, model-label | refs | site | finding |
| 57 | Max tools: no direct model create | use-shape (write) | reference | refs | site | finding |

Not engine rules: 8, 18, 19 (O, dropped: config schema validation and grimp blind spots); 16, 20, 29, 51 (P: GitHub owner lookup, scaffold scripts, test-runner adapter, receiver regression).
Row 49 stays in the table, but its blocking check is a runtime probe, so it moves from C (config) to P.

Count check: 47 mapped rows + 3 O + 4 P = 54 (49 counted as mapped; its static half is advisory).

Primitive use: edge 11, layout 7, coverage 6, use-shape 4, level 3, export 1, reach 1, inventory 2, entrypoint 1, signature 1, runtime probe 1, config-only 7 (rows 4, 5, 6, 37, 38, 44, 47), ledger gate 2 (rows 45, 46, section 9).

Views used beyond section 6: `migrations` (migration nodes, `migration-parent` edges), used only by row 48.
`cycle` has no user yet; it stays in the primitive list only if a rule needs it.

## 12. What v0 deliberately leaves out

- Type inference beyond annotations, class ancestry and re-exports. `instance-read` through an untyped variable is `unresolved`, not guessed.
- Partial-program checking while an agent types.
- Autofix. Remedies are hints with a kind, not edits.
- A rule DSL. Rules are config for the 11 primitives; anything else is a plugin with the same `Rule` interface.

## 13. Open decisions for the owner (Nov 4)

1. Default identity for the crossings ledger: keep `module-symbol` + count (the existing ledger), or move to `scope-symbol` (a use moved to another function in the same module shows as new). Finer catches more, and churns more lines on refactors.
2. Type-only imports: excluded from `dependencies` (rule 1) and `reach` (14), but counted by `entrypoint` (2)? Proposal: yes. About 1,580 `TYPE_CHECKING` sites in PostHog depend on this.
3. Default `on_unresolved` for cross-package rules: `finding` (proposed) or `incomplete`. `finding` puts unresolved sites in the ledger; `incomplete` blocks the run until the resolver improves.
4. Ledger prose: optional `reason`/`owner` columns, or a hard requirement for every admitted line (Packwerk lesson: todo files without owners only grow).
5. Growth authorization: keep the existing rule (crossings grow only with a scanner change in the same diff), or add a second path, admission by owner + reason. The second path is new doctrine, not a port.
6. Still open from `docs/research/research-round-1.md`: whether the Isolated skip stays a correctness gate (this decides whether coverage and level rules block or advise).

## 14. Cheapest checks of this model

1. Static model index vs `apps.get_models()` diff (local, no CI), at the full Relation record. Validates section 5 and the edge kinds `model-label`, `relation`, `reverse-accessor`. `reverse_accessors.py` is the reference implementation to match (231 reverse-accessor lines at the time).
2. Re-derive the crossings ledger (987 lines) from the model for 3 channels (`get_model`, `reverse-accessor`, one use-shape with `lock`/`write` precedence). Validates the identity projections and both ledger checks against real data.
3. Emit `file-imports` for one commit and diff it against `tach map`. Validates the cascade adapter and the import resolver together.
4. Static migration graph vs `MigrationLoader` graph. Cheap, and it covers `run_before` and `replaces`.

## 15. Changes from v0 (second-opinion review, all checked against the code)

1. Ledger: equality and growth split. Growth key per rule; crossings keep (crossing, kind) sums and scanner-change authorization (`grown_debt()`).
2. Shape: structured kind + detail + precedence, the 17 existing kinds; identity projections per channel, not one `module-symbol`. New `declares` edge, so rule 26 reads edges, not L1.
3. Migrations: `MigrationDecl`, `migration-parent` edges (dependencies, run_before, replaces), `aliases()` projection, subset predicate, second oracle on `MigrationLoader`.
4. The `zones` view keeps external and cross-package destinations (facade -> `rest_framework`; presentation contracts cover any product's backend).
5. `drives` is a derived edge with execution evidence (call chains, patches) and invalidation on helper bodies; unresolved counts once per location. This needs a cross-module call view: a capability the fit test did not list.
6. The `relation` record replaces model-label-only reasoning: owner by module, proxies canonicalized, concrete-parent fields excluded, sealed = hidden and no query name. The oracle compares full records. Rule 50 is two-way over all relations onto FeatureFlag.
7. Rule 49 stays a runtime probe: `ensure_registry_loaded()` imports the `HEALTH_CHECK_MODULES` strings, so no static view establishes it.

Lesson: rows marked C (config) in the fit test hid real semantics. Before a rule counts as "config", it needs a re-derivation of the existing tool's output, not only a schema fit.

## 16. Deliberation notes (Nov 4)

Facts checked:

- Type-only imports at the time: tach ignored them (`ignore_type_checking_imports` defaults to true, and `tach.toml` did not override it). import-linter counted them (grimp default). The crossings scanner read them (`_read_imports` lifts `TYPE_CHECKING` blocks), and `annotation` was an allowed shape.
- Unresolved at the time: the crossings scanner counted an unreadable query kind as `drives(unresolved-kind)` against every location it could reach. The ledger had 0 such lines.
- The growth gate at the time (`ledger_growth.py`): sums per (crossing, kind) vs the PR base; any diff under `tools/hogli-commands/hogli_commands/product/` skipped the gate. Its instruction text: no hand-edited lines; a coupling that must stand becomes an approved interface, a `MODEL_CROSSINGS` entry or a carve-out.

Proposals under discussion:

- Identity: findings always carry the full site. The ledger key is a projection over findings, so it can change later by regenerating, without a rescan. The growth key (crossing, kind) decides what blocks; ledger granularity only decides churn and precision. Measure churn per granularity by replaying recent commits before choosing.
- Type-only: record always (context `type_only`). Exclude from dependencies, reach and the cascade; include in entrypoint; `annotation` stays an allowed shape; the signature rule catches Django types on contracts.
- Unresolved: default `finding` with an `unresolved-*` kind (the existing precedent). `incomplete` only for producer failures (parse error, oracle diff). Never `ignore`.
- Debt lines: generated, no prose. Reasons and owners live on policy exceptions, which are hand-written and reviewed.
- Growth attribution instead of the directory gate: scan the HEAD code with the base scanner and policy. Growth there came from code and fails. Growth that only appears with the new scanner or policy came from the rule change and passes.

## 17. Spike round 1 results and v0.2 changes (Nov 4)

All spikes ran on the PostHog monorepo.

| spike | parity | static cold | oracle / existing tool |
|---|---|---|---|
| A relations | 604/604 models, 1,431/1,431 relation records, 231/231 reverse-accessor lines (3 adapter rules) | 9.0 s CPU (parse 5.0 s); 2.0 s with cached parse | `django.setup()` 2.2 s + 14 ms walk |
| B migrations | 694/694 nodes, 1,965/1,965 edges, alias invariant equal on the real graph + 5 mutations | 1.3-1.8 s | 7.0-9.5 s |
| C import map | 94,143 edges, 12,340/12,340 dependents sets equal to `tach map` | 12 s parallel, 37-47 s serial; warm one-file edit < 0.1 s | tach 2.7-3.6 s |
| D ledger | 61/61 lines (get_model, write, lock), counts exact; parity on history since Aug 25 | 7.3 s single process | - |

Conclusions:

- Static Django reading works: every Django-facing projection reached exact parity. The two oracles stay, because the adapter tables (swappable settings, package settings proxies) are hand-kept.
- Parsing is the cost everywhere. One shared L1 parse must feed imports, models, migrations and crossings; four separate passes would parse the same files four times.
- Ledger key: module identity. Scope adds about 1.17x lines and no precision on 34 replayed pairs; scope stays on findings. Renames are not priced.
- Attribution works: replaying with the extractor held fixed separates code growth from rule growth (one replayed commit: real +15, derived 0).
- "Unresolved = finding" needs a prior state: 61 of 92 unresolved `get_model` calls are not Django (`self.get_model`). Add `not-applicable` (the receiver is not a registry) before `unresolved`.

v0.2 changes:

1. Resolution states: resolved | candidates | not-applicable | unresolved | external.
2. The snapshot includes the site-packages sources that adapters use, the Django version and the lockfile; adapters are keyed by distribution version.
3. New L1 facts: SettingFact {name, value, conditional} (`INSTALLED_APPS` with conditional appends, `AUTH_USER_MODEL`, `OAUTH2_PROVIDER_*`); AppConfig with how it was chosen; an "unrecognized relation call" fact; parse failure as an unresolved fact.
4. Relation: add `model`, `kind` (fk | o2o | m2m | generic), `from_abstract` = declaring abstract class, `explicit_query_name` separate from the computed `query_name`; sealed = hidden and not explicit_query_name; `src` span. ModelDecl: meta_bases and Meta inheritance rules.
5. MigrationDecl: `initial`, `unresolved`, swappable(setting); migration-parent edge: `declared_by`, `remapped_from`; ledger key (child app, parent app, declared key). aliases() = "all routes configured" from `db_routing.yaml`.
6. The file-imports view = import minus type_only plus dotted-path StringRefs (tach's per-part rule); type_only = a bare or attribute `TYPE_CHECKING` test, whole statement; the resolver uses a case-sensitive inventory; Linux tach is the parity oracle.
7. Site.scope: decorators, bases and defaults belong to the enclosing scope. A get-model StringRef records its receiver.
8. Run envelope: extractor versions and policy_hash (`MODEL_CROSSINGS` + facade re-exports) pinned, for attribution replays.

## 18. Spike round 2 results and v0.3 changes (Nov 4)


| spike | parity | notes |
|---|---|---|
| E drives | 110/110 lines, counts exact | call view + derive 0.27 s from cached L1 |
| F facade | 235 + 131 + 62 + 7 lines exact | a few cases the existing scanner missed, which re-export following catches |
| G shapes | 150/150 lines over 9 kinds | a few cases the existing syntax-only classification missed, which def-use catches |
| H Rust L1 | bindings equal to Python on all 23,532 files | 0.8-1.0 s cold full run; about 0.15 s JSON boundary to Python |

With round 1, the whole crossings ledger is re-derived (61 + 150 + 435 + 110 + 231 = 987 lines), plus relations, migrations and the tach map.

Conclusions:

- Engine shape: Rust L1 (one parse pass, all fact kinds), Python L2 and up, Python oracles. L2 is recomputed in full from cached L1 on every run (import resolve 0.35 s, drives 0.27 s, product graph 28 ms); per-edge invalidation is not worth building. Only L1 content-hash caching matters. End-to-end time with all fact kinds was not measured yet. (Later replaced: see `docs/design/05-engine.md`.)
- Type depth: annotation resolution + re-export hops + class ancestry through repo sources. No type inference, no Protocol/TypeVar, no stubs.
- Flow: syntax decides 47/150 use-shape lines. One-function def-use fixes about 21 lines; interprocedural flow about 27 more. Under the doctrine (the linter carries the skip's safety), a def-use pass is a correctness gain, not polish.
- Adopting the engine grows the ledger (2 facade lines, flow-refined shapes). Attribution counts that as rule growth.

v0.3 changes:

1. Drive: execution direct | via-helper(chain) | none(reason: no-dispatch | patched); keep `none` facts; kind targets are a set; the trigger gets `unresolved-kind`.
2. Derived edges carry their reads (file hashes, symbol keys including negative lookups, named registry inputs). These are used for `explain` and property tests; the runtime recomputes L2 in full.
3. Body: callee names, test-client flag, patch leaf names, params with constant defaults; a Scope fact per class/module; a kind-candidate fact (const, tag, enum attribute, constructor).
4. Shape: `escape` at L1 (none | local | arg | return | store | class-attr | builtin); `detail` is statement-local; findings carry a flow-refined kind; an L2 def-use pass with a passthrough-callee table and unresolved-escape; an allowed `type-use` kind; async single terminals; framework declarations (DRF `Meta.model`, queryset attrs, `Field(queryset=)`, signal senders) as declaration edges; drop `passed-as-class(OneToOneField)` in favor of the relation edge.
5. exposes: slot, position (whole | nested), surface + origin callable, dataclass fields as `__init__` accepts. exports(Module) as a shared L3 projection (re-export module, `__all__`, self-alias, lazy maps). Lazy-map export as an L1 binding kind. A collection-members fact.
6. Rules: ledger projection gated by a level (two-phase evaluation; the Isolated level must not read rule 28 except through ledger acceptance). kind(model) = ancestry to Django `Model`, abstract excluded.
7. Toolchain: ruff crates 0.0.20 need rustc >= 1.94 (the dev environment's cargo had 1.91). Pin the crates; upgrades are deliberate.

Open decisions added:

- drives call resolution: bare names (the existing behavior; over-approximates, so it is safe for the skip but noisy: one new `get` flips 153 facts) vs an import edge or same-class receiver required (precise, can under-count).
- Flow-refined shapes: adopt in the ledger (more lines, catches locks and writes) or keep syntax-only parity first.

## 19. Defaults (Nov 4)

The architect set these defaults from the owner's doctrine ("the linter carries the Isolated skip's safety") and the spike data.
The owner may override any of them.
None of them is a recorded owner decision, except the doctrine itself.

| question | default | basis |
|---|---|---|
| ledger identity | module + count; scope on findings | spike D: scope 1.17x lines, no gain; re-projection needs no rescan |
| type-only imports | out of dependencies, reach, cascade; in entrypoint; annotation allowed | spike C: tach map matches only with type_only out; no runtime coupling |
| unresolved | finding with `unresolved-*` kind, after a `not-applicable` state; producer failure = incomplete | doctrine: unknowns count against the product; spike D: 61/92 not Django |
| debt location | one generated central ledger | it must be compared against a scan (no list-copy ratchets); two-way equality and the growth gate need one file |
| debt prose | none on ledger lines; reasons and owners on policy exceptions | generated lines; deliberate couplings are policy |
| growth authorization | attribution (base scanner on head code) replaces the directory gate; no admission path | the existing doctrine kept; spike D confirms attribution |
| drives call resolution | keep the bare-name over-approximation | doctrine: over-count only costs skips, under-count breaks safety |
| flow-refined shapes | adopt (one-function def-use first) | doctrine: missed locks and writes are exactly what the skip relies on |
| Rust toolchain | ship the Rust part as a prebuilt wheel built in CI with a pinned toolchain; developers need no rustc | the dev environment had 1.91, the crates need >= 1.94; same distribution as ruff and tach |

## 20. End-to-end spike (Nov 4)

The Rust report is summarized here.

Pipeline: Rust L1 (`rust-l1`, about 2,300 lines, `ruff_python_parser` 0.0.20, `--mode kinds --v1.1`) -> per-kind store -> Python L2 (`py-l2`, core + 6 channel modules) -> every output.

Parity from Rust facts, end to end:

- Ledger: 987/987 equal, 0 missing, 2 extra (the 2 real facade misses from spike F).
- tach map: 94,143/94,143 edges, 12,340/12,340 dependents sets.
- Migration graph: 694/694 nodes, 1,965/1,965 edges, 87/87 apps, alias invariant equal.
- Relations: 604/604 models, 1,431/1,431 records, 231/231 accessor lines.
- Rust vs Python reference L1: 0 differing records on 25,534 files (v1 and v1.1 rules).

Timing (measured under load, so these are upper bounds):

| step | wall | CPU |
|---|---|---|
| Rust L1, 25,534 files, kinds store | 2.55 s | 12.8 s |
| Python L2: load 1.45 s, core 0.06 s, channels 4.66 s (drives 2.74) | 6.73 s | 5.9 s |
| cold total | about 9.3 s | |
| Rust warm, 1 changed file, cache (phase 2R) | 1.16 s | |
| warm total (composed, not run as one) | about 7.4 s | |

Compared with the existing tools: tach 1.3-3.2 s + lint-imports 1.3 s + `product:lint --all` 8 s + Django repo invariants 267-278 s per CI run.
The oracles (registry about 3 s, `MigrationLoader` about 9 s) stay in CI.

What was left before a usable tool:

- L2 is the warm-path cost (Python, full recompute). Levers: a per-def body fact for drives (2/3 of channel time); a long-lived process (pre-commit daemon or LSP) that keeps facts in memory and recomputes channels only.
- The Isolated rung from `product.yaml` + level evaluation (the spike took it from the baseline, which is circular).
- Config-driven rules, ledger equality + the attribution gate, run result, CLI, tests: not built. The spike reproduces outputs; it is not the rule engine.
- A Linux tach parity check before replacing `tach map`.
- Schema v1.1 should bump `v`; the L1 extract costs 4x a bare parse (no optimization needed for the targets).
