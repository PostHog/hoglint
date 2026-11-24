# Second opinion: phase 3c, where time and memory went after phase 3b

> A second opinion from another model on the native engine after phase 3b, Nov 9, 2026 (evening): where the time and memory went, and what would reach the gates. With the question, the architect's view written before it, and what the decisions took.
> Partly replaced: its proposals became D22 to D26 in `docs/history/decision-log-2.md`; D39 (Nov 10) later moved rule evaluation and labels into Rust, so the Python-side parts of this design are history. The engine as built is in `docs/design/05-engine.md`.

## The question

After phase 3b the native engine (a Rust link with Python packs, labels and rules, as decided in D1 and D2) passed parity, the read audit, the invariants and determinism, but missed the timing and memory gates.
The second opinion was asked for an independent design review: read the code and the measurements, then propose its own design. The questions:

1. Where does the time go, structurally? Confirm or correct from the code.
2. What design reaches the gates? Concretely: which work moves native, which stays Python, which algorithm changes, and the expected saving of each.
3. What is wrong or fragile in the split between Rust and Python? Look at the pack API, labels, and the copy detection.
4. What would you not do, and why?
5. What risks to output parity does your design carry, and how would you test them?

Short answers, ranked by expected impact, citing files and functions.

## What it was given

- The code: the Rust core crate (facts, link, resolution, indexes), the PyO3 binding and its stub, the Python side (config, labels, rules, the native bridge, the packs, the index).
- The decision log as it stood (D1 to D21), the user guide, the PostHog preset and the root config.
- Requirements:
  - `hoglint check` evaluates the whole repo on every run (no incremental evaluation). It runs in pre-commit on the staged tree, which is a new tree almost every time.
  - Gates: warm full check at most 4 s wall on a developer laptop; peak RSS at most 1.5 GB; ten concurrent checks at most 15 GB in total.
  - Output stays identical between the Python reference engine and the native engine, except named deltas.
  - Rules, packs and labels change weekly and are written by Python developers. The native side should hold mechanics, not policy.
- Measurements of a warm native check on a frozen snapshot of the monorepo:
  - scale: 29,038 modules, 291,041 symbols, 1,577,409 core edges, 154,031 pack edges, 41,295 findings;
  - on a quiet machine: 8.2 s wall, 7.8 s user, 1.6 s sys, 2.5 GB peak RSS;
  - per phase, measured under load (CPU inflated): native analysis 1.34 GB peak after, 4.5 s multi-threaded; Django pack 2.12 GB, 4.2 s; PostHog pack 2.29 GB, 1.9 s; conflicts plus rules 2.50 GB, about 6 s;
  - inclusive CPU per rule primitive: Entrypoint 1.26 s (1 rule), Layout 1.22 s (3 rules), find_conflicts 0.65 s, Edge 0.57 s (16 rules), Reach 0.51 s (2), Coverage 0.45 s, Export 0.44 s, Signature 0.42 s (2), UseShape 0.26 s (2), Cycle 0.26 s, ledger read 0.16 s;
  - profiler hot paths (shape only): `Labeler.kind` 343k calls, `match_kinds` 292k, `kind_matches` 1.17M, `glob_match` 2.35M, `label_module` 29k, `path_candidates` 29k, `kind_ruled_out` 320k, `selectors.evaluate` 643k;
  - Python objects alive after rules: 292k `KindMatch`, 120k `EntityId`, 254k `Edge`, 243k `Site`, 112k `Candidate`, 111k `Label`, 2.3M tuples;
  - pack edges by producer: `django.uses` 101,466; `posthog.facade-facts` 42,311; `django.relations` 3,446; `django.migrations` 2,329; `posthog.drives` 2,173; others below 1,200.
- A recent change: the `django.uses` pack emitted a copy of a core `reference` edge with a shape added, and rules detected those copies (`index.core_copies`, `Link.core_twins`, `REFINED_FIELDS`) so that only rules reading a refined field saw the copy.

The second opinion had the decision log of the day, but not the architect's view below.

## The architect's view, written first

Written after phase 3b and before any second opinion.

**Where things stood.** Phase 3b was committed (D16 to D21, bugs 13 to 15, five native queries). Both engines were equal on every table.

| Gate | Target | Now |
|---|---|---|
| Warm full check | at most 4 s | 8.2 s quiet; 14 s measured under heavy load |
| Peak RSS | at most 1.5 GB | 2.3 to 2.5 GB |
| Rules plus conflicts | at most 1.5 s | 3.0 s quiet |
| Ten concurrent checks | at most 15 GB | about 25 GB (extrapolated from three) |

