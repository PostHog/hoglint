# Engine round: adversarial reviews (2 of 3)

> The adversarial reviews of the engine round, Nov 9, 2026, part 2: the extension and maintenance adversary and the performance adversary, each finding with what it led to. The method and the correctness adversary are in `engine-adversarial-reviews.md`; the skeptic, the demand audit and the bug list in `engine-adversarial-reviews-3.md`.
> Partly replaced: the decisions these findings led to are in `docs/history/decision-log.md` and later parts, and the engine as built is in `docs/design/05-engine.md`.

---

## The extension and maintenance adversary

What it ran:

- a probe of fact reads and resolver calls per producer, by monkeypatching (one run of the core reference producer plus both packs: 16 s, 3.1 GB);
- a probe of function-scope import hits, def-use hop depth, and the files the drives call view reads;
- counts of syntax forms in the facts, and of `get_model` receivers;
- the git history of the scanner of the day, `tach.toml`, semgrep and the hoglint build (a helper agent; the adversary checked its classes against the diffs it quotes).

"C2-A" is blind-a's design, "C2-B" is blind-b's. "Field" means a new entry in the Rust catalog. "Q" means a new index query in Rust. Both cost a wheel release.

### Verdicts

**C1, Python cleaned: holds (one fix).**

- The pack API stays. Raw rows and the Python resolver stay. A change to the resolver or to a pack is a Python PR.
- New syntax costs an extractor change and a wheel, as in every candidate. Since schema v3 (`eada57fad5c`), new call vocabulary and new dict keys are pack config and cost no wheel.
- Migration throws nothing away. All three parity invariants hold.
- Fix: replace the positional rows with typed record views (F9).

**C2, native link: holds with fixes, but the catalogs that both blind designs specify break.**

- The real packs need 8 new catalog fields under either design (6 or 7 of which change parity output), plus 6 index queries.
- Both designs' own kill signals ("more than 5") fire on paper.
- drives cannot be expressed by either sketch.
- Resolution moves into Rust, and resolution semantics changed four times in one review commit on Nov 9.
- The parity harness reads `Edge` objects that C2 no longer builds.
- Fixes: make the schema-v3 facts the catalog; add one generic row query by spelling and one scope-aware resolve; keep the Python resolver as a CI oracle until cutover; port the parity harness first; ship a native test constructor and a facts and resolve dump.

**C3, persistent queries: holds with fixes, at the highest recurring cost per pattern.**

- Packs stay in Python and keep the facts of the day.
- Every producer must be rewritten into tracked, partitioned queries before it gains anything: 9 producers read whole-snapshot collections, 16 memo caches live inside packs, 2 file reads bypass the snapshot.
- Each pattern change then needs an invalidation test as well as a behavior test.
- Fixes: a pack API with no ambient I/O, enforced by a test audit hook; an invalidation test kit; the cache-off differential on every PR; `explain --deps`.

### F1 (critical). A fixed generic catalog does not carry the packs

**Claim attacked.** blind-a: "At most 3 catalog fields added. No analysis needs the raw AST", kill signal "more than 5 catalog additions". blind-b: "at most 3 schema gaps", "drives is the only fact that needs ast_pass", kill signal "more than 5 gaps". The synthesis named this C2's riskiest assumption.

**Measurement.** All 57 fact accesses of the Django pack and the PostHog pack, mapped onto each design:

| | C2-A | C2-B |
|---|---:|---:|
| expressible with the listed catalog and API | 19 | 21 |
| needs an unlisted index query only (Q) | 19 | 16 |
| needs a new catalog field (F) | 10 | 11 |
| needs the `ast` hatch | 1 | 1 |
| becomes a C2 core primitive (facade facts) | 7 | 7 |
| cannot be expressed (parity tool) | 1 | 1 |

- Distinct new fields on the pack side: 8 under each design. Fields that change parity output: A 6, B 7.
- Core side: type-alias values for annotation expansion, covering 1,489 module-level `X = Union[...]` or `A | B` assigns (c).
- Index queries: 6 under both designs (by spelling; scope-aware resolve at a site; resolve pack-supplied dotted text from a module; class detail such as direct body constants, nested classes, MRO and direct bases; a per-def callee-name summary; snapshot listing and non-Python file reads).
- The one-day experiments each design proposed would roughly pass. They leave out drives, migrations, settings and facade, which is where the rest of the fields come from.

**The fields, with the volume each carries:**

| Field | What the packs read | Volume | A | B |
|---|---|---|---|---|
| F1 | Full escape path with names and the AST parent (`assign:<targets>`, `kw:<name>:<callee>`, `arg:<i>:<callee>`, `classkw:`, `dict:val:<key>`, return, cmp, bin, ifexp) | 36 distinct first steps on model uses (m) | needed (11 unnamed roles) | needed (10 roles, `other` has no detail) |
| F2 | def-use past one hop | 130 of 461 flow refinements need 2 or more hops; 8 need 4 or more (m) | needed (one hop) | 8 lost (3 hops) |
| F3 | Call keywords, `**` included | related_name 1,264, related_query_name 55 field kwargs (c); `sender=` on 300 decorator signal edges | unclear | needed |
| F4 | Nested value trees: a call inside a list or a call, BinOp, BoolOp, Set vs List vs Tuple, Star | 290 migrations with `swappable_dependency(...)` inside `dependencies` (c); 225 class-body field calls wrapped in a call (c); settings values with BinOp 133 and BoolOp 54 (c) | needed | BinOp, BoolOp and Set needed |
| F5 | `+=` and mutation of module names | 1 settings `+=`; facade `mutated()` | needed | present |
| F6 | Statement flags on class-body and module-body rows (direct vs under if/try) | fidelity of class bodies | needed | needed |
| F7 | Parameter default values | 7,369 test params with defaults (c) | needed | needed |
| F8 | Bare identifier strings, per scope | 67,196 CamelCase string occurrences in tests (c) | present | needed |
| F9 | `<Name` tags inside strings | 506 test rows (c) | needed | needed |
| F10 | Dict entries under a watched key anywhere, with the escape path | 1,436 `kind` entries in tests (c) | needed | needed |
| F11 | If-compare branches with body extent | 1 file, 142 rows | hatch | hatch |

