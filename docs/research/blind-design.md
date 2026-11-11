# Blind design: one boundary linter for a Django monorepo

> A design written blind from the requirements for research round 1: from the PostHog doctrine and tool configs only, without earlier proposals, Nov 3, 2026.
> Partly replaced: it fed the v0 data model (`docs/research/data-model.md`); the current design is in `docs/design/`.

Working name `bound`.
Written from the doctrine (`products/architecture.md`, `products/README.md`) and the existing tools (`tach.toml`, the import-linter contracts in `pyproject.toml`, `hogli_commands/product/`).
It does not build on earlier proposals.

## The thesis

The existing tools disagree about what an edge is: tach and import-linter know imports, `crossings.py` knows name uses and `get_model` strings, and `reverse_accessors.py` boots Django for FK accessors.
Each has its own debt list.

`bound` has one idea: **every coupling is a typed edge between two sites, and every rule is a predicate over edges.**
An import, a string FK, a `mock.patch("products.x.backend.logic.f")` target and a `send_task("name")` call are all edges.
They differ only in `kind`.
Once the extractors emit edges, a single rule engine and a single ledger cover all of them.

## 1. Core model

**Entities**

- **Unit**: the ownership and isolation boundary. Each `products/<name>/` is one unit, matched by pattern, so the config needs no 100 hand-written blocks. Each of `posthog`, `ee` and `common` is a unit too.
- **Zone**: a named path set inside a unit (`facade`, `contracts`, `presentation`, `routes`, `wiring.query`, `models`, `migrations`, `commands`, `tests`, `internal`). The first match wins, and `internal` is the fallback. Zones are the vocabulary the doctrine already uses.
- **Symbol**: a module, class or function, plus derived facts: model (with app label and fields), Celery task name, Temporal workflow name, query kind, route basename.
- **Edge**: `(from_site, to_symbol, kind, span, via)`. The kinds are `import` (with the names, plus type-only and deferred flags), `model-ref`, `reverse-accessor`, `signal`, `task-name`, `workflow-name`, `dispatch`, `drives`, `url-include`, `migration-dep`, `dotted-ref` and `use` (a name-level use, carrying its syntactic shape).

**Rule kinds.** The kinds are few and built in. There are no user plugins.

1. `expose`: across units, only the listed zones are reachable, on every channel.
2. `layer`: inside a unit, zone A may reach only zones B and C.
3. `forbid`: zone A must not reach a target, optionally through the transitive closure.
4. `surface`: the shape of what a zone hands out, for example the types on facade signatures, or a class that must subclass an approved base.
5. `use-shape`: a default-deny list of syntactic shapes allowed on a use of a target class.
6. `relation`: predicates over model relation fields.
7. `coverage`: a set of zones must appear in an external artifact, such as the turbo inputs. Where possible, `bound emit` generates that artifact instead of checking it.

**Exceptions differ from debt.**
An `allow` in config is sanctioned and permanent, and it must name a reason and a doctrine anchor.
A ledger line is debt, and it can only shrink.

