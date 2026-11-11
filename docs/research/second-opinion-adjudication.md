# Second opinion: adjudication of the engine round

> A second opinion from another model that adjudicated the three adversary reports of the engine round, Nov 9, 2026: a verdict per finding, what the adversaries over-claimed or missed, and the engine to prototype. With the question, the architect's view written before it, and what the decisions took.
> Partly replaced: its recommendation (a revised C2) became D1 to D13 in `docs/history/decision-log.md`; the adversary reports are in `docs/research/engine-adversarial-reviews.md` and `engine-adversarial-reviews-2.md`, and the bug list in `engine-adversarial-reviews-3.md`.

## The question

Phase 2 of the engine round had three adversaries (correctness, extension and maintenance, performance) attack the three candidate engines: C1 Python cleaned, C2 native link with full relink, C3 persistent queries (`docs/research/engine-synthesis.md`).
The second opinion was asked to adjudicate:

1. For each finding rated critical, high or medium-high, give a verdict (confirmed, refuted, partly confirmed with which part, unverifiable), checking the code at the cited lines and the fixture or script where one exists. Special attention to:
   - performance F4: the Rust port matches all 1,143,531 rows and links in 0.42 to 0.57 s; does the port really cover what the Python resolver does, and does the parity check compare everything that matters?
   - extension F1: 8 new catalog fields are needed; is the count right, and is "make the schema-v3 facts the catalog" a real fix?
   - correctness F2 and F3: the base-ledger shortcut breaks, and C3 breaks on reads the resolver makes outside any query boundary;
   - correctness F6: the engine reports clean where it cannot see.
2. Name findings the adversaries got wrong or over-claimed, and anything important all three missed.
3. Recommend which engine (possibly a revised candidate) phase 3 should prototype; say what the prototype must prove, with numbers, and what must be fixed in any architecture before cutover.

Cite file and line for every code claim; under about 250 lines.

## What it was given

