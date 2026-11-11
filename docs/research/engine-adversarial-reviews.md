# Engine round: adversarial reviews (1 of 3)

> The adversarial reviews of the engine round, Nov 9, 2026, part 1: the method, then the correctness adversary, each finding with what it led to. Part 2 (`engine-adversarial-reviews-2.md`) holds the extension and performance adversaries; part 3 (`engine-adversarial-reviews-3.md`) the skeptic, the demand audit and the bug list.
> Partly replaced: the decisions these findings led to are in `docs/history/decision-log.md` (D1 to D21 and later), and the engine as built is in `docs/design/05-engine.md`.

## Method

The engine round of Nov 9 had two phases (`docs/research/engine-synthesis.md`).

**Phase 1** produced two measured reviews of the first implementation:

- **The skeptic** asked how the engine does its work, not what it must achieve: every mechanism, its need, its cost, and a simpler way. It also answered three open questions of the round.
- **The demand audit** counted, for every rule and output, which edges and facts it reads. It validated the counts by masking the unread edges and checking that every rule's findings stayed equal.

Both are in part 3 (`engine-adversarial-reviews-3.md`).

**Phase 2** sent three adversaries at the three candidate engines (C1 Python cleaned, C2 native link with full relink, C3 persistent queries).
Each adversary had one angle, the code, and the phase 1 reports:

1. **Correctness:** unknown is never clean, determinism, equality to a clean run, compare and growth, the skeptic's base-ledger shortcut, staged snapshots, the hazards of incremental state.
2. **Extension and maintenance:** every fact access of the Django and PostHog packs mapped onto C2's index API; wheel release friction; two resolvers during migration; test seams; the migration path.
3. **Performance claims:** which rules cannot be evaluated by group, Rust link time, C3's validation cost, memory with many concurrent worktrees.

Rules for all reviewers:

- Read-only on the hoglint code (commit `0c197478064`); experiments ran on copies, monkeypatches or fixture repos.
- Every number carries a tag: **(m)** measured in the review, **(c)** counted from git or files or computed from measured numbers, **(e)** estimated.
- Every finding names the claim it attacks, the evidence, the consequence and a fix.
- Findings are ranked by severity.

A second opinion from another model then adjudicated every finding rated critical, high or medium-high, against the code and the scripts (`docs/research/second-opinion-adjudication.md`).
Its verdict is noted below each finding.

File references below point into hoglint's own modules at that commit (Python under `hoglint/`, Rust under `src/`).
Measurements ran on a developer laptop, often measured under load; the performance adversary reports timings as ranges for that reason.

A note on the candidates' labels: "blind-a" and "blind-b" are the two blind architects (`docs/research/engine-blind-design-a.md` and `docs/research/engine-blind-design-b.md`).
"The second opinion" in a claim is the engine-round second opinion (`docs/research/second-opinion-engine-round.md`).

---

## The correctness adversary

What it ran:

- fixture repos `fx1` to `fx4` through the real `hoglint check` and `hoglint compare`;
- the real `Resolver` on synthetic records, to test C3's dependency hazards;
- one graph build of the monorepo, 21 s, to count re-export cycles;
- two full checks in sequence, 43 s and 48 s, 5.3 GB peak, to test determinism;
- git experiments for conflicts, hook indexes, untracked files and interpreter versions.

### Verdicts

**C1, Python cleaned: holds with fixes.**

- The engine of the day was deterministic (m).
- But its semantics turned several unknowns into clean results, and C1 keeps them: unparsed files, `import a.b.c` uses, star-import uses, non-class bases, aliased hooks (F5, F6).
- compare skipped the growth check with exit 0 when the base scanner failed. The extractor binary was one machine-wide file that nobody checked (F9).
- Two of the skeptic's interim steps break equality to a clean run (F16, F17).

**C2, native link with full relink: holds with fixes.**

- Nothing above the facts is cached, so equality to a clean run is easy to argue.
- The selector algebra is Kleene-monotone (c), so skipping a lazy demand field on a decided false is sound.
- It breaks where it narrows the existing behavior:
  - pack queries by resolved origin drop the spelled-but-unresolved candidates that become unknown edges (F5);
  - the group key must carry the surface symbol under `subclass_of` exports (F14);
  - the `ast`-hatch cache key leaves out the interpreter (F13);
  - blind-a's cross-engine contract is too thin for the incomplete split and move admission (F10).