```toml
# boundaries.toml (repo root). Per-unit overrides go in products/<p>/product.yaml under `boundaries:`.
[unit.product]
match = "products/*"
package = "products.{name}"
app_label = { from = "backend/apps.py" }         # read statically, never imported

[unit.core]
roots = ["posthog", "ee", "common"]               # one unit per root

[zones.product]                                   # first match wins
contracts    = ["backend/facade/contracts.py", "backend/facade/enums.py"]
facade       = "backend/facade/**"
presentation = "backend/presentation/**"
routes       = ["backend/routes.py", "backend/routes/**"]
webhooks     = "backend/webhook_consumers.py"
commands     = "backend/management/commands/**"
"wiring.query"    = "backend/hogql_queries/**"
"wiring.temporal" = "backend/temporal/**"
"wiring.tasks"    = ["backend/tasks/**", "backend/tasks.py"]
"wiring.max"      = "backend/max_tools.py"
models       = ["backend/models/**", "backend/models.py"]
migrations   = "backend/migrations/**"
tests        = ["**/tests/**", "**/test_*.py"]
internal     = "backend/**"

[[rule]]
id = "cross-unit-surface"
kind = "expose"
to = "unit.product"
expose = ["facade", "routes"]                     # a product can add e.g. presentation.views in its product.yaml
channels = ["import", "model-ref", "dotted-ref", "signal", "task-name", "workflow-name"]
scope = { tests = "error" }                       # tests are in scope; see rule (b) in section 7

[[rule]]
id = "presentation-through-facade"
kind = "layer"
from = "presentation"
may_reach = ["facade", "contracts", "presentation", "@other-units", "@external"]

[[rule]]
id = "commands-through-facade"                   # review-only at the time (architecture.md section 7)
kind = "layer"
from = "commands"
may_reach = ["facade", "contracts", "@other-units", "@external"]

[[rule]]
id = "contracts-pure"
kind = "forbid"
from = "contracts"
to = ["django.**", "rest_framework.**"]
transitive = true

[[rule]]
id = "facade-signatures"
kind = "surface"
zone = "facade"
signature_types = { deny = ["django.**", "rest_framework.**", "@models"] }
```

Each `[[rule]]` is one rule from the doctrine.
Five config formats become one.

## 2. Django coupling that is not an import

All extraction is static and works per file.
The extractors run on the ruff AST.

- **App labels and the model index.** Read `label =` and `name =` from each `AppConfig`. Find the model classes by resolving base classes through the repo, as `wiring_interfaces.py` already does. Expand abstract bases, so inherited fields belong to each concrete model. Ownership comes from the defining module, not from `_meta.app_label`, because of legacy labels such as `ee.Role`.
- **String FKs.** For `ForeignKey("app.Model")`, `"self"` and the lazy `"Model"` form in the same app, resolve through the label map. Read `settings.AUTH_USER_MODEL` once from the settings literal.
- **Reverse accessors.** Derive them; do not observe them. For each relation field that crosses units, the accessor is `related_name`, or `<model>_set` for an FK and M2M, or `<model>` for an O2O. Expand the `%(class)s` and `%(app_label)s` templates on abstract bases. `"+"` with no `related_query_name` is sealed.
- **`apps.get_model`.** A literal `("label", "Model")` or `"label.Model"` becomes a `model-ref` edge. A variable argument becomes an `unresolved` edge. An unresolved edge is always visible and is ledgered like any other.
- **Signals.** For `@receiver(sig, sender=X)`, `sig.connect(fn, sender=X)` and the string sender form, emit an edge from the receiver to the sender model's unit.
- **Celery and Temporal names.** Build a name table from `@shared_task(name=...)` (the default name is the dotted path), from `@workflow.defn(name=...)` and from activity names. Calls such as `send_task("n")`, `signature("n")`, `execute_workflow("n")`, `start_workflow`, `execute_activity` and beat `"task": "..."` entries then resolve to the defining unit.
- **URLs.** `include("dotted.urls")` is a module edge. Parse `routes.py` for `routers.*.register(prefix, ViewSet, basename)`, so a test that calls `reverse("basename-list")` can become an `http` edge. That edge is information only; see section 6.
- **Migrations.** Read `dependencies` and `run_before` from the literal lists, and read model strings from operations. With `products/db_routing.yaml`, this replaces the Django-booting invariant `test_migration_dependencies_share_a_database`.
- **`dotted-ref`.** Any string literal that resolves to a first-party module or symbol is an edge. This one cheap rule catches settings dotted paths, `mock.patch` targets and `import_string`.
- **Query-kind dispatch.** A declarative `[[registry]]` extractor reads core's dispatch function, in the form `if kind == "K": from <m> import R`, and builds a map from each kind to its module. A test that builds a `KQuery(...)` or `{"kind": "K"}` and calls a dispatcher emits `drives`. This is the same logic that lived in `crossings.py`, moved into an extractor.

