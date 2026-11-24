# Engine decision log, part 1 (D1 to D21)

> The engine decision log of Hoglint, part 1: the shape, the correctness contract, hygiene, the summary store in CI (D14), the own repo (D15) and phase 3b, Nov 9, 2026.
> Partly replaced: the current state of each topic is in `docs/design/` (start at `docs/design/README.md`); later entries in this log and in `docs/history/decision-log-2.md` and `docs/history/decision-log-3.md` revise several decisions here.

## How to read this log

This is history. Entries are in the order they were written, from Nov 9 to Nov 11, 2026; a few entries carry an earlier date than their neighbours, because they were written up later. Decisions were revised in place: an entry marked REVISED, amended, superseded or withdrawn is followed by its new form, and a later entry wins over an earlier one. For the current state by topic, read `docs/design/` first (`docs/design/README.md` is the index). The log continues in `docs/history/decision-log-2.md` (D22 to D47) and `docs/history/decision-log-3.md` (D48 to D70).

**Who decided.** A decision marked "the owner" was stated by Hoglint's owner. Every other decision is the architect's: an AI agent that designed the engine, wrote this log and directed the agents that built each phase. The owner could veto any of the architect's decisions. "The architect's call" in an entry means the same thing as an unmarked decision.

**Names used in the log.**
- "The native engine" is the Rust engine reached through PyO3; "the Python engine" is the original implementation, kept as the differential oracle.
- "Parity" and "the ten tables" are the comparison of both engines' full outputs over the PostHog monorepo snapshot.
- "Crossings 968 + 2" and "tach 0/0" are invariants that compare Hoglint with the scanners it replaces.
- "adv-performance", "adv-correctness", "adv-extension", "skeptic" and "audit" are the adversarial reviews of the engine round; their findings are summarized in `docs/research/engine-adversarial-reviews.md`.
- "Bugs 1 to 19" are the numbered defects of the first engine found by those reviews.

The log starts after the engine round of Nov 9. The phase 3 agents built against it.

## The shape

**D1. Rust holds the mechanics, Python holds the meaning.**
- Rust:
  - facts (the schema-v3 catalog of that time);
  - configurable name resolution;
  - class ancestry with certainty (yes, no, maybe);
  - candidate indexes by resolved origin and by spelling;
  - graph work: a CSR per context mask, reach, SCC.
- Python:
  - the Django and PostHog packs;
  - shapes, labels and rule predicates;
  - ledger, compare policy and outputs.
- Why:
  - The resolver runs 10x faster in Rust (adv-performance F4, narrow parity).
  - Packs and rules change weekly and are written by Python developers.
  - Prior art: tools move the mechanics native and keep the meaning scripted.
- Later: D39 and D47 revised in `docs/history/decision-log-2.md` replace this split.

**D2. The bridge is in-process PyO3, in the layout of an existing PyO3 package (owner, Nov 9).**
- A pure core crate plus a thin binding crate, maturin, abi3 cp312 wheels and a `.pyi` stub.
- Coarse calls and opaque handles: no JSONL on the check path, no Python object per row.
- `Edge` objects exist only for finding witnesses and for edges that packs emit.
- Why: one fewer pattern to maintain (the same layout as that package). The cost was always the per-row crossing, never the language (prior art: LibCST, grimp).

**D3. There is one cached artifact type: the run summary.**
- The key is (code tree oid, engine id, policy hash, framework digest). It is stored in one cache per machine that all worktrees share.
- Readers:
  - the pre-commit and `check` baseline, for introduced versus inherited;
  - compare's base and head;
  - the run memo: `check` on a tree that was already evaluated is a lookup;
  - `query` and `explain`;
  - later, an artifact from main-branch CI with the same key.
- A staged run stores its summary under the staged tree oid, so the next commit's HEAD baseline already exists.
- A missing summary is computed inline and stored.
- Only an exact key match counts. A corrupt or mismatched entry is a miss.
- Why: one mechanism serves five needs. The key is exact, so the cache cannot drift from a clean run. A background pull of summaries fits as one more source.
- Later: D34 (`docs/history/decision-log-2.md`) changes the tree part of the key to the input set.

**D4. Facts are keyed by (extractor build id, blob oid).**
- Snapshots are lists of blob ids: the index, a commit, or the working tree with `-uall`.
- There are no checkouts, and every semantic input comes from the snapshot.
- Why: a cache hit reads no file (the demand audit, `docs/research/engine-adversarial-reviews-3.md`), and staged mode reads the right policy (bugs 18 and 19).

