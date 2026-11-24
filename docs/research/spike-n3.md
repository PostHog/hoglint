# Spike N3: the packs out of per-row Python?

> A spike on the two framework packs, Nov 10, 2026: a profile, one producer ported to Rust, one producer given coarse native queries, and the cost of a new pattern in each shape.
> Partly replaced: D49 took the middle path (port `django.uses`, keep `drives` in Python), and "The PostHog pack's home" keeps the PostHog pack in the consuming monorepo; see `docs/history/decision-log-3.md` and `docs/design/05-engine.md`.

**The question.**
The two packs took about 1.3 s of a 5.3 s check (Django 0.65 s, PostHog 0.6 s), spread over 12 million Python calls with no single hot spot.
Three ways forward were open: (A) port producers to Rust, (B) keep them in Python and replace per-row loops with coarse native queries that return columns, or (C) leave them.
Phase N1 had taught that time is gained only where a consumer of Python objects goes away, so the spike measured the whole packs phase in same-minute pairs, never a kernel alone, and priced one real new pattern in each shape.

Fixed commit `00bc03284f7`.
Nothing committed.

**Caveat first: measured under heavy load the whole time (other jobs on the machine).**
CPU seconds were inflated up to 2x against the 5.3 s reference, and single lines swing by 1 s.
The spike took 12 one-thread triples (base, Rust, coarse in the same minute) and 6 four-thread triples, and quotes medians of the per-triple deltas.
The two quietest triples (total about 7.5 s) agree with the medians; they are cited too.

## 1. Profile of the packs phase (inclusive `process_time` per producer, one thread)

Packs: Django 1.45 s, PostHog 1.12 s (under load).

| producer | s | read | wrote |
|---|---|---|---|
| django.uses | 0.69 | 101,475 batch rows, 3,976 `rows("derived")` decodes (97,654 rows) | 101,466 refinements |
| posthog.drives | 0.65 | 15,972 `may_mention`, 8,094 `has_rows`, 7,885 `imported_modules` (112k strings), 11 kinds decoded (~250k rows) | 2,173 edges |
| posthog.facade-facts | 0.46 (sink 0.13) | 1,514 bodies, ~55k rows decoded | 42,311 edges |
| django.migrations | 0.25 | 3,459 records + digests | 2,329 edges |
| django.installs / signals | 0.17 / 0.16 | 28,174 / 28,191 `may_mention` calls each, one per file | 457 / 391 edges |
| models, relations, apps, get-model, symbols, loaders | 0.26 together | | 3,446 edges, 5,569 symbol facts |

Per row, the top three (from coarse function timers):
- **django.uses:** per use, a memo lookup of (chain, up) to a shape (`UsePosition.of`, `OrmShapes.classify`, 93k memo hits, a few thousand misses); per file with a local, decode `derived` with orjson (0.19 s of the 0.69), sort it, and follow locals with bisect; one `Refinements.add` per use. No hot spot: memo hits, tuple builds, list appends.
- **posthog.drives:** `may_drive` over 8,112 test files (0.21 s: two `may_mention`, `has_rows`, `imported_modules` plus a `startswith` loop per module); `FileFacts.test` on the 229 that pass (0.21 s: loops over names, tags, callees, refs, uses, watched rows, one `Mention` object per hit); `derive_file` with the call view (0.15 s); `grow_origins` (0.08 s).
- **posthog.facade-facts:** `ModuleView.annotation_names` (43,816 calls), `qualify`/`qualify_name`/`binding` per annotation name, then 42k `Edge` objects (sink 0.13 s).

## 2. Rust port of django.uses (A)

One binding method `Link.django_uses(files, models, write)` takes the core `reference` edges onto the models from the link's index, judges each use (the whole of `OrmShapes` and `UseProducer.shaped`, `derived` parsed with serde_json), and writes the refinements with `edges.refine`.
No Python object per use.
The aggregate edges (`uses_to`, 0 rows here) and per-file failure edges stay in Python.
- Rust: 586 lines (533 without comments and blanks). Python: +19 lines.
- Python that goes away only if the Python engine stops running this producer: about 120 lines of `uses.py` and all 269 of `shapes.py`. While both engines run packs (D22), it is 0 removed, and the shape logic exists twice.
- Not ported: `RefinedFields` bookkeeping in the sink. The port checks the edge's own `flow_shape` attr itself, but a later producer that refines the same edges would not see these as conflicts. None does in the preset.

**Equality proof** (dry mode against the Python producer in the same process): on the snapshot and on a staging repo, 101,466 = 101,466 rows; handles, shapes, details and flow shapes are equal in order (461 flow shapes); 0 failed files.
**Whole graph** (a digest of the whole graph on the snapshot): all 1,475,943 edges in order (refinements included), the per-producer digests and 291,041 symbols with their facts are identical for base, Rust and coarse.

