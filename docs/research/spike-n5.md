# Spike N5: where the 1.4 GB goes, and what a mapped fact store buys

> A memory map of one hoglint check and two prototypes (an earlier release, a mapped fact store), Nov 10, 2026.
> Partly replaced: D56 withdrew the mapped store and the 0.5 GB target and replaced phase N5 with N5a (release pack records per step, smaller fact database); see `docs/history/decision-log-3.md`, `docs/design/06-fact-cache-and-summaries.md` and `docs/design/07-speed-and-memory.md`.

**The question.**
One check of a 29,000-file repo peaked at 1.40 GB, and that figure had not moved while the rule evaluator, the labels and one pack producer moved to Rust and the check went from 5.0 s to about 3.6 s.
Ruff checks the same files in 0.39 GB and tach in 0.22 GB.
The recorded direction (D37) was "facts are mapped, not decoded".
The spike had to measure where the peak is and who holds it, then build the smallest prototype that tests that direction end to end, and measure the whole check, never a part alone.

Export of ad83640b66e; staged `check --index` on a shared clone of a staging repo; warm fact cache unless stated.
Measured under load the whole time; treat wall deltas under 0.3 s as noise.
Nothing committed.

**Short answer.**
The mapped store does not pay as a memory fix.
The decoded fact rows are 0.24 GB of a 1.25 GB peak.
The prototype removed 0.14 GB of that and cost about 1 s of CPU per check.
A version with true in-place views could keep the time flat, but it can remove 0.24 GB at most.
The rest of the peak is link structures the link needs anyway (0.5 GB) and Python pack records (0.2 to 0.35 GB).
The 1.4 GB figure is RSS, and RSS is the wrong measure here: see "Two peaks".

## 1. Memory map at HEAD

Method: a wrapper around the real CLI marks every phase boundary.
Each mark reads the physical footprint and its lifetime max (`proc_pid_rusage`), RSS, `ru_maxrss`, the malloc zones (in use / held), the Rust heap (a counting `GlobalAlloc` in an instrumented build), `Link.heap()` per structure, and `vmmap --summary` at three marks.
The Python heap per owner comes from a separate `tracemalloc` run.

**Two peaks.**
- The physical footprint peaks at **1.25 GB inside `posthog.facade-facts`**. This is the memory the process owns.
- RSS (`ru_maxrss`, the "1.37 to 1.40 GB" quoted so far) peaks **at the end of the run**, during rules and the summary. After `release`, about 0.34 GB of freed malloc pages stay resident: RSS counts them, the footprint does not. `malloc_zone_pressure_relief` did not lower RSS.

**Who holds the 1.25 GB footprint peak** (one thread; four threads give the same split):

| Holder | GB | Measured with |
|---|---|---|
| Rust heap, total | 0.74 (0.79 transient) | counting allocator |
| ↳ decoded fact rows: defs 0.079, assigns 0.049, refs 0.042, callees+names 0.033, uses 0.016, imports 0.015, calls 0.006 (strs already dropped) | 0.24 | Link.heap |
| ↳ interner | 0.15 | Link.heap |
| ↳ edges: rows 0.085, via/attrs 0.051 (the index adds 0.026 in rules) | 0.14 | Link.heap |
| ↳ scopes 0.069, keys 0.025, module tables 0.023, resolver caches 0.021, symbols 0.015 | 0.15 | Link.heap |
| ↳ not attributed by Link.heap | about 0.06 | allocator minus Link.heap |
| Python heap, total | 0.34 (0.39 transient) | tracemalloc, separate run |
| ↳ decoded pack records (`NativeRecord.load`; 31 MB of JSON becomes about 0.2 GB of objects) | 0.19 to 0.22 | tracemalloc by file |
| ↳ NativeGraph 0.066, listing 0.020, the rest 0.03 | 0.12 | tracemalloc by file |
| Malloc held but not in use (partly clean) | 0.14 | malloc_zone_statistics |
| File-backed pages | about 0 (SQLite is read with pread) | vmmap |

Not attributed: the 0.06 GB of Rust outside Link.heap; how much of the malloc slack is dirty; pymalloc fragmentation (the arenas hold 0.36 GB dirty against 0.34 GB traced).

