# Spike N7: how far does one commit's change spread?

> A measurement of 33 real commits and 11 mutations of the PostHog monorepo, Nov 10, 2026 (parts 2 to 4 written that night).
> Current as of Nov 11, 2026, as a measurement. The design built on it (stored answers with traces, the fast hook) is in `docs/design/08-fast-hook.md`; the staged plan is `docs/research/incremental-plan.md`.

**The question.**
One check rebuilt everything between the cached per-file facts and the result (link, packs, rules) for all 29,000 files: about 3.2 of 3.75 s.
The question (Nov 10): should Hoglint reuse computed results between checks?
That works only if a real commit's change stays near the changed files and their importers, and fails if changes appear in files with no import path to them, or if most producers must see the whole repo.
So: when a real commit changes a few files, how far does the change spread in the graph and in the findings?

Scratch only; nothing committed.
The engine was a fixed export of the verified commit of the time, native, run in process.

**Short answer:** in 32 of 33 commits, every edge, finding, incomplete item and label that moved was in a file the commit changed.
In the one exception, a deleted package moved the origin of a re-exported name.
That change reached importers one and two hops away, always along an import path.
Nothing moved in a file with no import path to a changed file.
Reach, cycle and coverage results never moved.

**Sample.**
The snapshot (a snapshot tree) is master `dcf2ddf1a31` (Nov 9) plus the Hoglint tool, config and preset.
Outside the tool and `.github`, its Python files are byte-equal to master from `8fb43f03542` to `7f4eb25af4c`.
The spike took the 30 consecutive first-parent commits that touch `.py` files, ending at `8fb43f03542` (58 first-parent commits, Nov 8-9).
None touches more than 50 Python files, so none was skipped.
The 30 cover a product's Django models file (308 importers), three product facades, a product's re-exporting `__init__.py`, and a new product (`56d9611`, 46 files).
They contain no module that thousands of files import, so three chosen commits were added:
`a3f2a2e7153` (`posthog/models/team/team.py`, 2,725 importers),
`8da9a9e4a93` (deletes `posthog/models/filters` and `entity`, edits `team.py` and `property.py`), and
`892818a81f5` (a product facade's `types.py`, 2,529 importers).

**Trees.**
One scratch clone with the snapshot's objects as an alternate.
Each tree is the snapshot tree with `git diff-tree dcf2ddf1a31 <commit>` laid over it through a temporary index (`update-index --index-info`, `write-tree`).
Paths where the snapshot already differs from master (hoglint, its config, `.github`, 593 paths) keep the snapshot's version, so every tree is judged by the same config and preset.
None of the commits' own Python paths is among them.
This gave 42 distinct trees for the 30 commits and 6 for the chosen ones.
Each tree ran once, in its own process: `Runner.run(policy_of(tree), clone, staged=True, tree=tree)`, which is what `check --index` runs after `write-tree`, so no checkout was needed.
**Check against a real checkout:** the snapshot tree run by blob id and the snapshot's working tree gave equal results in all 12 kept sections (edges, imports, symbols, findings, incomplete items, levels, labels, modules, failed producers).
Kept per tree:
a digest of each (site file, producer) pair with no order (the sum of row hashes, rows with line numbers);
the import relation (core `import` edges, targets and `via`, mapped to files);
symbol facts per file;
findings by (rule, identity, status) with their witness and subject files;
incomplete items;
levels;
and a digest per module of `labeler.explain` (provider fields included).

**Classes.**
A difference is classed by its file:
`changed` (the commit changes it, including added and deleted files);
`imp1`/`imp2`/`imp3+` (the file imports a changed file through that many hops);
`dep` (a changed file imports it);
`none` (no import path either way).
A finding is classed by the nearest of its witness and subject files.
The import relation is the union of both trees.

| commit | py files | files whose edges changed | producers past changed files | findings changed | incomplete | module labels | whole-graph moved |
|---|---|---|---|---|---|---|---|
| 8fb43f03542 | 21 | changed:20 | none | changed:1 | - | changed:4 | module table |
| b0e35bc125d | 2 | changed:2 | none | - | - | - | - |
| df3f61e6d94 | 12 | changed:12 | none | changed:2 | - | changed:1 | module table |
| 5666872eba5 | 4 | changed:4 | none | changed:9 | - | - | - |
| f18803ea84d / eafe1d88a07 / f649cccfff0 | 2 / 2 / 1 | changed:2 / 1 / 1 | none | - | - | - | - |
| bb302ce2f66 / f550ded7c51 / 2fe80584306 | 4 / 2 / 2 | changed:3 / 2 / 1 | none | - | - | - | - |
| 57a42e256c4 | 1 | - | none | - | - | - | - |
| 89aea3c5146 | 8 | changed:7 | none | changed:2 | - | - | - |
| e432c839d63 / bcbcf708f21 | 2 / 5 | changed:2 / 4 | none | - | - | - | - |
| 6bbcb1c08b5 | 5 | changed:5 | none | changed:5 | - | - | - |
| e6cf6b17f65 / 33543fc479a / a2c8a13369a | 4 / 6 / 2 | changed:3 / 6 / 2 | none | - | - | - | - |
| 502b9454d5e / 49a908507a1 / 8e7f1a66dd8 | 5 / 2 / 1 | changed:4 / 2 / 1 | none | - | - | - | - |
| b0c712e5924 / 5ac3180fec7 | 6 / 5 | changed:5 / 4 | none | - | - | - | - |
| 56d9611d691 | 46 | changed:38 | none | changed:55 | changed:1 | changed:32 | levels, module table |
| 3f112de3b3d / 55c1c4dad50 | 2 / 1 | changed:2 / 1 | none | - | - | - | - |
| 34ded768266 | 6 | changed:6 | none | changed:8 | - | - | - |
| 1418617f33f / 9954265aff7 | 5 / 3 | changed:5 / 2 | none | - | - | - | - |
| b32e1a7b0c1 | 5 | changed:5 | none | changed:1 | - | - | - |
| *a3f2a2e7153* (chosen) | 7 | changed:7 | none | - | - | - | - |
| *8da9a9e4a93* (chosen) | 34 | changed:30 imp1:5 imp2:1 | core, posthog.facade-facts | changed:9 | - | changed:13 | levels, module table |
| *892818a81f5* (chosen) | 9 | changed:7 | none | changed:2 | - | changed:3 | module table |

**Totals.**
- 30-commit sample: 172 Python files changed. Edges changed in 152 files, all in the changed class. 83 finding keys, 1 incomplete item and 37 module labels changed, all in the changed class. 30 of 30 commits stayed inside the changed files.
- With the 3 chosen commits (33 commits, 222 Python files): edges changed in 196 changed files, 5 at `imp1` and 1 at `imp2`. Every finding, incomplete item and label difference was in a changed file.
- Commits where every difference stayed within the changed files and their direct importers: **32 of 33** (30 of 30 in the sample). Commits where every difference stayed within the changed files: also 32 of 33.
- Differences in a file with no import path to a changed file (`dep` or `none`): **0**.

**Spread past the changed files: one carrier, the re-export origin.**
In `8da9a9e4a93`, `GroupTypeIndex` moved from `posthog.models.filters.utils` (deleted) to `posthog.constants`.
`posthog.models.property` re-exports it.
Edges store the origin after the re-export chain, so every edge that names `posthog.models.property.GroupTypeIndex` changed its target.
Example: a product's facade `api.py` (`core-import`, `core-reference`, `core-exposes` and `posthog.facade-facts` edges), at `imp1`.
The `imp2` case is a module in another product's facade.
Only its `posthog.facade-facts` rows changed, and they carry the origin of a signature type through a re-exported function.
In both cases the spread followed the re-export chain, which is an import path; its depth is the length of the chain.
None of the other carriers the brief named appeared (a name looked up without an import, a Django label in a string, a class hierarchy, a config glob).
That is because none was in the sample; it does not show they cannot happen.

**Whole-graph results.**
- Reach, cycle and coverage findings never moved.
- Levels moved in 2 of 33 commits (`56d9611d691`, `8da9a9e4a93`). In both, the passing set stayed the same; only the block lists of the level groups that own the changed findings moved (including the new product's group). In `8da9a9e4a93`, the block lists of three products changed; why one of them changed was not traced. The stored block text may include witness lines.
- The module table moved in 5 of 33 commits, each time because files were added or deleted.
- No producer failed in any tree.

**The floor.**
`check --index` with the stored summary of the same staged tree already in the cache, 4 runs, all equal within 0.01 s, measured under load.
Wall/CPU in seconds:

| start + imports | write-tree | tree listing | policy files | policy digest | input digests | store read | output | other | total in process | process wall |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.05/0.05 | 0.05/0.00 | 0.10/0.04 | 0.06/0.03 | 0.02/0.02 | 0.00/0.00 | 0.10/0.10 | 0.08/0.07 | 0.06/0.05 | 0.52/0.36 | 0.55 |

- Input digests cost 0.016 s on the first run and 0 after that, because the input digest is memoized on disk.
- The interpreter starts in 0.01 s.
- The first run, which missed the memo, did the live run: 4.9 s wall, 3.7 s CPU.
- Steps that nest are timed by their own time only.

**What this cannot tell, and what surprised the spike.**
- The spike measures where outputs move, not what inputs a step must read to know that they did not move. A rule such as `no-new-package-cycles` or `public-surface` gave the same answer, but a memoized step still has to prove that from local inputs. This spike does not show which producers or rules can do that.
- The sample is 30 consecutive commits from one day, mostly leaf edits: product code, tests, views. Only 3 commits renamed or deleted names. One of those 3 gave the only spread. A refactor that moves a widely re-exported name would spread to every importer along the chain.
- Commits were put into classes, not weighed by cost. For example, how many edges an `imp1` file would recompute was not measured.
- The import relation covers only static `import` edges. A spread through `dynamic-import`, string labels or config globs would have shown as `none`. None appeared, but the sample would show one only by chance.
- Differences in edge order are not counted; the digests have no order. The digests include line numbers.
- Surprise: `57a42e256c4` changed a Python file and no edge moved, which is consistent with an edit to text only. Several commits changed one file more than the number of files whose edges changed (test or settings files with no edge change).
- Surprise: no change to `team.py`, the facade `types.py` or the product models file (each with hundreds to thousands of importers) moved a single importer's edge. The importers name targets by entity, so an edit that does not move an origin does not reach them.

## Part 2: can a file's stored answer prove "unchanged" from what it can see?

Same 33 commits and trees, same engine export.
Each tree was traced in one process, and each pair compared.

**What was taken.**
- **interface v1(F):** a digest of the record keys `defs` (all, nested ones included), module-level `imports` rows, module-level `assigns` (this covers `__all__`) and `tags`, with the line, column and end fields removed (`l`, `c`, `e`, and the import row's line and column). The digest also covers F's symbol facts (`graph.symbols`, packs included) and F's labels (the `labeler.explain` digest). A file that is added or deleted counts as changed.
- **interface v2:** the same data, one digest per defined name. Nested defs fall under their top-level name. A star import is read as `(M, "*")`, which changes when any name of M changes. Labels count as the name `<labels>`, and a module's existence as `<module>`.
- **trace(F), narrow:** the files, or `(file, name)` pairs for v2, of every target and every `via` step of every edge sited in F. Inherit edges add base classes, transitively. An unresolved or candidates edge adds the file of the longest module prefix of its surface, as the name that was looked up and not found.
- **trace(F), wide:** the narrow trace plus the file of the module the edge spells (its surface). Part 3 shows why this is needed.
- **Rule rows:** a finding or incomplete item counts at its site file. Its prediction also reads the labels of the site and the interface of its witnesses' target files.
- **Ground truth:** files whose per-producer edge digest, symbol facts or labels differ, plus the site files of findings and incomplete items whose count differs.

**Soundness.**
- No edge, symbol or label was missed, with either the narrow or the wide trace and either interface version.
- **One rule-row miss**, in `56d9611d691` (a new product): an `export-shape` row whose identity names `typing.Any` and the new product's contracts module (`export-unknown`). It is sited in `posthog/dataclasses.py`, which did not change: the `@frozen` decorator returns `Any`. The row is about the new contracts module, but its site is the definition the unknown type comes from. **Widening that fixes it:** store and invalidate a rule row under the files its identity names as well as its site. With that rule, 0 misses remain in the 33 commits.
- **Rules folded over the whole graph or to packages** are reach, cycle, coverage, and edge rules with `view = "packages"` (`no-new-package-cycles`, `foundation-direction`, `contracts-no-django`, `health-checks-no-dags`, `wiring-watched`). Their rows sit at a representative witness, which can be any file. So they cannot be held by file; part 3 shows this. They are judged by their relation digest instead (see below).

**Cost per commit: predicted-invalid files and their share of all 1.48 M edges, against the ground truth.**

| | median files | median edge share | worst commit |
|---|---|---|---|
| ground truth | 4 | 0.023% | 47 files (`56d9611d691`, new product); 1.05% of edges (`df3f61e6d94`) |
| v1 (file grain, wide trace) | 7 | 0.045% | 3,686 files, 36% of edges (`8da9a9e4a93`); 2,541 files, 12.5% (`892818a81f5`) |
| v2 (name grain, wide trace) | 6 | 0.034% | 361 files, 5.1% (`df3f61e6d94`) |

- v1 recomputes thousands of files exactly when a hub's interface moves. `8da9a9e4a93` edits `team.py`; `892818a81f5` adds a field to a facade `types.py`. In both, almost no importer changed (truth: 39 and 7 files).
- v2 brings both down to 77 and 12 files.
- v2's worst case is a new field on a product's model class (`df3f61e6d94`). The digest of the class name changes, so every file that names the class is predicted; 12 really changed. A finer grain (per class member, or a digest that leaves out members other files cannot see) is the next step if that matters.
- The narrow trace (without the spelled module) gave the same soundness on these commits, and costs 3 % to 10 % fewer files.

**Pack producers: reads cannot be tied to an item.**
- Each pack step builds its whole output and emits it in one batch when the step ends (`ProducerPlan.run_step`).
- Later steps read the state earlier steps built from the whole repo: the app registry, the model registry, the migration graph.
- `django.uses` is judged inside the Rust link: one call, opaque to Python.
- So no item-level trace can be observed yet. That needs producer code that records reads per item.

What could be measured is per step, with every read at depth 0 logged: about 120k distinct reads per tree, and 99 % of them name a file.
- Over the 33 commits, 292 reads changed their answer. **Every one of them named a changed file or a file whose interface v1 changed (0 unexplained).** So the interface is wide enough for what the packs read.
- At step grain, a step would rerun whole: `posthog.drives` in 20 of 33 commits, `posthog.facade-facts` 11, `django.models` 4 (and the Django steps after it), `django.signals` 3, `django.installs` 2.
- In 33 step-runs, a step asked questions in only one of the two trees. Its control flow followed a changed answer. That is mostly `django.installs`, which narrows its candidate files by `may_mention`. An item trace would have to record the questions asked, not only the answers.

**Whole-graph rules.**
- reach (`contracts-no-django`, `health-checks-no-dags`) reads the file-import relation without test and type-only edges: 149,869 (file, module) rows.
- cycle and the package-view edge rules read 206,245 (module, module, kind) rows of import, reference, dotted-path, model-label and relation edges, without tests, folded to package pairs.
- coverage (`wiring-watched`) reads 8,745 drives, signal, installs and test dotted-path rows, plus the turbo inputs file.
- levels read the rule rows of each package.
- The module-grain relation digests moved in 17 of 33 commits (coverage in 5); the levels digest in 2. Aggregate rule rows moved in 0 of 33.
- So the relation digest is sound (it moved every time the rows moved, in part 3 too) but too fine: these rules would rerun in about half of the commits.
- A package-grain digest would move less often. It was not measured: the package mapping through `labeler.builtin("package")` gave only 4 rows, so that result is wrong and is not reported.

**Sizes of one tree:**
- Interface record data, normalized JSON: 87 MB over 29,038 files. As digests: 29k for v1, 524,622 name digests for v2.
- Trace entries: v1 151k (file, file) pairs with the spelled modules (133k narrow); v2 445k (file, name) pairs (382k narrow). 22-25k files have a trace.
- Not measured: the time to build the interface digests. A check must parse every changed record and read every stored digest.

## Part 3: targeted mutations of the snapshot tree

The parent is the snapshot tree, except for mutation 1.
Each child edits one or two real files.
All results below use the wide trace and the rule-row rule from part 2 ("identity files too", aggregate rules by relation).

| mutation | really changed | predicted v1 / v2 | miss | carrier |
|---|---|---|---|---|
| 1. An import of a missing name becomes found. The parent adds `from posthog.dataclasses import n7_pending` to a product's models file; the child defines it. | 1 file (+1 rule row) | 2,082 / 2 | none | the unresolved edge's surface names `(dataclasses.py, n7_pending)` |
| 2. A model re-export (`HealthIssue`) deleted from `posthog/models/__init__.py` | 3 | 2,098 / 3 | **narrow trace: 2 files + 1 rule row** | the spelled module (`posthog.models`) is in neither targets nor `via`; the wide trace fixes it |
| 3. `__all__` added to a module in a product package: one new name, one dropped, used through the package's `__init__` star import | 0 | 3 / 2 | none | nothing changed: the engine does not read `__all__` for star imports (an importer still resolves the dropped name) |
| 4a. A plain class in a product's models file gets `models.Model` | 4 | 19 / 4 | none | inherit plus symbol facts |
| 4b. A model in another product's models file loses `models.Model` | 6 | 7 / 6 | none | same |
| 5. A product model renamed (string labels and migrations name it) | 14 | 17 / 14 | none (per file) | string labels resolve through the model file, which changed. Cycle rows moved in 2 packages, and the relation digest moved too |
| 6. A body starts calling `apps.get_model("posthog", "Team")` | 1 | 1 / 1 | none | `get-model` and `facade-facts` reads changed, each naming the changed file |
| 7. The annotation of a re-exported facade function changes | 1 (facade file: `facade-facts`, `facade-signature`) | 5 / 4 | none | `via` names the defining file |
| 8. A product's `product.yaml` owner changes; no Python file changes | 86 labels | labels: all 29k (the rule used: any non-Python change re-labels everything); edges 71 / 1 | none | providers read files the trace does not name; sound only by that blunt rule |
| 9. Body-only edit | 0 | 1 / 1 | none | control holds: prediction is the file alone |
| 10. `posthog/dataclasses.py` moved to `posthog/models/` | 2,082 | 2,082 / 2,082 | none per file | every importer now fails to resolve. 71 cycle rows and 2 `foundation-direction` rows moved, sited across the repo; the relation digest moved |

**Before the widenings.**
- Mutation 2 missed with the narrow trace.
- Mutations 1, 2, 5 and 10 moved package-grain rule rows (`no-new-package-cycles`, `foundation-direction`) whose sites have no tie to the changed file: 1, 15, 2 and 73 rows. A per-file store loses them; only a relation digest holds them.
- Mutation 1, first attempt: the edit put a line between a decorator and its def. The file stopped parsing, and 2,104 files changed through the extractor failure. The tree was rebuilt; that failure mode (an unreadable hub) is the same shape as mutation 10.

**Verdict.**
- **The model holds with three widenings:**
  1. the trace includes the module an edge spells;
  2. rule rows are kept under their identity files too;
  3. rules folded to packages or to the whole graph are reused by relation digest, not by file.
- **Labels from provider files need their own trace.** The spike used "any non-Python change re-labels everything", which is sound but blunt.
- **The name grain (v2) is what keeps the cost near the truth.**
- **Pack producers need per-item read recording before any of this applies to them.** As built, they can only be reused per step, and `posthog.drives` would rerun in most commits.

## Part 4: what would a fast pre-commit mode (changed files, core-only rules) see?

A findings-only rerun of the same 61 trees.
Each differing finding key or incomplete item counts once per pair: "appears" if its count went up, "goes" if it went down.
Rules come from the preset of the time, byte-equal to the snapshot's.
Views: `zones` is core `import` only; `packages` is import, reference and dotted-path.
The labels the class-1 rules read come from `[[assign]]` paths, zones or registries in config.

**Rules by class.**
- **1, core-only:** `prod-imports-test` (import and dotted-path, testcode), `infrastructure-direction` (core kinds; zone, tier, stage), `leaves-stay-leaves` (import; tier, external), `dynamic-imports` (core kind; registry), `thin-commands`, `presentation-uses-facade` and `facade-no-drf` (zones view; labels, external, module), `facade-logic` (core declares with core `body`/`decl` attrs). The last three are facade-named but read nothing a pack makes. Counted as facade rules (class 3), class 1 sees 0 below.
- **2, needs a pack:**
  - pack edge kinds: `get-model`, `reverse-accessor`, `receiver-direction`, `installs`, `drives`, `migration-database`, `featureflag-relations`;
  - use-shape: `model-crossings`, `atomic-outside-views`;
  - pack symbol facts: `orm-independent` (`dst.kind`), `own-database` (kind, database);
  - `no-product-lists-in-core` (`dst.exported` in its `unless`);
  - hard to classify: `product-code-at-home`, `registry-slots`, `generated-producer`.
- **3, aggregate, export or facade:** `contracts-no-django`, `health-checks-no-dags` (reach); `no-new-package-cycles` (cycle); `foundation-direction` (packages view); `wiring-watched` (coverage); `public-surface`, `export-shape`, `facade-signature`.

| | seen | needs a pack | aggregate | elsewhere |
|---|---|---|---|---|
| 30-commit sample, appears / goes | 9 / 0 | 6 / 0 | 69 / 0 | 0 / 0 |
| 3 chosen commits, appears / goes | 2 / 0 | 0 / 2 | 2 / 5 | 0 / 0 |
| 11 mutations, appears / goes | 0 / 0 | 299 / 3 | 196 / 88 | 2,154 / 1 |

**Share of appearing findings that class 1 sees.**
- 30-commit sample: 9 of 84 (10.7 %). All 33 commits: 11 of 88 (12.5 %). Mutations: 0 of 2,649.
- All 11 that it sees are `facade-logic` (9) and `presentation-uses-facade` (2).
- What it misses in the commits: `export-shape` 29, `public-surface` 21 + 3, `facade-signature` 19 + 4, `no-product-lists-in-core` 6, `own-database` 1, `model-crossings` 1.
- In the mutations, the hub move adds 2,051 `prod-imports-test` findings in unchanged importers (class 4, "elsewhere"). Mutation 1 removes one in the importing file, which is not the changed file.

**Snapshot tree.**
Of the 41,295 findings: 5,368 (13 %) are in class-1 rules, 5,306 (13 %) in class 2 and 30,621 (74 %) in class 3.
