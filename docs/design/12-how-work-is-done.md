# 12. How the work is done: gates, verification, reviews, lessons

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 16, 2026.

## Decided

**Roles.**
- The owner decides direction, policy, targets, the repo, publishing, and anything that changes what the ledger promises.
- The architect, an AI agent, drives the design and the build and records its decisions in the decision log.
  The owner can veto any of them.
- Build agents work from written briefs and never decide scope.

**Gates every phase passes:**
- the ten parity tables: zero delta against the base at real size (`bin/prove --real --base`) and against the model repository's stored tables, or a **named delta**, where the architect's own script explains every changed row;
- the whole-graph digest, a per-row proof, and equal ordinals;
- invariants against the tools hoglint replaces (the crossings ledger with two known extras; tach's module map with no missing and no extra edge over about 99,000 edges);
- lint (ruff, ruff format, ty, clippy `--workspace`, cargo fmt `--all`), pytest and cargo tests with `HOGLINT_REQUIRE_NATIVE=1`, so a missing native module fails and never skips;
- determinism over seeds and thread counts; every repro set from earlier reviews prints what it printed before;
- the portability gate (macOS arm64, Linux arm64, Linux amd64) at the end of a phase;
- timing in same-minute pairs, CPU next to wall (see `07-speed-and-memory.md`).

**Independent verification.**
The architect reruns repros, writes its own delta scripts, and does not take a build agent's numbers as verified until its own round passes.

**Reviews:**
- a code review and a simplification review when a piece of work is done (owner, Nov 9);
- adversarial reviewers per phase, each finding with a repro;
- blind designers, blind users and blind maintainers, who get the requirements and not the code (owner, Nov 6 and Nov 9);
- a second opinion from another model, given facts and numbers only.
  The architect writes its own view first, so the two are independent.
- A reviewer's finding is checked before use.
  Several were rejected after reading the code.

**The model repository** (architect, D106, Nov 12, on the owner's idea).
`tests/model_repo/` is a miniature of the PostHog monorepo's layout with one invented example per case, checked with the real preset in the native engine inside the test suite.
- **What it is for:** the small question behind most real-size runs, "does this kind of code still give this verdict?", answered in about a second.
  Each case has a finding and a clean neighbor.
  `CASES.md` lists the cases; `expected.toml` is the reviewed result, every row tied to a case; `tables.toml` holds the row count and a digest of each of the ten tables, so a row that moves in any table is seen; `messages.toml` holds every message and reason, so a wording that moves is named; `test_explain.py` holds what `explain` says to the run it explains (each label to the evaluator's value, each judged edge's truths to the rows), and `explained.txt` keeps its wording for a few modules and edges the cases name.
  Verdicts that are decided and not built yet are `[[wanted]]` entries, run as expected failures.
- **How to use it:** `bin/model-repo` after a rule or preset change prints, per rule, the rows whose verdict changed and the case each is tied to.
  `bin/model-repo --refresh` rewrites the expected file, the tables and the messages; a reviewer then reads its diff, ties every new row (case `"?"`) to a case, and checks each gone row against the decision behind the change.
- **What it is not for:** it does not replace the proof at real size.
  Twice on Nov 12 a change passed every small test and failed on the snapshot.
- **Its own rule:** every row kind that a real-size run shows and no case predicts becomes a new case.

**The slot table test** (architect, Nov 13).
`tests/native/test_slot_table.py` holds the small trees of the proofs of the export rule: per case, who reports a type slot (`export-shape`, `facade-signature`) and how many times, with no producer failing.
A hole a proof finds becomes a case there.
A hole whose fix is decided and not built is a strict expected failure with its reason, so the change that builds it turns the case green.

**The generated slot test** (architect, Nov 13).
`tests/native/test_slot_exactly_once.py` builds its trees from three lists: where a slot sits, how its type is written, and how a facade hands the name out.
Each tree runs with a model of the product in the slot, with `int`, with a name that does not resolve, and, under the bare wrapper, with `Any`.
- **The invariant:** the model gives exactly one finding, from the rule the table of [09](09-contract-surfaces.md) names, and it names the model; `int` gives none; the unresolved name and `Any` give exactly one, from that rule. It reads the native engine's tables through the shared harness (`clean_dump`), which also requires that no producer fails.
  A wrapper the page reads as unknown (`NewType`, a `TypeVar` with no bound, a class-body alias) gives one finding that names it, with every probe; a member of a class core defines gives none. A `TypeVar` with a bound is judged by its bound; a functional `TypedDict` is a class, as its class form is, and the slot table holds the two side by side.
- **Named limits:** a tree that does not meet the invariant today is in `LIMITS`, a row per cause: the trees it holds for, what they give, why, and the page sentence that states it (or "not written").
  The test asserts the row still gives exactly that, so a fix makes the row fail; remove the row then.
- **A new form** is one more entry in one of the three lists.
- **Speed:** many trees share one repo, one product per tree, so one dump serves a batch.
  The suite runs every placement with every wrapper under one hand-out form, every placement with every hand-out form under the bare wrapper, and a fixed sample of the rest (under a minute).
  `HOGLINT_SLOTS_FULL=1` runs every tree.

**`bin/prove`** (architect, Nov 13).
One command runs the mechanical checks side by side and prints one verdict table: per check, passed or failed, one line, its seconds, and a log file.
- **Always:** the build; the model repository (its rows and its stored tables); pytest with the native engine required (the known failures named); `cargo test`; clippy; `cargo fmt`; ruff check and format; `ty` against its baseline count; determinism on the model repository (three hash seeds times one and four threads); the store modes on the model repository (memo off, answers off, trace on, fill, warm), each against a cold dump.
- **At real size, by argument:** `--real <snapshot>` (repeatable) with `--base <rev>` builds the base into its own target directory, dumps both in the native engine on a copy of each snapshot laid with each side's preset, and prints per rule the findings and incomplete items that came and went, edges by kind, and the products that gain or lose a level; the moved rows go to files. `--replay <sample>` runs the replay sample in today mode and as the certificate.
- **Private by construction:** each run has its own work directory, a private copy of the native module, and a private cache per check. Snapshots and samples are copied, never changed. Paths of snapshots, the sample, the Python for real-size runs and the base's target directory come by argument or environment variable.
- **What it is not:** real size is a measurement read by the architect, not a correctness proof; correctness lives in the small trees.

**Checkpoints:** a phase stops in its first third if its cheapest steps do not show the gain in pairs.
N2's bar was 0.45 s; it met it with 1.1-1.4 s.

**Before proposing a change to a decided design, read the decision and its alternatives, and say in the proposal what was decided before and why** (architect's rule, Nov 11, after the count-ledger idea was turned down).
`docs/design/` exists for this rule.

## What went wrong, and the rule each mistake left

| What happened | Rule |
|---|---|
| Agents committed through a shared git index and swept in other agents' files | Commit with `git commit -F <msg> -- <paths named by hand>`; one git worktree per parallel agent |
| One commit was amended with hooks skipped | Reported to the owner; to be closed by a reword in the history cleanup |
| "summary: equal" was vacuous since phase 3 (a caught `TypeError`) | Parity fails loudly on any dump step |
| The parity dump built its engine on a bare repo root, so the ten tables never covered plugin directories | The dump builds its files root as `check` does |
| `cargo clippy` without `--workspace` never linted the binding crate | Gate script fixed |
| The config generator varied only `[rules]`, so three review rounds found defects outside its reach | Generate configs from the schema's key lists |
| A build agent claimed memory was mostly Python objects; the architect measured otherwise | Measure before accepting a cause |
| Scratch output (parity dumps, build directories) grew without bound and stopped work | A disk rule in every brief; delete dumps after the compare |
| Timing scripts without a private cache root filled the real cache | A private `XDG_CACHE_HOME` per run |
| An agent built into the shell's default `CARGO_TARGET_DIR` | Each phase its own target directory |
| A test count was measured in another worktree's Python environment | Run the repo's tools from the repo's own environment, by path |
| Clock times in the progress log were estimated and ran ahead | Times come from `date` |
| A brief built on a summary of a study (D48's "three additions") dropped builtins | Diff the real effect first (take the producer out, compare findings), then write the brief |
| Estimates from reading code or from cProfile shares overstated gains | Pairs only; say "estimate" |
| A delta script keyed rows by identity, but one identity can have several rows | Compare row multisets |
| The architect answered a design question from memory under time pressure | Read the decision first (rule above) |
| Statements to the owner that needed correcting: "stores the same facts twice"; salsa persistence "a prototype"; "no finding is added or dropped" (D62) | Correct them on record in the decision log |

## Standing limits on agents

No push, no PR, no repo creation and no publishing without the owner.
Nothing deleted outside the program's scratch area.
Disk and memory rules in every brief.
Other checkouts are used read-only.

## Open

- This public version of the record is D68.

## Sources

`docs/history/decision-log.md`; `docs/history/decision-log-2.md`; `docs/history/decision-log-3.md` (D62, D68, "How existing findings are carried"); not published (the progress log, the history cleanup list, the build commands).
