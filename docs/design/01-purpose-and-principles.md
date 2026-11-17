# 1. What hoglint is for, and its fixed principles

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 16, 2026.

## Decided

**Purpose.**
hoglint is one boundary linter for a large Python and Django monorepo.
Its case study is the PostHog monorepo.
There it replaces five tools: tach, the boundary contracts of import-linter, the isolation checks in `hogli product:lint`, the crossings scanner, and repo invariants that boot Django.
- A real build, under the name hoglint (owner, Nov 4).
- tach and import-linter are not kept as a base.
  Their outputs serve only as parity oracles while hoglint runs in shadow (owner, Nov 5).
- hoglint can still write tach's module map JSON, so tools that read it keep working until they switch.
- A later `hoglint impact` command is to give other tools one graph of what a change reaches, in place of several partial graphs (owner, in principle, Nov 9).

**Principle 1: the linter carries the safety of the test skip.**
PostHog lets a product on its "Isolated" rung skip other products' test suites when only that product changes.
That skip is sound only if no coupling is missed.
The owner decided on Nov 4 that the linter carries this safety; the whole test-skip doctrine depends on it.
This is the root of most other decisions.
A more lenient test selector does not give this guarantee.

**Principle 2: nothing deletes an edge.**
Ledger lines and exceptions decide whether a finding blocks.
They never remove an edge from any view.
An accepted edge still shows in reach, cycle and impact witnesses.
- Written into the data model (architect, Nov 4).
- Owner, Nov 9, as part of the Isolated recommendations.

**Principle 3: unknown is never clean.**
- Parse failures, unresolved names, unsupported forms and exhausted budgets are stored obligations with a stable identity.
  Rules that depend on absence read them as unknown (D8).
- Selectors use three-valued logic.
  A field outside its declared domain is a known `none`, not unknown (review blocker 1, agreed Nov 9).
- An incomplete run exits 2.
  An empty findings list with an incomplete rule is exit 2, never 0.
- Later applications: an unreadable source always blocks (D32 amended); a column the evaluator lacks is an error, not an empty answer (D43, D54); an unreadable `__all__` leaves a marker (N3b review).

**Principle 4: a cache problem is never a wrong answer and never a failed run.**
It may only cost time (D28).
- Keys come from the bytes actually read.
- A miss is never an error.
- A corrupt store is moved aside.
- Every later cache rule cites it: the summary store (phase 4a), the Django pack cache (phase 4b), damaged record rows (N6 review).

**Principle 5: core holds no framework or company name.**
- Code splits into the `hoglint` core (no Django, no PostHog), the Django pack, and the PostHog preset.
  hoglint itself enforces that direction (architect, Nov 4, after the generality spike).
- Django shapes (lock, write, DRF) moved out of core into the Django pack (review blocker 5).
- The fact format (owner, Nov 9): neutral use positions in core, Django shapes in the pack, language-qualified ids, a schema hash in the ledger, run-local edge ids.
- Checked in the code on Nov 9: no Django or PostHog names in the extractor or core.
- "Facade" is PostHog's word.
  The engine's word is `export` (D48).

**Principle 6: every run is exact.**
A cached or reused answer must equal a full run.
D5 (the whole repo every run) is the current form.
Any reuse must keep this property and prove it with an oracle (D59 point 3, D61).

**Principle 7: one engine; every changed row is named.**
The native engine builds the graph and judges every rule (owner, Nov 13: the Python engine is retired, the graph build first, then the rule judgment).
Python words what the engine judged and holds the rule metadata.
A change keeps every row of the ten parity tables and every stored message, or names each one it changes: against the model repository's stored rows, tables and messages, the stored runs of the hand-written rule cases and of a sample of generated configs, the slot table test, and the real-size compare with a base.
See `05-engine.md`.

## Why

- The test skip needs a static check that does not miss couplings.
- Five tools meant five configs and several debt lists.
  The Django-booting checks cost about 4.5 minutes per main-branch CI run.
  The debt lists grew (tach `depends_on` from 163 to 600 edges between May and October 2026; crossings from 856 to 987 in six weeks).
  The scanners of the time missed couplings.
- Unknown as data: bugs 1 to 6 in the first engine read unknown as clean.
  In the prior art, semgrep reported findings in files that failed to parse as "fixed".
- The cache rule: one cache per machine serves every worktree, so its failure modes must degrade to a recompute.
  An adversarial review showed that one editor save could poison a machine-wide entry.
- No framework names in core: the owner did not want hoglint coupled to PostHog's shape; not every repo has a `products/` folder.
  The generality spike on Sentry, Saleor and Zulip showed that a built-in package, zone and level model failed there.

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| Fork tach | Only about 2,000 lines of tach's Rust would be used; every wanted capability needs a whole-program index tach lacks; one maintainer | Owner, Nov 4 | `docs/research/tach-fork.md` |
| Build on ty's language server or ruff's import graph | Explored on Nov 3; a detector on ty looked like a narrow investigation, not the start of a linter | Owner, Nov 3 | not published |
| Keep tach and import-linter as a base | Both can be dropped completely | Owner, Nov 5 | not published |
| A built-in package, zone and level model | Failed on Saleor, Zulip and Sentry | Agreed by exclusion, Nov 9 | `docs/research/generality.md` |
| Unknown counts as clean, or `ignore` for unresolved | Breaks the test skip's safety | Architect, Nov 4 | `docs/research/data-model.md` |

## What would change the answer

- The doctrine itself.
  If the Isolated test skip were dropped, or moved to a runtime proof, coverage and level rules could advise instead of block.
- A second repo that uses hoglint with no `products/` folder tests principle 5.
  Configs for Sentry, Saleor and Zulip exist in `docs/research/generality.md`.


## Sources

`docs/research/data-model.md` (sections 8, 9, 16, 19); `docs/history/decision-log.md` (D5, D8, D28); `docs/history/decision-log-2.md` (D32, D43); `docs/history/decision-log-3.md` (D48, D54, D59, D61); `docs/research/adversarial-reviews.md`; `docs/research/tach-fork.md`; `docs/research/generality.md`; not published (the plan, the design page, the owner's messages).
