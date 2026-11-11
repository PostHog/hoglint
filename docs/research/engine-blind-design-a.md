# Engine round: blind design A

> A design written blind from the requirements, without the code or earlier proposals, by an architect agent in the engine round, Nov 9, 2026.
> Replaced: the engine round's decision is in `docs/research/engine-synthesis.md` and `docs/history/decision-log.md` (D1-D13); the engine as built is in `docs/design/05-engine.md`.

The brief asked for the engine of hoglint: how source code becomes findings, in which language, with which data layout, and what is computed at all.
The user-facing behavior and the config were settled; a prototype engine existed, but the two architects did not see it and worked independently.
The engine had to produce findings with line-free identities, exit status 0, 1 or 2, ledger equality, growth by attribution across two engine versions, `explain`, `query --by owners`, `compare` against the merge base, the Isolated level per product, and a module dependents map for CI test selection; it had to be deterministic, give the same result from any cache state, and never treat unknown as clean; it had to serve CI, pre-commit on the staged snapshot, coding agents and interactive commands, across dozens of git worktrees on one machine with no long-lived process; its core had to stay generic, with Django meaning in a Django pack and PostHog facts in a preset and a small PostHog pack; and developers must not need a Rust toolchain, so any native part ships as a prebuilt wheel.
The inputs were the user guide, the PostHog preset and root config, and a workload study of the repo (`docs/research/user-guide.md` is the public user guide).

---

Number tags: **(m)** measured by the architect on Nov 9, 2026, on the PostHog checkout at `d1051a38` (28,830 tracked `.py` files). **(w)** taken or computed from the workload study. **(e)** estimated. Scripts and raw output: (not published).

The architect opened none of the paths the brief ruled out.

## 1. Summary

1. One short-lived process per command. A Rust core, shipped as a PyO3 wheel, indexes the code. Python owns all meaning: config, labels, packs, rules, ledger and outputs.
2. Rust parses each file with ruff's parser into a fixed, generic catalog of syntax facts. Facts depend only on the file bytes, so one store, keyed by git blob id, serves all worktrees.
3. Every run rebuilds the whole-repo state from those facts: modules, name resolution (surface, via, origin), class ancestry, edges and the import graph. Nothing above the per-file facts is incremental.
4. The Django and PostHog packs are Python. They query the index ("calls whose callee resolves to X", "classes under Y") and add domain edges, symbol kinds and gaps.
5. Python interns label vectors into label classes. Rust groups edges by projected class, and Python evaluates each rule predicate once per group, in three-valued logic. Only edges in failing groups cross into Python.
6. Every rule runs over the whole snapshot on every run. "On demand" only decides which facts Python materializes, never which files or rules count.
7. Unknown is a value. Unresolved ends carry unknown labels, no deny passes them, and each failed analysis becomes a named gap that sets exit 2.
8. A snapshot is a list of (path, blob id) from the index, a commit or the working tree. Pre-commit reads staged blobs from the object store.
9. compare runs the base engine in-process when it equals the head engine. Otherwise it runs it as a pinned subprocess that returns growth sums only.
10. Cost: CI full check about 6-8 s cold; pre-commit and agent runs 2-3 s; about 0.3 s when nothing changed (all e).

## 2. Computation model

```
git ─► S0 snapshot ─► S1 extract (Rust, per blob, cached) ─► S2 link (Rust, whole repo)
                                                               │  index (Rust memory)
        Python: S3 packs  ◄── queries / pack edges ───────────┤
                S4 labels ─── class arrays ─► Rust group-by ──┤
                S5 rules  ◄── groups, selected edges ─────────┘
                S6 ledger, exceptions, levels, exit ─► S7 command output
```

| Stage | Language | Scope | When |
|---|---|---|---|
| S0 snapshot: (path, oid) of every discovered `.py` file and every policy input (TOML, YAML, JSON) | Rust, git CLI | whole | eager |
| S1 extract: `FileFacts` per blob | Rust, parallel | per file, cached | eager, misses only |
| S2 link: modules, bindings, resolution, ancestry, core edges, graph | Rust | whole | eager, every run |
| S3 packs: domain edges, symbol facts, gaps | Python over index queries | whole | eager, every run, ordered by declared `provides`/`needs` |
| S4 labels: module and symbol labels, label classes, provenance | Python | whole | eager |
| S5 rules: every rule of every primitive | Python + Rust group-by | whole | eager |
| S6 ledger, exceptions, levels, exit status | Python | whole | eager |
| S7 report, `--write-ledger`, dependents map, explain trace, owners query | Python | per command | on demand |