**What the numbers said, in the architect's reading:**

1. **The rules phase is mostly labeling, not judging.** Entity rules (layout, entrypoint) loop over all 291k symbols and ask `labeler.kind` for each one. `match_kinds` runs every `[kinds]` spec per symbol, with ancestry in Python. Path labels run every assignment's globs per path: 2.35M `glob_match` calls. Whichever rule runs first pays for the labeling, so the per-rule numbers hide it.
2. **The 2.5 GB is not mainly Python objects.** The native analysis alone peaks at 1.34 GB. The Django pack adds 0.78 GB at peak, because it still decodes per-file records into Python. The rules add only 0.2 GB.
3. **Pack copies of core edges are the wrong pack API.** `django.uses` re-emits 101k core edges to attach a shape. Bug 13 was fixed by detecting the copies with a hand-kept field list. A new pack field outside the list silently makes rules judge only the core twin. This is a public pack API question, and the repo split (D15) needs that API settled.
4. **Two edge populations make two code paths.** Core edges live in the link as handles; pack edges are Python objects. The native ancestry cannot see pack `inherit` edges.

**What the architect would do:**

1. **Packs write into the link.** A producer appends its edges in one batch call. A refinement (shape, shape detail, flow shape, attrs) attaches to a core edge by its id instead of copying it. Copy detection goes. After the pack phase there is one native edge table, and `Edge` objects exist only for witnesses.
2. **Labels by inverted candidates.** A kind's members come from a reverse reach over `inherit` edges from its seeds; its unknown set from a reverse reach from unresolved bases. An entity rule that pins `kind` iterates members plus unknowns, never all symbols. Path globs compile once into one matcher per field, and each path is matched once, natively.
3. **Native memory.** Measure what the 1.34 GB holds, then intern strings and drop fact rows that no pack query needs after the link. Target at most 0.8 GB for the native layer. Finish the pack queries, so no pack decodes whole records.
4. Keep the 4 s gate. Pre-commit runs on a new tree almost every time, so the run memo does not help there.

**Unsure:** whether reverse reach can match the Python ancestry exactly, including the unresolved markers; whether refinement in place can change any rule's output beyond bug 13; how much of the native 1.34 GB is fact rows versus link tables.

## The second opinion

What follows is the second opinion's answer, cleaned. File references point into Hoglint's modules at the time.

It would keep Python policy, but replace the object-based execution boundary with native tables, projected batches and explicit refinements. The split had moved resolution into Rust while leaving much of the data expansion in Python. Reaching both gates requires fixing that expansion, not merely moving graph traversal.

This was a static review against the measurements; it did not run benchmarks. Savings are engineering estimates.

### 1. Where the time and memory go

The profile is consistent with the code:

- **"Native analysis" includes eager Python-facing expansion.** `NativeAnalysis.run()` exported every symbol into nested Python dictionaries. `NativeStore.__init__()` constructed `KindTexts`, whose constructor immediately called `link.text()`, so the deferred fact text became one whole-snapshot JSON buffer during analysis. Later, `NativeRecord.load()` decoded sections and kept them alongside the Rust facts and the JSON (`native/analysis.py`, `native/build.py`, `src/link/facts.rs`). The 1.34 GB phase peak is not a measurement of a compact Rust engine alone.
- **Pack candidate selection is native; candidate representation and retention are not.** `NativePackQueries.fields_to()` fetched complete edge tuples, created `Site`, target and via tuples and `EdgeFields`, and dropped the originating edge ids. `UseProducer.by_file()` kept those candidates; `shaped()` emitted full replacement-like edges; `aggregate_edges()` also read `uses` rows across the pack's files (`native/queries.py`, `packs/django/uses.py`).
- **Labels are memoized, but their first computation stays expensive.** `match_kinds()` tests each configured kind for each requested symbol. `LayoutPrimitive.kind_ruled_out()` called `kind()` while iterating every symbol: a classification pass dressed as a prefilter. Module labels already cached path candidates; the glob count is not evidence that regex compilation needs caching, because it already was cached (`labels.py`, `rules/entities.py`, `globs.py`).
- **Rule acceleration stops before materialization.** `Grouper` rejected core groups efficiently, but `NativeRuleContext.rule_groups()` materialized the surviving edges. `CoreEdges.get()` kept every materialized edge for the run. Pack edges bypassed the native grouping path. Reach used Python BFS and cached `Hop` objects; cycle collected Python edge-env lists before a Python Tarjan (`native/rules.py`, `native/graph.py`, `rules/graphs.py`).