**Consequence.** Every row above is a wheel release before cutover. The premise that the catalog "rarely changes" fails on day one.

**Fix.** Adopt the schema-v3 facts as the C2 catalog. Since `eada57fad5c` they hold no Django or PostHog names, and they already carry F1 to F11. Extend the one-day experiment to drives and migrations.

**Adjudication:** partly confirmed. The list arithmetic is right, but eight is not a defensible count of independent schema additions: the def-use probe measured the maximum downstream hop of an already refined source, not whether truncation changes a final shape, so "130 need two hops" and "eight are lost" are upper bounds. A field, an enum variant, a depth limit and an index query are different costs. "Use schema v3 as the catalog" is a real way to keep information, but not sufficient alone: facts were still filtered by configuration and watch lists, values have budgets and opaque forms, and packs still need efficient selection.

**Led to:** D1 (Rust holds the facts as "today's schema-v3 catalog").

### F2 (critical). Queries by resolved origin cannot express matches by spelling

**Claim attacked.** blind-b: the record API "all filter by resolved origin, not by spelling". blind-a: "calls whose callee resolves to X".

**Counterexample.**

```python
def forwards(apps, schema_editor):
    Team = apps.get_model("posthog", "Team")   # apps is a parameter: it has no origin
```

**Measurement.**

- 728 of 1,119 `get_model` sites have a receiver that no import binds (c); 615 of them are in migrations.
- The other 391 go through `django.apps.apps`, an instance. Its `get_model` resolves only as a member of a variable, and both designs exclude type inference.
- `uses.py` matched `*.get_model` by spelling on purpose.
- installs also matched by spelling, so that it can emit an unresolved edge when resolution fails (4 at the time, m). A query by origin cannot return a call that resolves to nothing.
- 6 more accesses match by spelling: `.connect`, `kwargs.setdefault("to", ...)`, `X.append(...)` in settings, `patch`/`.object`, test-client verbs, dispatch call names.

**Consequence.** get-model (246 findings) is not expressible. An origin-only port of installs drops its unknown edges, so unknown reads as clean.

**Fix.** One native row query by spelling (last name, suffix or key) that returns both resolved and unresolved candidates.

**Adjudication:** confirmed. Keep spelling, resolution status and scoped lookup together.

**Led to:** D1 (candidate indexes by spelling), D17 (candidates by spelling), D8.

### F3 (high). Model-crossings growth keys contain the use-position vocabulary

**Claim attacked.** blind-a: a use chain of 11 roles plus one hop. blind-b: uses carry one of 10 roles, and `other` has no detail.

**Measurement.**

- The preset's model-crossings growth key is `["dst.symbol", "edge.label"]`. The label is the shape text, for example `other(Return)`, `other(Attribute:save)`, `passed-as-class(<callee>)`, `write(create,update)`, or `drf-model-serializer` (from `assign:model`, `kw:model:` or `classkw:model`).
- 2,874 of 101,466 model uses are `other(...)` (m); 1,119 of them are cross-package and not test (m). 356 model uses are `drf-model-serializer` (m).
- The crossings baseline of the day held 4 `other(...)` lines and 4 `drf-model-serializer` lines (c).

**Consequence.** With unnamed roles these labels collapse. Ledger lines and growth keys change at cutover, and the crossings parity lines break. Any later refinement of the vocabulary moves the keys again.

**Fix.** Carry the existing escape path verbatim as the use-chain field (F1).

**Adjudication:** confirmed. Preserve equivalent information; the literal string is one way, not the only one.

**Led to:** D1 (shapes stay in Python, over the schema-v3 facts, which carry the escape path). The open point "versioning of shape labels that sit in growth keys" stayed open.

### F4 (high). drives cannot be expressed by either sketch

**Claim attacked.** blind-a reads a literal dispatch table. blind-b runs an `ast` pass over "at most 2,000 prefiltered test files".

**Measurement.**

- The real dispatcher is an `if kind == ...:` chain with function-local imports in each branch. drives read it from `branches` rows and the imports inside each body. A literal table does not exist, so A's analysis yields its own gap and drives stays incomplete for good.
- The call view follows helpers into other modules. It read the bodies of 255 non-test files plus 356 test files (m). B's test-only prefilter misses the 255.
- drives also reads bare strings (F8), tags (F9), watched `kind` entries (F10), parameter defaults (F7), and the callee last names of every function in 8,367 files, 695,728 rows (m).

**Consequence.** 396 drives findings depend on this, and so does wiring-watched (38 findings), which reads the drives findings. Under B, drives becomes a Python pass over all first-party files; B's own fallback prices that at about 15 s cold.

**Fix.** Keep drives as a Python analysis over the catalog rows above, plus a native per-def callee summary. Read the one-file dispatcher through the hatch.

