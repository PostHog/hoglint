# Engine round: blind design B

> A design written blind from the requirements, without the code or earlier proposals, by an architect agent in the engine round, Nov 9, 2026.
> Replaced: the engine round's decision is in `docs/research/engine-synthesis.md` and `docs/history/decision-log.md` (D1-D13); the engine as built is in `docs/design/05-engine.md`.

The brief asked for the engine of hoglint: how source code becomes findings, in which language, with which data layout, and what is computed at all.
The user-facing behavior and the config were settled; a prototype engine existed, but the two architects did not see it and worked independently.
The engine had to produce findings with line-free identities, exit status 0, 1 or 2, ledger equality, growth by attribution across two engine versions, `explain`, `query --by owners`, `compare` against the merge base, the Isolated level per product, and a module dependents map for CI test selection; it had to be deterministic, give the same result from any cache state, and never treat unknown as clean; it had to serve CI, pre-commit on the staged snapshot, coding agents and interactive commands, across dozens of git worktrees on one machine with no long-lived process; its core had to stay generic, with Django meaning in a Django pack and PostHog facts in a preset and a small PostHog pack; and developers must not need a Rust toolchain, so any native part ships as a prebuilt wheel.
The inputs were the user guide, the PostHog preset and root config, and a workload study of the repo (`docs/research/user-guide.md` is the public user guide).

---

Number tags: **(m)** measured by the architect on Nov 9, 2026. **(w)** taken from the workload study. **(c)** computed from the workload study. **(e)** estimated.
Experiments and scripts `parse_time.py`, `extract.py` and `resolve.py`: (not published).

## 1. Summary

- A Rust core ships as one prebuilt abi3 wheel (`hoglint-native`). It turns each Python file into a summary that does not depend on the file's path. The key of a summary is its git blob id.
- One SQLite store per machine holds the summaries. All worktrees share it. Pre-commit reads blob ids from the git index, so a cache hit reads no file at all.
- Each run relinks the whole snapshot from the summaries. This covers resolution, class ancestry, the module graph, labels and rule evaluation. There is no incremental link and no daemon.
- Python holds the CLI, the Django pack, the PostHog pack, the providers, the ledger, exceptions, levels and output.
- Packs query indexed records by resolved origin: calls, decorators, classes, assignments, uses and strings. They emit edges, kinds and labels through opaque handles. A per-file Python AST pass is the escape hatch, and it is cached per blob.
- Evaluation is three-valued (Kleene). Unknown labels, unresolved endpoints, failed derivers and unhandled records are values, not omissions. So a lazy step can skip only what is already decided.
- compare runs the base engine (base `tools/hoglint` plus the base native pin) as a subprocess on the base and head trees. The two engines exchange a versioned JSON file of sums.
- Cost: about 1.3 s for pre-commit with a warm store, 5–8 s for a cold CI full check, 3–4 s for compare with a warm store (all e). The design is fast cold and faster warm.

## 2. Computation model

### 2.1 One run, in order

A run takes a **code snapshot** (a path→blob map) and a **policy snapshot** (the config files). They are usually the same tree. compare separates them.

| # | Step | Where | Eager or on demand |
|---|---|---|---|
| 0 | Snapshot: `git ls-tree -r -z <rev>` (CI, compare), `git ls-files -s -z` (pre-commit), or the index plus `git status` and hashing of changed files (worktree mode). Every non-Python input is read from the same snapshot: TOML, YAML, `turbo.json`, ledger. | Rust | eager |
| 1 | Config: parse the TOML with spans (`toml_edit`), merge `extends`, validate. Duplicate rules and conflicting assignments without `override` are config errors. Pack sections go to Python as dicts. | Rust | eager |
| 2 | Extract: get each blob's summary from the store, or parse it (ruff's parser) and summarize. This also covers framework sources (Django, DRF). | Rust, parallel | eager, all files |
| 3 | Export providers: modules that define `__getattr__` (22 first-party modules, m) ask packs for lazy export maps. | Python | eager, small |
| 4 | Link: module table, symbol tables, `__all__`, star imports, re-export chains, resolution of every import, use, string and annotation; class table and ancestry; the reverse export index (origin → surfaces that hand it out); the module graph as CSR. | Rust | eager |
| 5 | Packs: derivers run in dependency order. They produce kinds (the model index), edges (signal, installs, relation, reverse-accessor, model-label, migration-parent, drives) and symbol labels (`database`, stable ids). | Python | eager; shape classification runs on demand (section 2.2) |
| 6 | Labels: compiled `[[assign]]` and zone tables plus provider assignments (owners) over every file and symbol. Equal label tuples are interned into **label classes**. | Rust | eager |
| 7 | Evaluate: every rule over its view. The result is findings (identity, count, witness handles), a status per rule with incomplete scopes, and growth sums. | Rust | eager; witnesses and remedies only for findings |
| 8 | Policy: exceptions, ledger equality, levels, exit code, rendering. | Python | eager |