**D5. Every run evaluates the whole repo.**
- `--changed` filters the output only.
- There is no incremental evaluation and no locality shortcut.
- Why:
  - A full native run was expected to cost 2 to 4 s.
  - Locality bounds are wrong (adv-correctness F1).
  - C3's tracked dependencies cost more to keep correct than they save (adv-correctness F3; `docs/research/prior-art-second-opinion.md`).
- Later: D59 and D61 (`docs/history/decision-log-3.md`) open the road to exact reuse with traces.

**D6. Pre-commit links single-threaded. CI uses all cores.**
- Why: a single-threaded link takes about 0.5 s. Ten concurrent checks oversubscribe the performance cores of a developer laptop (adv-performance F2).
- Later: revised twice in `docs/history/decision-log-2.md`.

**D7. There is one resolver in the end.**
- Until cutover, the Python resolver stays as the differential oracle.
- After cutover it is deleted. A fixture corpus and golden summaries replace it.
- Why: two resolvers forever is double maintenance (adv-extension).
- Later: amended in `docs/history/decision-log-2.md` (a small independent oracle stays).

## Correctness contract

**D8. Unknown is data.**
- Parse failures, unresolved names, unsupported forms and exhausted analysis budgets are all obligations with a stable identity. That covers the depth limit of the `drives` producer at that time.
- Rules that depend on absence (reach, cycle, coverage, inventory) read complete relations with unknown values.
- Spelled-but-unresolved candidates become unknown edges, wherever the producer's semantics say so.
- Why: bugs 1 to 6, adv-correctness F5 and F6, and prior art (semgrep reports "fixed" for findings in files that failed to parse).

**D9. compare and growth:**
- shape-aware move admission;
- counted obligations with identity, and ambiguous replacements reported as such;
- a failed required scan is exit 2;
- a versioned summary protocol;
- shrink attributed to scanner, policy or environment;
- a pinned base environment.
- Why: bugs 7 to 12. These hold in any engine.

## Hygiene

**D10. One cache root.**
- It is namespaced by build id and bounded by size (2 GB) and age (30 days).
- The first run of the day prunes it.
- Why: the fact cache had already grown to 845 MB in 6 namespaces (skeptic).

**D11. Parity-only machinery leaves the shipped package.** `legacy_ledger`, the facade parity edges and the byte pin to the spike reference move to the shadow job's script. They are deleted at cutover.

**D12. Owners are computed at render time.** Provider set fields are left out of the conflict check.

**D13. drives and migrations get rewritten.**
- drives selects test files by kind mention, uses helper summaries, and fixes the depth-memo bug.
- migrations become per-blob summaries.
- Why: these are the packs that read millions of rows (adv-performance F1).

## The owner's calls (Nov 9)

- Adopting the revised engine after the phase 3 gates.
- Whether a main-branch CI job publishes run summaries.

## Added after the owner's call (Nov 9)

**D14. A consuming repo's main-branch CI can publish run summaries from an existing CI job, with no new workflow (the owner).**

*Where.*
- An existing CI job of the consuming repo, next to the checks Hoglint replaces.
- On a push to the main branch, `hoglint check` writes its summary to the CI provider's cache and to a CI artifact, under the D3 key.

*Readers.*
- A pull request's `compare` in CI restores the merge base's summary from that cache. On a miss, it runs the full base scan.

*Local runs.*
- Order: the machine cache, then an inline compute (the native engine, 2 to 4 s), then a store.
- The architect's call: no local network fetch for now. A fetch costs about the same as computing (an API listing plus a download, and it needs auth). It only pays while the slow Python engine is in use, and the native engine is what ships.
- Revisit if a measured fresh-worktree baseline takes more than about 5 s.

*Rollout.*
- The step must degrade gracefully on branches that predate Hoglint.

*Portability.*
- The summary key must mean the same thing on Linux CI and on macOS.
- The engine id is a digest of the engine sources (crate sources, `Cargo.lock`, Python engine sources), never of a platform binary.
- Paths come from git, never from the file system, so a case-insensitive macOS file system cannot change a result.
- A cross-platform determinism check, Linux CI against macOS with the same key, joins the gates.

