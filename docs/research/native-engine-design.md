# hoglint as a native tool: the target design

> The architect's target design for moving a hoglint check out of per-row Python, Nov 10, 2026, with the measured state at each checkpoint of that day.
> Partly replaced: D47 revised (a thin Python shell over one PyO3 extension, not a single binary) and "The PostHog pack's home" (`drives` stays a Python plugin in the consuming monorepo) reverse sections 4 and 9; see `docs/history/decision-log-2.md`, `docs/history/decision-log-3.md` and `docs/design/05-engine.md`.

The question (Nov 10): can the linter run like Ruff, fast over tens of thousands of files?
This is the design that gets there.
It replaces the direction of D29, D30 and D38 (make the Python layer cheaper) with a simpler one: take Python out of the run.

## 1. What the measurements say

Same repo snapshot (29,110 Python files), same machine, same hour, measured under load:

| Tool | What it does | Wall | CPU | Peak memory |
|---|---|---|---|---|
| `ruff check`, whole repo, no cache | per-file lint | 1.4 s | 17 to 19 s | 0.39 GB |
| `ruff format --check`, whole repo | per-file | 0.65 s | 8 s | 0.28 GB |
| `tach check` | import boundaries, whole repo | 2.3 s | 10 s | 0.22 GB |
| `tach map` | import graph, whole repo | 4.1 s | 12 s | 0.29 GB |
| `ty check`, whole project | types, whole program | 9.9 s | 105 s | 7.2 GB |
| `hoglint check --index` | names, classes, framework edges, 30 rules, whole repo | 4.9 s | 4.7 s | 1.40 GB |

Three things follow.

1. **hoglint does less work than any of them.** With its per-file cache warm it spends 4.7 CPU-seconds on the whole repo. Ruff spends 17 without its cache.
2. **hoglint is slow on the clock because it runs on one core.** Ruff, tach and ty spread their work over every core. hoglint cannot: 3.7 of its 4.9 s are Python (packs 1.2 s, labels and rules 2.0 s, start and output 0.5 s), and Python runs one thread at a time.
3. **Memory: Rust tools hold 0.2 to 0.4 GB on this repo.** hoglint holds 1.40 GB because it keeps a private copy of every file's facts, decoded from JSON on every run, plus Python objects for packs and rules.

Where a port gains, measured in this project: the name resolver went from 5.0 s in Python to 0.42 to 0.57 s in Rust on one thread.
That is 10 times before any parallelism.

## 2. Target

- One check of the whole repo: **at most 1 s wall** on a laptop's cores, **at most 1.5 s on one thread**.
- Peak memory: **at most 0.5 GB**. (Withdrawn Nov 10, D56: a check owns 1.25 GB, of which 0.45 GB is the linked graph itself; about 1.0 GB is what cheap steps reach. See `docs/research/spike-n5.md`.)
- A commit whose baseline is missing runs the check twice. At 1 s each that needs no background job, no hook besides pre-commit, and no warm-up.
- Still exact: every run evaluates the whole repo (D5 stays).

These are targets from the numbers above, not results.
The N1 spike (section 7) is the first measured point, and it moved the estimate:

- after N1 to N4, about 1.7 s on one thread and about 1.2 s wall on four;
- after N5, about 1.4 s on one thread and about 0.6 s wall on four.

So the wall target looks reachable only with N5, and the one-thread target has little margin.

The measured state at each checkpoint, newest first.
All are the architect's same-minute pairs.

**After N2b and N3a (Nov 10, ad83640b66e):** one staged check is 3.7 s CPU and 3.8 s wall on one thread, 3.4 s wall on four.
One thread: analysis 1.05 s, Django pack 0.44 s, PostHog pack 0.58 s, rules 1.05 s, the rest 0.5 s.
Peak 1.37 GB.

**After N2 (Nov 10, a3ab31d45fb):** one staged check is 3.75 s CPU and 3.85 s wall on one thread, 3.5 s wall on four threads (before N2: 4.9 s CPU, 5.0 s and 4.7 s wall).
The rules phase is 1.0 s on one thread and 0.67 s wall on four (before: 2.15 s and 1.85 s).
One check on one thread: analysis 1.05 s, Django pack 0.60 s, PostHog pack 0.56 s, rules 1.01 s, the rest 0.5 s.
Peak 1.40 GB, unchanged.

