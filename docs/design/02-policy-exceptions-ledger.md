# 2. Policy, exceptions and the ledger

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 11, 2026. D67 is decided but not built yet.

## Decided

**Three files, split by the direction of a change** (owner, Nov 5).
- `hoglint.toml`: architecture and policy, hand-written, at the repo root.
- `hoglint.exceptions.toml`: reviewed allowances.
  An addition is always bad and a removal always good, so code-owner review can tell the direction.
- `hoglint.ledger`: generated debt.
  Growth from code fails.

**All hand-written policy goes through code-owner review.**
The generated ledger stays open, guarded by the growth check (agreed by exclusion, confirmed by the owner on Nov 9).
This replaced the Nov 5 idea that `hoglint.toml` edits are neutral.
An adversarial review showed that relabels in `hoglint.toml` can launder policy (review blocker 3).

**The ledger: one line per (rule, identity), generated, no prose** (architect defaults, Nov 4; none overridden).
- Two checks, kept apart:
  - **Equality.**
    The scan and the ledger match line by line, with counts.
    A stale line fails, so a fixed finding must leave the ledger.
    The hook (`check --index`) judges a commit, not HEAD (owner, Nov 18): a new finding that HEAD has as often, and a line that is out of step at HEAD at the same counts, do not block it; CI judges them.
    A commit that changes the policy is judged against the ledger alone.
  - **Growth.**
    Per growth key, against the merge base.
    Growth from code fails.
- **Identity:** module-level, with a count; the scope stays on the finding.
- Every ledgered rule declares its identity and its growth key, and the guide prints them (review blocker 4, agreed Nov 9).
- A growth key must be a subset of the identity (found while porting, Nov 9).
- **Attribution** replaces a gate by directory.
  The base scanner and policy run on head code.
  Growth there came from code and fails.
  Growth that appears only with the new scanner or policy came from the rule change (D9).
- **Move admission.**
  A move is admitted only when the same couplings existed before, with the same counts.
  New uses bundled into a move fail (review item 9; agreed Nov 9; shape-aware since phase 3).
- **Location-free growth keys** make a consumer move free without a relocation list (round 4, Nov 6).

**Exceptions** (owner, Nov 9): required fields and match keys; a stale exception (one that matches nothing) fails; the owners of the target package approve a boundary allowance; the tool's owners approve a relaxation; a passed `review_by` date shows without blocking unrelated PRs; a test-only scope exists.

**Owners.**
Findings and debt are attributed to owners through a pluggable provider.
Reason (owner, Nov 4): a linter can show who owns how much trouble and what is grandfathered.
Owners are computed at render time; no rule reads them (D12).

**No noise in the ledger** (owner, Nov 11, D66).
The ledger must not grandfather noise.
The architect's reading: this is met at the rules, before the first ledger write, not by weakening the ledger.
See `10-findings-triage-method.md`.

**Growth is judged per product** (owner, D67, Nov 11).
Every rule's growth key gains the source package.
A coupling removed in one product no longer pays for a new one in another.
Moves inside a product stay free.
A move across products goes through move admission.
- Why: before D67, growth was `after <= before` per growth key, and most keys had no source package (`model-crossings`: `dst.symbol, edge.label`; `public-surface`: `dst.symbol, edge.kind`; `facade-signature`: `dst.symbol`).
  A second opinion from another model found this; the architect confirmed it in `hoglint/ledger/growth.py`.
  "No new coupling" and "no increase in a count of couplings" are different guarantees.
- The earlier form summed crossings growth over all consumers, so that moving or splitting a consumer module carried its lines.

**The count-based ledger is withdrawn; a rule gets an `off` state; the hook's messages never suggest regenerating the ledger** (architect, Nov 11).

## Why

- Split by direction (owner, Nov 5): the policy file stays free of exception clutter, and a review can tell a good edit from a bad one.
- Equality: a list that is never compared against a scan is a copied list, not a ratchet.
  Two-way equality needs one generated file.
