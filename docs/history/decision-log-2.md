# Engine decision log, part 2 (D22 to D47)

> The engine decision log of Hoglint, part 2: phase 3c, phase 3d and its reviews, phase 4a and 4b, the native-tool direction (D39), phase N1 and its reviews, and the PyO3 end state (D47), Nov 9 to Nov 10, 2026.
> Partly replaced: the current state of each topic is in `docs/design/` (start at `docs/design/README.md`); later entries here and in `docs/history/decision-log-3.md` revise several decisions.

This is part 2 of a chronological log; read the note on how to read it, and who decided, at the top of `docs/history/decision-log.md`. A decision marked "the owner" was stated by Hoglint's owner; every other decision is the architect's (an AI agent), and the owner could veto it.

## Phase 3c: no bulk expansion into Python (after phase 3b, Nov 9 evening)

Phase 3b passed parity, the read audit, the invariants and determinism. It missed the timing and memory gates: 8.2 s warm, 2.5 GB. Inputs: the architect's measurements and view (written first), and an independent design from a second opinion from another model (`docs/research/second-opinion-3c.md`). Both agree on the direction. The second opinion found three things the architect had missed: `KindTexts` reads the whole snapshot's fact text at analysis, `CoreEdges` keeps every edge it makes, and pre-commit is the single-threaded staged path. The architect did not take the second opinion's compatibility view for copies (see D22).

**D22. The pack API is a typed reader and a sink. Packs refine core edges; they never copy them.** This settles the pack surface D15 waits for.
- Reader: projected candidate batches with run-local edge handles. Only the columns a pack asks for cross. A bounded escape hatch reads one file's record, and the run counts its uses.
- Sink:
  - new edges, in one batch per producer;
  - refinements: `refines=<edge handle>` plus the fields it sets (shape, shape detail, flow shape, attrs);
  - symbol facts;
  - producer failures.
- The pack phase ends before any rule index is built. After it, there is one native edge table. Ancestry, groups, reach, cycles and kind members all read it.
- Two refiners that set the same field on one edge differently are a producer failure, which makes the rule incomplete.
- Removed: `core_copies`, `Link.core_twins`, `copy_key`, `REFINED_FIELDS`, and the copy case in `EdgeRule.sees`.
- One enriched edge, no compatibility view with two rows. The only rule that reads model shapes filters `edge.shape != none`, so the architect expected no delta. Parity against the pinned baseline must show zero or a named delta.
- Why:
  - Copy detection matches on six fields and omits scope, contexts, resolution, via, use and attrs, so an independent edge can pass as a copy (the second opinion).
  - A hand-kept field list is a trap in a public API.
  - Two edge populations need two code paths, and the native ancestry cannot see pack `inherit` edges.
  - A view kept only to be deleted is waste when the parity tooling already names every delta.

**D23. No whole-snapshot expansion into Python.**
- `link.text()` as one buffer goes. Packs get projected batches (D22).
- Symbols stay native, with sparse columns for what packs add. There is no Python dict per symbol.
- `CoreEdges` does not keep the edges it makes. `Edge` objects exist only for witnesses, display and `query`.
- The native side interns strings and releases fact rows once the pack phase ends.
- Finish the pack queries (`uses_to`, kind mentions), so no pack decodes whole records.
- Memory target: ≤1.2 GB peak for a check (margin under the 1.5 GB gate).

**D24. Labels come from membership sets, not per-symbol classification.**
- Per kind there is a true set and an unknown set. Both come from reverse reach over `inherit` edges from the kind's seeds, filtered by decl, abstract and decorators. Unknown means a first-party symbol without facts, or a descendant of an unresolved base.
- An entity rule that pins `kind` iterates true members plus unknowns, never all symbols. Known negatives get no `KindMatch` object.
- Path labels are computed once per distinct path, with assignment globs bucketed by literal prefix. Provenance strings are rendered only when shown.
- Python keeps the label meaning: value resolution, overrides and conflict policy. Whole-table matching moves native only if a measurement says it must.
- This refines D1's "labels in Python": Python plans, and bulk set work runs where it is cheapest.

**D25. Rules group pack rows like core rows.** One edge table (D22) lets the grouper treat both alike. Reach and cycle move to native adjacency only if the budget still misses after D22 to D24; they are a minority of the cost (the second opinion agrees).

**D26. The gate is the pre-commit path.**
- Measure `check --index` on a fresh staged tree, single-threaded (D6), with a warm fact cache and no summary memo hit.
- Also measure ten overlapping staged checks, cache writes and pruning included, and the full check with all cores.
- Budget: analysis 1.3 s, packs 1.0 s, labels plus rules 1.1 s, ledger and output 0.4 s, margin 0.2 s.
- Report CPU time always. Report wall time only on a quiet machine.

Parity tests added for phase 3c (from the second opinion):
- refinements at the same site with different contexts or resolution;
- two refiners of one edge;
- a refined field read only by an identity;
- a failed refinement producer;
- the read audit run on perturbed non-representative group members;
- glob parity, including repeated capture names.

## After the 3b adversarial review (Nov 9 evening)

**D27. A rule group shares only its verdict.**
- The group shares the decision: violation truth, droppable, unknown.
- Everything that names the entity is computed per edge from that edge's own env: identity, subjects, owner, ledgerable. Findings are about 41k, so this is cheap.
- At that time `replica` copied the representative's owner and subjects, so findings went to the wrong team (review finding 2, reproduced; the preset rules `foundation-direction` and `infrastructure-direction` have this shape).
- Templates are keyed by edge handle, never `id(edge)`. Once D23 stops keeping edges, `id()` values get reused.
- `sees` is evaluated per edge, in bulk where the link can do it.
- The read audit covers the judge step too. `IMPLIED` keeps only reads the group key holds. Representatives that `sees` rejects are audited.
- A re-judged verdict that differs from its group's verdict is an engine error (exit 2), never silent.
- `repo1` from the review becomes a permanent parity fixture.
- Why the architect did not take the reviewer's smaller fix (patch owner and subjects in `replica`): it fixes the two fields known so far. Sharing only the verdict makes a wrong copy impossible for any field added later.