**Adjudication:** partly confirmed. A literal-table-only implementation and a strictly test-only hatch fail. But this does not force parsing every first-party file in Python: branch facts and reachable helper summaries suffice.

**Led to:** D13 (drives is rewritten: test files selected by kind mention, helper summaries, and the depth-memo bug fixed), D17. drives stayed a Python plugin of the PostHog pack (D49).

### F5 (high). Resolution moves into Rust, and resolution changes often

**Claim attacked.** blind-b: import semantics and the primitives "change rarely and are owned by the tool's owners". Neither design lists a resolver oracle.

**Evidence.**

- One review commit, `419f28f6804` on Nov 9, changed four resolution or use-position rules: a conditional def no longer replaces an import; `returns` vs `return`; the context of the TYPE_CHECKING else branch; which argument a dynamic import reads.
- Schema v2 (`7397ea50c52`) fixed three encodings within hours of the first pack review.
- The scanner of the day gained static Python syntax needs at about 3 a month, Jul to Nov (c, 14 items); 12 of the 14 needed cross-file linking (ancestry through re-exports, app labels, call graphs), which is Rust under C2.
- Mapped onto the C2 catalogs: 9 of 15 items needed a field or a query (e), about 2 wheel releases a month. With the fixes from F1 and F2, about 4 of 15 remain, all in the link: about 1 a month (e).
- Packs need scope-aware resolution: 11,103 imports in 2,731 files bind names in function or class scope (c). Through them the engine resolved 7,017 core references in 1,842 files, 1,506 model uses, 53 installs and 25 signal lookups (m). blind-a limits references to module-bound names.

**Consequence.** Each such change becomes a wheel release, then a pin bump, with a merge-queue wait between the two.

**Fix.** Keep the Python resolver as a differential oracle in CI, with zero diff per linker change. Add a scope-aware `resolve(path, scope, line, chain)` that includes function-scope imports and member chains.

**Adjudication:** partly confirmed. Scoped resolution and evolving semantics are real; the cited commit changes bindings, contexts, use positions and dynamic-import handling, not four independent resolver algorithms. Monthly release forecasts from bootstrap history are not established. Scope-aware APIs and a resolver oracle are justified regardless.

**Led to:** D7 (the Python resolver stays as the differential oracle until cutover; then a fixture corpus and golden summaries), later amended to keep a small independent oracle for generated cases.

### F6 (high). The C2 migration breaks the instruments that prove parity

**Claim attacked.** The invariants: crossings 968 + 2, tach 0/0, Rust equals reference. Both designs have edges as columns, with `Edge` objects only for witnesses.

**Measurement.**

- Crossings parity came from the legacy ledger tool. It read `Edge` attributes of four kinds, and the parity-only kinds `export`, `declares`, `inherit` and `decorate`: 10,251 edges.
- 430 of the 968 baseline lines are facade lines (c).
- tach 0/0 came from the tach-format map, which resolved surfaces again in Python.
- "Rust equals reference" pinned the extractor byte for byte to the spike's reference. A new catalog voids that pin.

**Consequence.** No invariant stays fixed while the engine changes. Each must be proved again with a rewritten tool.

**Fix.** First PR: port the legacy ledger and the tach map to read findings and index-shaped queries, and prove them equal on the existing engine. Then move producers.

**Adjudication:** partly confirmed. The adapters need adaptation, but the invariants themselves can stay fixed, and keeping schema-v3 extraction avoids invalidating extractor parity.