**Timeline** at one thread (footprint / Rust heap, GB):
after snapshot 0.09 / 0;
after `link_blobs` 0.90 / 0.68, with a 0.73 peak inside it that includes the 0.18 GB SQLite body buffer;
after graph and build objects 1.00;
after the Django pack 0.97 (1.16 at most, during `django.uses`);
after drives 1.14;
after facade-facts 1.18 (lifetime max 1.25);
after `release` 0.89 / 0.39;
after rules 1.00 / 0.52;
summary written and end 1.03 (RSS 1.37).

What packs decode: drives 17 MB of JSON (defs, assigns, callees, refs, imports), facade-facts 6 MB, the Django pack 6 MB, about 11,500 (record, key) loads in all.
Since N3a, Django decodes little.

## 2. Where analysis time goes

Five warm runs at one thread, with timers added in the instrumented build:

| Step | s |
|---|---|
| Snapshot and discovery (Python, git index) | 0.42 |
| SQLite: keys 0.013, 183 MB of bodies 0.072, open and slots 0.03 | 0.115 |
| Interning (serial, in fact order) | 0.075 |
| Decoding bodies into rows | 0.10 |
| Index and scopes | 0.107 |
| Link producers (the link proper) | 0.33 |
| Graph and build objects (Python) | 0.09 |

Read, decode and intern add up to 0.29 s of the 1.25 s this harness measures.
That is the most a store that decodes nothing can take off the analysis phase.
In one run of five, and in many paired lines below, the body read took 1.6 to 2 s instead of 0.07 s, because the database pages were no longer in the page cache.
That cost is I/O, not CPU.

## 3. Prototypes

**C: release earlier** (+31/−2, Python only).
- After each producer step, the decoded keys of every record are dropped (path and status stay). A later step decodes again what it reads.
- After `release`, `malloc_zone_pressure_relief` runs. It made no measurable difference, so the per-step drop is the whole effect.

**A: mapped store** (+389/−32).
- One append-only body file per facts id, plus an index file. Each body is the existing HLFACTS3 entry. Entries a run reads from SQLite or extracts are appended.
- `link_blobs` interns and decodes straight from the mapping, so the 183 MB copy goes.
- After the link, every decoded row is dropped. A later reader decodes one record again from the mapping, with a 16-record cache and the string ids kept from the load (8.5 MB).
- Scopes keep `is_class` and `parent` per def, so name lookups never read rows.
- What it is not: true in-place views. The link proper still decodes once, and later readers decode again.

**Equality.**
On the snapshot, a digest of the whole graph after link and packs, per producer: base, C, A cold, A warm, and A with only defs and assigns unloaded at four threads all give 1,475,943 edges, whole digest `daa1134f49b66d7a818b2f45`, symbols 291,041 `cafc6bbfbf680e585668a9d8`, no failed producers.
The graph straight after link, before packs, was not digested on its own; it is part of the same digest.

**Pairs.**
Six rounds warm, three cold.
The order rotates each round, because the first check of a round often paid the page-cache cost above.
"d" is the median paired delta against base.

| Warm | base | C | A |
|---|---|---|---|
| Peak footprint, 1 and 4 threads (GB) | 1.25 / 1.25 | 1.16 / 1.16 (d −0.09) | 1.11 / 1.10 (d −0.14) |
| Peak RSS (GB) | 1.37 / 1.38 | 1.31 / 1.32 | 1.47 / 1.47 (d +0.09: mapped pages count in RSS) |
| Whole check CPU, 1 thread (s) | 4.11 | 3.91 (d +0.01) | 5.00 (d +1.03) |
| Whole check wall, 1 and 4 threads (s) | 5.41 / 4.39 | 4.14 / 4.00 | 5.64 / 5.85 (d +0.93 / +1.19) |
| Analysis wall, 1 thread (s) | 2.19, I/O spikes; 1.12 to 1.31 without | 1.18 | 1.25 |
| Packs wall, 1 thread (s) | 1.17 | 1.14 | 2.58 (d +1.23) |

