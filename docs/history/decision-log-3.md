# Engine decision log, part 3 (D48 to D70)

> The engine decision log of Hoglint, part 3: the public surface in the engine (D48), the packs, N2, memory and the fact cache, the reuse spikes (N7 to N9, D59 to D61), the own repo, contract surfaces (D62 to D65), the ledger and triage (D66, D67), the public record (D68) and the settled contradictions (D70), Nov 10 to Nov 11, 2026.
> Partly replaced: the current state of each topic is in `docs/design/` (start at `docs/design/README.md`); the last section of this file settles contradictions in the earlier entries.

This is part 3 of a chronological log; read the note on how to read it, and who decided, at the top of `docs/history/decision-log.md`. A decision marked "the owner" was stated by Hoglint's owner; every other decision is the architect's (an AI agent), and the owner could veto it.

**D48. The public surface is first class in the engine (the owner, Nov 10).** The owner decided that the facade, or whatever other patterns call a public surface, is a first-class concept of the engine.
- The policy side is first class already: `[exports.*]` (membership by path or by class, with an audience), `[shapes.*]` (what an export may hand out) and the `export-shape` rule.
- The fact side is not: `presets_py/posthog/facade.py` (539 lines) derives what a facade module hands out through re-exports, the types in each signature slot (return, parameter, dataclass field), its declarations and its wiring decorators, with PostHog's layout (`products/<name>/backend/facade/`) written into it.
- So the engine derives those facts itself, for every surface a config declares under `[exports.*]`, with no pack. "Facade" is PostHog's name for one such export; the engine's word stays `export`.
- It follows the owner's standing principle on catch-all folders: a surface is public by its shape, never by its folder. The folder only says where to look; `[shapes.*]` says what may be there.
- When: with the packs decision after the N3 spike, which measures the facade producer's cost. The PostHog pack then holds `drives` only.

## The packs, after the N3 spike (Nov 10)

`docs/research/spike-n3.md`: profile per producer, a Rust port of one producer, coarse native queries in another, all measured end to end in same-minute triples, measured under load (medians of paired deltas, cross-checked on the two quietest triples).

- **Where the 1.3 s goes** (profile shares scaled to the reference load): django.uses 0.31 s, posthog.drives 0.29 s, posthog.facade-facts 0.21 s (0.06 s of it the sink making 42,000 `Edge` objects), django.migrations 0.11 s, installs and signals 0.08 s each (28,000 `may_mention` calls each, one per file), the six small Django producers 0.12 s together.
- **Rust port of django.uses:** 586 lines, equal on 101,466 refinements in order and on the whole graph after the packs (1,475,943 edges, 291,041 symbols), on its first build. The producer drops from 0.69 to 0.22 s in the loaded profile; on the quietest triples the Django pack goes from 0.90 to 0.63 s. So about 0.25 to 0.3 s at the reference load. Not ported in the prototype: the sink's conflict bookkeeping.
- **Coarse queries in posthog.drives:** 83 lines of Rust, 24,000 calls removed, no gain above the noise. The time is in the Python objects of the judgment (`Mention`, call-graph walks), not in the lookups. N1's rule, seen from the other side.
- **A new pattern** (signal receivers with `sender=`): 10 to 30 lines of Python; an estimated 400 to 600 lines of Rust plus a wheel release if the producer lives in Rust.

**D49. The packs move to Rust by measured value, producer by producer; the PostHog plugin stays Python (the architect).**
1. `django.uses` (with `shapes`) becomes a Rust producer in the Hoglint engine, with the sink's conflict bookkeeping. Generic Django, about 100,000 rows, logic that rarely changes: the case a port pays for.
2. Facade facts become the engine's export analysis (D48), written in Rust, so their 0.21 s and their 42,000 `Edge` objects go with the move they need anyway.
3. `PackSink.add_edges` takes columns, so a Python producer makes no `Edge` object per row.
4. `mentions_many` replaces the per-file `may_mention` loops in installs and signals.
5. `drives` stays a Python producer in the monorepo (D15), at about 0.29 s. A coarse query does not help it; a port would put PostHog's query-dispatch logic into Rust and make each of its changes a release.
6. The other Django producers stay Python until a profile shows one worth a port.
- Estimate for the packs phase after 1 to 4: 1.3 s to about 0.65 s. A full port (about 6,500 lines of Rust) would reach about 0.45 s.
- **What this means for the target, said plainly:** with a Python shell, a Python PostHog plugin and the Django pack partly in Python, one check lands at about 2 to 3 s wall on a laptop after N2, N3 and N5 (estimate: shell 0.65, analysis 0.3 to 1.15, packs 0.65, rules 1.0 on one thread, less on four). The 1 s target set earlier that day needed everything in Rust. The owner's bar is 4 s acceptable, effort decides; this stays under it with about half the port.
- Order: after N2, because both touch the same Rust crates. N3a = items 1, 3, 4; N3b = item 2, after the architect writes the design for the engine's export analysis.

## Phase N2 landed, and its reviews (Nov 10)

**N2 as built (a22fc4b8e84..a3ab31d45fb, fifteen commits).** Every rule type is judged in the evaluator; Python reads module labels from its columns; a finding keeps its edge as a handle; the stored summary reads witness rows from the link; a ledger write judges evaluator findings again; the selection path is deleted. The architect's pairs against the commit before it: a staged check 5.0 s to 3.85 s wall on one thread and 4.7 s to 3.5 s on four; the rules phase 2.15 s to 1.0 s on one thread and 1.85 s to 0.67 s wall on four; peak 1.40 GB on both sides. The architect's gates passed at a3ab31d45fb.