On demand inside the eager stages: signatures, type trees, class bodies, use chains, witness edges and pack per-file AST passes. The engine computes each request set from the whole snapshot, so it is never "the changed files".

**S1 catalog** (generic, no Django names). Per file: parse status. Imports with context bits (type_checking, function_local, conditional, class_body). Module and class bindings: def, class, assign, annotated assign, `type` alias, star import, `__all__`, PEP 562 `__getattr__` with literal maps. Classes: bases, keywords, decorators, body statements as literal or call summaries, nested classes. Functions: decorators, parameters and return as type-expression trees, body kind (trivial or not). Calls: callee chain and argument summaries (literal, name chain, one-level container of those, other). References to module-bound names with a use chain: the ancestor path from the name to its statement (attribute, call, subscript, argument-of, with, decorator, annotation, isinstance, except, base, assignment target), plus one hop of local def-use. Identifier-like string constants with their enclosing call. Module-level literal values. String annotations are parsed as expressions. Facts hold no path, so a renamed file hits the cache.

**S2 link.**
- Modules come from snapshot paths and `[discovery]`. Namespace packages count.
- Bindings follow imports, defs, assignments, star imports (a literal `__all__` wins) and PEP 562 literal maps. A module with `__getattr__` and no readable map resolves missing names to `unknown(dynamic-getattr)`.
- Each import, reference and dotted string gets `surface`, `via` (hops with file:line) and `origin` or `unknown(reason)`. Names outside first party and `discovery.external` become opaque external nodes labeled with their top-level package. Django and DRF sources are extracted and linked like first-party code.
- Class ancestry over resolved bases carries a certainty: `yes`, `no`, or `maybe` when a base is unknown or opaque.
- Core edge kinds: `import`, `reference`, `dotted-path`, `declares`, `dynamic-import` (stdlib importlib with a computed argument), `registers` (`[registries]` loader calls to each module that matches the convention).
- Graph: the module import graph in CSR form, one adjacency per context mask. A reverse re-export map (origin → surfaces) feeds remedies.
- Feasibility: a 150-line Python binder resolves 209,729 first-party `from` imports in 0.30 s and leaves 177 unresolved, 0.08 % (m). Most of the 177 are PEP 695 `type` aliases that the quick binder skips.

**S5 evaluation by group.** Each rule names some src, dst and edge fields. Python projects the label classes onto those fields (an int array). Rust groups the rule's edges by (projected src, projected dst, kind, context, groupable edge attributes) and returns the keys with counts. Python evaluates the compiled predicate once per group. A predicate that names a field only Python knows (`edge.shape`) is split by partial evaluation: false drops the group, true takes it, a residual fetches its edges for a per-edge check. 215,926 first-party import edges fall into 6,171 groups; a Python predicate takes 28 ms over the edges and 1 ms over the groups (m). On Nov 9 there were 825 path-label classes before owners, over 108 packages (m).

| Primitive | Rules | Evaluation |
|---|---|---|
| edge | the direction and layer rules, get-model, reverse-accessor, receiver-direction, installs, drives, facade-logic, dynamic-imports, migration-database | group, predicate per group; `view` sets the identity grain (module pair, package pair) |
| entrypoint | public-surface | group on the `surface` end. `dst.exported` is a pair field: the surface's export set checked against the audience predicate on src labels. An unknown surface follows `on_unresolved`. |
| reach | contracts-no-django, health-checks-no-dags | Python turns `deny` into a module bitmask. Rust runs a BFS per source over the context-filtered graph. It returns the first denied module, the shortest witness path (sorted adjacency, so deterministic) and every unknown node it touched. |
| signature | facade-signature, orm-independent | Python walks the type trees of public callables in selected modules. Leaves resolve to symbols with kinds. A missing annotation, `Any`, bare `dict` or `dict[str, Any]` is unknown and follows `on_unknown`. |
| export | export-shape | Each exported surface is followed to its origin and classified against the shape policy. The walk recurses into fields, signatures, bases and re-exports with a visited set. `unknown = "error"` turns an unclassifiable shape into a finding. |
| use-shape | model-crossings, atomic-outside-views | group on the `origin` end, then fetch the use chains of the selected references. The core names generic shapes (call, with, decorator, annotation, isinstance, except, base). The Django pack refines model shapes (scalar-chain, subquery, instance-many(filter)) and provides `django.uses`. Counts are per (target, consumer module, shape). |
| coverage | wiring-watched | Python builds the required paths from rule outputs (`rules.drives.dst.path`) and edge projections. It reads `turbo.json` inputs through the turbo adapter and reports each uncovered path. |
| cycle | no-new-package-cycles | group to package pairs, Tarjan SCC in Python. Each pair inside an SCC is a finding with sample witnesses. The ledger holds the current pairs. |
| layout | product-code-at-home, own-database, registry-slots | predicates on module or symbol labels; registry-slots also reads the slot binding and the members' ancestry |
| inventory | featureflag-relations, generated-producer | the selected set against the classification file; missing and stale entries both fail |
| level | isolated | Per product: every required rule is complete for it, no unledgered finding has it at either end, no unknown edge starts or ends in it, and strict rules are at zero even when ledgered. |

