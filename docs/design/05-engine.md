# 5. The engine: Rust link, Python shell, two engines, packs

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 11, 2026.

## Decided (current form)

**Shape: a thin Python shell over one PyO3 extension; Rust does the per-row work** (architect, D47 revised, Nov 10).
- Rust: facts, the link (name resolution, class ancestry, core edges), the rule evaluator, labels, `django.uses`, and the contract-surface analysis (D39, N1, D49, D62).
- Python: the CLI, config, git calls, the ledger, the run summary, `compare`, output, plugin readers, and Python producers (`drives`, most Django producers) (D47 revised, D49).
- TOML holds the policy (D39).
- **The seam rule:** Python hands Rust the config and the snapshot once, and gets back rows it prints.
  A call per row, or a row turned into an object nobody prints, is the defect to look for.
- The bridge is in-process PyO3: pure core crates plus a thin binding crate, maturin, abi3 cp312 wheels and a `.pyi` stub (D2).
  The layout matches an existing PostHog PyO3 package (owner, Nov 9).

**Two engines.**
The Python implementation is frozen as the executable spec.
Every phase must equal it on ten parity tables: zero delta, or a named delta.
- After adoption, a scheduled shadow job runs both engines and fails on any difference (D42).
  The reason: the native evaluator's `clean` is never judged again at run time.
  The Python engine needs about 35 s for this job.
- D7 amended: a small independent oracle stays for generated cases.
  A golden corpus replaces the Python engine when that engine leaves the wheel.

**What was ported, in order.**
Each phase landed with zero delta unless a delta is named.

| Phase | Moved | Commits, date |
|---|---|---|
| 3, 3b, 3c, 3d | Native link prototype, fact database, typed pack reader and sink (packs refine core edges, never copy them), label sets | Nov 9-10 (D16-D30) |
| 4a, 4b, 4c | Commit path: one captured tree, declared side inputs, summary keyed by input set; D40, D41 | 2e9fadc662d, e9f4830295c, b736d282928 (D33-D41) |
| N1 | Labels and per-edge judging in Rust; grouping and the read audit deleted | b10fc74cf04, Nov 10 |
| N1b, N2, N2b, N2c | All rule types in the evaluator; fixes D43-D55 | up to 2ac1c21e243, Nov 10 |
| N3a | `django.uses` as a Rust producer | ad83640b66e, Nov 10 |
| N4a, N5a, N6 | Findings as a table; pack records released per step; record compression | up to 461ef834c48, Nov 10-11 (D56-D58) |
| N3b | Facade facts into the engine (see `09-contract-surfaces.md`) | 7f26c49 to 4719fd5, Nov 11, in the hoglint repo (D62-D64) |

**The pack API, and where PostHog's parts live** (D49; "The PostHog pack's home", Nov 10):
- All Rust lives in the hoglint repo and ships as one abi3 wheel.
  The consuming monorepo pins the wheel and holds config, ledger, exceptions and Python plugins.
- `drives` (1,044 lines; PostHog's query-runner dispatch) stays a Python producer in the PostHog monorepo (owner, Nov 10).
- The pack API must be stable enough for a version pin.
  It is not published as a public surface (D31 dropped).
- `PackSink` takes new edges, refinements by edge handle, symbol facts and producer failures.
  Two refiners that disagree on a field are a producer failure (D22).

## Why

- The resolver in Rust ran 10x faster than in Python on one thread (5.0 s to 0.42-0.57 s).
- Starting the shell costs 0.06-0.10 s (measured at b10fc74cf04, three runs).
  A binary would save about 0.1 s.
  tach has the same shape (a Python CLI over an abi3 PyO3 extension) and checked the PostHog repo in 2.3 s wall.
- The cost was Python work per row and Python copies of data, not Python being present.
- `drives` in Python: coarse native queries gave no gain (83 lines of Rust, 24,000 calls removed).
  A port would put PostHog logic into Rust and make each of its changes a release.
- The old scanner gained about three new static-Python reads a month since August 2026.
  In a Rust-only pack, each would be a release.
- Packs refine, never copy: copy detection on six fields let independent edges pass as copies, and a hand-kept field list is a trap in a public API.

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| Rust facts plus a Python rule layer, in a subprocess writing JSONL | Python per row cost about 19 s CPU and 12.5 M objects | Architect default Nov 4; replaced Nov 9 | `docs/research/data-model-2.md`; `docs/research/engine-synthesis.md` |
| A cleaned Python engine (12-20 s) | Pre-commit stays a full run, so too slow | Engine round, Nov 9 | `docs/research/engine-synthesis.md` |
| Persistent queries across runs (option C3) | Untracked reads and 82 order-dependent cycle results break it; costlier | Adversarial review and second opinion, Nov 9 | `docs/research/engine-adversarial-reviews.md`; `docs/research/second-opinion-adjudication.md` |
| Make the Python layer cheaper (D29, D30, D38) | D29 saved about 10 % where the profile promised 45-50 % | Architect, stopped by D39, Nov 10 | `docs/history/decision-log-2.md` |
| One Rust binary, Python plugins in a child process (D47, first version) | A preference stated as a requirement; about 0.1 s gain | Architect withdrew it, Nov 10 | `docs/history/decision-log-2.md` |
| Publish a polished Python pack API (D31) | Packs became built-in or a monorepo plugin | Architect, Nov 10 | `docs/history/decision-log-2.md` |
| The PostHog pack as a built-in Rust module in the hoglint repo | D47 revised removed the reason | Architect; owner, Nov 10 | `docs/history/decision-log-2.md` |
| Port `drives` to Rust | See Why | Architect, D49 | `docs/history/decision-log-3.md` |
| `mentions_many` for installs and signals | 0.43 to 0.42 s, inside the noise | N3a build; skipped | not published |
| A two-row compatibility view for pack copies | The parity tooling already names every delta | Architect, D22 | `docs/history/decision-log-2.md` |
| Delete the Python resolver at adoption (D7, original) | Ten equal tables cannot show an error that both engines share | Architect, D7 amended | `docs/history/decision-log-2.md` |

## What would change the answer

- A rate of new patterns far above about three a month, or a need to run without Python, would reopen a single binary.
- A profile that shows a remaining Python producer worth a port.
  The other Django producers stay Python until then.

## Open

- **A contradiction in the notes, settled.**
  `docs/research/native-engine-design.md` sections 4 and 9 move the PostHog pack into the hoglint repo.
  The decision of Nov 10 ("The PostHog pack's home") reversed that.
  The decision log is the latest.
- The PostHog preset (`presets/posthog.toml`) and pack (`hoglint/presets_py/posthog`) still sit in the hoglint repo, to move out later (D15).
- One snapshot object per run, to replace process-wide registries (`PLUGIN_ROOTS`, `WorktreeStatus.taken`, `WorktreeListing.taken`, `RunInputs.taken`).
- Proposals not yet decided by measurement: facade facts as a generic engine analysis for any repo (partly done in N3b); the PostHog pack's name lists moved into a `[posthog]` config section.

## Sources

`docs/history/decision-log.md` (D1, D2, D7 original); `docs/history/decision-log-2.md` (D7 amended, D22, D29-D31, D38, D39, D42, D47, "The PostHog pack's home"); `docs/history/decision-log-3.md` (D49, D62-D64); `docs/research/native-engine-design.md`; `docs/research/engine-synthesis.md`; `docs/research/spike-n3.md`; `docs/research/data-model-2.md`; not published (the progress log).
