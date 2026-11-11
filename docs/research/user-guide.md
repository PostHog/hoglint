# hoglint user guide (proposed v0)

> A user guide written for blind user tests, Nov 9, 2026: four testers got this guide and a task each, not the code, and answered as users of a tool that was not built yet.
> Partly replaced: the current behavior is in `docs/design/` (mainly `02-policy-exceptions-ledger.md`, `03-levels-and-isolated.md` and `04-rules.md`). The section "What changed since" lists where this guide no longer matches.

## What changed since (checked Nov 11, 2026)

- **Default zone.** A product file outside the zone table now lands in a default private zone, `internal`. In this guide such files read as unknown (lesson U1 below).
- **Growth is judged per product** (D67, decided by the owner Nov 11; not built at the time of writing). Every growth key gains the source package, so a coupling removed in one product no longer pays for a new one in another. The "summed over all consumers" growth key for `model-crossings` below is replaced. Moves inside a product stay free; a move across products goes through move admission.
- **Isolated is specified apart from lint acceptance.** Ledger lines and exceptions never remove impact edges; a safety-relevant unknown or unwatched wiring forbids the test skip; the `requires` list of the level is published. This guide leaves those questions open (lesson U2).
- **Exceptions** have required fields and match keys; a stale exception (it matches nothing) fails; a passed `review_by` is visible without blocking unrelated PRs (lesson U3).
- **Commands.** `hoglint plan` was not built. The CLI has `check` (with `--index` for the staged snapshot, `--write-ledger`, and an advisory `--changed`), `explain`, `query --by owners|target`, `compare --base <ref>`, `explain-config`, `scan` and `summary`.
- **Rules.** The preset grew from the 19 rules below to 31; for example `export-shape`, `facade-logic`, `prod-imports-test`, `dynamic-imports`, `own-database` and `health-checks-no-dags` were added. Rules use eleven primitives: `edge`, `entrypoint`, `export`, `signature`, `use-shape`, `reach`, `cycle`, `layout`, `coverage`, `level`, `inventory`.

The guide itself follows as the testers read it, with one change: the exceptions example uses invented module names.

---

hoglint is PostHog's boundary linter.
It is not built yet.
This guide describes how it is meant to behave, so you can reason about what it would report.
Where this guide is silent, say so in your answer instead of guessing.

## What it does

hoglint reads the Python source once and turns it into facts and edges.
An edge is one coupling between two pieces of code: an import, a use of a name, a string that names code, a model label, a relation between models, a migration dependency, a signal receiver, a callback registered into a hook.
Each edge records:

- `surface`: the name the code wrote (for example `posthog.models.Insight`)
- `via`: the re-export hops
- `origin`: where the name finally resolves (for example `products.product_analytics.backend.models.Insight`)
- `context`: `prod`, `test`, `type_only`, `function_local`, `conditional`, `migration`

Then it labels every file and symbol from config (`package`, `tier`, `zone`, `role`, `owners`, ...), and checks rules against the labeled edges.

## Commands

| Command | What it does |
|---|---|
| `hoglint check` | Runs all rules. Exit 0 clean, 1 violations, 2 incomplete. Exit 2 wins over exit 1. |
| `hoglint check --write-ledger` | Rewrites `hoglint.ledger` from the current scan. |
| `hoglint explain <path or edge>` | Shows each label and the config line that set it, or why an edge was selected and how the ledger treated it. |
| `hoglint query --by owners` | Open findings and accepted debt per team. |
| `hoglint compare` | PR mode against the merge base. Lists four things apart: findings resolved, findings new, ledger lines added and removed, policy entries changed. Also prints new package-to-package edges. |
| `hoglint plan <package>` | (proposed) What moving code into a package would touch. |

## Labels

| Field | Values | Set by |
|---|---|---|
| `package` | product name, `core`, a named core package (`egress`, ...), a leaf (`owners-yaml`, ...) | path globs. `products/{product}/**` sets `package = "{product}"`. |
| `tier` | `core`, `product`, `leaf` | same globs. `posthog/**`, `ee/**`, `common/**` are core. `packages/{pkg}/**` are leaves. |
| `role` | `foundation`, `feature` | products only. Foundation: access_control, notifications, event_definitions. |
| `zone` | `facade` (includes `contracts`), `presentation` (includes `routes.py`), `logic`, `models`, `migrations`, `tasks`, `commands`, `tests`; core has `composition` and `infrastructure` | paths inside `products/{product}/backend/` |
| `owners` | team names | `owners.yaml`, `product.yaml` |

Each field declares where it must be set.
Outside that domain the value is a known `none`.
Inside it, a missing value is `unknown`, and unknown never passes a deny rule.
Two config lines that set the same field differently are a config error unless one says `override = true`.
Defining the same rule twice is an error.

Zones inside a product (`products/{product}/`):

```
backend/facade/**                      facade
backend/facade/contracts.py, enums.py  contracts (inside facade)
backend/presentation/**, routes.py     presentation
backend/logic.py, backend/logic/**     logic
backend/models.py, backend/models/**   models
backend/migrations/**                  migrations
backend/tasks/**                       tasks
backend/management/commands/**         commands
**/tests/**, **/test_*.py              tests
```