**S6 ledger.** Python renders the ledger text from the findings: one sorted line per identity, with its count. Equality is a line diff against the ledger blob in the same snapshot, so pre-commit compares the staged ledger. Exceptions match on identity columns and are applied before the diff.

**compare.** An engine is (the `hoglint-core` version that `uv.lock` pins, the tree oid of the engine and pack Python sources). A policy is the config files at a commit. compare evaluates four (code, policy) pairs:

| Pass | Code | Policy and engine | Gives |
|---|---|---|---|
| 1 | head | head | the check itself: equality, new findings |
| 2 | base | base | growth sums, base side |
| 3 | head | base | growth sums, head side: code growth fails |
| 4 | base | head | findings removed on base code: a relaxation that needs review by the tool's owners |

Rule growth is pass 1 minus pass 3, listed in the PR. When the base engine equals the head engine, all passes run in one process: two links (base code, head code), two pack runs, four label and rule passes. Otherwise the head process materializes the base engine with `git archive` into `engines/<tree-oid>/` and runs passes 2 and 3 through `uv run --no-project --with hoglint-core==<base pin>`. That subprocess returns `scan-result` v1 growth sums, the only cross-version contract. Growth keys use stable target ids: the model's table name, the package id, or a path mapped through `git diff -M base head`.

## 3. Language split

| Part | Language | Reason |
|---|---|---|
| S0, S1, fact store | Rust: ruff_python_parser, rusqlite, zstd | Parse and walk take 7 s single-threaded (w). CPython `ast.parse` alone takes about 29 s, and a Python extractor about 45 s (m, from a 10 % sample). |
| S2, group-by, reach, dependents map | Rust | Keeps about 3M fact records out of Python. Decoding a smaller fact set into Python objects costs 2.6-3.0 s per run (m). |
| Config, expression language, labels, packs, primitives, ledger, compare, explain, query, CLI | Python | Changes often. Python developers write it. |

Why not pure Python: it works, with a warm floor near 4-5 s per run (fact decode 2.6 s (m), link 0.3-0.5 s (m), rules) and cold extraction of 12-14 s wall with many processes in parallel (m). That is the plan B if the wheel pipeline fails. Why not more Rust: packs and rules change weekly, and their authors write Python.

The boundary is PyO3, in process. The native module ships as abi3 wheels for macOS and Linux (manylinux), built by maturin in CI. `uv.lock` pins it with hashes. Python checks `hoglint_core.CATALOG_VERSION` on import and exits 2 on a mismatch. Only the tool's owners need a Rust toolchain. A core change lands in two steps: publish the wheel, then bump the pin together with the Python side. Type stubs (`.pyi`) ship in the wheel, so pack authors get completion.

| Crossing | Direction | Format | Volume per run | Cost |
|---|---|---|---|---|
| Snapshot spec, discovery config | Py → Rust | one call | small | ~0 |
| Module table | Rust → Py | columnar lists | 29k rows (w) | 30 ms (e) |
| Pack queries | Rust → Py | frozen pyclasses | 20-50k records (e) | 50-100 ms (e) |
| Label class arrays | Py → Rust | `array('I')`, buffer protocol | ~430k ints (e) | <5 ms (e) |
| Pack edges | Py → Rust | int tuples | 20-30k (e) | 20 ms (e) |
| Groups, selected edges, witnesses | Rust → Py | tuples | 10-20k groups, ~60k edges (e) | 100 ms (e) |
| Base engine growth sums | subprocess → Py | JSON, versioned | 10-60k keys (e) | 50 ms (e) |