So D2's "no Python object per row" was only partly realized, and D18's native graph execution was unfinished.

The busy CPU timings cannot be subtracted from 8.2 s wall. Successive peak RSS values are high-water marks, not retained-memory attribution. They identify priorities, not precise savings.

### 2. Proposed changes, ranked by expected impact

| Rank | Change | Expected benefit to validate |
|---|---|---|
| 1 | Replace whole-snapshot JSON exposure and retained decoded records with bounded, projected fact batches. Keep symbols native with sparse pack-added columns. | The largest memory opportunity: target 0.5 to 0.9 GB lower peak and 0.6 to 1.2 s quiet wall saving. |
| 2 | Keep edge ids through pack queries; write refinements and new pack edges into native storage in batches. Evaluate both through the same native grouping machinery. | Target 1.2 to 2.0 s wall saving across packs and rules and 0.2 to 0.4 GB lower peak, overlapping rank 1. |
| 3 | Compute kind membership and label equivalence classes in bulk; drive layout from indexed candidate sets. | Target 0.7 to 1.2 s wall saving across layout, conflicts, entrypoint and exports. |
| 4 | Keep findings as compact identity, count and witness-handle records; run reach and cycle over native adjacency. | Target 0.4 to 0.8 s wall saving plus less survivor and witness retention. Graph traversal alone is a minority of this. |

The estimates overlap. The upper ends would reach 4 s; the lower ends would not. The gate should not be claimed until measured.

Boundaries:

- **Rust:** storage, projection, indexing, ancestry, grouping, joins, counting, adjacency traversal, witness ids, generic matching mechanics.
- **Python:** config interpretation, Django ORM shape semantics, facade and signature policy, provider logic, rule decisions over distinct input classes, ledger admission and rendering.

For Django uses, return batches of `(edge_id, file_id, scope_id, position_id, ...)` with only requested columns. Python classifies distinct positions and handles relevant per-file flow; it returns `(edge_id, shape, detail, flow_shape)` columns. `LocalSources.held()` repeatedly sorted and scanned assignments: use ordered per-local histories with predecessor lookup, keeping its "before every assignment, first source" behavior.

For labels, compute **true and unknown membership sets**, not merely positive kinds. Feed layout the relevant known members plus unresolved or conflicting candidates. Store module label values and provenance ids compactly; do not construct per-symbol `KindMatch` objects for known negatives. Python can keep conflict resolution semantics while native code supplies ancestry, declaration and decorator sets. Path matching should work on distinct paths and pattern candidate buckets; render provenance strings only when needed.

For rules, keep "Python decides once per equivalence class", but extend it to pack rows and keep expansion and counting native. Materialize edges only for displayed witnesses or explicit queries.

Acceptance budget: roughly **1.3 s analysis, 1.0 s packs, 1.1 s labels and rules, 0.4 s ledger and output**, leaving 0.2 s margin. For memory, target **at most 1.2 GB**, rather than engineering exactly to 1.5 GB.

### 3. The fragile parts of the split

The pack API was effectively `produce(core: Any, graph: Graph, root: Path)` (`plugins.py`). It exposed engine internals, mutable graph state and unrestricted row access. Performance then depended on every weekly pack change knowing which innocent-looking property decodes or retains a large dataset.

Replace it with a typed snapshot and query reader and an output sink:

- projected candidate batches with stable, run-local handles;
- explicit symbol facts, new edges, refinements and producer failures;
- declared inputs and outputs, and a finalization boundary before rule indexes are built;
- a bounded escape hatch for arbitrary Python analysis.

**Copy detection is a semantic workaround, not a sound refinement model.** `copy_key()` and native `has_twin()` matched kind, location, surface and targets. They omitted scope, contexts, resolution, via, use and attributes. An independently emitted coupling could be classified as a refinement merely because those fields collide. The native side also built a large by-site index to rediscover what the pack query had already known (`index.py`, `src/link/index.rs`).

`REFINED_FIELDS` was a global hard-coded list. `reads_refinements` examined decision reads, excluding identity columns, so a rule could use a refined field in its identity without receiving the copy. And a rule that read refinements saw both the core edge and the copy; the logic did not replace the former (`rules/base.py`).

Use an explicit `refines=edge_id`, declared changed fields and producer provenance. At first, keep the two-row visibility through a compatibility view. Switching to one enriched logical edge is a separate, named semantic delta; collapsing them silently would put parity at risk. Multiple refiners need explicit conflict and composition rules.

### 4. What it would not do