## Exports: what is public

A product is public only through its exports.
The preset declares:

- `facade`: `backend/facade/**`, audience everyone, shape `contract`.
- `registration`: `backend/routes.py` and registry modules, audience core composition code only.
- `presentation-adapter`: named mixins (for example access-control serializer mixins), audience listed per adapter.

Shape `contract`: data crosses as frozen dataclasses; behavior as an ABC that core owns.
A Django model, manager or queryset is never allowed, also not inside a dataclass field, a signature, a base class or a re-export.
The check is recursive.

## Registries

Core finds product code through registries, never through lists of product names.
A registry names a loader, a module convention, an attribute and an ABC.
Example: the loader `posthog.products.load_product_modules` finds `products/{product}/backend/webhook_consumers.py`, reads `WEBHOOK_CONSUMERS`, and each entry must subclass `posthog.ingress.contracts.WebhookConsumer`.
The loader call becomes `registers` edges, so test selection sees them.
`import_module`, `import_string` or `apps.get_model` with a computed argument outside a declared loader is a dynamic-import fact: it makes rules that need it incomplete, and it fails the Isolated level.

## Rules in the PostHog preset

| Rule | Primitive | Fires when |
|---|---|---|
| `public-surface` | entrypoint | Code in another package names a product symbol that is not exported to it. Judges the surface the caller wrote. Type-only imports count. |
| `presentation-uses-facade` | edge | Presentation code imports any product's backend (its own product too) outside facade and presentation. |
| `facade-no-drf` | edge | Facade code imports DRF, `django.http` or presentation. |
| `contracts-no-django` | reach | Contracts reach Django through any import chain. |
| `facade-signature` | signature | A public facade function accepts or returns a model, manager or queryset; or has no annotation; `Any`, bare `dict` and `dict[str, Any]` count as unknown. |
| `model-crossings` | use-shape | Code outside a model's package uses the model in a shape other than annotation, isinstance, exception, nested class attribute or type use. Judged at the origin, so a re-export hides nothing. Counted per (target, consumer module, shape). |
| `get-model` | edge | `apps.get_model` names another package's model. Counted. |
| `reverse-accessor` | edge | A relation into another package is not sealed (`related_name="+"`, no query name). |
| `drives` | edge | A test outside a product executes the product's wiring. Counted. |
| `wiring-watched` | coverage | The product's `turbo.json` contract-check inputs do not cover: wiring other packages drive, modules with signal receivers or installed callbacks, and modules that outside tests `mock.patch`. |
| `receiver-direction` | edge | A receiver listens to another product's sender. Counted. |
| `installs` | edge | A product registers a callback into another package's hook (for example `register_post_delete_hook(fn)`). The callback's module must be watched. |
| `thin-commands` | edge | A management command reaches its own product's models, logic or tasks instead of its facade. |
| `no-new-package-cycles` | cycle | A new product-to-product dependency closes a cycle. Edges: import, reference, dotted-path, model-label, relation. Existing cycles are ledgered. |
| `foundation-direction` | edge | A foundation product depends on a feature product. |
| `infrastructure-direction` | edge | Infrastructure code depends on a product. |
| `no-product-lists-in-core` | edge | Core imports or names a product outside a registry. |
| `product-code-at-home` | layout | A file belongs to a product package but lives outside `products/`. Counted per package. |
| `leaves-stay-leaves` | edge | A leaf package imports core or product code. |

Level `isolated`: a product is Isolated when every required rule passes for it.
Then CI may skip the full Django suite when only that product changes.
An unresolved or unknown edge counts against the product.

## Findings

A finding prints the rule, its identity (no line numbers), the primary location, the witness edges, and remedies.
Remedy kinds: `permitted-destination`, `documented-replacement`, `safe-rewrite`, `needs-public-api`, `seal-relation`, `admit-debt`.

## The three files

| File | Written by | An addition is | A removal is |
|---|---|---|---|
| `hoglint.toml` and the preset: rules, labels | people | judged by effect: a change that removes findings on the base code (a relabel, a widened export, a weaker rule) is a relaxation and needs the tool's owners review | same |
| `hoglint.exceptions.toml`: reviewed allowances, each with a reason and owners | people | always needs the tool's owners | always fine |
| `hoglint.ledger`: accepted debt, generated, no prose | hoglint | fails the growth check if the code grew the debt | always fine |

Two ledger checks run separately:

1. **Equality.** The scan of the head code and the ledger file match line by line, with counts. A new line fails. A stale line fails too, so fixed debt leaves the file in the same PR.
2. **Growth by attribution.** hoglint scans base and head code with the base scanner and base policy, and sums debt per growth key. For `model-crossings` the growth key is (target, shape), summed over all consumers. More debt in head code is "code growth" and fails. More debt only under the new scanner or policy is "rule growth" and passes, listed in the PR. This runs again in the merge queue on the merge candidate.