`check` always runs steps 0–8. `explain` reruns the same pipeline with tracing on for one label or edge. `query` reuses the stored result of the last run when its input digest matches (section 6).

### 2.2 The two ideas that keep it cheap and sound

**Label classes.** A predicate such as `src.package != dst.package and dst.tier == 'product'` reads only labels, the edge kind, the context and a few edge attributes. The evaluator memoizes each predicate per key (rule, src class, dst class, kind, context, attributes the rule reads). Then most of the roughly 2M edges cost one hash lookup each (e).

**Kleene logic with demand fields.** Each field is true, false or unknown. Some fields are expensive or live in Python: `edge.shape`, `dst.exported`, ancestry-based kinds. The evaluator first runs a predicate with those fields as unknown. If the result is already false, it skips the edge. A false result with unknown inputs stays false for every value of those inputs. Only edges that come out true or unknown get the field computed. Then the predicate runs again. So laziness never hides an edge.

### 2.3 Each primitive

| Primitive | Preset rules | How it is evaluated |
|---|---|---|
| edge | presentation-uses-facade, facade-no-drf, thin-commands, prod-imports-test, facade-logic, get-model, reverse-accessor, receiver-direction, installs, drives, dynamic-imports, the three direction rules, no-product-lists-in-core, leaves-stay-leaves, migration-database | Filter the rule's view by `kinds` and `contexts`. Evaluate `from`/`select`/`deny`/`unless`/`require` per label class key with memoization. Group matches by identity and count them. Views: `file-imports` (module edges), `zones` (module edges with zone labels), `packages` (package pairs with counts and witness pointers), and `declares` (module→definition, with `body` and `decl`). |
| entrypoint | public-surface | The dst fields come from the **surface**, the module path the caller wrote. `dst.exported` is a demand field: the surface module lies under an export's paths, `subclass_of` holds through ancestry, and the export's `audience` is true for the src class. It is memoized per (export, src class). An unresolved surface becomes a finding (`on_unresolved = "finding"`). Remedy: if the reverse export index finds the same origin in an export open to src, the remedy is `permitted-destination`. Otherwise it is `needs-public-api`. |
| reach | contracts-no-django, health-checks-no-dags | Take the module graph without the excluded contexts and condense it into SCCs. One reverse pass gives every module `reach ∈ {yes, no, unknown}`. The value is yes if any successor is yes. It is unknown if no successor is yes but some successor or the module itself has an unresolved or dynamic import. Otherwise it is no. For third-party modules, reach comes from the `Requires-Dist` closure of the installed distribution. Missing metadata means unknown. The witness is the shortest BFS path, run only for selected sources. |
| signature | facade-signature, orm-independent | Each public callable in a selected module has slots (its params and its return). Each slot holds a type-expression tree from the summary. String annotations are parsed too. Components are resolved and looked up by kind. A missing annotation, `Any`, bare `dict`, `dict[str, Any]` or an unresolved name is unknown, and `on_unknown = "finding"` makes it a finding. |
| export | export-shape | Each export path of each product hands out names: `__all__`, or else its public bindings. The engine takes a closure over type trees, dataclass fields, bases, method signatures, aliases and re-exports, memoized per symbol. A kind that the shape denies, or a kind the engine cannot determine, is a finding. The witness is the closure path, for example `FooDTO.items → BarDTO.insight → Insight`. |
| use-shape | model-crossings, atomic-outside-views | Reference edges are judged at the origin. Stage 1 decides every condition except `edge.shape` on label classes. Stage 2 sends the remaining uses in one batch to the pack's shape classifier, which is Python. The core supplies the generic shapes `call`, `with` and `decorator` from the role of the use. Counts are kept per identity. |
| coverage | wiring-watched | The `covers` expressions produce the subject paths. The `turbo` adapter (Python) reads `backend:contract-check` inputs per product from the snapshot. A subject that is not covered is a finding. A missing `turbo.json` or task is unknown. |
| cycle | no-new-package-cycles | Run Tarjan's algorithm on the package view. Each package pair inside a non-trivial SCC is a finding with identity package-pair. The witness is the module edges behind the pair plus the shortest path back. The ledger holds the current pairs. |
| layout | product-code-at-home, own-database, registry-slots | A predicate per file or per symbol. registry-slots folds the attribute's literal and checks each member's ancestry against the slot's ABC. A literal that cannot be folded is unknown. |
| inventory | featureflag-relations, generated-producer | Compare the computed set with the classification file. Missing entries fail, and stale entries fail too. |
| level | isolated | After all rules, per product (`group_by = package`): no unledgered finding in a required rule, no incomplete scope that covers the product, and no finding at all in a `strict` rule. A finding counts against every product among its endpoints (an assumption; the guide does not say which endpoint). |