- Both blind designs exit 2 on any incomplete. The agreed design overrides that (an inherited incomplete does not block), and attribution in pre-commit then needs a HEAD baseline that their cost tables leave out (F12).

**C3, persistent queries: breaks as planned.**

- It keeps the Python resolver and wraps it in tracked contexts. That resolver makes reads that no query boundary sees (F3, all m):
  - the existence of other modules;
  - the global set of repo heads;
  - PEP 562 candidates;
  - in-process memos.
- 82 results in the monorepo meet a re-export cycle. Their answer depends on which query entered the cycle first (m).
- Packs read files, git output and site-packages outside any snapshot (F4).
- It holds only if resolution becomes tracked queries, cycle results become volatile, and every input enters the snapshot. That is the rewrite C3 set out to avoid.

**The skeptic's shortcut (base ledger instead of base scan): breaks as a stand-in, holds as a cache.**

- compare needs four things the ledger does not hold (F2): incompletes (1,227 at the time), triples, couplings, and non-ledger findings.
- Its locality fallback, "touched files and by-name dependents", misses edges that change in untouched files (F1, m).
- It assumes that the base passed and ran under the same framework sources.
- Fix: CI publishes a full summary for each master commit, and the shortcut reads that. Without it, compare runs the full base scan.

### F1. Edges of unchanged files change (critical: the shortcut, the skeptic's `--changed`)

**Claim.** The skeptic: "New package triples and introduced incompletes need base edges only for touched files and their by-name dependents." The same scope was proposed for `--changed`.

**Counterexample (m, `fx2`).** Only `posthog/models/base.py` changes:

```python
# posthog/models/base.py, before -> after
class Base:                         class Base(models.Model):
    pass                                class Meta:
                                            abstract = True
# products/p/backend/models.py, untouched
class Mid(Base):
    class Meta:
        abstract = True
# products/r/backend/models.py, untouched, never names Base
class Leaf(Mid):
    thing = models.ForeignKey("q.Thing", on_delete=models.CASCADE)
```

- `products/r/backend/models.py` gains `relation`, `reverse-accessor` and `model-label` edges.
- compare gains three triples: `r q relation`, `r q reverse-accessor`, `r q model-label`.
- That file is neither touched nor a by-name dependent of `Base`.

**Consequence.** A shortcut that reuses head edges for files outside its local set hides new package pairs, new reverse-accessor findings and new incompletes. Kinds flow down ancestry chains and re-export chains of any length.

**Fix.** Never gate on by-name locality. Where it buys speed, close the set over ancestry and re-export chains to a fixpoint.

**Adjudication:** confirmed. A dependency closure must include semantic consumers, not just direct import or name consumers.

**Led to:** D5 (every run evaluates the whole repo; `--changed` filters output only; "locality bounds are wrong"). When reuse came back on Nov 10 and 11, the trace of a stored answer had to include not-found lookups and the module an edge spells (D59, `docs/research/second-opinion-reuse.md`).

### F2. The base ledger is not the base scan (critical: the skeptic's shortcut)

**Claim.** The skeptic: when scanner and policy are unchanged, `git show <base>:hoglint.ledger` replaces the base build.

**Evidence.**

- compare reads from the base scan (c):
  - incompletes, to split introduced from inherited (`compare.py:308-316`);
  - triples (`compare.py:296-300`);
  - couplings and witnesses, for move admission (`ledger/growth.py:107-125`);
  - every non-allowed finding key, for "findings new" and "findings resolved";
  - level sets, for policy evidence.
- The ledger holds only ledgerable findings of ledgered rules, plus level lines (`rules/engine.py:222-233`).
- The monorepo had 1,227 incomplete items (m):

  | Rule | Items |
  |---|---:|
  | own-database | 566 |
  | wiring-watched | 297 |
  | contracts-no-django | 240 |
  | dynamic-imports | 75 |
  | public-surface | 37 |
  | registry-slots | 10 |
  | generated-producer | 2 |

