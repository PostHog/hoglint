# Engine round: synthesis

> The synthesis of the engine round of Nov 9, 2026: what phase 1 settled, the three candidate engines (C1, C2, C3), what the measurements refuted, and the decision that followed phase 2.
> Partly replaced: the decisions it led to are in `docs/history/decision-log.md` (D1 to D21), and the engine as built later is in `docs/design/05-engine.md`. D39 (Nov 10) later moved the rules and labels into Rust as well.

## Context

On Nov 9, 2026, the first implementation of hoglint took about 45 s and 4 GB for a full check of the PostHog monorepo, and about 70 s for the pre-commit form (`check --index`).
The engine round asked how the engine underneath the settled user-facing behavior should work.

Phase 1 had these inputs:

- a demand audit, measured: which edges and facts each rule and output reads (`docs/research/engine-adversarial-reviews-3.md`, "The demand audit");
- a skeptic's review of the approach, measured (`docs/research/engine-adversarial-reviews-3.md`, "The skeptic");
- two blind architects, A and B, who got the requirements only and no code (`docs/research/engine-blind-design-a.md` and `docs/research/engine-blind-design-b.md`);
- a prior-art study (`docs/research/engine-prior-art.md`);
- a second opinion from another model that could read the code (`docs/research/second-opinion-engine-round.md`);
- the architect's own view, written before any report came in (summarized in `docs/research/second-opinion-engine-round.md`).

Phase 2 sent three adversaries at the candidates (`docs/research/engine-adversarial-reviews.md` to `engine-adversarial-reviews-3.md`), and a second opinion adjudicated their reports (`docs/research/second-opinion-adjudication.md`).

## 1. What phase 1 settled

### Agreed by every report, with measurements

**Resolution belongs out of per-row Python.**

- Rust parsed for 1.3 s of 45 s.
  Python then decoded 1.8 GB of facts and resolved every name.
  It built 12.5M objects before any rule ran: about 19 s of CPU, and most of the memory peak (skeptic, measured).
- The cost line of Nov 4 came from the spike's per-output channels (core 0.04 to 0.06 s).
  The repo-wide graph that shipped was never costed again (skeptic; the demand audit).
- Both blind architects put resolution in Rust, each on their own.
  One of them measured why a pure-Python warm floor exists:
  - decode 1.1 s;
  - tables 0.7 s;
  - resolve 1.6M uses 1.5 s;
  - total 3.4 s before any rule.
- Prior art: every tool moved this line down over time (grimp, tach, Pants, semgrep), and none moved work back.

**Snapshots are (path, blob id) lists. No checkouts.**

- Policy files are read with `git show` (0.11 s, against 21.5 s for a checkout of the index).
- The fact cache is keyed by blob id, so a cache hit reads no file.
- Staged mode must also read the owners, turbo and routing inputs from the index.
  The first implementation read them from the working tree, which is a bug (the demand audit).

**Owners are computed at render time.**
No rule reads them.
A set field never conflicts.
Owners for every module that a finding names take 0.9 s, and about 0 s with an equal ledger.
No owners cache is needed (skeptic; the demand audit).

**Unknown is a stored value.**

- Kleene logic is used everywhere, and every failed analysis becomes a named gap record.
- Laziness may skip only a decided false.
- Prior art shows the failure when this is not done: semgrep reported findings in failed files as "fixed" until Dec 2025.

**compare has four (code, policy) passes.**
A base engine that differs from the head engine runs as a pinned subprocess that returns its summary.
There is no base checkout.

### Refuted by measurement

- **The architect's option A, "compute only what rules select", is not the main lever.**
  - 63% of edges are never read (the architect had guessed over 70%).
  - Building an edge costs about the same as resolving its row (1.6 µs).
  - Demand-only edges take a check from 23.0 s to 19.6 s of CPU (the demand audit).
  - A filter that interprets the rules' own selectors per edge cost 2.4 s more than it saved.
- **Deduplicating edges:** 1.58M edges hold 1.0M distinct couplings (skeptic), so the gain is at most 1.6x on that part.
- **Columnar or msgpack formats while Python iterates rows** (measured).
  Prior art agrees: the cost is one Python object per row, whatever the transport.
- **GC tricks as a plan:** mypy's freeze trick regressed on CPython 3.14 (prior art).
  It is acceptable as an interim step only.

### Not an architecture question: bugs and waste found on the way

These hold in any architecture and get fixed in any of them:

