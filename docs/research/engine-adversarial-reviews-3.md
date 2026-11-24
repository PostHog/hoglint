# Engine round: adversarial reviews (3 of 3)

> The reviews of the engine round, Nov 9, 2026, part 3: the skeptic, the demand audit, and the list of bugs and waste the round found, each with what it led to. The method is in `engine-adversarial-reviews.md`.
> Partly replaced: the decisions these findings led to are in `docs/history/decision-log.md` and later parts, and the engine as built is in `docs/design/05-engine.md`.

---

## The skeptic

Scope: how the engine does its work, not what it must achieve.
Code at commit `0c197478064`.
Numbers: **[m]** measured by the skeptic, under heavy load, so wall times are upper bounds; **[pm]** from the performance study of the first implementation (full check 44.7 s, 3.9 GB); "unchecked" where a claim could not be verified.

### Three open questions (Nov 9)

**Why owners work at all?**

- No rule read `owners`. It fed only output: the owners printed next to a finding and `query --by owners`.
- The cost came from config validation. `Engine.run_rules` first called `Labeler.find_conflicts`, which labeled every module and called every provider for each path.
- `owners` is a set field. Set values are unioned and can never conflict. So the provider call added nothing to the conflict check.
- [m] `find_conflicts` over all 29,142 modules: 8.62 s with the owners provider, 0.47 s without. The conflict list is the same.
- [m] Owners for every module any finding names, with an empty ledger (the worst case, 3,427 modules): 0.91 s. With a ledger, only new findings print, so this is close to zero.
- compare paid the 8.6 s once per evaluation, because each engine made its own labeler and provider runner.
- Answer: compute owners only when a finding prints or `query` asks. A content-keyed owners cache is then not needed.

**Why copy the whole git index into a temp directory?**

- `check --index` made two copies of the index, not one.
- Copy 1 checked out the whole index to read the policy files (`hoglint.toml`, the preset, the ledger, the exceptions file). [m] 68,871 files, 955 MB, 21.5 s.
- Copy 2 wrote every staged `.py` blob to a second temp tree so the Rust binary could read files from disk. [m] 29,152 files, 6.1 s, 676 MB peak RSS, because `cat-file --batch` output was held in memory whole.
- [m] 0 `.py` files differed between the index and the working tree. Both copies duplicated files the working tree already held.
- [m] `git show :<path>` reads two policy files in 0.11 s.
- Answer: read the policy files with `git show`. Key the fact cache by git blob id so an unchanged blob is never read. Both copies go.

**12.5 million frozen dataclass objects: isn't this what Rust is for?**

- Rust only parsed: 1.34 s of 44.7 s [pm]. Everything after the parse was Python: decoding 253 MB of JSON into 1.8 GB of dicts, all name resolution, one `Edge` per coupling, labels and rules.
- The 12.5M objects [pm] fall in three groups: resolver intermediates (`Lookup` 1.78M, `Resolved` 1.43M, `Hit` 0.37M); the graph (`Edge` 1.58M, `Site` 1.46M, `EntityId` 0.81M); rule-time interpreter values (`Value` 2.0M, `Endpoint` 0.64M), plus 0.81M in the drives port.
- The line between Rust and Python had one stated reason: "Shape kinds stay in Python: a ledger change needs no Rust release". That reason covers classification and policy. It does not cover name resolution, which follows the language, not the ledger.
- The cost basis came from the spike, where core L2 took 0.04 s and the cold run 5.35 s. The spike built one channel per output; the shipped engine built a repo-wide graph of every coupling instead.
- Answer: yes, this is the work Rust should do.

### Mechanisms, with verdicts

Verdicts: keep, change, drop, unsure. The most important rows:

