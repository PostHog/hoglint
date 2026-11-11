# hoglint research and studies

> The index of the earlier research, studies, spikes and second opinions behind hoglint's design, Nov 3 to Nov 11, 2026.
> Each file says at its top whether later work replaced it. The current design is in `docs/design/`; the decisions in order are in `docs/history/`.

These are the real documents, cleaned for a public repo.
Names, local paths, PostHog policy, adoption plans and findings about PostHog's code were taken out or made general; the technical content is kept.
"The owner" is hoglint's owner; "the architect" is the AI agent that drove the design; "a second opinion" is another model, given facts and numbers, usually after the architect wrote its own view first.

Read them in this order.

## Round 1: should there be one linter at all (Nov 3-4)

| File | One line |
|---|---|
| `research-round-1.md` | The question, the method, and the synthesis of the first round; where things stood after the first spikes |
| `research-round-1-empirical.md` | Measurements on the PostHog monorepo: speed of the old checks, debt growth, escapes, friction |
| `research-round-1-prior-art.md` | Survey of boundary tools (Packwerk, import-linter, ruff, ty, Bazel, Nx, ArchUnit, Django tools) and how they failed |
| `research-round-1-contrarian.md` | The case against building a new linter, and the alternatives |
| `tach-fork.md` | Whether to fork tach: maintenance, architecture, a benchmark, the cost of each wanted feature |
| `blind-design.md` | The round-1 design written blind from the requirements only (Nov 3) |
| `research-round-1-second-opinions.md` | Four second opinions: an independent proposal, a review of the data model, where config lives, a rule mapping |

## The data model and its generality (Nov 4)

| File | One line |
|---|---|
| `data-model.md` | The v0 data model, part 1: facts, edges, labels, the eleven primitives, the ledger (sections 0-10) |
| `data-model-2.md` | Part 2: the mapping of 54 existing rules, open decisions, review changes, spike results (sections 11-20) |
| `generality.md` | The spike engine checked on Sentry, Saleor and Zulip, and the projection layer designed from that |
| `generality-2.md` | The config sketches for those three repos |

## Rounds 2 to 4: dependency lists, exports, adversarial review (Nov 5-6)

| File | One line |
|---|---|
| `depends-on.md` | What replaces a hand-kept dependency list: graph rules for cycles, infrastructure and foundation |
| `depends-on-second-opinion.md` | The two second-opinion rounds behind it |
| `exports-and-shapes.md` | Facades as exports with a shape; generated code; registries; one fact store; moves without a relocation list |
| `exports-and-shapes-second-opinion.md` | The three second-opinion rounds behind it |
| `adversarial-reviews.md` | Six adversarial reviews of the design draft: blockers, design fixes, and the config, core-purity and gaming reviewers |
| `adversarial-reviews-2.md` | The ledger, implementation and tool-replacement reviewers, the adjudication, and a follow-up measurement |

## Prior art round 2 and blind users (Nov 9)

| File | One line |
|---|---|
| `prior-art.md` | The synthesis: which mistakes of earlier tools the design avoids and which it repeated |
| `prior-art-conformance.md` | Architecture conformance tools and what went wrong with them |
| `prior-art-fact-stores.md` | Systems that store code as facts (CodeQL, Glean, Kythe, SCIP and others), with a measurement |
| `prior-art-modules-deployment.md` | Module systems, plugin registries, and how static analysis was rolled out |
| `prior-art-second-opinion.md` | A second opinion that checked the claims and ranked the design changes |
| `user-guide.md` | The guide written for blind user tests, how it was used, and what changed since |

## The engine round (Nov 9)

| File | One line |
|---|---|
| `engine-prior-art.md` | How Python-fronted tools split native and scripted work; negation in demand-driven engines; Python object costs |
| `engine-blind-design-a.md` | Blind architect A's engine design, from the requirements only |
| `engine-blind-design-b.md` | Blind architect B's engine design, from the requirements only |
| `second-opinion-engine-round.md` | The first second opinion on the engine design |
| `engine-adversarial-reviews.md` | The method, and the correctness adversary's findings with what each led to |
| `engine-adversarial-reviews-2.md` | The extension and the performance adversaries |
| `engine-adversarial-reviews-3.md` | The skeptic, the demand audit, and the list of bugs the round found |
| `second-opinion-adjudication.md` | A verdict per adversary finding, and the engine to prototype |
| `engine-synthesis.md` | The three candidate engines, what the measurements refuted, and the decision (D1-D13) |
| `second-opinion-3c.md` | After phase 3b: where time and memory went |

## The native engine (Nov 10)

| File | One line |
|---|---|
| `second-opinion-3d.md` | After phase 3d: the commit path, memory, the rules phase |
| `native-engine-design.md` | The target design for moving a check out of per-row Python, with each checkpoint's measured state |
| `spike-n1.md` | Labels and edge rules judged per edge in Rust: equal, and how much faster |
| `spike-n3.md` | The two packs: a profile, one producer ported to Rust, coarse queries in another |
| `spike-n5.md` | A memory map of one check, an earlier release, and a mapped fact store |
| `facade-facts-map.md` | Who reads the facade producer's edges; the input to moving facade facts into the engine |

## Reuse between checks and the fast hook (Nov 10-11)

| File | One line |
|---|---|
| `spike-n7.md` | 33 real commits and 11 mutations: where outputs move, whether per-file reuse is exact, what a fast mode sees |
| `second-opinion-reuse.md` | A second opinion on reuse between checks |
| `spike-n8.md` | Prewarming the check from git hooks |
| `spike-n9.md` | Salsa with persistence at the real scale |
| `incremental-plan.md` | The staged reuse plan S0 to S7, with sizes and stop rules |

## Carrying existing findings (Nov 11)

| File | One line |
|---|---|
| `second-opinion-ledger.md` | How a linter should carry existing violations: baselines, counts, scopes, rule states, the ledger's unit |

## A note on rule counts

The PostHog preset had 31 rules by the engine round of Nov 9; the user guide, written the same day, describes an earlier set of 19.
"About 30 rules" in the Nov 10 documents is the same count, rounded.
"23 rules" counts a subset: in `spike-n1.md`, the rules the grouping engine grouped; in `second-opinion-ledger.md`, the rules that had findings in the dump the triage read.

## Not here

The findings triage of PostHog's code, PostHog policy, adoption plans and the build briefs are not published.
