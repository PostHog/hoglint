# Prior art, round 2: synthesis

> The architect's synthesis of the second prior-art round and the second blind round, Nov 9, 2026, written before the second opinion.
> Partly replaced: the decisions it asked for are in `docs/history/decision-log.md` and its continuation files, and the design they led to is in `docs/design/`.

The round had three prior-art studies and one second opinion:

- `docs/research/prior-art-conformance.md`: architecture conformance checking (reflexion models, DSM, DCL, practice surveys).
- `docs/research/prior-art-fact-stores.md`: code fact stores and code graphs (Kythe, Glean, CodeQL, SCIP, jQAssistant, FAMIX, Salsa).
- `docs/research/prior-art-modules-deployment.md`: module systems, registries, and how static analysis was deployed (JPMS, Sorbet packages, Coverity, Google, Infer).
- `docs/research/prior-art-second-opinion.md`: a second opinion from another model that checked the claims and ranked the changes.

The same round also ran a second blind design round (two designers, for registries and for generated code) and four blind user tasks with a grading pass.
Those inputs are not published; this synthesis uses their results.

## The question (Nov 9): does hoglint repeat the mistakes of past decades?

**Avoided by design:**

- privacy by directory (Packwerk);
- mapping cost for products (reflexion models; globs on the product layout do the mapping);
- order-dependent first-match rules;
- unmapped code that passes silently;
- stale baselines (the ledger is checked for equality);
- line-number identity;
- scanner upgrades counted as code growth (attribution separates them);
- a general query language that only specialists write (QL, Cypher, CQLinq);
- build-coupled extraction (Kythe, CodeQL).

**Repeated or only partly avoided:**

1. Blocking a PR on a result it did not cause (Coverity, Google). The draft had "incomplete" and "exit 2 wins" as written.
2. Batch burndown as the main debt path (FindBugs dashboards, Infer nightly: a fix rate near zero, against more than 70% on the diff). The draft's owners dashboard is that path.
3. Hiding new couplings inside allowed pairs (Rosik on reflexion models). `compare` printed only new package pairs, so a consumer swap was invisible. No blind user and no adversarial reviewer found this.
4. Framework vocabulary in the core edge (FAMIX): Django shape kinds and contexts in core. This is the same issue as adversarial-review blocker 5 (`docs/research/adversarial-reviews.md`).
5. No schema versioning of facts against the ledger (CodeQL dbscheme upgrades, Glean `evolves`).
6. Live feedback came late (Knodel: live feedback cut new violations by about 60%). Pre-commit was only in phase 4.
7. Channels with no runtime oracle: signals, registrations, hooks.
8. Name lists that break as code grows (Lattix rules by class name): presentation adapters selected by name, and the hardcoded product app list treated as debt.

## Proposed changes

**Decisions put to the owner:**

- **A. Incomplete results.** Block only the PR that introduced the unknown. Inherited unknowns go to the tool owner and never block. An unknown still denies the Isolated skip, so the full suite runs. The exit status is computed from what the PR caused. This resolved the open question "who owns an incomplete result".
- **B. Registries by file convention, not `product.yaml`.** The registries designer and the module-systems study agree. The registry attribute must be a literal that hoglint reads without importing. Slots gain `lifecycle` and `contract_tests`. A slot change runs the product suite plus its contract tests. This resolved the open registries decision.
- **C. The hardcoded product app list stays.** The rule is completeness (installed apps equal the product directories that have an `apps.py`), not debt. This corrected the draft.
- **D. Publish the Isolated `requires` list,** and state per rule whether ledger lines and exception lines count as passing. All four blind users hit this gap. It was already open and became urgent.
- **E. Exceptions.** The owners of the target package approve too. Add a test-only scope. Define expiry: a passed `review_by` is a finding. An exception that matches nothing fails.
- **F. A runtime oracle job** for signals, loader imports, Celery tasks and hook lists. A runtime edge with no static twin makes that product incomplete, so it gets no skip.

**Design fixes (no decision needed):**

- F1. A default private zone (`internal`) for product files outside the zone table (`apps.py`, `admin.py`, `receivers.py`, `api/`, `temporal/`). Same root cause as adversarial-review blocker 1.
- F2. The guide prints the identity and growth key of each rule (adversarial-review blocker 4 again).
- F3. `compare` reports new (package pair, edge kind, context) triples. Paired added and removed ledger lines on one growth key show as a swap. Existing ledger lines in touched modules show as advisory.
- F4. Relaxations are judged on base and head. Removing a label that matches no head file is not a relaxation.
- F5. A schema hash in the ledger header. `Edge.id` is run-local only. Shape kinds and contexts become language-pack enums.
- F6. Selectors get no joins, recursion or aggregation. Anything that needs one is a derived-edge producer.
- F7. Registry completeness in both directions. A slot-named file in an uninstalled app is an error. Loaders raise on near misses.
- F8. Fixed files: cross-owner hook calls only in `backend/wiring.py`; foreign receivers only in `receivers.py`.
- F9. Hubs: a core module that reaches more than N products needs an allow entry, ratcheted. 80 to 90% of hub edits ship with a product change, so they run the full suite.
- F10. Generated code gets a shape (projection, split, placed, aggregate) and a lifecycle (regenerated, scaffolded, vendored). Symbol maps only for aggregates. Frontend product registries are composition roots. Migrations are scaffolded. Generated-file headers are never decisive.
- F11. A pinned hoglint version. A scanner bump must show no new incomplete result on master. A noise budget per rule demotes it to advisory.
- F12. `check --changed`, advisory, ships with build slice 5 of the first plan instead of its phase 4.
- F13. TypeScript: a per-file L1 producer on oxc closes the TypeScript gap. scip-typescript (measured at 12 min 46 s and 11.2 GB) is only an off-path checker.
- F14. Measure SQL and HogQL strings that name another product's table (blind user task U5).

