# Adversarial reviews of the design, part 2

> Six adversarial reviews of the Hoglint design draft, part 2: the ledger, implementation and tool-replacement reviewers, the second opinion's adjudication, and a follow-up measurement, Nov 6 and Nov 9, 2026.
> Partly replaced: the fixes are in `docs/design/` (mainly `02-policy-exceptions-ledger.md`, `05-engine.md`, `06-fact-cache-and-summaries.md`, `08-fast-hook.md`, `11-repo-and-packaging.md`) and the decisions in `docs/history/decision-log.md` and its continuation files.

Part 1, with the method, the blockers and fixes, and three reviewers, is `docs/research/adversarial-reviews.md`.

## Reviewer: ledger, growth, attribution, moves, merge queue

Scope: parts of the design draft (not published); the data model sections 4, 9 and 16 to 19 (`docs/research/data-model.md`); the attribution spike; `docs/research/depends-on.md`; the existing crossings scanner and growth gate in the PostHog monorepo.
Evidence: a replay of the existing crossings ledger over its 65 first-parent master commits.

The reviewer opened with "the existing growth gate has never run in CI".
Verification refuted that (see part 1, "Overstated or wrong").
The findings below do not depend on it.

### 1. Legitimate growth has no admission path, and a target move into a product is that growth (blocker; plausible in principle)

Sequence (commit `ffd78d9c710`, which moved query runners from core into a product, with no scanner change):
- Base: the runners live in core. Core is not a wiring location, so tests that run them produce no `drives` lines.
- The PR moves the runners into the product and edits no test.
- The ledger gains 180 `drives` lines on three keys, from 0.
- Attribution: the base scanner and base policy on head code see the same 180 lines, because the move is code. Result: code growth, fail.
- Expected: pass. Moving code into products is the isolation program's main activity.
- The only way through is to split the change: first a `hoglint.toml` assignment that puts the old directory into the product (rule growth, passes), then the move. That route goes through finding 2.

The same shape:
- A revert of a cleanup PR during an incident restores old couplings. That is code growth, so the revert is blocked.
- Target identity was inconsistent. The draft said a model's identity is its table name; the config used `growth_key = ["dst.symbol", "shape"]`; the ledger printed owner-by-module names. A move of a core model into a product renames the key on every one of its lines (about 100 for the hottest target), which is growth on a new key.
- A model rename without an explicit `db_table` changes the table. A drives location and a facade callable had no stable id at all.

