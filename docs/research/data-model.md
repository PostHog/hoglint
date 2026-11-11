# Data model v0 (part 1: sections 0-10)

> The v0 data model of hoglint (v0.1, with the v0.2 and v0.3 changes from the spikes), Nov 4, 2026. Part 2 (sections 11-20) is `docs/research/data-model-2.md`.
> Partly replaced: later work refined it, and the current state is in `docs/design/`: `02-policy-exceptions-ledger.md` (ledger), `04-rules.md` (rules), `05-engine.md` (engine: an in-process PyO3 design replaced the Rust/Python split of sections 18-20), `06-fact-cache-and-summaries.md` (cache).

Inputs: a fit test of 54 PostHog boundary rules (not published), `docs/research/research-round-1.md`, the second opinions in `docs/research/research-round-1-second-opinions.md`, and `docs/research/blind-design.md`.
A review by a second opinion from another model found 7 issues, and the architect checked each one against the PostHog code.
Section 15 lists what changed from v0 because of that review.

The model is engine-agnostic.
Types are written as plain records; `?` means optional, `[]` means list.

## 0. Shape in one picture

```
source snapshot
  └─ L1 local facts      per file, cacheable by content hash, no cross-file knowledge
       └─ L2 edges        resolved, cross-file; keep the surface the caller named AND the origin
            └─ L3 projections  package, zone, symbol kind, attributes (from config + manifests)
                 └─ views     named graphs with stated semantics (file, symbol, zone, package)
                      └─ rules  read views, emit findings with identities
                           └─ ledger   applied after evaluation; accepted findings stay in the graph
                                └─ run result   clean / violations / incomplete, per rule
```

Three separations hold everywhere:

- A fact says what the code does. A policy says what is allowed. A ledger line says what is accepted for now.
- Config changes re-run L3 and up. Only source changes re-run L1 and L2.
- Nothing downstream deletes an edge. Filtering is a view, acceptance is a ledger match.

## 1. Run envelope

```
Snapshot   { id: hash(file list + contents), source: worktree | staged | commit<sha> }
Run        { snapshot, policy_hash, analyzer_version, extractor_versions{producer: ver} }
```

Every finding and every ledger comparison carries the run.
Pre-commit uses `staged`, CI uses `commit`.

## 2. Entities

```
Module   { id: dotted name, path, is_package, namespace: bool }
Symbol   { id: module.qualname, module, kind_decl: class | function | assignment | import-binding,
           span, scope: qualname of the enclosing scope }
Site     { module, scope: qualname of the enclosing function/class or "<module>", span }
External { id: dotted name, distribution }        # django, rest_framework, ...
```

`Site.scope` is the stable part of a location.
Spans move with every edit; scopes move only on renames.

## 3. L1 local facts (per file)

Facts record syntax and binding only. No ownership, no policy.

```
Binding     { site, name, from: dotted surface, level: absolute | relative(n), type_only: bool,
              conditional: none | try | if, local: bool }        # every import binding
Declaration { symbol, bases: [expr], decorators: [expr], annotations: {param|return: expr} }
Reference   { site, expr: name or attribute chain, shape }       # a use of a bound name
StringRef   { site, value, role }      # role: fk-target | get-model | dotted-path | registry-key(<registry>)
                                       #       | migration-dep | settings-path
ModelDecl   { symbol, app_label?, abstract, proxy, fields: [FieldDecl] }   # produced by the Django adapter
FieldDecl   { name, kind: fk | o2o | m2m, target_expr, related_name?, related_query_name?, span }
AppConfig   { module, label, name }                               # from apps.py
MigrationDecl { app_label, name, dependencies: [(app, name)], run_before: [(app, name)],
                replaces: [(app, name)], span }                   # from migrations/*.py
Body        { symbol, zone?, calls: [expr], patches: [expr], span }   # function/method bodies, for
                                                                  # facade-logic and drives
```

`shape` is a structured classification, not a flat word:

```
Shape { kind, detail?, precedence }
```