| Cold (fact dir deleted before each line) | base | C | A |
|---|---|---|---|
| Peak footprint, 1 and 4 threads (GB) | 1.62 / 1.66 | 1.54 / 1.65 | 1.36 / 1.39 (d −0.27) |
| Whole check wall, 1 and 4 threads (s) | 11.55 / 8.37 | 11.11 / 8.18 | 13.37 / 10.85 (d +2.0 / +2.5) |
| Analysis wall, 1 thread (s) | 8.72 | 8.36 | 8.79 |

| Ten overlapping, warm, one thread each | base | C | A |
|---|---|---|---|
| Largest sum of footprints (GB) | 12.32 | 11.53 | 11.01, plus one shared 0.18 GB page-cache copy |
| Largest sum of RSS (GB) | 13.62 | 13.24 | 14.62 |
| Wall for all ten (s) | 10.05 | 7.35 | 9.56 |
| Median per check: CPU (s), footprint (GB) | 6.58, 1.26 | 6.42, 1.17 | 7.62, 1.11 |

The overlapping runs went once per variant, one after the other, base first.
Base's wall likely includes warming the page cache, so read the wall column as rough.

**Why A costs time.**
The packs scan every file's rows for mentions again and again: models, uses, get-model, signals, installs and drives each do it, 261,000 decodes in one check.
With true views those scans would walk mapped rows at about the current cost.
A decode-on-demand prototype cannot show that.

**Why the memory gain is only 0.14 of 0.24 GB.**
The rows are decoded and then freed, and the freed space is spread through the zones the link's tables live in.
A variant that unloads only defs and assigns (0.13 GB) moved the peak by 0.03 GB.
Only a store that is never decoded into the heap avoids that fragmentation.

## 4. Why not B (packs read facts through the link)

At the peak, B's target is 0.19 to 0.22 GB of decoded Python records.
C reaches about half of it in 31 lines with no time cost.
A full B means drives reads facts through Rust queries, and D49 keeps drives in Python.
Facade-facts already goes to Rust in N3b (D48), which is B for that producer.
So B is N3's work, and this spike adds nothing to it.

## 5. What a full A needs

- **Format.** Per entry, a header with an offset per kind, so a reader goes straight to one kind. Fixed-width rows read in place. V trees and texts as offsets into the entry. Strings as ids into one string table per store, so a decode looks nothing up. Versioning: the facts id stays in the file name, and the magic carries the layout.
- **Torn or stale file (D28).** Each entry carries a checksum. An index line past the end of the file, or a bad checksum, is a miss, and the run re-extracts. A failure to open or map means the run holds decoded rows, as before. Appends go under `flock`. A reader maps the size it saw.
- **Pruning.** The daily first run rewrites the live entries into a new file and renames it into place. A process that still maps the old file keeps its inode.
- **Python reference engine:** unchanged, because it reads the JSON records, which stay in SQLite.
- **The link.** `FileFacts` becomes a view type in every reader: facts.rs, scope.rs, resolve.rs, produce.rs, the link and binding queries, and django/uses.rs.
- **Estimate: 2,500 to 3,500 lines** (an estimate). The store and its upkeep about 700. The view type and its readers 1,500 to 2,500, changed or new. Tests about 400. Basis: this prototype is 420 lines and covers only the store and the readers after the link. The files that read rows hold about 4,100 lines.
- **Expected gain** from the map above: at most 0.24 GB off the footprint peak (about 20 %) and at most 0.29 s off analysis CPU. The interner (0.15 GB) stays as long as edges and scopes use run-wide ids.

## 6. Surprises, and what the spike could not measure

- The fact rows are already binary in SQLite (HLFACTS3). JSON is only the pack records. The JSON decode named in the brief is gone from the link side.
- `ru_maxrss` overstates and misplaces the peak. Under A it even rises, because the mapped pages count in RSS. Measure the footprint (D37 already says so).
- Warm checks often paid 1 to 2 s of I/O to read the facts again from disk (analysis over 2 s: base 15 of 26 lines, C 4 of 26, A 3 of 26). Base and C share one database and the same Rust code, so most of that gap is order and page-cache eviction, not code. A's smaller file (183 MB against a 459 MB database) may help; not proven.
- Not measured: the time of true in-place views; Linux (no `malloc_zone_*`, and RSS behaves differently there); compressed memory; the 0.06 GB of Rust outside Link.heap. CPU deltas are steadier than wall deltas.
