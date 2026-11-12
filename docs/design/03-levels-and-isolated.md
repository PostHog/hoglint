# 3. Levels and the Isolated test skip

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 12, 2026.

Background: in the PostHog monorepo, a product on the Isolated rung skips other products' test suites when only that product changes.
The skip is sound only if no coupling is missed.
The doctrine is in the monorepo's `products/architecture.md` ("What makes the skip sound").
hoglint expresses rungs like this one as **levels**: named sets of rules that a product passes or fails.

## Decided

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
- The rule `request-path` is a `reach` with `through_denied`: it starts at the modules the Django settings name under the request-pipeline keys (middleware, authentication backends, the session engine, the default classes of the REST framework and its exception handler), and at the module of the class every API view inherits.
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

**A wiring location without `__init__.py` is watched through its files** (Nov 12).
Each module file directly inside the directory stands for it, as `__init__.py` stands for a package: `wiring-watched` requires each of them.
Files further down and the tests under it are not asked for.
A directory with no file directly inside falls back to every file under it.

## Why

- A green lint result is not a permit to skip tests.
  Ledgered debt says "accepted", not "covered" (the second opinion's first ranked change, Nov 9).
- Blocking a PR on a gap it did not cause repeats a known failure (Coverity, Google's static analysis experience).
- Entry modules match how facades and signal receivers are treated.

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
  Agreed on Nov 9.
  The preset is to hold it; the list was not found written down in the notes.
- **Selecting a rule by a product's level or claimed maturity** is not built (architect, Nov 11).
- **A level ratchet** (a product that holds a level cannot drop).
  It works through the ledger's level lines and the equality check.
  No decision entry builds anything beyond that.

## Sources

`docs/research/data-model.md` and `data-model-2.md` (sections 7, 13, 18, 20); `docs/research/prior-art.md`; `docs/research/prior-art-second-opinion.md`; `docs/research/second-opinion-ledger.md`; `docs/history/decision-log-3.md`; not published (the plan, the design page, the progress log).