**Pairs:**
- Profile: django.uses 0.69 s down to 0.22 s (the Rust call itself; it makes 0 Python objects but still parses `derived` JSON and runs the regex per (chain, up)).
- One thread, 12 triples: packs CPU median 3.00 s (base) vs 2.10 s (Rust), paired delta **−0.73 s**. Django pack delta −0.48 s. Whole check CPU delta −0.94 s.
- Four threads, 6 triples: packs CPU delta −0.39 s, whole-check wall delta −0.77 s.
- Quietest triples: Django pack 0.90 to 0.63 s, and 0.91 to 0.69 s. So on a quieter machine, about **−0.25 to −0.3 s** of the 1.3 s. The loaded medians overstate it, because base Python suffers more from contention than Rust does.

## 3. Coarse queries in posthog.drives (B)

Two new queries, 83 lines of Rust: `mentions_many(files, kinds, words)` returns one byte per file; `import_tags(files, prefixes, modules)` returns, per file, the products its imported modules reach, as two u32 columns.
`drives.py` changed by +40/−1 lines: `Deriver.prefilter` runs both queries once per owner group over the 8,112 test files, and `may_drive` keeps its judgment (owner excluded) in Python.
Output: identical whole graph (above).
- Profile: drives 0.65 s down to 0.56 s; the 15,972 `may_mention` and 7,885 `imported_modules` calls are gone.
- Pairs: one thread, 12 triples, packs CPU delta **+0.24 s** (noise); PostHog pack delta +0.05 s. Four threads, delta −0.02 s. Quietest triple: PostHog pack 0.85 vs 0.84 s. **No gain measurable above the noise.** The 0.09 s from the profile is under the noise of this machine.
- Why so little: the prefilter was a fifth of drives. The rest is `FileFacts.test` plus `derive_file` on 229 files, which builds `Mention`/`Pending` objects and walks call graphs. That is judgment, not lookup, so a coarse query cannot take it without taking the logic too.

## 4. Peak memory (`ru_maxrss`, staged check)

Base 1.41 GB, Rust 1.43 GB, coarse 1.43 GB (medians, one thread; +0.02 GB both, within run-to-run spread).

## 5. One new pattern in each shape: signal receivers with `sender=` (ff6c1e8b801, Nov 5)

- **Python today (measured):** `packs/django/signals.py` is 269 lines for receivers, `.connect`, sender forms and configured wrappers, through `calls_named`, `may_mention` and the resolver. A follow-up like "imports inside methods count" is about 10 to 30 lines of Python, no build.
- **Rust, shape of step 2 (estimate):** about 400 to 600 lines of Rust. Basis: the uses port was 586 Rust lines for about 390 lines of Python (1.5x), and signals also needs the resolver from Rust (it is already in the link) and the model registry, which is Python today and would have to cross or be ported too. Every follow-up becomes a Rust change, a rebuild (about 2 min), a new native id and a wheel release. At about 3 new static-Python reads a month (from the history of the monorepo's earlier scanner), that is a steady Rust queue.
- **Coarse-query shape of step 3 (estimate):** the same 269 lines of Python, plus one coarse query only where a per-file loop shows up. Signals has one: 28k `may_mention` calls, which `mentions_many` already covers (about 10 lines to adopt). A new query is about 40 to 80 lines of Rust, written once and reused.

## Estimates for the whole packs phase (1.3 s at the reference)

All are labeled **estimate**; the basis is the profile shares and the quiet pairs above.
- **A, all producers in Rust:** the uses port kept about 70 % of its producer's time off (0.69 s to 0.22 s in the profile, −0.25 to −0.3 s in quiet pairs). At that rate for all producers, the packs phase goes from about 1.3 s to 0.4 to 0.5 s, so about **−0.8 s**. Cost: about 6,000 to 7,000 lines of Rust for the about 4,500 lines of pack Python (Django 2,800, PostHog 1,700; 1.5x), and the PostHog pack moves out of Python, against D15 and D47 revised. Every new pattern is then a Rust change.
- **B, coarse queries everywhere:** drives showed no measurable gain. The cheap targets left are the prefilter loops (installs and signals, 28k `may_mention` each, `mentions_many` fits) and the `derived` decode in uses (0.19 s), perhaps 0.1 to 0.2 s together. Estimate **−0.1 to −0.25 s** for maybe 200 lines of Rust; the logic stays Python.
- **C, leave it:** 0 s, 0 lines.
- A middle path the numbers support: port only django.uses (Django pack, lives in Hoglint, logic changes rarely, about 100k rows) for about −0.3 s, and leave the PostHog pack in Python.

## What surprised the spike, and what it could not test

- The port matched the Python producer exactly on the first build. The Python logic was written row by row with explicit memos, so it translated almost line for line.
- The Rust uses call still costs 0.22 s: serde_json over `derived` and regex tokenizing. A typed `derived` column in the link would take most of that.
- The coarse queries removed 24k calls and 112k strings and still did not show. N1's rule holds the other way too: the time is where the Python objects of the judgment are (`Mention`, `Edge`, `Refinement`), not in the lookups.
- facade-facts spends 0.13 s in the sink alone (42k `Edge` objects): a column-wise `add_edges` would help any shape, A or B.
- Not tested: quiet-machine timings (none were available), the Python engine with the port (it does not have the method; it would keep the Python code), conflict bookkeeping across producers with the port, a port of drives or facade (both are the PostHog pack, D15).