- Module identity: spike D measured scope identity at 1.17x the lines, with no gain in precision on 34 replayed commit pairs.
- Attribution: the spike D replay separated code growth from rule growth.
- Per-identity lines and equality: a count cannot see a swap, and it has no line that can go stale.

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| Exceptions inside `hoglint.toml` | Clutters the config; review cannot tell direction | Owner, Nov 5 | not published |
| `hoglint.toml` edits are neutral | Relabels can launder policy | Review blocker 3, Nov 6; replaced Nov 9 | `docs/research/adversarial-reviews.md` |
| Receipts for relaxations | Judged a quick patch, not a design | Owner, Nov 9 | not published |
| A direction classifier as a required check; path-based code owners on mixed files | Same weakness, more machinery; cannot see direction | By exclusion, Nov 9 | not published |
| A `[[relocations]]` list | The owner did not like it; location-free growth keys make it unneeded | Owner, Nov 6 | `docs/research/exports-and-shapes.md` |
| No admission path for moves | Blocks real migrations | By exclusion, Nov 9 | not published |
| Scope identity for the ledger | 1.17x the lines, no gain | Architect, Nov 4 | `docs/research/data-model.md` |
| YAML for debt | A generated text ledger was chosen; no other reason recorded | Nov 5 | not published |
| A separate gate for shrink caused by a rule change | Policy and scanner changes already need code-owner review | By exclusion, Nov 9 | not published |
| A count per rule and product below a level | Loses swaps and staleness; a product can get worse | Owner, Nov 11 | `docs/history/decision-log-3.md` |
| Baseline today's whole output | Grandfathers noise | Owner, D66; the second opinion agrees | `docs/research/second-opinion-ledger.md` |
| Regenerate the ledger as the repair path | Erodes enforcement | Second opinion, Nov 11; architect decided | `docs/research/second-opinion-ledger.md` |

## What would change the answer

- Move friction that proves too expensive.
  The second opinion says narrower count buckets are a compromise only if the guarantee is described honestly as "no net increase within these buckets".
- A measured churn per identity grain from replayed commits.
  The Nov 4 proposal said to measure before choosing.

## Open

- **D67 is decided but not built.**
  A growth key may need to name `src.package` where the identity holds `src.module` (today growth columns must be identity columns), and the preset's growth keys change.
  Planned for the next engine phase, with the defects from the findings triage.
- **Ledger shards per source product.**
  The second opinion: one logical ledger, stored in files per product, never per file or per team.
  The Nov 4 default was one central file.
  Not decided.
- **An `experimental` rule state** (own report, no ledger, no blocking) is the second opinion's proposal.
  Only `off` is decided.
- **Ledger identity grain.**
  The question (Nov 4): is a finer grain more future-proof? It was left for deliberation.
  The module-level default stayed after spike D.
  No later decision.
- **The `public-surface` identity holds `edge.kind`** (architect, Nov 9, to restore the baseline's scan lines).
  The findings triage of Nov 11 named the double count as the largest single cause of noise: one row at the import, one more at every later use of the name.
  Since T3 the rule sets `uses_at_import`, so a later use of a name its module imports is judged once, at the import.
  A use with no import of its own, such as a name reached as an attribute of an imported module, keeps its own row.
  So does a use whose import the rule judges clean: the rule can tell the two apart (by `edge.kind`, `edge.context` or the source symbol), so the import's judgment does not cover the use.
  The identity still holds `edge.kind`.

## Sources

`docs/research/data-model.md` (sections 9, 13, 15 to 19); `docs/research/adversarial-reviews.md`; `docs/research/exports-and-shapes.md`; `docs/history/decision-log.md` (D9, D12); `docs/history/decision-log-3.md` (D66, D67, "How existing findings are carried"); `docs/research/second-opinion-ledger.md`; not published (the plan, the design page, the triage reports, the owner's messages).
