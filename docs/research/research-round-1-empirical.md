# Empirical evidence: where boundary tooling hurts

> Measurements on the PostHog monorepo for research round 1 (speed of the existing boundary checks, debt growth, escapes, friction), Nov 3, 2026.
> Partly replaced: later timings of hoglint itself are in `docs/design/07-speed-and-memory.md`; the round's synthesis is `docs/research/research-round-1.md`.

Checkout: the PostHog monorepo as of 2026-10-13, warm disk cache.
Measured on 2026-11-03.
Windows: 6 months = since 2026-05-03, 3 months = since 2026-08-03.

## 1. Speed

Local wall time (`/usr/bin/time`, 2 runs where noted):

| Check | Invocation (as CI runs it) | Local wall | CI step (3 master runs of the backend CI workflow) |
|---|---|---|---|
| tach | `hogli lint:tach` (two passes: deps, interfaces) | 3.1-3.3 s (user 7.9 s, multi-core) | 17-18 s |
| tach, single pass | `tach check --dependencies --interfaces` | 1.5-1.8 s, but exits 1 (tests in the dependency graph) | n/a (fallback only for old branches) |
| import-linter | `lint-imports` (6 contracts) | 1.25-1.4 s (cache on or off) | 11-12 s |
| product:lint | `hogli product:lint --all` | 7.7-8.1 s | 14-15 s |
| invariant: model crossing | `pytest posthog/test/repo_invariants/test_model_crossing_uses.py` | 32 s wall (22 s test + 10 s startup) | part of 267-278 s |
| invariant: dagster isolation | same directory, one file | 11 s | part of 267-278 s |
| invariant: pytest module collisions | same directory, one file | 12 s | part of 267-278 s |
| invariant: migration deps share DB | same directory, one file | 4.9 s | part of 267-278 s |
| invariant: rbac shadow resolver | same directory, one file | 5.2 s | part of 267-278 s |
| invariant: generated files registered | same directory, one file | 3.8 s | part of 267-278 s |
| All repo invariants | `pytest -p no:cacheprovider posthog/test/repo_invariants` | not run (long) | 267 s, 276 s, 278 s |

Notes:

- The three static tools are fast already: tach plus import-linter plus `product:lint` take about 12-13 s serial locally. In CI they run in a `parallel:` block with `owners:lint`, so the block costs about 17-18 s. The visible cost in CI is the setup around them (checkout 25-46 s, installing Python dependencies 14-15 s, bootstrap scaffold 1-2 s), not the checks.
- The expensive part is the invariants step: about 270 s in every run. It is the only boundary-adjacent step that needs `django.setup()` (the CI comment says "import graph after django.setup(), signal registries, repo-wide AST scans"). The one boundary test measured, `test_model_crossing_uses`, costs 22 s of test time and 10 s of Django start. So a registry-aware rule costs about 10 s of startup plus its own scan, versus about 1.3 s for an import-only rule.
- `hogli product:lint --all` ran in 7.7 s locally; CI takes 14-15 s. The local run reported failures for 2 products that look unrelated to speed; they were not investigated. The timing still reflects a full pass.
- No tach, import-linter or `product:lint` hook ran in `.husky/pre-commit`. Only a warn-only scaffold check ran there. The pre-push hook runs `hogli ci:preflight --strict`. Boundary checks were CI-only for most people: the checks are cheap enough to run on commit, but nothing wired them in. Preflight was not timed.
- The tach CI invocation needs two passes (`hogli lint:tach`) because tests are in the interface graph but not in the dependency graph. The naive one-pass `tach check` fails on this tree with test-file violations (a test in one product importing another product). That is a tool-model mismatch worth designing around.

## 2. Escapes and debt over time

### Debt lists, time series (computed from `git show <commit>:file` at the dates shown)

| Date | model_crossing_uses_baseline (entries) | isolation_baseline (entries) | tach modules | tach `depends_on` edges | tach interfaces | tach.toml lines |
|---|---|---|---|---|---|---|
| 2026-05-03 | file absent | absent | 53 | 163 | 6 | 559 |
| 2026-07-15 | absent | absent | 73 | 394 | 21 | 1,076 |
| 2026-08-15 | absent | absent | 88 | n/a | n/a | 1,293 |
| 2026-09-01 | 856 | 62 | 91 | 520 | 33 | 1,408 |
| 2026-09-15 | 970 | 62 | 93 | n/a | n/a | 1,530 |
| 2026-10-01 | 945 | 62 | 97 | n/a | n/a | 1,649 |
| 2026-10-13 | 987 | 58 | 100 | 600 | 42 | 1,685 |

- The model-crossing baseline was created on 2026-08-21 (bc676dfdade, "check and ratchet how consumers use model-crossing classes"). It grew from 856 to 987 entries in 6 weeks (+15%), even though it is a "ratchet". Over the 3-month window it saw 1,628 added lines and 602 removed lines across 47 commits by 24 authors. That is churn in both directions: people drain entries, and new ones appear (baseline regeneration after refactors).
- `isolation_baseline.txt` (products that are not isolated): 62 -> 58 over 6 weeks. This is the only list that shrinks. 8 commits, 6 authors, 83 lines added vs 7 removed (the added lines include the initial file).
- tach `depends_on` edges: 163 -> 600 (3.7x) in 5 months, while modules went 53 -> 100. Edges per module rose from 3.1 to 6.0. The graph is getting denser, not cleaner. The file has 5 explicit "Isolation debt" or "legacy leak" comment blocks that say "drain by... then delete this block".
- import-linter at HEAD (`pyproject.toml` `[tool.importlinter]`): 6 contracts. `ignore_imports` counts: presentation must use facade 127, routes must only import presentation 51, facade must not import presentation or DRF 9, webhook consumers 1, others 0. Total 188. Time series from a crude count of `"a -> b"` lines in `pyproject.toml`: 13 (May 3), 66 (Jul 15), 83 (Aug 15), 194 (Sep 1), 222 (Oct 1), 188 (Oct 13). The Oct 13 drop is real, but the count is approximate; treat the trend as "grew about 14x, then fell about 15% in the last two weeks".

