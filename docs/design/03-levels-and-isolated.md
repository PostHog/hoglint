# 3. Levels and the Isolated test skip

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 16, 2026.

Background: in the PostHog monorepo, a product on the Isolated rung skips other products' test suites when only that product changes.
The skip is sound only if no coupling is missed.
The doctrine is in the monorepo's `products/architecture.md` ("What makes the skip sound").
hoglint expresses rungs like this one as **levels**: named sets of rules that a product passes or fails.

## Decided

**What Isolated means** (owner, Nov 16, D108).
A product's Isolated level is blocked only by what can make its test skip miss tests, and these block even when the ledger accepts them:
1. code that is not a product (core, `ee`, `common`, tools, `bin`; production or test) reaches a product file past the product's export, and the product's contract-check inputs leave the file out;
2. a product's own presentation reaches its own internals past its facade;
3. unwatched wiring, with its incomplete items.

A product also earns the level only with the structure: a facade with `backend/facade/contracts.py` (or a `contracts/` package) and a `backend/facade/api.py` that defines a function.
Everything else (loose types, shapes, model crossings, other products' imports) is a lint matter only and does not count for the level.
No hand-kept list of products: the request path is a watched channel (D103).

How the preset says it:
- `reached-watched` is a `coverage` rule with one edge cover: every product file that a source outside the products reaches past the export, read at the module the code names (the surface), must be in the contract-check inputs.
  A watched file is fine: a change there runs the full suite anyway.
  A product that reaches another product does not count; that crossing is lint only.
  Its rows are a list (advisory, no ledger); `public-surface` holds the crossing as debt.
- `presentation-uses-facade` is read through the level key `within_group`: a row whose subjects fall in two groups is not counted, so only a product's own presentation counts.
- `has-facade` is a `layout` rule: `files = ["backend/facade/{contracts.py,contracts}", "backend/facade/api.py: function"]`, a list (advisory, no ledger).
  A `files` entry may hold `{a,b}` alternatives, a file or a directory, and may end in `: function` or `: class`: the file must then also define a symbol with that declaration.
- `wiring-watched` also asks for what the old product lint kept in a narrowing product's inputs (see "What the contract-check inputs watch" below).
- The level requires these four rules and makes all four strict.
  Its `select` reads `path startswith 'products/'`: a product is judged where its facade and its contract-check config live.
- Whether a product skips is still its own config (`package.json` and `turbo.json`); the level says whether that skip would be sound, and a product that skips must hold it (next entry).

Its limits, in plain words:
1. `reached-watched` reads the file the code names, not what that file imports, nor the `__init__.py` files above it (as the request path, D103).
2. An edge whose source tier cannot be told is an incomplete item of `reached-watched` on the source module, so it blocks no product.

**A product that narrows must pass Isolated** (Nov 16).
A product whose own `turbo.json` narrows its contract-check inputs claims the level.
If it fails the level, `check` fails, and no ledger line or exception can accept that: an unsound skip is never debt.
`check` names the level's reasons for the product and the two ways out: fix the reasons, or drop the narrowing.
- In core words, a level may name the outside input in which a group claims it: `claimed_by`, a coverage adapter and its options, the table a coverage rule's `inputs` takes.
  The adapter tells, per directory, whether the inputs that decide there are narrowed, and by which file (`narrowed(handle, directory)`).
  A group claims the level when one of its module files sits in such a directory.
- A claiming group that fails the level fails `check` and `compare` (exit 1), and so does a level whose claims cannot be read (no repo root, an input that does not read, a narrowed file whose group cannot be told): unknown is never clean.
  A claiming group that the level's `select` leaves out fails too: it cannot pass the level it claims.
- The turbo adapter: a package narrows a task when its own `turbo.json` gives the task an `inputs` list without `$TURBO_DEFAULT$`.
  Inputs a package inherits from the root config narrow nothing of its own; that is how the monorepo's test selection reads the skip.
- The preset: `[levels.isolated] claimed_by = { adapter = "turbo", task = "backend:contract-check" }`.
- The adapter's files are part of the run's key, as a coverage rule's are, and the stored run summary keeps each level's claims (protocol 5), so a check that a stored summary answers fails the same way.

Why a level key and not a rule that reads a level's result:
- Rules run before levels, and a level reads rules; a rule that reads a level would need a second order between rules, and its findings would sit next to the level they help to judge (selectors that depend on the level they help compute were turned down on Nov 11).
- A rule's finding is a ledger line or an advisory row, so "no ledger accepts it" would need an exemption; a failed claim is no finding at all, like a stale exception.
- The claim lives in the outside input the coverage rules already read, so the level needs no new field, provider or rule.

**The linter carries the skip's safety** (owner, Nov 4).
Rules that feed the skip block, and unknowns count against the product.

**Isolated is specified apart from lint acceptance** (owner, Nov 9):
- ledger lines and exceptions never remove impact edges;
- a safety-relevant unknown, or wiring that is not watched, forbids the skip;
- other architectural debt may coexist with Isolated, while impact stays conservatively covered;
- the `requires` list of the Isolated level is published.

**An incomplete result belongs to whoever caused it** (owner, Nov 9):
- a gap a PR introduced belongs to its author and blocks that PR;
- an extractor failure belongs to the tool's owners;
- an existing wiring gap belongs to the product's owners;
- an inherited gap may merge with the full required test suite, and stays visible;
- violations and completeness are reported apart.

**Levels are evaluated from rules, never from a baseline** (architect, Nov 4).
The rung of the time was read from a baseline file, which is circular.
- `level` rules run after the rules they read.
  That is the only ordering between rules.
- The Isolated level must not read the wiring rule except through ledger acceptance.

**Hook callbacks: entry modules only** (owner, Nov 9).
Following their imports would add about 170 watched files.

**New producers that feed the skip** (owner, Nov 9): signal edges, installed callbacks, mock patch targets, dynamic imports.

**The request path is a watched channel** (owner, Nov 12).
A product module that core's request pipeline reaches by imports that run at import time is a watched file, like a receiver or a hook callback: it must be in the product's contract-check inputs.
There is no list and no special level; a product keeps the skip for the code the pipeline does not reach.
- The rule `request-path` is a `reach` with `through_denied`: it starts at the modules the Django settings name, and at the module of the class every API view inherits.
  Out of a settings module it follows every settings entry except those under a short list of keys that are not the per-request pipeline (app registration, task and shell imports, the root URL conf, the application entry points, static files, the API schema, logging, caches, and two login-flow keys that a single URL runs).
  An entry whose setting cannot be named is an incomplete item; a key missing from the list is followed, so a stale list adds rows and never hides one.
  A path string in a settings module is followed whatever function holds it.
  It follows module-level imports through core and on through product code, so every product module on a chain is a row.
- Its rows are a list, not debt: the rule is advisory and keeps no ledger lines.
  `wiring-watched` requires the file of every module on each row's chain.
- What the rule cannot tell, `wiring-watched` cannot tell either: an import on the path that does not resolve, or a settings entry whose setting cannot be named, is an incomplete item of `wiring-watched`.
  An advisory rule's own incomplete items do not reach a check's output, so they must be carried.
- A seed written as a path that matches no file is reported (`config:stale-name`), never dropped.

Its limits, in plain words:
1. An import inside a function is not followed.
   A decision that core reaches only through such an import is not on the path.
2. The `__init__.py` of a package above a reached module is not followed, although Python runs it.
3. A name is followed to the module that defines it, not to the module the code wrote.
   A facade that only re-exports the name is skipped, together with what that facade imports for its own functions.

**What the contract-check inputs watch, at parity with the old product lint** (Nov 16).
Besides the drives, request-path, receiver, hook-callback and patch-target covers, `wiring-watched` asks for:
- the model surface: every module of the `models` and `migrations` zones, since a model is reachable without an import (`apps.get_model` strings, migrations, admin);
- every module a registry loads by name (`registry != none`): routes, scopes, data freshness, webhook consumers, health checks, integration usages and hooks, today's highlights;
- every declared permanent interface (`permanent`);
- each wiring location core runs by a name it reads at run time (`runs_by_name`: tasks, temporal, Max tools), as one location that any one watched file in it watches, as the old lint reads it (the owner, Nov 16); query runners stay with `drives`, which reads their channel;
- every product module a settings module names by dotted path, under any key, not only the keys the request path follows;
- the modules of its own product that a product's AppConfig module (`app_config`) imports at module level.

A coverage rule may select modules by an entity selector: `"<entity selector>: module"` needs the file of each module the selector keeps.
`"<entity selector>: location"` needs one watched file in each location those modules stand in: the outermost package or directory without `__init__.py` that the selector keeps, else the module's own file.
A module it cannot tell it keeps is an incomplete item.

Its limits, in plain words:
1. An import inside `ready()` or inside a callback is not followed: hook callbacks and receivers are watched at their entry module (Nov 9).
2. A registry the preset does not declare is not known, so a new loader call needs a registry entry.
   The old lint found every `load_product_modules` call by itself.
3. The model surface is the two zones; a model class defined elsewhere in a product is not asked for.
4. A location run by name is watched through any one file in it, so a change to another file in it does not reach the check.

**A wiring location without `__init__.py` is watched through its files** (Nov 12).
Each module file directly inside the directory stands for it, as `__init__.py` stands for a package: `wiring-watched` requires each of them.
Files further down and the tests under it are not asked for.
A directory with no file directly inside falls back to every file under it.
This holds for the locations `drives` names; a location run by name is watched through any one file in it (Nov 16, above).

## Why

- A green lint result is not a permit to skip tests.
  Ledgered debt says "accepted", not "covered" (the second opinion's first ranked change, Nov 9).
- Blocking a PR on a gap it did not cause repeats a known failure (Coverity, Google's static analysis experience).
- Entry modules match how facades and signal receivers are treated.
- D108: a loose type or a shape does not make a skip miss tests, so it is the wrong lever for the level; the safety setting blocks exactly what can, and every product it blocks can pass by watching files.
  Of the settings the owner weighed (no findings at all; the preset as first built; safety plus imports inside functions; a seal on every import past the door; safety), safety keeps the most skips that are sound.

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| Take the rung from a baseline file | Circular | Architect, Nov 4 | `docs/research/data-model-2.md` |
| Never block on any incompleteness | Too broad as a universal rule; gaps a PR introduces still block | Second opinion, Nov 9 | `docs/research/prior-art-second-opinion.md` |
| One runtime boot as proof of completeness | Overclaims coverage | Second opinion, Nov 9 | `docs/research/prior-art-second-opinion.md` |
| Declared contract tests replace the full suite | Overclaims coverage | Second opinion, Nov 9 | `docs/research/prior-art-second-opinion.md` |
| Rules disabled below a level | Products below the level can get worse, and can stay just below forever | Second opinion; owner, Nov 11 | `docs/research/second-opinion-ledger.md` |
| Selectors that depend on the level they help compute | "Fails isolation" turns into "the rules no longer apply" | Second opinion, Nov 11 | `docs/research/second-opinion-ledger.md` |
| Hook callbacks with their import closure | About 170 more watched files | Owner, Nov 9 | not published |
| A level blocked by every unknown type of twelve rules | A loose type does not make a skip miss tests | Owner, Nov 16 (D108) | not published |
| A level blocked by every import past the door, from anywhere | Counts imports between products, which the owner left to lint | Owner, Nov 16 (D108) | not published |
| A hand-kept list of products that may not skip | The request path is a watched channel (D103) | Owner, Nov 12 and Nov 16 | not published |
| Every file of a wiring location run by name | Each file of the location becomes a watched input; the owner takes the old lint's reading, any one watched file in it | Owner, Nov 16 | not published |
| Only the `__init__.py` of a wiring location run by name, as for `drives` | The owner takes the old lint's reading, any one watched file in it | Owner, Nov 16 | not published |
| The non-product reach as one more `covers` entry of `wiring-watched` | It would move that rule's rows and ledger lines and make each such file a lint obligation; a list read by the level moves none | Builder, Nov 16 | not published |

## Measured state

- Nov 9, the first rule engine on the PostHog monorepo: the Isolated level passed for far fewer products than the rung of the time listed.
  This was flagged as parity work, not as a result.
- Nov 11, after the N3b rewrite, on a snapshot copy: the pass count went up and moved by one product between two review rounds.
  The move was a consequence of D63 (fields of handed-out classes count wherever the class is defined).

## What would change the answer

- The second opinion's rule: keep "boundary rules passed" apart from "safe to narrow tests".
  Debt that affects test-selection soundness must stay visible to the selector, even when it does not block a commit.
- A runtime oracle for signals, loader imports, background tasks and hook lists was proposed in the prior-art round.
  It was agreed only as a runtime audit, never as proof.

## Open

- **The published `requires` list.**
  Agreed on Nov 9; the preset holds it since D108 (Nov 16).
- **Selecting a rule by a product's level or claimed maturity** is not built (architect, Nov 11).
- **A level ratchet** (a product that holds a level cannot drop).
  It works through the ledger's level lines and the equality check.
  No decision entry builds anything beyond that.

## Sources

`docs/research/data-model.md` and `data-model-2.md` (sections 7, 13, 18, 20); `docs/research/prior-art.md`; `docs/research/prior-art-second-opinion.md`; `docs/research/second-opinion-ledger.md`; `docs/history/decision-log-3.md`; not published (the plan, the design page, the progress log).
