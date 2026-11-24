# Second opinion: the engine round

> A second opinion from another model on the design of Hoglint's engine, Nov 9, 2026, with the question it got, the architect's view written before it, and what the decisions took from it.
> Partly replaced: its recommendation (persistent queries) was rejected after the adversarial phase; the decisions are D1 to D13 in `docs/history/decision-log.md`, and the round is summarized in `docs/research/engine-synthesis.md`.

## The question

On Nov 9, 2026, Hoglint's user-facing behavior and config were settled, and the engine underneath was not.
A first implementation existed: a full check of the PostHog monorepo took about 45 s and 4 GB, and the pre-commit form took about 70 s.

The second opinion was asked to design the engine:

- what should be computed at all, and what eagerly or on demand;
- where the line between native code and Python should sit;
- the data layout across that line;
- how rules and packs are written.

It was asked for two or three distinct architectures, each with: the computation model; the language split; the data layout; the extension model for Python pack authors, with a short code sketch of adding a new Django hook pattern; how "unknown is never clean" holds; the cost per mode (CI full, pre-commit on a 3-file staged change, compare with base and head) with its basis; the migration path from the existing code; and the risks.
Then a recommendation, and the one-day experiment most likely to prove the recommendation wrong.
It was told to read the code where the code decides something, and to cite it.

## What it was given

- The settled behavior: the user guide, the PostHog preset (every rule, label, export, registry, identity and level), the root config.
- The workload notes: repo size, change locality, rule locality, worktree sharing.
- The first implementation's code (Rust and Python), its design page and data model, and its performance measurements.
- The constraints:
  - it runs in CI, as a pre-commit hook on the staged snapshot, and by coding agents, across dozens of git worktrees on one machine;
  - the owner rules out a long-lived process per worktree; on-disk caches and master CI artifacts are allowed;
  - results are deterministic and identical to a clean run;
  - unknown is never clean: anything unresolved is reported, never dropped;
  - the core is generic; a Django pack and a PostHog preset and pack sit on top, and other repos must be able to use the core and the Django pack;
  - a small team of the tool's owners maintains the core; pack and preset authors are Python developers, and the rules change often;
  - developers must not need a Rust toolchain (prebuilt wheel).

It got facts and numbers, not the architect's view.

## The architect's view, written first

The architect wrote this before the round started, and did not share it with the blind architects or the second opinion.

**What the architect thought was wrong:**

- The engine built a complete graph of the repo (1.58M edges), then each rule filtered it. Most edges were never selected by any rule or output: references (456k), where model-crossings needs only uses of models outside their package and public-surface needs only cross-package names; declares and exposes (606k), which the facade rules need only in facade zones. Imports were the exception: test selection and the tach-format map need all of them.
- The Rust/Python line sat right after parsing. Rust wrote a 253 MB JSONL per run, Python turned it into 1.8 GB of dicts and then 12.5M dataclass objects. GC and object construction cost more than the analysis.
- The line was the architect's default on Nov 4, not a decision of the owner. In the Nov 4 spike the Python core built only imports (0.06 s); the cost grew as the core gained references, declarations and exposes.
- Owners labeled every module eagerly (9.3 s), but owners only route findings and ledger lines.
- Staged mode checked out the whole index (22 s), but only staged-modified blobs differed from the content cache.

**Options as the architect saw them:**

- **A. Compute only what is selected.** Each rule and output declares its edge kinds and scope predicates; producers build only those. Keeps the split. Risk: unknown is never clean and levels count unresolved edges, so demand must include them; export-shape needs an on-demand closure.
- **B. Move the generic core into Rust.** Resolution, edges in a compact table, generic selectors. Python keeps the packs, config and rule logic, and reads the table without one object per row. Risk: two languages in the resolver, an interface every pack crosses.
- **C. A first, measured; B only for what is still slow.**

**Expected:** a large share of edges never selected (a guess of over 70%); A as the biggest lever; caching as the last step, not the first.
**Unsure:** whether "compute only what is selected" can be correct without each producer knowing every rule, and whether pack authors can live with a Rust core.

The demand audit later measured 63% unread and refuted A as the main lever (`docs/research/engine-synthesis.md`).

## The second opinion

What follows is the second opinion's answer, cleaned. File references point into Hoglint's modules at the time.

### Recommendation

It recommended **a persistent query engine with Python semantics and native storage**, introduced gradually behind the existing implementation: cache completed analyses and their dependencies across processes; keep Django interpretation and policy in Python; avoid rebuilding the whole graph as Python objects.