**Can `django.setup()` be avoided? Yes, on the check path, always.**
Django stays as an **oracle**: a nightly job, or a CI job that runs when `models/**` changes.
It boots Django and diffs the `apps.get_models()` fields and accessors against the static model index.
A mismatch is a bug in `bound`, not a finding in user code.
This keeps the fast path honest without paying the roughly 10 s boot cost in pre-commit.

## 3. Engine and speed

- **Language: Rust.** Parse with ruff's `ruff_python_parser` and `ruff_python_ast`, pinned by git rev. ruff does not publish these crates as a stable API, so expect a pin bump every few months. tach makes the same trade.
- **Not grimp or `ruff analyze graph`.** Both see module imports only, with no names, spans or string channels; wrapping seven more channels around them rebuilds this tool anyway.
- **Not a tach fork.** Its module, interface and `depends_on` model is the part to replace.
- **Not ty, for now.** It would close the "instance the consumer already holds" residual, but it has no Django model semantics and no embedding API.
- **Two phases.** Phase 1 maps each file to its facts. It is a pure function of the file bytes, cached by content hash (blake3) in `.bound/facts`. Phase 2 links the facts across the whole repo in memory and evaluates the rules. The repo has about 23k first-party `.py` files, and linking is hash-map work. The author's estimate, not measured: a cold run takes a few seconds on all cores, and a warm run after a commit that touches 20 files stays under 300 ms.
- **Diff scoping filters the report, not the evaluation.** A new FK in product A creates a violation sited on product B's model. For that reason, phase 2 always runs on the whole graph, and the report shows the findings that touch changed files, plus every ledger delta.
- **No separate daemon.** `bound lsp` is the long-lived process, and it keeps the linked graph hot for the editor. The commands are `check`, `lsp`, `explain`, `ledger`, `query` and `emit`.

## 4. Debt and adoption

- **One format and one key.** Each line is `rule-id  from-symbol  to-symbol  kind  count`. The key has symbols and no line numbers, so an edit never churns the file. The ledger is per unit, at `products/<p>/bound.ledger`: CODEOWNERS then routes the debt to its owner, and a ledger of about 1,000 lines stops causing merge conflicts.
- **Strict equality, as the existing ledger does.** A finding without a line fails. A line without a finding fails as stale, and `bound ledger --shrink` removes it. That is the only automatic write.
- **Growth is a scanner change, not a line.** This is the existing `ledger_growth.py` rule, made generic. Lines may be added only when the same diff changes a rule's definition, which records the findings that exist when the check lands. Otherwise growth needs `bound ledger --admit <rule> --reason "..."` and an approval from the code owners of `boundaries.toml`.
- **Adoption.** Import all current debt in one move: the tach TODO blocks, the import-linter `ignore_imports` TODO lists, `isolation_baseline.txt`, `model_crossing_uses_baseline.txt`, and the `drives` and `reverse-accessor` lines. Run the old tools and `bound` side by side for one release, and diff their verdicts. Then delete the old tools.
- **The isolation rung is computed, not declared.** It derives from the seals plus an empty ledger. `product.yaml` holds `isolated: true` as a claim that the linter verifies. `bound emit turbo-inputs` generates the narrowed `backend:contract-check` inputs from the zones and the live `drives` edges, so the "inputs miss X" family of checks disappears.

## 5. Reporting

- **Humans.** Diagnostics in the rustc style, with the edge chain spelled out: `ForeignKey("other_product.Thing") here adds Thing.widget_set (reverse-accessor), seen by any caller`. Each one also shows the rule id, a one-line why and the doctrine anchor. CI gets GitHub annotations and a summary table with the new findings, the stale lines and the ledger delta per unit.
- **Editor.** LSP diagnostics and code actions, only where the fix is mechanical: add `related_name="+"`, or rewrite an import to the facade re-export when one exists.
- **Agents.** `--format jsonl` gives one finding per line, in a stable and versioned schema: `rule`, `severity`, `file`, `span`, `edge{from,to,kind,via}`, `ledger{status: new|ledgered|stale}` and `fix{class: mechanical|move-code|add-facade-fn, edits[], instruction, doctrine_url}`. The instruction text comes from the doctrine ("move the code, do not permit it"). It never suggests `--admit`.
- **Ask before you write.** `bound query --from products/x/backend/logic --to products.y.backend.models.Foo` answers "allowed?", and when the answer is no, it names the facade function or contract that already exists. That one command prevents most agent violations before they happen.
- **Exit codes.** 1 means a new violation, 2 means only stale lines (auto-fixable), and 3 means a config error.