## 4. Data layout

**Rust, in memory** (all ids are u32, assigned in sorted path order, so they are deterministic):
- One string interner.
- Modules: path, dotted name, oid, is_package, parse status, `Arc<FileFacts>`.
- Symbols: module, qualname, kind (module, class, function, variable, type alias), line, parent. About 400k rows (e).
- Edges as columns: kind u8, context bits u8, src module, src scope, surface, origin (or an unknown sentinel plus a reason id), via offset into a hop arena, line, column, attribute offset (pack attributes, use-chain id). 2-3M rows, about 32 bytes each (e).
- Ancestry: per class, sorted ancestors with certainty.
- Graph: CSR adjacency per context mask.
- Peak process memory 300-600 MB (e).

**Python, in memory:** config dataclasses with file:line provenance per entry; compiled predicates (closures, no `eval`); `dict[label tuple, class id]`; `array('I')` maps from module and symbol to class; findings; gaps. `tomllib` gives no positions, so the loader maps each table and key to its line with a scan of the same text. Each label value keeps the entry that set it and the entries it overrode, which is what `explain` prints.

**On disk, shared by all worktrees** (`$XDG_CACHE_HOME/hoglint/`):
- `facts-<extractor-build-id>.sqlite`: `facts(oid BLOB PRIMARY KEY, data BLOB)`, data = zstd(postcard(FileFacts)) with a checksum. WAL mode. A comparable Python fact set is 147 MB raw, 44 MB zstd and a 78 MB SQLite file (m). Expect 40-80 MB per repo state, and not much more across all worktrees, because most file contents are shared.
- `perfile-<pack>-<pack-digest>.sqlite`: results of pack per-file AST passes, keyed by oid.
- `results/<run-key>.json.zst`: the memo of complete run results (section 6).
- `engines/<tree-oid>/`: base engine sources materialized with `git archive`, read-only.

**In the repo:** `hoglint.ledger` (sorted lines, tab-separated identity columns, count) and `hoglint.exceptions.toml`. Outputs: findings JSON (`scan-result` schema v1, also the cross-version contract), the dependents map in tach's `{file: [files that import it]}` JSON, and per-product levels JSON for turbo.

## 5. Modes and cost

Component costs on a warm fact store:

| Component | Cost | Basis |
|---|---|---|
| Python start, imports, native module | 0.15 s | 0.11 s for Python with stdlib imports (m), plus load (e) |
| Snapshot: index / commit tree / working tree | 0.06 / 0.56 / 0.10 s + hashing of dirty files | `git ls-files -s`, `git ls-tree -r`, `git status --porcelain=v2` (m) |
| Fact load, 29k blobs | 0.2-0.4 s | Python SQLite point reads 0.40 s, zstd 0.28 s, single thread (m); Rust batches and decodes in parallel (e) |
| Extract a miss | 0.35-0.5 ms CPU per file | 7 s parse+walk for 29k files (w), ×1.5-2 for encoding (e) |
| Link | 0.3-0.6 s | Python binder 0.30 s for `from` imports (m); Rust does more work, faster (e) |
| Packs | 0.3-0.6 s | 20-50k records (e) |
| Labels | 0.2-0.3 s | path labels 0.22 s (m), symbol classes (e) |
| Rules | 0.5-1.0 s | group-by economics (m), signature, export and shape walks (e) |
| Ledger render and diff | 0.1 s | about 55k lines (w) |