`kind` is the vocabulary of PostHog's crossings ledger at the time:
`annotation | isinstance | exception | nested-class-attr | meta | scalar-chain | subquery | instance-single | instance-many | in-collection | write | lock | construct | passed-as-class | drf-model-serializer | drf-field-queryset | other`.
`detail` carries the parameter that the ledger lines print (`write(save)`, `instance-many(filter)`, `passed-as-class(cast)`).
Precedence follows `classify_use()` in the crossings scanner (`crossings.py`): `lock` and `write` beat a scalar terminal, so `select_for_update().count()` is `lock`, never `scalar-chain`.
The extractor assigns the shape at L1 from syntax only. L2 decides what `X` is.

## 4. L2 edges (resolved relationships)

One record type for every coupling. Rules never see L1 directly.

```
Edge {
  id:         stable hash(kind, src.module, src.scope, surface, ordinal-in-scope)
  kind:       EdgeKind
  src:        Site
  surface:    dotted name the code actually named        # payments.api.charge
  targets:    [Symbol | Module | External]                # payments.implementation.charge
  via:        [Symbol]                                    # re-export chain, surface -> origin
  resolution: resolved | candidates | unresolved | external
  context:    {prod | test | migration | type_only | function_local | conditional | app_ready | admin}
  shape?:     use-shape (references only)
  producer:   pass id + version
  evidence:   [span]
}
```

Edge kinds (v0):

| kind | from L1 | example | producer |
|---|---|---|---|
| `import` | Binding | `from products.x.backend.models import M` | core resolver |
| `reference` | Reference | `M.objects.filter(...)` | core resolver |
| `inherit` | Declaration.bases | `class A(Base)` | core resolver |
| `decorate` | Declaration.decorators | `@shared_task` | core resolver |
| `exposes` | Declaration.annotations | facade `def f() -> QuerySet[M]` | core resolver |
| `model-label` | StringRef fk-target / get-model | `ForeignKey("x.M")`, `apps.get_model("x","M")` | Django adapter |
| `inherited-relation` | ModelDecl + inherit | concrete model gets an FK from an abstract base | Django adapter |
| `relation` | ModelDecl + FieldDecl + inherit | one effective relation per concrete model (see Relation below) | Django adapter |
| `reverse-accessor` | Relation | target gains an accessor, or a query name, unless sealed | Django adapter (derived from `relation`) |
| `registry` | StringRef registry-key | query kind, Celery task name, Temporal workflow, Max tool | registry extractor (one per registry) |
| `drives` | import / registry + Body | a test executes wiring in another package | drives deriver (derived) |
| `migration-parent` | MigrationDecl | resolved parent edge: dependencies, reversed run_before, replaces mapped | migration resolver |
| `declares` | Body | a callable with a body lives in a zone | core resolver |
| `dotted-path` | StringRef dotted-path / settings-path | `"products.x.backend.tasks.run"` | core resolver |

`inherited-relation` from v0 folds into `relation` (field `from_abstract`).

Relation record (the `relation` edge carries it):

```
Relation { declaring_model, field, concrete_target, target_owner_by_module, target_label,
           from_abstract, from_concrete_parent, hidden, accessor_name?, query_name?, auto_created }
sealed = hidden and not query_name        # related_name="+" alone is not sealed
```

Owner comes from the defining module, never the app label (`access_control.Role` keeps the legacy `ee` label).
Proxy targets are canonicalized to the concrete model.
Fields a model inherits from a concrete parent create no accessor of their own.

`drives` record:

```
Drive { trigger: module-import | query-kind | name, location: wiring location (zone path),
        execution: direct | via-helper(chain) | none, patched: bool }
```

A query built only for formatting is `execution: none` and is not counted.
A patched helper suppresses it.
An unresolved kind counts once per reachable wiring location, not once per candidate symbol.

Resolution rules:

- `surface` is never replaced. `targets` is the origin after following re-exports. `via` keeps the chain. The entrypoint rule reads `surface`, the export rule reads `via`.
- `candidates` lists every possible target. A registry key built at runtime resolves to `candidates = all targets of that registry` (fit test: "unresolved: all-candidates").
- `unresolved` is a result, not an absence. A rule declares what an unresolved edge means for it (section 8).
- Function-local and type-only imports are edges with a context, never dropped.

