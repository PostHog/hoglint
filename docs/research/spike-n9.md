# Spike N9: can salsa persistence carry Hoglint's reuse between processes?

> A scratch prototype on salsa 0.28.5 with its `persistence` feature, at the real scale of a 29,000-file repo, Nov 10, 2026.
> Partly replaced: D59 item 2 and D61 rejected salsa for reuse between processes; see `docs/history/decision-log-3.md` and `docs/design/08-fast-hook.md`.

**The question.**
The N7 spike (`docs/research/spike-n7.md`) showed that a commit's effects stay in the files it changes, with rare spread along re-export chains, so the plan under study was to store each file's answers with a record of what they read and reuse them in the next process.
Salsa (the incremental query framework of rust-analyzer and ty) does that bookkeeping in memory and has a `persist` option behind its `persistence` feature.
Hoglint's main use is a git pre-commit hook, so a fresh process per check: can a salsa database of Hoglint's size be stored and loaded fast enough (budget: about 1 s after a one-file change, against 0.55 s for a fully stored result), can inputs be changed after a load, and what does the API force on the design?

Scratch only; nothing committed.

**Short answer:** it works, after one workaround, and it is not fast enough.
A fresh process can load the stored database, change one file's input and recompute only what the change reaches, and every result was equal to a cold computation.
But the load deserializes the whole database every time: 0.41 s for this reduced model.
That is more than a cold full computation of the same model (0.27 s).
The load cost grows with the database, not with the change.
Out of the box, a load followed by any input change panics (minimal case below).

**Salsa.**
Release 0.28.5 from crates.io (tag `salsa-v0.28.5`, commit `d434f8805c6`, Sep 24 2026), features `persistence` plus the defaults.
The minimal case also ran against salsa master `1f48f4a3295` (Nov 9).
Rust 1.98.1.
Sources read: the book (`cycles.md`, `plumbing/db_lifetime.md`), the persistence tests (`persistence.rs`, `persistence_stale_interned.rs`, `persistence_never_change.rs`, `tracked-struct-entries-persistence.rs`), `src/database.rs` and `src/function.rs`, and the changelog since PR #967 (the first version of the feature, Aug 2025).
- What it does: `<dyn Database>::as_serialize(&mut db)` gives a serde `Serialize` of the whole database. `<dyn Database>::deserialize(&mut db, deserializer)` fills an empty database in place. Ingredients marked `persist` (inputs, interned, tracked structs, tracked functions) go to disk, and the ids stay the same. A dependency on a query that is not persisted is flattened to that query's own persisted dependencies (#975). Since #967 there are fixes for interned values, cycles during serialization, stale keys and never-change durability (#1109). The tests change an input after a load (`partial_query`), so input updates after a load are supported.
- What it does not do: there is no partial or lazy load. The format addresses ingredients by registration index and stores no schema or version fingerprint, so a rebuilt binary must not read an old file. There is no query hashing. The changelog has no persistence entries after 0.27. Nothing in the book documents persistence.
- Formats: a format must be self-describing enough and must borrow `&str` keys. postcard fails (`SerializeSeqLengthUnknown`, from `CycleHeads::serialize` calling `serialize_seq(None)`). MessagePack (`rmp-serde`) and JSON work. The numbers below are for MessagePack.

**Model** (every ingredient `persist`):
- Inputs: `SourceFile { path, module, is_package, rows }`, one per file, where `rows` are the file's module-level defs and assignments and all its imports in line order. A singleton `Workspace { files, modules }` holds module name -> file, sorted. `Sym` is an interned string.
- `module_file(Sym)` looks a module up in the workspace. `exports(file)` gives the module-level bindings, where the last one wins: def, re-export (module, name), or module, plus the star sources.
- `resolve(module, name)`, with two interned arguments, follows the chain. It tries a binding, then a submodule, then the star sources (public names only). The result is a def, a module, external, or missing.
- `file_edges(file)` gives the origin of every import at any scope. `file_digest(file)` is a hash of those origins as text. `whole_check(workspace)` folds the 28,794 digests.
- Cycles: `resolve` uses fixpoint iteration with `cycle_initial = Missing`. A star or re-export cycle starts at Missing, and an origin found on another path wins in the next round. The cold run had 75 cycle iterations.
- Second check: a plain resolver with no salsa, which cuts cycles by the path. Its digest was equal in every case.