Smallest fix proposed: an admission class "boundary growth" (the target's identity existed at base, and only its package or tier changed), passing with a `compare` listing and the review an exceptions addition gets; an explicit `revert` admission (a PR that reverts commit X may restore exactly the ledger keys X removed); and `dst.identity` (table name for models) in the config instead of `dst.symbol`.

The second opinion tightened this: "target existed, package changed" is not enough; prove the admitted couplings already existed, with unchanged counts and matched identities, and reject new uses bundled into the move.

### 2. The policy file launders code growth and receives no review (blocker; confirmed by design reasoning)

**Sequence A (loosen, then couple):**
- PR-a edits `hoglint.toml` and assigns one core API module to a product's package. The draft rated `hoglint.toml` additions as "neutral". Rule shrink passes both checks, and one ledger line is removed.
- PR-b adds a write on that product's model in the same module. The module is now the same package as the target, so there is no finding.
- Outcome: a new write on a foreign model, zero ledger lines, and no tool-owner review anywhere. The same works with `select`, `allow_shapes`, `contexts.exclude` or `[zones]` edits.

**Sequence B (bundle new detection with new code):**
- One PR adds flow-refined shapes (def-use). It also adds new code of a shape that only the new extractor's def-use pass sees.
- Head ledger, key `(Team, lock)`: base 2 → head 2 + 1 (an old case now found) + 1 (new code).
- Attribution with the base scanner on head code sees neither of the +2, so both are labeled rule growth and pass.
- Expected: +1 code growth, fail.

Smallest fix:
- Double-difference the attribution. Code growth = scan(head code) − scan(base code), computed under the base tools and again under the head tools. Either positive result fails. Sequence B then fails under the head tools.
- Classify every policy diff by its effect: run the head policy on base code. Any finding removed, and any package reassignment of an existing module, needs the review that exceptions additions get.

### 3. Growth keys: per-line for 4 of 5 ledgered rules (major; confirmed)

Only `model-crossings` declared `growth_key`.
drives, reverse-accessor, facade-signature, public-surface and the cycle rule fell back to the data model's default: "a rule without a declared growth key defaults to per line".
The existing scanner sums every channel per (first column, kind), so this was a regression.

Real moves that pass the existing gate and fail under the draft config (replayed): a test file moved within a product (`9e690f001f2`), a model moved between products (`fe45977ac01`), and a test promoted within core (`897c502836f`).
Per line, each added line is a new identity, so it counts as code growth and fails.
Git follows every one of these moves at 98 to 100% similarity.

The rules also conflict.
One rule requires a facade module to split into capability submodules, and that split changes every `facade-slot` identity, so facade-signature fails the fix another rule demands.
A product directory rename changes every `package-pair` key in the cycle ledger, because the package id came from the path.

The other direction: the one location-free key needed no evidence of a move.
Remove one use of a model's manager from one package and add one in another package: the key is unchanged, equality passes after a regenerate, and growth reads "moved".
A product gained a cross-package write.

Smallest fix (one mechanism for both):
- `growth_key` is mandatory on every ledgered rule. Port the existing projections: drives (location, trigger), reverse-accessor (target, accessor), facade (denied type, slot), public-surface (dst identity).
- Carry a line only when its site is rename-matched to a base site (git `-M` file rename plus qualname).
- An unmatched add paired with an unmatched remove on the same key is growth.

### 4. Resolution flips and regrowth after a scanner bug are blamed on innocent code (major; plausible)

**Sequence A (scanner bug):** scanner v2 breaks the re-export fixpoint on `from .x import *`, and k lines on key K vanish. Rule shrink passes, and reviewers read "-" lines as good. Weeks later, a PR replaces the star import with explicit names (a ruff F403 cleanup). The buggy v2 now resolves, and K grows by k. Base scanner = head scanner = v2, so the growth is code growth, and the PR fails with "Fix the code" for couplings it never wrote.

**Sequence B (unresolved to resolved):** 92 unresolved `get_model` sites existed, about 31 of them real dynamic reaches. A PR hoists a label into a module constant, and the new kind-candidate fact now resolves `apps.get_model(label, "Thing")`. Keys: `(unresolved, get_model)` −1 and `(Thing, get_model)` +1. The second is growth on its own key, so the PR fails.

Smallest fix:
- Match findings across base and head by `Edge.id` before summing. An edge that existed at base (unresolved, or resolved differently) is a resolution flip. It passes when the losing key shrinks by the same count.
- A rule-change shrink writes tombstone entries (key, count, scanner version) that equality ignores. Later growth up to a tombstone reports as "resurfaced" and does not fail.

### 5. Ledger-against-ledger growth plus git's merge of counted lines breaks master, and the queue base is unspecified (major; confirmed)

Identical-hunk merge of a hot counted line:
- Base: one line counts 1 use in module m1, plus one line each in m3 and m4. Sum 3.
- PR A moves a use from m3 into m1: m1 1→2, m3 removed. Sum 3, passes.
- PR B moves a use from m4 into m1: m1 1→2, m4 removed. Passes.
- Git sees the same change on both sides and merges it cleanly. Merged ledger: m1 = 2. Scan: m1 = 3.
- If the queue checks equality, the batch fails and bisection kicks B, which is innocent.
- If only PR CI checks it, master lands with ledger ≠ scan. Then every PR fails equality on a line it never wrote, and the regenerate PR fails growth when growth compares ledger files (base ledger 2, head code 3).

Combination case (cycle rule): A adds package edge x→y and B adds y→x. Neither alone closes a cycle; together they create two new cyclic edges. Only a check in the queue catches this.

Smallest fix: define code growth against scan(base code), not ledger(base), so master drift never blames a PR and equality stays the only check of the file.
Equality and growth run in the merge queue with base = the queue's target tip, so the batch is judged as merged and bisection assigns the cycle to the second PR.

The second opinion called "deadlock" overstated: the existing gate admits growth that comes with a reviewed scanner change, so recovery is possible.

### What holds up

Splitting equality from growth is right; equality stays exact.
Growth summed per key is right in principle, and attribution with the base tools fixed does separate rule growth from code growth (the attribution spike replay: +15 rule growth, 0 code growth).
Location-free carry matches how code actually moves: git follows every real move found at 98 to 100% similarity.
Exceptions in their own file, keyed to a target and reviewed on addition, keep reasons and owners out of the generated ledger.
The cycle ledger keyed by package pair is the right granularity; its problems are the package id taken from the path and checks that see one PR at a time.
The holes cluster in two places: what counts as "the same debt" across a change (findings 3, 4), and which channels may grow it without review (findings 1, 2).

---

## Reviewer: implementation and shipping

Scope: distribution, toolchain, caching, pre-commit, time budget, failure modes.
Checked against parts of the design draft (not published), the data model sections 17 to 20, the end-to-end spikes (including the Rust L1 source) and the Rust parser spike, at `d1051a3875f`.

### 1. "uv workspace member" and "prebuilt wheel, developers need no rustc" cannot both be true (blocker; confirmed; adjudicated as partly overstated)

- If the Rust crate is a uv workspace member, the dev environment's `uv sync` builds it from source with the environment's cargo 1.91.1. The ruff parser crates need rustc 1.94 or later, so the build fails and the venv does not come up. The same build runs in every CI workflow that syncs the environment.
- The other reading is a pure-Python member plus a Rust wheel on PyPI. Then every L1 schema change takes three steps: merge, publish, merge a pin bump. Until the pin bump merges, the in-tree L2 reads facts from the older pinned extractor. The early phases change the schema many times.
- No document said where the wheel goes, who publishes it, or how in-tree Python stays in step with it.
- All existing workspace members were pure Python; Hoglint would be the first compiled member.
- The import-map consumer in CI runs with no project sync and no cargo, so the map tool must install from an index.
- The precedent is PostHog's `hogql-parser-rs` wheel: a maturin build matrix, publish from master only, and a pin in the monorepo. The precedent's own source and pin had once drifted by a patch version, so the lockstep risk is real.
- Attribution ("replay with the extractor held fixed") needs the base extractor in CI. Built from source, that means two cargo builds per PR.

Smallest fix: ship Hoglint as one maturin package (Rust L1 and Python L2 in one wheel) and publish it on the `hogql-parser-rs` model.
Consume it everywhere by pin (a dev dependency for check and pre-commit, a PEP 723 pin in the map script), exempt it from `exclude-newer`, and keep it out of the uv workspace.
Hoglint's own CI builds from source with a `rust-toolchain.toml`; Hoglint developers use an editable install.
L2 refuses facts whose schema version differs from its own and reports `incomplete`.
A side benefit: a pin bump becomes the one place where attribution sees rule growth from an extractor change.

The second opinion: a Python workspace member can depend on a prebuilt native wheel; a locally built mixed Rust member conflicts with the documented toolchains.
The decision was a prebuilt wheel.
The packaging design is in `docs/design/11-repo-and-packaging.md`.

### 2. The "staged snapshot" is the working tree, and lint-staged does not fix that (major; confirmed in spike code)

- An agent edits 5 files and stages 2; Hoglint judges all 5.
- An untracked scratch file adds an import edge; ledger growth blocks an unrelated commit.
- A file deleted on disk but not staged drops out of discovery, so ledger equality fails on lines the commit does not touch.
- CI sees none of this, because its checkout is clean. Local and CI results differ.

The spike discovery used `git ls-files -co --exclude-standard` (`-o` adds untracked files), filtered by `is_file()`, and read contents from disk.
lint-staged hides unstaged hunks only for partially staged files; other modified and untracked files stay as they are.
It also runs task groups concurrently while the Python group rewrites files.

Smallest fix: an `--index` source mode.
Read paths and blob ids with `git ls-files -s -z` (`-c` only), read contents with `git cat-file --batch`, never read the working tree.
Run it once from the pre-commit hook after lint-staged, so formatters have re-staged their changes, not as a lint-staged task.
CI uses the same mode on HEAD, so local and CI read identical inputs.
The blob id is a free cache key.
The second opinion added: read config from the index too.

### 3. The L1 cache key leaves out the extractor build and the parser version (major; confirmed in spike code)

Because Hoglint carries the Isolated skip's safety, stale facts can wrongly skip tests, not only cause noise.
- A walker fix changes which facts come out of some file shape, and nobody bumps the schema number. Every unchanged file keeps its old entry forever.
- A warm laptop and a fresh CI runner then report different ledgers.
- A parser crate bump (weekly 0.0.x releases) can change the AST shape, with the same effect.
- A cache shared across worktrees mixes entries from different builds.

The spike keyed the cache on a hand-set schema version (still `1` while running v1.1 rules), the config and two caps.
The crate version, the parser crate versions and the build id were not in the key.
A helper that lends one checkout's venv to another worktree when the lockfile matches would also run the wrong build after an edit to an editable Hoglint.

Smallest fix: cache key = (git blob id, build id), where the build id hashes the crate version, `Cargo.lock` and the extraction flags at compile time.
Keep the cache under the git common dir, so worktrees share entries only within one build.
Print the build id in the run envelope and in every `explain`.
Add `--no-cache`.
Do not use a CI cache for L1: a cold run costs 2.55 s, too little to be worth a staleness risk.
The second opinion added: (blob, build) alone is not enough unless the build identity also covers schema and options.

### 4. One exit-code contract serves three consumers that need different failure behavior (major; plausible)

- A draft PR has one file with a syntax error. The old `tach map` drops that file and exits 0, which cannot under-test. Under Hoglint's doctrine (parse failure = unresolved fact, producer failure = incomplete, exit 2) the map run fails, and the CI test selector widens to every product and every merge-queue lane.
- The same widening happens when the wheel is missing for a platform, the package index is slow, or a new pin is still inside the `exclude-newer` window.
- For pre-commit, nothing said whether a missing binary, a timeout or `incomplete` blocks the commit.

Smallest fix: each consumer gets its own contract.
`hoglint map` exits 0 when it can write a map, lists unparsed files in the map body, and consumers widen only for the products that own those files.
Pre-commit treats a missing binary, a timeout above budget, or `incomplete` outside the staged files as "skipped, CI checks it", with a one-line notice.
CI `check` blocks on `incomplete` only when the incomplete facts sit in files the PR changed; otherwise it warns and routes to the tool owner.

### 5. The 7 to 9 s budget is not measured as one run, and memory is not measured at all (major for pre-commit; confirmed)

- Agents commit many times per task, often in several worktrees at once, and each commit already runs ruff, format and `ty check`.
- Hoglint adds a whole-repo run on every commit: 12.8 s of CPU for Rust cold, about 1.2 s warm, about 6 s of CPU for Python L2, and an unknown peak RSS. L2 held a 207 MB JSON fact store with 2.98 M rows.
- The warm 7.4 s figure was "composed, not run as one". Every timing was measured under load. No document recorded RSS.
- The only lever named was a daemon, which is a large new surface.

Smallest fix: before pre-commit is in the definition of done, measure one end-to-end warm run on a quiet laptop with wall time and peak RSS; gate the hook on staged `.py`, `.pyi`, `hoglint.*`, `product.yaml` or `owners.yaml` files; set a hard timeout that falls back to "skipped"; do the per-def body fact for drives first (2/3 of channel time), which may make a daemon unnecessary.
The later engine work is in `docs/design/07-speed-and-memory.md` and `docs/design/08-fast-hook.md`.

### What holds up

Rust L1 matched the Python reference on all 25,534 files.
Every legacy output reached exact parity from Rust facts.
A case-sensitive inventory from `git ls-files` instead of `exists()` removes the macOS phantom-edge class that tach has.
A full L2 recompute over a content-addressed L1 removes per-edge invalidation as a bug source.
Keeping the crate outside the monorepo's Rust workspace is correct.
The problems are all at the edges: how the binary reaches each consumer, what "snapshot" means, what the cache key covers, and what each consumer does when the tool cannot answer.
Each fix is small if decided before the first build layer, and expensive after.

---

## Reviewer: replacing the older tools

This reviewer attacked the plan to replace tach, import-linter, the crossings scanner and the product lint in the PostHog monorepo, and how test selection would survive it.
The parts about one monorepo's adoption order and CI files are not published.
The design lessons, in tool terms:

1. **A reader that returns empty on a missing source must fail closed.** When a tool's config or baseline is replaced, any reader of the old source that returns an empty result on a missing file reads as "nothing to protect", and a test skip can follow from that. Lesson: switch every consumer before deleting a source, and make each reader raise on a missing source, with a test that proves it. (Confirmed.)
2. **Fact parity does not prove verdict parity.** The shadow phase compared facts (94,143 of 94,143 map edges, the exact ledger), never the rule results of the old checks against `hoglint check`. Verdicts depend on config semantics the fact diff never exercises: regex `expose` values that match more than a glob, a two-pass mode that treats tests differently per pass, `utility` and `unchecked` flags, a comment marker another tool parses. Some old rules had no row in the inventory and no replacement rule. Lesson: shadow verdicts and selected tests per rule, seed replay fixtures from known escapes (`docs/research/research-round-1-empirical.md`), and list every old rule in the inventory by name. (Plausible.)
3. **Hoglint's own inputs are graph inputs.** Once Hoglint draws the import map, `hoglint.toml`, the preset, the tool source and the wheel pin shape the graph, so CI must treat a change to them as a graph change. The map output must say "incomplete" or "file not parsed"; the old map format had no such field. Lesson: a golden-graph test (product-pair edges at a pinned fixture commit) on every tool change, and the old map tool kept as a scheduled oracle for a soak period. (Confirmed.)
4. **Switching and deleting are separate steps.** If one step switches consumers and deletes the old config, a revert does not restore the old protection, because other readers (scaffolding, maturity scoring, CI routing) changed with it. Lesson: switch behind one flag with the old tool as an oracle, delete after a soak with zero diffs, and write the rollback for each step. (Confirmed.)
5. **Routing test selection through core packages removes "a core change runs everything".** A named infrastructure package is mostly consumed without a product import: settings dotted strings, middleware and Celery config strings, conftest fixtures, package-initializer side effects. The importer of such a reference is the settings module, a core node the product graph drops. Lesson: a core package leaves full-suite routing only when its impact view includes dotted-path and settings edges, an edge into the composition zone (settings, URLs, apps, conftest) maps to "full suite", and `incomplete` maps to "full suite". (Plausible, a later-phase risk.)

What holds up: shadow-first with real oracles is the right shape, and the fact parity is strong.
Keeping the old map JSON as the CI interface keeps its fail-safe paths (deleted files widen, map failure widens, test files stay in the graph).
The type-only default matches tach's `ignore_type_checking_imports`.
"Unknowns count against the product" and "no violations never authorizes a skip" are the right doctrine; the risk is in ordering and wiring, not in the model.

---

## Second opinion: adjudication

A second opinion from another model adjudicated the findings that survived verification.

1. **Configuration 1: real; blocker before build.** Use field domains and distinguish a known `none` from an unresolved unknown. Define every selector position, but do not turn an unknown selection into a definite violation: report incomplete coverage and deny Isolated eligibility. Validate field names and types first; typos must be config errors.
2. **Configuration 2: real; blocker before build.** Reject duplicate definitions; require explicit amendments. Expanded-config diffs are right. Start with explicit replacement and list add/remove operations, not a general patch language. Preserve provenance so reviewers see what changed.
3. **Ledger 2 and configuration 3D: real; blocker before build.** "Config changes are neutral" permits policy laundering. Compare old and new policy on the same base code, but also review structural relaxations that remove zero findings today. Require tool-owner review for boundary relabeling and weakened protections. To catch bundled scanner improvements plus new coupling, compare base and head code under both tool and policy versions; base-scanner attribution alone misses it.
4. **Ledger 3 and 5: real, with "deadlock" overstated; blocker before build.** Require an explicit growth identity for every ledgered rule, not every rule. Compare scans of base and head code; separately require head-ledger equality. Run both on the merge queue's actual merge candidate against its target base. The existing growth checker compares ledger files and admits growth that comes with a reviewed scanner change, so recovery is possible. The claim that growth never ran in CI is wrong.
5. **Ledger 1: real; fix in design.** A reviewed boundary-growth admission is appropriate, but "target existed; package changed" is not enough. Prove the admitted couplings already existed, with unchanged counts and matched identities; reject new callers or uses bundled into the move. No relocation list or general growth waiver.
6. **Gaming 1: real; fix in design before skips rely on it.** Model installation plus callback dependencies. `installs` is reasonable, but extending `registers` to callbacks may suffice. Recognize declared APIs and framework patterns, not every `register_*` name. An unresolved callback closure must widen test selection; a complete Python call graph is unnecessary.
7. **Gaming 2 and 3: real gaps; fix in design.** Emit dynamic-import attempts, resolve declared loader patterns, and mark the rest incomplete. A path alone cannot grant test exemptions. Full production-entrypoint reachability is excessive as the first fix: prohibit runtime production-to-test imports unless explicitly reviewed, and keep production treatment across those edges.
8. **Core 1: real; blocker before build. Core 3, 4, 5: overstated; fix in design.** Move ORM and DRF interpretation into packs; core emits neutral syntax and use positions. Untested generality does not establish that exports, registries or provenance are wrong core abstractions. Keep generic declarations with shared selectors, shape predicates and pack-provided discovery. Bare `registers` and `generates` edges lose useful contracts and provenance. Add cross-repo fixtures and a language-qualified entity seam; (language, path, symbol) is snapshot identity, not move-stable debt identity.
9. **Implementation 1: overstated; implementation 2 and 3: real.** Packaging is a blocker before build; index mode and the cache key are fixes in design. A Python workspace member can depend on a prebuilt native wheel; a locally built mixed Rust member conflicts with the documented toolchains. Use the pinned `hogql-parser-rs` distribution pattern. Read index blobs, including config, not working-tree files. Cache by content plus extractor, build, schema and options identity.
10. **Cutover 1, 3, 4: real; fix in design before cutover.** Switch all consumers before deleting their sources. Missing required evidence must produce incomplete and no skip. Update CI filters first. Separate switching from deletion, keep a tested rollback during a soak, and compare verdicts and selected tests, not only facts.

**Minimal revision to agreed decisions:** items 2 and 3 change "`hoglint.toml` edits are neutral" to "architecture edits have no inherently good or bad direction, but relaxations require review".
Keep the three-file split.
Item 8 does not justify reversing facade-as-core-export or registries-as-core-concept: keep them, move framework semantics into packs, and reuse existing primitives internally.

---

## Follow-up: new producer work, measured (Nov 9)

After the review, the architect measured the producer work the fixes need, on the PostHog monorepo at `d1051a3875f`:

- **signal edges:** 217 non-test `@receiver` uses (205 with `sender=`, 18 with the sender as an `"app.Model"` string, 12 without a sender, which means all senders); about 59 explicit `<signal>.connect(...)`; 8 custom `Signal()` definitions. Derivable from existing L1 facts (decorator args, call args); the data model already listed signal senders as declaration edges. Unknown: no sender, or a variable sender. The blind design (`docs/research/blind-design.md`) had `signal` and `mock.patch` dotted-ref edges; the first data model parked receivers as a plugin.
- **installs edges:** 56 `register_*` calls in 18 `apps.py` files, about 12 hook APIs (the four most used have 10, 8, 6 and 6 calls). Needs a declared hook list. Callable-as-argument is the L1 `escape = arg` fact from the shapes spike.
- **mock.patch targets:** 18,730 `patch("<first-party>...")` strings and 6,105 `patch.object` uses in tests. Already dotted-path StringRefs (the file-imports view includes them for tach-map parity), so this is rule work only.
- **dynamic imports:** 74 non-test, non-migration files call `import_module`, `__import__`, `import_string` or `get_model`. An L1 call-site fact; the cost is triage.
- **Return-position exposure (blind finding A8): dropped.** 3 of 2,187 public top-level facade functions lack a return annotation. Replaced with: a return annotation is required, and `Any`, `dict` or `dict[str, Any]` is unknown.