| Mechanism | What it is for | Cost | Simpler approach | Verdict |
|---|---|---|---|---|
| Rust extraction subprocess | per-file facts from one parse | 1.34 s warm [pm]; reads and hashes every file every run; cache 845 MB in 6 namespaces [m] | key the cache by git blob id; leave watch lists out of the namespace (see correctness F16: unsafe as stated) | keep, small change |
| Per-run facts file decoded into Python | hand facts to Python | 253 MB written and decoded per build; 2.95 s, of which 1.96 s GC; 1.8 GB of dicts [pm] | resolve in Rust; interim: one JSON document per kind and GC off, −2.5 s [pm] | change |
| Core producers | import, reference, dotted-path, declares, exposes, inherit, decorate, dynamic-import, unparsed edges | 9.4 s CPU [pm]; 1.42M edges, `declares` 279,581 and `exposes` 200,084 for every top-level function, though 652 of 29,141 files are facade files [m] | produce only the kinds a rule or output reads (see correctness F17: unsafe for compare) | change |
| Django `uses` | model-use shapes | copies 101,466 core reference edges with `dataclasses.replace` to add a shape [m] | store the shape beside the core edge | keep, small change |
| PostHog drives | the drives rule and coverage | 2.53 s, +0.47 GB [pm]; its own import table for every file: 310k `ImportEdge`, 500k `RootUse` | read core's import edges and resolver; read only test files and the files their calls reach | change |
| PostHog facade facts | `exposes` for functions a facade re-exports | 10,251 parity-only edges (`export`, `declares`, `inherit`, `decorate`) that no rule reads | stop emitting the parity-only kinds | change |
| Ancestry, twice | kinds, export shapes, model detection | one implementation reads bases back out of 126k `inherit` and `decorate` edges; another computes the same from the tables | one ancestry table | change |
| `find_conflicts` | config conflicts are errors | 8.62 s with owners, 0.47 s without [m] | skip set fields from providers | change |
| Owners on every finding | owners printed with findings | 0.91 s worst case [m] | compute at render time | change |
| Rule engine (selector interpreter) | 31 rules from config | 11.2 s [pm]; 2.0M `Value`, 0.64M `Endpoint` | keep the language; feed it fewer edges. Deduplication is not the fix: 1.58M edges hold 1.0M distinct couplings (1.6x) [m] | keep, change the input |
| Cycle rule keeps every edge env per package pair | no-new-package-cycles | 2.19 s, 0.83 s of it GC [pm]; uses only `len(envs)` and `envs[:3]` | keep a count and the first three witnesses | change, small |
| Rust output pinned byte for byte to the spike's Python reference | parity with the spike | structure crosses as strings Python parses per row | design the format for its consumers | change |
| `Edge` as a frozen slots dataclass, one per coupling | witnesses, identities, explain | 1.07 µs per `Edge`, 1.7 s for 1.58M; 754 MB without strings [pm]; a frozen class cannot take a shape, so `uses` copies the edge | an `Edge` only for a finding's witnesses | change |
| Summary with couplings | move admission | 0.52 s and 902,692 entries per scan; 125 MB of JSON across the subprocess boundary [m] | couplings only for witnesses of grown keys | change |
| Graph cache inside one command | one graph per root | `compare` holds 2 graphs: 5.4 GB peak [m] | drop a graph once its summaries exist | change |
| No cache above per-file facts | simplicity | premise was import resolve at 0.35 s; L2 is now about 16 s [pm] | decide after the resolver moves to Rust | unsure |
| `check --index` | pre-commit on the staged snapshot | 70.5 s, 4.5 GB [pm]; two copies, 27.6 s [m] | see the owner's second question | change |
| `check --changed` | advisory feedback on touched files | a full run, then a filter: 66.8 s with `--index` [pm] | local rules on touched files and by-name dependents (see correctness F1: unsafe) | change |
| `compare` and growth | PR mode | 2 to 4 builds and evaluations; common path 110 s, 5.4 GB [m]; plus a base checkout of 69k files that runs the repo's hooks | see wrong approach 2 | change |
| `explain <path>`, `explain edge:<id>`, `query` | debugging | each a full run, about 45 s; no output prints an edge id | labels without a run (0.47 s); print the id or explain by `path:line` | change |
| tach-format map | turbo cascade input | resolves each surface again the way tach does; ships an oracle diff and its own CLI | write the map from Hoglint's own resolved targets | change |
| Generic edge JSON dump | any non-tach output | 1.58M dicts when configured | remove | drop |
| `[django] oracles` key | the two Django-booting oracle jobs | the key is ignored; no oracle code exists | build the jobs, or reject the key until they exist | unsure |
| Legacy ledger tool in the package | diff against the crossings baseline | a second policy source inside the package; the only reason for the 10,251 parity edges | move it into the shadow job's script; delete at cutover | change |

