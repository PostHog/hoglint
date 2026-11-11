# Research round 1: should there be one boundary linter?

> The first research round on a boundary linter for the PostHog Django monorepo: the question, the method and the synthesis (Nov 3, 2026), plus where the work stood after the first spikes (Nov 4, 2026).
> Partly replaced: the data model it led to is `docs/research/data-model.md`; the current design is in `docs/design/` and the decisions are in `docs/history/decision-log.md`.

## The question

PostHog is a large Django monorepo.
About 100 product packages live under `products/<name>/`, next to the older core trees `posthog/`, `ee/` and `common/`.
The goal is product isolation: a product exposes a small public surface (a facade and contract types), and the rest of the repo talks to it only through that surface.

In Nov 2026 the boundary rules were spread over several tools:

- tach: `tach.toml` (module dependencies, interfaces, utility modules).
- import-linter: contracts in `pyproject.toml` (`[tool.importlinter]`).
- `hogli product:lint`: custom checks in `tools/hogli-commands/hogli_commands/product/` (structure, isolation levels, model crossings, wiring).
- Repo invariant tests: `posthog/test/repo_invariants/`.
- semgrep rules: `.semgrep/rules/`.
- Baselines and ledgers of known violations, for example `products/model_crossing_uses_baseline.txt`.

The doctrine these tools enforce is in `products/architecture.md` and `products/README.md`.

The problems:

- The rules live in five places, with five config formats and several debt lists.
- Some rules are only checked by review, because no tool can see them.
- Django couples code in ways that are not imports: string model references (`ForeignKey("app.Model")`), reverse accessors, `apps.get_model`, signals and receivers, Celery and Temporal registrations looked up by name, URL includes, migration dependencies, settings.
- Some checks need the Django app registry, which means a slow `django.setup()`.
- Speed matters. The goal is a check fast enough for pre-commit and the editor, not only CI.

The idea under discussion: build one new, general boundary linter for Django monorepos that could replace the tools above.
Nothing about its shape was settled: the implementation language, whether to build on ruff (parser, `ruff analyze graph`), on ty (type checker, LSP), on tach (a fork), or from scratch, and what the config model looks like.

## Method

Each researcher got the same brief (the text above) and one specific question.
Earlier work was withheld on purpose, so each view stayed independent.
No researcher could edit the repo or post anywhere.

| study | question | file |
|---|---|---|
| prior art | how other boundary tools work and what went wrong with them | `docs/research/research-round-1-prior-art.md` |
| empirical | where boundary tooling hurts in the PostHog monorepo: speed, escapes, debt growth, friction | `docs/research/research-round-1-empirical.md` |
| contrarian | is one new linter the right answer at all? | `docs/research/research-round-1-contrarian.md` |
| tach fork | is tach a good base to fork? | `docs/research/tach-fork.md` |
| blind design | design the linter from the doctrine and the tool configs alone | `docs/research/blind-design.md` |
| second opinion | an independent proposal by another model | `docs/research/research-round-1-second-opinions.md` |

An earlier fit test (not published) had already mapped 54 PostHog boundary rules onto a draft config.
The synthesis below compares all of these.

## Synthesis (Nov 3)

### Facts that changed the framing

- Static checks are already fast: tach 1.3-1.7 s per pass (3.2 s through `hogli`), lint-imports 1.3 s, ruff graph 1.0 s, `product:lint --all` 8 s. The CI steps take 11-17 s.
- The slow part is the Django-aware repo invariants: 267-278 s per master run. `test_model_crossing_uses` alone takes 32 s, about 10 s of it Django startup.
- No boundary check ran in pre-commit.
- The import graph covers about 33% of real test dependencies. A PostHog test-selection study had validated this against testmon data; the architect verified the claim in its source.
- Debt grows: tach `depends_on` edges 163 -> 600 (May-Oct, modules 53 -> 100); the crossings baseline 856 -> 987 in 6 weeks; import-linter `ignore_imports` about 13 -> about 222 -> 188; only `isolation_baseline` shrank (62 -> 58).
- Friction: 130 commits by 53 authors touched `tach.toml` in 3 months, 70% of them 6 lines or fewer.
- The escapes were config and discovery gaps (tests excluded from tach until Aug 28; a comment truncated a `depends_on` list; 16 of 68 `product:lint` commits were fixes), not missing rule kinds.

