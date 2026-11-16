# 5. The engine: Rust link, Python shell, packs

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 16, 2026.

## Decided (current form)

**Shape: a thin Python shell over one PyO3 extension; Rust does the per-row work** (architect, D47 revised, Nov 10).
- Rust: facts, the link (name resolution, class ancestry, core edges), the rule evaluator, labels, `django.uses`, and the contract-surface analysis (D39, N1, D49, D62).
- Python: the CLI, config, git calls, the ledger, the run summary, `compare`, output, plugin readers, and Python producers (`drives`, most Django producers) (D47 revised, D49).
- TOML holds the policy (D39).
- **The seam rule:** Python hands Rust the config and the snapshot once, and gets back rows it prints.
  A call per row, or a row turned into an object nobody prints, is the defect to look for.
- The bridge is in-process PyO3: pure core crates plus a thin binding crate, maturin, abi3 cp312 wheels and a `.pyi` stub (D2).
  The layout matches an existing PostHog PyO3 package (owner, Nov 9).

**One engine** (owner, Nov 13).
The native engine builds the graph: core producers, name resolution, class ancestry, what a surface hands out, the pack queries, `django.uses`.
The Python graph build that did the same is retired; `--engine native` is still accepted, because a newer `compare` passes it to an older base's scan, and any other engine name is refused.
- **The native evaluator alone judges every rule** (Nov 13).
  The Python rule classes no longer judge a row again, at run time or on a ledger write; the ledger-write confirm (D50, D55) is gone with them.
  A rule, label or selector change is written once, in Rust.
- **Python words what the evaluator judged and holds the metadata** (D51, D53).
  The evaluator gives each row what its message names: the identity, the two ends' names, the edge's label and the refs the judgment read as unknown.
  Python keeps each rule type's class (its metadata, its plan, its messages and remedies), the labeler that words a label set two ways and gives `explain` its sources, and the steps with no Rust twin: a layout rule's file and registry checks, the coverage adapter, a cycle rule's strongly connected parts, levels, stale names, the classification compare.
  `explain edge:`, the couplings a rename scope keeps and `explain`'s label values come from the evaluator too.
  A graph that is not linked natively gets no rule context; hand-built test graphs are loaded into a native link first.
- **What stands in for the comparison of the two engines:**
  - the model repository's stored result: its reviewed rows (`tests/model_repo/expected.toml`), the row count and digest of each of the ten tables (`tests/model_repo/tables.toml`) and every message and reason (`tests/model_repo/messages.toml`), checked by `bin/model-repo` and the suite;
  - what `explain` says on the model repository, held to the run it explains (`tests/model_repo/test_explain.py`): every label it prints is the value the evaluator holds; for every rule judged per destination and every edge, the truths `explain edge:` prints give a row exactly where the run has one, and each finding's identity is what the evaluator reads again at its edge; a reach or cycle finding is a path `explain edge:` reads as selected; a small stored sample (`tests/model_repo/explained.txt`) holds the wording;
  - the stored runs of the hand-written rule cases (`tests/native/stored_rows.json`), written while both engines still agreed on them, and of one generated config per rule mode, picked by hand (`tests/native/generated_runs.jsonl`);
  - relations between sibling rules on the evaluator alone (`tests/native/test_rule_relations.py`), and the tables `tests/rules/judgment.txt`, `connectives.txt` and `missing_side.txt`, each read by a Rust test;
  - the labeler twin (`tests/native/test_evaluator.py`): the module labels the native labeler reads equal the Python labeler's, and so do the symbol labels the evaluator reads at each edge's destination, over the fixture's config and the generated configs that differ outside `[rules]`;
  - the rule tests on hand-built graphs, the slot table test, the generated exactly-once test, and the producer and pack tests, which link hand-written records in the native engine and assert stored rows;
  - `bin/prove --real <snapshot> --base <rev>`: every row of the ten tables of the head against a base build at real size, the moved rows written out.
- **What these cannot show:** a stored value holds the engine to its own earlier output, so an error the engine always had stays (the reason D7 amended kept an oracle).
  A relation holds wherever an error is the same in every sibling rule, so an atom read wrong everywhere is seen only where a stored value reads it.

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
| Keep the Python rule classes as a second judge inside every native run (D27, D42, D50, D55) | By the record it never caught a real difference at run time or on a ledger write; it made every rule change get written twice and cost a ledger write about 5 s. The stored runs, the relations test and the judgment tables stand in for the test-time comparison | Architect, on the owner's delegation, Nov 13 | not published |
| Delete the Python resolver at adoption (D7, original) | Ten equal tables cannot show an error that both engines share. Reversed on Nov 13: the owner retired the Python graph build, with the stored tables above in its place | Architect, D7 amended | `docs/history/decision-log-2.md` |

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