The strongest alternative, in its view, was a **native batch engine** that recomputes broadly but cheaply. That alternative deserved a small benchmark before committing to the complexity of persistent queries.

All its timings are engineering estimates grounded in the supplied measurements, not new benchmarks. It read the implementation but did not run it.

### What the implementation established

The language boundary was expensive because it transferred ownership of essentially the whole analysis into Python:

- `FactStore.from_jsonl()` materialized every record as dictionaries and lists (`facts.py`). The measurement attributed about 1.8 GB to loading 253 MB of JSONL.
- `CoreBuild` created scopes for every analyzed file and ran the selected producers into a graph (`core/build.py`).
- Cross-run caching stopped at extraction. `GraphSource` cached graphs only inside one process (`source.py`).
- Pre-commit materialized the whole index for policy, then separately materialized staged Python sources (`cli.py`, `core/discovery.py`). `--changed` filtered after analysis; it was not incremental analysis.

Two limits on shortcuts:

- Export validation recursively follows fields, signatures and ancestors outside export directories (`rules/exports.py`). The measured directory-limited optimization lost 543 findings.
- drives depends on helper bodies and imports, not merely exported names, and it built its fact representation for every first-party file.

So "three changed files" is a useful workload observation, **not a correctness boundary**.

### Decisions common to all three architectures

Use an immutable snapshot abstraction: `list`, `read` and `glob`, backed by a commit tree, the git index, or captured working-tree contents. Read staged blobs with batched git object access. All inputs, including config, owners files, routing, turbo inputs, exceptions and the ledger, come from that snapshot. Detect unmerged index entries as incomplete rather than omitting them.

Share immutable, content-addressed objects across worktrees. Keep snapshot manifests separate from those objects. Cache keys include the relevant parser, semantic implementation, pack dependencies, framework sources and configuration. Publish objects atomically; missing, corrupt or incompatible cache entries cause recomputation. Master artifacts accelerate a matching computation; they never establish cleanliness merely because their commit is nearby.

Separate these computations:

| Compute eagerly | Compute when demanded by an enabled rule or output |
|---|---|
| inventory, parse status, module names, imports and binding summaries | name resolution and re-export chains |
| definition headers, bases, decorators, annotation expression references | class ancestry, model metadata and effective relations |
| compact call, use and body indexes | detailed expression values and local def-use |
| candidate indexes for strings, calls, classes and registry slots | recursive export closure and test-to-helper execution |
| relevant config and provider input fingerprints | findings, detailed witnesses and explanations |

A full check must enumerate **all obligations of every enabled rule**. Demand-driven execution means avoiding irrelevant intermediate work, not ignoring untouched findings.

Keep small aggregate graphs: package dependencies, module imports and wiring obligations. Recompute package SCCs and, initially, module reachability when their inputs change. Do not build all-pairs transitive closure or implement fully dynamic graph algorithms first. The measured global rules cost only about 3.2 s.

Do not retain ASTs for the entire repository, construct universal `declares`/`exposes` edge objects unnecessarily, perform general Python type inference, or boot Django in the hook. Preserve syntax facts and derive the specific semantic relations required. Runtime Django oracles remain a separate CI validation job.

The settled TOML remains the public rule language in every option. The core supplies generic primitives; Django supplies framework interpretation; PostHog supplies its preset, providers and drives semantics.

### Architecture 1: optimized full recomputation in Python

The lowest-risk architecture and the migration baseline.

**Computation and language split.** Rust parses files and caches neutral syntax facts. Python resolves symbols, interprets Django, runs all rules and constructs results on every invocation. Add demand-driven declaration and type expansion within a run, but no persistent semantic dependency graph. Keep the existing generic core versus framework separation: the core produces neutral use positions, with packs supplying framework meaning.

**Data layout.** Replace one large JSONL load with independently loadable fact kinds or file shards. Load compact module summaries eagerly; load bodies and expression details lazily. Intern repeated strings at construction, use small ids and context bitmasks, and avoid duplicate pack-specific copies of facts. Python still owns the semantic graph. Columnar input that is immediately converted to Python objects is not an optimization: the supplied columnar experiment was slower after row materialization.

**Pack extension model.** Ordinary Python producers receive file facts and resolver helpers. The core manages failures. Adding another exact hook name still needs only a `[django].hooks` entry. A new invocation pattern, such as a callback supplied through `receiver=`, is Python code:

```python
def keyword_hook(scope: FileFacts, ctx: PackContext) -> None:
    for call in ctx.hook_candidates(scope, ctx.config.hooks):
        hook = ctx.resolve(call.callee)
        callback = ctx.resolve(call.keyword("receiver"))
        ctx.emit_install(call.site, hook, callback)
```

`hook_candidates` retains unresolved plausible matches, and `emit_install` carries unknown resolution into the result. A failed resolution is not "no edge". Candidate selection must also handle aliases, not only the written callee's last name.

**Unknown handling.** Preserve explicit resolution states and three-valued predicates. A file or producer failure contributes an incomplete obligation. Lazy loading returns "unavailable", never an empty successful collection. The existing item- and producer-level failure handling is a useful foundation.

**Migration.** Replace snapshot materialization first, then cache owners results, reduce object churn, and change fact loading. Keep the resolver, producers and rule implementations. Add lazy export expansion only after differential verification.

**Risks.** It still does repository-sized semantic work for every hook. Memory improves but stays tied to Python objects. A serviceable CI replacement, but a disappointing interactive tool.

### Architecture 2: persistent queries, Python semantics, native storage (recommended)

The unit of reuse is a **completed semantic query**, not an edge and not a whole worktree.

**Computation model.** Representative query keys:

```text
bindings(module)
resolve(module, name)
class_info(symbol)
body_summary(function)
django_model(symbol)
installs(module)
drives(test_module)
rule_findings(rule, source_partition)
package_graph(view)
```

Each cached result includes:

```text
query identity + implementation/config fingerprint
value object hash
completeness status
dependencies: query/input key -> observed value fingerprint
```

On a change, invalidate the queries that read changed inputs, recompute them, and propagate only when their observable result changes. Dependencies include unsuccessful reads and collection membership. Examples:

- Resolving `a.X` records that `X` was absent, including relevant star-import and submodule alternatives.
- A registry query records the result of its path-pattern enumeration, including an empty result.
- `drives(test)` records helper body summaries, patches, dispatcher configuration and registry membership.
- An export query records every reached type, even outside facade paths.
- An owners query records applicable metadata and the discovery of metadata files.

Keep semantic fingerprints distinct from presentation fingerprints: moving a line should refresh locations without invalidating every consumer of a symbol.

Do not make a query for each of 1.58 million edges. Use module or producer result shards, shared symbol queries and function summaries. Begin with coarse dependencies where necessary: "this producer depends on the whole model registry" is safe and understandable.

**Language split.** Rust owns parsing, immutable fact storage, indexed selection, serialization and bulk dependency bookkeeping. Python owns resolution semantics initially, Django and PostHog interpretation and rule orchestration. Native graph or selector operations are added where measured. This avoids rewriting the Python resolver and all Django reasoning before learning whether persistence solves the problem.

**Data layout.** Immutable binary shards with string dictionaries and integer ids; separate site, binding, definition, call and expression tables; tagged resolution values and context bitmasks; offset arrays for candidates, arguments and re-export chains; typed producer-specific attribute columns or sparse side tables; explicit completeness records. An embedded metadata database for manifests and query dependencies; larger values as immutable blobs. Readers should not wait behind a long whole-repo writer transaction.

Across the Python boundary, pass **selected batches and stable handles**, not the whole fact store. Small batches may become ordinary Python records; large filters stay native. Persist portable entity keys separately from shard-local numeric ids. Neither run-local edge ordinals nor interned integer ids become ledger identities.

**Pack extension model.** Python authors write deterministic functions against a tracked context. The context records reads automatically; authors do not write invalidation rules.

```python
@pack.producer("keyword-installs", partition="module", emits="installs")
def keyword_hook(ctx: QueryContext, module: ModuleId) -> EdgeBatch:
    output = ctx.edge_batch("installs")
    for call in ctx.hook_candidates(module, ctx.config("django.hooks")):
        hook = ctx.resolve(call.callee)
        callback = ctx.resolve(call.keyword("receiver"))
        output.install(call.site, hook, callback)
    return output.finish()
```

`ctx.config`, candidate enumeration, expression access and resolution all register dependencies; empty results do too. The supported pack contract forbids undeclared filesystem, environment, clock or network inputs. Unported producers run with a coarse snapshot-wide dependency and recompute on any snapshot change. Producer fingerprints include their code and supporting package dependencies; correctness must not depend on remembering to bump a handwritten version.

The existing pack interface exposed the whole mutable graph and the core build (`plugins.py`). Replacing it is the main architectural change.