**D28. The fact cache is a cache: a cache problem is never a wrong answer and never a failed run.**
- **Keys come from the bytes extracted.**
  - Dirty and untracked files are hashed from their raw bytes (`git hash-object --no-filters`).
  - Rust hashes the bytes it actually read as a git blob, and stores an entry only when that hash equals the snapshot oid.
  - On a mismatch, the file changed during the run. The run uses what it read, and stores no fact entry for that file and no run summary (the D3 drift guard extends to `.py` files).
  - Why: review finding 1 showed one editor save can poison a machine-wide entry for good. Not storing dirty files at all would also work, but it hides the race instead of detecting it.
- **Reads are by natural key** (config, oid), never by row id alone (finding 3).
- **A miss is never an error.**
  - An entry that went away mid-run is a miss: re-extract that file.
  - Any open, read or prune error is logged once, and the run continues without the cache.
  - A corrupt database is moved aside.
- **One pruner per subtree.**
  - `CacheRoot` skips `facts/`.
  - The fact pruner deletes another facts id's database only as a unit (main file, `-wal`, `-shm`), and only when no process has opened it for a day (size pass) or for 30 days (age pass). Every open touches a per-database mark.
  - `incremental_vacuum` is stepped to the end.
  - The prune mark is per facts id.
- **The layout version is in the database file name** (finding 6).
- **No read transaction is held across extraction** (finding 8).
- Why: review findings 3 to 8. A shared per-machine cache serves every worktree, so its failure modes must degrade to a recompute.

**Labeler cache: a module field's domain is a property of the module.** `compute_value` evaluated a module field's domain selector with the asking symbol, but cached the result per module, so the answer depended on which symbol asked first. A module field's domain is evaluated with no symbol. A domain selector that reads a symbol field on a module field is a config error.

**D29 (direction, Nov 9; build after D22 and D23 land, confirmed by a fresh profile first). Selectors run once per label class, over per-run label columns.**
- After the labels work (f59dbe26aab), labeling is about 13% of the rules phase. The rest is per-env interpretation: about 614k selector evaluations, 928k `to_value` calls, 873k `read_endpoint` calls and 8.4M dict lookups (cProfile, all rules).
- Label values become per-run columns:
  - one value per module per field, computed once, lazily per field, and indexed by module ordinal;
  - symbol fields are sparse, for the symbols that carry them.