**At the N2 checkpoint (Nov 10, 9d4d25dc1e7):** one staged check is 4.5 to 5.0 s CPU on one thread and 4.1 s wall on four threads; the rules phase is 1.55 s on one thread and 1.2 s wall on four.
Gained against the commit before N2: 0.6 to 0.75 s.
Three changes did it, and each removed a Python consumer: labels read from the evaluator, a finding's edge kept as a handle, and signature rules in Rust.

**After N1 (Nov 10):** one staged check is 5.3 s CPU on one thread, as before: analysis 1.15 s, packs 1.3 s, rules 2.2 s, start and the rest 0.65 s; peak 1.40 GB.
Two estimates of the architect have now missed in the same direction (the spike gate by 0.1 s, N1 by all of its 0.7 s).
Read the figures above as the far end of what the port can give, and trust only the paired measurements at each checkpoint.

## 3. The shape

One batch pipeline in Rust, parallel where the work is per file or per rule:

| Stage | Today | Target |
|---|---|---|
| Snapshot: (path, blob id) of every input, from one captured tree | git calls from Python | same, native |
| Facts: per blob, cached by content | JSON in SQLite, decoded on every run (270 MB of rows, 151 MB of strings) | a binary store that is mapped, not decoded; shared by every process |
| Link: resolve every name, build edges | Rust, one thread on a commit | Rust, parallel per file |
| Packs: framework edges and refinements | Python, 1.2 s, 0.4 GB of decoded records | Rust modules, enabled and tuned by config |
| Labels, kinds, exports | Python | Rust |
| Rules | Python, with grouping to cut the row count | Rust, per edge; no grouping |
| Ledger, summary, compare, output | Python | Rust |
| Start | Python interpreter and imports: 0.06 to 0.10 s, measured | the same. A thin Python shell over PyO3 stays, as in tach (D47 revised); a binary would save a tenth of a second |

"Linking" means the join across files: a file's facts say `from x import y`, and only the other files' facts say what `y` is.
hoglint redoes that join for all files on every run.
It is cheap (about 0.5 s of CPU, and it parallelizes).
What is expensive today is decoding the facts before it and holding private copies after it.

## 4. Where meaning lives

> **Reversed in part.** D47 revised keeps a thin Python shell, and "The PostHog pack's home" keeps the PostHog pack (`drives`) as a Python plugin in the consuming monorepo, not as a module in the hoglint repo. Facade facts went into the engine as a generic export analysis (D48, built as N3b). The text below is the Nov 10 design as written. Current state: `docs/design/05-engine.md`.

- **Rules stay TOML.** Selectors, fields, zones, exports, identities and levels do not change. Only their evaluator moves.
- **Packs become built-in analyses**, selected and parameterized in config, the way Ruff ships its Django and FastAPI rule sets. The Django pack's knobs (hooks, receiver decorators, settings proxies, database routing) are config already.
- **The PostHog pack** (drives, facade facts) moves into the hoglint repo as its own module, enabled by the PostHog preset. A change to it becomes a hoglint release and a pin bump.
- **A Python plugin API stays as an escape hatch**, off the default path and not a stable public surface. D31's work to polish that API for publication is dropped.
- D1 becomes: Rust runs everything a check does; TOML holds the policy; Python is packaging and an optional plugin host.

## 5. What this deletes

Each of these exists only because a run is slow or heavy:

- rule grouping and the read audit (`Grouper`, `EndpointClasses`, `FindingClasses`, `Rejudged`, `Reads`, `audit`: about 2,200 lines, and 12 binding methods), with their class of bugs;
- the glue that keeps edges and symbols out of Python (`hoglint/native/`: about 3,000 lines);
- the limit on concurrent runs (D36), the thread-count rules (D6), and the idea of a background warm (D35);
- the pressure on the run memo and the stored baseline: they stay as conveniences, not as what makes a commit bearable;
- the "ten checks at once" memory gate, which becomes trivial at 0.5 GB.

## 6. How to keep it correct

- The Python implementation is frozen and becomes the executable spec. Every phase must equal it on all ten parity tables, with zero delta.
- Each stage gets its own switch, so a ported stage runs against the Python version of the same stage on the same graph.
- The generator from the grouping test becomes the differential test for the evaluator: generated rule configs, Python against Rust.
- After the last phase the Python implementation stays in the test suite, not in the wheel, until a golden corpus replaces it (D7 as amended).

## 7. Phases

Each phase lands with zero delta.
Each is about the size of the link port that is already done.