### The five most expensive wrong approaches, ranked

**1. Python did all work after the parse, for the whole repo, as one object per coupling, before any rule ran.**
Cost [pm]: parse 2.95 s, core build 0.6 s, core producers 9.4 s, packs 5.0 s, index 0.9 s, about 19 s of 42 s CPU; most of the 7 s of GC; 12.5M dataclass instances.
Peak memory came from this phase: 3.72 GB right after the graph build [m].
Simpler: move resolution into the Rust binary or a PyO3 module in the same prebuilt wheel; Python receives resolved couplings as compact rows; an `Edge` object only for a finding's witnesses; shapes, labels, rules and the ledger stay in Python.
Interim steps: GC off during the build −4.9 s; mutable slots −3.6 s; lazy declarations −3.1 s and −460k edges [pm].
What does not help: deduplicating edges (1.6x); msgpack or columns while consumers iterate Python rows.

**2. `compare` evaluated the repo two to four times.**
Head build and evaluation; a `git worktree add` of all 69k files that ran the repo's post-checkout hook; base build and evaluation; with a changed policy, two more evaluations; with a changed scanner, two `scan` subprocesses with 125 MB of JSON each.
Common path: 110 s wall, 5.4 GB [m].
Proposed: read the base ledger with `git show` when scanner and policy are unchanged, plus base edges for touched files and their by-name dependents.
This shortcut was refuted by the correctness adversary (F1, F2).

**3. Snapshots copied the whole tree to disk.**
27.6 s of the 70.5 s pre-commit [m]. The policy needs 4 files; the code needs only the blobs that differ.
Simpler: `git show :<path>` for policy; a fact cache keyed by blob id; `git cat-file --batch` for base code, never checkout hooks.

**4. Labels ran eagerly, including the owners provider, for every module.**
8.62 s of the label phase, 19% of a check, once per evaluation [m].
Simpler: leave provider set fields out of the conflict check (0.47 s); owners at render time.

**5. Every command was a full-repo run.**
About 45 s per `explain` that needs 0.5 s of label work; 66.8 s for `check --index --changed` on a typical staged change of 3 files [pm].
Locality is strong: 79% of findings come from rules that need only the source file's edges and path labels; a local commit reaches a median of 3 files by changed name, p90 19 [pm].
Proposed: local rules for `explain <path>` and `--changed`; global rules reported as not judged in that mode.

### What the skeptic's review led to

- Question 1 and approach 4: D12 (owners at render time; provider set fields left out of the conflict check).
- Question 2 and approach 3: D4 (facts keyed by (extractor build id, blob oid); snapshots are lists of blob ids; no checkouts).
- Question 3 and approach 1: D1 and D2 (resolution in Rust; coarse calls and opaque handles; no Python object per row).
- Approach 2: refuted as a shortcut; replaced by exact-key summaries (D3, D14).
- Approach 5: D3's run memo serves `check` on a tree already evaluated, and `query` and `explain` read stored summaries; `--changed` filters output only (D5).
- The fact cache at 845 MB in 6 namespaces: D10 (one cache root, bounded by size and age).
- The legacy ledger tool, parity edges and byte pin: D11.
- drives reading every file: D13.

---

## The demand audit

Facts first; opinion only at the end.
Code at commit `0c197478064`. Code under check: the monorepo with 29,034 modules, 28,801 analyzed files, 291,025 symbols, 1,577,327 edges, 54,575 findings.
Warm fact cache, measured under load.
"CPU" is in-process CPU of the Python process; the Rust extractor runs as a child (0.8 s wall on cache hits).
Timings are the mean of 7 clean baseline runs unless a row says "GC off".
Instrumentation used monkeypatching and patched package copies; every instrumented run produced per-rule findings digests identical to the clean baseline, except where a row says otherwise.

### Summary