- `read_endpoint`, `field_value` and `Grouper.dst_class` read the columns. There is no per-key dict cache and no label row built per destination key.
- Selectors are evaluated per distinct label class, or per (src class, dst class) pair for edge rules. Edges map to classes by module ordinal, and the link can do that mapping in bulk.
- Python keeps the selector language and its three-valued semantics. Only the evaluation unit changes.
- Why: the per-edge cost is interpretation overhead, not labeling. Two populations of label reads (the labeler's cache and `Grouper` rows) do the same work twice.
- The labels agent's changes, accepted (Nov 9): the `kinds_can_overlap` gate is removed, so every name two kinds hold is a config error (no delta on the snapshot); `restricts` pins on text literals only (fixes `kind == none` pinning on "None").

**D29 result (Nov 9).** Built as 73d9a43d4a5, with zero delta. It saved about 10% of real CPU in the rules phase: 2.65–2.77 s → 2.45–2.52 s, measured under load. cProfile had put per-env interpretation at 45–50% of the phase, but it inflates many tiny calls. Lesson: confirm a profile share with low-overhead timers before committing to a redesign.

**D30 (Nov 9; after D23 lands). Rust iterates edges; Python iterates label classes.**
- What remains in the rules phase is Python iterating per edge and per key, plus making objects. Timers, inclusive:
  - edge-rule grouping about 0.8 s (58k `Endpoint`s, column fills, class tuples);
  - `LinkEdges.get` 0.55 s (122k calls);
  - `replica` 0.19 s (35.5k findings);
  - two whole-table decl scans 0.1 s, because `NativeSymbols.__getitem__` builds a dict per read.
- Python computes a code per module per column, once per run (D29 columns).
- The link maps every edge to its (src class, dst class) in bulk from those code arrays, the surface name included, and groups natively. Python sees one representative handle per class.
- The entity key is needed only where a `subclass_of` export covers the path.
- Rules take handle arrays, not `Edge` objects. `Edge` objects are made only for findings that print, witnesses, `explain` and `query` (D2, D18).
- Findings stay compact (identity, counts, handle) until shown (D18). The summary reads identities and the witnesses move admission needs.
- A native query answers "symbols with decl X". `NativeSymbols` reads return values, not dicts.
- Why: D1 says Python decides once per equivalence class. The loop over edges must not stay in Python just to find those classes.

**D31 (Nov 10, open: settle before D15). The public pack API exposes queries, not engine internals.**
- The phase 3c `PackReader` still hands packs `index`, `tables`, `resolver`, `classes` and `context`, which are engine internals. It also returns two styles: `Edge` lists from `edges_to` and `edges_spelled`, and columns from `batch_to`. Symbol facts arrive as a dict of dicts.
- Published as they are (D15), these would freeze the internals.
- Before the split:
  - every internal a pack reads becomes a named query, or moves into the PostHog or Django pack's own code;
  - one result style: columns with handles;
  - the per-file record escape hatch stays, and the run counts its uses.
- Proof: the blind maintainer test (add a rule, a Django hook pattern, a PostHog fact) uses only the narrowed API.
- Later: dropped by D39, then reframed under "The PostHog pack's home" below.

## After phase 3d (Nov 10)

Phase 3d (6a061a406aa to 33b1723e535, all zero delta): staged `check --index` takes 4.83–4.90 s wall at its best paired runs (4.8–5.2 s CPU), against 5.2 s before. Peak 1.46 GB, so the 1.5 GB gate is met. Ten overlapping checks take 10.1 GB, so that gate is met. Each further step saves 0.2–0.3 s, spread over many small Python costs.

Context for the gate: the PostHog monorepo's pre-commit hook runs ruff fix, ruff format and `ty check` on staged Python files. tach, import-linter and product:lint run only in CI, so Hoglint is a new pre-commit cost. lint-staged runs its glob groups in parallel, so Hoglint overlaps the existing Python chain.

**D6 REVISED. A staged run picks its thread count from the load.**
- Threads = clamp(cores − 1-minute load average, 1, 4). `HOGLINT_THREADS` still overrides.
- Why: one thread protected the rare case of ten simultaneous commits at the cost of every ordinary one. Output is already proven identical across thread counts (determinism gate: seeds and threads 1, 2, 4, 8, all).
- The analysis phase (about 1.0 s at one thread) is the only multi-threaded part.
- Later: the third version below returns to one thread.

**D25 triggered.** The budget still misses after D22 to D24, so reach and cycle move to native adjacency, as D25 said they would.

**Grouping hole (found by the 3d agent), to fix before more speed work.**
- `Reads.of` keys `edge.label` on the edge kind only, while the audit grants label and shape whenever kind is read.
- A rule whose decision reads `edge.label` could share a group across different labels, and the audit would not catch it. No preset rule decided on `edge.label` at that time.
- Fix: key `edge.label` on what label derives from, and let the audit grant only what the key holds (D20, D27).

**Portability gate (D14) PASSED at ffedb9cc30d** (macOS arm64, Linux arm64, Linux amd64 in Linux VMs): all ten tables and every key part are equal. It took one fix first: the framework digest hashed the whole RECORD file, which holds the venv's path in a script shebang. The architect's own script runs it; rerun at the end of every phase.

**D32 (Nov 10). Everything a run executes or reads is in a key part, or the run fails loudly.**
- **An unreadable source is an incomplete obligation, not a new exit code.**
  - Each unreadable path is an `IncompleteItem` with cause EXTRACTOR, keyed on the path.
  - It follows D9: one the change introduces blocks; an inherited one does not.
  - At that time it was only a stderr line from the Python engine, and the native engine crashed on it (`native/snapshot.py` `worktree()`: `hash-object` raises).
- **Plugins declare the distributions they import.**
  - The PostHog pack and its owners provider import an owners-file distribution from the environment, and no key part covered it. A Linux run without it lost the owners of 34k findings under an equal key.
  - Each plugin lists its distributions. The framework digest covers them as it covers django (the `.py` RECORD rows).
  - A declared distribution that is missing is a provider failure.
  - Why declare instead of tracing imports: explicit and boring. A missing declaration shows as a failure on a clean machine, which the portability gate runs.
- **External file paths are relative to site-packages at extraction** (for example `<site-packages>/django/...`). At that time 19 django migration-parent edges held the absolute venv path, so a witness could name one machine's venv while the key matched. This is a named delta: 19 edge rows.
- **Local builds do not run the manylinux audit.** `bin/build` builds with `--compatibility linux`. `manylinux2014` stays in `binding/pyproject.toml` for release wheels, which are built in a manylinux container (the wheels workflow of D15).
- **D32 amended (Nov 10):** an unreadable source always blocks (cause EXTRACTOR, exit 2), and a summary that holds one is never stored. Staged and commit runs read blobs from git, so only a local working-tree run can meet an unreadable file. An "inherited" one cannot arise, and a summary that holds less than its tree must not serve as a baseline or a memo hit. Built in 6b396b4445f, 29d4183a0ef and 7a2438a51b0. 7a2438a51b0 also covers editable installs: their RECORD lists only a path hook, so the digest builds the rows from the files the import finds. A macOS editable install and a Linux wheel of the same code then give one digest.

## Review after phase 3d (Nov 10; a fresh look at the agents' work and this log)

The architect wrote a view with the measurements first. A second opinion from another model (`docs/research/second-opinion-3d.md`) and a metamorphic grouping test were running. The items below were **proposed** until those were in. Items marked "the owner" are the owner's calls.

**Phase 3d closed at dc2271eba47.** cf9917c0a5f (export members as handles) and dc2271eba47 (lazy kind groups): zero delta in both engines, 339 tests.

**Measured defects of the commit path (not covered by gate D26):**
- A commit with no stored baseline evaluates HEAD inline, then the staged tree, in one process: 25.0 s wall, 2.33 GB, against 8.3 s and 1.40 GB on a hit in the same minute.
- One dirty or untracked non-Python file anywhere in the working tree stops a staged run from storing its summary, so the next commit has no baseline.
- A staged run reads its side inputs (owners, CODEOWNERS, turbo and package files, provenance, layout file checks) from the working tree. D4 says they come from the snapshot.
- The key holds the whole repo's tree, so a commit with no Hoglint input still changes it.

**D33 (proposed). A staged run reads every input from the index.**
- A provider or adapter declares the path patterns it reads. The run materializes those from the snapshot, and nothing else is visible to the plugin.
- The drift check and `git status` leave the staged path. Every staged run can store its summary.

**D34 (proposed). The summary key names the input set, not the repo tree.**
- The `tree` part becomes the digest of (path, blob oid) over the discovered sources, the declared side inputs and the policy files.
- The patterns are static, so the key is known before the run. D32 holds: what a plugin can read is what the key covers.

**D35 (the owner's). What a commit does when HEAD has no stored summary.** It must not evaluate HEAD inline. Choices: unattributed incompletes that do not block locally (CI attributes); a background warm from `post-merge`, `post-checkout` and `post-rewrite`; both; or accepted incompletes in the ledger.

**D36 (proposed). At most K evaluations at once per machine.** A lock-file semaphore in the cache root, K from free memory. The others wait.

**D37 (direction, to evaluate). Facts are mapped, not decoded.** One position-independent binary store per facts id, shared by every process; rows are views. It replaces D19's JSON records in SQLite and keeps D28's cache rules. Measure the physical footprint, not the peak RSS.

**D38 (direction, to evaluate after the grouping test). Rust evaluates selectors per edge over columns; nothing is grouped.**
- Python computes each selector atom once per distinct value of its column. Rust combines atoms per edge with three-valued logic. Identity is code columns; a witness is an edge id until it prints.
- It replaces `Grouper`, `EndpointClasses`, `FindingClasses`, `Rejudged`, `Reads` and the read audit.
- D1 amended: rule evaluation, labels and identity projection are mechanics. Rules are TOML, the primitives are a closed set, and no plugin adds one. Packs, providers and adapters are the Python extension points.
- Confirm the saving with timers before any build (estimate: 0.3 s alone, 0.9 s with labels and witnesses native).

**Gate amendments (the architect's).**
- Measure three commit paths: baseline hit, baseline miss, and a working tree with an untracked file.
- Time in pairs with a calibration export. The machine's state moves every phase by up to 1.4 times under the same load.
- A test that needs the native module fails when the module is missing, when `HOGLINT_REQUIRE_NATIVE=1` is set (CI and gate runs set it). No skip by file time.

**At D15 step 1:** three crates (facts, link, binding). The facts id is the digest of the facts crate, so the `!` lines in `engine-sources.txt` go.

**The owner's calls from this review:** the memory target (the gate said 1.5 GB), the time target (4 s, or no slower than the monorepo's hook of that time at about 3 s), and D35.

## After the second opinion and the final 3d gates (Nov 10)

**Final gates at dc2271eba47:**
- Pass: ruff, ty, clippy; read audit 14,355 decisions, 0 undeclared; crossings 968 + 2; tach 0/0 over 99,210; 339 pytest and 13 cargo tests; determinism over 5 runs.
- Staged check, one thread: 4.89 to 5.06 s, measured under load (the older export 33b1723e535 took 5.49 s in the same minutes, so HEAD is about 0.5 s faster). The 4 s gate is missed.
- Peak 1.40 GB. Ten overlapping checks, memo off: 16.3 s each, 13.0 GB together.

**The second opinion (`docs/research/second-opinion-3d.md`) reached the same main findings without seeing the architect's view:** the commit path often runs two scans, the blanket drift guard causes it, grouping is becoming a second semantic engine, and memory is overlapping copies. It added four things, all verified in code:
- The index is read three separate times (`checkout-index`, `write-tree`, `ls-files`), so a summary can be stored under a tree the run did not evaluate.
- A baseline miss keeps two graphs alive: the cyclic collector is off and `rule → findings → Rejudged → rule` is a cycle. Measured: peak 2.33 GB, and 1.78 GB with one collection after the baseline.
- The summary store can fail a run (prune outside the guard, unguarded `utime` and `put`, a prune mark with no exclusion).
- A load average is not admission control, and the framework digest does not cover transitive imports, native extensions or package data, so D32 says more than the code does.

**D33, D34 and D36 are decided** (both reviews agree). They are built as phase 4a, together with: one captured tree per staged run, disposal after the baseline run, a summary store that never fails a run, and tests that fail instead of skip.

**D6, third version: a staged run links on one thread; D36 bounds how many run at once.**
- The adaptive count gave no gain in paired runs (5.6 to 9.4 s against 4.9 to 5.1 s at one thread, same hour), and many simultaneous starts all read the same low load.
- `HOGLINT_THREADS` still overrides. A baseline run started by a commit uses the staged count.

**D32, scoped down.** The framework digest covers the Python files of the declared distributions on an intact install. It does not cover native extensions, package data or transitive imports. The envelope's doc says so; the supported contract is "declared distributions, intact installs".

**D7 amended.** After cutover a small independent oracle stays for generated cases (selectors, unknowns, irregular edges, refinement conflicts). Both engines shared the Python rule code at that time, so ten equal tables cannot show an error they share. The metamorphic grouping test is its first part.

**Still the owner's:** D35, the memory target and the time target. D37 and D38 stay directions until the targets are set; each needs a spike with timers first (the second opinion's acceptance bar for D38: public-surface plus one shape-sensitive rule, 0.7 to 1.0 s saved in the rules phase).

## Direction: Hoglint as a native tool (Nov 10)

**The owner's targets, Nov 10.**
- Memory: no target set.
- Time: no slower than the existing hook is the aim, traded against effort; 4 s on a commit is acceptable if less takes a very large effort.
- Baseline miss: no background job; the tool itself must be fast enough (Ruff-like speed over tens of thousands of files).

**Reference values, same snapshot and hour:** `ruff check` whole repo without cache 1.4 s wall, 17 to 19 s CPU, 0.39 GB; `tach check` 2.3 s, 10 s CPU, 0.22 GB; `ty check` 9.9 s, 105 s CPU, 7.2 GB; `hoglint check --index` 4.9 s, 4.7 s CPU, 1.40 GB. Hoglint does the least work and is the only one on one core, because 3.7 of its 4.9 s are Python.

**D39 (the architect). Rust runs everything a check does. TOML holds the policy. Python is packaging and an optional plugin host.** Full design: `docs/research/native-engine-design.md`.
- Targets: one whole-repo check in at most 1 s wall (1.5 s on one thread) and at most 0.5 GB. Targets, not results, until the spike confirms them.
- Phases N1 to N5, each with zero delta against the frozen Python implementation: labels and edge rules; the other primitives; the Django and PostHog packs; ledger, summary and CLI; a mapped fact store and a parallel link.
- N1 starts with a spike (`docs/research/spike-n1.md`). Gate: judgments equal for every edge, and at least 0.7 s off the rules phase.
- D38 is folded into N1. D29, D30 and D25's Python side stop here: no more work to make the Python layer cheaper.
- D1 and D2 amended: the split is no longer mechanics against meaning. The binding stays for the optional plugin host and for the port's stage-by-stage comparison.
- D31 dropped: the Python pack API is not published as a stable surface. Packs become built-in modules selected by config.
- D35 answered: no background job. A missing baseline is a second run, which a fast engine can afford. Phase 4a's fixes make misses rare until then.
- D36 and D6 (third version) are interim. They go when a check is light.
- D7 as amended holds: the Python implementation is the oracle through the port, then stays in the test suite until a golden corpus replaces it.

**The owner's calls that remain:**
- The PostHog pack moving into the Hoglint repo as a built-in module (part of D15). Settled below under "The PostHog pack's home".

**N1 spike result (Nov 10, `docs/research/spike-n1.md`).** Labels and all 17 `edge` and `entrypoint` rules judged per edge in Rust, no grouping: 0 differences on 290,380 label values and 3,648,152 judged pairs. One thread: 0.08 s labels and 0.20 s judging, against 0.24 s and 0.66 s for the Python engine in the same minute (about 0.6 s saved; 0.055 s wall on four threads). Plain Python per edge takes 12.3 s, so grouping had bought 18 times and Rust adds 3 times on this part. The correctness half of the gate passed; the time half is 0.1 s short on one thread for the scope measured. D39 stands, with the estimate corrected in `docs/research/native-engine-design.md`: about 1.7 s on one thread after N1 to N4, about 0.6 s wall on four threads after N5. N1 is built after phase 4a lands, because both touch `labels.py` and `engine-sources.txt`.

**Grouping test result (Nov 10).** One real divergence, verified by rerunning its minimal spec: a use-shape rule with `flow = "def-use"` and `select = "edge.shape == 'write'"` gets 32,343 findings grouped and 32,565 per edge. `Grouper.stage` keys an edge-only conjunct on the link's shape, while `UseShapePrimitive.env` replaces the shape with the flow shape. 222 findings are dropped with no error. No preset rule was exposed (`model-crossings` stages `edge.shape != none`, and every edge with a flow shape has a link shape); one changed selector in that rule would lose 9 of 371 findings.
- 671 random specs and 630 targeted identity checks found nothing else. `FindingClasses` sharing held in every case, so that concern of the architect's was not borne out.
- It is the third grouping hole, and like the first two it was found by reading the code. The random specs missed it.
- Not fixed in the grouped engine: N1 deletes that code, and the spec becomes a permanent test of the new evaluator. Had the grouped engine shipped, the fix (the stage key takes the primitive's own reads) would have been needed first.

**Phase 4a landed (Nov 10): 42b7427a6e0 to 2e9fadc662d, eight commits.** One captured tree per staged run, declared side inputs in per-plugin directories, summaries keyed by input set (protocol 3), disposal after the inline baseline, a summary store that never fails a run, K slots, tests that fail when the native build is required, and a tree capture from a copy of the index (found by the ten-overlap gate: `git write-tree` takes `index.lock`).
- The agent's numbers, in same-minute pairs: the commit after an untracked note went from a miss (10.0 s, 2.27 GB) to a hit (5.4 s, 1.42 GB); a baseline miss keeps its time (about 11 s) and peaks at 1.78 GB instead of 2.33 GB.
- Named delta: the stored summary's envelope (`tree` → `inputs`, protocol 2 → 3). Behavior change: an index with conflicts exits 2 with one line, with no report.
- The architect's independent check and an adversarial review were running.

**D36 amended.** The default K is one slot per 4 GB of memory, capped by the core count, with no cap at four. With the cap, ten overlapping checks on a developer laptop with ample memory finished in waves up to 21.4 s, against 11.6 s all at once, to save memory the machine did not lack. To build with the review's fixes.

**Open from 4a, for the fix round after the review:**
- Packs still get the repo root (`run_packs` in `hoglint/source.py` was outside the agent's paths). Their one file read comes from the tree, but nothing enforces it.
- Plugin roots sit in a process-wide registry keyed by path (`PLUGIN_ROOTS`), also because of the path limits. Pass them through `Labeler` and `RuleContext` instead.
- The Django pack's own cache under `<cache>/packs` is not covered by "never fail a run".
- A coverage column that asks whether a non-source path exists reads the tree's path list, which the input set does not cover.

**N1 started (Nov 10)** at 2e9fadc662d.

## After the adversarial review of phase 4a (Nov 10)

Seven defects, each with a repro. The architect reran two (the stale summary from an ignored directory, and the symlinked CODEOWNERS) and got the reviewer's output. What held: every hook index mode, split and sparse indexes, linked worktrees, three ways to run one tree, ten overlapping staged runs, and the store's handling of bad manifests.

**D40. A working-tree snapshot is the index's blobs plus what `git status` reports. Only a tree run writes the summary store.**
- The working tree, as an input, is: the index's blobs, overlaid with the files `git status` reports as modified or untracked. Nothing else on disk is an input: no ignored file, no empty directory, no file behind a `skip-worktree` or `assume-unchanged` bit. Both engines read it that way.
- A question "does this path exist" is answered from that list, never from the file system.
- When the status shows no change to an input, `check` runs HEAD's tree as a tree run. Otherwise it runs the overlay and stores nothing.
- Why: the review showed a working-tree run reading things the status cannot see (an ignored `__pycache__` under a path a layout rule forbids; sparse checkouts; a file saved during a Python-engine run) and storing the result under HEAD's key, where staged runs then read it back. The reviewer's smaller fix (never store a working-tree run) leaves two meanings of "working tree" in the two engines.

**D41. A symlink inside the tree is read as its target.**
- A listing resolves a link to the blob it points at, through chains, and the input digest holds the path, the mode and the resolved blob.
- A link that is dangling or points outside the tree is an unreadable source (D32), or a provider failure for a side input.
- Why: a tree run read a symlinked CODEOWNERS as a file holding the target's path, so `check --index` lost the owners that `check` printed, under one key.

**D36 withdrawn. No limit on concurrent evaluations.**
- In its first review it produced a hang (`compare` waits for a slot its own parent holds; with one slot, as on a 7 GB CI runner, it never ends), a broken bound (the daily prune deletes held slot files) and a crash on a bad setting.
- A slot limit pays only when many checks run at once; one check per CI job does not need it.
- The staged run stays on one thread (D6, third version).

**Existence questions are declared inputs.** A layout rule's `files` and `absent`, and a coverage adapter's question whether a path is tracked, may only ask about paths that match a declared pattern or a source. Those paths are in the input set. A question outside them fails loudly.

**Also to fix:** prune marks either side of midnight delete each other; `**` in input patterns does not match a newline; `compare` does not pass `--engine` to the base engine's child process, so `compare --engine native` fails its base scan whenever the engine changed (since b143de73743).

Built as phase 4b, on paths N1 does not touch.

**Portability gate PASSED at 2e9fadc662d** (macOS arm64, Linux arm64, Linux amd64): all ten tables equal in both engines on every platform, and every key part equal, the new `inputs` digest included. No row needed the site-packages path replaced any more. Rerun after 4b and N1.

**A tool lesson from that run.** A preset rule named a policy file that did not exist in the tree. The rule read the missing file as an empty list, so every relation it should have classified became a finding. Decision: a policy file that a rule names but the tree lacks is a config error.

## Phase 4b landed (Nov 10)

Commits 93199c3c4b8 (slots removed), 3bef58982b0 (D40), 54ee5ac0e5a (D41), dc49577d7c7 (existence questions fail outside the key), c15e6381920 (prune marks), f1c3766f4bc (`**` with a newline), 24c3a0e4ada (`compare` passes its engine), f2bce72924a (a pack gets a root of its own), b36e56ec016 (the Django pack cache never fails a run), e9f4830295c (a clean check pays less for links and plugin roots). New module `hoglint/ledger/listing.py` (`TreeListing`, `WorktreeListing`, `Links`).

**The architect's check so far (export of b10fc74cf04, Python engine):** r01, r02, r03, r10 and r14 print the right output where they printed the defect; r09 prunes twice instead of four times; r15 covers all three paths; r11 gives one report and one key three ways, with and without an untracked file; r13 still misses on every bad manifest. **Still to run after N1:** the native repros (r01 to r05, r14, r16, r06 as a compare), parity against 2e9fadc662d, the gates, timings.

**Decided by the architect:**
- **The clean working-tree check may cost 0.2 s more CPU.** The agent measured +0.22 s (median of pairs, measured under load) against a 0.1 s gate. The cost is real work D33 asks for: 361 side-input blobs written from git into the plugin roots, and `ls-tree` in place of `ls-files`. Accepted: 0.2 s is small next to a whole check. The lever, when it matters: keep a plugin root in the cache under the digest of its declared inputs, so an unchanged set of owners files is written once.
- **A dangling link as a policy file reads as absent.** It becomes a config error together with "a policy file a rule names but the tree lacks".
- **One snapshot object per run, as a follow-up after N1.** Four process-wide registries held run state: `PLUGIN_ROOTS`, `WorktreeStatus.taken`, `TreeListing.taken` and `WorktreeListing.taken`; a test already had to clear one by hand. `Engine`, `RuleContext`, `Labeler` and the adapters should take one snapshot object that owns the status, the listing and the plugin roots. The agent listed the call sites (`labels.py` `ProviderRunner.open`, `rules/entities.py` `check_files`, coverage `evaluate`, `TurboInputs`, `native/parity.py`, `presets_py/posthog/legacy_ledger.py`).

**Behavior changes to name when a repo adopts this version:**
- a clean `check` reads HEAD's blobs, so ignored files, empty directories and skip-worktree or assume-unchanged edits never change a verdict;
- a dirty `check` reads the index for files the status does not report;
- in a working-tree run, turbo's `$TURBO_DEFAULT$` counts an untracked file (as turbo does);
- every stored key misses once, because the input digest now holds the mode;
- `HOGLINT_MAX_RUNS` is gone.

**Test tooling, the architect's to fix after N1:** `compare_dumps` loads two 0.9 GB dumps whole (5 to 7 GB peak); the generated-config gate runs with the collector off (10.7 GB at 300 configs). Fix: compare row by row; leave the collector on.

## Phase N1 landed (Nov 10)

Commits cd213751e71 (the edge's own audience apart from the export cache), 2a5d5a4d35d (edge and entrypoint rules judged per edge in the link), f32ee8cd5a5 (selection for the rules that judge in Python), 2ea9c2afc11 (reach through the evaluator), 2d47ede5853 (grouping and the read audit deleted), 2cf8120d77e (the permanent per-edge test), a1b8fa237b5 (a set compared as text), b10fc74cf04 (the Python label pass is skipped when no label is set two ways). The evaluator is `src/link/rules/` (4,093 lines); the binding has `Rules(link, plan)`, `judge`, `select`, `pairs`, `reach`, `passing`, `conflicted`, `column`.

**The result: exact, and not faster.** The architect's pairs against 2e9fadc662d: the staged check takes 5.1 to 5.5 s CPU on one thread on both sides, the rules phase 2.0 to 2.4 s on both sides; on four threads the rules phase is 0.25 s wall shorter; peak 1.40 GB on both. The architect had expected 0.7 s less on one thread. The figures and the profile are in `docs/research/native-engine-design.md` ("N1 as built").

**Decided by the architect:**
- **N1 stays as built.** It is the base N2 needs, it deletes the grouping code with its class of bugs, and it costs no time.
- **A phase is cut where Python objects end.** N1 moved the judging and handed rows back to Python code that makes a `Finding`, an `Edge` and a label per row, so the cost moved and did not leave. Every later phase names the Python consumer it removes, and its gate is the whole phase's time in same-minute pairs.
- **A checkpoint in the first third of each phase.** N1's time gate was only testable at the end. N2 starts with the three cheapest steps that should show a gain (labels read from the evaluator's columns, lazy `Edge` for a finding's witness, signature in Rust) and stops for a new plan if they give less than 0.45 s.
- **The N-plan's figures are the far end, not a forecast.** Two of the architect's estimates missed in the same direction. From here only measured pairs count as results; anything else is labeled "estimate".
- **N2's order is by Python seconds left in the rules phase** (one thread, 2.11 s): signature 0.35, layout 0.30 (0.22 of it the label pass), export-shape 0.27, public-surface 0.21, coverage 0.18, use-shape 0.13, cycle 0.09, levels 0.07; `Edge` objects 0.29 across them.

**Open on N1 until the architect's gates and the adversarial review are in.** One thing the review must answer: the design makes a wrong `clean` from the evaluator silent, because only rows that are not clean are judged again by Python. The proofs cover the preset and 900 generated configs; nothing checks a clean row at run time.

**Named by the agent, accepted:** a set compared as text renders sorted, where Python's own answer depends on the hash seed. Unsupported and documented: a declared field named `context` or `exported` read through an endpoint, and non-string items in a set-typed assignment. The review checks that each fails loudly.

**D42. A shadow run of the Python engine guards the native one after cutover (the architect).**
- The native evaluator's `clean` is never judged again at run time: only rows that are not clean go back to the rule's Python code. A wrong `clean` is a missed finding that nothing reports.
- The proofs (every preset rule at snapshot scale, generated configs, the permanent per-edge test) run in Hoglint's own CI. They cover the tool, not the day a consuming repo's config change reaches a corner no test holds.
- So a consuming repo runs one scheduled shadow job: both engines over the repo, the ten tables compared, a difference fails the job. The Python engine needs about 35 s for it. This is D7's "small independent oracle" in its first form; a golden corpus replaces it when the Python engine leaves the wheel.
- It sits next to the D14 summary step.

## After the code review and the adversarial review of N1 (Nov 10)

**The architect's gates passed at b10fc74cf04:** parity in both engines and zero delta against 2e9fadc662d, lint, crossings 968 + 2, tach 0/0, 387 pytest and 19 cargo tests, determinism 5, and the review-4a repros on the native engine.

**Code review (over 2e9fadc662d..b10fc74cf04, by reading; ten claims).** The architect checked each before use:
- Reproduced: a directory that is no git repo crashes with a traceback and exit 1; a repo root below the git top-level misses a working-tree edit and then fails in the extractor; a rename git reports in the working tree (old file deleted, new one added with `git add -N`) leaves the old path in the listing, so both engines report a finding in a deleted file.
- Read and agreed: an unexpected exception exits 1, the code for violations; `read_blobs` fails without a path on a blob git lacks; `PackRoot` copies policy files from disk, around the listing; `Runner.run` and `Runner.evaluate` build the files root from two forms of the config.
- Rejected: an empty context set is never read (`contexts_of` falls back to the default contexts); a dangling source link is covered by 4b's test in both engines; an undeclared existence question failing the run is the design.
- Fixed as phase 4c, which also makes `compare_dumps` read a table at a time.

**Adversarial review of N1 (the architect reran r1, r2, r3 and r6).** Five defects:
1. HIGH, missed findings: a cycle rule whose group field a provider sets finds nothing natively (Python: 2 violations; native: 0, exit 0). `RulePlan.names_read` did not count the group as a read, so the plan sent no column and Rust read it as empty.
2. MEDIUM, a lost config error: a label set two ways passes natively in two shapes (a symbol field set by two module assignments; two `symbols = {}` assignments on a name outside the graph's symbols).
3. MEDIUM, a silent missed finding: a set field with non-string items.
4. LOW: limits only the native engine has (65 exports, a selector 130 levels deep) end as a traceback with exit 1.
5. LOW, a missed finding: a wildcard `within` over a non-ASCII directory.

No exposure in the PostHog preset: the ten tables are equal. What held: endpoint reads, every operator, reach and cycle order, kinds, globs, the engine id, and the loud direction (a Rust violation Python judges clean stops the run).

**D43. A column the plan lacks is an error, never an empty answer.**
- Defect 1 is the read audit's bug class in a new place: Python guesses which fields a rule reads, and a wrong guess is silent. The guess stays as a way to send less, but Rust refuses a rule that reads a provider-set field with no column, whatever part of the rule reads it.
- Provider columns are tied to module names (the names or their digest travel with the plan), not to a position and a count.

**D44. The evaluator reports every label conflict it meets, as data; Python words it.**
- The last N1 commit let Rust decide alone that a config has no conflict, to skip the Python label pass. Its `conflicted()` knew one of the shapes Python knows.
- Rust hands over the entity and field of every conflict, those met during the rules included. Python raises the same `ConfigError` as before.
- A differential test over generated conflicting configs holds the two engines to the same answer. N2's first step (labels read from the evaluator) rests on this.

**D45. Config values have one type and one text form.**
- A set field holds strings: any other item is a config error at load, in both engines.
- A set read as text has the identity form, items sorted and joined by commas, in both engines. Python's `str(frozenset)` follows the hash seed, so the oracle itself had no single answer there.

**D46. A limit belongs to the config, not to one engine.**
- More than 64 exports and a selector nested too deep are config errors at load in both engines. An error the evaluator still returns is an `EngineError` (exit 2).
- `within` takes named captures only. Both engines cut the path at the length of the pattern text, which is right only for a named capture; a bare wildcard is a config error at load.
- With 4c's rule that an unexpected exception exits 2, exit 1 means violations and nothing else.

**The generator makes configs, not only rules.** The 900 generated configs varied `[rules]` over one fixed preset, so every defect above sat outside its reach. The permanent test and the snapshot gate now also vary provider fields, roles, symbol assignments, non-ASCII paths and repeating pack edges.

Built as phase N1b, in parallel with 4c on disjoint paths. N2 starts from the verified result of both.

**D47 (direction, the architect's, Nov 10; the question: does PyO3 stay?). PyO3 is the port's seam, not part of the end state.**
- One Python process drives a check and reaches all Rust through one PyO3 extension module (`hoglint_native`: facts, link, and since N1 the rule evaluator). That stays through N2 and N3: each phase moves work behind the same seam.
- At N4 a Rust binary becomes the driver, shipped in the wheel as ruff ships. The default path then loads no Python and no PyO3.
- A Python plugin, when a config names one, runs in a child process. The plugin contract already fits a process boundary: a plugin gets a directory holding only its declared inputs (D33) and returns data (label columns, answers, edges). The binary does not link libpython.
- Gap this exposed in the phase list: PostHog's own run uses Python plugins that are not packs (the CODEOWNERS and owners-file providers, the turbo adapter). They become built-ins in N4, or PostHog's run still starts Python.
- Not built and not measured. The child-process protocol is a design to write when N4 starts.

**D47 revised (Nov 10; the question: why would a Python shell over PyO3 be bad?). It is not bad. The end state is a thin Python shell over PyO3; the binary is dropped as a goal.**
- Measured on the export of b10fc74cf04 (the architect's own script, three runs): interpreter start 0.01 s; importing the CLI, the plugins and the native module 0.06 to 0.10 s. A binary would save about a tenth of a second.
- tach, one of the tools Hoglint replaces, has this shape: a Python CLI over an abi3 PyO3 extension. It checks the PostHog monorepo in 2.3 s wall on all cores.
- What costs time and memory is not that Python is present. It is Python work per row (per edge, per finding, per module: 3.0 of the 5.3 s of that time, in the rule types not yet ported and in the packs) and Python copies of the data (the 1.4 GB). Those go; the shell stays.
- So: N2 (remaining rule types), N3 (packs) and N5 (mapped fact store, parallel link) stay. **N4 as "one binary" is dropped.** What stays of it is only what measurement shows per-row or slow in the ledger, summary and compare code. The CODEOWNERS, owners-file and turbo readers stay Python plugins; no child-process protocol is needed.
- The rule for the seam: Python hands Rust the config and the snapshot once, and gets back rows it prints. A call per row, or a row turned into an object nobody prints, is the defect to look for.
- The first version of D47 above (a binary, plugins in a child process) is withdrawn. It was the architect's preference stated as a requirement.

**The PostHog pack's home: back to D15 as agreed on Nov 9 (Nov 10).**
- D15 already says the PostHog pack stays in the monorepo as a plugin on Hoglint's pack API. That morning's native design (D39, section 4 of `docs/research/native-engine-design.md`) wanted it inside the Hoglint repo as a built-in Rust module. D47 revised removes the reason: Python stays as the shell and plugins stay Python.
- Decided (owner, Nov 10): `drives`, the only producer left, stays in the monorepo as a plugin, and Hoglint moves to its own repo. So D15 stands as written, and the open call is withdrawn.
- No Rust is split. All Rust (facts, link, rule evaluator, the PyO3 module) lives in the Hoglint repo and ships as one abi3 wheel; the monorepo pins it and holds config, ledger, exceptions and Python plugins.
- The architect's proposals on top, not yet decided by measurement: facade facts become a generic analysis in the Hoglint repo, switched on by config (any repo with a public-API convention can use it); the pack's name lists and paths (`DISPATCH_CALLS`, the dispatcher and enum paths, decorator names) move into the `[posthog]` config section; `drives` (1,044 lines, specific to PostHog's query-runner dispatch) stays the one PostHog pack producer, in Python, in the monorepo. The N3 spike shows how the pack's 0.6 s splits between drives and facade facts.
- Consequence for D31: the pack API has one user outside the Hoglint repo (the monorepo's plugin), so it needs to be stable enough for a version pin, not published as a public surface.

## From the simplification reviews of the commit path (Nov 10)

Four read-only reviewers read the commit path. Decided by the architect:
- Lands now, byte-identical: the working-tree status and its listing become one cached thing and the unused background start goes; one reader of the index, one `git` helper, one list of source suffixes; `TreeFiles` takes a listing; the parity dump builds its files root as `check` does; side inputs read in one git call; one atomic write; `--changed` reads paths NUL-separated (a fix).
- **A hole in the architect's own gate:** `Dump.check` in `native/parity.py` built the engine on a bare repo root, so every parity run so far went around the plugin directories of declared inputs (D33), the guard on existence questions and turbo's working-tree path. The repros covered those paths; the ten tables did not. Fixed in the simplification pass.
- **The snapshot object, refined** (after N2): three registries hold run state, not four (`TreeListing.taken` is a memo of immutable trees and stays). The object deletes `PluginRoots`, `TreeRoot`, `PLUGIN_ROOTS`, `run_root`, `Run.files_root`, the unregistered-root fallback, turbo's own git calls, `GraphSource.listing_of` and both snapshot selectors.
- **`tach_map --index` goes through `write_tree`** when the snapshot object lands, so `StagedIndex` and `BlobSnapshot.index` go: a third reader of the index that ignores D41. Its output changes only for symlinks and conflicts.
- Left alone: the scratch-directory helper, the four reactions to an unreadable file (their texts are output), the policy directory.

The log continues with D48 in `docs/history/decision-log-3.md`.