- Without a base scan, `reference()` is None, so compare marks all 1,227 items as introduced and exits 2 on every PR (c). If they default to inherited instead, a new unknown passes.
- "The base passed" is not true of every master commit: merge bases from before the tool was required, commits inside a batched merge-queue run where only the batch tip is tested, and admin merges.
- "Scanner unchanged" must cover the framework sources. The base check ran with the base lock file's Django, and the head scan with the head's. After a Django bump, the shortcut compares across two Django versions.

**Consequence.** Either every PR blocks, or introduced unknowns pass. New pairs disappear as well (F1).

**Fix.** CI publishes the base summary for each master commit, with incomplete counts, triples and couplings, keyed by (commit, engine id, policy hash, framework digest). compare uses it when it exists and runs the full base scan when it does not.

**Adjudication:** partly confirmed. The shortcut is unsound. "All 1,227 become introduced" is not universally true, because `reference()` falls back to `base_head` and incompletes were collapsed by key. These qualifications do not repair the missing information.

**Led to:** D3 (one cached artifact, the run summary, under an exact key with the framework digest), D9 (a versioned summary protocol), D14 (summaries published by master CI under the same key). The shortcut was dropped: only an exact-key summary replaces a scan, never the ledger.

### F3. The resolver makes reads that no query boundary sees (high: C3)

**Claim.** The engine-round second opinion: "Python owns resolution semantics initially", "Put existing producers behind query contexts with coarse dependencies", "Dependencies include unsuccessful reads and collection membership."

**Counterexamples (m, real `Resolver`).**

1. **A new file.** `from app.pkg import X` is unresolved. Adding `app/pkg/X.py` makes it resolve to module `app.pkg.X`, and `table(app.pkg)` does not change. The lookup calls `index.exists(...)` (`core/symbols.py:278, 338`).
2. **A global set.** `import yaml` is external. Adding a top-level `yaml.py` (source root `.`) makes every untouched `import yaml` first-party. `is_external` reads `repo_heads`, a set built from all paths (`core/modules.py`).
3. **PEP 562.** `app.lib.Thing` stays unresolved until `app/things.py` exists (`core/symbols.py:209`).
4. **A memo.** `resolve(app.c, X)` reads tables a, b and c. `resolve(app.d, X)` then reads only table d, because `name_cache` answers (`core/symbols.py:256`). A trace taken at table level records {d}, but the answer depends on a, b and d.
5. **Cycles.**
   - 82 (module, name) results in the monorepo meet a re-export cycle, so the resolver never caches them (m).
   - Fixture: `p` does `from q import X` and `q` does `from p import X`, and both have a submodule `X`.
   - Asked first, `p.X` answers `app.p.X` and `q.X` answers `app.q.X`. Inside the other's lookup, each one answers the other's submodule.
   - So a persisted cycle result that a new query reads changes that query's answer.
6. **Collection reads (c).** These read the set of all modules or all symbols: settings modules by `fnmatch` over every module name; model discovery over every file; `empty_expected_kinds` over every symbol.

**Consequence.** Incremental results differ from a clean run, and nothing reports it. A differential test finds this only on the histories it replays.

**Fix.** Rewrite resolution as tracked queries: every `exists`, head-set, stub, lazy-map and memo read becomes a dependency. A result that met a cycle is volatile, and so is every query above it. Or take C2 and link again on every run.

**Adjudication:** partly confirmed. Confirmed against table-only instrumentation or naive memo wrapping. It does not refute a properly tracked C3: the proposal required negative and collection dependencies. The overclaim is that keeping Python semantics needs a wholesale resolver rewrite; wrapping every input and memo boundary, or invalidating conservatively, can work, at a cost.

**Led to:** D5 (C3 rejected: its tracked dependencies cost more to keep correct than they save). D21 (re-export cycles keep their semantics, with a shuffled-query property test in both engines and this fixture as a permanent test; the hazard applies to results persisted across queries, not to one run). The same carriers (absence, set membership, cycles) became the core of the reuse design later (`docs/research/second-opinion-reuse.md`).