- A full check cost 28.3 s CPU / 30.0 s wall (m), 23.0 s CPU with GC off (m).
- Rules, internal readers and the conflict check selected **584k of 1.58M edges (37%)** (m). 993k edges were never read past the index (m). Rules alone selected 491k (31%) (m).
- The biggest unread blocks: 276k `declares` (99% unread), 192k core `exposes` (96%), 90k `decorate` (98%), 226k core `reference` (50%), 114k `import` (34%) (m).
- compare read more than any rule: its package triples read every cross-package edge of every kind and context (249k edges). With compare and the tach map, demand rose to 828k edges (52%) (m).
- Removing the 993k unselected edges after the build left all 31 rules' findings identical (m, 2 runs). An oracle-filtered `reference` producer kept 229k of 456k edges with identical findings and saved 0.73 s CPU of 23.0 (m).
- Edge construction is cheap: about 1.6 µs per reference edge, against 1.6 µs per row for resolution (m). Building only the selected edges saves about 3.5 s of 23.0 s CPU (e). The owners provider alone cost 6.0 s (m) and fed 0 printed entities in a steady-state full check (m).
- Several consumers read whole-repo data whatever the rules: `find_conflicts` labeled all 29,034 modules (owners included) and kind-matched all 291k symbols; `own-database` evaluated the kind of every symbol (m).
- Three double counts: 12,263 public-surface findings came from Django `uses` copies of core reference edges (37% of that rule); facade-signature judged 367 slots twice through two producers; no-new-package-cycles selected 241k same-package edges to keep 12.9k cross-package ones (m).
- export-shape read a `frozen` symbol fact that no producer wrote. 1,552 of the 2,481 symbols it reported as `export-shape(class)` were frozen dataclasses (m).

### Union of selected edges, by kind (m)

Layers are cumulative.

| Kind | Edges | Rules | + all inherit | + django.uses reads | + tach map | + compare | in findings or incomplete |
|---|---:|---:|---:|---:|---:|---:|---:|
| reference | 557,140 | 202,983 | 202,983 | 266,830 | 266,830 | 351,203 | 27,763 |
| import | 333,477 | 219,316 | 219,316 | 219,316 | 333,418 | 333,428 | 10,567 |
| declares | 284,815 | 3,302 | 3,302 | 3,302 | 3,302 | 6,364 | 3,302 |
| exposes | 232,144 | 40,178 | 40,178 | 40,178 | 40,178 | 57,184 | 5,856 |
| decorate | 91,739 | 1,860 | 1,860 | 1,860 | 1,860 | 7,418 | 0 |
| inherit | 35,979 | 5,858 | 35,085 | 35,085 | 35,085 | 35,512 | 0 |
| dotted-path | 27,928 | 13,339 | 13,339 | 13,339 | 27,901 | 27,901 | 1,234 |
| other kinds (10) | 14,105 | 4,493 | 4,493 | 4,493 | 4,493 | 8,624 | 1,136 |
| **all** | **1,577,327** | **491,329 (31.1%)** | **520,556 (33.0%)** | **584,403 (37.1%)** | **713,067 (45.2%)** | **827,634 (52.5%)** | **49,858 (3.2%)** |

`loads` (345) and facade-facts `export` (3,745) had no reader except compare. Facade-facts `declares`, `inherit` and `decorate` (6,506) had no rule reader.

### Absence and completeness

The engine checks `selection` before it checks for an unresolved destination. An unresolved destination reads every `dst.*` as unknown, so a selection with a `dst` part is unknown, never false. Such an edge is inside the scope unless a `src`, context or kind part is false.

| Consumer | Depends on absence or all edges because | Size (m) |
|---|---|---|
| wiring-watched (coverage) | a missing edge removes a required path, which reads as covered | 13,184 selected of 29,003 candidates |
| `on_unresolved` = finding / block / incomplete | unresolved edges must reach the rule | 718 of 880 unresolved edges |
| levels | incomplete items without subjects fail every group; unknown findings forbid the level even when ledgered | 179,461 of 209,116 selected |
| no-new-package-cycles | a missing pair can break a strongly connected component | 12,889 edges over 446 package pairs, plus 84 unresolved; the static selection admits 254,206 (241,233 same-package) |
| contracts-no-django (reach) | a missing hop hides a chain | 90,864 hops from 5,144 modules; 240 incomplete items |
| ledger equality | a missing finding makes a line stale | the full selection of every ledgered rule |
| find_conflicts, own-database | conflicts and kinds of every symbol | 35,085 inherit edges, 291,025 symbols |
| compare triples | new group pairs | 248,510 edges for 8,089 triples |