Verdict: the rule surface and the debt both grew fast.
Only the isolation baseline and (recently) the import-linter ignores shrink, slowly.
Model-crossing debt grows.

### Escapes (violations that got past the tools)

The messages of most squash-merged commits here have no body (title and trailer only), so the judgment comes from titles plus diffs.

- 5431914d611 (2026-08-28, "fix(devex): stop tach from excluding test directories"). `tach.toml` had `exclude = ["tests", ...]`. Test code outside a product could import product internals unchecked. The fix needed 56 files moved or changed, a new two-pass tach check, and a new `[[interfaces]]` debt block. The existing violations were invisible until the exclusion was removed. This is the largest single escape: the tool could see tests, but a config line hid them.
- 72829989659 (2026-08-03, "fix(ci): stop a comment in tach.toml from truncating a module's deps"). A comment in the TOML silently truncated a module's `depends_on` list. A format or parser bug in the config, not a violation, but it made the rules weaker with no signal.
- 7c6f555144a (2026-05-07, "fix(devex): emit valid tach config from product scaffold"). The scaffold produced invalid tach config.
- Tooling bug-fix commits in `tools/hogli-commands/hogli_commands/product/` since May: 16 titled `fix...` out of 68 commits touching that directory (about 24%). Examples: f184aeed0c6 (facade modules in subfolders were not read by `product:lint`), a6ae3be3f85 (facade functions in submodules not detected), aae2abc24fb (tach deps miscounted in the maturity report), 412ed370462 (cloud deploys did not fail on unconfigured product database routes), plus two fixes to the inputs that the narrowed contract check watches. Several of these are "the checker mis-saw the repo": the missed rule was a layout or discovery bug.
- Facade routing fixes done after the fact: two commits on 2026-09-15 routed one product's reads and API writes through its facade (the bypass existed before the fix). One commit on 2026-07-10 fixed a circular import in a facade that was caught at runtime, not by a boundary tool.
- Reverts: 47 commits with a `revert` subject in 6 months, out of about 25,000 commits. None of the ones scanned (titles matching boundary/isolation/facade/tach/import/circular) was a boundary-breakage revert. No revert caused by a boundary violation was found. Evidence of an escape that gets reverted is thin; escapes show up as follow-up `fix` commits or tool changes.
- `git log` with a broad "fix + boundary|facade|isolation|tach|import-linter" title match returns 110 hits, mostly false positives ("attachment", "error boundary", "date boundary"). Those were discarded; the real signal is the tool-config and tool-code fixes listed above.

Limits: the study cannot separate "violation shipped, found later" from "tool improved, old debt surfaced" without reading about 100 diffs.
The 6 weeks of baseline data show new model-crossing entries still appear at about the rate old ones drain.

## 3. Friction (last 3 months, since 2026-08-03)

| File set | Commits | Distinct authors |
|---|---|---|
| `tach.toml` | 130 | 53 |
| `products/model_crossing_uses_baseline.txt` | 47 | 24 |
| `products/isolation_baseline.txt` | 8 | 6 |
| `pyproject.toml` (any section) | 150 | n/a |
| `[tool.importlinter]` section (`git log -L` over its line range) | 68 commits touching the range | n/a |
| `tach.toml` + both baselines, unique commits | 177 | 65 |

- `tach.toml` size per commit: 91 of 130 (70%) change 6 lines or fewer, 33 (25%) change 7-30 lines, 6 (5%) change more than 30 lines. Net: 761 lines added, 214 removed. Small edits dominate, which fits "add one `depends_on` or one expose entry so the check passes".
- 123 of 130 `tach.toml` commits are not titled devex/ci/tach, so they are product or feature work that had to touch the tool config. About 53 authors in 3 months is a large share of the contributor base for one config file. 177 unique commits out of roughly 12,000 in the window is about 1.5%.
- Whether each edit was "only to satisfy tools" or a deliberate dependency was not verified per commit. By size and title, most look like the former (a product gains a new consumer, or a new facade expose line). That is an inference, not a measurement.
- No GitHub-side data was collected (no PR comment or review counts). Only git was used. GitHub Actions job data was read only for step timings.

## Takeaways for the design

1. Static import checks are already cheap (1-3 s locally). Speed is not the issue for them; wiring is (they are not in pre-commit), and the setup cost dominates CI. The 270 s is the Django-aware repo invariants. A new tool that keeps registry-dependent rules out of the hot path and shares one parse across rules would be the real win; being faster than tach is not.
2. Configuration gaps, not rule gaps, caused the largest escapes: excluded test trees, comment-truncated dependency lists, discovery bugs for facade submodules. A tool that validates its own config (and shows what it did not scan) targets real pain.
3. The debt grows where it is not ratcheted per entry (tach edges 163 -> 600, the model-crossing baseline +15% in 6 weeks). Debt lists need owners and expiry, or they only accumulate.
4. About 53 authors per quarter edit `tach.toml`, mostly in small edits. A rule format where "add this allowed edge" is a one-line, owner-routed change (or derived from a declared product contract) would cut the friction.

Sources: the backend CI workflow (`.github/workflows/ci-backend.yml`); GitHub Actions job data for three master runs (read-only); `git log` and `git show <sha>:file` on `tach.toml`, `pyproject.toml` and `products/*baseline.txt`; the commits cited inline.