| Mode | Cost | Basis |
|---|---|---|
| CI full check, cold store | 6-8 s on a typical parallel CI runner | Extraction of 10-14 s CPU (e) scales about 4x on such a runner; 7 s single-threaded dropped to 1.7 s fully parallel (w). So 3-5 s wall (e), plus 2.5 s of the rest. CI uses no cache. |
| Local full check, cold store | 5-6 s | same, on a developer machine |
| Pre-commit, 3 staged files (median 3, p90 14 (w)) | 2-3 s | the component sum; 3 misses cost about 1 ms |
| Pre-commit, 13,582-file sweep (w) | 4-6 s | plus about 6 s CPU of extraction, run in parallel (e) |
| Agent check, working tree | 2-3 s; 0.3 s on a memo hit | git status 0.10 s (m) |
| compare, one engine | 7-10 s warm | 2 snapshots, 2 links, 2 pack runs, 4 label and rule passes: (head code, head policy), (base, base), (head, base), (base, head). The last finds relaxations. |
| compare, two engines | 12-18 s | plus a base engine process for 2 passes; plus 3-5 s if its extractor differs (e) |
| explain a path label | 0.3-0.5 s | config and path only |
| explain a symbol label or edge | 2-3.5 s | warm run plus a predicate trace |
| query --by owners | 0.3-0.5 s on a memo hit, else 2-3.5 s | findings and ledger grouped by the `owners` label of the primary subject |
| Dependents map | 0.8-1.3 s warm, 4-6 s cold in CI | S0-S2 and a JSON write. tach took 2.7-3.6 s on Nov 9 (w). |

For reference: the prototype took about 45 s full and 70 s pre-commit, and the Django-booting checks took 267-278 s (w). The design is not fast only when warm: a cold local run is 5-6 s (e).

## 6. Caching and sharing

| Cache | Key | Value | Scope |
|---|---|---|---|
| L1 facts | (extractor build id, blob oid) | FileFacts | global, all worktrees |
| L2 per-file pack passes | (pack source digest, blob oid) | pack facts | global |
| L3 run memo | H(engine id, policy digest, snapshot digest, external digest, `today`) | complete result: findings, gaps, ledger text, levels | global |

Nothing else is cached: link, packs, labels and rules run every time. Invalidation by imports would not pay off, because a change's transitive importers number 18,182 at the median (w). Name-level invalidation would touch only 5 modules at the median (w), but star imports, `__all__` and PEP 562 make it subtle, and a full link costs under a second (e).

Why a cached result equals a clean run:
- Each cached value is a pure function of its key, and the key covers everything the function reads. The extractor build id hashes the crate source, `Cargo.lock` and the rustc version at compile time. The pack digest hashes every file of the pack package. The engine id combines the core build id with the digest of the Python engine files actually imported. The snapshot digest covers every `.py` and policy oid. The external digest covers the Django and DRF files in the environment. `today` is explicit, because exception `review_by` dates depend on it.
- Pack per-file functions receive only an `ast.Module`, never the index, so their purity holds by construction.
- Entries carry a checksum and the build id. A bad entry is a miss, never a gap and never a result.
- Determinism: ids follow sorted paths. Outputs are sorted by identity. Witnesses are the smallest (path, line, column). Pack outputs are sorted before insertion, so Python set order cannot leak.
- Checks: CI always runs cold and is the reference. A nightly job runs warm and cold on the same commit and compares the bytes, and runs twice with different `PYTHONHASHSEED`. `hoglint check --no-cache` is always available.

Sharing: worktrees share the object store, so a miss reads the blob with one `git cat-file --batch` process. A fact one worktree extracts serves every other worktree. Writers insert their misses in one transaction with insert-or-ignore. A writer that times out on the lock skips the write; the cache is optional. Old `facts-*` files expire after 30 days. A CI artifact from master is unneeded, because cold extraction takes 3-5 s (e).

## 7. Unknown is never clean