Moves: moving or splitting a consumer module keeps the sums per growth key, so it passes.
A target that moves keeps its identity through the model's table name, the package id, or git rename detection.
A split that git cannot follow fails: land the move and the refactor as separate PRs.
Moving code into a product passes only when the same couplings existed before the move, with the same counts, and the tool's owners admits it; new uses bundled into a move fail.

## Exceptions file format

```toml
[[exception]]
rule    = "model-crossings"
match   = { src = "ee.api.reports", dst = "orders.Order", shape = "instance-many(filter)" }
reason  = "Reports read orders until the orders facade exists."
owners  = ["team-orders"]
review_by = "2027-03-01"
```

---

## How it was used

On Nov 9, 2026, two kinds of blind tests ran against the design.
Neither kind of tester saw the code or the design notes.

**Blind users.**
Four testers each got this guide and one task: add a new product, fix a finding, review a PR as the tooling team, and move code into a product.
Their answers were graded against an answer key written before the run.

| Task | Result | Missed | Notable |
|---|---|---|---|
| 1. New product | All 10 key findings, exit 1; the webhook and routes registrations pass | nothing of weight | Treated a cross-product foreign key as an open Isolated question (the key flags it as a question for the owner too) |
| 2. Fix a finding | Right pick (a facade function that returns an existing DTO); a ledger rewrite seen as a trap; an exception as the honest fast path | the consumer-swap door; moving code under a test path | Found that raw SQL on another product's table is invisible; found that existing facade functions return models or take querysets, which is `facade-signature` debt from day one |
| 3. Review a PR | Full match: request changes, a relabel is laundering, a "temporary" exception rejected, a ledger move only after a diff check | none | |
| 4. Move code | A `git mv` PR first; the "add the assign line first" path found; persisted dotted paths (Temporal names, pickled paths) found | misread existing importers as new findings (they were already ledgered under the assign line) | A real design catch: removing the now-dead `[[assign]]` line after the move reads as a relaxation on base code |

What the users taught the design (each of the four hit at least one):

- **U1.** Product files outside the zone table (`apps.py`, `admin.py`, `receivers.py`, `webhook_consumers.py`, `backend/api/`, `backend/temporal/`) read as unknown. They need a default private zone. Built as `internal`.
- **U2.** Which rules Isolated requires, and whether ledger or exception lines count as passing. Users hit this open decision in tasks 1, 2 and 3. It must be decided and printed. Decided: see `docs/design/03-levels-and-isolated.md`.
- **U3.** Exceptions: required fields, match keys, what happens at `review_by`, and that a stale exception fails.
- **U4.** The identity and growth key of each rule must be written in the guide. Users could not tell how `public-surface` and `drives` lines survive a move.
- **U5.** SQL and HogQL table names are not edges, so raw SQL on another product's table passes silently. A gap in what the tool can see.
- **U6.** Relaxation check: removing an assignment that matches no file in head code must not count as a relaxation. Judge relabels on base and head.
- **U7.** Greenfield crossings: a new product's first coupling has no path except a facade function or an exception. State it.
- **U8.** A consumer swap (one consumer drops a coupling while another adds the same one) stays undetected by users too. `compare` must show it plainly, as paired added and removed lines on one growth key. D67 (growth per product) later addressed swaps across products.

**Blind designers.**
Two designers worked from the problem statement and the repo only, one on registries and one on generated code.
Both converged with the architect's design on the core: registries on the product loader, provenance per generated file, never running generators inside the linter, findings placed at the input, and splitting a shared generated schema module per product.
That convergence is evidence against anchoring on these topics.
New ideas they brought:

- **Registries.** A slot is a loader, a path, its exports, a lifecycle (setup, first request, worker start, on demand) and contract tests; a slot change runs the product suite plus the slot's contract tests, not the full suite. Fixed files for wiring (cross-owner hook calls in one `wiring.py`, foreign-sender receivers in `receivers.py`, `apps.py` holds only AppConfig fields) make watching trivial. Django's installed-apps list stays a hand-kept list on purpose, because Django needs it before any registry runs; lint its completeness instead (installed apps equal the components with an `apps.py`). Core "hub" modules that reach more than N products need an allow entry, ratcheted; most edits to such hubs come with a product change and so run the full suite, which makes moving hubs to slots a CI win. A misnamed slot registers nothing, so a slot-named file in an uninstalled app is an error, and loaders raise on near-miss names. A registry that swallows import errors makes a broken product read as absent. A path-based slot is its own manifest; no separate manifest file.
- **Generated code.** Generated files come in shapes (projection, split, placed, aggregate) and lifecycles (regenerated, scaffolded, vendored). Migrations are scaffolded, not generated: many carry hand-written operations under a generated header, so headers are never decisive. A symbol origin map (a sidecar from the generator) is needed only for aggregates. A frontend product manifest that inlines every product's manifest is a second aggregate. Files marked as generated collapse in PR review, so a product type that lands in a core aggregate is invisible there; surface it on the input. One registry of generators can derive the `.gitattributes`, ignore files and CI drift steps.