- The phase 1 reports: the demand audit, the skeptic, the two blind designs, the prior-art study, and its own earlier architecture answer (`docs/research/second-opinion-engine-round.md`).
- The synthesis, sections 1 to 3 and 5 only: what phase 1 settled, the disagreement, the candidates, the phase 2 plan. It was told not to read section 4 (the architect's read), the architect's views, or the bug list.
- The three adversary reports, and their scripts and fixtures, including the Rust port of the resolver.
- The settled behavior (user guide, preset), with the agreed decisions overriding the guide where they differ.
- The code at commit `0c197478064`.

## The architect's view, written first

Written after phase 2 and before the adjudication, and not shared with the second opinion.

**C1, Python cleaned:** a migration step and CI fallback, not the engine. Its full check reaches 12 to 20 s only with every interim fix applied, and its pre-commit is a full run: at ten concurrent worktrees, 20 to 39 GB and 45 to 75 s per run.

**C3, persistent queries: rejected for now.**

- Its performance is better than feared: the frontier is a median of 5 files, p90 14; the dependency store takes 26 to 65 MB and 0.2 to 0.4 s to verify.
- But it breaks on correctness unless resolution is first rewritten as tracked queries: module existence, repo heads, PEP 562 and memo reads are invisible to it, and 82 re-export cycle results depend on query order.
- Its cold path costs 20 to 55 s, and every pattern change would need an invalidation test.
- Revisit only as tracked reads inside the Rust link, if pre-commit ever needs to be under 1 s.

**C2 survives, but narrower than the blind designs. Call it C2': a Rust link over the existing facts.**

| Part | C2 as the blind architects drew it | C2' |
|---|---|---|
| catalog | a new generic catalog | the schema-v3 facts; the blind catalogs need 8 new fields and fail their own kill signal |
| link | new | a port of the Python resolver, already done in scratch: all 1,143,531 rows match, 0.42 to 0.57 s against 5.0 s in Python |
| pack API | queries by resolved origin | by resolved origin plus by spelling, where an unresolved spelled match becomes an unknown edge; bulk row reads (0.08 s for 1.23M rows) |
| drives, migrations | the escape hatch | drives rewritten to pull only test files with kind mentions (299 of 8,111 test modules read beyond their own file); migrations as a per-blob summary |
| rules | predicate per label-class group | the same; the group key carries the surface symbol under `subclass_of` exports |
| oracle | none | the Python resolver as a CI differential until cutover |
| compare base | base-ledger shortcut | a summary per master commit published by CI, keyed by (commit, engine id, policy hash, framework digest), else a full base scan |
| budgets | 1.3 to 3 s pre-commit, 5 to 8 s cold CI, 0.3 to 0.7 GB | 2 to 4 s pre-commit idle (about 3x under ten-way concurrency), 7 to 15 s cold CI on 4 cores, about 1.2 GB per process |

**What phase 2 changed in the architect's read:** C2's biggest risk moved. It is no longer "can Rust resolve fast enough"; two risks remain: packs read millions of rows, and resolution changes become wheel releases (about once a month). The bug list is long and independent of the architecture. Pre-commit needs a baseline to tell new unknowns from inherited ones; that is a design gap, not a speed issue.

**Phase 3 as the architect would cut it:** bring the scratch Rust link in behind a flag; measure full check, pre-commit with a blob-id snapshot, and ten concurrent runs; keep every parity invariant green with the Python resolver as the differential; prototype the drives rewrite; run the blind maintainer test.

## The second opinion

What follows is the second opinion's answer, cleaned. Code references are to hoglint's modules at `0c197478064`; "scripts" are the adversaries' experiment scripts.

**Prototype a revised C2: native full relinking, typed schema-v3-derived facts, and Python packs and predicates.** Phase 2 establishes that native resolution is promising, but does not establish complete linker parity or the proposed end-to-end timings. C3 remains viable; its correctness and maintenance costs now outweigh its demonstrated advantage.

It inspected the code, the scripts and fixtures, and independently compared the saved parity rows. It did not rerun timing experiments.

### Correctness findings

| Finding | Verdict | Adjudication |
|---|---|---|
| F1: unchanged files gain edges | **Confirmed** | The `fx2` ancestry chain defeats one-hop name locality. Model recognition follows transitive ancestry, so changing the base changes discovery and relations in untouched descendants. A dependency closure must include semantic consumers, not just direct import or name consumers (`packs/django/models.py`, `core/symbols.py`). |
| F2: the ledger replaces the base scan | **Partly confirmed** | **The shortcut is unsound.** Ledger generation excludes non-ledgered findings and incompletes; compare also needs triples, levels and move witnesses or coupling counts. But "all 1,227 become introduced" is not universally true: `reference()` falls back to `base_head`, and incompletes were already collapsed by key. The skeptic also required a successful base check and kept the full path before enforcement. These qualifications do not repair the missing information (`rules/engine.py`, `ledger/scan.py`, `compare.py`). |
| F3: untracked resolver reads break C3 | **Partly confirmed** | **Confirmed against table-only instrumentation or naive memo wrapping.** Existence, repo heads, lazy maps, negative lookups and cached subqueries all matter, and the fixture shows these hazards. It does not refute a properly tracked C3: the proposal required negative and collection dependencies and a coarse fallback. The overclaim is that keeping Python semantics necessarily means replacing the resolver wholesale; wrapping every input and memo boundary, or invalidating conservatively, can work, at a cost (`core/symbols.py`, `core/modules.py`). |
| F4: ambient inputs bypass snapshots | **Confirmed** | Routing reads disk; turbo reads git membership with a filesystem fallback; layout checks existence; framework discovery reads the active interpreter's packages; the clock affects exceptions. A Python-blob manifest alone cannot validate reuse. This affects batch snapshot correctness too, without persistent queries (`packs/django/migrations.py`, `adapters/turbo.py`, `rules/entities.py`, `facts.py`, `rules/engine.py`). |
| F5: origin-only queries lose candidates | **Confirmed** | `fx4` exercises a real unresolved-hook obligation. `get_model` deliberately matches spelling and can resolve a model label despite an unresolved receiver. The fix must keep producer-specific handling: **not every spelling-only candidate becomes an unknown edge** (`packs/django/installs.py`, `packs/django/uses.py`). |
| F6: unknown becomes clean | **Confirmed, with a mechanism correction** | All four blind spots are real; details below. "No finding" means the relevant obligation disappears, not necessarily that the whole repo exits clean. |
| F7: move admission ignores shape | **Confirmed** | The saved `fx3` rename turns an annotation into a query. Coupling identity excludes shape, and admission compares only that coarse identity and count. Include the rule-relevant semantic dimensions, not merely a universal `label` field assumed sufficient for every rule (`ledger/scan.py`, `ledger/growth.py`). |
| F8: incomplete multiplicity disappears | **Confirmed** | The information is lost **before** compare: `ScanSummary.from_run()` overwrites entries sharing (rule, key). Changing only compare to a counter cannot recover it. Keep multiplicity and useful obligation identity through serialization (`ledger/scan.py`, `compare.py`). |
| F9: a failed base scanner disables growth | **Confirmed** | Failure returns `None`; growth becomes "skipped"; skipped growth does not fail. The subprocess uses `sys.executable`, while the extractor defaults to a shared machine-wide target. Distinguish "the base predates the scanner" from "a required base execution failed", and pin the whole execution environment (`cli.py`, `compare.py`, `facts.py`). |
| F10: growth-only cross-engine contract | **Confirmed** | Growth sums cannot support incomplete attribution, finding differences, triples, levels or move admission. Version a sufficient summary protocol before introducing incompatible engines. It need not serialize the whole graph. |
| F11: unattributed scanner and environment shrink | **Partly confirmed** | Shrink is visible through resolved findings and removed ledger lines, so "nothing labels it" is too broad. **Its cause is not attributed:** rule growth reports increases only, and policy classification iterates changed policy entries. Routing and classification contents and framework versions need explicit provenance beyond the TOML hash. |

For **F6**, the precise failures:

- **Unparsed files:** the producer emits `unparsed`, but rule execution does not turn that into scoped incomplete obligations, and Django excludes failed records. Merely storing an unknown edge is not enough.
- **Dotted model uses:** extraction sends these to aggregated `uses`, not detailed `refs`. There **is** a fallback producer, contrary to an implication of the report, but it emits no shape, and the preset's `edge.shape != none` excludes it. A known absence is used where uncertainty is required.
- **Star-bound names:** site lookup needs an explicit table binding or a scoped import; it does not call the resolver's star-import search for an otherwise unbound name. Extraction also drops names absent from its binding map. Fixing resolution alone is not enough.
- **Variable bases:** class ancestry drops bases that do not resolve to classes; the graph-based ancestry marks only explicitly unresolved bases incomplete. Neither establishes that a resolved variable cannot produce a model base.

### Extension findings

| Finding | Verdict | Adjudication |
|---|---|---|
| F1: eight catalog additions | **Partly confirmed** | The proposed catalogs lack required information, but **eight is not a defensible exact count of independent schema additions** (see below). Schema v3 is a useful starting point, not a complete architectural repair. |
| F2: spelling queries required | **Confirmed** | Parameter receivers, local receivers and unresolved hooks need candidate queries independent of origin. Keep spelling, resolution status and scoped lookup together. |
| F3: use vocabulary affects growth keys | **Confirmed** | The classifier distinguishes parent syntax, attribute names, argument callees and `model` positions. Collapsing them into `other` changes labels and therefore ledger and growth identity. Keep equivalent information; the literal `up` string is one way, not the only one. |
| F4: drives does not fit either sketch | **Partly confirmed** | The dispatcher is branch-based, and helper traversal crosses into non-test files. A literal-table-only implementation and a strictly test-only hatch fail. But this does **not** force parsing every first-party file in Python: branch facts and reachable helper summaries suffice. |
| F5: native resolution creates release friction | **Partly confirmed** | Scoped resolution and evolving semantics are real. The cited commit changes bindings, contexts, use positions and dynamic-import handling, not four independent resolver algorithms. Monthly wheel-release forecasts from bootstrap history are not established. Scope-aware APIs and a resolver oracle are justified regardless. |
| F6: migration breaks parity instruments | **Partly confirmed** | The adapters consume Python edges and parity-only attributes, so they need adaptation. But the invariants themselves can stay fixed, and columnar storage does not prohibit a compatibility view. Keeping schema-v3 extraction also avoids invalidating extractor parity. |

The **F1 count** needs these corrections:

- The report lists eight entries for A and eight for B, so its arithmetic is right.
- It labels B's def-use support "yes (8 lost)" and excludes F2 from B's eight. A depth-three limit stays a gap if deeper flows change output.
- More importantly, the probe measured the **maximum downstream hop of an already refined source**, not whether truncating at one or three hops changes its final shape. "130 need two hops" and "eight are lost" are upper-bound candidates, not shown parity losses.
- A schema field, an extra enum or value variant, an analysis-depth limit and an index query are different costs. Eight gaps need not mean eight wheel releases.

"**Use schema v3 as the catalog**" is a real way to avoid discarding information: defaults, flags, full use paths, keywords, tags, watched keys and branches already have representations (`src/facts.rs`). It is **not** sufficient by itself:

- Facts were still filtered by first-party configuration, watch lists and requested kinds; cache keys must keep those dependencies, or extraction must become configuration-independent.
- Values have explicit budgets and opaque forms; adopting the schema does not establish completeness.
- Packs still need efficient selection, scoped resolution and typed access, not wholesale Python materialization.
- The F6 omissions must be repaired, not fossilized as "parity".

### Performance findings

The performance report gave no severity grades, so all ten were adjudicated.

| Finding | Verdict | Adjudication |
|---|---|---|
| F1: pack crossing volume underestimated | **Partly confirmed** | The broad reads are real; they do not prove the volume or cost of a rewritten query API. Bulk tuple construction is not a measurement of PyO3 handles, strings and real pack logic. |
| F2: ten concurrent worktrees | **Partly confirmed** | A valuable missing workload. The linker slowdown does not establish a universal 3x for Python, C3 or developer p90. Ten simultaneous checks need a measured frequency before they become a latency distribution. |
| F3: 1.3 s hook budget | **Partly confirmed** | The omitted stages make 1.3 s unsupported. The replacement 2.2 to 4.0 s is still a component estimate. The config conflict check is real whole-repo work. |
| F4: exact, sub-second Rust linker | **Partly confirmed** | **Saved narrow parity is confirmed independently. Full semantic parity is refuted.** The timing is plausible evidence for the measured subset, not a completed linker budget. Details below. |
| F5: cold CI depends on hatch scope | **Partly confirmed** | The script measures parsing plus an AST walk, not the proposed pack. It shows the danger of a broad hatch, not the final 9 to 11 s or the x86 multiplier. |
| F6: real traces prove a small C3 frontier | **Partly confirmed** | Useful evidence that many recorded dependencies are local. **Not a correct history replay or a complete invalidation proof.** Details below. |
| F7: group evaluation is cheap enough | **Partly confirmed** | Per-symbol and per-use residual work is correctly identified. 0.3 to 0.7 s stays an estimate; export and shape consumers need detail beyond module classes. |
| F8: C1 needs combined optimizations | **Partly confirmed** | The saved Python baseline supports a large full-run floor. The combined 12 to 20 s and the concurrency extrapolation are not measured implementations. |
| F9: compare budgets and the base-ledger fix | **Partly confirmed** | Two equivalent-envelope scans versus up to four differing-envelope evaluations is reasonable. **The recommendation to implement the base-ledger shortcut is refuted.** Only a sufficient, exact-envelope summary can replace a scan. |
| F10: C2 memory underestimated | **Partly confirmed** | The port's 0.90 GB is a useful warning. Neither a universal 2x miss nor the final 1.1 to 1.4 GB follows without integrated packs and storage. |

For **performance F4**, the second opinion compared the two saved row files as **multisets**, including duplicate counts: **1,143,531 rows on each side; zero differences**. However:

1. Python serialized only kind, path, line, column, resolution and targets; Rust used the same restricted projection and skipped `declares`. The Python run actually produced **1,423,089 edges**.
2. The comparison omitted surface, via, scope, contexts, producer, use position, shape and attributes. MRO values were computed but not compared.
3. The port omitted semantic data: `expose()` discarded whole and nested position and emitted no callable or slot attributes; declarations omitted body and decl metadata and inherit and decorate attributes. Python supplies those, and ancestry indexes consume them.
4. Discovery, exclusions, test recognition and decorator and patch handling held benchmark-specific constants instead of the configurable behavior.
5. The extra callee and use pass counted resolutions; it did not build and expose the full pack-query index. Its CSR omitted context partitions and unresolved and external graph obligations.
6. The report's own repeated-run range is **0.49 to 0.74 s**, wider than the headline 0.42 to 0.57.

So: **sub-second native resolution is credible; "same work", "exact port" and "none needed for the link" are not.**

For **performance F6**, the most consequential problems:

- It loads **one trace** and applies historical changes to it; it does not recompute dependencies and compare outputs at every revision.
- Modified-file fingerprints omit lazy-map contents, so a dictionary-only PEP 562 edit does not invalidate.
- Existence and external classification share one key, while invalidation does not propagate repo-head changes or namespace-parent membership changes.
- The recursive closure returns empty on a cycle and memoizes partial closures; that is not an SCC dependency closure.
- Only `.py` changes enter the replay, so stub changes and other environment and config inputs are excluded.

### Omissions and overclaims across the round

The largest overclaims are the full Rust parity assertion, the implied completed C3 replay, exact catalog-gap and release counts, and extrapolated end-to-end timings. The correctness adversary also overstates "C3 breaks as planned": the design specified the necessary safety conditions, but underestimated the work to enforce them.

Two more correctness issues:

- **drives has a depth-sensitive memo problem.** Depth exhaustion returns an ordinary false with no incomplete marker; memo keys omit depth, and negative results are installed before traversal. A deep traversal can leave a negative result that a shallower traversal later reuses. This matters even within one fresh process, independently of C3; callers turn an unsuccessful traversal into `"none"`. It needs a targeted fixture; the second opinion did not run the counterexample.
- **Counting incompletes still cannot identify a replacement.** Removing unknown obligation A and introducing B under the same coarse key leaves the count unchanged. A counter-only repair cannot satisfy strict causal attribution. Define stable obligation identity or report ambiguous replacements.

A broader measurement omission: the correctness fixes themselves enlarge the workload. Recording omitted references and keeping all semantic attributes changes extraction, memory and pack demand. Benchmark the **corrected contract**, not a faster reproduction of present blind spots.

### The phase 3 prototype and its gates

Use **C2-A's division of responsibility**, revised: Rust owns compact facts, configurable resolution, ancestry, candidate indexes and graph operations; Python owns framework meaning, shapes, labels, predicates and comparison policy. Keep pure per-blob summaries, but no cross-snapshot semantic query cache at first.

The prototype must include **drives, migrations, model shapes and facade and export analysis**. Four convenient origin queries are not a representative test.

Proposed gates, not predictions:

| What it must prove | Numerical gate |
|---|---|
| Semantic parity | **Zero unexplained differences** across full edge semantics, symbol facts, ancestry certainty, findings and counts, incompletes, levels, triples and move evidence. Compare all 1,423,089 baseline core edges, not just the 1,143,531-row projection. Approved bug fixes get separate expected deltas. |
| Rollout invariants | Reproduce **968 baseline crossings plus the two documented extras**, **0 missing and 0 extra tach pairs**, and extractor parity wherever the fact contract is unchanged. |
| Mutation correctness | Every adversary fixture plus negative lookups, stub changes, lazy-map edits, cycles, failed parsing, opaque bases, move-plus-new-use and incomplete replacement: **zero unexpected passes**, cold, warm and restarted. |
| Determinism and snapshot isolation | **20 repeated runs** with shuffled traversal and several hash seeds: identical normalized outputs. Index results unaffected by conflicting unstaged policy or code. |
| History | **200 master and 200 local transitions**, with real snapshot execution and output comparison, not a fixed-trace frontier estimate. |
| Native layer | Full fact load plus a semantically complete link and index: **p90 at most 1.5 s** on the reference laptop. |
| Local workflow | Warm full check **p90 at most 4 s**; staged check **including the required baseline attribution at most 6 s**; unchanged-envelope compare without a base-summary artifact **at most 8 s**. |
| Cold CI | On a real **4-vCPU Linux x86 runner**: cold check **at most 15 s**, ordinary cold compare **at most 30 s**. Report environment installation separately. |
| Memory and concurrency | Integrated peak **at most 1.5 GB per process**; ten simultaneous warm staged checks **p90 at most 12 s**, aggregate peak **at most 15 GB**, with shared-cache writer contention. |
| Maintainability | A blind Python maintainer completes **three representative pack changes without Rust edits**, including an unresolved spelling match and cross-file helper analysis, at most about one working day each. |

If native linking passes but packs miss the total budget, optimize the measured pack bottleneck before adding persistence. If C2 cannot meet the maintenance gate without recurring schema and query additions, reconsider C3 over the same corrected fact and snapshot APIs.

**Before any architecture cuts over:**

- snapshot-backed reads for all semantic inputs, including file membership, stubs, framework contents and policy sidecars;
- explicit incomplete obligations for parse failures, unsupported analysis and exhausted budgets; inherited uncertainty may stay non-blocking, but must still prevent unsupported Isolated claims;
- a versioned summary protocol that keeps counts, obligation identity and move evidence; exact-envelope summary reuse, never ledger substitution;
- pinned base and head engine environments, and an explicit failure when a required base scan cannot run;
- shape-aware move admission, and attribution of shrink to scanner, policy or environment;
- full semantic parity tooling, deterministic witnesses and correct symbol-sensitive grouping;
- cache keys covering extraction configuration, interpreter and pack dependencies and external inputs; corruption recovery and bounded cache growth;
- the shared phase 1 correctness repairs, especially duplicate semantic edges and missing symbol facts, with intentional behavior changes kept apart from representation parity.

The revised C2 should earn adoption through those gates. The Rust benchmark is strong enough to justify the prototype, not to authorize cutover.

## What was taken

The second opinion's recommendation and the architect's C2' agreed. From the decision log (`docs/history/decision-log.md`, written after the engine round on Nov 9):

- **D1:** C2-A's split as revised: Rust holds the facts (the schema-v3 catalog), configurable name resolution, ancestry with certainty, candidate indexes by origin and by spelling, and graph work; Python holds the packs, shapes, labels, predicates, ledger, compare policy and outputs. D1 cites the narrow-parity 10x of the resolver, not "exact port".
- **D5:** every run evaluates the whole repo, with no cross-snapshot semantic cache; C3's tracked dependencies cost more to keep correct than they save (citing this adjudication).
- **D7:** one resolver in the end; the Python resolver is the differential oracle until cutover.
- **D8:** unknown is data, including exhausted budgets and spelled-but-unresolved candidates "wherever the producer's semantics say so", following the adjudication's limit on F5.
- **D9:** compare and growth: shape-aware move admission; counted obligations with identity, and ambiguous replacements reported as such (the adjudication's replacement point); exit 2 on a failed required scan; a versioned summary protocol; shrink attributed by cause; a pinned base environment.
- **D13:** drives is rewritten and "fixes the depth-memo bug" (the adjudication's addition), as a counted delta.
- **D3 and D14:** exact-key run summaries, published from master CI; never the ledger as a stand-in for the base scan.
- **The gates:** phase 3 measured semantic parity on the full tables, the rollout invariants (crossings 968 plus 2, tach 0/0), determinism across seeds and thread counts, the 4 s and 1.5 GB gates and ten overlapping checks, and a cross-platform check between macOS and Linux under the same key (D14).
- **Not taken as stated:** the ten-overlap gate became "at most 15 GB in total", not a p90 time; the blind maintainer test was set as the proof of the narrowed pack API (D31), and the pack API itself was later dropped as a public surface by D39.
