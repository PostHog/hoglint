# Second opinion: the engine after phase 3d

> A second opinion from another model on Hoglint's engine after phase 3d, Nov 10, 2026: the commit path, memory, the rules phase, and the split between Rust and Python. With the question, the architect's view written before it, and what the decisions took.
> Partly replaced: its findings became D33, D34, D36 (later withdrawn) and amendments to D6, D7 and D32 in `docs/history/decision-log-2.md`; the same day D39 moved everything a check runs into Rust. The engine as built is in `docs/design/05-engine.md`.

## The question

The question (Nov 10): a fresh look at what the agents had built and at the decision log (D1 to D32).
The second opinion was asked for an independent design review, told that nobody had said what the answer should be, and invited to disagree with the decision log. The questions:

1. **The commit path.** Walk through what one developer's machine does, and what it costs, over a realistic day: pull, edit, commit, commit again, switch branch, commit, with a normal working tree. Where does the design hold, where not, and what would you change? (Pointed at `Commands.check`, `Baselines`, `RunMemo`, `TreeSummaries`, `Snapshots` in `cli.py`, and the ledger's status, envelope and store modules.)
2. **Memory.** Is 1.4 GB per check a sound result for this job? Where would you cut, and to what figure? A design, not a list of tweaks.
3. **The rules phase.** Judge the approach (the native rule context, rules base, reads, audit, selectors, labels, the Rust grouping and its binding) on correctness risk, cost and how it will age. If you would build it another way, say how, and what it would cost and save.
4. **The split between Rust and Python** (D1, D2, D22, D31). Is the line in the right place?
5. **Anything else** in D1 to D32 you would overturn, and anything the gates do not measure but should.

Output: at most 120 lines of Markdown; findings ranked by weight with evidence, failure or cost, and proposed change; verified separated from inferred; which proposals need a spike before any build, and what the spike must measure.

## What it was given

- What Hoglint is: a boundary linter for a large Python monorepo (about 29,000 Python files, 291,000 symbols, 1.47 million edges). It resolves names across the whole repo, builds an edge graph, lets framework packs add and refine edges, and judges about 30 rules written in TOML. A committed ledger holds accepted findings. It is meant to run as a pre-commit hook (`hoglint check --index`), by developers and by many coding agents in many worktrees on one machine, and in CI. Rust holds extraction and the link (PyO3 binding); Python holds packs, labels, rules, the ledger and the CLI.
- The code at commit `dc2271eba47`, the preset, the root config, and the decision log (D1 to D32).
- The gates: pre-commit `check --index` at most 4 s wall and 1.5 GB peak; ten overlapping checks at most 15 GB in total; the Python reference engine and the native engine equal on ten output tables, with zero or named deltas per commit.
- Measurements at `dc2271eba47`, one thread: a staged check with a stored baseline, on a quiet machine, 4.83 to 4.90 s wall: analysis (snapshot, fact load, link) 1.05 s; Django pack 0.62 s, PostHog pack 0.56 s; labels and rules 2.0 s; the rest about 0.5 s. Peak RSS 1.40 to 1.46 GB. The existing pre-commit hook for Python files (ruff and `ty check`) takes about 2 to 3 s.
- Raw data: the rules phase by rule and function, and memory by part.

It was told not to read the architect's view, so that the review stayed independent.

## The architect's view, written first

Written before the second opinion. Every number measured at `dc2271eba47` unless stated. The machine was about 1.4 times slower that day than in the phase 3d measurements (HEAD 7.0 s measured under load; the older export `33b1723e535` 7.2 s in the same minute), so there was no regression, and timing needed a calibration run.

**F1. A commit with no stored baseline pays for two runs in one process.** Measured 25.0 s wall, 20.0 s CPU, peak 2.33 GB, against 8.3 s and 1.40 GB with a stored baseline in the same minute. `Baselines.prepare` evaluated HEAD's tree inline when the cache had no summary for it, and the allocator did not give the first run's memory back. Gate D26 measured only the hit. A miss happens after a pull, rebase, merge or checkout that moves HEAD; after a commit Hoglint did not run for; after a Hoglint, preset or Django upgrade; and after F2.

**F2. One dirty non-Python file anywhere stops a staged run from storing its summary.** One untracked note file gives "summary of tree ... not stored", and the next commit meets F1. The drift check counted every path not ending in `.py` or `.pyi` in the whole repo. Developers nearly always have such a file, so F1 is the common case.

