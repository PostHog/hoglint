# 7. Speed and memory: what was measured, what worked, what did not

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 11, 2026.

Every number below carries its date, its commit and its conditions.
Timings were measured on a developer laptop, often under load.
Load and power mode moved times by up to 1.4x, so only same-minute pairs are compared.
All timings are on the PostHog monorepo (about 29,000 Python files).

## Decided

**Targets** (architect, D60, from the owner's Nov 11 decision): the design targets a pre-commit check that runs on every commit in 2 to 3 s on a loaded laptop.
So it needs about 2.2 s or less on a quiet machine.
- The earlier target (Nov 10) accepted a slower hook if speeding it up took a very large effort; the target of Nov 11 (owner) is 2 to 3 s on every commit.
- Memory: the owner set no target.
  The 0.5 GB target is withdrawn (D56).
  Memory is measured as the physical footprint, with RSS next to it.

**How time is measured** (architect): same-minute pairs; CPU next to wall; wall only at a stated condition; "not resolved" when a gap is inside the spread; a checkpoint in the first third of each phase; a phase is cut where Python objects end.

## Measured state, in order

| When, commit | Staged check, one thread | Four threads | Peak memory | Conditions |
|---|---|---|---|---|
| Nov 4, spike | cold about 9.3 s, warm about 7.4 s (composed from parts) | | | under heavy load |
| Nov 9, integration | full check 33-57 s | | | under load |
| Nov 9, phase 3b | 8.2 s warm | | 2.5 GB RSS | quiet |
| Nov 10, end of 3d, dc2271eba47 | 4.89-5.06 s | | 1.40 GB RSS | under load |
| Nov 10, N1, b10fc74cf04 | 5.1-5.5 s CPU, unchanged | rules phase -0.25 s wall | 1.40 GB | under load |
| Nov 10, N2, a3ab31d45fb | 3.73-3.79 s CPU (5.00 before) | 3.47-3.53 s wall | 1.40 GB | quiet pairs |
| Nov 10, ad83640b66e (N2b + N3a) | 3.62-3.93 s CPU | 3.39-3.44 s | 1.37 GB | |
| Nov 11, 461ef834c48 | about 3.6 s (analysis 1.05, Django pack 0.44, PostHog pack 0.59, rules 0.93, rest about 0.6) | about 3.2 s | footprint 1.16 GB, RSS 1.31 GB | normal power, moderate load |
| Nov 11, 4719fd5 (N3b and fixes) | 5.17 to 4.79 s against 4328924, median gap -0.43 s | 4.61 to 4.21 s | RSS 1.31 to 1.20 GB; footprint 1.16 GB | under heavy load, so absolute times are high |

**Reference tools** (same snapshot, Nov 10, under load): `ruff check` 1.4 s wall, 17-19 s CPU, 0.39 GB; `tach check` 2.3 s, 10 s CPU, 0.22 GB; `ty check` 9.9 s, 105 s CPU, 7.2 GB; `hoglint check --index` 4.9 s, 4.7 s CPU, 1.40 GB.
The CI checks Hoglint replaces, measured together under load: tach 2.3 s, `lint-imports --no-cache` 18.3 s, `hogli product:lint --all` 42.8 s.

**Floor:** a check that hits the stored summary of its tree takes 0.55 s wall and 0.36 s CPU (N7 spike).

## What worked

- Moving the name resolver to Rust: 5.0 s to 0.42-0.57 s on one thread.
- N2, removing Python consumers (labels read from the evaluator's columns, a finding's edge kept as a handle, signature judged in Rust): rules phase 2.15 to 1.0 s.
- N3a, `django.uses` in Rust: Django pack 0.62 to 0.44 s.
- N3b, facade facts in the engine: -0.38 s in the PostHog pack.
- Fixing the commit path (one captured tree; summary keyed by input set): baseline misses became rare (D33, D34).
- Releasing pack records after each step: footprint 1.25 to 1.16 GB, with no time cost (D56).

## What did not work

| Attempt | Expected | Measured |
|---|---|---|
| D29, selectors per label class | 45-50 % of the rules phase (cProfile share) | about 10 % |
| N1, per-edge judging in Rust | 0.7 s less on one thread | 0 s; the cost moved to Python consumers |
| Adaptive thread count by load (D6, second version) | a gain | 5.6-9.4 s against 4.9-5.1 s on one thread |
| N4a, findings as a table | 0.3-0.4 s (profile) | about 0.09-0.13 s |
| Lazy edge fetch (an efficiency review) | 0.2-0.35 s | the bulk fetch is 2,670 ids and 5 ms |
| One parallel judge call; a surface-module memo | a gain | inside the noise |
| Coarse native queries in `drives` | a gain | none above the noise |
| mimalloc | a gain | none |
| N5a, cutting the snapshot step | 0.2 s | the snapshot is 0.16 s inside analysis; the gate out of reach |
| Building only the edges rules select | the main lever | 23.0 to 19.6 s CPU only |
| D69, a forward check of rule rows (reuse stage 1) | 3.9 to 3.3-3.5 s | computing the row keys costs 2.1-3.0 s against 0.66-1.0 s of judging they would skip |

## The lessons about estimates

- **Profile shares overstate Python against native code.**
  cProfile inflates many tiny calls.
  Confirm a profile share with low-overhead timers before committing to a redesign (after D29).
- **Count the consumers, not the kernel.**
  N1 moved the kernel to Rust and left the Python consumers of its rows, so the time stayed.
- **The architect's own estimates missed in the same direction twice.**
  From Nov 10 on, only measured pairs were quoted to the owner, and the rest was labeled "estimate".
- **A reading-based efficiency estimate was off by 40x** (the lazy edge fetch).
- **The memory number was the wrong number.**
  1.4 GB was `ru_maxrss` at the end of the run.
  The physical footprint peaked at 1.25 GB, inside the facade producer.
- **A cache key can cost more than the work it skips** (D69).
  Judging reads few fields per row and stops early; a key must read and hash every input of every row.

## Considered and rejected (targets)

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| 1 s wall and 0.5 GB (the D39 targets) | 1 s needs everything in Rust; 0.5 GB needs a rewrite of the link | Architect, D49 and D56 | `docs/history/decision-log-3.md` |
| A 4 s gate for the staged check | Replaced by the owner's 2-3 s | Owner, Nov 11 | `docs/history/decision-log-3.md` |
| A limit on concurrent runs (D36) | A hang, a broken bound and a crash in its first review | Architect withdrew it, Nov 10 | `docs/history/decision-log-2.md` |

## What would change the answer

- A memory bar from the every-commit target: several agents committing at once multiply 1.16 GB per check.

## Open

- Analysis on four threads after N3b: +0.045 s median, not resolved under load; to measure again on a quiet machine.
- Pairs for the N3b fix rounds on a quiet machine are owed.

## Sources

`docs/history/decision-log-2.md` (D6, D29, D36, D39); `docs/history/decision-log-3.md` (D49, D56, D58, D60, D69 and the round notes); `docs/research/native-engine-design.md`; `docs/research/engine-synthesis.md`; `docs/research/spike-n1.md`, `spike-n3.md`, `spike-n5.md`, `spike-n7.md`; `docs/research/data-model-2.md`; not published (the progress log).