**Led to:** D11 (parity-only machinery leaves the shipped package for the shadow job's script), D7.

### F7 (medium). C3 asks every pack for a rewrite before pre-commit gains anything

**Claim attacked.** The second opinion: "Unported producers run with a coarse snapshot-wide dependency"; authors "do not write invalidation rules".

**Measurement.**

- 9 producers read whole-snapshot collections (m). Files read per kind: installs and signals 21,623; uses 22,546; drives the imports of 28,751; get-model 7,231; migrations 3,459; models 13,417 through the resolver.
- 16 memo caches inside packs span query keys (c).
- 2 reads bypass the snapshot (c): the routing file from the working tree, and the owners files.

**Consequence.** Until restructured, drives (3.9 s, m) and uses (0.9 s, m) recompute on every change. Each new pattern needs a mutation test in three forms: a body change, a new definition that satisfies a failed lookup, and a member that is added or removed.

**Fix.** A pack API with no `Path`, `open` or environment access, enforced by a test audit hook. A shared invalidation test kit.

**Led to:** D5 (C3 rejected). The same point decided the reuse spike of Nov 10: "the packs are the blocker" (`docs/research/second-opinion-reuse.md`).

### F8 (medium). Rule tests have no native seam

**Claim attacked.** Both blind designs test through the real native engine.

**Measurement.** 74 test functions (225 cases) built hand-written fact rows or Python `Edge` graphs (c). Only the compare end-to-end test needed the binary. A pack that needs a new field cannot be tested until the wheel ships. B's Rust evaluator makes graph-builder rule tests impossible without a native edge injector: the grimp fake-filesystem trap (`docs/research/engine-prior-art.md`, §3 item 11).

**Fix.** Ship `Index.from_rows(...)` and `EdgeTable.from_python(...)` in the wheel.

**Led to:** no decision entry for the constructors. D7's fixture corpus and golden summaries carry the tests after cutover.

### F9 (medium). C1 and C3 keep a fragile positional pack API

**Measurement (c).** Packs held 34 raw `record.get`/`record[...]` reads, 18 positional tuple unpackings in loops, and 13 positional index reads such as `row[5]`. Schema v3 changed every site row layout, so every pack was edited.

**Fix.** Typed row views generated from the Rust schema.

**Led to:** D22 (the pack API is a typed reader and a sink: projected candidate batches with edge handles) and D31 (the public pack API exposes queries, not engine internals).

### F10 (medium). compare across engines during migration

**Claim attacked.** blind-a: the base engine returns `scan-result` v1 growth sums, "the only cross-version contract".

**Fact.** The engine of the day emitted its summary with couplings (125 MB), not `scan-result` v1. Open PRs keep merge bases from before the cutover for weeks, so the old engine must run and must speak the new contract.

**Fix.** Ship `scan-result` v1 from the existing engine before cutover.

**Led to:** D9 (a versioned summary protocol).

### F11 (medium). Debugging and explain

- **C2-A:** `explain` is a warm run plus a Python predicate trace per group. It cannot show why a pack made no edge, or why resolution failed past the stored reason.
- **C2-B:** the evaluator memo lives in Rust, so any trace feature is a wheel release.
- **C3:** a wrong reuse shows as a wrong answer that a cache-off run does not reproduce. The only tool is a full rerun without the cache and a diff.
- **At the time:** the resolver was Python and steppable in pdb; facts were JSONL.

**Fix.** Ship `hoglint facts <path>`, `hoglint resolve <path>:<line> <chain>` and `explain --deps` (C3) on the same native calls that packs use.

**Led to:** no decision entry.

### F12 (low). The cheapest changes dominate, and they are Python or TOML everywhere

| Source | Change | Volume | Cost in every candidate |
|---|---|---|---|
| `tach.toml`, since May | commits | 361 commits (c) | TOML |
| | list edits | 324 of the 361 (c) | TOML |
| | new module blocks | 65 (c) | TOML |
| | new interface blocks | 81 (c) | TOML |
| Scanner history, Mar to Nov | list commits | 18 (e) | TOML or a Python constant |
| | vocabulary commits | 4 (e) | Python constant |
| | rule commits | 11 (e) | TOML or a Python constant |
| `[django] hooks` | hook names | 84 entries | TOML |
| managers | managers missing from the list | one manager with 30 uses (m) | a pack Python constant |

The scanner's list, vocabulary and rule commits needed no new syntax. Missing managers were classified as `other(Attribute:...)`.
No fix needed: it is evidence that shapes and vocabulary belong in Python or TOML in every candidate.

**Led to:** D1's reasoning (packs and rules change weekly; shapes and vocabulary stay out of the native core). D39 later put rule evaluation in Rust, because rules are TOML and the primitives a closed set.

### What none of the candidates addressed

1. **Who owns resolution semantics, and the oracle that keeps them honest:** conditional bindings, stubs, PEP 562, function-scope imports, member chains and `X.Meta` parents. blind-a kept a Python oracle for the extractor only, not for the linker.
2. **Spelling-based matching as a first-class, unknown-preserving query.** Parameters such as `apps` in migrations have no origin by design.
3. **How parity is measured while the engine changes,** including the old engine in compare for open PRs with pre-cutover bases.
4. **Versioning of shape labels that sit in growth keys.** A vocabulary change rewrites ledger lines and keys.
5. **Pack-author debugging tools under a native core:** a facts dump, resolve-at-site, query and cache traces.
6. **A native test seam for rules and packs,** so tests need neither a released wheel nor a second evaluator.
7. **The release train itself.** A pack PR that needs a field means a release workflow run, a publish from the main branch only, and a second PR for the pin. Nobody budgeted the rate of about 1 to 2 releases a month (e).

Items 1 and 3 led to D7 and D11; item 2 to D17; item 7 to D15 (hoglint in its own repo, published from tags only).



---

## The performance adversary

Tags: **(m)** measured in this round; **(m:src)** measured by an earlier report (the audit, the skeptic, blind-a, the performance study of the first implementation); **(c)** computed from measured numbers; **(e)** estimated.

Machine: a developer laptop. Every timing here was measured under load, so ranges are given.

**The central experiment.** The adversary ported the core resolver of the day to Rust: the symbol and site lookup, and the import, reference, declaration and dotted-path producers. It covers re-exports, star imports, PEP 562 lazy maps, conditional-binding rules, cycle handling, local scopes, use positions, dotted strings and C3 MRO. It runs on the same facts file as the Python baseline, and it produced all 1,143,531 edge rows identical to Python on kind, path, line, column, resolution and target (m).

(The adjudication later found that this projection leaves out surface, via, scope, contexts, use position and attributes, and that the port skips some semantic data; see F4.)

### Verdicts

**C1, Python cleaned: holds for CI, breaks for pre-commit.**

- Core resolution alone costs 5.5 s CPU with GC off (m). Add packs (2.9 to 4.6 s), rules (4.75 s, m:audit) and labels (about 1 s), and a full check lands at 15 to 16 s CPU with GC off (c).
- Demand-only edges bring that to the audit's 12.5 s (e). GC on adds 2 to 5 s. So 12 to 20 s needs every interim fix.
- Pre-commit costs the same as a full check. With ten worktrees committing at once, that becomes 20 to 39 GB and 40 to 75 s each (c).
- Keep C1 as the interim CI engine only.

**C2, native link with full relink: holds with fixes.**

- The link claim holds. The Rust port links the repo in 0.42 to 0.57 s on one core (m). Fact decode adds 0.20 to 0.28 s (m). Resolving every call and use for pack queries adds 0.10 to 0.13 s (m).
- Over-claimed: pre-commit at 1.3 s (blind-b); packs at 0.2 to 0.6 s; memory at 0.3 to 0.7 GB.
- Fixes: cache the per-file reading of drives and migrations per blob from day one; budget pre-commit at 2 to 4 s; budget about 1.2 GB per process; budget cold CI at 7 to 15 s on 4 cores.

**C3, persistent queries: holds with fixes.**

- The feared frontier blow-up does not happen. The frontier counts real dependencies, negative lookups included: resolution median 5 files, p90 14 (m); ancestry p90 6 classes invalidated (m); drives p90 6 test modules (m).
- Validating the core trace costs 0.2 to 0.4 s (m).
- Fixes: make every whole-repo query (origins, registry, package and module graphs, ledger equality, levels) a cheap projection; keep per-blob input fingerprints in Rust; budget the cold path (CI without an artifact, engine changes, new machines) at Python F times 1.2 to 1.6, so 20 to 55 s.

### F1. C2 pack budgets assume 20 to 50k crossing records; the ported packs read 1 to 3 million per run

**Claim.** blind-a: "Pack queries 20-50k records, 50-100 ms", "Packs 0.3-0.6 s". blind-b: "thousands to tens of thousands of objects per run, 10-50 ms", "packs 0.2-0.4 s". The synthesis: pre-commit 1.3 to 3 s.

**Measurement.** Record volumes each pack step reads, from the same facts file:

| Pack step | At the time (GC on) | What it reads (m) | C2, index queries every run (e) | C2, per-blob pass, cold (m) | C2, per-blob pass, warm (e) |
|---|---:|---|---:|---:|---:|
| django.uses: model-crossings shapes | 1.24 s | 101,475 references onto 701 model classes; the demand set is 3,180 references in 581 files, plus 9.8k `derived` and 30.7k `uses` rows of those files | 0.05 to 0.1 s | not needed | n/a |
| models and relations | 0.28 s | 9,821 class-body assignments, 107k value nodes | 0.1 to 0.2 s | n/a | n/a |
| migrations | 0.16 s | 3,816 files, 8,605 assignments, **1,582,726 value nodes** | 0.2 to 0.8 s (bulk materialization 0.03 s, m; handle-per-node access costs more) | 1.7 s CPU, 0.6 s wall on 6 workers | 0.02 to 0.05 s |
| signals, installs, get-model | 0.53 s | they scan 1.19M `callees` rows by last name | 0.02 to 0.05 s, because the link resolves callees | n/a | n/a |
| drives | 2.53 s, of which per-file fact reading 1.5 s, origins 0.11 s, derive 0.17 s (m) | 8,211 test files: **1.23M rows** (names 51k, callees 675k, uses 280k, refs 222k), plus the imports of every file | 0.8 to 1.5 s as ported; 0.1 to 0.3 s if rewritten to pull only test files with kind mentions | **17.2 s CPU** parse plus walk, 3.5 s wall on 6 workers | 0.3 to 0.5 s |

- Materializing the same volumes into Python costs 0.08 s for 1.23M test-file rows, 0.03 s for the migration trees, 0.048 s for 1M tuples of 6 ints, all with bulk C-speed construction (m). The crossing is cheap. The per-row Python logic is not.
- Only 299 of 8,111 test modules read anything besides their own file in drives; 193 reach the registry (m). A drives rewritten against the index can stay small. The ported drives cannot.

**Consequences.** C2 packs cost 0.5 to 0.9 s warm with per-blob passes, and 1.3 to 2.8 s as ported (e). The cold drives pass alone is 17 s CPU on CI; blind-b's own estimate was 2,000 files and 3 s or less.

**Fix.** Ship drives as a rewrite that selects test files by kind mention, and migrations as a per-blob summary. Keep models and uses on index queries.

**Adjudication:** partly confirmed. The broad reads are real; they do not prove the volume or cost of a rewritten query API.

**Led to:** D13 and D17 (drives prefiltered by kind mention; migrations as per-blob summaries cached by (pack digest, blob oid); signals, installs and get-model on origin and spelling queries; target: packs at most 1 s warm).

### F2. No candidate budgets ten concurrent worktrees

**Claim.** Every cost table assumed one process on an idle machine. blind-a: "300-600 MB per process for 2-3 s".

**Measurement.**

- Ten link processes at once (Rust port, single thread, under load): each took 2.4 s, decode 0.72 s and link 1.3 s. Alone, the same process takes 0.7 to 0.8 s, so ten at once is **3x slower** (m).
- A laptop has few performance cores. Single-threaded processes beyond them land on efficiency cores and share memory bandwidth.

| Candidate | Per process | Ten concurrent pre-commits |
|---|---|---|
| C1 | 3.48 GB core-only (m), 3.9 GB full (m:src). Cleaned: 2 to 2.5 GB (e). | 20 to 39 GB on a developer laptop already busy with other work. 45 to 75 s each (c, 3x factor). |
| C2 | 0.90 GB peak for decode plus link in the port (m), including a 151 MB file buffer. Add 0.2 to 0.5 GB for Python packs, labels and rules (e): 1.1 to 1.4 GB. | 11 to 14 GB, 7 to 12 s each (c). Rayon threads oversubscribe, so a parallel link does not help here. |
| C3 | steady state 0.3 to 0.8 GB (e); cold path like C1 | steady: 3 to 8 GB, 4 to 8 s each (e). After a fleet-wide engine bump, every worktree pays the cold path once: 20 to 40 GB (e). |

**Consequence.** All pre-commit numbers are p50 on an idle machine. The p90 a developer feels is about 3x higher.

**Fix.** State the pre-commit budget at ten concurrent processes. Run single-threaded when more than N hoglint processes are running. Keep per-process memory under 1.5 GB.

**Adjudication:** partly confirmed. A valuable missing workload; the slowdown of the linker does not establish a universal 3x for Python or a developer p90, and ten simultaneous checks need a measured frequency.

**Led to:** D6 (pre-commit links single-threaded). D6 went through three versions (load-based threads, then one thread with a machine-wide limit D36, which was withdrawn on Nov 10). The gates of D26 measure ten overlapping staged checks.

### F3. C2's 1.3 s pre-commit undercounts the fixed Python steps

**Claim.** blind-b: "labels, rules and ledger 0.2 s; interpreter 0.1 s", total about 1.3 s.

| Step | blind-b (e) | This report |
|---|---:|---|
| interpreter, imports, config | 0.1 | 0.11 to 0.16 (m: import 0.08 to 0.10, loading the config 0.03 to 0.06) |
| snapshot | 0.06 | 0.06 to 0.08 (m:src) |
| store read and decode | 0.2 to 0.4 | 0.20 to 0.28 decode and intern from a flat file (m), plus 0.1 to 0.3 for SQLite and zstd (e) |
| link | 0.3 to 0.5 | 0.42 to 0.57 (m), plus 0.10 to 0.13 to resolve all calls and uses (m), plus 0.05 for the reverse export index and label classes (e) |
| packs | 0.2 to 0.4 | 0.5 to 0.9 warm with per-blob passes; 1.3 to 2.8 as ported (F1) |
| labels and config conflict check | (inside 0.2) | 0.22 path labels (m:src), 0.47 conflict check without owners (m:src) |
| rules and findings | (inside 0.2) | 0.3 to 0.7 (e, see F7) |
| ledger diff, render | (inside 0.2) | 0.1 (e) |
| **total** | **1.3** | **2.2 to 4.0 warm; 3.0 to 5.9 as ported** (c) |

**Consequence.** blind-a's 2 to 3 s holds at its low end. blind-b's 1.3 s breaks. Both are still 20 to 30x faster than the 70 s of the day.

**Fix.** Cache the conflict check by (policy digest, module path set). Budget pre-commit at 2 to 4 s.

**Adjudication:** partly confirmed. 1.3 s is unsupported; the replacement 2.2 to 4.0 s is still a component estimate, not a measured command.

**Led to:** D26 (the gate is the pre-commit path, with a budget per phase: analysis 1.3 s, packs 1.0 s, labels plus rules 1.1 s, ledger and output 0.4 s, margin 0.2 s).

### F4. C2's link claim holds, measured on the real resolver

**Claim.** blind-a: link 0.3 to 0.6 s. blind-b: "Rust link 0.3-0.5 s, measured 2.2 s Python divided by 5-8".

| Phase | Python, GC off (m) | Rust, one thread (m, quiet / under load) |
|---|---:|---:|
| module index, tables, file scopes | 0.37 | 0.14 / 0.17 to 0.24 |
| import producer (333k edges) | 0.85 | 0.06 / 0.04 to 0.07 |
| reference producer (456k edges) | 1.46 | 0.04 / 0.05 to 0.08 |
| declaration producer (606k edges) | 2.02 | 0.11 / 0.18 to 0.26 |
| dotted-path producer (28k edges) | 0.16 | 0.02 / 0.02 to 0.04 |
| MRO of 52,874 classes | 0.16 | 0.02 / 0.03 |
| module graph as CSR | n/a | 0.02 |
| **total** | **5.02** | **0.415 / 0.49 to 0.74** |

- The Rust tables hold 30,016 modules, 150,644 cached name resolutions, 68,153 cached from-resolutions and 361k hits.
- Resolving every aggregated callee and use (1.65M rows) adds 0.10 to 0.13 s.
- Decode plus intern of all facts from a 151 MB per-file binary store takes 0.19 to 0.28 s. Peak RSS is 0.90 GB.

Over 10x faster than Python is enough. Three things change the budget: CI x86 cores are slower per thread (e: 1.3 to 2x, so 0.6 to 1.0 s there); under ten-way concurrency, link plus decode is 2.0 s (F2); memory is 0.9 GB, not 0.3 to 0.7 GB.

**Consequence.** The link is not C2's risk. Packs (F1) and the fixed Python steps (F3) are.

**Adjudication:** partly confirmed. The saved narrow parity was confirmed independently (1,143,531 rows on each side as multisets, zero differences). Full semantic parity is refuted: Python produced 1,423,089 edges, the comparison omitted surface, via, scope, contexts, producer, use position, shape and attributes, the port dropped some declaration and expose attributes, and some discovery and test-recognition behavior was hard-coded for the benchmark. Sub-second native resolution is credible; "exact port" is not.

**Led to:** D1 (the resolver moves to Rust; "the resolver runs 10x faster in Rust (narrow parity)").

### F5. C2's cold CI number depends on the drives escape hatch

**Claim.** blind-a: CI full check 6 to 8 s cold. blind-b: 5 to 8 s on 4 to 8 cores, "including a cold AST pass for drives".

**Measurement.**

- The extractor of the day with every fact kind and no cache: 0.70 s wall on all threads, 2.02 s on 4 threads, 7.2 s CPU (m).
- C2's richer catalog adds use roles, def-use and call arguments: an estimated 1.5 to 2x, so 11 to 14 s CPU.
- `ast.parse` plus a walk (m): all test files 17.2 s CPU; migrations 1.7 s CPU; the whole repo 35.6 s CPU.

**Consequence.** On four cores of this machine, with the drives hatch as ported (c): 3 to 4 s extraction, 0.6 s link, about 5 s hatch, 1 to 1.5 s for the rest; total 9 to 11 s. With a byte prefilter on the hatch: 6 to 8 s. On x86 runners 1.3 to 2x more (e). Everything stays far below the 45 s of the day and the 267 to 278 s Django-booting checks.

**Fix.** Prefilter the drives hatch by kind-name bytes, or rewrite drives as in F1. Measure on a real CI runner before promising 5 to 8 s.

**Adjudication:** partly confirmed. The script measures parsing plus an AST walk, not the proposed pack.

**Led to:** D13.

### F6. C3: the frontier stays small with real dependency traces; the cold path is the cost

**Claim.** The second opinion: "Missing negative dependencies ... expensive dependency validation ... The workload's changed-name counts do not prove the actual semantic query frontier will be small." Cost table: pre-commit 2 to 6 s, full 20 to 35 s.

**Method.** The adversary instrumented the resolver and recorded every read a query engine would have to record: positive and negative name lookups, star-import lists, lazy maps, module existence checks, class bases. It replayed 200 master commits and 200 local commits against that trace, with blob facts for every old and new blob, and recorded the drives read set of every test module.

| Per commit (m) | Master: median / p90 / max | Local: median / p90 / max |
|---|---|---|
| changed `.py` files | 5 / 13 / 150 | 3 / 14 / 13,582 |
| resolution frontier, positive dependencies only | 5 / 14 / 150 | 3 / 14 / 13,585 |
| plus negative lookups, star lists and existence | 5 / 14 / 200 | 3 / 14 / 13,586 |
| commits where negative dependencies widen the frontier | 2 of 200 | 1 of 200 |
| classes whose ancestry is invalidated | 0 / 6 / 38 | 0 / 10 / 11,016 |
| plus files with an edge onto those classes | 5 / 14 / 200 | 3 / 14 / 13,586 |
| drives test modules invalidated | 1 / 6 / 174 | 1 / 3 / 2,443 |

- A rebase over all 200 master commits (Nov 6 to 9, 1,269 changed files) invalidates 1,485 files, 5% of the repo (m).
- Drives read sets: median 1 file (the test itself), max 54. The most-read files are a schema enum module (193 test modules), the query dispatcher (190) and the schema module (168) (m).

**Validation and store size, core trace only (m).** 226,915 queries, 1,552,016 dependency edges, 525,150 keys. 26 MB as int-keyed marshal, 65 MB as a string-keyed pickle. Loading takes 0.10 to 0.20 s and verifying every dependency 0.08 to 0.23 s, in Python. Input fingerprints need every module's name table: about 1 s in Python (e), about 0.05 s if per-blob fingerprints are kept in Rust (e). With packs, rules and cached values: 0.2 to 0.5 GB per snapshot (e).

**Recording overhead (m).** Instrumented core producers took 7.9 s against 4.9 s plain, 1.6x; a built-in recorder should land at 1.2 to 1.5x (e).

**Consequences.**

- C3's steady-state pre-commit can reach 1.3 to 2.5 s (e), if the whole-repo queries are cheap. `origins` alone reruns on every import change (0.11 s, m); the package graph, the module graph for reach, coverage, ledger equality over about 55k findings and levels each read every file's contribution.
- The cold path is Python F times 1.2 to 1.6, 20 to 55 s, on every CI run without a store artifact, after any engine change, on new machines, and once per worktree after a pull of a new hoglint.

**Fix.** Make every whole-repo query an incremental projection. Keep per-blob input fingerprints in Rust. Use a per-worktree revision diff, so a no-change run costs a manifest compare (about 10 ms). Publish a master store artifact for CI, or accept 20 to 55 s.

**Adjudication:** partly confirmed. Useful evidence that most recorded dependencies are local, but not a correct history replay: it loads one trace and applies changes to it rather than recomputing at every revision; lazy-map contents were not fingerprinted; existence and external classification shared one key without propagating repo-head changes; the recursive closure returned empty on a cycle; only `.py` changes entered the replay.

**Led to:** D5 (C3 rejected; no incremental evaluation). Locality came back as the basis of stored answers with traces, tested by a real replay with full recomputation as the oracle (D59, `docs/research/second-opinion-reuse.md`).

### F7. Per-edge rule work under C2 is small; findings, not groups, set the floor

**Claim.** blind-a: "evaluate each predicate once per group: 216k edges, 6,171 groups, 28 ms to 1 ms". Which rules cannot be grouped?

| Rule | Why not per group | Per-edge work set | Cost per item (e) | Total (c) |
|---|---|---|---:|---:|
| public-surface | identity is (src.module, dst.symbol) per finding; `subclass_of` membership is per symbol; the remedy needs a reverse-export lookup per finding | 33,358 finding edges (m:audit); 21,095 after the `uses`-copy fix (c); selected 61,613 | 3 to 6 µs in Python | 0.07 to 0.2 s |
| export-shape | recursive closure per symbol through fields, signatures and bases | 37,483 exposes visited (m:audit) | 3 to 8 µs | 0.1 to 0.3 s |
| model-crossings (use-shape, def-use) | `edge.shape` per use | 3,180 demanded references (m), 40k flow rows | 10 to 20 µs | 0.03 to 0.07 s |
| contracts-no-django (reach) | BFS per source, witness path | 90,864 hops from 5,144 modules (m:audit) | Rust | under 0.01 s |
| no-new-package-cycles | witness module edges per pair | 12,889 edges over 446 pairs (m:audit) | Rust | under 0.01 s; 2.19 s at the time |
| every rule | ledger identity and growth key per finding | 54,575 findings (m:audit) | 1 to 3 µs | 0.05 to 0.15 s |

**Consequence.** Rules cost 0.3 to 0.7 s under C2 (c), which fits blind-a's 0.5 to 1.0 s. The attack on group-by fails on cost. The floor is that every full check materializes all of about 55k findings to test ledger equality.

**Fix.** None needed. Optional: build identity strings in Rust to save 0.1 s.

**Led to:** D1 (rule predicates per label-class group in Python), D18 (findings stay compact until something shows them). D58 later made a rule's findings a table.

### F8. C1's 12 to 20 s needs every interim fix and still misses pre-commit

- Core load plus resolution plus edges costs 5.5 s CPU with GC off (m), at 3.48 GB peak.
- The audit's 12.5 s floor is an estimate; its measured parts are the reference oracle and the mask run.
- Packs (2.9 to 4.6 s GC off), rules after the mask (4.75 s), labels without owners (about 1 s) and the index (0.22 s) sum to 15 to 16 s CPU without demand pruning (c). Demand-only edges save about 3.4 s, so about 12.5 s. With GC on, add 2 to 5 s (e).

**Consequence.** The claimed 12 to 20 s needs GC off or frozen, demand-only edges and lazy owners all at once. Pre-commit equals a full check, and F2 multiplies it under concurrency.

**Fix.** None for pre-commit. Use C1 only as the migration step and CI fallback.

**Led to:** C1 kept only as the source of interim fixes.

### F9. compare cost per candidate

| Candidate | Common path: scanner and policy unchanged | Scanner changed |
|---|---|---|
| C1 | 2 builds plus 2 evaluations: 110 s, 5.4 GB at the time (m:skeptic). Cleaned 2F: 25 to 50 s (c). With the skeptic's base-ledger shortcut: about F, 12.5 to 25 s (c). | 4 evaluations over two engines: 50 to 100 s, plus base engine setup (c) |
| C2 | With the same policy, passes 3 and 4 collapse into 1 and 2: 2 runs of 2 to 4 s, so 4 to 8 s warm (c). Cold CI: 10 to 20 s (c). | Plus a base engine subprocess for 2 runs; a different extractor build extracts both trees again: 8 to 15 s warm, 20 to 35 s cold CI (e). |
| C3 | Incremental head run of 1.5 to 3 s plus base results from the store: 2 to 4 s (e). Without a base trace: Python F, 20 to 35 s. | Every query of a changed implementation reruns: 25 to 55 s on the head side (c) |

**Consequence.** C2 has the best worst case. C3 has the best common case only when the store holds the base snapshot, which on CI means an artifact.

**Fix (as proposed).** Implement the skeptic's base-ledger shortcut in any candidate.

**Adjudication:** the cost comparison is reasonable; the recommendation to implement the base-ledger shortcut is refuted (see the correctness adversary's F2). Only a sufficient, exact-key summary can replace a scan.

**Led to:** D3 and D14 (exact-key run summaries, published by master CI).

### F10. Memory claims for C2 are low by 2x

**Claim.** blind-a: 300 to 600 MB. blind-b: 400 to 700 MB.

**Measurement.** The port peaks at 0.90 GB (m), including a 151 MB raw store buffer, boxed value trees and hash maps. A tighter layout could reach about 0.5 GB (e). The Python side holds labels for 29k modules, pack outputs, cached per-file pack results (drives alone added 0.47 GB, m:src) and about 55k findings.

**Consequence.** Budget 1.1 to 1.4 GB per process (c), still about 3x below C1.

**Fix.** Drop the store buffer after decode. Keep value trees in arenas. Load pack summaries lazily per test module.

**Led to:** the memory gate of 1.5 GB per check; D16 to D19 and D23 (sectioned summaries, compact findings, one fact database, no whole-snapshot expansion into Python, a 1.2 GB target).

### What none of the candidates addressed

- **Concurrency and core types.** Every estimate was one process on an idle performance core. No design capped threads or stated a p90 under load.
- **CI hardware.** All numbers came from one developer laptop. Nobody measured a Linux x86 runner.
- **Fixed whole-repo Python steps on every run:** the config conflict check (0.47 s), path labels (0.22 s), findings for ledger equality (about 55k), owners at render (0 to 0.91 s). None appeared as its own line in C2's or C3's budget.
- **Cold-path frequency.** Nobody measured how often a pre-commit follows a pull, a rebase, a branch switch or an engine bump.
- **Store growth and GC.** The fact cache already held 845 MB in 6 namespaces. No design said when entries expire or who pays for the first run after an expiry.
- **The drives rewrite.** Every C2 cost figure assumed drives reads a few hundred files.

Store growth led to D10 (one cache root, bounded by size and age). The portability gate (Linux CI against macOS with the same key) came with D14.