| Phase | Moves to Rust | Effect |
|---|---|---|
| N1 (done, b10fc74cf04) | Labels, kinds, exports, selector evaluation per edge, identity projection, finding rows; selection for the rules that judge in Python; reach and cycle through the same evaluator | **Measured: no time gained on one thread** (rules 2.2 s before and after), 0.25 s wall less on four threads. Grouping deleted. The architect had expected 2.1 s → 1.3 s. |
| N2 | In this order: (1) Python reads labels from the evaluator's columns, so the Python label pass runs only to word a conflict; (2) a finding row keeps its edge as a handle, and an `Edge` is made only when something reads it; (3) signature; then export-shape, coverage, use-shape, layout, inventory and levels, by cost | estimate: rules 2.2 s → about 1.0 s on one thread. **Checkpoint after step 3: at least 0.45 s gained in pairs, or stop and plan again** |
| N3 (D49, after the spike) | `django.uses` as a Rust producer; facade facts as the engine's export analysis (D48); column-wise `add_edges`; `mentions_many` for installs and signals. `drives` and the other Django producers stay Python | estimate: packs 1.3 s → about 0.65 s. Measured in the spike: `django.uses` alone gives 0.25 to 0.3 s |
| N4 (dropped as "one binary", D47 revised) | The Python shell stays: CLI, config, git calls, plugin readers, printing. Measured cost of starting it: 0.06 to 0.10 s. Only what measurement shows per-row or slow in ledger, summary and compare moves | the 0.65 s outside the three big stages needs a breakdown before any estimate |
| N5 (replaced by N5a after the spike, D56) | The mapped fact store is withdrawn: measured 0.14 GB for 1 s more CPU, and at most 0.24 GB for about 3,000 lines. N5a instead: release pack records per producer step, cut the snapshot's 0.42 s of Python, shrink the fact database a check reads | estimate: peak footprint 1.25 GB → about 1.0 to 1.1 GB with N3b; analysis time by what the measurements show |

- **The rule N1 taught: a phase gains time only when a consumer of Python objects goes away.** Moving a kernel to Rust and handing its result back as Python objects moves the cost; it does not remove it. So every later phase is cut at a place where the Python objects end. Its gate is the whole phase's time in same-minute pairs, measured at a checkpoint in the first third of the work.
- Order by value: N1 removed the riskiest code; N2 and N3 remove the most time; N3 and N5 remove the most memory.

### The N1 spike (done, `docs/research/spike-n1.md`)

Python handed Rust a plan of the preset.
Rust computed the labels and judged all 17 `edge` and `entrypoint` rules per edge, with no grouping.

- **Exact:** 290,380 label values and 3,648,152 judged (edge, rule) pairs, 0 differences against the Python per-edge path. 30,290 pairs kept, the same as the grouped survivors of that day.
- **Time, one thread:** labels 0.08 s and judging 0.20 s, against 0.24 s and 0.66 s for the Python engine in the same minute. About 0.6 s saved on this scope. On four threads judging takes 0.055 s wall.
- **Against the three ways to judge:** plain Python per edge 12.3 s, grouped Python 0.66 s, Rust per edge 0.20 s. Grouping had already bought 18 times, so Rust gives 3 times on this part, not 10. The parts that are still plain per-row Python (the other primitives, the packs) are where the larger factor applies.
- **Against the gate the architect set (0.7 s off the rules phase):** the correctness half passed. The time half came in at 0.6 s on one thread for the scope measured, and the spike builds no findings, so it is not like for like. The architect had put too much of the saving in N1 and too little in N2; the sum did not change much.
- **Effort:** 1,500 lines of Rust and 310 of Python.
- **Found on the way:** `Labeler.exported` caches by source module and destination and ignores endpoint attrs an audience could read. Nothing differs on this snapshot. It is one more cache that the per-edge evaluator does not need.

### What a full N1 adds to the spike

- Findings as rows: identity projection, owner and subject modules, ledger flags, and incompletes for unknown selections and unresolved edges, in the engine's rule order. Python wraps the rows for the ledger code until N4. A message is made only for a finding that prints, by judging that one edge again in the frozen Python rule and failing loudly on a different judgment.
- Class ancestry, `kind` and export membership by class in Rust; symbol-level fields; selector domains; provider fields.
- Selection for use-shape and signature, and the hop verdicts of reach and cycle, through the same evaluator. Then `Grouper`, `EndpointClasses`, `FindingClasses`, `Reads` and the read audit go.
- A stage switch, so the parity tables compare the Rust rules with the Python rules on the same graph.