**The checkpoint worked as a control.** The first three steps (labels read from the evaluator, a finding's edge as a handle, signature in Rust) were measured in six pairs before the rest began: 1.1 to 1.4 s, measured under load, against a bar of 0.45 s. The rule from N1 held: each step named the Python consumer it removed.

**Reviews.** A code review (ten claims, by reading) and the adversarial review, which also reproduced or refuted the code review's four correctness claims:
1. HIGH, missed findings: an entity inventory that also sets `from` drops entities natively (Python 3 violations, native 1). The schema accepts `from` there, Python ignores it, the plan sent it.
2. MEDIUM, a lost config error: a conflict that only a level meets passes natively. `level_groups` reads labels in the evaluator after the met conflicts were raised.
3. MEDIUM, a promise not kept: the ledger-write rejudge covers edge, entrypoint, signature and use-shape rows; a made-up export, layout or inventory row is written to the ledger as accepted debt.
4. LOW, inferred: the rejudge compares the judgment, not the identity.
- Refuted: reach with no hops (the schema refuses `endpoint` on a reach rule).
- No exposure in the PostHog preset: the ten tables are equal. As after N1, every defect sat in a config shape no generator made.

**D50. A ledger write checks every rule type and the identity.**
- Rules with a per-row Python judge: the rejudge compares the judgment and the identity `project` gives.
- Export, layout and both inventory kinds: the rule's Python primitive runs whole, and its findings and incompletes must equal the evaluator's. These primitives cost about a second together, on a command a developer runs by hand.
- `check` stays as it is; D42 (the shadow run) is the net for a clean row that should not be clean.

**D51. Text is worded in one place.** The evaluator returns rows as data. A finding's message and an incomplete's reason come from one builder on the rule's Python class, which both engines call. N1b removed a Rust imitation of Python's `repr`; N2 added another (`python_repr` in `export.rs`), and four messages existed twice. Same principle as D44 for conflicts.

**D52. A key a rule does not read is a config error.** The schema accepted `from` on an entity inventory, the Python rule ignored it, and the two engines then disagreed about what it meant. Every rule type and mode gets the same check: what the schema accepts is what the rule reads.

**The generator follows the schema.** For each rule type every accepted key is present in some generated configs and absent in others, and levels are generated. Three rounds of review found their defects in shapes the generator did not make; a generator built from the schema's key lists cannot fall behind it.

Built as phase N2b, in parallel with N3a on disjoint paths.

## D48 in detail: what "first class" still needs (Nov 10, from `docs/research/facade-facts-map.md`)

A read-only study of who reads the facade producer's edges changed the size of this work.

- **The engine's public surface is first class already.** The core link emits `declares`, `decorate`, `inherit` and `exposes` edges for every module. `[exports.*]` names the surfaces, and the export primitive walks core edges by itself.
- **Most of what the PostHog facade producer writes has no reader in the engine.** No rule can name an `export` edge (the kind is in no kind list). Its `declares`, `inherit` and `decorate` edges repeat core's under other attr names, so the symbol facts ignore them. Seven of its attrs (`mode`, `def_kind`, `hops`, `defined_here`, `whole_any`, `stub`, `wiring_decorator`) have no engine reader. Their one reader is `legacy_ledger.py`, the script that proves Hoglint against the old scanner (the "crossings 968 + 2" invariant).
- **Three things it adds that rules do read**, all through `exposes` edges:
  1. the signature slots of a function a surface re-exports from elsewhere;
  2. a slot for a parameter with no annotation (`annotation=missing`); core skips those;
  3. the fields of a class decorated with PostHog's `@frozen`, as `param:<field>` slots of `C.__init__`. Core knows `dataclasses.dataclass` only (its `dataclass_decorators` setting) and names such slots `field:<field>`.

**So N3b is smaller than a port of 539 lines, and it has a policy side:**
1. The engine derives the three additions itself for the surfaces a config asks it for: unannotated slots and re-exported function slots in the core producer or the evaluator, and `dataclass_decorators` gets `posthog.dataclasses.frozen` in the preset.
2. The facade producer then runs only for the legacy ledger script, which keeps it until both are retired. A normal check no longer pays its 0.21 s or carries its 42,000 edges.
3. **This cannot be zero delta.** The edges table loses the producer's rows, and where a slot's name changes (`param:` to `field:`) a finding's identity changes, so ledger lines change. The gate is a named delta: findings, incompletes, levels and scan lines equal except the rows listed and explained; the edges table equal after the producer's rows are set aside.
4. **Open, and partly the owner's:** whether an unannotated parameter on the other surfaces (`exports.registration`, `exports.presentation-adapter`) becomes an unknown too. Only the facade got them, because only the facade producer wrote them. Making it uniform is the first-class reading, and it adds findings to PostHog's ledger. The architect proposes it with the count when N3b is measured; until then the preset keeps the existing reach through a per-export setting.
- When: after N2b and N3a are verified.
- Later: D62 corrects this scope.

**D48, point 4 refined (Nov 10; the question: what does "treating all three surfaces alike" mean?).**
- Rough count on the snapshot (the architect's own script, plain `ast`, public functions and methods with a parameter that has no annotation): facade 43 of 2,578 (56 parameters, flagged already); registration files 0 of 79; `backend/presentation/**` 400 of 2,808 (511 parameters; the adapter surface is a subset of these classes, mostly DRF methods such as `list(self, request)`).
- So "alike" is the wrong design, not only a costly one. A facade is a call contract: its signatures are the contract. A presentation adapter is exported to be subclassed: its method signatures are DRF's, not a contract with callers.
- **Design (the architect's): the shape says whether signatures are part of the contract.** `[shapes.<name>]` gets one key for it (typed signatures required, an unannotated slot is an unknown). The engine writes the marker for the surfaces whose shape asks. PostHog's preset sets it on `contract` and not on `registration`, which is exactly the existing behavior, now stated in config instead of following from which producer ran.
- The owner's, small: turning it on for the registration files too would add no finding (all 79 functions are annotated) and would keep them that way.

**The per-surface signature question is parked (Nov 10)** (D48 point 4: whether unannotated parameters on other surfaces count as unknown). It needs more context; the preset keeps the existing reach until the numbers are in.

**After N2b (Nov 10), two keys nothing reads, left for the architect by the agent.** `flow` and `resolve_calls` on edge rules are accepted and read by no code. The preset's `drives` rule sets `resolve_calls = "bare-name"`: it states how the drives producer resolves calls, and nothing checks the statement. Decided (D52 applies): the preset drops the key, and the schema then refuses both on edge rules. It changes the preset's policy hash and no finding, so it goes into a preset change, not into an engine commit of its own.

**Next engine step: memory (N5), by a spike first.** The peak stayed at 1.40 GB through N1 to N3a while a check went from 5.0 s to about 3.6 s. The spike: a memory map at HEAD, where analysis time goes, and two of three prototypes measured end to end (a mapped fact store, packs reading facts through the link, earlier release). Scratch only; the architect decides the phase from its numbers.

## After the simplification reviews of the evaluator (Nov 10)

Four read-only reviewers read the evaluator at ad83640b66e (reuse, simplification, efficiency, altitude). The altitude review named the cause behind the day's defect family.

**The cause: nothing in the code says what a rule type is.** Its parts are worked out again in seven places: `JUDGED` and `RulePlan.mode` (`plan.py`), `RUN_WHOLE` and `found_codes` (`native/rules.py`), `rule_mode`, `entity_rule` and `UNREAD_KEYS` (`config/schema.py`), `MODES` and `ENTITY_MODES` (the test generator), and the option bag of the Rust `Rule`. Each fix that day corrected one copy. They already disagreed once more: the schema checks a coverage rule's identity as an entity identity and Rust compiles it as an edge identity (harmless only because Rust never reads it). `UNREAD_KEYS` came from one manual audit, and its test checks the schema against itself.

**D53. A rule type is declared once, on its class.**
- Each primitive class states its native mode, whether it runs over entities, how a ledger write confirms it, which judgments make a finding, and the config keys it reads per mode (`READS`).
- The schema's unread-key check, the plan's mode, the ledger-write set and the generator's modes are derived from those statements.
- The plan for a rule is built by the rule instance (`rule.plan()`), from the selectors and defaults the class already derived, not by a second builder that restates them. The inventory `from` defect sat in that restatement.
- The Rust rule is a tagged enum (mode, then verdict kind), not a set of options plus a mode, so a combination Python cannot plan is a decode error and not a runtime check.
- One table-driven test holds it: for every mode, the class reads exactly the keys it declares, and the plan reads only keys the class reads.

**D54. The evaluator says which columns it reads (supersedes the guard half of D43).** `Rules.compile(plan)` returns the provider fields its compiled rules read; Python then sends those columns. `names_read` goes, and a column the plan lacks cannot happen. D43's rule stands as the invariant; this makes it true by construction.

**D55. D50 completed: every row a ledger write stores is confirmed with labels the evaluator did not compute.**
- The row rejudge reads labels from a plain Python `Labeler`. Before, it read the evaluator's own columns, so a wrong label passed on both sides.
- Reach, cycle, coverage and the level groups join the rules that rerun whole in Python on a ledger write.
- Not chosen: rerunning every edge rule whole in Python on a ledger write. Python per edge costs about 12 s on the PostHog monorepo, and a ledger write only needs the rows it stores to be right. A row wrongly judged clean is D42's job (the shadow run).

**Smaller, same family:** judgment and verdict codes travel as names, not as positions in an enum; `NO_PLACE` is defined once; `explain` passes a flag instead of swapping the labeler's evaluator to `None`; the Django shape vocabulary (managers, write methods, terminals) reaches the Rust producer from the Python tables, so the two cannot drift.

**Efficiency, from the review's reading (estimates until measured):** the largest bucket of the rules phase is Python, not Rust: one `Finding`, `EdgeHandle` and `Rejudged` per row for 41,295 rows, and `mark_accepted` rebuilding nearly every finding (about 0.3 to 0.4 s of 1.05 s). In Rust: the module of an edge's surface is found by string hashing per edge; destinations are built before the cheapest test that rejects the edge; candidate lists are rebuilt and sorted per rule; rules run one after another (about 0.15 to 0.3 s, and 0.1 to 0.2 s wall on four threads).

**Order (the architect's):**
1. **N2c, structure, no behavior change:** D53, D54, D55, the smaller items, the reuse and simplification lists, and `mod.rs` split by concern.
2. **N4a, the rules phase as one call, measured with a checkpoint:** findings stay rows through scan lines and accepted marks (a `Finding` is made for a new, printed or blocking one only); every rule judged in one parallel call; then the small Rust items, each kept only if pairs show a gain.

## Memory, after the N5 spike (Nov 10)

`docs/research/spike-n5.md`, on an export of ad83640b66e: a memory map of one staged check, the split of analysis time, and two prototypes measured end to end.

- **The 1.4 GB was the wrong number.** It is `ru_maxrss`, reached at the end of the run because freed pages stay resident. The memory the process owns (physical footprint) peaks at **1.25 GB, inside the PostHog facade-facts producer**.
- **Who holds the 1.25 GB:** Rust 0.74 GB (decoded fact rows 0.24, interner 0.15, edges 0.14, scopes, keys, tables and caches 0.15, not attributed 0.06); Python 0.34 GB (decoded pack records 0.19 to 0.22, the graph object 0.07); allocator slack 0.14 GB.
- **Fact rows are already binary in SQLite.** Reading, decoding and interning them is 0.29 s of a check. JSON is only what the packs read.
- **A mapped fact store (prototype A, 389 lines):** peak 1.25 to 1.11 GB, and about 1 s more CPU per check, because rows are decoded again on demand and the packs scan them 261,000 times. A full version with in-place views could keep the time flat; it can remove 0.24 GB at most and needs an estimated 2,500 to 3,500 lines.
- **Releasing each pack step's decoded records (prototype C, 31 lines of Python):** peak 1.25 to 1.16 GB, no time cost.
- No result changed under either (whole-graph digest equal).

**D56. D37 is withdrawn as the memory plan; the 0.5 GB target goes with it (the architect).**
- D37 assumed the per-run decode of facts was most of the memory. It is a fifth of the peak, and a mapped store that removes all of it costs about 3,000 lines.
- What the tool holds is the linked graph of the whole repo (edges, names, scopes: about 0.45 GB) and the facts it was linked from. ruff and tach do not build that; ty, which does more, takes 7.2 GB on the PostHog monorepo.
- **What the architect does instead, in order of cost:** release decoded pack records after each producer step (0.09 GB, 31 lines); N3b takes the facade producer out of a normal check, and the peak sits inside it; measure memory as physical footprint from now on, with RSS next to it. Expected landing: about 1.0 to 1.1 GB. That is an estimate.
- Not planned: a compact representation of the link's own structures. It is where a real cut would come from, and it is a rewrite of the link.
- On the question of 2 GB for a linter baseline: the two-runs-in-one-process case that gave 2.33 GB is fixed (4a), a check owns 1.25 GB, and about 1.0 GB is what cheap steps reach.

**Two findings about time, from the same spike:**
- **Snapshot and discovery are 0.42 s of Python per check** (listing 29,000 files from git and building the list), the largest single item in the analysis phase (link proper 0.33 s, index and scopes 0.11 s, decode 0.10 s, SQLite 0.12 s, interning 0.08 s).
- **Under load a warm check often pays 1 to 2 s of disk reads**: the fact database is 459 MB for 183 MB of fact bodies, and its pages fall out of the page cache (15 of 26 base lines in the spike). On a developer's laptop this can outweigh every CPU saving of the day.
- Both go into **phase N5a** with the pack-record release: measure, then cut what a measurement shows.

## The fact cache's size (Nov 10; the question: why is it over 300 MB?)

Measured by the architect on a live fact database (read-only, the architect's own script) and on the preset snapshot:

- Python source: 266.6 MB in 29,099 files; 26,694 distinct contents have a cache entry.
- Database 483 MB = JSON records 263 MB (mean 9.9 KB per file) + binary fact bodies 183 MB (mean 6.8 KB) + keys 2 MB + SQLite page slack about 35 MB. The cache is 1.8 times the source it describes.
- Both tables encode the same extracted facts (`src/encode.rs` writes the JSON line from the same `FileRecord`). The JSON exists for the Python packs, which read about 31 MB of it per check.
- JSON by kind: defs 21 %, callees 17 %, assigns 17 %, refs 14 %, imports 9 %, uses 8 %, derived 5 %, the rest 9 %.
- Nothing is compressed. zstd level 3 per row: JSON 4.2x (5.0x with a shared dictionary), binary 3.3x (3.9x). Decode through Python: JSON 680 MB/s, binary 430 to 470 MB/s, so a check that reads all 183 MB of bodies would pay about 0.4 s of CPU on one thread.
- The cache root measured held 2.5 GB against its 2 GB bound: three databases (three engine versions, each a new facts id), four directories of the Python engine's per-file records (260 MB each, one 135 MB), and 60 empty directories that the prune leaves behind. The bound is soft for a day, because the prune removes only units no run opened since the day before.

**D57 (the architect's; a direction, not yet built). The cache holds one copy of the facts.**
1. Drop the `records` table. A pack that asks for a record gets it decoded from the binary body by the binding. Gate: zero delta, and the packs phase not slower in pairs.
2. Compress the bodies only if same-minute pairs show no slower warm check. Try a fast codec first; zstd's measured decode speed costs up to 0.4 s on one thread.
3. The prune removes a database of another engine version as soon as a newer one has been opened twice, removes empty directories, and the bound goes from 2 GB to 500 MB.

Target: under 100 MB per engine version without compression loss of time, about 60 MB with it. The owner had not decided anything here; the bound is the owner's to change. Revised below.

## The rules phase after N2c: where one check's time goes (Nov 10, the architect's profile)

One warm staged check at 2ac1c21e243 under cProfile, one thread, measured under load. A profiler inflates Python against native code, so these are shares, not times. Whole check 8.96 profiled seconds:

| Part | Profiled s |
|---|---|
| Analysis (`link_blobs` 1.11 of it) | 1.97 |
| Django pack | 1.06 |
| PostHog pack (drives 1.07, facade facts 0.68) | 1.94 |
| `Engine.evaluate` | 2.09 |
| After it: `summary_of` 0.23, `store.put` 0.53, `report` 0.28, temp directory cleanup 0.25, site-packages walk 0.19, `policy_of` 0.18 | about 1.8 |

Inside `Engine.evaluate`: `Rules.judge` (native, 22 calls) 0.65; Python around the judged rows 0.26; `mark_accepted` 0.21 (41,298 `dataclasses.replace`); the coverage rule 0.32 (the turbo adapter globs the file system and strips 207 `package.json`); two `Labeler` constructions 0.30 (the owners provider opens twice, 0.19); evaluator compile 0.14; `pairs` 0.11; `export` 0.09; `reach` 0.07.

**What each of the 41,295 findings costs in Python, end to end:** made as `Finding` + `Rejudged` + `EdgeHandle` (0.26), replaced by `mark_accepted` (0.21), made again as `FindingRecord` (0.23), turned into lists and dumped (0.42 of `store.put`'s 0.53). Together 1.2 of 8.96 profiled seconds. All but a handful are accepted debt that nothing prints.

**D58 (the architect). A rule's findings are a table; a `Finding` is made for a reader that needs one.**
1. `RuleRun.findings` is a table in both engines: columns per row (edge id, destination, judgment, source and destination module, flags) and the distinct identities with the rows that carry each. The native engine fills it from the evaluator's columns. The Python engine fills it from the `Finding` objects it makes. Every later step reads the one table type, so the two engines cannot differ after the rules.
2. The steps that read every finding work on keys and columns: exceptions, scan lines, accepted marks, levels (only rows that can block), the summary's findings section. Status is per identity plus the few rows an exception allows; no row is copied to change it.
3. A `Finding` object is made on demand for a row something prints, explains or judges again: new findings, `--changed` debt, a ledger write's confirmation.
4. The summary's findings section is written from the columns. Its stored form may change (protocol number up); a reader of an older summary runs the check, as for any unreadable one.
5. Then, each kept only if same-minute pairs show the gain: every row-confirmed rule judged in one parallel call; the Rust items the efficiency review named (module of an edge's surface, destinations built before the cheapest test, candidate lists per rule); the labeler built once.

Expected (an estimate from this profile, to be replaced by pairs): 0.3 to 0.4 s of a 3.75 s check on one thread. Not in it: the coverage adapter's file reads, the temp directories of an index check, `policy_of`, the site-packages walk (N6). They are listed for a later phase on the check's shell.

**Round 6 timing (the architect's, ad83640b66e against 2ac1c21e243, measured under load, so absolute times are higher than the earlier ones):** one thread, six pairs: whole check median 5.40 against 5.36 s (median of pair gaps +0.03), rules phase 1.48 against 1.47, PostHog pack 0.84 against 0.87, peak footprint 1.25 against 1.16 GB, peak RSS 1.38 against 1.31 GB. Four threads, four pairs: 4.82 against 4.83 s (median of pair gaps +0.06). No measurable change in time; 0.09 GB less memory.

## Should a stronger database back Hoglint? (Nov 10, a brainstorm, nothing decided)

The question (Nov 10): should Hoglint, unlike ruff and tach, sit on a database with the convenience of SQLite and the speed of a server store, and what does the Rust world have? The architect's answer, with the numbers it rests on:
- **Storage is not where the time is.** SQLite reads the keys and the 183 MB of bodies in about 0.1 s of a 3.75 s check (N5 spike). A faster store moves that 0.1 s. The cache's size is a duplicate copy (D57), not SQLite.
- **A general query engine would be slower than the evaluator.** The rules are already selectors over an edge table; the special-purpose evaluator judges 1.48 M edges in about 0.45 s.
- **The form of the idea that pays is incremental reuse.** Only per-file facts and whole-tree summaries were cached. Link, packs and rules run over the whole repo on every check (about 3.2 of 3.75 s). CodeQL and Glean keep a program database for this reason, at a far larger scale. For a pre-commit check near 1 s, not redoing work is the only route left: D49 puts whole-program recomputation at 2 to 3 s.
- **Two ways to do it.** (a) Memoized steps keyed by content, as a build system does it: per file or per module, with the answers in the same cache. It needs steps that are pure functions of few inputs, not a new database. (b) A daemon that keeps the graph in memory and applies a change: fastest, and it holds over 1 GB while idle, needs a lifecycle, and does nothing for CI.
- **Rust-world names the architect gave (from memory, not checked that day):** salsa (incremental queries, in memory; rust-analyzer and ty use it), redb and LMDB through heed (embedded key-value files), fjall and RocksDB (LSM stores with block compression), DuckDB (embedded columnar), CozoDB and the Datalog crates (ascent, datafrog), differential dataflow.
- **What the architect proposed: a spike before any design.** For a sample of real commits, measure what one changed file changes in the graph and the findings, and how many other files' edges move with it. If the answer is "the file and its direct importers", per-module memoized steps reach about 1 s. If changes spread wide, only a daemon does.

## N7 spike, the architect's part: what salsa is and how ty uses it (Nov 10, read from primary sources)

Decided (owner, Nov 10): a spike, as cheap as necessary, with salsa and ty's use of it in scope. An agent measured the spread of real commits. The architect read the sources:
- **Salsa is an in-memory framework** for on-demand, incremental computation: inputs, tracked functions whose reads are recorded, memoized results, and a red-green check that reuses a result when what it read did not change (salsa README and book, version 0.26.1 on docs.rs).
- **It was built for a long-lived process.** Its issue #10, "Serialization to disk", is open since 2018 and sits in the "Far future" milestone. In it a Ruff maintainer wrote in April 2024 that a persistent cache is essential for CLI and CI use.
- **A persistence prototype exists.** PR #967, "Initial persistent caching prototype", merged in August 2025 behind the `persistence` feature. Its author lists the limits: every transitive dependency must be serializable, there is no easy way to update inputs after they are deserialized, query hashing is not implemented. The second limit is the pre-commit case itself: load, change a few files, recompute.
- **ty uses salsa for the editor and for `ty check --watch`**, where one process stays alive (ty docs, "Type checking"). The architect found no source that says a one-shot `ty check` reuses results of an earlier process; ty's published speed figures are without caching. A note by an engineer on the ty team says ty wants results to persist across invocations and points at issue #10.

**What follows for Hoglint (the architect's):**
- Salsa pays only inside a process that stays alive. For a hook that starts a new process per commit it gives nothing, unless Hoglint runs as a daemon or takes on the persistence prototype with its limits.
- Adopting it means the link, the packs and the rules become demand-driven queries with recorded reads. The link is one batch pass over shared tables, and the largest pack producer is Python (D49). That is a rewrite of the core, not an addition.
- Reuse between processes, without salsa, is what Hoglint already does at two levels (facts by file content, summaries by tree): the open question is the levels between, and the spike's spread numbers answer whether per-file or per-module answers can be reused.
- So the choice the spike informs has three arms, not two: (a) content-keyed stored answers per step, no daemon; (b) a daemon with an in-memory graph, where salsa or a hand-written delta applies; (c) stay at whole-program recomputation near 2 to 3 s.

**N4a checkpoint 1 (the agent's numbers, no profiler, measured under load):** the four per-finding consumers cost about 0.25 s of a 4.2 s warm check (made 0.08, `mark_accepted` 0.07, `FindingRecord.of` 0.06, the write about 0.035); native `Rules.judge` 0.47 s. The architect's estimate from the profile was 0.3 to 0.4 s: again too high, as the profiler weighs Python heavier than it is. One plain check makes 82,549 `Finding` objects for 44 new findings. Decided: go on in proportion: only judge rows become columns; no new stored format unless pairs show the gain; step 6 (the judge call itself) matters as much.

## D57 revised after N6's first measurement (Nov 10, the architect)

**The architect's premise was wrong.** The architect wrote that the cache stores the same facts twice. The agent compared all 26,386 entries: the binary body is a reduced projection for the link, not a copy.
- Whole keys of the JSON are not in the body: derived 11.3 MB, branches 3.0, sha256 2.0, watched 0.6, cfg 0.4, bytes 0.3.
- Defs, assigns, strs and calls are reduced in the body (a call loses its arguments, a def its parameter kinds and defaults). Those are 66 % of the bytes the packs read.
- What the packs read in one check: 10,257 loads over 3,519 files, 28.5 MB of JSON; warm 0.09 s in SQLite and 0.11 s in `orjson.loads`; with the file evicted 0.7 to 1.3 s in SQLite.

**So "one copy" would need a new lossless body format** (the agent proposed binary JSON with a string table per entry) and a link decoder moved onto it, with more intern calls in the analysis phase. Not chosen: it is several hundred lines of a format the project then owns, in the path every gate depends on, for a cache-size fix.

**D57 as it stands now:**
1. The records table stays, and each entry's JSON is stored compressed. A record read decompresses one entry; packs and `Link.raw` do not change. Expected from the architect's sample: 263 MB to about 60 MB with zstd; the cold read of a check drops from 132 MB to a few MB.
2. The bodies are compressed only if same-minute pairs show the analysis phase and the whole check no slower than 0.03 s on one thread.
3. The prune and the 500 MB bound as before.

Expected database size: about 250 MB with step 1 alone, 120 to 180 MB if step 2 passes its pairs (an estimate until measured). The cache is then an index of 0.5 to 0.9 times the source it describes. Getting far below that means the packs stop reading records at all (D49's question, not this phase's).

**Correction on record.** An earlier statement that the cache stores the same facts twice was wrong. Right is: it stores the full record as JSON for the packs and a reduced binary form of it for the link, and neither is compressed.

## Code review of N2c + N5a (Nov 10, on ad83640b66e..2ac1c21e243)

Ten findings; the architect read the code for each. Outcome:

| # | Finding | The architect's reading | Where it goes |
|---|---|---|---|
| 1 | Rows the rule's own code reports (`judged_alone`: unresolved-as-finding, unknown-violation; 662 rows in the preset) are not confirmed with plain labels on a ledger write | Confirmed: `confirm_findings` walks only `self.rejudged` | N4a step 5 |
| 2 | A row's subjects and the level results are not confirmed; `confirm_levels` compares group sets only | Confirmed by reading | N4a step 5 |
| 3 | A cycle rule lists `remedy` in `READS` but never reads it | **Rejected**: it reads it through `on_unresolved` -> `emit` -> `remedies()` | none |
| 4 | `cycle_rows` and `reach_found` convert names outside `engine_errors()` | Confirmed; low (a bare `ValueError` in place of an engine error) | N4a, small commit |
| 5 | `classify = false` with `exactly_one`: the schema checks an entity inventory, the engine runs an edge inventory over a file named "False" | Confirmed; older than the range; a config shape the generator does not make | N4a, small commit: `classify` must be a string |
| 6 | A ledger write reruns reach, cycle and coverage whole in Python; no memory figure | Known and accepted for time (D55); memory not measured | N4a reports the footprint |
| 7 | The plan is serialized, parsed and compiled twice per check | Confirmed by reading | N4a step 6, by pairs |
| 8 | `found_rows` copies every row and builds `Judgment` per row | Confirmed; N4a removes it | N4a step 2 |
| 9 | Rule classes import `hoglint.native.graph` for `CONTEXT_BITS`; `context_bits` exists twice | Confirmed: the meaning depends on the shell | N4a, small commit |
| 10 | `count_ordinals` defined after its caller | Confirmed | N4a, small commit |

**D55 is not complete until 1 and 2 are fixed.** Both are the same family as before: a ledger write trusts something the evaluator computed (a label on a row the rule code reports; a module name; a group label). No preset exposure is known: it needs a wrong evaluator answer first.

**D57, the bodies (Nov 10, the architect): not compressed.** The agent built it in scratch (lz4 per body, decoded in the parallel read) and measured 12 warm pairs per thread count: database 305 -> 209 MB; analysis phase +0.09 s CPU on one thread (middle half of the pair gaps +0.03 to +0.16) and +0.11 s on four; cold, on four threads, -0.15 s wall. The architect dropped it: 0.1 s on every warm check for 96 MB of disk is the wrong trade while the check is recomputed whole. It comes back if a later design reads bodies for changed files only. The cache for the PostHog monorepo is therefore about 305 MB (records lz4 94 MB, bodies 182 MB, slack), down from 465 to 483 MB. (The last section corrects this figure to 321 MB for layout 4.)

## After the N7 spike: how a check can stop redoing work (Nov 10, night; the architect's view, written before any second opinion)

**What N7 measured** (`docs/research/spike-n7.md`; 30 consecutive Python-touching main-branch commits plus 3 chosen hub commits, 48 trees, native engine):
- In 32 of 33 commits every edge, finding, incomplete item and label that moved was in a file the commit changed. In the one exception a deleted package moved the origin of a re-exported name, and the change reached importers one and two hops away along the re-export chain. Nothing moved in a file with no import path to a changed file.
- A change to a file with more than 2,500 importers (a core model file, a facade types module, a models module) moved no importer's edge: importers name a target by entity, so an edit that moves no origin does not reach them.
- Reach, cycle and coverage never moved. Levels moved in 2 of 33 (block lists only).
- The floor, a check that hits the stored summary of its tree: 0.55 s wall, 0.36 s CPU (listing 0.10, store read 0.10, output 0.08, policy files 0.06, start 0.05, write-tree 0.05).
- Limits the agent named, and one the architect adds: the spike shows where outputs moved, not whether a step can prove "unchanged" from what it can see locally; the sample is one day of mostly leaf edits; a finding was classed by the nearest of its witness and subject file, so a finding in an unchanged importer whose destination changed counted as "changed file". A rule's answer for an edge depends on the labels of both ends.

**The architect's reading.** Outputs are local, so reuse can pay. Whether it can be made exact depends on a second question: can a file's stored answer name everything it read from other files, so that "none of it changed" is checkable without recomputing?

**The model that fits Hoglint's identity design (D3, D4, D5, D28): stored answers with a record of what they read.** No daemon, nothing mutable, everything keyed by content.
- Every file has an *interface*: what other files' answers can read from it (its definitions and their kinds, bases, decorators and signatures, its imports that serve re-exports, the facts packs attach to its symbols, its module's labels). The interface has a digest.
- Every stored answer for a file (its edges per producer, its symbol facts, its rule rows) carries a *trace*: the files it read from and the interface digest each had.
- A check lists the tree, builds the table of interface digests, and reuses a file's answer when the file's content and every digest in its trace are the same. It recomputes the rest. A change that leaves a file's interface alone reaches nobody: that is the 2,500-importer case.
- Whole-graph rules read a small relation folded from the per-file answers; their result is reused when that relation's digest is the same.
- The tree's summary is the base tree's stored summary with the recomputed files' rows replaced.
- Cold (CI, first run) is the full run.

**The solutions the architect sees, to compare for the owner:**
- **S0, stay:** whole-program recomputation, 2.7 to 3.3 s after the current phases (estimate), floor 0.55 s only for an unchanged input set.
- **S1, prewarm:** no engine change. A git `post-index-change` hook starts the normal check in the background when the index changes; the pre-commit check then hits the stored summary. Hides the latency when staging and committing are seconds apart; does nothing for `commit -a`; spends a full check per index change.
- **S2, stored answers with traces:** the model above. Reaches floor plus work for the changed files. The cost is the boundary work: the link's per-file step and each pack producer must be a function of a file plus traced reads. Deliverable in stages (core edges, then rules, then packs).
- **S3, daemon:** the graph stays in memory and a change is applied to it. It needs the same invalidation logic as S2 (what does a change reach), plus a process lifecycle, and holds about 1 GB while idle. Salsa fits here and only here. A daemon that recomputes everything saves little: the time is computation, not start-up. So S3 is a step after S2, not a way around it.

**What the architect tests that night, in scratch:**
1. **S2's premise, on N7's 33 commits:** define the interface and the trace from data the engine already has; predict per commit which files' answers are invalid; the files whose answers really changed must all be in the prediction (a miss means the interface is wrong and names its carrier); the size of the prediction is the recompute cost. Rule rows are tested by site file with the labels of both ends.
2. **S1 as a prototype** on a staging repo: three flows (stage, wait, commit; `commit -a`; a ten-commit rebase), perceived wait and CPU spent.
3. **A second opinion from another model** on the architecture question, given the facts and numbers and none of the above.

## The second opinion on the architecture (Nov 11)

The second opinion was given the facts and numbers and none of the architect's conclusions (`docs/research/second-opinion-reuse.md`). It chose the same model as the architect's S2 and named the same next step.
- **Its pick:** persistent, dependency-validated reuse of producer outputs, with conservative whole-program fallbacks; the unit is one producer's result for one file; an old answer is valid when every recorded dependency has the same current answer; propagation stops when a recomputed answer is equal. Reverse dependencies and a manifest of the snapshot, so a launch does not walk the whole dependency graph.
- **Its first step:** a "dependency-certificate experiment": record dependencies on the parent tree, predict what is reusable, compare each predicted reusable output with the child's full output. This is N7 part 2, which was already running.
- **What it added to the architect's view, and the architect takes:**
  1. The largest silent-failure risks are **absence and set membership**: a name looked up and not found, a new subclass, a new file that mentions a searched name, a registry that gains a member. Each needs its own recorded dependency. Pack producers should split candidate discovery from per-file work.
  2. **Unchanged findings do not prove an unchanged input** for whole-graph rules: recompute when the relation they read changes, and keep edge counts in that relation.
  3. **Order and multiplicity are part of the contract**; N7's digests have no order. The ten parity tables do.
  4. **Persist semantic identities, never interner numbers or graph positions.**
  5. A targeted mutation list for the hard carriers; the architect sent it to the N7 agent as part 3 (ten mutations).
- **On the other options:** a daemon does not supply invalidation and start-up is only 0.05 s, so it comes later if loading dominates; a background hook run is opportunistic and needs coalescing; an embedded database is storage, not correctness, and SQLite is a fine start; a remote database is rejected.
- **On salsa:** it gives the tracked-query semantics wanted, and its docs show persistence options, so it is not memory-only. It cannot see reads inside Python producers. The second opinion favors a narrow layer of Hoglint's own first, and would reconsider salsa if that layer grows into a generic scheduler.
- **Its planning range:** 0.7 to 1.1 s for a one-file edit, only if the packs and the rules are incremental too; the linker alone cannot deliver it.
- **Its stop conditions:** broad dependencies force most packs and rules to run; validation costs approach recomputation; the Python producers cannot be confined to tracked reads; or the hook is not used enough to justify a persistent incremental engine to save roughly three seconds.
- **Two corrections it asked for, both right:** the analysis substeps the architect gave it sum to 0.63 s of 1.05 (the rest is the Python snapshot and discovery, 0.42 s), and the Django migrations pack has a narrower cache of its own.

**Correction on record (salsa).** An earlier statement that salsa's saving to disk is a prototype behind a flag was wrong. The current docs (salsa 0.28.5, `attr.tracked`) show a `persist` option on tracked structs and functions under the `persistence` feature, so it is a documented, feature-gated part of the API. Whether it loads a database of Hoglint's size in a fresh process fast enough, and lets inputs change after a load, cannot be read off the docs. So the architect started **N9**: a reduced model of the link's name resolution on the real 29,000 files, on salsa with persistence, measured in fresh processes.

**Three measurements decide the reuse question:** N7 parts 2 and 3 (is per-file reuse exact, and what does it recompute), N8 (prewarm hooks), N9 (salsa with persistence at real scale).

**D57, closing notes (Nov 11, the architect).**
- The fact cache of the PostHog monorepo is 305 MB (was 465 to 483): records lz4 94 MB, bodies 182 MB, keys and slack the rest. (Corrected to 321 MB for layout 4 in the last section.)
- Gate waived: warm packs phase +0.035 s CPU on one thread against the architect's 0.03 s line. Taken for 160 MB less disk and a cold check 0.3 to 0.7 s faster.
- The bound is 1 GB, not the 500 MB proposed earlier: the prune keeps two engine versions and one is 305 MB. The size trim never removes a row read since the day before; a root that is over the bound with only live data stays over it.
- The site-packages walk stays (0.11 s per check): the install record cannot be shown equal to the walk without the walk.

## N8 spike: prewarm by a git hook (Nov 11, `docs/research/spike-n8.md`, scratch prototype)

A `post-index-change` hook starts the normal check in the background; the pre-commit hook runs the check in the foreground. Three runs per flow, measured under heavy load.

| Flow | Wait without the hook | Wait with it |
|---|---|---|
| Stage, wait 6 s, commit | 4.6 to 6.8 s | **0.8 to 1.2 s** |
| Stage and commit at once | 4.6 to 4.8 s | 5.2 s (no help: the foreground check neither waits for nor reuses the running one) |
| `git commit -a` | 4.5 to 6.4 s | 4.8 to 7.1 s (no help: the hook sees the old index) |
| Ten `git add`, then commit | 6.4 to 9.0 s | 17.5 to 26.6 s unguarded: 19 to 20 checks at once, 17 to 20 GB, about 190 s CPU |
| Rebase of ten commits | 0.75 s | 1.6 s, and 14 checks at once (15 to 18 GB) |

- The storm has a cause inside hoglint: its own `git write-tree` on its index copy fires the hook again, so each background run starts another.
- A guard (skip when `GIT_INDEX_FILE` is set; one background check at a time) cuts the storm to one check, and then the final index state is not the one that was warmed.
- Hoglint has no "run in progress" mark, so a second process cannot wait for or reuse a run of the same tree. A stale background run is harmless: a result is keyed by the tree it read.

**The architect's reading: S1 is not a solution, at most an opt-in extra.** It helps one flow of five, does nothing for `commit -a`, costs a whole check and about 1.2 GB per staging, and needs a guard that makes it miss. It does show the floor is real: a developer who waits six seconds sees about one second.

## N9 spike: salsa with persistence, at the real size (Nov 11, `docs/research/spike-n9.md`, scratch prototype)

A reduced model of the link's name resolution (exports, re-export chains with star imports, cycles by fixpoint) on the real 28,794 files: 184,409 module-level definitions, 333,432 imports, 160,591 queries on a cold run. Salsa 0.28.5 with `persistence`, MessagePack file. Every incremental result was equal to a cold salsa run and to a plain resolver without salsa.

| Run | Wall | Re-run queries | Peak |
|---|---|---|---|
| Cold, compute everything | 0.27 s | 160,591 | 285 MB |
| Save | 0.22 s, 63 MB file | | 352 MB |
| Fresh process, load, no change | 0.41 s | 0 | 223 MB |
| Fresh process, a leaf file gains an import | 0.44 s | 3 | 226 MB |
| Fresh process, a hub's re-export is renamed (1,522 importers' origins move) | 0.45 s | 2,984 | 228 MB |
| Fresh process, the hub gains an unrelated definition | 0.44 s | 58, and no file's result re-ran | 224 MB |
| One live process, the same three changes | 0.027 / 0.042 / 0.024 s | 3 / 2,984 / 58 | 287 MB |

- **The bookkeeping is right and the change-dependent work is tiny.** The early stop works on real data: a hub that gains a definition re-runs 58 lookups, finds them equal, and stops.
- **A fresh process pays a whole-database load: 0.41 s here, more than computing this model from nothing (0.27 s).** Reading the file is 0.006 s; the time is decoding every slot and memo. It grows with the database (half the repo: 0.22 s), not with the change. There is no partial or lazy load, and nothing on salsa's main branch points to one. The real link stores far more per file than this reduction.
- After any change salsa verifies from the top down: about 160,000 memos in 0.024 s.
- Out of the box, load then change then query panics ("tracked function ingredients cannot be accessed before calling `init`", 0.28.5 and the main branch of Nov 9); calling each tracked function once after the load avoids it. No schema check across binary versions; postcard fails as a format; the book does not document persistence.

**The architect's reading:**
1. **Salsa is not the vehicle for reuse between processes.** It is the right tool inside one long-lived process: an editor server, a watch mode, a daemon. There a change costs 0.02 to 0.04 s at this layer.
2. **The spike names the rule any store for S2 must keep: a fresh process may read only what the change touches, plus a small manifest.** A store that has to be loaded whole loses to recomputation. So S2's answers are fetched by key, the reverse dependencies are looked up from the changed files outward, and nothing walks all memos.
3. **It also shows the upside is real:** on real data the set a change reaches is small (3, 58, 2,984 queries of 160,591), and the early stop keeps the 2,500-importer case cheap.
4. Python pack producers stay outside any such framework: their reads are invisible to it (the second opinion said the same).

## Code review of N4a + N6 (Nov 10, on 2ac1c21e243..5469719079c)

| # | Finding | Where it goes |
|---|---|---|
| 1 | Stored findings decode lazily, so a malformed row raises outside the store's guards: a traceback where a rescan was (D28) | N4a agent: check row shape at load; tests |
| 2 | A Python-engine namespace that is only read can be pruned under a running reader | N6 agent: a mark touched on open |
| 3 | `unpack` allocates `keys.json_len` bytes unchecked: a damaged row aborts the process (D28) | N6 agent: length check, miss |
| 4 | A record that does not decompress is extracted again on every run and never repaired | N6 agent: replace the entry, warn once |
| 5 | The prune now deletes other units daily, widening the list-then-unlink race with a run of an older build | N6 agent: re-read the mark before deleting |
| 6 | A one-key read decompresses the whole record under one lock | N6 agent: per-key frames (already in work), no lock across re-extraction |
| 7 | The status rule is written three times in `FindingTable` | N4a agent: once, with a test comparing the three paths |
| 8 | Four table methods have no caller | N4a agent: remove |
| 9 | `finding_keys()` and `query` build every `FindingRecord` to read keys | N4a agent: read raw rows |
| 10 | Two prune methods defined after their caller | N6 agent |

Verified by the architect's own reading: 1, 3, 8. Accepted on the reviewer's reading: the rest. None changes a result on the PostHog repo; 1 and 3 are D28 defects (a cache problem that would fail a run).

## N7 parts 2 and 3: per-file reuse is exact on what was tested, at name grain, and the packs block it (Nov 10, `docs/research/spike-n7.md`)

The same 33 commits, then 11 targeted mutations of the snapshot tree (a not-found name becomes found, a re-export deleted, `__all__`, a class gains and loses a base, a model renamed that strings name, a new `get_model` call, a facade function's annotation, an owners file, a body-only edit, a hub moved).

**Soundness.** With the interface and trace as the architect first defined them, the prediction missed in two places. With three widenings it missed no file in the 33 commits and the 11 mutations:
1. the trace holds the module an edge spells (the `__init__` an import goes through), not only its targets and its re-export chain;
2. a rule row is kept and invalidated under the files its identity names, not only under its site (a row about a new contracts module was sited in another module);
3. rules folded to packages or the whole graph (cycle, reach, coverage, package-view edge rules) are reused by the digest of the relation they read, never by file: their rows sit at a representative witness anywhere in the repo.

Labels that come from provider files (`product.yaml`, owners files) were covered only by a blunt rule: any non-Python change re-labels everything.

**Cost: the grain decides.**

| Per commit | Median files | Worst commit |
|---|---|---|
| Really changed | 4 | 47 files; 1.05 % of edges |
| Predicted, one digest per file | 7 | 3,686 files, 36 % of edges (a hub's interface moves, almost no importer changes) |
| Predicted, one digest per name | 6 | 361 files, 5.1 % of edges (a new field on a model class) |

**The packs are the blocker.** A pack step builds its whole output in one batch, and later steps read state built from the whole repo, so no read can be tied to an item. Reused per step, `posthog.drives` would rerun whole in 20 of 33 commits, `posthog.facade-facts` in 11, `django.models` in 4. Every one of the 292 pack reads whose answer changed named a changed file or a file whose interface changed, so the interface is wide enough; what is missing is producers that record reads per item.

**Whole-graph rules:** their relation digests at module grain moved in 17 of 33 commits while their rows moved in none. They would rerun in about half the commits unless the digest is taken at package grain (not measured: the agent's package mapping was wrong and it said so).

**Sizes of one tree:** interface data 87 MB as normalized JSON (525k name digests); traces 445k (file, name) entries.

**A surprise worth a look later:** the engine does not read `__all__` for star imports (mutation 3 changed nothing).

### D59 (the architect, Nov 10, night). What follows from the four spikes

1. **Prewarm by git hook (option S1 of that night's list): not pursued.** It helps one flow of five and storms without a guard (N8).
2. **Salsa: not the vehicle for the hook.** Its persistence loads the whole database per process (N9). It stays the right tool if Hoglint ever runs as a long-lived process (an editor server or watch mode).
3. **Stored answers with traces (option S2 of that night's list) is sound on everything tested and is the only route to a check near 1 s in a fresh process.** Its rules, from the spikes:
   - name grain, with not-found lookups and spelled modules in the trace;
   - a store read by key from the changed files outward, never loaded whole;
   - rule rows under site and identity files; aggregate rules by relation digest;
   - provider-file labels with their own trace;
   - an oracle in CI: the incremental result of a commit replay equals the full result.
4. **S2 pays only when the last whole-program consumer is converted.** While one pack step still needs the whole graph (`drives` reruns in 61 % of commits), the analysis cannot be skipped. So the saving arrives at the end of the program, not stage by stage, except for the rules phase.
5. **Option S2 is not started yet.** It is a program at least the size of the native port so far (the architect's estimate), so its scope is the owner's call, and a staged plan with a grounded size comes first.
6. **A product choice that would change S2's risk, the owner's to make:** CI stays the exact gate; the hook may be a fast check that is allowed to miss what CI then reports. That is not a cache problem under D28; it is a declared mode. It would let stages ship earlier.

### D59, corrected and extended after the staged plan (Nov 10, night; `docs/research/incremental-plan.md`)

- **Point 4 was too strong.** The architect wrote that S2 pays only when the last whole-program consumer is converted. The planner showed from the code that rules and packs can be reused before the analysis can: while the full link is in memory, a stored answer is checked forward by hashing what it read in the current snapshot. So the program has stages with value: an estimated 3.9 s to 3.3-3.5 (rule rows), 2.9-3.2 (aggregate rules, summary delta), 2.2-2.6 (packs per item), 2.1-2.5 (facade in the engine), then 0.9-1.3 s when the partial link lands. All estimates.
- **Size, an estimate from line counts of resembling code:** 13,200 to 20,700 lines for all eight stages, against 25,263 lines in 96 commits for the native round so far. The first five stages (exact results, 2.1 to 2.6 s) are about 40 % of that.
- **Three paths, not one:**
  - **A, exact and partial:** stages 0 to 4. A leaf commit at an estimated 2.1 to 2.6 s. No new store, no partial link.
  - **Full S2:** all eight stages, an estimated 0.9 to 1.3 s, exact.
  - **B, a declared fast mode for the hook, CI exact:** link and judge only the changed files with rules that read nothing a pack produces; an estimated 1,000 to 1,500 lines; the architect reads its time as about 0.8 to 1.0 s (the planner's 0.5 to 0.8 s is below the 0.55 s floor). It misses what packs, aggregate rules and unchanged files carry; CI reports those. Its lazy fact set and partial link are also what full S2's last stage needs, so it is not thrown away.
- **What the architect doubts until measured:** stage 1's saving (a forward check must digest 1.48 M edges per run before it can skip judging them), and B's coverage (measured on the N7 sample as part 4).
- **The architect's recommendation (the choice is the owner's):** build stage 0 (the oracle) and B, because B alone reaches the hook's bar soon and its parts are on the path to full S2; decide on stages 1 to 7 after B's coverage and the hook's use are known. If the coverage number is poor, A is the fallback and the hook waits.

### D59, the fast hook mode measured (Nov 10 to 11, night; `docs/research/spike-n7.md` part 4)

**A fast mode that links and judges only the changed files, with rules that read nothing a pack produces, sees 11 of the 88 findings that 33 real commits introduced (12.5 %); 9 of 84 in the 30-commit sample; 0 of 2,649 in the mutations.**
- What it sees: `facade-logic` 9 and `presentation-uses-facade` 2.
- What it misses in the commits: `export-shape` 29, `public-surface` 24, `facade-signature` 23, `no-product-lists-in-core` 6 (it reads `dst.exported`), `own-database` 1, `model-crossings` 1. So 76 of 88 come from the three facade and export rules, which read the facade-facts producer.
- Of the snapshot's 41,295 findings, 13 % are in rules the mode could run, 13 % in rules that need a pack, 74 % in aggregate, export and facade rules.
- One commit (a new product) carries 55 of the 84.

**So path B as defined is rejected (the architect).** A hook that stays silent on seven of eight new findings teaches people that it passes when CI will fail.

**What the number says about order (the architect):** the rules that fire on real commits are the facade and export rules. Their producer moves into the engine in N3b, which was already decided (D48). N3b is therefore the next engine phase, ahead of any reuse stage: it is a stage of every faster route, and a fast mode that includes the facade rules can only be measured after it.

**The architect's recommendation:**
1. N3b next.
2. Then the replay check (stage 0 of the plan).
3. Then choose the route, with two numbers that did not exist yet: what the first reuse stage really saves, and what a fast mode with the facade rules would see and cost.

## The owner's calls of Nov 11, and what follows from them

**The owner's calls.**
1. The hook's bar: the design targets a hook that runs on every commit in 2 to 3 s.
2. A subset in the hook: allowed (for example the staged files and everything related), if the lint quality is on par.
3. The repo (D15): Hoglint moves into its own repo.
4. The two cache trade-offs (bodies uncompressed, bound 1 GB): not decided.
5. The size of the reuse program is no obstacle. Open question: are salsa and "exact reuse, all eight stages" a fork in the road, and how does ty stay fast?

**D60 (the architect). The design targets a hook that runs on every commit in 2 to 3 s on a loaded laptop.**
- The check took 3.2 s (four threads) to 3.6 s (one thread) on a quiet machine; a loaded laptop or a low-power state moved times by about 1.4x, so the target needs about 2.2 s or less on a quiet machine.
- A hook on every commit adds bars that speed does not cover: memory of several checks at once (1.16 GB each), a cold first run, and a wrong block being worse than a miss (CI stays exact, so a miss is caught later; a wrong block stops a commit).
- A subset is allowed only with the replay check (S0) showing it reports what the full check reports for the commit.

**D61 (the architect). The road: exact reuse on Hoglint's own store, all eight stages; not salsa.**
- ty measured (0.0.80, the PostHog monorepo): one leaf file 0.08 s, a hub model file 0.21 s, four files 0.17 s, 30 to 140 MB. The whole repo on Nov 10: 9.9 s wall, 105 s CPU, 7.2 GB. It keeps no cache on disk. It is fast in the hook because it checks only the staged files and computes only what they need, from nothing, in each process. Salsa's memo pays in the editor server, which is a process that stays alive.
- So the lesson from ty for a hook is "compute only what the change needs", which is stage 7 (the partial link) and the owner's "staged files and all related". Hoglint needs more than ty for it: a ty diagnostic depends on what a file imports; a Hoglint finding also depends on who imports the changed file and on pack facts, so it needs importers and per-name interfaces on disk, read by key (S5, S6), and packs that run per item (S3).
- The fork with salsa is narrow and late: S0 to S5 are needed on both roads. They part at where answers live between two commits: on disk read by key (Hoglint's), or in a live process (salsa). Salsa's disk form loads whole (N9: 0.41 s to load against 0.27 s to compute the reduced model). Salsa stays possible later for an editor server, on S5's per-name queries.
- Order: S0 (replay check) and N3b first; then S3 and S5 side by side; S1 and S2 are reviewed again before they start, because a hook that may report only the affected files may not need every stored row S1 keeps.

**D15 step 2, done (Nov 11).**
- 256 commits were replayed from the tool's folder in the monorepo into this repo. Each kept the old commit's tree for the folder, author, committer, dates and message. In this repo they run from ebd8549ed7a to 461ef834c48.
- One commit was skipped, because it changed only the monorepo's root `hoglint.toml`.
- Checked: the head tree equals the old folder's tree; 256 of 256 pairs are equal in message, people, dates and tree; fsck clean; 206 files.
- Lesson: when extracting history from a partial clone, pack only the objects the new history reaches.
- Still inside the new repo, to move out later (D15): the PostHog preset (`presets/posthog.toml`) and pack (`hoglint/presets_py/posthog`, 2,229 lines). After N3b only `drives`, the owners provider and the name lists remain to move.

**D61, the order settled (Nov 11, the architect).** The architect wrote above that S1 and S2 get a second look. Done: they stay.
- S0 to S4 alone are the plan's alternative A: an estimated 2.1 to 2.6 s per leaf commit, exact, with no reverse index and no partial link. That already meets the owner's 2 to 3 s bar with the fewest new ways to fail. S5 to S7 are the stretch to about 1 s.
- S2's counts per identity are needed in any case: a ledger line's growth is a count over the whole repo, so a check that judges only the affected files still needs the stored count of the others.
- S1's forward check is partly scaffolding (stage 7 replaces "digest every file's edges" with traces), but its stored rows are what stage 7 assembles its output from, and it is the fallback if stage 7 fails its stop rule.
- Waves: 1 = S0 and N3b (running). Then T1 alone (types and lint config). 2 = S1 (rules), S3 (packs), S5 (link), three disjoint areas, each build agent working apart so that no agent commits another's files. 3 = S2, S6. 4 = S7.

**Open, from the every-commit target (the owner's calls).**
- What the hook does when Hoglint itself fails (an engine error, a missing wheel): block the commit, or let it through and leave it to CI.
- Memory: one check peaks at 1.16 GB; several agents committing at once multiply it. Stage 7 should cut it; by how much is not measured.
- The first check after a pull re-reads every changed file (a cold start costs more than a warm check).

**D62 (Nov 11, the architect). N3b's scope corrected: a contract surface exposes the whole signature of what it hands out.**
- The N3b agent stopped before coding and showed that the architect's brief (and D48's summary) was wrong: with the facade producer out of a check, 2,946 findings go (`facade-signature` 2,772, `export-shape` 174), not only those of the "three additions". 1,540 are findings on builtin types (`builtins.dict`, `builtins.object`), which core drops from every signature and only the producer supplied. `docs/research/facade-facts-map.md` lists "builtins" in its overlap table; the D48 summary lost it.
- Also found: every producer-witnessed `facade-signature` identity has `-` in place of the callable (1,673 of 2,738 ledger lines), because the identity reads an attr the producer's rows do not have; 286 findings are double counts of a core finding, and 33 name a type by an alias of the origin core already names.
- Decided:
  1. in the modules of a surface whose shape makes signatures part of the contract, core exposes every slot with every type name, builtins included;
  2. the identity names the callable (the `-` was a defect);
  3. the double counts are removed as a named class;
  4. "hands out" keeps the producer's rule (`__all__`, `import x as x`, lazy names, and every import of a module with no top-level function), kept apart from the export primitive's "every module-level import";
  5. handed-out dataclass-like classes get their field slots at the surface.
- No policy changes hands: the same real findings exist after it. One policy question comes out of it for the owner: the "module with no functions hands out every import" clause makes `frozen` a re-export of every contracts module; the agent counts what that clause accounts for.
- Lesson for the architect: a brief built on a summary of a map inherits what the summary dropped. The agent's first step (take the producer out, diff the findings) is the check the architect should have run before writing "three things".

**D62, continued (Nov 11, the architect): what the engine's own rows add beyond the producer's.** The N3b agent paired all 32,060 producer `exposes` rows and classified all 2,946 gone findings (identity 1,904, `param:` to `field:` 554, duplicates 312, same identity with another witness 165, origin name 11). Four kinds of new core rows had no partner. Decided:
- **C1 accepted:** `dataclass_decorators` is a global truth, so every `@frozen` and pydantic dataclass in the repo gets its non-builtin field rows (5,054 edges, no finding).
- **C2 held back, the owner's call:** field slots of a class the facade hands out but that is defined in another module (1,742 rows, 94 new findings). The architect's amendment assumed the producer did this. It did not, so it widens the rule's reach. Taken out of the phase, kept as a patch with the list.
- **C3 accepted:** a slot typed with a module-level alias names the alias and its expansion, as core already does on a function's own slots. The producer named only the expansion. About 69 new findings; the architect does not copy the producer's special case into core. (Reversed by D70 in the last section.)
- **C4 accepted:** a decorator is resolved, not matched by spelling, so `dataclass as stdlib_dataclass` counts (330 builtin field rows, some new findings).
- **Correction on record:** an earlier statement that no finding is added or dropped was wrong. C3 and C4 add findings the producer missed.

**D63 (the owner, Nov 11). The facade signature check covers the fields of a class the facade hands out, wherever the class is defined.**
- The owner's reasoning: a product may keep its classes in its own modules and import them thinly into the facade, as long as the layers still hold; so the check is switched on.
- So C2 of D62 goes in: 1,742 rows, 94 new `facade-signature` findings in 18 products. A new field of that kind is flagged from then on.
- The architect's, with it: where a class is defined is the product's choice, so no check of a contract may depend on it. The same already holds for functions.

## Code review of N3b (Nov 11, on 4328924..7f26c49)

Ten findings on how core derives the slots of a contract surface. None shows on the PostHog snapshot's tables as an engine difference; they are inputs PostHog's code does not hold, and gaps both engines share. A fresh agent fixes nine, test first in both engines; a finding whose test cannot be made to fail is rejected.

1. A handed-out def read from the wrong file when a module has a `.pyi` twin or a duplicate module name: a Rust panic or another def's slots. Fix.
2. The two engines read a type alias's value from different files with a stub twin. Fix.
3. An alias is expanded only when assigned in the module that uses it; an imported alias hides its inner types. Fix (the architect's reasoning: where an alias is defined must not change the verdict, the same reasoning as D63). New findings expected; counted.
4. `__all__` read only in its plain form; the annotated form, `+=` and concatenation are ignored in silence. Fix: read them; an unreadable `__all__` leaves a marker (unknown is never clean).
5. A handed-out name that cannot be followed gets no row; star imports are skipped. Fix: an unresolved row; a star import hands out its source's public names.
6. `self` and `cls` left out by name in any function. Fix: by position in a method.
7. A dataclass exposes only its own body's fields, not inherited ones. Fix: fields along the MRO. New findings expected; counted.
8. One fact written at two modules of the same product's surface (the architect's count: 108). The architect had left it; the reviewer is right that the ledger then counts both. Fix: when the origin is a module of the same surface, the origin alone reports it.
9. Wasted and unbounded work in alias expansion and field maps (an exponential case). Fix: one map per file, a memo, a bound.
10. The "module with no functions hands out every import" clause also catches imports that a class-only module merely uses. Not changed then: counted first, then the owner's call, because dropping those findings narrows what the old producer reported.

**D64 (Nov 11, the architect). Two follow-ups to the hand-out rule.**
- A module with no top-level function hands out an import by that clause only when its own code does not refer to the name. `frozen` (a decorator in 20 contracts modules) made 60 findings about its own signature; 26 more were classes and functions the module only uses. A product can still hand such a name out on purpose with `__all__` or `import x as x`.
- `__all__` built from a module-level literal (`sorted(_LAZY)`, `list(NAMES)`) is readable. The 8 unresolved rows the review fix added were all that idiom.

**D65 (the owner, Nov 11). A preset may name the models that may cross a contract surface.**
- A preset may hold a named list of models that are allowed to cross a contract surface. A rule by ownership ("any model core owns") is not enough, because a model that core owns today may later move into a product and would then pass a surface unchecked.
- Which models a company lists is its own policy and is not published.

**D66 (the owner, Nov 11). The ledger must not carry noise forward; the findings get a triage first.**
- The owner decided that the ledger must not grandfather noise, while real problems the work surfaced are handled.
- The architect's reading of it: a ledger line means real debt someone chose to carry. A rule whose findings nobody would fix is wrong and changes, so its lines vanish and are not accepted. Triage comes before the first ledger write. Verdicts per kind of finding: real (crucial ones named), noise (the rule change and how many lines go), tool defect.
- The policy changes that come out of a triage are the owner's to approve. The method is in `docs/design/10-findings-triage-method.md`. The triage reports are not published; shares are given there in general terms.

## How existing findings are carried: a trap the architect walked into, and the second opinion (Nov 11)

**The trap.** The question was whether rules can be turned off as in ruff, and whether zones or levels could cover the valid part of a noisy rule. The architect proposed a new ledger model: strict at a level, and below it only a count per rule and product. The owner turned it down: under a count, a product may never get better and can even get worse.
- The design of Nov 4 to 9 had already weighed it (`docs/research/data-model.md` sections 9 and 16, `docs/research/adversarial-reviews.md` items 4 and 9, `docs/research/prior-art.md` F3): one ledger line per (rule, identity); equality with the scan, so a fixed finding must leave the ledger; growth per growth key against the merge base, so new debt from code fails; move admission only when the same couplings existed. A count cannot see a swap and has no line to go stale.
- Lesson: a long chronological log is easy to answer from memory instead of from the decision. **Rule from then on: before proposing a change to a decided design, read the decision and its alternatives first, and say in the proposal what was decided before and why.**
- D66 (no noise in the ledger) is met at the rules, before the first ledger write, not by weakening the ledger.

**Scoping a rule instead of turning it off.** Whether a rule such as "objects passed without types" flags across the codebase or can be limited in config, for example to presentation code only. It can, and it is: `facade-signature` selects `src.zone == 'facade'`. Rules select on zone, tier, context and shapes per export. Not built: selecting on a product's level or claimed maturity.

**The second opinion** (`docs/research/second-opinion-ledger.md`; the architect's view was written first).
- Agrees: a precise per-identity ledger; do not baseline the current output; counts never as the sole protection; zones say where a rule is right, levels recognize progress.
- **Finds a hole, which the architect confirmed in `hoglint/ledger/growth.py`:** growth is `after <= before` per growth key, and most growth keys have no source package (`model-crossings`: `dst.symbol, edge.label`; `public-surface`: `dst.symbol, edge.kind`; `facade-signature`: `dst.symbol`). A coupling removed in product A pays for a new one in product B. The promise was "no net increase per target", not "no product gets worse".
- Its test for a scope: "Would this code remain acceptable here after the migration is finished?" Yes: scope the rule. No: debt. Unresolved: keep the rule experimental.
- A rule's states: off, experimental (own report, no ledger, no blocking, no evidence for the test skip), enforced. Promotion by hand with a review date; no automatic demotion or activation.
- A ratchet preserves improvements; it does not cause them: a few named migrations with an owner each.
- One logical ledger; store it in shards per source product; never per file or per team.
- For a hook on every commit, with agents writing code: config changes are not neutral (a semantic policy diff); the repair path must not be "regenerate the ledger"; "boundary rules passed" stays apart from "safe to narrow tests".
- Against itself: complexity. Launch fewer trustworthy rules.

**D67 (the architect's proposal, the owner's to decide because it changes what the ledger promises): growth is judged per product.** Every growth key gains the source package. Moves inside a product stay free; a coupling that moves to another product needs the move admission or a review.

**Decided by the architect:** the count-based ledger is withdrawn. A rule gets an off state. The hook's messages never suggest regenerating the ledger.

**D67 (the owner, Nov 11). Growth is judged per product.** Proposed after the second opinion. Every rule's growth key gains the source package, so a coupling removed in one product no longer pays for a new one in another. Moves inside a product stay free; a move across products goes through move admission. To build: a growth key may name `src.package` also where the identity holds `src.module` (the growth columns were identity columns), and the preset's growth keys change. It goes into the next engine phase with the triage defects.

**D68 (the owner, Nov 11). The record is versioned, and a public version lives in the Hoglint repo.** The owner decided that the working notes are versioned and that a public version of the record goes into the Hoglint repo, so future agents and engineers have the learnings and the earlier research, as much of it as possible.
- To do, after the record by topic is compiled: the repo gets `docs/` with the design record by topic, the list of traps, the decision log, and the earlier research (data model, prior art, reviews, spikes, second opinions), plus an `AGENTS.md` that sends an agent there before it proposes a design change.
- The architect's rules for the public form: no person's name (the owner is "the owner"), no local paths or session links, no finding about PostHog's code that is security-relevant, and PostHog-specific policy and the triage reports are not published.

**D69 (Nov 11, the architect). Stage 1 is not built: a forward check of rule rows costs three times the judging it would skip.**
- Measured by the S1 agent before building anything (its step 1; the key code kept as a patch; branch unchanged). 61 trees, about 4.57 M judged rows per tree in 22 per-row rules.
- The hit rate is fine: 99.87 % of row keys and 99.99 % of label keys are unchanged between a commit and its parent (worst: the hub move, 95.6 % by row).
- The cost is not: computing all row keys takes 2.1 to 3.0 s on one thread; the native `judge` calls they would replace take 0.66 to 1.0 s. About 1.0 s against 0.3 s on four threads. Judging reads few fields per row and stops early (0.15 to 0.2 microseconds per row); a key must read and hash every input of every row.
- So the architect's doubt in the plan's notes was right, and the plan's line "3.3 to 3.5 s after stage 1" is void. **Reuse of rule rows pays only when unchanged rows are not read at all: traces and the partial link (stages 5 to 7).** Stage 2 (relations over stored rows) falls with it in its "full graph present" form.
- The reads of a judged row are now listed in full in the agent's report (not published): stages 5 and 6 need exactly that list.
- A side finding to use at once: in the rules phase `judge` is only about half of the CPU. The coverage rule's adapter reads turbo and package JSON on every check, and the ledger's equality check and other Python around the rules are the rest. The architect's profile of Nov 10 had the same items and left them "for a later phase on the check's shell". That phase starts now (P1).

## The record by topic, and what compiling it showed (Nov 11)

**The record.** An agent compiled a record by topic from this log, the plan, the data model, the design page and the reviews: a README, twelve topic pages and a list of traps. Its public form is `docs/design/` (index: `docs/design/README.md`). Read the topic page and `docs/design/traps.md` before proposing a change to a decided design. The spikes, the briefs and some studies were not read in full for it; the README says so.

**Contradictions it found in the architect's notes, settled here.**
- **D70 (the architect). An alias whose expansion is known is not a finding.** D62 accepted the alias-name rows as findings (class C3, 114 of them). The triage showed they are noise: 385 `facade-signature` findings report an alias as an unknown type although its expansion is known. Reversed, and fixed with the other triage defects. This log did not record the reversal until then.
- **The cycle rule stays as decided (owner, Nov 5 and Nov 9): new or growing package cycles block, the existing cycle is recorded.** The triage called its 349 findings noise and proposed "advisory". A recommendation to drop the rule repeated the count-ledger trap: it did not name the earlier decision, and it was withdrawn. The owner had also decided on Nov 9 that every rule family blocks and that "advisory" was turned down as a way to start rules.
- **The fact cache is 321 MB** for the PostHog monorepo (layout 4, measured in the architect's verification round before the repo move). "305 MB" in "D57, closing notes" was the layout 3 number.
- **The PostHog pack's home:** `docs/research/native-engine-design.md` sections 4 and 9 still move it into the Hoglint repo. This log reversed that on Nov 10 (drives stays a Python plugin in the monorepo; owner, Nov 10). The log is right; the design page is history.
- **`featureflag-relations`:** a note of Nov 10 says to write its missing classification file; the triage says to retire the rule because a test in the monorepo already enforces the same. Open, the owner's.
- **Ledger identity grain:** the question (Nov 4): is a finer grain more future-proof? It was left for deliberation. The module-level default stayed after spike D, with no later decision. Open.
- **The level ratchet** (a product that holds a level cannot drop) works through the ledger's level lines and the equality check. No decision entry said so. Recorded here.
- **A rule's states:** only "off" is decided (the architect's; owner, Nov 11, for rules that are not ready). "Experimental" is the second opinion's proposal, not a decision.