| Item | Effect | Source |
|---|---|---|
| Django `uses` copies of core reference edges also feed public-surface and cycles | 12,263 of 33,321 public-surface findings are duplicates | the demand audit |
| The `frozen` symbol fact is read but never written | 1,552 of 2,481 `export-shape(class)` findings are frozen dataclasses | the demand audit |
| facade-signature judges 367 slots twice (core `exposes` and facade-facts `exposes`) | double findings | audit summary |
| The cycle rule selects 241k same-package edges to keep 12.9k | waste | the demand audit |
| installs and signals append the same `Edge` object several times | fragile counts | the demand audit |
| `--index` reads owners, turbo, routing and classification files from the working tree | wrong snapshot | the demand audit |
| `[django] oracles` is accepted and read by nothing; the two oracle jobs do not exist | a false safety net | skeptic |
| `explain edge:<id>` ids are printed nowhere | an unusable command | skeptic |
| Parity-only machinery ships in the package: the legacy ledger, 10,251 facade parity edges, the Rust byte pin to the spike reference | cost, and a second policy source | skeptic |
| Two ancestry implementations; the tach-format map resolves names again on its own | logic exists twice | skeptic |
| `explain`, `query` and `--changed` each start with a full run | 45 to 67 s for small questions | skeptic |

The full list, with the bugs the adversaries added, is at the end of `docs/research/engine-adversarial-reviews-3.md`.

## 2. The disagreement

**Blind architects A and B (independent, nearly identical):**

- A Rust core does the snapshot, extraction, the link (resolution, ancestry, core edges as columns, CSR graphs) and grouping.
- Python packs query the index by resolved origin, and keep a per-file `ast` escape hatch cached by blob.
- Python rules evaluate each predicate once per label-class group: 216k import edges fall into 6,171 groups, 28 ms down to 1 ms (A, measured).
- No incremental state above per-file facts: every run links everything again.
- Estimates: pre-commit 1.3 to 3 s, CI cold 5 to 8 s.
- B also puts the expression evaluator in Rust. A keeps predicates in Python.

**The second opinion (another model, with the code):**

- Persistent queries across runs, in the style of rust-analyzer: Python keeps resolution and meaning, Rust holds storage.
- Its first stage is the cleaned Python engine.
- Estimates: pre-commit 2 to 6 s, full check 20 to 35 s.
- Its own fallback is a native batch engine.

**Prior art on the disagreement:**

- Whole-module granularity has the strongest evidence (Pyrefly).
- Per-identifier demand brings cycle-order and negation hazards (Salsa, Soufflé, Swift).
- A Python callback per row inside a native engine is 2 to 6x slower than a plain Python loop.
- A native-only core turns framework support into a core-team backlog (Pyrefly, pks).

## 3. Candidates for phase 2

| | C1 Python, cleaned | C2 Native link, full relink | C3 Persistent queries |
|---|---|---|---|
| Rust | parse and per-file facts (as before), cache by blob id | snapshot, parse, facts, link (resolution, ancestry, core edges in columns, graphs), group-by, reach | parse, fact storage, indexed selection, dependency bookkeeping |
| Python | everything after the parse | packs (index queries plus a cached per-file `ast` hatch), labels, rule predicates per label-class group, ledger, compare, outputs | resolution, packs, rules, all as tracked queries |
| State across runs | facts only | facts only | facts plus every query result, with its read dependencies |
| Expected full check | 12 to 20 s (audit floor about 12.5 s with lazy owners and demand edges) | 5 to 8 s cold (estimate) | 20 to 35 s cold (estimate) |
| Expected pre-commit, 3 files | same as a full check | 1.3 to 3 s (estimate) | 2 to 6 s (estimate) |
| Main risk | pre-commit stays a full run | the generic catalog is too poor for the packs, so every new pattern waits on a wheel release | missing negative dependencies; cache correctness; validation cost |
| Proposed by | the second opinion's stage 1; the skeptic's interim steps | blind A, blind B; the skeptic's main fix | the second opinion |

Common to all three: the snapshot and owners fixes, unknown as a value, the four compare passes, and the bug list above.

## 4. The architect's read before the adversaries

- **C2 led.**
  - Two blind designs reached it independently, and the skeptic reached it from the code.
  - It needs no cache for correctness, which matches the owner's principle of broad exploration before any caching.
  - Prior art favors its shape: coarse crossings, ints and not objects, whole-module granularity.
- **C2's riskiest assumption was shared by both blind architects:** a fixed generic catalog must carry the Django and PostHog meaning.
  The packs of the day were the evidence to check: what do they read, and can each read become an index query?
- **C3 was attractive only if C2's pre-commit failed.**
  Its correctness argument was the hardest of the three.
- **C1 was the fallback, and the source of the interim fixes.**