## 3. Language split

| Part | Language | Why |
|---|---|---|
| Snapshot, store, parse, summary extraction, link, labels, expression parser and evaluator, graph algorithms, rule primitives, sums | Rust (PyO3, maturin, abi3 wheel for macOS and Linux) | These are the hot loops over 29k files, 344k imports and 1.19M call sites (w). They follow Python import semantics and the rule primitives, which change rarely and are owned by the tool's owners. tach is the precedent in this repo: a Rust core shipped as a Python wheel. |
| CLI, modes, git orchestration of compare, packs (Django, PostHog), providers (owners-yaml), adapters (turbo), ledger, exceptions, levels, explain and query rendering | Python, in `tools/hoglint`, run from the repo venv | This is the part that changes often, and PostHog developers write it. It needs no release: in-repo code runs directly. |
| Preset rules and labels | TOML | Settled contract. |

**Why not pure Python.** The architect built a rough pure-Python extractor (`extract.py`). It records imports, definitions, assignments, use chains with roles, and dotted strings.

- `ast.parse` alone takes 29.7 s of CPU single-threaded (m).
- Parse plus walk takes 10.4 s wall with many processes in parallel (m).
- The run produced 333k imports, 291k definitions, 179k assignments, 1.61M uses and 55k dotted strings (m).
- Even with a warm cache, the global phase is slow: `marshal.loads` of all facts takes 1.13 s, symbol tables 0.70 s, and resolving 1.6M uses 1.53 s (m). That is already 3.4 s before packs, labels and rules, so a warm pure-Python check lands at about 5–6 s (e).
- A cold pure-Python check on a small CI runner takes about 30–40 s (e).
- Rust parses and walks the repo in 7 s single-threaded (w). Rust link is estimated at about 0.3–0.5 s (e: the measured 2.2 s in Python for tables and resolution, divided by 5–8).

The Rust side stays small because packs never put Django meaning into it.

**Crossings and their cost.**

