# 10. The findings triage: method and kinds of result

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 11, 2026. The triage reports are not published; shares are given here in general terms.

## Decided

**The ledger must not grandfather noise; findings get a triage before the first ledger write** (owner, D66, Nov 11).
- The architect's reading: a ledger line means real debt that someone chose to carry.
  A rule whose findings nobody would fix is wrong, and it changes.
  Its lines then vanish; they are never accepted.
- Verdicts per kind of finding:
  - **real**: a coupling the rule is meant to catch;
  - **noise**: correct by the rule as written, but not worth fixing; the verdict names the rule change and how many lines go;
  - **tool or config defect**: the engine or the preset reads the code wrong;
  - **policy or cannot tell**: needs an owner's decision.
- Policy changes that come out of a triage are the owner's to approve.

**Method used on Nov 11.**
Four read-only agents, one per group of rules, read a dump of one check of the PostHog monorepo.
The architect then checked five of the largest claims against the dump and the code; they held.

## Result, in general terms (Nov 11)

- About a quarter of the rows were real, most of the rest was noise, and about a tenth were tool defects.
- The largest single cause was a double count.

**Kinds of rule setting that caused noise:**
- An identity that holds the edge kind, so every later use of a name already reported at its import counts again.
- A rule that counts every function of a surface, instead of the functions with a defect.
- A shape rule that knows only one dataclass flavor, so other frozen dataclass decorators read as unknown.
- A rule that treats every import in a surface file as an export.
- A rule without `endpoint = "surface"`, so it reads the inside of a module where only its surface matters.
- A rule that repeats what another rule already reports.
- Type aliases read as unknown although their expansion is known (fixed by D70).

**Kinds of tool or config defect found** (to fix in the next engine phase):
- a context (`migration`) that no discovery setting assigned, so every `exclude = ["migration"]` did nothing;
- strings that look like dotted module paths read as references;
- a framework call (`get_model`) matched by name only;
- signal connections with no sender read as targets;
- a framework package treated as a product boundary;
- a policy file that does not exist read as empty (it must be a config error);
- mock patch targets resolved to their origin instead of the named module;
- a production module matched by a test-file glob;
- a third-party package with the same top-level name as a first-party package resolved as first-party;
- one producer's results kept at a coarser grain than the rule needs.

**Cross-checks against the tools Hoglint replaces** were part of the triage.
Where a rule has a predecessor, its findings matched the predecessor's baseline almost line for line, plus a few new rows.

## Why

- A second opinion from another model, independently: do not baseline today's output; for a rule that is mostly noise and partly valuable, find the reliable subset and enforce that subset.
- The question that started the discussion (Nov 11): can a rule be turned off for now, as in Ruff, or is the issue that even a noisy rule has some valid findings?

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| Write the ledger from today's output | Grandfathers noise | Owner, D66 | `docs/history/decision-log-3.md` |
| A count per rule and product below a level | See `02-policy-exceptions-ledger.md` | Owner, Nov 11 | `docs/history/decision-log-3.md` |
| Make the package-cycle rule advisory because its findings looked like noise | The owner had decided (Nov 5 and Nov 9) that new or growing cycles block and that rules do not start advisory | Architect withdrew the recommendation, Nov 11 | `docs/history/decision-log-3.md` |

## Open

- The rule changes and policy questions from the triage wait on the owner.
  They are PostHog policy and are not published.

## Sources

`docs/history/decision-log-3.md` (D66, "How existing findings are carried", D70, "The record by topic"); `docs/research/second-opinion-ledger.md`; not published (the triage brief and reports).