- **Values.** Every label lookup returns a value, `none` (outside the field's domain) or `unknown`. Predicates use Kleene logic. A deny or select that comes out unknown follows the rule's `on_unresolved` or `on_unknown`: `finding`, `incomplete` or `block` (a finding the ledger cannot accept). The default is `incomplete`. An `allow` that is unknown does not allow.
- **Unresolved names** stay in the edge table with `dst = unknown(reason)`. Their group has the unknown dst class, so it never vanishes from a group-by.
- **Gaps** are records: stage, subject (file, module, symbol, package, rule, adapter, engine), what was not computed (`edge:signal`, `label:kind`, `rule:model-crossings`), and the reason. Examples: a parse error in a file; a Rust panic in one file's extraction (caught per file); a pack analysis that raised; a declared `expected` kind with zero members; a git index with conflict entries; a base engine that would not start.
- **Gap to rule.** A rule is incomplete when a gap subject can fall inside the rule's scope (its src predicate is true or unknown on the subject's labels), or when the gap's `what` meets the rule's needs. The needs come from the config, not from runtime: the rule's kinds, the fields it names and its `requires`. The engine refuses at load time a field or kind that no pack declares, so a rule cannot silently read an empty fact set.
- **Laziness.** No rule or file is ever skipped. On-demand fetches take sets that the whole-snapshot pass computed. A use-chain fetch that fails turns its group into a gap, not a smaller group.
- **Two derivations for models.** The Django pack builds the model index from ancestry and checks it against the static migration state (CreateModel per app label). The architect read `oracles = ["registry", "migration-loader"]` as these two static oracles; neither boots Django. A `maybe` class with a CreateModel is a model. A `yes` class with no migration and no `abstract` is a gap. Disagreements are named, not dropped.
- **Levels.** Isolated needs complete required rules for the product and no unknown edge at either end, so an unknown can only withhold the level.
- **Exit.** 2 if any rule is incomplete or compare could not run an engine. Else 1 if any finding is unledgered and not excepted, the ledger differs, or the code grew debt. Else 0. Every gap prints with its subject and reason.

## 8. Extension model

Packs declare analyses. An analysis reads the index and yields `Edge`, `SymbolFact` and `Gap` records. It declares `provides` and `needs`, and the engine orders analyses from those. Tests use `hoglint.testing.Repo({path: source})` and assert ledger lines.

**(a) A preset rule.** TOML only:

```toml
[rules.tasks-no-presentation]
primitive  = "edge"
doc        = "Celery tasks never import presentation code."
view       = "zones"
from       = "src.zone == 'tasks'"
deny       = "dst.zone == 'presentation'"
contexts   = { exclude = ["test"] }
identity   = "module-pair"
growth_key = ["dst.module"]
```

Then `hoglint check --write-ledger` admits the current debt, and the tool's owners review the change. The author must know the field vocabulary, the primitives, the three-valued `unknown`, and how identity and growth key shape the ledger. `hoglint explain <edge>` shows the predicate trace for one case.

**(b) A new Django pattern.** Example: a hook that registers subclasses through `__init_subclass__`, so defining a class installs it.

```python
# hoglint/packs/django/hooks.py
from hoglint.pack import Edge, Index, analysis

@analysis(provides=["edge:installs"], config="django")
def subclass_hooks(ix: Index, cfg: DjangoConfig) -> Iterator[Edge]:
    """Defining a subclass of a hook base installs it."""
    for base in ix.symbols(cfg.hook_bases):            # unknown names fail at load with a config error
        for cls, certainty in ix.subclasses_of(base):  # "yes" or "maybe"
            if certainty == "maybe":
                yield Edge.unknown("installs", src=cls, dst=base, reason="base not resolved")
                continue
            yield Edge("installs", src=cls, dst=base, at=cls.site, label=base.name)
```

The author adds `hook_bases` to the pack's config dataclass and lists the bases in `[django]`. They must know the Index API, the direction of an edge (src is the code that couples, dst is what it couples to), and the duty to yield an unknown edge or a gap for every shape they cannot read. If the catalog lacks a syntax fact, they use the escape hatch:

```python
@per_file(name="django.dict-hooks", select="tier != 'leaf'", version=1)
def dict_hooks(tree: ast.Module) -> list[tuple[str, str, int]]:
    """{'kind': callback} dicts passed to a hook call; resolved later by an analysis."""
    ...
```

The result is cached by blob. The tool's owners promote a stable escape hatch into the catalog.

**(c) A PostHog-only fact.** Example: a test that names a query kind drives the product runner behind it. API and PostHog names in the sketches are illustrative.

```python
# hoglint/presets_py/posthog/drives.py
@analysis(provides=["edge:drives"], needs=["label:testcode"])
def query_kind_drives(ix: Index, cfg) -> Iterator[Edge | Gap]:
    runners = dispatch_table(ix.literal("posthog.hogql_queries.query_runner", "RUNNERS"))
    if runners is None:
        yield Gap("posthog.drives", what="edge:drives", reason="core dispatch table is not a literal")
        return
    tests = ix.modules(where="testcode == true")
    for site in ix.strings(values=runners.keys(), modules=tests):
        yield Edge("drives", src=site, dst=runners[site.value], label=site.value, execution="kind-string")
```

