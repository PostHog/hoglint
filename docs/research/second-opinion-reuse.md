# Second opinion: how a check can stop redoing work

> A second opinion from another model on reuse between checks, Nov 11, 2026 (night of Nov 10 to 11): which architectures avoid whole-program work after a small change, how a stored answer is shown to be still right, and where a daemon, salsa, a database and git-hook prewarming fit. With the question, the architect's view written before it, and what the decisions took.
> Current as of Nov 11, 2026. The decisions it fed are D59 and D61 in `docs/history/decision-log-3.md`; the spikes are in `docs/research/spike-n7.md`, `spike-n8.md` and `spike-n9.md`, and the staged plan in `docs/research/incremental-plan.md`.

## The question

By Nov 10 the native engine (D39) recomputed link, packs and rules for the whole repo on every check whose input set was new. The questions (Nov 10): can the pre-commit check get near 1 s; does salsa (as ty uses it) or a stronger database help; how much memory may a linter use?
The N7 spike had just measured how far the outputs of real commits spread.

The second opinion was asked, in at most 130 lines:

1. Which architectures would you seriously consider so that a check after a small change does not redo whole-program work? For each: what it reuses, where its state lives, what it costs to build and to run.
2. For your pick: what is the unit of reuse, what is its key, and how exactly is "this stored answer is still right" established? Be concrete about cross-file dependencies: re-export chains, names looked up and not found, class hierarchies, labels of the far end of an edge, whole-graph rules, producers that ask "which files mention X".
3. How does that design give a silently wrong answer, and how would you test for it before building? What cheap experiment on real commits would tell you the design is sound or not?
4. Where do a long-lived daemon, salsa, an embedded database, and a background run triggered by git hooks fit, if at all? What would each solve, and what not?
5. What is the realistic floor for a one-file change under your pick, given the numbers, and what dominates it?
6. What would make you advise against doing any of this?

It was asked for its own judgment, and to say if something in the description looked wrong or underspecified.

## What it was given

- **The system.** Hoglint is a boundary linter for a large Python monorepo (about 29,000 Python files, 267 MB of source): a Python CLI over a Rust engine (PyO3, one abi3 wheel). A fixed export of the code: the Rust engine (fact extraction, the linker, the rule evaluator, pack producers), the PyO3 layer, the Python shell (CLI, rules, native bridge, Django and PostHog packs, ledger).
- **One check:**
  1. list the tree (index blobs or a tree) and digest the declared inputs;
  2. facts per file, extracted by a Rust parser and cached in SQLite by content; a warm run parses nothing, but reads and decodes all files' cached facts;
  3. the link: one whole-program pass that interns names, builds scopes and an index, resolves imports and references across files (following re-export chains to the origin of a name), and emits typed edges; about 1.48 million edges, 291,000 symbols, 29,000 modules;
  4. packs: producers that add framework edges and symbol facts; some in Rust (Django ORM uses), some in Python, reading per-file records plus link queries such as "which files mention this name", "subclasses of X", "rows named Y" (Django models, signals, installed apps; a PostHog pack that derives which tests drive which product code, and typed facts about each product's facade functions);
  5. labels and rules: labels from config (path globs, owners files, kinds by base class or decorator); declarative selectors over edges with the labels of both ends, plus whole-graph rules (reach, package cycles, coverage) and levels; about 41,000 findings, nearly all accepted in a checked-in ledger, with zero to a few new ones per check;
  6. a summary stored under (engine id, config, digest of the input set); a later check of the same input set reads it and does no analysis.
- **Measured, one thread, warm fact cache:** a whole check about 3.75 s: analysis (read, decode, intern, link) 1.05 s; Django pack 0.44 s; PostHog pack 0.58 s; labels and rules 1.05 s; the rest 0.5 s. Peak memory about 1.2 GB. Four threads: about 3.4 s wall. A check that hits its stored summary: 0.55 s (tree listing 0.10, store read 0.10, output 0.08, policy files 0.06, start-up 0.05, `git write-tree` 0.05). Reading all cached facts about 0.1 s, decode and intern about 0.2 s, the link proper about 0.33 s. Only per-file facts and whole-run summaries are cached.
- **The N7 measurement over 33 real commits** (30 consecutive Python-touching commits plus 3 that edit modules with more than 2,500 importers), comparing the full graph and results of parent and commit tree:
  - in 32 of 33 commits, every edge, finding, incomplete item and label that differed was in a file the commit changed;
  - in the exception, a package was deleted and a re-exported name got a new origin; edges changed in 5 files one import hop away and 1 file two hops away; nothing changed in a file with no import path to a changed file;
  - editing a module with 2,500+ importers changed no importer's edges when no origin moved;
  - whole-graph rule results never moved; level block lists moved in 2;
  - the measurement shows where outputs moved, not what a step would have to read to know its output did not move.
- **Constraints:** the main use is a git pre-commit hook, a fresh process per commit on a laptop, with a wish near 1 s or below for a commit touching a few files; CI runs cold and is not latency-sensitive. Results must be exactly what a full recomputation gives; a cache problem may cost time, never a wrong answer or a failed run. Runs are deterministic and equal on macOS and Linux. Several checks can run at once. Developers switch branches (hundreds of files change at once). Part of the pack logic is Python on purpose and stays Python. A pure-Python reference engine must agree.

It got the facts and numbers and none of the architect's conclusions. It was told to read only the first 73 lines of the N7 report (later additions were the architect's).