Does an unresolved edge outside every selected scope change a result?
For rules, levels and ledger lines, no: the mask run dropped the 162 unresolved edges no rule selects and all 31 digests stayed equal (m).
For compare, yes in principle: move admission compared coupling counts over all edges, and 4 of the 162 shared a coupling key with a finding witness (m).
For `explain edge`, yes: run ids used ordinals over all edges.

### Owners

Owners are a set field, so they can never conflict. The run computed owners for 29,034 modules in every scenario:

| Scenario | Printed findings | Owner modules needed |
|---|---:|---:|
| full check with no ledger committed (every finding new) | 54,575 | 3,392 |
| full check with an equal ledger | 0 | 0 |
| pre-commit `--changed` on 23 real 3-file local commits | 0 to 166 | median 3, max 25; 10 of 23 need none |

### Staged mode

`--index` checked out the whole index (68,871 files, 955 MB) to use as the policy root: 7.5 s wall (m). It then wrote all 29,152 `.py` blobs into a second temp tree: 4.1 s wall (m). The Rust extractor read and hashed every file of that tree, then hit the content cache. The rest was a full check; `--changed` filtered only the printed report. Total 70.5 s against 44.7 s (mm).

Inputs `--index` read from the working tree, not from the index: owners and product files, turbo and package files for coverage, the database routing file, and the classification file.

The least it needs: blob ids of staged `.py` files (`git ls-files -s`, 0.08 s for 29,152 entries, m); a fact cache keyed by blob id (92 to 100% of blobs exist in master or another worktree); blob contents only for cache misses (a median of 3 `.py` files per commit); about 200 small non-Python inputs from the index; no checkout.

### Python objects

12.47M dataclass instances per run (m). The largest: `selectors.Value` 2.0M (transient operands in rules), `Lookup` 1.77M (never reaches a rule), `Edge` 1.58M (584k selected, 993k never), `Site` 1.46M, `Resolved` 1.43M (copied into `Edge`), `EntityId` 0.80M, `Endpoint` 0.64M, the drives port's `RootUse`/`ImportEdge`/`Mention` 1.0M (for 2,173 edges), `KindMatch` 305k (about 14k read by rules).

Edge fields no consumer in `check` read: `via` (42,467 edges carry it; no rule reads it), `use` (read only by a rule that selected 0 edges), `site.column` (read only by django.uses). Many edge attributes were written and never read. Facts were read but never written: symbol facts `frozen`, `metaclass`, `members`, `dataclass`, `bases`, `decorators`, and the symbol field `database` (566 incompletes in own-database).

### Growth since the Nov 4 spike

The spike's Python L2 took 5.9 s CPU, of which core 0.06 s (a module index) and channels 4.66 s. It made no generic edge graph, no labels and no rules. On Nov 9 a check took 28.3 s CPU (m):

| Addition | Cost now, CPU s (m, GC on / off) | Consumer that drove it |
|---|---|---|
| core import producer (333k edges) | 1.29 / 0.92 | tach map parity, public-surface, zone and direction rules, reach, cycles |
| core reference producer (456k edges) | 2.05 / 1.44 | public-surface, cycles, direction rules; the Django uses producer |
| core declaration producer (declares 280k, exposes 200k, decorate 91k, inherit 35k; 291k symbols) | 2.89 / 2.01 | facade-logic, signature rules, export-shape, kinds |
| Django uses on core references (101k copies) | 0.82 / 0.78 | model-crossings (the copies also flowed into public-surface and cycles) |
| facade facts as edges (42k) | 0.15 | signature rules and export-shape |
| drives as edges (2,173) | 1.65 / 1.44 | drives, wiring-watched |
| rule engine, labels, selectors, levels | index 1.06, rules 6.40, levels 0.10 | the preset |
| owners provider | 6.0 inside find_conflicts | owners in the report |
| conflict check every run | 7.23 in total | config errors fail every run |
| JSONL parse of every needed kind | 2.17 / 0.68 | all producers |