The module registers with `PACK = Pack("posthog", section="posthog", edge_kinds=["drives"], analyses=[...])` and loads through `plugins`. The author must know the same API plus which PostHog convention the fact encodes. The core and the Django pack never import it, so another repo installs `hoglint` and gets the core plus `hoglint:django` alone.

## 9. Risks

**Riskiest assumption: a fixed, generic Rust catalog carries all Django and PostHog meaning.** If packs keep needing new syntax facts, every rule change waits on a wheel release. The tool's owners become the bottleneck, and the split fails socially. Use-shape with def-use (model-crossings) is the hardest case.

One-day experiment:
1. Morning: write the catalog as Python dataclasses and a throwaway `ast` emitter for it. Save it as the future differential oracle for the Rust extractor.
2. Afternoon: port four analyses as pack code that reads only the catalog through an Index stand-in: the model index with relations and reverse accessors, get-model, signal receivers, and model-crossings shapes with def-use.
3. Run on master and diff the ledger lines against the output of `hogli product:crossings` and the existing ratchets.

Expected: reverse-accessor 242/242, get-model 246/246 and receiver-direction 60/60 lines equal (w: the counts on Nov 9). model-crossings: at least 98 % of 1,353 lines equal (w), each difference explained. At most 3 catalog fields added. No analysis needs the raw AST. The catalog is at most 60 MB zstd (m: 44 MB for a cruder set). The four analyses pull at most 50k records. Kill signal: more than 5 catalog additions, or model-crossings needs the raw AST. Then use-shape moves to a Python per-file pass, cached by blob: at most +45 s CPU cold (e, bounded by the full Python extractor (m)), warm cost unchanged.

Other risks:
- **Linker precision.** Every unresolved name is a finding or an incomplete rule, so a weak linker floods users. Low risk: 177 of 209,729 unresolved with a quick binder (m).
- **Wheel release friction.** A core change needs two PRs, and old base engines need their wheel in uv's cache or on the index. Unknown pins exit 2, never pass.
- **Partial clones in CI.** compare needs base blobs. Only blobs that differ from head are read, but a blobless clone must fetch them in one batch.
- **Memory with many concurrent hooks.** 300-600 MB per process for 2-3 s (e). Extraction threads scale with misses, so a 3-file commit uses one thread.

## 10. What not to build

- A daemon, LSP or watch mode (ruled out), or a long-lived index process.
- Incremental link or rule evaluation with dependency tracking. Transitive importers cover the whole repo for most commits (w), and a full pass takes about a second (e).
- A CI fact artifact from master. Cold extraction takes 3-5 s, and an uncached CI is the reference run.
- A pure-Python runtime fallback for the extractor. The Python emitter lives in tests as an oracle only.
- Django boot in any check. A runtime cross-check of the static oracles could run nightly, if the static ones ever disagree with reality.
- Type inference or a call graph beyond one hop of def-use and the bare-name call resolution that `drives` asks for.
- A Datalog engine, DuckDB, Arrow or numpy. The expression language and a group-by cover the primitives.
- Rule logic in Rust, or Django names in the core.
- `hoglint plan` until the engine is stable.

## Appendix: measurements (m)

| What | Result |
|---|---|
| CPython `ast.parse`, 10 % sample (2,883 files, 25.8 MB), single-threaded | 2.93 s, so about 29 s for the repo |
| Python extractor, same sample, single-threaded | 4.46 s, so about 45 s for the repo |
| Python extractor, all 28,830 files, many processes in parallel | 11.7-13.8 s wall |
| Python facts: imports, defs, calls, strings, uses | 333k, 291k, 1.49M, 31k, 672k; decode 2.6-3.0 s |
| Fact size | 147 MB marshal, 44 MB zstd-3, 78 MB SQLite file |
| SQLite point reads of 28.8k rows / zstd decode, Python | 0.40 s / 0.28 s |
| Quick binder: first-party `from` imports, unresolved, time | 209,729, 177, 0.30 s |
| Path labels for all modules; distinct classes; packages | 0.22 s; 825; 108 |
| Import edges, groups, predicate over edges vs groups | 215,926; 6,171; 28 ms vs 1 ms |
| git: `ls-files -s`, `ls-tree -r HEAD`, `status --porcelain=v2` | 0.06 s, 0.56 s, 0.10 s |
| Test files (outside products), migrations, `@receiver` outside tests | 8,097 (1,933), 3,884, 218 |