**F3. A staged run reads its side inputs from the working tree.** D4 says every semantic input comes from the snapshot. The code read owners files, CODEOWNERS, turbo and package files, the provenance file and the file-existence checks of layout rules from the working tree. The drift guard only refused to store; the run still printed a verdict made from index code and working-tree side inputs. F2 exists because of F3.

**F4. The summary key holds the whole repo's tree.** 90 of the last 300 master commits touched no `.py`, `.toml` or `.yaml` file, and each one changed the key.

**F5. Memory: 1.4 GB per check, most of it private copies of things that never change.**

| Part | Size |
|---|---|
| Fact rows, decoded from JSON in SQLite on every run | 270 MB |
| Interner (every string, interned again on every run) | 151 MB |
| Scopes | 69 MB |
| Edges (1.47M) | 111 MB |
| Module tables, symbols, caches | 48 MB |
| Allocator slack after the link | about 150 MB |
| Python graph and build objects | 110 MB |
| Django pack | 140 MB |
| PostHog pack (drives +185 MB, facade facts +230 MB for a moment) | 300 MB at the peak |

After the pack phase, 0.42 GB was in use and 0.94 GB held. Ten concurrent checks with the memo off: about 13.5 GB. Nothing limited how many checks run at once.

**F6. Grouping is now the main correctness risk, and no longer the main cost.** Four layers of partial evaluation sit on top of each other. `FindingClasses` shares identity, owner and subjects per class again, though D27 said only the verdict is shared. About 12 of the link's methods are the grouping protocol, though D2 asked for coarse calls. Soundness was shown for the PostHog preset on one snapshot; rules are TOML users write, so it must hold for every config. Both holes so far were found by reading. Rules phase cost: grouping 0.84 s, `Edge` objects 0.44 s, conflicts 0.35 s, then ten primitives at 0.07 to 0.57 s each. The grouped rules judge 5.25M (edge, rule) pairs and keep 48k.

**F7. D1's reason for rules in Python does not hold for the evaluator.** Rules are TOML, the primitives are a closed set of ten, and no plugin adds a primitive. Selector evaluation, labels and identity projection are mechanics; packs are the real Python extension point.

**F8. The 4 s gate is not the developer's experience.** The existing Python hook chain is about 2 to 3 s. Hoglint on a hit: 4.8 s quiet, 9.5 s each when ten run at once; on a miss, three times that.

**F9. Smaller.** 25 native tests skip when the module is missing, and two more skip by file time. The fact cache's validity rests on nine hand-kept exclusion lines; crate boundaries could decide it instead. `Snapshots.index()` checks out every toml and yaml file (784) per staged run: 0.24 s.

**What the architect would change:**

- **A. Make the staged check a pure function of the index, and never compute a baseline inside a commit.** Providers and adapters declare the path patterns they read, and the run materializes exactly those from the snapshot. The key part `tree` becomes the digest of (path, blob oid) over the sources, the declared side inputs and the policy files. On a miss the check does not evaluate HEAD (choices for the owner: unattributed incompletes that do not block, with CI attributing; a background warm from `post-merge`, `post-checkout` and `post-rewrite`; both; or accepted incompletes in the ledger). A machine-wide limit on concurrent evaluations.
- **B. Memory: stop copying immutable facts into every process.** Facts in a position-independent binary layout, mapped and shared; measure the physical footprint, not peak RSS. Then cut the pack transients.
- **C. Rules.** Now: a metamorphic test, grouping off against grouping on, over many generated rule configs. To evaluate: per-edge evaluation in Rust over columns, with no group key, no read declarations and no audit; estimate about 0.3 s saved alone, about 0.9 s with labels and witnesses native. Not recommended: free-threaded Python or `fork` for packs and rules.
- **D. Considered and left.** Demand-driven or incremental evaluation (D5 still holds); fetching baselines from CI (a rebased branch tip is never a master commit).

Open for the owner: the memory target, the time target (4 s, or "no slower than the existing hook"), and what a commit does on a baseline miss.

## The second opinion

What follows is the second opinion's answer, cleaned. "Verified" means established by reading code; "measured" means supplied measurements; "inferred" means a consequence or estimate that needs validation. Paths are Hoglint modules.

It would not approve a pre-commit cutover yet. The engine had improved a lot, but the measured fast path missed the time gate, and ordinary baseline misses exposed costs the reported gates did not establish. It inspected HEAD `dc2271eba47`, the code, the decision log and the profiles. It did not read the architect's view, modify files or rerun benchmarks.

### 1. Highest weight: the summary key does not identify one immutable staged input