GC: 4.9 s of the 28.3 s (m).

### Lower bound

| Phase | Now, CPU s (m, GC off) | Demand-only, CPU s |
|---|---:|---:|
| discover + extract (child, cache hits) | 0.35 + 0.8 wall | same |
| JSONL parse | 0.68 | ~0.5 (e) |
| core build (module tables) | 0.41 | 0.41 (e) |
| core-import | 0.92 | ~0.75 (e) |
| core-reference | 1.44 | **1.13 (m)** |
| core-declaration | 2.01 | ~0.6 to 0.8 (e) |
| dotted-path, dynamic-import, unparsed | 0.20 | ~0.15 (e) |
| django.uses | 0.78 | ~0.4 (e) |
| other Django steps | ~0.55 | same |
| drives, facade facts | 1.44 + 0.15 | same |
| index | 0.55 | **0.22 (m)** |
| find_conflicts | 6.92 | ~1.0 (e), minus the owners provider |
| rules | 5.66 | **4.75 (m)** |
| levels, equality, render | ~0.3 | ~0.3 |
| **total** | **23.0 (m)** | **~12.5 (e)** |

The edge-only part (everything except owners) is about 23.0 to 19.6 s (e).
A rule-derived filter that asked the rules' own selectors per edge kept exactly the same 229,204 reference edges and all digests equal, but cost more than it saved: producer 2.0 to 3.7 s, total +2.4 s CPU (m).

### The audit's opinion

- The edge graph is not the main cost. Owners (6 s), GC (5 s) and fact reading in drives (1.4 s) each rival or beat what demand filtering of edges can save (about 3.5 s). A redesign that only prunes edges lands near 19 to 20 s, not 5.
- Demand must be compiled into per-module and per-target label tables to pay off. Interpreting selectors per edge, even cached, costs more than building the edge.
- Run-wide consumers set the floor, not the rules: the conflict check, own-database's per-symbol kind and compare's triples need whole-repo data. They need a cheap whole-repo answer more than a demand-driven one.
- Some demand is accidental and worth deciding before any engine change: the Django `uses` copies counted again, facade-facts exposes judged next to core exposes, same-package edges in the cycle selection, readers missing for `via`, `loads` and facade `export`, and the unwritten `frozen` fact.
- Staged mode needs no checkout if the fact cache is keyed by blob id, and it should read owners, turbo and routing inputs from the index too.

### What the audit led to

- The architect's option A ("compute only what rules select") was refuted as the main lever (`docs/research/engine-synthesis.md`). D5: no demand pruning.
- Owners: D12. Staged mode: D4.
- The double counts and the unwritten fact: bugs 13 to 15, fixed with named deltas after parity was frozen (decision log, after D21).
- Compiled label tables: D24 (labels from membership sets) and D29 (selectors once per label class), then D39 (rules in Rust).
- Reach and cycle on native adjacency: D18 and D25.

---

## Bugs and waste found in the round

These hold in any architecture. Sources: audit (A), skeptic (S), correctness adversary (C), extension adversary (X), performance adversary (P). File references are to Hoglint's own modules at the time.

### Unknown reported as clean (blocks cutover)

| # | Bug | Evidence | Source | Led to |
|---|---|---|---|---|
| 1 | A file that does not parse yields no finding and no incomplete; the Django pack skips it | `fx1`, `packs/django/pack.py` | C F6 | D8 |
| 2 | Uses through `import a.b.c` make no `refs` row (refs only when the first name or attribute starts upper case) | `fx1`, `src/walker.rs` | C F6 | D8 |
| 3 | Star-import uses fall back to local / NOT_APPLICABLE | `fx1`, `core/sites.py` | C F6 | D8 |
| 4 | A base that resolves to a variable is dropped; ancestry reads complete and false | `fx1`, `core/symbols.py`, `index.py` | C F6 | D1 (ancestry with certainty), D8 |
| 5 | Pack scope stops at the INSTALLED_APPS packages without a gap | `packs/django/pack.py` | C | D8 |
| 6 | Non-literal settings list items are skipped; a model without an app label is dropped; only "zero models" is checked | `packs/django/apps.py`, `models.py` | C | D8 |

