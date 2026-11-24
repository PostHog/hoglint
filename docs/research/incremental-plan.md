# Stored answers with traces: a staged plan and a size estimate

> A staged plan for reusing stored answers between checks (S0 to S7), written by a read-only planning agent on the architect's brief, Nov 10-11, 2026, with the architect's notes at the end.
> Partly replaced: D69 decided that stage 1 is not built, because a forward check of rule rows costs three times the judging it would skip; the "3.3–3.5 s after stage 1" figure is void. See `docs/history/decision-log-3.md` and `docs/design/08-fast-hook.md`.

The planner worked from an export of `5469719079c`.
**Every time, size and percentage is an estimate unless it quotes a spike's measurement.**
Times are one thread.
Code paths are relative to the Hoglint source tree.

## The main finding: a saving can come earlier than D59 says

D59 says the saving comes at the end, because the analysis cannot be skipped while a pack step needs the whole graph.
That holds for the analysis.
It does not hold for rules and packs.
While the full link is still in memory, a stored answer can be checked **forward**: hash what it read in the current snapshot and compare.
That needs no reverse index, no partial link and no reverse-dependency search, and it is exact as long as the key covers every read.
So stages 1 to 4 cut rules and packs with the full graph present.
Stages 5 to 7 then remove the analysis.
Stage 7 is the only one that skips the analysis ("the flip").

## Stages

| # | What is built | Leaf commit after it | Full run after it |
|---|---|---|---|
| 0 | Oracle and replay harness | 3.9 s | 3.9 s |
| 1 | Rule-row and label memo, full graph present (not built, D69) | 3.3–3.5 (void, D69) | 4.0–4.1 |
| 2 | Aggregate relations, finding partitions, summary delta | 2.9–3.2 | about 4.0 |
| 3 | Packs per item with a traced reader | 2.2–2.6 | 4.1–4.3 |
| 4 | N3b facade in the engine, with per-file traces | 2.1–2.5 | lower by the N3b gain (not measured) |
| 5 | Link traces and per-name interfaces, checked in the oracle only | no change | +0.05–0.1 |
| 6 | Answer store read by key, used for prediction only | no change | +0.1–0.2 (writes; off in CI) |
| 7 | Partial link: the flip | **0.9–1.3** | about 3.9 (store off in CI) |