**D15. Hoglint gets its own repo (the owner's proposal, Nov 9).** Agreed: switch once the Rust/Python surface and the pack API are settled.

*Why.*
- The generic core and the Django pack become physically separate.
- The consuming monorepo pins a prebuilt abi3 wheel, as it pins other PyO3 packages.
- compare's base engine becomes `uv run --with hoglint==<base pin>`.

*What lives where.*
- The Hoglint repo: all Rust (facts, link, later the rule evaluator, the PyO3 module), the generic core, the Django pack, one abi3 wheel.
- The consuming monorepo:
  - the PostHog preset;
  - the PostHog plugin (drives, facade facts, owners provider) on Hoglint's pack API;
  - the root `hoglint.toml`, the ledger and exceptions files;
  - the parity and shadow tooling.
- The monorepo pins the wheel.

*Steps.*
1. The tool's folder in the monorepo acts as the future repo root, and the PostHog parts move to a sibling package.
2. Extract with history, set up CI and a wheels workflow, publish from tags only, then pin.
3. Afterwards: local iteration through a uv path source.

*The engine id after the split.* The Hoglint version plus the digest of the PostHog plugin and preset. The three-id scheme stays inside Hoglint, for the fact cache and the rebuild check.

Step 2 was done locally on Nov 11; see "D15 step 2" in `docs/history/decision-log-3.md`.

## Phase 3b: make the meaning layer cheap (after the native prototype, Nov 9)

The native prototype passed semantic parity, the invariants and determinism. It missed the timing and memory gates: a full check took about 15 s warm against ≤4 s, and 4 GB RSS against ≤1.5 GB. The Rust side takes about 2 s. Everything else is Python doing per-row work. The split stays as it is; these decisions finish it.

**D16. Sectioned, lazily loaded summaries.**
- Sections: envelope, incompletes, findings and identities, triples and couplings, levels.
- `check` reads only the incompletes section of the baseline.
- The framework digest comes from installed distribution metadata (name, version, a hash of RECORD), never from hashing sources.
- The side-input drift check reuses the snapshot's porcelain status instead of a second git call.
- Target: the summary path costs ≤0.5 s in `check`.

**D17. Packs select their candidates natively; Python sees only candidates.**
- Candidates come by resolved origin, by spelling and by kind mention.
- drives is rewritten: test files prefiltered by kind mention, helper summaries through the resolver, and the depth-memo bug fixed (a counted delta).
- migrations become per-blob summaries, cached by (pack digest, blob oid).
- signals, installs and get-model run on origin and spelling queries.
- Output stays identical in both engines, except named deltas.
- Target: packs ≤1 s warm.

**D18. Findings stay compact until something shows them.**
- Every finding gets an identity, because ledger equality needs all of them.
- Full `Finding` objects, with witnesses and remedies, are built only for findings that print and for `query`.
- reach and cycle run on the native CSR.
- The conflict check's kind matching uses the native ancestry.
- Target: rules plus the conflict check ≤1.5 s.

**D19. One fact database per facts id, instead of 29k small files.**
- SQLite under the cache root: WAL mode, insert-or-ignore, shared by all worktrees.
- Target: fact load ≤0.3 s alone, ≤1 s with ten processes at once.

**D20. Grouping is safe by construction.**
- A rule's group key derives from its parsed selectors automatically.
- A primitive written in Python declares what it reads. A read-audit mode in tests records every field access and fails on any read that was not declared.
- Why: the native agent named "a group key missing a read" as the top review risk. Parity only proves the current rules.
- Later: grouping was deleted in phase N1 (`docs/history/decision-log-2.md`), after three grouping holes.

**D21 (REVISED Nov 9). Re-export cycles: keep the existing semantics, and prove they do not depend on order.**
- Originally this said cycle names become `unresolved(cycle)`. The native agent measured that this would make 340 correct resolutions unresolved: 77 of the 82 cycle names are packages importing their own submodule, and the submodule fallback is Python's semantics.
- The resolver never caches a result that met a cycle, so each top-level answer is already a pure function of the tables. The F3 hazard applies to results persisted across queries (C3), not to one run.
- Decision: keep the `met_cycle` logic. Add a shuffled-query property test in both engines (the 82 names plus samples, many orders and interleavings, equal across engines), plus the F3 p/q fixture as a permanent test.

The original text, for the record:

**D21 (superseded). Re-export cycles resolve the same way in any order.**
- A name whose resolution meets a cycle with no definition is `unresolved(cycle)`, in both engines.
- It is an expected delta, counted against the 82 order-dependent results of that time (adv-correctness F3).
- Why: packs call the Rust resolver in their own order after the link.

**Bug fixes with expected deltas, after parity is frozen.** Each one is its own commit and names its delta:
- 13: Django `uses` copies counted again by public-surface and cycles;
- 14: `frozen` is never written;
- 15: facade-signature judges some slots twice;
- 17: aliased hooks.

**Measurement.** Report CPU time as well as wall time. Run gates on a quiet machine where possible; a timing measured under load says so.