| Boundary | What crosses | Format | Cost |
|---|---|---|---|
| git → Rust | path, mode, blob id; blob bytes on a miss | `ls-files -s -z` / `ls-tree -r -z` pipes; `cat-file --batch` | 60–90 ms for the list (m); 3,000 blobs in 0.54 s (m), so misses stay cheap and a cold run reads the checkout from disk instead |
| store ↔ Rust | summaries | SQLite rows: a versioned bincode body compressed with zstd | the whole tree in about 30 MB of zstd (m: the facts of the architect's Python extractor, 132 MB raw → 30 MB, decompressed in 0.09 s); per-file rows are larger, about 50 MB (e) |
| Rust → Python (pack queries) | record handles (u32) with lazy attributes; `Value` trees for literals | PyO3 classes, plain lists | thousands to tens of thousands of objects per run, 10–50 ms (e) |
| Python → Rust (pack output) | edges, kinds, labels, unknowns, all by handle | one PyO3 call per item, batched by deriver | about 50k items, 20–50 ms (e) |
| Rust → Python (results) | findings as columns, status, sums | lists of tuples; identities as strings only when rendered | 54k findings (w), about 0.1 s (e) |
| base engine ↔ head engine | growth sums, status, engine and policy ids | `sums.v1.json` on stdout or in a file | 3–5 MB, about 50 ms to parse (e) |

The Python and Rust parts check a shared `NATIVE_API` integer at import. `uv.lock` pins the wheel in the same commit as `tools/hoglint`, so the two move together.

## 4. Data layout

**Summary (per blob, path-independent).** A per-file string table plus these records:

- imports: level, module, name, alias, context bits (`type_only`, `function_local`, `conditional`), line;
- definitions: qualname, kind, bases and decorators as `Value`s, class-level assignments and nested classes, params and return as type-expression trees, a body-is-trivial flag;
- module-level assignments, plus `append`, `extend` and `+=` on module names, with folded literals (`Value`: str, int, bool, None, Ref(chain), List, Dict, Call(callee, args), Splat, Opaque);
- **uses**: head binding, attribute and call chain, role (annotation, decorator, base, isinstance, except, with, call-arg(callee, slot), assign-to-local, return, other), up to three enclosing callees, scope, context bits, line; a use assigned to a local also carries the continuation chains of that local in the same scope (def-use, depth ≤ 3);
- calls: callee chain plus argument `Value`s (a call is a use with role "called");
- strings: literals that match `ident(.ident)+`, with the enclosing callee and slot;
- parse status (ok, syntax error at line, decode error, too large).

Relative imports keep their level. The link step derives module names from paths. So 3,468 duplicate blobs (m: 28,830 tracked `.py` files, 25,362 distinct blob ids in one tree), such as empty `__init__.py`, share one row. A moved file also keeps its row.

**In memory (Rust, one run).**
- One global interner (u32 ids) for names, chains and paths.
- `modules: Vec<Module>`: path, name, blob, label class, parse status.
- Per-module symbol tables: sorted `Vec<(name, Binding)>`. A binding is Def, Import, Star, Lazy or Dynamic.
- `origins`: a column parallel to the uses. Each origin is Symbol, Module, External(dist, name), Builtin, Excluded, Unresolved(reason) or Dynamic.
- Edges as a struct of arrays: kind (u8), context bits (u8), src module and scope, dst origin, surface, via-hop offset, line, attribute offset. About 2M rows (e).
- The class table, with ancestry as a memoized closure.
- The module graph as CSR, one per context mask that is in use.
- Label classes: interned label tuples. Modules and symbols point to a class id.
- The evaluation memo: `HashMap<key, Tri>`.
- Peak memory: about 400–700 MB (e).

**On disk.**

| Path | Contents |
|---|---|
| `$XDG_CACHE_HOME/hoglint/store.sqlite` (WAL) | `summary(key, body)`, `framework(dist_key, body)`, `astpass(key, body)`, `seen(key, day)` for GC |
| `$GIT_DIR/hoglint/last-run.bin` (per worktree, outside the tree) | the last run's input digest, findings, status and sums, for `query` and `explain` reuse |
| `$XDG_CACHE_HOME/hoglint/engines/<id>/` | base engines that compare extracts |

## 5. Modes and cost

| Mode | Work | Cost | Basis |
|---|---|---|---|
| CI full check, cold runner | `ls-tree`; extract about 26k distinct blobs plus 990 framework modules; link; packs (including a cold AST pass for drives); evaluate | 5–8 s wall on a typical parallel CI runner; 2–3 s with a restored store | e: 7 s single-threaded Rust parse and walk (w) × 1.5–2 for summaries, divided across the runner's parallelism; plus about 1.5 s for link, packs, rules and policy. Compare: the prototype took 45 s (w), and the Django-booting checks took 267–278 s (w) |
| Pre-commit, 3 staged files, warm store | `ls-files -s` 0.06 s (m); index config blobs 0.02 s; 29k store lookups and decode 0.2–0.4 s; 3 extractions 0.01 s; link 0.3–0.5 s; packs 0.2–0.4 s; labels, rules and ledger 0.2 s; interpreter 0.1 s | about 1.3 s | e, from the parts listed. most of a new worktree's contents already exist in another one, so its first run is warm too |
| Agent check (worktree mode) | as pre-commit plus `git status` 0.09 s (m) and hashing of changed files; `--changed` filters the output, not the computation | about 1.4 s | e |
| compare (base and head, two engines) | head run; base engine setup (`git archive` of `tools/hoglint` plus `uv run --with hoglint-native==<base pin>`); base engine on base code and on head code, in parallel | about 3–4 s warm; 8–12 s cold CI. If the base extractor build differs from head's, the head blobs are extracted twice (+2–4 s) | e. Most of a worktree's files equal its merge base, so the base tree needs few new extractions |
| `explain` | the full pipeline with tracing for one label or edge | about 1.5 s | e |
| `query --by owners` | reuse `last-run.bin` when its digest matches, otherwise run `check` | 0.2 s or 1.4 s | e |
| Dependents map (tach JSON, `{file: [importing files]}`) | steps 0–4 plus the `registers` edges; no packs, no rules | about 0.8 s warm, 4–5 s cold | e; `tach map` took 2.7–3.6 s (w). Files that fail to parse are listed in an `incomplete` key, where tach drops them silently |

## 6. Caching and sharing

| Cache | Key | Shared by | Invalidated by |
|---|---|---|---|
| summary | git blob id + extractor build id + parser target version | every worktree and every engine build with the same extractor | a different key; nothing else |
| framework summary | dist name, version, digest of `RECORD` + extractor build id | every worktree | a different key |
| pack AST pass | blob id + pass name + digest of the pack's source files | every worktree | editing the pack |
| run result | digest of (manifest, policy blobs, engine build id, pack digests, framework keys, mode) | one worktree | any input change |
| base engine | base `tools/hoglint` tree id + native version | compare runs | a different key |

How a cached result stays equal to a clean run:

1. Each cached value is a pure function of exactly its key. A summary sees only the bytes of its blob. An AST pass receives one file's AST and returns plain data, with no query handle, so it cannot read other inputs.
2. The extractor build id is the digest of the extractor's source, embedded at wheel build time. It is not a hand-bumped version, so a forgotten bump cannot reuse stale rows.
3. Link, labels, packs and evaluation are never cached across runs. Every run recomputes them from summaries. No partial or incremental state exists that could drift.
4. Writes are `INSERT OR IGNORE` in short transactions. Two worktrees that write the same key write the same bytes.
5. Two checks keep this honest. `check --no-cache` must match byte for byte (a test in CI). A nightly `check --verify-cache` re-extracts a sample and compares.

The design is not fast **only** with warm caches: a cold full check is 5–8 s (e). A CI master artifact of the store saves about 2–4 s, so it is optional.

Determinism: extraction results are sorted by path. Derivers run in a fixed order. Every emitted set is sorted. Witnesses tie-break by path, then line. There is no iteration over hash order in output. A test runs check with 1 and N workers and diffs the outputs.

## 7. Unknown is never clean

Unknowns enter as values and leave only as a finding or as an incomplete status:

| Source | Becomes |
|---|---|
| File that does not parse or decode | a module with status `unparsed`. Its outgoing edges are unknown. Resolution into it is unknown. Every rule whose `from` could select it is incomplete for its package. |
| Name, chain or string that does not resolve | `Unresolved(reason)` as the dst. The rule's `on_unresolved` applies: `finding`, `incomplete` (scoped to the src package), or `block` (a finding that the ledger cannot accept). If a rule says nothing, the default is `incomplete`. |
| Computed `import_module`, `__import__`, `import_string`, `apps.get_model` | a `dynamic-import` edge with an unknown dst. Outside a declared loader it makes the rules that read that module's outgoing edges incomplete, and it fails Isolated. |
| Module `__getattr__` with no provider map | names it does not bind are `Dynamic`. |
| Field missing inside its `required_on` domain | an unknown label. Any predicate that depends on it evaluates to unknown, and `on_unknown` applies. |
| Deriver that raises | its `provides` families get status `failed(deriver, error)`. Every rule that reads those kinds is incomplete and names the deriver. |
| Kind with `expected = true` that comes out empty (zero models) | incomplete: "the Django pack read no models". |
| A record a deriver subscribed to but neither turned into output nor `ignore`d | `unhandled(deriver, file:line)`, which makes the provided families incomplete. This **accounting** stops a pack from dropping a case silently. |
| Third-party module without metadata | reach is unknown. |
| Django sources not installed, or of an unexpected version | django facts fail, and the dependent rules are incomplete. |
| Optional oracle in CI (`[django] oracles`) | runtime model registry and migration graph that disagree with the static index make the affected rules incomplete. An oracle can add incompleteness. It never removes a finding. |

Under laziness, the only lazy step is a demand field (section 2.2). It is skipped only on a decided **false**. Unknown results are never memoized as false. Candidate sets are complete before the demand step runs. The exit code is 2 if any non-advisory rule is incomplete, which wins over 1. Every incomplete line names the rule, the scope (packages or modules), the reason and the location.

## 8. Extension model

### (a) A preset rule (TOML only)

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

Then run `hoglint explain --rule tasks-no-presentation` to see the selected edges, and `hoglint check --write-ledger` to accept the current findings. compare reports that as rule growth, which passes. Add a fixture case under `tools/hoglint/tests/preset/`.
The author must know the field vocabulary, the expression grammar (`== != in not startswith subset_of and or not none`), the three-valued semantics and `on_unknown`/`on_unresolved`, and how to pick an identity and a growth key. A rule that removes findings is a relaxation and needs review by the tool's owners.

### (b) A new Django pattern in the Django pack (Python)

Example: a hook from `[django] hooks` gets its callbacks through a module constant, as in `register_task_profiles(PROFILES)` with `PROFILES = [TaskProfile(run=run_audit)]`.

```python
# tools/hoglint/hoglint/packs/django/installs.py
from hoglint.pack import Q, deriver

@deriver(provides="edge:installs", config="django.hooks")
def installs(q: Q, hooks: list[str]):
    for site in q.calls_and_decorators(callee_in=hooks):  # accounted: each site needs an output
        refs = [v for v in site.argument_values() if v.is_ref()]
        if not refs:
            q.unknown(site, "hook called with a computed callback")
            continue
        for ref in refs:
            for target in ref.expand_constant():  # new: follows PROFILES into its folded literal
                q.edge("installs", src=target, dst=site.callee, at=site)
```

```python
def test_installs_through_module_constant(tree):
    tree.write("products/a/backend/task_profiles.py", """
from products.tasks.backend.facade.profiles import register_task_profiles
PROFILES = [TaskProfile(run=run_audit)]
register_task_profiles(PROFILES)
""")
    assert ("products.a.backend.task_profiles.PROFILES", "products.tasks.backend.facade.profiles.register_task_profiles") in tree.edges("installs")
```

The author must know:
- the record API (`calls_and_decorators`, `classes`, `assignments`, `uses`, `strings`; all filter by **resolved origin**, not by spelling);
- `Value` and how it folds literals;
- that every subscribed record ends in `edge`, `unknown` or `ignore(reason)`;
- `at=` for locations.

The `tree` fixture runs the real native engine on an in-memory snapshot. No Django boot is needed. A pattern that the summary cannot express uses `@ast_pass` (see (c)) until the tool's owners add a field to the extractor.

### (c) A PostHog-only fact (PostHog pack)

The house `@frozen` decorator counts as a frozen dataclass for the `contract` shape:

```python
# tools/hoglint/presets_py/posthog/kinds.py
@kind_provider("frozen-dataclass")
def house_frozen(q: Q):
    return q.classes(decorated_with="posthog.dataclasses.frozen")
```

The drives fact, which needs syntax the summary does not keep (query kinds inside test request bodies):

```python
@ast_pass(prefilter=DISPATCH_CALL_BYTES, files=lambda f: f.testcode)  # pure; cached per blob id
def kinds_in_test(tree: ast.Module) -> list[tuple[str, int]]: ...

@deriver(provides="edge:drives", requires=["posthog.query_runner_index"])
def drives(q: Q, runners: dict[str, str]):
    for f, found in q.ast_results(kinds_in_test):
        for kind, line in found:
            if kind not in runners:
                q.unknown((f, line), f"unknown query kind {kind!r}")
            else:
                q.edge("drives", src=f.module, dst=runners[kind], at=(f, line), label=f"drives({kind})")
```

The author must know the same API, and that an `ast_pass` sees one file only.

## 9. Risks

**Riskiest assumption: the generic summary is rich enough.** The bet is that Django and PostHog patterns derive from summaries alone, so `ast_pass` stays rare and the Rust extractor rarely changes. If this is wrong, two things follow. Packs fall back to Python AST passes over many files, so cold runs drift toward Python speed (about 30 s or more on CI, e). And every new pattern waits for a Rust release from a small team.

**One-day experiment.** Extend `extract.py` to the full schema of section 4, in Python, which is enough to test sufficiency. Write four derivers over summaries only: model-crossings shapes with def-use, `get_model`, reverse accessors, and receivers plus installs. Diff their output against `products/model_crossing_uses_baseline.txt` (1,007 lines, m) and against the existing AST tool in `tools/hogli-commands/hogli_commands/product/`.

Expected numbers:
- At least 98% of the baseline lines come back exactly.
- Every miss falls into at most 3 schema gaps that can be named (likely: if-test context for imports, dict-literal arguments, decorator-on-method targets).
- drives is the only fact that needs `ast_pass`, on at most 2,000 prefiltered test files, with at most 3 s of cold CPU.
- The derivers take at most 0.5 s over the summaries.

If coverage is under 90%, or there are more than 5 gaps, packs should own a cached per-file Python pass. The cost then rises to about 15 s cold and about 2 s warm (e).

**Second risk: full relink stays at or under 0.5 s.** A half-day Rust spike tests it: load 26k summaries from SQLite, resolve 1.6M uses, build the CSR. Expected: at most 0.4 s single-threaded. If it takes more than 1.5 s, the fix is name-level invalidation, because the dependents by changed name are 5 at the median and 29 at p90 (w). Module-level invalidation is no fix: the transitive importers are about 18k (w).

**Third risk: release overhead of a Rust wheel** for a small team. It is mitigated by keeping the churn in Python and TOML, and by tach's existing build pattern.

## 10. What not to build

- A daemon, watch mode or LSP server, and any incremental link or evaluation with dependency tracking.
- Caches of linked graphs or rule results that correctness depends on.
- A master-published store artifact before CI shows it is needed. A cold run is already cheap.
- Django boot in normal checks. It runs only in the optional oracle job.
- Type inference beyond def-use inside one function, and a call graph beyond the bare-name resolution that drives asks for.
- A Rust plugin API. Packs are Python only.
- Functions in the TOML expression language beyond the small set. A new need becomes a field that a pack computes.
- Reading third-party sources other than the declared `external` ones. Everything else uses metadata only.
- Autofix rewriting. `safe-rewrite` stays a suggestion.
- Using rule locality to skip computation. Locality only filters output (`--changed`).
- `plan`.

Known limitations:
- compare reads the framework sources installed at run time for both trees. A Django upgrade PR may therefore misattribute rule growth as code growth, and the output notes this when `uv.lock` changes Django.
- A pack still decides how wide its candidate patterns are, for example which `.connect` calls count. Accounting proves that every candidate is handled. It cannot prove the candidate set is complete.

Blindness: the architect read only the brief, its inputs, the PostHog source (read-only) and its own scratch directory. It did not open the prototype, any `tools/hoglint` directory, design A or other notes.
