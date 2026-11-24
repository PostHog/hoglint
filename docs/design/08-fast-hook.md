# 8. The fast hook: the reuse plan, its stages, and three roads turned down

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 11, 2026. D69 (stage 1 not built) is the latest change.

## Decided

**The bar** (owner, Nov 11): the design targets a pre-commit hook that runs on every commit in 2 to 3 s.
It may check a subset (the staged files and everything related) if the lint quality is on par with a full check.
- The architect's reading (D60): about 2.2 s or less on a quiet machine.
  A hook on every commit adds bars that speed does not cover: the memory of several checks at once, a cold first run, and a wrong block being worse than a miss.
  A subset is allowed only when the replay check (stage S0) shows that it reports what the full check reports for the commit.

**The road: exact reuse on Hoglint's own store, in stages; not salsa** (architect, D61, Nov 11).
- **The model: stored answers with traces.**
  Each file has an interface with a digest.
  Each stored answer records the files it read and their interface digests.
  A file's answer is reused when its content and every digest in its trace are unchanged.
  Whole-graph rules reuse by the digest of the relation they read.
  A cold run (CI, the first run) is today's full run.
- **The stages** (from `docs/research/incremental-plan.md`; times are estimates for a leaf commit):

  | # | Stage | Leaf commit after it (estimate) |
  |---|---|---|
  | S0 | Oracle and replay harness | 3.9 s |
  | S1 | Rule-row and label memo | void after D69 (was 3.3-3.5 s) |
  | S2 | Aggregate relations, partitions, summary delta | 2.9-3.2 s |
  | S3 | Packs per item with a traced reader | 2.2-2.6 s |
  | S4 | N3b facade with per-file traces | 2.1-2.5 s |
  | S5 | Link traces, per-name interfaces (oracle only) | no change |
  | S6 | Answer store read by key | no change |
  | S7 | Partial link ("the flip") | 0.9-1.3 s |

- S0 to S4 alone ("alternative A") were estimated at 2.1-2.6 s per leaf commit, exact, with the fewest new failure modes.
  S5 to S7 are the stretch to about 1 s (architect, Nov 11).
- **D69 (Nov 11): stage 1 is not built.**
  Measured before building: the hit rate is fine (99.87 % of row keys unchanged between a commit and its parent), but computing all row keys costs 2.1-3.0 s on one thread, against 0.66-1.0 s for the judging it would skip.
  Reuse of rule rows pays only when unchanged rows are not read at all: traces and the partial link (stages 5 to 7).
  Stage 2 in its "full graph present" form falls with it.
- Each stage has a stop rule (for example, S1: a hit rate under 80 % of rows, or a leaf saving under 0.3 s).
- Order: S0 and N3b first; then a types-and-lint pass; then S3 and S5 side by side, each build agent in its own git worktree; then S6; then S7.
- Size estimate: 13,200-20,700 lines for all eight stages, against 25,263 lines for the native port so far.
  Size was not set as a limit (owner, Nov 11); that is a statement about effort, not a choice of road.

## Why

- **N7 spike** (33 consecutive main-branch commits plus 3 hub commits, 48 trees): in 32 of 33 commits every changed edge, finding and label sat in a changed file.
  A change to a file with over 2,500 importers moved no importer's edge.
  Reach, cycle and coverage results never moved.
- **N7 parts 2 and 3:** per-file reuse was exact on the 33 commits and 11 targeted mutations, after three widenings of the trace.
  At name grain the median recompute set is 6 files, against 4 really changed (worst: 361 files, against 3,686 at file grain).
- **ty, measured Nov 11 (version 0.0.80):** one leaf file 0.08 s, a hub model file 0.21 s, with no disk cache.
  It is fast in a hook because it computes only what the staged files need.
  A Hoglint finding also depends on who imports the changed file and on pack facts, so Hoglint needs importers and per-name interfaces on disk, read by key.
- **A second opinion** from another model, given facts and numbers only, chose the same model: persistent, dependency-validated reuse of per-file producer outputs.

## The three roads turned down

| Road | Why turned down | Evidence | Who, when |
|---|---|---|---|
| **Salsa** for reuse between processes | A fresh process loads the whole database: 0.41 s for a reduced model that computes from nothing in 0.27 s; no partial or lazy load; a panic on load out of the box; no schema check across versions | N9 spike on the real 28,794 files, salsa 0.28.5 with `persistence` (`docs/research/spike-n9.md`) | Architect, D59 and D61 |
| **Prewarm by a git hook** | Helps one flow of five (stage, wait 6 s, commit: 4.6-6.8 s down to 0.8-1.2 s); nothing for `commit -a` or for staging and committing at once; unguarded it storms (up to 20 checks at once, 17-20 GB), because Hoglint's own `write-tree` fires the hook | N8 spike, measured under heavy load (`docs/research/spike-n8.md`) | Architect, D59: not pursued; at most an opt-in extra |
| **A declared fast subset mode** (link and judge only the changed files, with rules that read nothing a pack produces) | Sees 11 of the 88 findings that 33 real commits introduced (12.5 %); 76 of the 88 come from `export-shape`, `public-surface` and `facade-signature` | N7 part 4 (`docs/research/spike-n7.md`) | Architect, Nov 10: a hook that stays silent on seven of eight new findings teaches people that it passes when CI will fail |

Also turned down:
- **A long-lived process or daemon** for pre-commit.
  Reason (owner, Nov 9): checks and hooks run across dozens of worktrees.
  A daemon also needs the same invalidation logic as stored answers, holds about 1 GB while idle, and does nothing for CI.
  So it is a step after stored answers, not a way around them.
- **Stay at whole-program recomputation:** 2.7-3.3 s estimated; the 0.55 s floor applies only to an unchanged input set.
- **A background job on a baseline miss:** the target is Ruff-like speed (owner, Nov 10).

## What would change the answer

- Salsa stays right for a long-lived process (an editor server or a watch mode), on the per-name queries of S5.
- A fast mode becomes measurable again after N3b, because the facade rules that fire on real commits now live in the engine.
- The second opinion's stop conditions: broad dependencies force most packs to run; validation costs approach recomputation; Python producers cannot be confined to tracked reads; the hook is not used enough.
- `drives` reruns whole in 20 of 33 commits unless its origins are read at (module, name) grain.

## Open (the owner's calls)

- What the hook does when Hoglint itself fails (an engine error, a missing wheel): block the commit, or let it through to CI.
- Memory with several checks at once, and a cold first check after a pull.
- Package-grain relation digests for whole-graph rules were not measured (the spike's package mapping was wrong).
- A surprise to look at: the engine did not read `__all__` for star imports (N7).
  The N3b review later made a star import hand out its source's public names on contract surfaces.

## Sources

`docs/history/decision-log-3.md` ("After the N7 spike", "The second opinion on the architecture", "N8 spike", "N9 spike", D59 in three parts, "The owner's calls of Nov 11", D60, D61, D69); `docs/research/incremental-plan.md`; `docs/research/spike-n7.md`, `spike-n8.md`, `spike-n9.md`; `docs/research/second-opinion-reuse.md`.