**Unknown handling.** Completeness is part of the cached value. A cached empty collection is valid only with proof that its producer completed and its input enumeration is still valid. Failed reads, opaque expressions and recursion limits yield explicit unknowns.

Resolution cycles need care. The resolver deliberately avoids caching results met through a cycle (`core/symbols.py`). Persistent queries must keep that behavior or introduce a well-defined SCC or fixed-point computation; they cannot persist an incidental recursion-stack result. If dependency coverage is uncertain, invalidate a broader partition or recompute everything. Never reuse speculatively.

**Migration.**

1. Introduce the snapshot API and a shared immutable fact store.
2. Put existing producers behind query contexts with coarse dependencies.
3. Persist module-level producer results and local rule findings.
4. Add tracked symbol, body, registry and provider reads.
5. Stop materializing the full Python graph; keep aggregate projections and load witnesses as needed.
6. Keep a cache-disabled execution path as the differential reference.

**Risks.** Missing negative dependencies, accidental untracked pack reads, expensive dependency validation, and caches that grow faster than their benefit. The workload's changed-name counts do not prove the semantic query frontier will be small. Broad drives matching and framework registry dependencies may dominate.

### Architecture 3: native batch analysis with Python-authored plans and semantic plugins

More native work for simpler execution: recompute broad analyses efficiently instead of tracking fine-grained reuse.

**Computation model.** Load compact cached facts, build native symbol and index structures, resolve and evaluate generic primitives in batches. Build only the relation families required by active rules, but recompute those families across the snapshot. Python packs either describe a native execution plan or consume selected fact batches and emit typed semantic batches.

**Language split.** Rust owns more than storage: the generic resolver, selector evaluation, rule primitives and graph algorithms. Python owns the configuration front end, framework semantics, providers and unusual rules. A new generic primitive may require a native release; a new Django hook should not.

**Data layout.** Bulk facts and edges stay in native arenas or columnar tables throughout the run. Python submits plans and receives selected rows or finding batches. Typed status columns, not unresolved endpoints as nulls that disappear in joins. Do not wrap millions of native edges in millions of Python proxy objects; that recreates the original problem.

**Pack extension model.** A small typed Python builder for common patterns, and a batch Python escape hatch:

```python
pack.add_hook_pattern(
    HookPattern(
        callees=ConfiguredHooks("django.hooks"),
        forms=("call",),
        callback=KeywordArgument("receiver"),
        emit="installs",
        unresolved="emit",
    )
)
```

The builder produces data describing operations, not arbitrary Python compiled into Rust. A more complex pattern is a Python batch producer over the same typed fact schema. The pattern library must stay small: if authors need a second general-purpose query language for ordinary framework behavior, this architecture has missed the maintenance constraint.

**Unknown handling.** Native filters use the same three-valued logic as Python. Candidate-producing joins keep unresolved rows. Python batch failures invalidate the declared output family. Unsupported plan operations fail configuration or use an explicit Python implementation; they never become empty output.

**Migration.** Keep Python semantics as the reference. Introduce native storage, then port generic selection and grouping, graph algorithms, and resolution independently. Adapt packs to batch inputs. Compare each port differentially before switching.

**Risks.** The broadest rewrite, two semantic implementations during migration, and a Python SDK that could slowly become a bespoke language. Small resolver changes become wheel releases. Irregular Python pack logic may still dominate after the generic engine becomes fast.

### Cost by mode

The supplied measurements give a warm-extraction full check of 44.7 s, about 9.3 s for owners, 11.2 s for rules and about 5 s for the packs. The individually measured optimizations suggest roughly 25 s, but their combined gain was not measured. Budgets assume comparable hardware and reusable owners results and syntax facts. "Full" means all obligations evaluated with no prior semantic results.

| Architecture | Full semantic check, F | Warm staged change, 3 ordinary files | Compare, unchanged scanner and policy, no base artifact | Compare with an exact reusable base |
|---|---:|---:|---:|---:|
| 1. Python full recomputation | 25 to 35 s | 25 to 35 s | about 2F: 50 to 70 s | about F: 25 to 35 s |
| 2. Persistent Python queries | 20 to 35 s | **2 to 6 s target** | about F + incremental: 22 to 41 s | **2 to 6 s target**, plus loading and diffing summaries |
| 3. Native batch | **8 to 15 s target** | 8 to 15 s | about 2F: 16 to 30 s | about F: 8 to 15 s |