## 5. The phase 2 plan

Three adversaries attacked all three candidates, each from one angle, with the code and the phase 1 reports:

1. **Correctness.** Unknown is never clean; determinism; equality to a clean run; the compare and growth semantics; the skeptic's base-ledger shortcut; staged snapshots; the incremental hazards in C3.
2. **Extension and maintenance.** Every fact access of the Django and PostHog packs mapped onto C2's index API. Also the wheel release friction, the two-language resolver during migration, test seams, and the migration path from the existing code.
3. **Performance claims.** Which rules cannot be evaluated by group, Rust link time, C3's validation cost, and memory with many concurrent worktrees. Microbenchmarks where a claim is cheap to test.

A second opinion from another model then adjudicated the three adversary reports.
Phase 3 was to prototype the leader against its riskiest assumption and run the blind maintainer test.

## 6. What phase 2 refuted

From the adversaries (`docs/research/engine-adversarial-reviews.md`) and the adjudication (`docs/research/second-opinion-adjudication.md`):

- **The skeptic's base-ledger shortcut for compare.**
  The ledger holds only ledgerable findings; compare also needs incompletes, triples, couplings for move admission, and non-ledger findings.
  Only an exact-key summary of the base can replace a base scan, never the ledger.
- **By-name locality as a bound.**
  A change to a base class adds edges to a file that neither changed nor names the base (correctness F1).
- **C3 as planned.**
  The Python resolver of the day made reads that no query boundary saw: module existence, the set of repo heads, PEP 562 maps, its own memos.
  82 results met a re-export cycle, and their answer depended on query order (correctness F3).
  The adjudication called this "partly confirmed": a fully tracked C3 can work, at the cost of making every read tracked.
- **The blind designs' fixed catalog.**
  The real packs needed about 8 new catalog fields and 6 index queries under either design (extension F1).
  The adjudication called the exact count unproven, but agreed that the catalog of the day (schema v3) is the better starting point.
- **Queries by resolved origin alone.**
  They drop the spelled-but-unresolved candidates that the packs turn into unknown edges (correctness F5, extension F2).
- **The 1.3 s pre-commit estimate, and 0.3 to 0.7 GB per process.**
  Measured parts put pre-commit at 2 to 4 s warm and memory at about 1.2 GB (performance F3, F10).
- **"Exact port" for the Rust link benchmark.**
  The benchmark matched 1,143,531 rows on a narrow projection.
  The adjudication found the projection left out surface, via, scope, contexts, use position and attributes, so sub-second native resolution is credible but full parity was not shown.

What held: the native link at 0.42 to 0.74 s on one thread against 5.0 s in Python, over 10x (performance F4); grouped rule evaluation is cheap enough (performance F7); and C3's dependency frontier is small (median 5 files) but its cold path costs 20 to 55 s (performance F6).

## 7. The decision

The architect decided on Nov 9, after the adjudication, to prototype a **revised C2**: a Rust link over the facts of the day, with Python packs and predicates.
The decisions are D1 to D13 in `docs/history/decision-log.md`. In short:

- **D1.** Rust holds the mechanics: facts (the schema-v3 catalog), configurable name resolution, class ancestry with certainty (yes, no, maybe), candidate indexes by resolved origin and by spelling, and graph work. Python holds the meaning: the Django and PostHog packs, shapes, labels, rule predicates, the ledger, compare policy and outputs.
- **D2.** The bridge is in-process PyO3 with coarse calls and opaque handles: no JSONL on the check path and no Python object per row.
- **D3.** One cached artifact type, the run summary, under an exact key.
- **D4.** Facts are keyed by (extractor build id, blob oid); snapshots are lists of blob ids; there are no checkouts.
- **D5.** Every run evaluates the whole repo; `--changed` filters the output only. C3's tracked dependencies cost more to keep correct than they save.
- **D6.** Pre-commit links on one thread.
- **D7.** One resolver in the end; the Python resolver stays as the differential oracle until cutover.
- **D8.** Unknown is data, including spelled-but-unresolved candidates.
- **D9.** compare and growth: shape-aware move admission, counted obligations with identity, exit 2 on a failed required scan, a versioned summary protocol, shrink attributed by cause, a pinned base environment.
- **D10 to D13.** One cache root; parity-only machinery leaves the package; owners at render time; drives and migrations rewritten.

C1 stayed as the source of the interim fixes.
C3 was rejected for the engine as a whole.
The question of reuse between runs came back on Nov 10 and 11 in a different form, stored answers with a record of what they read (`docs/research/second-opinion-reuse.md`).
