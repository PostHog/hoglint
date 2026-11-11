# Traps: ideas that look right and were turned down

> The list of traps, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 11, 2026.

Read this before you propose a change.
Each row gives the idea, why it tempts, and why it was turned down or under which condition it would be right.
"Owner" and "architect" are as in `README.md`.
The topic pages hold the detail.

## Design traps

| Idea | Why it tempts | Why turned down, or when it would be right | Who, when | Topic |
|---|---|---|---|---|
| Carry existing debt as a count per rule and product, strict only above a level | Small ledger, Ruff-like simplicity | A count cannot see a swap and has no line to go stale; a product below the level can get worse. Right only if the guarantee is stated as "no net increase in this bucket" | Owner, Nov 11 | 02 |
| Baseline today's whole output into the ledger | Fast adoption | Most of the findings were noise; that grandfathers noise | Owner, D66, Nov 11 | 10 |
| Sum growth over a whole growth key | Makes moves free | A coupling removed in product A pays for a new one in product B. Growth is now per product | Owner, D67, Nov 11 | 02 |
| A `[[relocations]]` list for moves | Explicit and reviewable | Location-free growth keys plus move admission cover moves | Owner, Nov 6 | 02 |
| Receipts so that relaxations are reviewed and tightenings are not | Matches "tighten freely, relax with review" | A quick patch; all hand-written policy goes through code-owner review instead | Owner, Nov 9 | 02 |
| `hoglint.toml` edits are neutral | Policy is not debt | Relabels launder policy; config changes need a semantic diff | Review, Nov 6; second opinion, Nov 11 | 02 |
| Keep a hand list of package edges (`depends_on`) | Familiar from tach | Never prevented cycles; no dependency owner signs it; nobody reviews 425-700 edges | Owner, Nov 5 and 9 | 04 |
| A rank field instead of direction rules | One number per package | Cannot express same-rank bans, audiences or cycles | Round 3, Nov 6 | 04 |
| Public by folder (the facade folder is public) | Simple; Packwerk does it | Becomes a catch-all drawer; publicness comes from shape | Owner's doctrine; by exclusion, Nov 9 | 09 |
| Start new rule families as advisory | Less friction at adoption | Advisory means batch burndown with a near-zero fix rate; all families block, with debt in the ledger | Owner, Nov 9 | 04 |
| Make a noisy rule advisory, or drop it | Removes noise fast | Fix the rule's setting first; a decided blocking rule stays blocking unless the owner decides otherwise | Architect withdrew it, Nov 11 | 10 |
| Auto-demote a noisy rule by a noise budget | Self-healing | Weakens explicit policy; turn a rule `off` by hand instead | Second opinion, Nov 9; architect, Nov 11 | 04 |
| Exclude tests or generated files from boundary rules wholesale | Removes noise | Tests are real coupling for test selection; generated code makes real imports. Scope by context, per rule | Round 3; second opinion, Nov 11 | 04 |
| Register product parts in a manifest file | One manifest per product | Manifests go stale; a file convention instead | Owner, Nov 9 | 03 |
| Treat every surface alike for unannotated parameters | A uniform rule | Presentation adapters are subclassed, not called; the shape says whether signatures are a contract | Architect, Nov 10 | 09 |
| Let any model that core owns cross a contract surface | An easy rule | Core models may move into a product later; the preset names a list of future-proof models | Owner, D65, Nov 11 | 09 |
| Let the place where a class or alias is defined change the verdict | Matches the old producer | A product chooses its module layout; verdicts must not depend on it | Owner, D63; architect | 09 |
| Report an alias as unknown even when its expansion is known | Strictness | Noise; reversed by D70 | Architect, Nov 11 | 09 |
| Disable rules below a level, or select rules by the level they compute | Fewer findings for immature products | Products below the level can get worse; "fails isolation" becomes "rules no longer apply" | Second opinion, Nov 11 | 03 |

## Engine and speed traps