### N1 as built (b10fc74cf04)

- **Exact, in the build agent's proofs:** 319,418 label values, 5,087,213 judged (edge, destination) pairs with 30,290 rows, 11,511 cycle rows and both reach rules equal to the Python per-edge path. 900 generated configs found one divergence, which was fixed (a set compared as text). The use-shape spec the old engine got wrong gives 32,565 findings in both engines. Ten tables equal against the starting commit.
- **Time, the architect's pairs** (2e9fadc662d against b10fc74cf04, same minute, measured under load):
  - one thread, 6 pairs: whole staged check 5.1 to 5.5 s CPU on both sides; rules phase 2.03 to 2.44 s before, 2.10 to 2.36 s after (one outlier at 2.86 s);
  - four threads, 4 pairs: rules phase 2.05 to 2.25 s wall before, 1.79 to 1.93 s after;
  - peak memory 1.40 to 1.42 GB on both sides.
- **Where the rules phase goes now** (one thread, 2.11 s CPU): signature rules 0.35 s (38,000 selected edges judged in Python), layout 0.30 s (0.22 s of it is the Python label pass, which moved there), export-shape 0.27 s, public-surface 0.21 s (0.16 s in Rust), coverage 0.18 s, use-shape 0.13 s, cycle 0.09 s, levels 0.07 s, the 14 edge rules together 0.2 s, and 0.29 s of `Edge` objects made in 6,189 calls across those.
- **Why the estimate failed.** Removing the grouping saved about 0.74 s. Rust's own work costs 0.5 s on one thread. The rest came back as Python that the old engine did not pay: signature lost the tight selection grouping gave it, and the label pass that grouping had made lazy is paid in full by the first rule that reads Python labels. The architect had counted the kernel and not the consumers.
- **Kept as it is.** It is the base N2 needs, it removes about 700 lines of Python and the class of grouping bugs, and it is not slower.
- **Size:** Rust rule code 1,106 → 4,283 lines; Python rule-engine files 2,985 → 2,279.

## 8. Commit-time checks are a new feature

- At the time of writing, boundary tools ran only in CI in the target monorepo. Nothing checked boundaries at commit time.
- So CI can use the engine as it is after phase 4a. Five seconds and 1.4 GB do not matter there.
- A pre-commit hook is a new feature. It can come when a check takes about a second (see `docs/design/08-fast-hook.md`).

## 9. Risks

> **Reversed in part.** The last point argues for the PostHog pack living in the hoglint repo. "The PostHog pack's home" reversed that: with a Python shell kept (D47 revised), the reason went away, and `drives` stays a Python plugin in the consuming monorepo. The rest of the section stands. Current state: `docs/design/05-engine.md`.

- **The estimates.** They rest on one measured port (10 times) and on Ruff's and tach's figures. The N1 spike tests them first.
- **Fidelity of the port.** The oracle, the stage switches and the generated configs cover it; the link port met the same bar on 1.47 million edges.
- **A PostHog module inside the tool's repo.** It is coupling. It is acceptable while PostHog is the tool's owner, and other repos use config and the Django pack.
- **More Rust to own:** about 16,000 lines in the end, against about 14,500 lines of Python that leave the run.
- **How often a pack needs a new read.** The history of the monorepo's earlier boundary scanner (read Nov 10) shows about 3 new static-Python reads a month since August (get_model, wiring ancestry, system checks, signal receivers), and 12 of 14 need facts from other files. In the native tool each one is a hoglint change, a release and a pin bump. Two consequences: a release must be one tag that CI turns into wheels, and the Python plugin host is the place to try a new read before it is ported, not only an escape hatch. It also argues for the PostHog pack living in the hoglint repo (one change, one release) while that rate lasts.

## 10. What stays from the earlier decisions

D3 (one summary type, exact keys), D4 (snapshots by blob id), D5 (whole repo every run), D8 and D9 (unknown is data; compare and growth), D12, D14, D15 (own repo), D22's rule that packs refine and never copy, D27's rule that nothing naming an entity is shared, D28 (a cache problem is never a wrong answer or a failed run), D32 as scoped, D33 and D34 (one captured tree, declared inputs, key by input set).
The decisions are in `docs/history/decision-log.md` (D1-D21) and `docs/history/decision-log-2.md` (D22-D47).