**Data.**
Hoglint's own fact extractor (kinds `imports,defs,assigns`) ran on the snapshot.
That gave 28,794 files (the config's excludes applied) in 0.8 s.
A small script turned the result into a flat TSV with 184,409 module-level definitions, 333,432 imports (322,394 of them at module level) and 56 star imports.
Module names come from the `.` source root, and relative imports are made absolute.
A cold run executes 160,591 queries: resolve 69,211, module_file 20,415, exports 13,376, file_edges and file_digest 28,794 each, whole_check 1.

**Changes:**
the hub is `posthog/models/__init__.py`.
Its re-export `Team` has 1,522 `from posthog.models import Team` rows, and the module has 3,717 importer rows.
The leaf is a core admin module that no file imports.
- Leaf: the leaf gains `from posthog.models import Team`.
- Rename: the hub's `Team` binding gets a new local name, so its importers resolve to missing.
- Unrelated: the hub gains an unrelated definition.

**Runs.**
Release build, one process per run, measured under load.
Wall and CPU times are the median of 3 rounds (the spread was 0.02 s or less).
Peak memory is the lifetime peak physical footprint.
"Re-run" counts salsa `WillExecute` events; in runs 2-5 it leaves out 2 executions caused by the workaround (see the API section).
"Validated" counts memos that deep verification found still valid.
"Equal" means that the digest was equal to a cold salsa run and to the plain resolver on the changed data.

| run | wall | CPU | peak | load / recompute | re-run | validated | equal |
|---|---|---|---|---|---|---|---|
| 1 cold, compute everything | 0.27 s | 0.27 s | 285 MB | read 0.12 + compute 0.15 | 160,591 | - | yes (plain) |
| 1 save, MessagePack / JSON | 0.22 / 0.25 s | same | 352 / 423 MB | 63 MB / 135 MB file | - | - | - |
| 2 fresh, load, no change | 0.41 s | 0.41 s | 223 MB | 0.41 / 0.000 | 0 | 0 | yes |
| 3 fresh, leaf gains import | 0.44 s | 0.44 s | 226 MB | 0.41 / 0.027 | 3 | 160,588 | yes |
| 4 fresh, hub rename (origins move) | 0.45 s | 0.45 s | 228 MB | 0.41 / 0.043 | 2,984 (1,462 file digests) | 157,608 | yes |
| 5 fresh, hub gains unrelated def | 0.44 s | 0.44 s | 224 MB | 0.41 / 0.025 | 58 (56 resolve, no digest) | 160,533 | yes |
| 6 live process, cold then 4 steps | 0.28 s, then 0 / 0.027 / 0.042 / 0.024 s | same | 287 MB | - | 0 / 3 / 2,984 / 58 | as 2-5 | yes |

With JSON, runs 2-5 took 0.54-0.59 s and peaked at 314-319 MB.
In run 5 the 56 changed `resolve` results came out equal, so salsa backdated them and stopped: no `file_digest` ran again.
That is the cheap case, and it stays cheap.

**What a fresh process pays regardless of the change:**
1. Deserializing everything: 0.41 s (MessagePack). Reading the 63 MB file takes 0.006 s, so the time goes to decoding and rebuilding every slot and memo. The file holds the inputs (about 45 % of the JSON size: the facts themselves), then `file_edges` 21 %, `exports` 12 %, `resolve` and its interned keys 15 %, and `Sym` 9 %. The inputs cannot be left out: a persisted query that depends on an input that is not persisted panics at save ("must be persistable"), and memos need their input slots restored first.
2. After any change, deep verification walks every memo that `whole_check` reaches: about 160,000 memos in 0.024 s.

Both costs grow with the database.
Half the repo (14,397 files, a 32 MB file) loads in 0.22 s and verifies in 0.014 s.
The change adds only its own re-runs (0.002-0.02 s here).

**What the API forced or prevented:**
- **Load, then change, then query panics** in 0.28.5 and on master `1f48f4a3295`. Deep verification of a loaded memo reaches a tracked function that was never called in this process. That function's ingredient has no `init`ed view caster, so salsa panics with "tracked function ingredients cannot be accessed before calling `init`". The minimal case is two persisted functions, about 40 lines. The workaround is to call every tracked function once after the load, on any key. That costs under 0.001 s and caused the 2 extra executions. Without it, the real model panics too.
- Every type a persisted query returns needs `Serialize`, `Deserialize` and `SalsaValue`. Interned `Sym<'db>` works inside those types, and the `debug` option is required for `Debug`.
- Multi-argument queries get an interned key ingredient, which is persisted too. `cycle_initial` takes `(db, id, args...)`.
- After a load, inputs are found again through a singleton (`Workspace::get`). Path -> file needs a lookup of the model's own.
- The granularity is the query. `exports(hub)` changed as a whole, so every `resolve(hub, *)` ran again: 56 here, and more for a hub with more imported names. A per-name query would cut that but would add memos to the load.
- Unstable: no schema check across binary versions, the postcard failure, the init panic, no docs, and the persist attribute documented only on docs.rs.

**Judgment:**
- Salsa can do the bookkeeping (recorded reads, reuse, early stop on equal results) across processes: all four runs were correct and the change-dependent work was tiny. But it cannot meet the budget for Hoglint's real graph, because each fresh process pays a whole-database load.
- That load is 0.41 s for this reduced model, already 75 % of the 0.55 s spent elsewhere. The real link stores far more per file (sites, defs, classes, MROs, edges), and the load grows with it.
- For this to work, one of these would have to be true: salsa loads lazily or only the touched part (it does not, and nothing on master pointed that way), or Hoglint keeps a long-lived process (a daemon), where run 6 shows 0.02-0.04 s per change.
- The init panic needs the touch workaround or an upstream fix.
- Not tested: the real link's query shapes and size (only this reduction), a memory-mapped or zero-copy format (salsa's deserializer copies into its own tables), file addition and deletion (only content changes), parallel queries, and timings on a quiet machine.