### F4. Packs read outside any snapshot (high: C3, and blind-a's L3 memo)

**Claim.** The second opinion: "Unported producers run with a coarse snapshot-wide dependency", and "The supported pack contract forbids undeclared filesystem ... inputs."

**Evidence (c).**

- The database routing file came from disk (`packs/django/migrations.py:242`).
- The turbo adapter ran `git ls-files`, with `rglob("package.json")` as fallback (`adapters/turbo.py:111-125, 196-202`).
- The layout rule called `exists()` on disk for `files` and `absent` (`rules/entities.py:107, 115`).
- The owners provider read its files directly.
- The Django and DRF sources sit in site-packages, which no git snapshot contains.
- `today` came from the clock (`rules/engine.py:125`).

**Consequence.** A snapshot-wide dependency over (path, blob) does not change when `uv sync` swaps Django or an untracked file appears, so a coarse query gets reused while stale. blind-a's L3 key had an external digest; it still missed git listings and the layout `exists()` checks.

**Fix.** One snapshot API serves every read: blobs, path listings and git listings. Put the framework digest and the date in the input key.

**Adjudication:** confirmed. It affects batch snapshot correctness too, without any persistent query.

**Led to:** D4 (every semantic input comes from the snapshot), D32 (everything a run reads is in a key part, or the run fails loudly; plugins declare their distributions), D33 (a staged run reads every input from the index; providers declare the paths they read), D40 (a working-tree snapshot is the index's blobs plus what `git status` reports; "does this path exist" is answered from that list, never from the file system).

### F5. Pack queries by origin drop candidates that the row walk keeps (high: C2)

**Claim.** blind-b: packs "all filter by resolved origin, not by spelling". blind-a: packs query "calls whose callee resolves to X".

**Counterexample (m, `fx4`).**

```python
# products/beta/backend/via_param.py
def setup(hooks):
    def on_widget(): ...
    hooks.register_widget_hook(on_widget)
```

- The installs producer matched the last name, then emitted an UNRESOLVED edge (`packs/django/installs.py:32`). That gives an `unresolved-installs` finding.
- Queried by origin, the callee has no origin. So there is no record, no edge and no unknown.

**`get_model` is the larger case.**

- The producer matches any callee that ends in `.get_model` (`packs/django/uses.py:156`).
- 617 such call sites are in migrations, where the receiver is the `apps` parameter (m).
- 56 more, outside migrations, have an attribute or parameter as receiver (m).
- Their `model-label` edges feed the `packages` view, and `no-new-package-cycles` excludes only `test` and `type_only`.

**The reverse gap, where C2 does better (m).** `from ...hooks import register_widget_hook as add_hook; add_hook(fn)` made no edge, because the spelled last name is `add_hook`. C1 should match the origin as well as the last name.

**Fix.** An index query returns origin matches plus spelled-name matches whose origin is unresolved or local. The second group becomes unknown edges.

**Adjudication:** confirmed, with a limit: not every spelling-only candidate becomes an unknown edge. `get_model` can resolve a model label despite an unresolved receiver, so the handling stays producer-specific.

**Led to:** D1 (candidate indexes by resolved origin and by spelling), D8 (spelled-but-unresolved candidates become unknown edges wherever the producer's semantics say so), D17 (packs select candidates natively by resolved origin, by spelling and by kind mention). The aliased hook (the reverse gap) became bug 17, fixed with a named delta after parity was frozen.

### F6. Unknowns turned into clean results (high: C1 and C3 inherit; C2 must not copy)

**Claim.** The synthesis: "Unknown is a stored value." The producer code: "A file that did not parse is an unresolved fact, never an absence."

**Counterexamples (m, `fx1`, full `hoglint check`).** Each case printed `incomplete (0)`.

1. **Syntax error.** A product file has a syntax error and a `Widget.objects.filter()` use. Result: no finding and no incomplete. The `unparsed` edge exists, but no rule reads that kind, and the Django pack skipped any file that did not parse (`packs/django/pack.py:73`).
2. **Dotted import.** `import products.alpha.backend.models`, then `products.alpha.backend.models.Widget.objects.filter(...)`. Result: no finding. The extractor emitted a `refs` row only for a first-party import whose imported name or first attribute starts upper case (`src/walker.rs:785`).
3. **Star import.** `from products.alpha.backend.models import *`, then `Widget.objects.filter(...)` inside a function. Result: no finding. A star-bound name was missing from `table.names`, so the lookup fell back to `local=True` and NOT_APPLICABLE (`core/sites.py:204-214`, `core/producers.py:154-155`).
4. **Variable base.** `DynamicBase = make_base()`, `class Gadget(DynamicBase)`, and the facade has `def get_gadget() -> Gadget`. Result: no facade-signature finding. Only an unresolved base marked ancestry as incomplete (`index.py:120`); a base that resolved to a variable was dropped (`core/symbols.py:421`), and the walk then read as complete and false.

**Consequence.** Rules reported clean where they could not see. Cases 2 and 3 are exactly the uses that model-crossings exists to judge.

**Fix.**

- A module that did not parse makes every rule incomplete whose scope can include it.
- Emit refs for every import-bound first-party name.
- Resolve star-bound names through the source table, and return unknown when the source has no facts.
- A base that resolves to anything but a class makes ancestry `maybe`.
- For C2: blind-b already makes unparsed files incomplete; blind-a's `maybe` must also cover bases that are not classes; blind-b says nothing about ancestry certainty.

**Adjudication:** confirmed, with a correction of mechanism. "No finding" means the relevant obligation disappears, not that the whole repo exits clean. Case 2 has a fallback producer, but it emits no shape, and the preset's `edge.shape != none` excludes it: a known absence used where uncertainty is required. Case 3 also needs an extractor fix, because extraction drops names absent from its binding map.

**Led to:** D8 (unknown is data: parse failures, unresolved names, unsupported forms and exhausted budgets are obligations with a stable identity; rules that depend on absence read complete relations with unknown values). D1's ancestry "with certainty (yes, no, maybe)". These are bugs 1 to 4, which block cutover.

### F7. Move admission ignores the shape (high: compare, common to all)

**Claim.** The user guide and the growth module's docstring: "new uses bundled into a move fail."

**Counterexample (m, `fx3`, `hoglint compare --base HEAD~1`).**

- Setup: `git mv old_name.py new_name.py`, which also turns `def describe(widget: Widget)` (an annotation) into `list(Widget.objects.filter(name=name))`.
- Output: `admitted moves (review) (1)`: `model-crossings ...Widget instance-many(filter): 1 -> 2`; `code growth (fails) (0)`; exit 0.
- A coupling was (src, dst, kind) with no shape (`ledger/scan.py:18`), so the old annotation coupling admitted the new filter use.

**Consequence.** New debt passes as a move. The reviewer sees "admitted move" and reads it as "same couplings".

**Fix.** The coupling key includes the label column of the growth key.

**Adjudication:** confirmed. Include the rule-relevant semantic dimensions, not only a universal `label` field.

**Led to:** D9 (shape-aware move admission). Bug 7.

### F8. Incomplete keys are sets, not counts (high: compare, common to all)

**Claim.** The agreed design: "Incomplete results belong to whoever caused them and block only that PR."

**Measurement (m).**

- 1,227 incomplete items fell into 1,035 (rule, key) keys; 87 keys held more than one item.
- A dynamic-imports key is `module|dynamic-import|`, with an empty surface.
- compare classified an item by whether its key exists in the base (`compare.py:310`).

**Counterexample.** A module that already has one computed `importlib.import_module(name)` gains a second one. The key is the same, so compare calls it inherited and exits 0.

**Fix.** Count items per key. An item is introduced when the head count is above the base count.

**Adjudication:** confirmed. The information was lost before compare: the summary overwrote entries sharing (rule, key), so a counter in compare alone cannot recover it. Counting still cannot see a replacement (remove A, add B under the same key); define stable obligation identity or report ambiguous replacements.

**Led to:** D9 (counted obligations with identity, and ambiguous replacements reported as such). Bug 8.

### F9. A failed base scanner skipped the growth check with exit 0 (high: C1)

**Evidence (c).**

- `scan_external` returned None on any non-zero exit (`cli.py:422-424`).
- compare then printed "growth check skipped: the base commit has no hoglint scanner" (`compare.py:304`), and exited 0 when the head passed.
- The subprocess ran the base Python sources inside the head virtual environment.
- `find_binary()` took one machine-wide extractor build (`facts.py:17`), and the Python side never checked its `--build-id`. Every worktree's build overwrote that one file.

**Consequence.** The growth check was off exactly when the scanner changed. The result of any run depended on which worktree built last.

**Fix.** An engine that cannot run means exit 2. Pin the extractor per environment, as a wheel, or refuse a build id that does not match the in-tree sources.

**Adjudication:** confirmed. Distinguish "the base predates the scanner" from "a required base execution failed", and pin the whole execution environment.

**Led to:** D9 (a failed required scan is exit 2; a pinned base environment), D2 and D15 (the engine ships as a prebuilt wheel; compare's base engine runs from the pinned base version). Bugs 9 and 10.

### F10. "Growth sums only" is too thin a contract between engines (medium-high: C2, blind-a)

**Claim.** blind-a: the base engine "returns scan-result v1 growth sums, the only cross-version contract."

**Evidence (c).** Besides the sums, compare needs: base incompletes (F8); base finding keys; base couplings, and witnesses for head code under the base engine, for admission; level sets, for policy evidence. The subprocess of the day returned the full summary (125 MB per the skeptic).

**Consequence.** With two engines, neither the introduced/inherited split nor move admission can run. Every move fails, and either every incomplete counts as introduced or none does.

**Fix.** Version the full summary, with counts. Cut couplings down to the witnesses of grown keys.

**Adjudication:** confirmed. It need not serialize the whole graph.

**Led to:** D9 (a versioned summary protocol) and D16 (sectioned summaries).

### F11. Nothing labels debt that a scanner or environment change removes (medium-high: all)

**Evidence (c).**

- Rule growth listed only `after > before` (`ledger/growth.py:134`).
- The policy classifier labeled only policy entries that changed (`compare.py:278-294`).
- `policy_hash` covered only the merged TOML (`config/schema.py:890-891`), not the classification and routing data files or the Django version.
- Removing a ledger line is "always fine".

**Consequence.** C2's own cutover, every later wheel bump, a Django upgrade and an edit to a classification file can drop findings while compare says nothing about why. The agreed design relies on compare to label "debt removed by policy" for the reviewer.

**Fix.** List shrink per growth key between (head code, base engine) and (head code, head engine), and attribute it to scanner, environment or policy. Put the classification and routing files in the policy hash and the framework digest in the engine id.

**Adjudication:** partly confirmed. Shrink is visible through resolved findings and removed ledger lines, so "nothing labels it" is too broad. Its cause is not attributed.

**Led to:** D9 (shrink attributed to scanner, policy or environment), D3 (the framework digest in the summary key). Bug 11.

### F12. `check` cannot tell introduced from inherited (medium: C2, C3)

**Evidence (c).**

- Every rule-level `IncompleteItem` defaulted to INHERITED (`rules/base.py:58`). Only producer failures were EXTRACTOR.
- So `check` and pre-commit never exited 2 for a new unknown. Only compare could.
- The blind designs exit 2 on any incomplete; with 1,227 inherited items that would block every commit.

**Consequence.** The pre-commit estimates of 1.3 to 3 s hold only if pre-commit ignores new unknowns. Attribution needs the HEAD scan as well: a second link and rule pass, or a memoized HEAD result.

**Fix.** Pre-commit compares against a memoized summary of HEAD or the merge base. When no summary exists, it reports incompletes as advisory and says so.

**Led to:** D3 (the pre-commit and `check` baseline is the stored summary of HEAD; a staged run stores its summary under the staged tree so the next commit has its baseline). The cost of a baseline miss came back in the review after phase 3d (D33 to D35, `docs/research/second-opinion-3d.md`); D39 settled it: a missing baseline is a second run, which a fast engine can afford. Bug 12.

### F13. The `ast`-hatch cache key leaves out the interpreter (medium: C2)

**Claim.** blind-a's L2 key: "(pack source digest, blob oid)". blind-b: "blob id + pass name + digest of the pack's source files".

**Counterexample (m).** A blob containing `except ValueError, TypeError:`:

| Interpreter | Result |
|---|---|
| 3.13.13 | SyntaxError |
| 3.14.7 | parses; `ast.dump` digest `dd141c1f8692` |
| 3.15.0rc2 | parses; `ast.dump` digest `585a52b7a9b7` |

Checkouts on one machine can run different Python patch versions, and all of them share the store.

**Consequence.** A cache hit returns another interpreter's result, which differs from a clean run.

**Fix.** Add the interpreter version to the key, and the digest of every module the pass imports, including helpers outside the pack directory. blind-a's L1 key also needs the parser target version.

**Led to:** no `ast` hatch shipped. The escape hatch of D22 reads one file's cached facts record, whose key is (extractor build id, blob oid) (D4). Per-blob pack summaries are keyed by (pack digest, blob oid) (D17).

### F14. A group key by module class merges exported and unexported symbols (medium, latent: C2)

**Claim.** blind-a: "`dst.exported` is a pair field: the surface's export set checked against the audience predicate on src labels". blind-b memoizes it "per (export, src class)".

**Evidence (c).** With `subclass_of`, export membership is per symbol and three-valued (`labels.py:481-500`).

**Counterexample.** A core module imports two names from one product presentation module: an adapter class, exported to core because it subclasses the adapter base, and a helper that is not. One group, keyed (core class, presentation class, import, prod), gets one `dst.exported` answer for both names. No such import existed in the monorepo at the time (all 80 names imported from those modules were adapters), and the existing cache keyed on the symbol, so the defect was latent.

**Fix.** Compile the set of fields each predicate reads. Make the group key and the memo key the projection over exactly those fields. Add the surface symbol whenever a `subclass_of` export covers the module.

**Led to:** D20 (a rule's group key derives from its parsed selectors; a read audit fails on any undeclared read), then D27 (a group shares only its verdict). Grouping holes kept turning up by reading the code (`docs/research/second-opinion-3d.md`), and D39 replaced grouping with per-edge evaluation in Rust.

### F15. Staged and worktree snapshots (medium: C1; guards for C2 and C3)

**Evidence (m).**

- **Conflict entries.** During a conflicted merge, the staged listing returned nothing for the conflicted `.py` file, and `checkout-index --all` wrote neither that file nor the conflicted ledger. Discovery skipped every entry at a stage other than 0.
- **Pathspec commits.** `git commit -- a.py` while `b.py` is staged: the hook's `GIT_INDEX_FILE` index holds the new `a.py` and the old `b.py`; `.git/index` holds the reverse. Only the git CLI, run with the hook's environment, reads the right snapshot.
- **Untracked files.** `git status --porcelain=v2` prints an untracked directory as one line; `-uall` lists the files. `status.showUntrackedFiles=no` hides them completely.
- **Symlinks.** For mode 120000 the blob is the link text, not the file.

**Fix.** A conflict entry becomes an incomplete with its path. Build the snapshot only through the git CLI with the hook environment. Pass `--untracked-files=all -z`. Treat a 120000 `.py` entry as a gap, or follow it to the target blob.

**Led to:** D4 (snapshots from git, never from checkouts), D33 (one captured tree per staged run), D40 (the working-tree snapshot), D41 (a symlink inside the tree is read as its target). An index with conflicts exits 2 with one line (phase 4a). Bug 19.

### F16. Leaving watch lists out of the cache namespace serves stale facts (medium: C1 interim)

**Claim.** The skeptic: "Leave watch lists out of the cache namespace."

**Evidence (c).** `calls` rows existed only for watched callees, and `watched` rows only for watched keys (`src/walker.rs`). After a watch list changes, a cache hit returns old rows that lack the new calls.

**Fix.** First extract every call, as C2's catalog does. Only then drop the lists from the key.

**Led to:** recorded under "not to do" in the bug list. D4 keys facts by (extractor build id, blob oid); the extraction configuration must be part of that build id or the extraction must not depend on it.

### F17. Demand-only edges change compare and explain (medium: C1 interim)

**Claim.** The skeptic: "produce only the kinds rules read"; the demand audit's lower bound.

**Evidence (c, plus the audit's measurement).** Move admission counted couplings over all edges (`ledger/scan.py:63-66`). `explain edge:<id>` used ordinals over all edges with the same module, scope, kind and surface (`index.py:160-169`). 4 of the 162 unselected unresolved edges shared a coupling key with a witness.

**Fix.** Compute couplings from the facts, not from the pruned edge list. Derive run ids from (path, line, column, kind).

**Led to:** recorded under "not to do". D5: no demand pruning; the whole repo is evaluated.

### F18. Growth keys by table name merge a proxy with its model (low: blind-a and the user guide)

**Claim.** blind-a: "Growth keys use stable target ids: the model's table name ...".

**Evidence (m).** A proxy model shares its concrete model's table. Moving one filter use from the concrete model in product X to the proxy in product Y nets zero.

**Fix.** Key by the concrete model label plus the proxy class.

**Led to:** no decision entry. Growth keys stayed on symbols (`dst.symbol` plus a label), not table names.

### F19. blind-a's `__all__` rule is a semantic change (low: C2 parity)

**Evidence (c).** A star import resolved any name of the source table, private names and names outside `__all__` included (`core/symbols.py:334-337`). blind-a: "a literal `__all__` wins."

**Consequence.** The parity invariant "Rust equals reference" fails on purpose.

**Fix.** Change the reference first, in its own commit, with a ledger diff.

**Led to:** the reference semantics stayed through the port. The reuse spike of Nov 10 found again that the engine does not read `__all__` for star imports (`docs/research/spike-n7.md`).

### What held (tested)

**Determinism of the engine of the day.**

- Two full checks with PYTHONHASHSEED 1 and 2 gave identical results in identical order (m, 43 s and 48 s wall, 5.3 GB peak): 1,577,327 edges, 54,575 findings, 1,227 incomplete items, the same levels.
- The Rust extractor keeps input order: `par_iter().map().collect()`.
- C2 must keep it: no Rust `HashMap` iteration (it uses RandomState) in any output, id or "first witness" path, and witnesses chosen by minimum.
- C3 must also merge partition results in sorted order, or witnesses depend on cache history.

**Lazy demand fields in C2.** Every operator returns unknown on an unknown operand, and `and`, `or` and `not` are Kleene (`selectors.py:458-536`). So "skip on a decided false" is sound for selectors. The primitive-level lists (`allow_shapes`, `deny_shapes`, `unless`) must join the same three-valued evaluation.

**Python 3.14 syntax.** Ruff's parser reads PEP 758 `except A, B:` (m). New 3.14 syntax does not turn files into unparsed ones.

### What none of the candidates addressed

- **Attribution outside compare:** how pre-commit and the agent check decide "introduced" (F12).
- **Counting incompletes per key** (F8).
- **Labeling removed debt:** by the scanner, the environment, or policy data files (F11).
- **Silent partial reads in packs.** `SettingsValues.literal` skipped list items that are not literals; model registration dropped a model with no app label; only "zero models" was checked, so a partial result looked complete. Latent, because the settings list was a literal list.
- **Pack scope cut without a gap.** The Django pack read only files under the top-level packages of first-party INSTALLED_APPS. A runtime module outside those packages that uses models is never judged, and nothing reports that.
- **Completeness of the candidate set.** Accounting (blind-b) proves that each candidate is handled, not that the candidate set is complete. F5 shows misses in both directions.
- **Transient versus persistent failures.** In C3, a cached incomplete from a crash or an OOM stays until its inputs change. Bazel's Skyframe keeps the two apart.
- **A gate for equivalence.** Before any shortcut, incremental path or the C2 relink gates anything: replay the 200-commit history against the cold full path, and compare per commit the findings, counts, incomplete counts, triples and admitted moves.

The silent partial reads and the scope cut became bugs 5 and 6 (D8). The replay gate came back as the replay check, stage 0 of the reuse plan (D59).