## The architect's view, written first

Written on the night of Nov 10, before any second opinion.

**What N7 measured,** plus a limit the architect added: the spike shows where outputs moved, not whether a step can prove "unchanged" from what it can see locally; the sample is one day of mostly leaf edits; and a finding was classed by the nearest of its witness and subject files, so a finding in an unchanged importer whose destination changed counted as "changed file". A rule's answer for an edge depends on the labels of both ends.

**The reading.** Outputs are local, so reuse can pay. Whether it can be made exact depends on a second question: can a file's stored answer name everything it read from other files, so that "none of it changed" is checkable without recomputing?

**The model that fits Hoglint's identity design (D3, D4, D5, D28): stored answers with a record of what they read.** No daemon, nothing mutable, everything keyed by content.

- Every file has an *interface*: what other files' answers can read from it (its definitions and their kinds, bases, decorators and signatures, its imports that serve re-exports, the facts packs attach to its symbols, its module's labels). The interface has a digest.
- Every stored answer for a file (its edges per producer, its symbol facts, its rule rows) carries a *trace*: the files it read from and the interface digest each had.
- A check lists the tree, builds the table of interface digests, and reuses a file's answer when the file's content and every digest in its trace are the same. It recomputes the rest. A change that leaves a file's interface alone reaches nobody: that is the 2,500-importer case.
- Whole-graph rules read a small relation folded from the per-file answers; their result is reused when that relation's digest is the same.
- The tree's summary is the base tree's stored summary with the recomputed files' rows replaced.
- Cold (CI, first run) is the full run.

**The solutions to compare:**

- **S0, stay:** whole-program recomputation, 2.7 to 3.3 s after the current phases (estimate); floor 0.55 s only for an unchanged input set.
- **S1, prewarm:** no engine change. A git `post-index-change` hook starts the normal check in the background when the index changes, so the pre-commit check hits the stored summary. Hides latency when staging and committing are seconds apart; nothing for `commit -a`; a full check per index change.
- **S2, stored answers with traces:** the model above. Floor plus work for the changed files. The cost is the boundary work: the link's per-file step and each pack producer must be a function of a file plus traced reads. Deliverable in stages (core edges, then rules, then packs).
- **S3, daemon:** the graph stays in memory and a change is applied to it. It needs the same invalidation logic as S2 plus a process lifecycle, and holds about 1 GB while idle. Salsa fits here and only here. A daemon that recomputes everything saves little: the time is computation, not start-up. So S3 is a step after S2, not a way around it.