- **S0, oracle (needs nothing, saves nothing).** A replay runner over the N7 sample (30 consecutive commits, 3 hub commits; see `docs/research/spike-n7.md`) and the 11 mutations as fixtures. For each child tree it dumps the ten parity tables twice, incremental (warm store) and cold full, and compares them in order and as multisets. Built on `hoglint/native/parity.py` (348 lines). It also injects damage: a truncated blob, a missing row, a killed writer, two checks at once, A→B→A trees, shuffled item order. It must catch a planted miss.
- **S1, rule rows and labels by content key (needs S0). The first real saving.** (Not built: see D69.) Key of a judged row: the rule plan, the normalized edge, the label rows of source and destination, the destination's symbol facts and kind sets, and `dst.exported`, taken where `Rules::judge` builds the `Env` (`src/link/rules/mod.rs`, `env.rs`). Edge identity is (site file, producer, ordinal in file and kind), never the edge id. Labels per module are keyed by (path, policy digest, provider trace); the provider trace is the ownership files the resolver consulted, absent ones included. The label conflicts a row met are stored with the row. Saves part of `Rules.judge` and the two `Labeler` builds.
- **S2, aggregate rules over maintained relations (needs S1).** Each file contributes rows to a relation: reach reads the file-import relation (149,869 rows); cycle and the package-view rules read module or package pairs with counts (206,245 rows); coverage 8,745 rows; export rules the export-input relation. The rules run on the relation, not on 1.48 M edges. Export rules join this group, which removes the "identity files" widening. Each relation key keeps a count and the first witness in graph order, so a deleted edge is handled right. The summary's findings are stored in partitions by file group, so a new tree rewrites only changed partitions. Levels and couplings fold from partition digests.
- **S3, pack steps as items with recorded reads (needs S0; shared Python, so the reference engine runs the same code).** `ProducerStep` (`hoglint/core/harness.py`) gains `items()` and `run_item(item)`; `ProducerPlan.run_step` runs each item under a trace. A traced reader wraps the choke points: `NativeRecord.load` and `Tables.get` (`native/build.py`), the resolver and `index.exists`, `calls_named`, `uses_to`, `edges_to`, `subclasses`, `may_mention`, `imported_modules` (`native/queries.py`). A read is (kind, key, answer digest), "not found" included. Global folds become items with traces of their own: the app registry, the model registry, the drives origins. Refinements are keyed by edge identity, not handle.
- **S4, N3b facade facts in Rust, built per file and traced from the start (needs S3's trace format).** Only the trace delta is counted here.
- **S5, link traces and per-name interfaces (needs S0; can run beside S1–S4).** Recording is built into `resolve.rs` and `scope.rs`, with an owner set by `Producers::run` before each producer call. At first it is checked only in the oracle's certificate mode: predict the invalid files, compare with the full run.
- **S6, answer store (needs S5).** Per-file answers, roots, the reverse index and per-name interface rows, read by key. At first only the oracle reads it, to predict.
- **S7, partial link, the flip (needs S2, S3, S4, S6).** A lazy `FactSet`; `ModuleIndex` from the listing; `LinkConfig.analyzed` set to the affected set (that field already means "files that produce edges, in order"). The pack folds become incremental. Output is assembled from stored partitions. A fallback to the full run is decided before any work starts.

## The hard parts, from the code

**The link: what it reads from other files** (`resolve.rs` 915 lines, `scope.rs` 568, `produce.rs` 1,112).
- `Core::table(module)` → `build_table` → `collect` reads the module's own record and its `.pyi` stub: module-level defs, imports and assigns, the conditional-import fallback, and `lazy_map` when `__getattr__` is defined.
- Record in `Core::lookup`: `(m, n)` found or absent; `(m, <stars>)` when it walks star imports; `exists(m.n)` for a submodule; `(m, <lazy>)`; the missing-star case.
- Record in `resolve_from`, `descend`, `ModuleIndex::exists`, `longest_module`, `is_package`, `is_external` as module-membership reads (the listing answers them), and in `located`, `bases`, `mro`, `is_subclass` as class bindings.
- `name_cache` and `from_cache` hide the reads of a cached answer: keep a read-set id per cache entry and merge it into the current owner's trace on a hit.
- The module an edge spells goes in on `push` (the `surface` module, and `loaded` in `imports()`).
- The per-name interface digest comes out of `collect`'s binding events, plus `<stars>`, `<lazy>` and the module/package flag. It is keyed by blob oid, so it is immutable.

**The link: edges for a set of files.**
- `ModuleIndex::build` needs only paths. `build_table` reads lazily.
- The blocker is `FactSet.files: Vec<FileFacts>`, filled eagerly by `link_blobs` (`binding/src/blobs.rs`), with 60 `facts.files[...]` sites in `src/link`. Change: a lazy `FactSet::record(i)` loaded by oid.
- The interner is process-local: stored answers use strings through a per-blob string table, as `link/store.rs` `Enc::sid` does. `EdgeStore` positions never persist.
- Core edge order is kind-major, then file order; pack rows follow in step order. Assembly sorts partitions by (producer, file order), never by hash.
- `Symbols::set` is a global first-place, last-facts map: fold the per-file lists in file order.

**Packs: what each reads, and how hard it is to turn into items.**

| Producer | Grain and shared state | Difficulty |
|---|---|---|
| `uses.rs` `django_uses` (340 lines) | Per file: its own reference edges, the `derived` rows, model-set membership of each target | Easy (Rust) |
| Django get_model, signals, installs | Per file through `calls_named`, `may_mention`, `edges_to`; `edges_to(kind, T)` is a posting set | Easy to medium |
| `apps.py` AppRegistry | Settings modules, INSTALLED_APPS, AppConfig MROs: a small global item with a trace | Medium |
| `models.py` `discover` | `reader.subclasses(MODEL_BASE)` scans every module's classes: split per file, then a fold | Medium |
| `migrations.py` | Already keyed by fact digest (`SummaryCache`); make the graph fold an item | Medium |
| `drives.py` (1,044 lines) | Global: registry, seeds, `grow_origins` over importer postings. Per test file: `may_drive`, then `derive_file`, which reads `origins.get(...)` and `CallView.executes`, which walks other modules' bodies up to depth 16 | **Hard.** Origins must be read at (module, name) grain, or 20 of 33 commits rerun it whole. Stays Python (decided) |
| `facade.py` (539 lines) | Per facade file, but `classes_done` and `views` are shared, so a class's edges belong to the first facade file that reaches it | Hard in Python; move it to the engine (N3b) |

**Labels and rules.**
A judged row reads source and destination labels, kinds, symbol facts and `exported`.
Rows are stored per (site file, rule) and become invalid when the file's edges change or a read label or symbol digest changes.
Whole-graph rules run over S2's relations and rerun only when the relation digest changes.
Package grain was not measured; S2 measures it first.

**The store.**
One SQLite file per engine id, WAL.
Rows are immutable and keyed by content.
Each blob carries a checksum and a schema number.
A tree's manifest is its git tree; the diff against a base comes from `git diff-tree`.
A root (tree, policy, framework) holds per-file answer pointers and partition digests as one blob that is a delta on its base root, compacted after k steps.
The reverse index `(module, name) → reader paths` is a superset across versions, which keeps it sound without per-root rows; each candidate is checked against its trace.
The root row is written last, in one transaction with its index rows; a root that is not there is a miss.
Concurrent checks only repeat work.
A damaged answer means "recompute that file"; a damaged root or index means a full run and the file moved aside: never a wrong answer, never a failed run.
Branch switch: try the roots of the last ~20 trees in `git rev-list HEAD` and take the base with the smallest diff; fall back to a full run when the predicted share is over ~30 % of files.

**The oracle.**
Built in S0.
It runs in CI on changes to the tool and on a schedule against the target repo.
The cold full run stays the exact gate.

## Size (insertions + deletions, all estimates)

| Stage | Rust | Python | Tests | Total | Basis (`wc -l`) |
|---|---|---|---|---|---|
| S0 | 0 | 500–800 | 200 | 700–1,000 | parity.py 348 |
| S1 | 700–1,100 | 300–500 | 400–600 | 1,400–2,200 | rules/mod.rs 1,089, env.rs 786, labels.rs 811, labels.py 778 |
| S2 | 600–1,000 | 600–1,000 | 400–600 | 1,600–2,600 | reach 238, cycle 73, coverage 168, scan.py 350, store.py 302, levels.py 133, couplings.py 95, graphs.py 434 |
| S3 | 300–500 | 1,500–2,500 | 800–1,200 | 2,600–4,200 | Django pack 2,907, drives 1,044, harness 142, build.py 359, queries.py 338 |
| S4 (delta over N3b) | 300–500 | 0 | in S0 | 300–500 | facade.py 539 |
| S5 | 900–1,400 | 0 | 400–600 | 1,300–2,000 | resolve 915, scope 568, produce 1,112, paths 275 |
| S6 | 1,500–2,300 | 300–500 | 600–900 | 2,400–3,700 | factdb.rs 294, factdb.py 331, link/store.rs 662 |
| S7 | 1,400–2,200 | 800–1,300 | 700–1,000 | 2,900–4,500 | link/mod.rs 663, link/facts.rs 778, blobs.rs 704, binding link.rs 835, analysis.py 127 |
| **Total** | | | | **13,200–20,700** | |

- The native round so far (`2e9fadc662d..5469719079c`): 96 commits, +18,044 −7,219 = **25,263 lines** (Rust +10,114 −3,129; Python +4,803 −3,440; Python tests +2,970 −565).
- So S0 to S7 together are about 0.5 to 0.8 of that round by lines. They cost more per line: the native round had a reference engine to copy, and this has none.

## A stop rule per stage

- **S0:** the full run is not equal to itself across hash seeds and thread counts on the replay sample. Fix that first.
- **S1:** the hit rate is under 80 % of rows on replays, or the leaf saving is under 0.3 s, or the `Env` reads cannot be listed.
- **S2:** relation-based rules cannot reproduce witnesses and order; or the package-grain digest moves in more than half the commits and a rerun costs over 0.2 s.
- **S3:** a drives test item reads more than about 200 modules through `CallView`; or checking a trace costs over half of recomputing; or the Python reference diverges.
- **S5:** trace entries over about 2 M per tree; recording over 0.15 s on a full run; a certificate miss that no local widening fixes.
- **S6:** a leaf commit's store reads over 0.15 s; the store over about 300 MB on top of the fact cache.
- **S7:** the replay median over 1.5 s; any oracle mismatch that is not a locally fixable trace bug.

## Smaller alternatives

**A. Exact, at about 40 % of the work: S0 to S4 only.**
Leaf commit 2.1–2.6 s, results exactly equal.
No reverse index, no partial link, no new failure modes for a store beyond the memo tables.
Gives up the 1 s target.

**B. A declared fast hook mode (CI stays exact), about 1,000–1,500 lines.**
A lazy `FactSet` (needed by S7 anyway), plus `Link::build` with `analyzed` set to the changed files, so only the tables they reach are loaded.
Labels only for the modules it touches.
Judge only edge rules on core edges, then the existing ledger compare.
Estimate 0.5–0.8 s.
- It skips packs, drives, coverage, facade and export rules, model-label and use-shape rules, reach, levels, and findings in unchanged importers.
- On N7 those importer findings are 1 commit in 33, plus provider-file label changes.
- It can wrongly report a finding that a pack refinement or pack edge would have cleared, so it must run only an allowlist of rules that read no pack field.

**Salsa or a daemon** fits only later, on top of S5's per-name queries, for a watch mode or an editor server (see `docs/research/spike-n9.md`).

Not checked by the planner: the split of the 1.0 s between labels and judging; the real speed of N3b; whether the stored triple groups equal packages.

## The architect's notes

- **The planner corrects D59's point 4.** Rules and packs can be reused before the analysis can, by checking a stored answer forward against the graph in memory. The architect takes that: the program has value from stage 1, not only at the end. (D69 later dropped stage 1: see the header.)
- **The architect doubts S1's saving until it is measured.** A forward check must digest every file's edges on every run (1.48 M edges) before it can skip judging them. The native judge call is about 0.47 s; the digest pass is not free. The stop rule (under 0.3 s) is the right test. (D69 measured it: the forward check costs about three times the judging it skips.)
- **B's 0.5–0.8 s is below the floor of the time.** A check that recomputes nothing costs 0.55 s (listing, policy, store read, output). The architect reads B as "about 0.8 to 1.0 s" until measured.
- **B needs a coverage number before anyone chooses it:** of the findings real commits introduce, how many would the allowlisted rules catch? Measured on the N7 sample afterwards (`docs/research/spike-n7.md`, part 4): 11 of 88, or 12.5 %.
- **Sizes are line counts against resembling code.** They say "the same order as the native round", not more.