- **Do not rewrite Django or PostHog policy in Rust.** Batch execution removes most boundary cost without making weekly policy changes need Rust work.
- **Do not start with SCC optimization.** Reach plus cycle were only 0.77 s of the inflated profile.
- **Do not add more unbounded memoization.** Existing caches explain much of the live object population.
- **Do not depend on exact-tree result caching or incremental evaluation.** New staged trees defeat the first; the second violates the requirement. Per-blob syntax caches stay useful.
- **Do not parallelize Python packs with processes.** Copying this footprint undermines the ten-check memory gate.
- **Do not remove the reference engine before this redesign stabilizes.** The golden snapshot cannot exercise future weekly policy changes.

### 5. Parity and gate validation

The highest risks are unknown handling, refinement multiplicity, incomplete grouping keys and order-dependent witnesses. Extend the parity tooling with:

- differential comparisons of **finding identities and multiplicities, incompletes, conflicts, witnesses, rendered output and exit status**, not just finding totals;
- generated small graphs and configs covering missing versus `none`, incomplete ancestry, conflicting kinds, overrides, endpoint attributes, multiple targets and unknown selections;
- refinement cases with identical locations but different contexts or resolution; multiple refiners; refined fields used only in identities; failed refinement producers;
- read-audit checks plus deliberately perturbed non-representative rows: auditing only representatives cannot show that all group members share the inputs;
- exact glob parity, including repeated capture names, which the translator implemented with regex backreferences;
- stable BFS and SCC witness selection, context filters, unresolved obligations and resolver query-order tests.

Performance acceptance: **warm fact caches but fresh staged trees and no result-cache shortcut**, measuring end-to-end wall time and peak RSS. Run one staged check and ten overlapping checks, including cache writes and pruning. The staged path defaults to one thread; the supplied multi-threaded analysis measurement does not establish that path's timing.

The decisive experiment is whether ranks 1 to 3 remove bulk Python expansion. If they do not, moving the remaining algorithms to Rust will not rescue the memory gate.

## What was taken

From the decision log (`docs/history/decision-log-2.md`, phase 3c). The architect recorded that both views agreed on the direction, and that the second opinion found three things the architect had missed: `KindTexts` read the whole snapshot's fact text at analysis; `CoreEdges` kept every edge it made; pre-commit is the single-threaded staged path.

- **D22. The pack API is a typed reader and a sink; packs refine core edges and never copy them.** The reader gives projected candidate batches with run-local edge handles, and a bounded escape hatch whose uses the run counts. The sink takes new edges in one batch per producer, refinements by `refines=<edge handle>`, symbol facts and producer failures. The pack phase ends before any rule index is built. Two refiners that set one field differently are a producer failure. Copy detection, `REFINED_FIELDS` and the copy case in rules were removed. The reasons cited the second opinion's finding that copy detection matched on six fields and omitted the rest.
  - **Not taken: the compatibility view.** The architect chose one enriched edge with no two-row view: the only rule that read model shapes filtered `edge.shape != none`, so no delta was expected, and a view kept only to be deleted is waste when the parity tooling already names every delta.
- **D23. No whole-snapshot expansion into Python** (rank 1): `link.text()` goes, symbols stay native with sparse columns, `CoreEdges` keeps nothing, strings are interned, fact rows released after the pack phase. Memory target at most 1.2 GB, taken from the second opinion.
- **D24. Labels come from membership sets, not per-symbol classification** (rank 3, and the architect's own point 2): a true set and an unknown set per kind from reverse reach over `inherit` edges; no `KindMatch` for known negatives; path labels once per distinct path with globs bucketed by literal prefix; provenance rendered only when shown.
- **D25. Rules group pack rows like core rows;** reach and cycle move to native adjacency only if the budget still misses (rank 4, with "do not start with SCC"). It was triggered after phase 3d.
- **D26. The gate is the pre-commit path:** fresh staged tree, single-threaded, warm fact cache, no memo hit; also ten overlapping staged checks with cache writes and pruning. The budget per phase is the second opinion's: analysis 1.3 s, packs 1.0 s, labels plus rules 1.1 s, ledger and output 0.4 s, margin 0.2 s.
- **Parity tests added for phase 3c, from the second opinion:** refinements at the same site with different contexts or resolution; two refiners of one edge; a refined field read only by an identity; a failed refinement producer; the read audit on perturbed non-representative group members; glob parity with repeated capture names.
- **Later:** D29 to D31 continued the "Python decides once per class" line. D39 (Nov 10) then moved labels and rule evaluation into Rust, beyond what this second opinion proposed ("do not rewrite policy in Rust" still holds: the policy stays TOML).