**Verified.** In `cli.py`, `Snapshots.index()` materialized policy through `checkout-index`; `PendingTree` separately ran `write-tree`; `BlobSnapshot.index()` later listed source blobs through `ls-files`. These operations did not share a captured tree. `WorktreeStatus` captured status once, rather than validating the bytes each side-input reader actually consumed.

**Failure.** If an editor, formatter or another process changes the index between these operations, policy, source blobs and summary tree can describe different states. A cached summary can then claim an input it never evaluated. The precise interleaving is inferred; the independent reads are verified. D4's "every semantic input comes from the snapshot" was not implemented consistently.

**Change.** Capture the index tree once. Read code, policy, ledger and declared semantic data files from that tree. Keep the render-only ownership lookup explicitly separate. Give plugins a snapshot reader, not an unrestricted repository path. Working-tree checks need a manifest of the bytes actually read, with drift preventing publication.

**Validation before rollout.** A deterministic interleaving test that changes the index between policy capture, tree capture and source listing; a result is either coherent or rejected, never stored under another tree.

### 2. Baseline misses can keep two graphs alive; "a separate runner frees it" is false

**Verified.** `Baselines.evaluate()` created a separate runner to free its graph before the current run. But `native/source.py::batch_mode()` disabled the cyclic GC. `EdgeRule.evaluate()` stored findings in `rule.run.findings`; a grouped finding's `Rejudged` detail kept the same rule. So `rule -> findings -> detail -> rule` kept the rule context and graph alive. `ScanSummary.from_run()` copied results without breaking this cycle.

**Failure.** Returning from the baseline helper does not reclaim that graph by reference counting. The current scan can overlap its memory with the baseline's retained graph. The extra RSS was unmeasured, but the ownership cycle was there. This undermines the single-check memory claim for a normal cache miss.

**Change.** Give evaluation an explicit disposal boundary after summary construction. Break the result and context ownership cycles, or collect unreachable cycles once between scans. Keep periodic GC off during hot loops if that still pays.

**Spike.** An absent-baseline staged check in one process; measure live allocations and RSS before the baseline, after disposal, and during the current scan. Compare explicit teardown with one boundary `gc.collect()`, including its CPU cost.

### 3. The realistic commit path is often two scans, not the advertised one

**Verified.** `Commands.check()` prepared the baseline before trying the current-tree memo. `Baselines.prepare()` read only cached incompletes when available; otherwise it evaluated HEAD under the current policy. `TreeSummaries.put()` refused storage when `side_input_drift()` found *any* relevant-status non-Python file, whether Hoglint reads it or not. `RunMemo.lookup()` applied the same broad guard.

| Event | What the machine does |
|---|---|
| Pull, edit Python, commit | If the pulled HEAD has no exact summary: scan HEAD, then scan the staged tree. |
| Edit again, commit again | One current scan, if the previous staged summary was stored and became HEAD unchanged. |
| Retry the identical staged tree | A memo lookup can avoid evaluation, subject to ledger, output and drift guards. |
| Switch to a previously evaluated branch, edit, commit | Usually one current scan if its HEAD key still matches. |
| Switch to an unseen branch, edit, commit | Baseline plus current scan again. |
| Keep an unrelated unstaged README or untracked text file | Current summaries are refused; successive commits can repeatedly need baseline scans. |

**Cost.** One warm scan is measured at 4.83 to 4.90 s. Two scans plausibly approach twice that work. Three fresh commits with a baseline hit only for the middle one mean five evaluations; persistent unrelated dirt can mean six.

**What holds.** Shared exact-key summaries, blob facts, sectioned baseline reads and staged-summary reuse are good choices. Ordinary unstaged Python edits do not invalidate a staged snapshot.

**Change.** Replace blanket status invalidation with captured, declared semantic inputs. Permit reuse across unrelated changes. Consider a semantic source-manifest identity beneath the git-tree alias, so unrelated tree changes do not force equivalent analysis.

**Decision.** Keep whole-repo evaluation for now, but reject D5's justification that it already costs "2 to 4 seconds". Revisit D14's decision on local baseline prefetch after measuring this miss path; prefetch should be optional and asynchronous, with an exact-key local fallback.

### 4. The grouping optimizer is becoming a second semantic engine

**Verified.** The contract spans `Reads.of/granted`, `granularity`, `Labeler.module_level`, `EndpointClasses`, `Grouper`, Rust endpoint and edge keys, and binding conversions. `ReadAudit` dynamically observes selected access points. `Rejudged.get()` checks only findings whose detail is requested; wrongly dropped clean groups never reach it.

`FindingClasses` also shares identity, owner, subjects and flags through another key derivation. That departs from D27's "only the verdict". Its keys at the time kept endpoint identity, so this was **an extension hazard, not a demonstrated wrong preset result**. The earlier `edge.label` hole was already fixed at this HEAD.