| Idea | Why it tempts | Why turned down, or when it would be right | Who, when | Topic |
|---|---|---|---|---|
| A long-lived process or daemon for the hook | Warm memory, fast | Dozens of worktrees; about 1 GB idle; nothing for CI; needs the same invalidation as stored answers. Right later for an editor server | Owner, Nov 9; architect, Nov 10 | 08 |
| Salsa with persistence for reuse between processes | rust-analyzer and ty use salsa | A fresh process loads the whole database (0.41 s against 0.27 s to compute the reduced model). Right inside one live process | Architect, D59, D61 | 08 |
| Prewarm with a `post-index-change` hook | Hides latency | Helps one flow of five; storms to 20 checks and 17-20 GB | Architect, D59 | 08 |
| A fast hook mode on changed files with pack-free rules | Under 1 s | Sees 12.5 % of new findings | Architect, Nov 10 | 08 |
| Memoize rule rows by a forward key while the full graph is in memory | 99.9 % of keys are unchanged | Computing the keys costs three times the judging it skips (D69). Right only when unchanged rows are not read at all | Architect, D69, Nov 11 | 08 |
| A background job on a baseline miss | Hides the second run | The target is Ruff-like speed (owner, Nov 10); compute inline | Architect, D39 | 06 |
| One Rust binary, no Python | "Like ruff" | Python start is 0.06-0.10 s; the cost is per-row Python, not the shell | Architect withdrew D47 v1 | 05 |
| Port every pack to Rust, including `drives` | Speed | Company logic in Rust makes each change a release; coarse queries gave nothing | Architect, D49 | 05 |
| Group edges by label class to cut rows | 18x over plain Python | Three silent holes, found by reading; deleted in N1 | Architect, N1 | 04 |
| Compute only the edges rules select | 63 % of edges are unread | Building an edge costs as much as resolving it: 23.0 to 19.6 s only | Engine round, Nov 9 | 07 |
| Columnar or msgpack transport while Python iterates rows | Faster decode | The cost is one Python object per row | Engine round, Nov 9 | 05 |
| A mapped fact store | Less memory | 0.14 GB for +1 s CPU; about 3,000 lines for 0.24 GB at most | Architect, D56 | 06 |
| Store facts once (drop the JSON records) | Halves the cache | The binary body is a reduced projection, not a copy | Architect, Nov 10 | 06 |
| Compress the binary bodies | 96 MB less | +0.1 s per warm check. Right if a design reads bodies only for changed files | Architect, Nov 10 | 06 |
| List site-packages from RECORD | 0.1 s saved | A stale RECORD differs silently | Reverted, Nov 10 | 06 |
| Limit concurrent runs with lock slots (D36) | Protects memory | A hang, a broken bound, a crash | Architect withdrew it, Nov 10 | 07 |
| Adaptive thread count from the load average | Uses idle cores | No gain in pairs; many starts read the same low load | Architect, D6 third version | 07 |
| Cycle names resolve to `unresolved(cycle)` | Order independence | 340 correct resolutions lost; results were already deterministic per question | Architect, D21 revised | 05 |
| Never store a dirty file, or never store a working-tree run | Simplest safe fix | Hides the race; leaves two meanings of "working tree" | Architect, D28, D40 | 06 |
| Fetch main-branch summaries over the network locally | Saves a base run | Costs about the same as computing; needs auth. Revisit if a fresh baseline takes over about 5 s | Architect, D14 | 06 |
| Fork tach | A head start | Lacks a whole-program index; one maintainer | Owner, Nov 4 | 01 |

## Process traps

| Trap | Why it tempts | What happened, and the rule | Who, when |
|---|---|---|---|
| Propose a change to a decided design without reading the decision | Memory feels enough; time pressure | The count-ledger proposal repeated an idea weighed on Nov 4-9; an hour later the package-cycle rule was nearly dropped the same way. Rule: read the decision and its alternatives first, and say what was decided before | Owner, Nov 11 |
| Write a brief from a summary of a study | Saves reading | D48's summary dropped builtins; the N3b brief was wrong by 2,946 findings. Rule: measure the real effect first | Architect, Nov 11 |
| Trust an agent's report without an own check | Agents report with confidence | A vacuous "summary: equal"; a wrong memory cause; a test count from the wrong environment; a parity gate that missed plugin roots. Rule: independent rounds with own scripts | Architect, Nov 9-11 |
| A speed estimate from reading code or from a profile share | Looks precise | D29 (45-50 % against 10 %), N1 (0.7 s against 0), the lazy fetch (0.2-0.35 s against 5 ms), D69 (a saving that turned into a cost). Rule: pairs; say "estimate" | Architect, Nov 10-11 |
| Quote RSS as memory | One number | Peak RSS was the end of the run; the footprint is the memory the process owns | Architect, D56 |
| Compare timings across hours or power modes | Convenient | Load and power mode move times by 1.4x. Rule: same-minute pairs | Architect |
| A generator that varies only part of the config | Cheap fuzzing | Every review defect sat outside it. Rule: generate from the schema's key lists | Architect, Nov 10 |
| Let parallel agents share a git index | One worktree | Commits swept other agents' files. Rule: `git commit -- <paths>`; one worktree per stage | Architect |
| Leave scratch output and caches unbounded | Faster reruns | Work stopped on a full disk; the real cache grew past its bound. Rule: a disk rule, private cache roots | Architect, Nov 10 |
| Estimate the clock instead of reading it | Narrative flow | Log entries ran ahead by hours. Rule: times from `date` | Architect |
| Treat an owner's question or remark as a decision | Momentum | A remark about effort is not a choice of road; agreement to a proposal covers that proposal only | Record rule, Nov 11 |

## Sources

All topic pages; `docs/history/decision-log.md`, `decision-log-2.md`, `decision-log-3.md`; the spike and review files in `docs/research/` named on the topic pages.