## 6. Deliberately left out

- **Type and flow inference.** The three declared residuals stay residuals and show up as `unresolved`.
- **Runtime validation at registration** (`issubclass` at the door). The doctrine is right that it belongs at runtime.
- **HTTP-by-URL coupling enforcement.** The doctrine closes it by construction. `bound` emits `http` edges as information only.
- **Hand-declared `depends_on`.** The graph is computed. A declared list is a copy of the code, and it drifts.
- **Security and pattern rules** (`team_id`, egress, CSP). They are code patterns, not boundaries, and they stay in semgrep.
- **TS/frontend boundaries, test selection and maturity scoring.** These are consumers of `bound`'s JSON or graph, not part of it.
- **A plugin API** in v1. Every rule kind is built in, so the speed and the JSON schema stay under control.

## 7. The three hardest rules

**(a) Wiring couplings.**
The doctrine has three parts.
A behavior class crosses only when it implements an approved base, transitively.
It must live in a wiring location.
And that location stays in the contract-check inputs while an outside test drives it.

```toml
[[rule]]
id = "facade-wiring"
kind = "surface"
zone = "facade"
exported_classes = { must_subclass_any = ["posthog.hogql_queries.query_runner.QueryRunner",
  "ee.hogai.tool.MaxTool"], or_decorated = ["temporalio.workflow.defn", "temporalio.activity.defn",
  "celery.shared_task"], must_live_in = "wiring.*" }
[[rule]]
id = "wiring-watched-while-driven"
kind = "coverage"
zones = "wiring.*"
when = { edge = ["drives", "import"], from = "tests@other-units" }
artifact = "turbo:backend:contract-check.inputs"
granularity = "subtree"     # covers a product that watches only one subtree of hogql_queries/
```

The subtree granularity replaces a special per-product repo invariant.
The `drives` channel for Celery, Temporal and Max names comes from the same name tables, so the three "stay by presence" locations from the doctrine can finally be measured as well.

**(b) Consumer default-deny on crossing models, plus `get_model`.**

```toml
[allowance.models]           # MODEL_CROSSINGS, moved out of Python; only shrinks
some_product = ["SomeModel", "OtherModel"]
another_product = ["..."]
[[rule]]
id = "crossing-use-shape"
kind = "use-shape"
targets = "@allowance.models"
allow = ["annotation", "does-not-exist", "nested-class-attr", "meta",
  "manager-chain:values|values_list|count|exists|aggregate", "subquery-embed"]
```

`get_model` needs no rule of its own.
It is a `model-ref` edge into the `models` zone, and `cross-unit-surface` already denies that zone.
Because tests are in scope, the documented blind spot ("test modules stay out of scope ... not permission") closes too.
Migrations get one `allow` that cites the historical registry.

**(c) Sealed cross-unit relations, without Django.**

```toml
[[rule]]
id = "cross-unit-relation-sealed"
kind = "relation"
when = "field.target.unit != field.owner.unit"
require = { related_name = "+", related_query_name = "absent" }
[[rule]]
id = "relation-direction"
kind = "relation"
when = "field.target.unit is product and field.owner.unit != field.target.unit"
deny = true
allow = [{ owner = "<owner_product>", target = "<target_product>.<Model>", reason = "watched-models allowance", doctrine = "architecture.md#wiring-couplings" }]
```

This is the rule that forced `django.setup()` at the time.
Statically, it needs four parts: abstract-base expansion, name templating, the label map and module-based ownership.
All four live in the model index from section 2.
The Django oracle job keeps that index truthful, and the 231 `reverse-accessor` ledger lines become the oracle's first test fixture.