**Cost and aging.** The profile put 0.840 s in `Grouper.survivors`, mostly grouping, and 0.442 s in 6,049 `LinkEdges.get` calls (inclusive; not to be added blindly). Hundreds of thousands of candidates often produce zero or two survivors. New fields and primitive overrides enlarge the proof obligation across several representations.

**Change to spike.** Keep Python parsing, validation and policy construction; lower selectors into a small typed execution plan over native columns. Evaluate cheap predicates directly over candidate rows, keeping known, none and unknown semantics. Return verdict masks and handles; project finding metadata per surviving edge. Arbitrary Python primitives use an explicit, ungrouped fallback. This costs a native expression evaluator and exhaustive operator-semantics tests. It could replace much of the grouping and read-declaration machinery instead of adding another optimization beside it.

**Spike acceptance.** Implement public-surface plus one shape-sensitive rule. Compare grouping against direct native evaluation on current and high-cardinality inputs; measure total rules CPU, allocations, crossing bytes and all output tables. Seek roughly 0.7 to 1.0 s of rules savings: a target, not a forecast. D29's modest measured gain argues against another large rewrite without this experiment.

### 5. 1.4 GB is tolerable for an occasional audit, but not a sound steady-state target for this hook

**Measured.** The link alone reached 0.882 GB RSS. Retained components included 269.5 MB of fact rows, 150.6 MB of interner state, 69.4 MB of scopes and 110.8 MB of edges. After packs, 483,000 lists and 302,000 dictionaries stayed alive. Releasing records and facts reduced allocator bytes in use from 0.820 to 0.424 GB, but RSS only fell from 1.227 to 1.190 GB.

**Interpretation.** The issue is overlapping representations and allocation lifetimes, not an enormous final graph. Late release cannot undo an earlier peak, and allocator retention limits its RSS benefit.

**Design.** A phase-owned native analysis store: compact fact columns, stable numeric ids, scratch storage separate from graph-lifetime storage. Packs consume bounded projected batches and keep only compact outputs. No run-long decoded record caching; a bounded record escape hatch. Release link scratch before pack expansion and pack scratch before rule execution. Then evaluate a versioned packed fact format, possibly immutable mapped segments indexed by SQLite, to avoid decoding cached JSON into a second fact population on each run. Keep natural-key lookup, atomic publication and cache-miss fallback.

**Target.** At most **0.8 GB peak per active warm scan**, with 1.0 GB as an intermediate milestone. An engineering target, not a proven bound.

**Spike.** Convert the largest fact families and one record-heavy pack path. Measure peak live bytes, allocator-held bytes, RSS and PSS, cold-page behavior, warm CPU and ten distinct overlapping scans. Do not commit to a mapped format unless it wins enough to justify its format, reclamation and concurrency complexity.

### 6. Keep PyO3 and Python-authored policy; move the execution boundary

**Verified.** `plugins.py::PackReader` still exposed tables, resolver, classes, context and file scopes; `BuildReader` forwarded those internals. `NativeRecord.load()` decoded and kept Python objects. The rules bindings repeatedly converted arrays to bytes and back into Rust vectors.

**Judgment.** D2 is right about in-process calls and handles. D22 is right about one refined edge population. D31 is necessary before publication. D1's "mechanics versus meaning" is too vague: running a selector natively need not mean writing policy in Rust.

**Change.** Python owns configuration, pack algorithms, policy planning, the ledger and presentation. Rust owns resident relations and bulk execution, including selector plans. Queries return consistent projected batches; graph work stays behind opaque handles. Define refinement ordering, conflicts, failures and handle lifetimes in the pack contract. Complete the blind-maintainer exercise before the repo and API split (D15); publishing now would freeze the adapters responsible for much of the remaining complexity and memory.

### 7. Cache failure behavior and environment identity fall short of the stated contracts

**Verified.** In the ledger's store module, `SummaryStore.get()` pruned before its guarded read and touched the manifest outside that guard. `put()` let filesystem errors escape. `CacheRoot.prune_daily()` used a read-then-write marker, not mutual exclusion, so concurrent processes could both prune.

**Failure.** Permission, deletion or full-disk races can fail a check instead of causing recomputation. Extend D28's "the cache is optional" to the summary store: contain I/O errors, warn once, and use an atomic pruning claim. Fault-injection tests suffice; no spike needed.