### tach fork: no

- Gauge dropped tach (issue #791). One spare-time maintainer at `tach-org/tach`, no AI-written code accepted, collaborator-only PRs, 2 source commits since May.
- Per-file pipeline, no index phase, no cache, regex interfaces, config types tied to pyo3. Symbol kinds, Django references and registries need index-then-check: a rewrite.
- `ruff_python_parser` is on crates.io now (0.0.20). Depend on it directly.
- Constraint: PostHog's CI scripts read `tach map`. A replacement must emit an equivalent map.
- The second opinion differed slightly: fork only as a time-boxed spike.

### Where independent designs converge (fit test, blind design, second opinion, prior art)

- One fact model: typed edges between sites (imports, string FKs, `get_model`, receivers, task and workflow names, query-kind dispatch, migration dependencies, dotted strings). Every rule is a check over edges.
- Zones per package from one template, symbol kinds, use-shape, coverage of turbo inputs (generate, not check).
- Django edges read statically: app labels from `apps.py`, a model index with abstract-base expansion, reverse accessors computed. No `django.setup()` on the hot path. A registry oracle (CI or nightly) diffs the static index against `apps.get_models()`.
- Rust on pinned ruff parser crates, with an own modest resolver (imports, re-exports, aliases, constants, class ancestry). Not tach, not grimp, not `ruff analyze graph`. ty is not foundational (blind design and the second opinion agree); the fit test had used ty for the semantic tier.
- One debt ledger keyed by symbol, two-way equality, shrink-only, growth only with a rule or extractor change or an explicit admission.
- Additions from the second opinion: every edge carries provenance + a resolution status (resolved / candidates / unknown); unknowns never mean "no dependency". Diff mode limits recomputation, not correctness scope. Pre-commit checks the staged snapshot.

### Contrarian (do not build)

- Generate the tach, import-linter and semgrep label lists and the turbo inputs from `product.yaml` (89 products have one). This deletes the config-consistency rule family.
- Registry-shaped checks become Django system checks over a cached registry snapshot.
- A runtime auditor shard (import hook + patched `get_model`) feeds the crossings ledger.
- Delete duplicates (`product:lint` re-reading the presentation and routes contracts, core `depends_on` lists that name about every product, one of two facade-DRF checks, a one-product subtree special case).
- The deep question: does the turbo contract-check skip for about 23 Isolated products still pay for the burden of its soundness rules, now that a more lenient test selector exists?
- Mind-changers: static-visible coupling missed today; editor demand; tach stalls (it has); the core cycle shrinks; the snapshot is too stale.

### Conclusions

- "Super fast" is the wrong pitch for the import half; it is already fast. The speed target is the roughly 270 s Django invariant step, and getting any boundary feedback into pre-commit and the editor.
- The real value of a new tool: seeing Django edges without booting Django, one config, a debt ledger that cannot grow silently, editor and agent output.
- Generation from `product.yaml` is compatible with a new tool (the tool reads `product.yaml`) and is worth doing either way.
- Two questions decide the scope:
  1. Doctrine: does the Isolated skip stay as a correctness gate? If not, about half of the soundness rules become advisory, and the scope shrinks a lot.
  2. Technical: can a static Django model index match the real registry? Every design depends on it. It is cheap to test locally: the static index vs `apps.get_models()` (the existing inherited-FK runtime check is half the ground truth already).

## Where it stood after the first spikes (Nov 4)

### Decided

- The linter carries the Isolated skip's safety. Rules that feed the skip block; unknowns count against the product. A more lenient test selector exists, and it is not the safety net. (Decided by the owner, Nov 4.)
- No tach fork. An own engine on the ruff parser crates, with `tach map`-shaped output for the CI scripts that read it.
- One fact model (`docs/research/data-model.md`, v0.1): facts -> edges -> projections -> views -> rules -> ledger -> run result.
- Debt can only grow when a rule change explains it (the existing doctrine kept; the directory gate becomes attribution).
- Policy exceptions carry reasons and owners; debt lines are generated.

### Open on Nov 4

- Ledger granularity (module vs function): waited on the spike D churn numbers. (Later: module identity, see `docs/research/data-model-2.md` section 17.)
- Type-only imports per rule (proposal in data model section 16).
- Unresolved default: `finding` proposed; the owner was unsure.
- Inline suppression comments vs a central ledger: never researched; all designs assumed the ledger.
- Language for the core: Rust leading, gated on the spike C timings.

### Modules as planned on Nov 4

| module | job | language | notes |
|---|---|---|---|
| discover | snapshot (worktree/staged/commit), file inventory, excludes | Rust | |
| parse | L1 facts per file, content-hash cache | Rust | `ruff_python_parser` from crates.io |
| resolve | module index, bindings, re-exports, class ancestry, annotations | Rust | own resolver, explicit unresolved |
| adapter: django | app labels, model index, Relation records | Rust | validated by spike A |
| adapter: migrations | MigrationDecl, resolved parents, aliases | Rust | validated by spike B |
| adapter: registries | query kind, Celery, Temporal, Max tool keys | Rust | one small extractor each |
| adapter: drives | test -> wiring execution via call chains | Rust | the one call-graph user; no runtime coverage in CI |
| project | packages, zones, kinds, attributes from the boundaries config + `product.yaml` | Rust | a config change re-runs from here |
| views | file-imports, refs, zones, packages, migrations | Rust | |
| rules | 11 primitives | Rust | config-driven; plugin interface for the rest |
| ledger | equality, growth attribution vs merge base | Rust | |
| report | text, JSON, compare; tach-map emitter | Rust | |
| surfaces | CLI check/explain/query/compare, pre-commit, later LSP | Rust | |
| oracles | model registry diff, MigrationLoader diff, health-check probe | Python | must boot Django; a CI job, not the hot path |
| plugins | GitHub owner check, scaffold scripts, test-runner adapter, receivers | Python (`hogli`) | stay where they are |

### Spike results (Nov 4)

Details are in `docs/research/data-model-2.md` sections 17-20.

- Round 1, all exact parity: A relations 1,431/1,431 records and 231/231 reverse-accessor lines; B migrations 694 nodes, 1,965 edges, alias invariant equal; C import map 94,143 edges, all dependents sets equal to `tach map` (Python cold 12 s parallel vs tach 3 s; warm < 0.1 s); D ledger 61/61 lines, module identity kept (scope 1.17x lines, no gain), attribution confirmed.
- Round 2, all exact parity: E drives 110/110, full L2 re-derive 0.27 s from cached L1, so no per-edge invalidation; F facade 435/435; G shapes 150/150, def-use fixes about 21 lines; the existing scanner missed a few cases that def-use and re-export following catch; H Rust L1 0.8-1.0 s cold, hybrid boundary about 0.15 s. Recommendation: Rust L1 + Python for the rest.
- The whole crossings ledger (987 lines) was re-derived across the rounds.
- End to end: Rust L1 2.55 s + Python L2 6.7 s = about 9.3 s cold; every output exact from Rust facts (987 ledger lines plus a few new real findings, tach map, migrations, relations). Warm about 7.4 s, dominated by Python L2 (drives 2.7 s).

The language choice on Nov 4 was Rust L1 + Python L2 and up, as measured.
Later work replaced that split with an in-process PyO3 engine (`docs/design/05-engine.md`).
The defaults are in `docs/research/data-model-2.md` section 19.