### compare and growth

| # | Bug | Evidence | Source | Led to |
|---|---|---|---|---|
| 7 | A move that also changes the shape (annotation to filter) passes as an "admitted move", exit 0 | `fx3`, `ledger/scan.py` | C F7 | D9 |
| 8 | Incomplete keys are sets: a second dynamic import in the same module counts as inherited | `compare.py` | C F8 | D9 |
| 9 | A failed base scanner skips the growth check with exit 0 | `cli.py`, `compare.py` | C F9 | D9 |
| 10 | The "base scanner" runs in the head environment with one machine-wide extractor binary whose build id is never checked | `facts.py` | C F9 | D9, D2 |
| 11 | Debt removed by a scanner, environment or policy-data change is not labeled; `policy_hash` misses classification and routing files and the Django version | `ledger/growth.py`, `config/schema.py` | C F11 | D9, D3 |
| 12 | `check` and pre-commit cannot tell introduced from inherited incompletes | `rules/base.py` | C F12 | D3 |

### Double counts and false findings

| # | Bug | Evidence | Source | Led to |
|---|---|---|---|---|
| 13 | Django `uses` copies of core reference edges also feed public-surface and cycles | 12,263 of 33,321 public-surface findings | A | fixed with a named delta; then D22 (packs refine core edges, never copy them) |
| 14 | The `frozen` symbol fact is read but never written | 1,552 of 2,481 `export-shape(class)` findings are frozen dataclasses | A | fixed with a named delta |
| 15 | facade-signature judges 367 slots twice (core and facade-facts exposes) | | A | fixed with a named delta |
| 16 | installs and signals append the same `Edge` object several times | `installs.py`, `signals.py` | A | fragile counts; no decision entry |
| 17 | Hooks match only the spelled last name: an aliased hook import makes no edge | `fx4` | C F5 | fixed with a named delta; D17 |

### Snapshots

| # | Bug | Evidence | Source | Led to |
|---|---|---|---|---|
| 18 | `--index` reads owners, turbo, routing and classification files from the working tree | `cli.py` | A | D4, D33 |
| 19 | Conflicted files vanish from the staged snapshot; `git commit -- path` uses a temporary index; `git status` collapses untracked directories without `-uall` | git experiments | C F15 | D4, D33, D40 |
| 20 | `--index` checks out the whole index twice (27.6 s) | | S | D4 |

### Waste and dead config

| # | Item | Source | Led to |
|---|---|---|---|
| 21 | Owners computed for all 29k modules inside the conflict check (6 to 8.6 s); no rule reads owners | S, A | D12 |
| 22 | `[django] oracles` is accepted and read by nothing; the oracle jobs do not exist | S | no decision entry |
| 23 | Parity-only machinery ships in the package: the legacy ledger, 10,251 facade parity edges, the Rust byte pin | S | D11 |
| 24 | Two ancestry implementations; the tach map resolves names again on its own | S | D1 (one ancestry in the link) |
| 25 | `explain`, `query` and `--changed` each start with a full run; `explain edge:` ids are printed nowhere | S | D3 (the run memo serves `query` and `explain`) |
| 26 | The cycle rule selects 241k same-package edges and keeps every edge env per pair | A, S | D18 (reach and cycle on native graphs) |
| 27 | `loads` edges and facade `export` edges have no reader; `via` is read by nothing in `check` | A | no decision entry |

### Not to do (interim steps that break correctness)

- Dropping watch lists from the fact-cache namespace serves stale facts (correctness F16).
- Demand-only edges change compare's coupling counts and `explain edge` ids (correctness F17).
- The base ledger as a stand-in for the base scan (correctness F2), and by-name locality as a bound (correctness F1).

### Added by the adjudication

- **drives has a depth-sensitive memo problem.** Depth exhaustion returns an ordinary false with no incomplete marker; memo keys omit depth, and negative results are stored before traversal. A deep traversal can leave a negative result that a shallower traversal reuses later. Led to D13 ("fixes the depth-memo bug", as a counted delta).
- **Counting incompletes still cannot see a replacement.** Removing obligation A and adding B under the same coarse key leaves the count unchanged. Led to D9 ("ambiguous replacements reported as such").