- **Option 1:** chiefly measured optimizations. Removing the index checkout removes about 22 s, but does not make analysis local.
- **Option 2:** warm execution pays snapshot and manifest work, a small semantic frontier, aggregate graph work and rendering. The measured 3.2 s of global rules alone prevents assuming sub-second runs. Cold execution still does Python semantic work and writes query metadata.
- **Option 3:** several seconds for Python packs, several for native loading, resolution and rules, plus discovery and reporting. More speculative than option 1.

For a cold cache, add parsing and storage costs and owners: allow roughly another 10 to 15 s at first. Memory: option 1 stays at GB scale; options 2 and 3 should target below 1 GB of private process memory, to be measured with concurrent worktrees.

**Compare is not simply two runs when policy or scanner changes.** Keep:

```text
base code x base scanner/policy
head code x base scanner/policy
head code x head scanner/policy
base code x head scanner/policy
```

The first three support growth attribution; the fourth supports policy-effect comparison. A broad scanner change can require up to four substantial evaluations in any architecture. Cache reuse must be fingerprint-specific. Keep published base-scanner wheels and run incompatible scanner versions in separate short-lived processes.

These figures cover the static engine only. A full CI gate also pays runtime-oracle cost, artifact transfer and environment startup, which should be reported separately.

### The correctness contract

All three architectures need the same guarantees:

- Unknown and known-none are different values. Only a definitely false selection can exclude an obligation.
- Candidates keep their uncertainty; unresolved cases cannot disappear through filtering, grouping or deduplication.
- Parse failures, missing framework inputs, truncated expressions and exhausted analysis budgets stay visible.
- Findings, counts, failure states and witnesses are deterministic. Stable sorting and canonical witness selection must not depend on thread scheduling or cache history.
- Accepted debt never removes impact edges. Unknowns still prevent Isolated eligibility.
- Cache-disabled and cached runs produce identical normalized results for the same complete run envelope.

One discrepancy not to copy: `RunResult.exit_code()` let inherited incompleteness not block, while the settled guide said incomplete yields exit 2. The implementation is a migration reference, not the final behavioral authority.

### The one-day experiment

Choose option 2, with option 1 as the first stage. The most useful experiment is **a persistent dependency trace for drives**, not a faster parser benchmark. Wrap its fact, import, helper-body and registry reads; persist per-test-module results and dependencies; restart the process between runs; use coarse dependencies where instrumentation is incomplete. Exercise:

1. a three-file body-only change;
2. a helper that starts executing wiring without changing its name or signature;
3. a new definition that satisfies a previously unsuccessful lookup;
4. a registry member addition or removal;
5. a partially staged change with conflicting unstaged content.

Compare every incremental result against a cache-disabled full recomputation: findings, counts, unknowns, affected wiring and witnesses. Reject or narrow the recommendation if safe dependency tracking repeatedly pulls most of the repository into ordinary small changes, or if validating and loading the persisted dependencies costs nearly as much as rebuilding. Any mismatch is an immediate correctness failure.

drives is the best stress case because it combines body sensitivity, cross-file traversal, registry membership and heavy Python allocation. If persistence cannot make it exact and cheap, the native batch architecture becomes much more attractive.

## What was taken

From the decision log (`docs/history/decision-log.md`):

- **Not taken: the recommendation.** Its option 2 became candidate C3. The correctness adversary showed the Python resolver made reads no query boundary saw (module existence, the set of repo heads, PEP 562 maps, memos), and 82 re-export-cycle results depended on query order. D5: every run evaluates the whole repo; C3's tracked dependencies cost more to keep correct than they save.
- **Taken: its fallback, the native batch engine.** It became C2 with the blind designs and the skeptic. D1 put resolution, ancestry, candidate indexes and graph work in Rust, and kept packs, shapes, labels and predicates in Python. D2: coarse calls and handles, no Python object per row ("do not wrap millions of native edges in millions of Python proxy objects").
- **Taken: the common decisions.**
  - The snapshot abstraction with every input from the snapshot, and unmerged entries as incomplete: D4, later D33 and D40.
  - Content-addressed facts shared across worktrees, a miss on any mismatch: D3, D4, D28.
  - The four compare passes, and base scanners run as pinned separate processes: D9, D15.
  - The correctness contract (unknown versus known-none, candidates keep uncertainty, budgets stay visible, deterministic witnesses): D8, D9.
  - The cache-disabled path as the differential reference: D7 (the Python implementation as the oracle).
- **Taken later: the shape of the reuse question.** Dependencies on unsuccessful reads and on collection membership, and "never reuse speculatively", came back on Nov 10 and 11 when stored answers with traces were weighed (D59, `docs/research/second-opinion-reuse.md`).