**Verified limitation.** `Framework.digest()` covered selected `.py` RECORD rows or editable package sources. It does not prove D32's universal claim about everything executed or read: transitive imports, native extensions and package data need more coverage. A clean-machine test cannot expose an undeclared dependency that happens to be installed.

**Change.** Specify a supported dependency and input contract and fingerprint that contract, including plugin code and semantic data. Describe RECORD-based identity as relying on intact installations, not as general execution hermeticity.

### 8. The gates need to measure the product users will run

**Measured qualification.** The recorded 10.1 GB ten-check run included three memo hits. It established that mixed workload, not ten active evaluations. The memory profile also ran under heavy load, so its timings are not quiet-machine latency evidence.

**Change the gate matrix.** Require baseline hit and miss, current memo hit and miss, unrelated dirty files, partial staging, policy and engine upgrades, cold facts, daily pruning and ten distinct overlapping staged trees. Measure complete hook p50 and p95 latency next to ruff and ty, CPU, peak aggregate physical memory, swap and page faults, and cache growth.

**Overturn revised D6.** A one-minute load average is not admission control; simultaneous arrivals can all choose four threads. Keep a predictable hook budget until a burst experiment shows adaptive threading improves aggregate and tail latency. Include baseline scans: non-staged scans took all cores.

**Amend D7.** Retire the production Python resolver eventually, but keep a small independent oracle for generated cases through the execution redesign. Ten-table equality on the current repo is valuable, yet shared Python semantics can make both engines agree on the same error. Add adversarial selectors, unknowns, irregular edges, refinement conflicts and mutations of non-representative group members, with independently specified expected outcomes.

## What was taken

From the decision log (`docs/history/decision-log-2.md`, "After the second-opinion review and the final 3d gates", Nov 10). The architect recorded that the second opinion reached the same main findings without seeing the architect's view (the commit path often runs two scans, the blanket drift guard causes it, grouping is becoming a second semantic engine, memory is overlapping copies), and that it added four things, all verified in code:

1. The index was read three separate times (`checkout-index`, `write-tree`, `ls-files`), so a summary could be stored under a tree the run did not evaluate.
2. A baseline miss kept two graphs alive through the `rule -> findings -> Rejudged -> rule` cycle with the cyclic GC off. Measured afterwards: peak 2.33 GB, and 1.78 GB with one collection after the baseline.
3. The summary store could fail a run (a prune outside the guard, unguarded `utime` and `put`, a prune mark with no exclusion).
4. A load average is not admission control, and the framework digest does not cover transitive imports, native extensions or package data, so D32 said more than the code did.

Decisions:

- **D33 (a staged run reads every input from the index), D34 (the summary key names the input set, not the repo tree) and D36 (at most K evaluations at once per machine) were decided,** because both reviews agreed. They were built as phase 4a together with one captured tree per staged run, disposal after the baseline run, a summary store that never fails a run, and tests that fail instead of skip.
- **D6, third version:** a staged run links on one thread; D36 bounds how many run at once. The adaptive count had shown no gain in same-hour pairs, and many simultaneous starts all read the same low load.
- **D32, scoped down:** the framework digest covers the Python files of the declared distributions on an intact install, and says so; it does not cover native extensions, package data or transitive imports.
- **D7 amended:** after cutover a small independent oracle stays for generated cases (selectors, unknowns, irregular edges, refinement conflicts). The metamorphic grouping test was its first part; it found one real grouping divergence (a use-shape rule whose staged key read the link's shape while the primitive replaced it with the flow shape) that no preset rule exposed.
- **D38 (direction):** Rust evaluates selectors per edge over columns, with nothing grouped. The second opinion's spike acceptance (public-surface plus one shape-sensitive rule, 0.7 to 1.0 s saved) was recorded. D39 then folded it into the native port (N1), whose spike judged 3,648,152 pairs with zero differences.
- **Not taken, or taken differently:**
  - "Keep Python-authored policy and planning; move only the execution boundary." The architect went further the same day: D39 put everything a check runs into Rust, with TOML holding the policy and Python as packaging and an optional plugin host, after the owner set "no slower than the existing hook" as the wish and reference tools (ruff, tach) showed what a native check costs.
  - D36 was withdrawn after the adversarial review of phase 4a (a hang when compare waits for a slot its own parent holds, a broken bound, a crash on a bad setting); the staged run stays on one thread.
  - The mapped fact store (the second opinion's memory design and the architect's B, D37) was withdrawn as the memory plan after the N5 spike (D56).
  - Optional asynchronous baseline prefetch: D39 answered the baseline miss with "no background job; a missing baseline is a second run, which a fast engine can afford". A prewarm by git hook was later measured and not pursued (D59).