**To test that night, in scratch:** S2's premise on N7's 33 commits (predict which files' answers are invalid; every file whose answer really changed must be in the prediction; the size of the prediction is the recompute cost); S1 as a prototype on a staging repo; and this second opinion.

## The second opinion

What follows is the second opinion's answer, cleaned.

It would choose **persistent, dependency-validated reuse of producer outputs**, with conservative whole-program fallbacks, and test its dependency model first in a disposable tracing experiment.

The evidence supports that investment: most outputs stay local, and stable name origins give useful stopping points for invalidation. It does **not** justify "recompute changed files and their importers". Dependencies include unsuccessful lookups, registry membership, labels, and queries over sets of files.

It read only the report's first 73 lines and sampled the exported code.

### 1. Architectures worth considering

| Architecture | What it reuses; where state lives | Build and runtime cost |
|---|---|---|
| Persistent producer and query cache (its choice) | file and producer outputs, semantic lookup answers, labels, rule results; local disk with a small in-process working set | Substantial dependency-interface work. Small edits load and recompute affected partitions; global changes can fall back. |
| Coarse partition cache with conservative invalidation | module and package outputs and exported semantic summaries; disk | A simpler first implementation. Broad dependencies invalidate whole registries or packages, so gains are less predictable. A useful stepping stone. |
| Long-lived incremental query engine, possibly salsa | queries, dependency graph, indexes, interned values; daemon RAM, optionally persisted | Substantial query refactoring plus daemon lifecycle work. Avoids repeated loading, but holds memory and needs a cold fallback. |
| Relational incremental view maintenance | facts, edges, labels and derived relations; an embedded database plus delta-processing code | Attractive for joins and indexes, expensive for recursive resolution, deletions, ordered results and arbitrary Python producers. Not a place to start. |

A remote database dependency is wrong for a laptop pre-commit hook. A local database is reasonable, but storage and dependency correctness are separate decisions. The coarse cache and the preferred design can share infrastructure: begin with large, safe dependency boundaries and refine only measured bottlenecks.

### 2. The design it would pick

The main output unit is **one producer's result for one file**, including edges, symbol facts, incomplete items and diagnostics. Smaller semantic queries support those units; truly global producers stay global units.

Useful query units: `resolve(module, name)`, `signature(entity)`, `bases(class)`, `labels(entity)`, and indexed candidate sets. Rule outputs can be partitioned by rule and source file where their semantics allow.

Separate **logical identity** from **cache validity**:

- logical key: engine, schema and producer version, query kind, stable arguments and relevant configuration;
- stored record: canonical answer, answer fingerprint, and the exact queries and input values read to produce it;
- snapshot manifest: an immutable mapping of declared input paths to content identities, including additions and deletions;
- a snapshot-specific root points to the reusable records that check needs.

Do not put the whole tree digest into every reusable query key: that prevents cross-tree reuse. Do put it into the final summary key.

An old answer is valid only when its implementation and configuration match and **every recorded dependency has the same current answer**. Validate dependencies recursively, recomputing changed ones. If a dependency recomputes to the same answer, propagation stops.

Do not recursively visit the whole dependency graph on every launch. Keep reverse dependencies and snapshot-relative validation metadata: seed changes from the input-manifest diff, visit potentially affected queries, and keep untouched records through the complete dependency relation.

This requires deterministic computations whose inputs all pass through tracked interfaces. An untracked read needs a conservative dependency or disables reuse.

| Case | Required dependency |
|---|---|
| Re-export chain | Resolution reads bindings and module-existence decisions at every step. Consumers depend on the resolution result they use, including provenance when observable. |
| Missing name or module | Record the lookup and its "absent" answer. Adding a previously missing name must invalidate it, even without an old import edge. |
| Wildcard imports and module/package ambiguity | Track export-set membership, `__all__`, module selection and search precedence. |
| Classes | Track bases, their resolution, and MRO and member queries. A subclasses query also depends on membership, including newly introduced subclasses. |
| Django string references | Track registry lookup by app and model label, including missing or ambiguous answers, plus relevant installed-app configuration. |
| Far-end labels | Rule partitions read labels and symbol facts for both endpoints. A destination-label change can invalidate rules on unchanged incoming edges. |
| "Which files mention X?" | Depend on an indexed posting set for X, including empty sets. Changed files update both removed and added postings before consumers validate. |
| "Rows named Y" | Track the returned rows, or track membership separately and then each selected file's relevant rows. Stable membership alone is not enough. |
| Whole-graph rules | Depend on their complete semantic projection: applicable adjacency, node membership, labels, roots, build inputs and witness data. |
| Levels and ledger | Derive from current findings, acceptance policy and grouping configuration. Ledger-only changes must not require relinking. |

For whole-graph rules, at first **recompute when the relevant projection changes**: unchanged findings do not prove an unchanged input graph. A maintained package-adjacency projection can make cycle checks cheap; keep edge counts so removing one of several contributing edges does not remove the relation.

Resolution cycles need explicit treatment. Recompute a dependency component with the full engine's cycle semantics; do not assume generic fixed-point evaluation keeps those semantics.

Keep Python packs, but give them a tracked reader with immutable inputs and explicit outputs. A producer that enumerates all files depends on that enumeration. To avoid broad invalidation, split candidate discovery from per-file processing.

The pack query interface of the day was a starting point, not a dependency boundary: sampled queries scanned all scopes, and Django built shared registries. Those operations must become maintained indexes, independently cached queries, or conservative global dependencies.

Persist stable semantic identities, never interner numbers or graph positions. The edge index used positions and discarded indexes when edges changed; persisting that representation directly would be unsafe.

### 3. Silent failures and the cheapest useful experiment

The biggest risks are missing dependencies, especially **absence and set membership**. Others: stale removed outputs, mixed snapshots, incomplete producer-version keys, incorrectly compared answers, and failure or incomplete states treated as successful empty results.

Order is another risk. The report compared unordered digests, while the code exposes graph and fact ordering. Define the observable equality contract: findings, witnesses, locations, status, completeness, ordering and exit behavior. Keep multiplicity and any meaningful order.

Before building persistent execution, run a **dependency-certificate experiment**:

1. Instrument lookup and pack-reader boundaries during full parent-tree runs; record dependencies per proposed output unit.
2. Fully compute the child tree and evaluate those recorded dependencies against it.
3. Predict which parent outputs would be reused, then compare every predicted reusable output with the child's full output.
4. Count false reuse, candidate invalidations, dependency-validation work, bytes read and recomputation cost.

This uses full computations as an oracle: it tests the reuse contract without first building a scheduler or database.

Replay the 33 commits, then branch switches and structural edits. Add targeted mutations for missing-name creation, re-export deletion, wildcard exports, base-class changes, model-label strings, new mention candidates, destination-only label changes, ownership and config changes, and ledger changes. Include signature and body changes that keep an entity's origin: origin stability protects only consumers that read identity alone.

Any changed answer predicted reusable falsifies the dependency model. Zero mismatches is encouraging, not proof; the guarantee comes from complete tracked access.

Later, test Rust incremental against Rust full, and Rust full against the Python reference. Exercise deletions, A to B to A histories, shuffled execution, concurrent checks, interrupted writes, damaged caches, and macOS against Linux.

### 4. Daemon, salsa, database, background hooks

A **daemon** removes repeated hydration and can share indexes across checks. It does not supply correct invalidation. Start-up here is only about 0.05 s; saving that alone cannot justify a resident gigabyte. Consider it later if measured persistent-cache loading dominates, with idle eviction and an independent foreground fallback.

**Salsa** supplies tracked queries and propagation that stops when recomputed answers stay equal, which matches the semantics wanted. Its documentation also shows persistence support, so it is not necessarily memory-only ([algorithm](https://salsa-rs.github.io/salsa/reference/algorithm.html), [persistence options](https://docs.rs/salsa/latest/salsa/attr.tracked.html)). But salsa cannot discover reads hidden inside arbitrary Python code, choose the query boundaries, or decide the cycle semantics. Prototype persistence, fresh-process loading, memory and Python integration before adopting it. It favored a narrow domain-specific layer first, and named an expanding generic scheduler as a reason to reconsider salsa.

An **embedded database** supplies indexed storage and atomic publication. SQLite is a sensible start given the existing cache. Use immutable artifacts, short publication transactions, coherent snapshot roots and bounded retention. Concurrent processes can duplicate computation safely. Cache read or write errors, contention, incompatible versions or detected corruption should abandon caching and run the full analysis. Never turn a cache failure into a clean result or a failed check; real analysis failures keep their normal meaning.

A **background hook run** can fill exact-summary entries after commits, checkouts or merges. It is opportunistic prewarming: a previous tree's result cannot approve a new staged tree. Pre-commit must identify its exact snapshot and validate any reused result on its own. Background work also needs cancellation and coalescing to avoid wasting laptop resources.

### 5. Realistic latency and memory

With the existing shell and output path, **about 0.55 s is the measured baseline**, not a universal lower bound. A changed-tree check adds manifest comparison, validation, changed-file analysis, affected packs and rules, and publication.

Target **0.7 to 1.1 s for ordinary one-file edits**, conditional on avoiding whole-graph hydration and broad producer invalidation. A planning range, not a result. Cold caches, structural edits and branch switches keep slower paths.

There is only about 0.45 s of headroom for a one-second target. Leaving the roughly one second of Python packs, or the one second of labels and rules, unchanged defeats it. Optimizing the 0.33 s linker alone cannot deliver the goal.

Avoid loading 1.48 million edges merely to reuse them. Persist indexes and output partitions, load affected data, and keep aggregate results separately. Otherwise caching may save CPU while keeping the memory peak, or raise it through duplicate representations.

The reported 1.2 GB deserves its own allocation profile. No supplied measurement shows which structures dominate, so it would not promise a memory figure.

### 6. When it would advise against it

Stop if the certificate experiment shows broad dependencies force most packs and rules to run, validation costs approach recomputation, or the Python producers cannot be confined to explicit inputs without an unacceptable maintenance burden. Also stop if hook frequency and user frustration do not justify maintaining a persistent incremental engine to save roughly three seconds.

Two details need clarification: the quoted analysis substeps sum to about 0.63 s against 1.05 s reported for analysis; and the Django code references a migrations summary cache, so "only two caches" may omit a narrower one. Neither overturns the recommendation, but both matter to the budget.

Its next investment: the dependency-certificate experiment and memory attribution, to establish whether small output changes turn into cheap, provably justified reuse before the tool takes on a much more complicated execution model.

## What was taken

From the decision log (`docs/history/decision-log-3.md`, Nov 10 to 11). The architect recorded that the second opinion, given none of the architect's conclusions, chose the same model as S2 and named the same next step: its dependency-certificate experiment was the N7 part 2 run already in progress.

**Taken from what it added:**

1. The largest silent-failure risks are **absence and set membership**: a name looked up and not found, a new subclass, a new file that mentions a searched name, a registry that gains a member. Each needs its own recorded dependency. Pack producers should split candidate discovery from per-file work.
2. **Unchanged findings do not prove an unchanged input** for whole-graph rules: recompute when the relation they read changes, and keep edge counts in that relation.
3. **Order and multiplicity are part of the contract.** N7's digests had no order; the ten parity tables do.
4. **Persist semantic identities, never interner numbers or graph positions.**
5. Its targeted mutation list went to the N7 agent as part 3 (eleven mutations in the end).
6. **Both corrections were right:** the analysis substeps sum to 0.63 s of 1.05 s (the rest is the Python snapshot and discovery, 0.42 s), and the Django migrations pack has a narrower cache of its own.

**On salsa,** an earlier statement was wrong: persistence is a documented, feature-gated part of salsa's API (the `persist` option on tracked structs and functions, salsa 0.28.5), not only a prototype. Whether it loads a database of Hoglint's size in a fresh process fast enough, and lets inputs change after a load, could not be read off the docs, so the N9 spike measured it on a reduced model of the link's name resolution over the real 28,794 files. A fresh process paid a whole-database load (0.41 s, more than the 0.27 s to compute that model from nothing), with no partial or lazy load; inside one live process a change cost 0.02 to 0.04 s (`docs/research/spike-n9.md`).

**The results of the experiments it proposed or confirmed** (`docs/research/spike-n7.md`, parts 2 and 3): with three widenings of the trace (the module an edge spells, rule rows kept under the files their identity names, aggregate rules reused by the digest of the relation they read), the prediction missed no file over the 33 commits and 11 mutations. At name grain the median recompute was 6 files (4 really changed). The packs were the blocker: a pack step builds its whole output in one batch, so no read could be tied to an item; drives would rerun whole in 20 of 33 commits.

**D59 (the architect, Nov 10 to 11):**

- Prewarm by git hook (S1): not pursued. The N8 spike showed it helps one flow of five and storms without a guard (`docs/research/spike-n8.md`).
- Salsa: not the vehicle for the hook, because its persistence loads the whole database per process. It stays the right tool if Hoglint ever runs as a long-lived process (an editor server or a watch mode).
- Stored answers with traces (S2) is sound on everything tested and is the only route to a check near 1 s in a fresh process. Its rules: name grain, with not-found lookups and spelled modules in the trace; a store read by key from the changed files outward, never loaded whole (the N9 lesson); rule rows under site and identity files; aggregate rules by relation digest; provider-file labels with their own trace; an oracle in CI that the incremental result of a commit replay equals the full result.
- A declared fast mode that links and judges only the changed files was measured and rejected: it saw 11 of 88 findings that 33 real commits introduced, because most new findings come from the facade and export rules that read a pack producer.

**D61 (the architect, Nov 11): the road is exact reuse on Hoglint's own store, all eight stages; not salsa.** The fork with salsa is narrow and late: the first stages are needed on both roads, and they part only at where answers live between two commits (on disk read by key, or in a live process). The lesson from ty for a hook is "compute only what the change needs", from nothing, in each process; Hoglint needs more than ty for it, because a finding also depends on who imports the changed file and on pack facts.

**Not taken:** the second opinion's daemon-later option stayed later; its stop condition on hook frequency was answered by the design target of a hook that runs on every commit in 2 to 3 s (D60). D69 later found that one stage of the plan (a forward check of rule rows over the full link) cost three times the judging it would skip, which confirms its warning that validation can approach recomputation unless unchanged rows are not read at all.