Invalidation: L1 is cached by content.
L2 for a file is invalid when its own L1 changes, when any module it bound changes exports, or when the module inventory changes (a new file can make an `unresolved` edge resolve).
v0 rule: an inventory change re-resolves everything.
Derived edges (`drives`, `reverse-accessor`, `relation`, `migration-parent`) record the inputs they read: helper bodies, dispatch tables, registry membership, model declarations.
A change to any of them invalidates the derived edge even when the source file of the edge did not change (a helper that starts calling a dispatcher changes an untouched test's drives).
Property test: incremental run == clean run.

## 5. L3 projections (config-driven)

```
owner(Module)       -> Package                 # by package globs / paths
zone(Module)        -> Zone?                   # by the package template
kind(Symbol)        -> {model, contract, enum, wiring, function, class, ...}   # needs inherit/decorate edges
attribute(Package)  -> {database: str, level_claim: str, ...}     # from product.yaml and side files
public(Package)     -> [Zone]                  # manifest `public:`
declared(Package)   -> [Package]               # manifest `depends_on`
model(Symbol)       -> ModelInfo { app_label, abstract, concrete_of, relations: [Relation] }   # static model index
aliases(app_label)  -> {db alias}              # from products/db_routing.yaml + router behavior
```

Two projections have an outside oracle. A CI job boots Django and diffs both:

- Model index vs `apps.get_models()`: labels, concrete models, and the full Relation record per field (target, owner, hidden, accessor, query name, auto_created), not only labels and accessor names.
- Migration graph vs `MigrationLoader(...).graph`: resolved parents, including `run_before` and squash `replaces`. `apps.get_models()` says nothing about migration topology.

Any diff fails that job and marks the dependent rules `incomplete` until fixed.

## 6. Views

A view is a named graph with stated semantics.
Rules name the view they read.
Witnesses say which view they come from.

| view | nodes | edges | semantics | consumers |
|---|---|---|---|---|
| `file-imports` | files | `import` (all contexts) | real import chain | tach-map adapter, reach rules |
| `refs` | sites -> symbols | all kinds | one edge per coupling site | public, use-shape, model-labels, exports, signatures |
| `zones` | (package, zone) -> any destination | `import` edges from a zone, direct only | source is a zone; destination keeps package, zone, or external (`rest_framework`, `django`) | layers |
| `packages` | packages | `refs` projected, cross-package | component dependency, not a chain | dependencies, levels, cascade |

A package path A -> B -> C in `packages` is reported as a component path with one witness edge per hop.
It is never shown as an import chain.

Output adapter: `file-imports` serialized in the `tach map` JSON shape keeps PostHog's `.github/scripts/turbo-discover.js` working unchanged.
Its context filter must match the `tach map` output (tests included) or the CI cascade changes.

## 7. Rules

```
Rule {
  id, version, owner, doc            # doc: what, why, allowed and forbidden examples, blind spots, repairs
  primitive:   edge | entrypoint | export | signature | use-shape | reach | cycle | layout
               | coverage | level | inventory
  view:        file-imports | refs | zones | packages
  select:      { kinds: [EdgeKind], contexts_in: [...], contexts_out: [...], from: selector, to: selector }
  predicate:   primitive-specific config (allow/deny lists, transitive, shapes)
  identity:    site | scope-symbol | module-symbol | module-pair | package-pair | entity
  counted:     bool                   # ledger stores a count per identity
  on_unresolved: block | finding | incomplete | ignore
  requires:    [producer]             # if a producer failed, the rule is incomplete
}
```

Selectors use L3 only: `package:*`, `zone:presentation`, `kind:model@other` (a model owned by another package), `ext:django`, `path:posthog/dags/**`.

Primitives:

| primitive | question | witness |
|---|---|---|
| edge | is this edge allowed? | the edge |
| entrypoint | did a cross-package edge name a public surface? (reads `surface`) | the edge |
| export | does a public zone hand out a symbol of a denied kind? (reads `via`, `kind`) | binding + origin |
| signature | does a public signature mention a denied type? (`exposes`) | the annotation |
| use-shape | is this use of a foreign symbol in an allowed shape? | the reference |
| reach | is a denied target reachable? (BFS on the view) | shortest path, deterministic tie-break |
| cycle | SCC in the view | one cycle + one edge per hop |
| layout | do required files/zones exist, xor pairs hold, moved files gone? | the path |
| coverage | does an external input list (turbo inputs) cover what the rule names? | the uncovered path |
| level | which ordered level does a package pass? (reads other rules' results) | the failing requirement |
| inventory | does every edge into X carry a classification exception? | the unclassified edge |

`level` rules evaluate after the rules they read.
That is the only ordering between rules.

## 8. Findings and run result

```
Finding {
  rule_id, rule_version
  identity:  key tuple per rule.identity (see below)
  subject:   Site | Package | Path
  primary:   span
  related:   [span]
  witness:   [Edge.id] (+ view name)
  status:    new | accepted | resolved        # filled by the ledger step
  remedies:  [Remedy]
}
Remedy { kind: permitted-destination | documented-replacement | safe-rewrite | needs-public-api
               | seal-relation | admit-debt,
         target?, doc? }

RuleResult { rule_id, outcome: clean | violations | incomplete, reasons_incomplete: [...] }
RunResult  { run, rules: [RuleResult], findings: [Finding], coverage: {edges_by_resolution, unresolved_sites} }
```

Exit code: 0 clean, 1 violations, 2 incomplete or config error.
An empty findings list with an incomplete rule is exit 2, never 0.

Identity keys (no spans, no line numbers):

| identity | key | moving code inside... is free |
|---|---|---|
| `site` | rule, src.module, src.scope, kind, surface, ordinal | nothing |
| `scope-symbol` | rule, src.module, src.scope, kind, target | the same function |
| `module-symbol` | rule, src.module, kind, target | the same module (the crossings ledger at the time) |
| `module-pair` | rule, src.module, dst.module | the same module pair |
| `package-pair` | rule, src.package, dst.package | the same package pair (tach) |
| `entity` | rule, subject | n/a (layout, level, coverage) |

Identity is not one generic key per rule family.
Each rule declares a projection from finding to ledger columns.
PostHog's crossings ledger already used five:

| channel | ledger line at the time | identity projection |
|---|---|---|
| use-shape, get_model | `target.Class consumer.module kind(detail) n` | (target, src.module, shape.kind+detail), counted |
| reverse-accessor | `target.Class owner.Model.field reverse-accessor(name) 1` | (target, declaring_model.field, accessor) |
| drives | `pkg:location test.module drives(Kind) n` | (location, src.module, trigger), counted |
| facade-accepts/returns | per callable and parameter | (facade symbol, param or return, denied type) |
| facade-logic | `pkg:backend/facade/<file>.py module facade-logic n` | (zone file, module), counted by `declares` edges |

## 9. Ledger

```
LedgerLine { rule_id, identity, count?, reason, owner, added_in: commit, review_by? }
```

- One file, sorted, one line per (rule, identity). Generated lines carry no prose; `reason` and `owner` are optional columns.
- Two checks, kept apart:
  - Equality: the scan and the ledger match line by line, with counts. A stale line fails. This is exact.
  - Growth vs merge base: each rule declares a growth key and who may authorize growth.
    Crossings keep the semantics of the existing gate: growth key (crossing, detailed kind) summed over all consumers, so moving or splitting a consumer module carries its lines and raises no debt (`grown_debt()` in `crossings.py`). Growth is authorized only by a change to the scanner in the same diff.
    The growth gate compares sums per growth key, never one grand total. A rule without a declared growth key defaults to per line.
- Admission by owner + reason, without a scanner or policy change, is not in the existing gate. It is a doctrine change, listed in section 13.
- Applied after evaluation. Accepted edges stay in every view, so a waived edge still shows up in reach and cycle witnesses for other rules.
- `compare` (PR mode) reports four lists separately: findings resolved, findings new, ledger lines added, policy entries changed. Review reads the last two.

## 10. Policy vs debt split of the existing config

| at the time | becomes |
|---|---|
| import-linter `ignore_imports` "presentation -> facade allowed" | rule `layers.presentation` allow list (policy) |
| import-linter `ignore_imports` "TODO: existing violations" | ledger lines, identity `module-pair` |
| tach `depends_on` per module | manifest `depends_on` (policy); undeclared edges seen at adoption -> ledger |
| tach `[[interfaces]] expose` | manifest `public:` (policy) |
| `model_crossing_uses_baseline.txt` | ledger lines, identity `module-symbol`, counted |
| `isolation_baseline.txt` | ledger lines for rule `level.minimum`, identity `entity` |
| `MODEL_CROSSINGS`, `CARVE_OUTS` | `exceptions` in config (policy: declared, reviewed, shrink-only) |

Sections 11-20 continue in `docs/research/data-model-2.md`.
