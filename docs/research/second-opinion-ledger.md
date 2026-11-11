# Second opinion: how existing findings are carried

> A second opinion from another model on how a boundary linter should carry existing violations, Nov 11, 2026: baselines, counts, scoping by zone or level, rule states, and the unit of the ledger. With the question, the architect's view written before it, and what the decisions took.
> Current as of Nov 11, 2026. The decisions it fed are D66 and D67 in `docs/history/decision-log-3.md`; the ledger design is in `docs/design/02-policy-exceptions-ledger.md`.

## The question

On Nov 11, 2026, the first full run over the PostHog monorepo had produced tens of thousands of findings, and a triage had sorted them.
The questions (Nov 11):

- Can specific rules be turned off for now, as in ruff? Or is the issue that even a noisy rule has some share of valid findings, which zones or the levels of products and areas could express?
- Does a rule such as "objects passed without types" flag across the whole codebase, or can config say it is a problem only in, for example, presentation code?

Two constraints held (owner, Nov 11): the ledger must not grandfather noise, while real problems the work surfaced get handled (D66); and a product must not be able to drift worse or stay where it is without anyone seeing it.
A proposed ledger model (strict at a level, and below it only a count per rule and product) had been turned down (owner, Nov 11), because a product could then gradually get worse. The architect withdrew it, wrote down a view, and asked for a second opinion.

The second opinion was asked:

1. How should a tool like this carry existing real violations, so that (a) noise is never accepted as debt, (b) a product cannot get worse, and (c) products actually get better over time instead of sitting on accepted debt? Compare the serious options: a per-finding baseline with equality and growth keys (the design of the day), counts per rule and product, strictness by level or by zone, per-product or per-file strictness markers, expiry or review dates, anything else. Use what is known of other tools (Packwerk todo files, ESLint bulk suppressions, Sorbet strictness levels, Betterer, tach, import-linter `ignore_imports`, Nx tags, Bazel visibility, Ruff `per-file-ignores`) and what failed there and why.
2. What is the right division of labor between scoping a rule by zone, tier or level and accepting existing findings in a baseline? Where does each go wrong?
3. What should happen to a rule that is noisy or not ready: off, advisory, or something else? Who decides, and how does it come back on?
4. Given a hook that runs on every commit and agents that write much of the code, what would you change and what would you keep? Name the two or three largest risks.
5. Is one central generated ledger of roughly 10,000 lines the right unit?

It was asked for a recommendation, the reasoning, and the strongest argument against its own recommendation.

## What it was given

- Read access to the public monorepo, especially the products README and architecture page, the monorepo layout doc, `tach.toml`, the isolation and crossings baselines, and the `product` commands of the repo's CLI.
- The situation: about 100 products under `products/` plus a core. Each product is meant to be isolated: other code reaches it only through its facade (`products/<x>/backend/facade/`), facades pass contract types (frozen dataclasses, enums) and not Django models. Enforcement was then a mix of tach, import-linter, the crossings scanner with a text baseline, and an isolation baseline. hoglint builds a whole-repo graph (imports, references, Django models and their owners, signal receivers, registrations, signature types) and judges 31 rules (23 of them had findings in the dump the triage read). Config labels every module with a tier, a zone inside a product by path, and a context. A rule selects edges with expressions over those labels and can be blocking or advisory, and can be kept out of the stored debt. A level (one, `isolated`) lists the rules a product must satisfy; a product that passes cannot drop out without failing the check.
- The design of the day for existing violations, agreed a week earlier: three files split by the direction of change. `hoglint.toml` (the architecture; changes are neutral and reviewed by the tool's owners). `hoglint.exceptions.toml` (hand-written, reviewed allowances; adding one is always "bad", removing always "good"; a stale exception fails). `hoglint.ledger` (generated debt; one line per (rule, identity), an identity being a projection of a finding with no line numbers; two checks kept apart: equality, where the scan and the ledger must match line by line, and growth against the merge base, where sums per growth key may not grow from code changes). Every ledger line is attributed to an owning team, and a report of "who owns how much" was planned. The Packwerk retrospective was on record as a warning: its todo files grew monstrously, the value came from holding the line against new dependencies, and zero violations still did not mean a package ran isolated.
- The triage, in general terms: about a quarter of the findings were real, most of the rest was noise from rule settings (for example a rule counting every later use of an imported name as its own finding, or a rule counting every facade function), and about a tenth were tool defects. The triage reports are not published.
- The questions and constraints above: no noise grandfathered, but real problems handled; the risk that a product slowly stops improving or gets worse under a count; whether rules can be turned off like in ruff, and whether the valid part of a noisy rule can be carved out by zones or levels; whether a check like "objects passed without types" applies everywhere or only where configured; and a hook that runs on every commit, with much of the code written by agents.

It got the situation and the questions, not the architect's view.

## The architect's view, written first

Written before the second opinion was asked, so the two are independent.

1. **Keep the ledger as designed on Nov 4 to 9:** one line per (rule, identity), equality with the scan, growth per growth key. Growth from code fails, so a product cannot get worse. A stale line fails, so a fixed finding must leave the ledger and cannot come back unseen. The proposal of that morning (below a level, only a count per rule and product) loses both: a count cannot see a swap, and it has no line to go stale. Withdrawn.
2. **"Do not grandfather noise" (D66) is met at the rules, not at the ledger.** The triage says about a quarter of the findings is real. Fix the tool defects and the rule settings first; write the ledger after. A ledger line then means real debt.
3. **Getting better, not only not worse, needs more than a ratchet.** The Packwerk lesson: todo files without owners only grow. The design already plans: every ledger line attributed to an owner, a report of who owns how much, levels per product as the visible target with a ratchet. To add: the crucial findings fixed as PRs now; later a per-product stricter-only setting (a product declares a rule strict for itself; monotone).
4. **Scoping by zone is how rules are written already.** The loose-type check applies to the facade zone only. More distinctions are config: a rule per zone, a shape per export. So the answer to "is it flagged everywhere" is no.
5. **A rule that is not ready: advisory and out of the ledger (exists), or off (to add).** By hand, not by an automatic noise budget (rejected on Nov 9).
6. **Unsure:** whether one central ledger of about 10,000 lines is the right unit, or one file per product for ownership and smaller diffs.

## The second opinion

What follows is the second opinion's answer, cleaned.

**Keep a precise debt ledger, but strengthen its regression check and separate architectural compliance from permission to skip tests.** Use zones to express where a rule is correct, not where compliance is convenient. Use levels to recognize progress, not to exempt unfinished products from preventing new debt.

It would not baseline the output as it stood. Taking the triage numbers as given, the first deliverable is a smaller, trustworthy rule set. The remaining findings are still candidates for debt, not automatically approved debt. It read the architecture and enforcement code of the repo; it did not reproduce the triage.

### The design has an important loophole

Equality proves that the ledger accurately describes today's findings. It does not prove that today's findings are acceptable changes from yesterday's.

Suppose a growth bucket holds five model crossings. A PR removes one crossing from product A and introduces another in product B. The total stays five. Regenerating an exact ledger makes equality pass too. Product B got worse.

The proposed design has the problem wherever its growth keys are this broad.

"No new coupling" and "no increase in a count of couplings" are different guarantees. For mature architectural rules:

- Store stable, semantic violation identities without line numbers.
- Require every new identity to be an approved exception or a recognized transfer of an existing violation.
- Treat moves and renames as transfers, with evidence connecting old and new identities. Ambiguous splits need explicit review, not unrestricted use of a shared debt budget.
- Keep counts as an extra constraint where multiplicity matters. Never use them as the sole protection against new relationships.
- Define the promised granularity. A module-to-model identity prevents new module-to-model dependencies; it cannot prevent more harmful operations inside an already coupled module.

That last point matters. Repeating an imported name 100 times is usually not 100 architectural debts. But introducing writes where there were only reads may materially worsen a dependency. Rule-specific identities should distinguish meaningful capabilities without turning every reference into a separate cleanup obligation.

### How the serious alternatives compare

These are each mechanism's limits. Apart from the Packwerk retrospective, it would not claim these tools have generally "failed".

| Mechanism | What it does well | Where it falls short here |
|---|---|---|
| Exact identities plus equality | Makes existing violations inspectable; removes obsolete allowances; supports targeted repairs | Coarse growth buckets can still permit replacement debt. Fine identities cause refactor churn unless transfers are supported |
| Counts per rule and product | Cheap reporting; useful for genuinely quantitative budgets | Deleting one violation funds a different one. Severity and affected dependencies can worsen at unchanged counts |
| Product or area levels | Makes adoption stages understandable; protects completed milestones | Rules disabled below a level cannot prevent deterioration there. Products can stay just below the threshold indefinitely |
| Per-file strictness markers | Allows small, attainable adoption steps | Moving code into a weaker file becomes an escape hatch; local strictness does not establish a boundary involving other files |
| Narrow reviewed exceptions | Records a deliberate deviation with an owner and rationale | Broad patterns become permanent permissions for future violations |
| Expiry or review dates | Forces a decision to be revisited | Creates no repair capacity. Blanket expiry produces unrelated build failures and routine extensions |
| Scheduled cleanup commitments | Can produce real progress toward a runnable boundary | Needs engineering time and accountable prioritization; the linter supplies neither |

The external tools support different parts of this:

- **Packwerk:** Shopify's retrospective documents growing todo files, expensive fixes, and a package that still could not boot independently after its violations were eliminated. The useful result was holding dependency boundaries around a concrete isolation objective. This is evidence against treating violation elimination as the objective itself. [Shopify retrospective](https://shopify.engineering/a-packwerk-retrospective)
- **ESLint bulk suppressions:** stores counts by file and rule and supports pruning unused suppressions. Practical adoption machinery, but count equality cannot see substitution within a bucket; weaker than the architectural guarantee wanted here. [ESLint documentation](https://eslint.org/docs/latest/use/suppressions)
- **Sorbet:** file strictness enables gradual adoption while keeping useful information from less strict files. It does not promise that unchecked bodies cannot deteriorate. Borrow the staged adoption and kept graph information, not the assumption that a strictness marker proves isolation. [Sorbet documentation](https://sorbet.org/docs/static)
- **Betterer:** snapshots results and rejects movement in the wrong direction. Its guarantee depends on the result and comparison you define: an aggregate count inherits aggregate-count weaknesses. [Betterer introduction](https://phenomnomnominal.github.io/betterer/docs/introduction/)
- **tach and import-linter:** deprecated dependencies and ignored imports accommodate legacy architecture. A permitted dependency can keep receiving uses; an ignored edge is not an automatic cleanup plan. Unmatched-ignore checking removes stale exceptions, but cannot retire still-used ones. [tach deprecation](https://docs.gauge.sh/usage/deprecate/), [import-linter documentation](https://import-linter.readthedocs.io/_/downloads/en/v1.12.0/pdf/)
- **Nx tags and Bazel visibility:** express intended dependency permissions. Bazel explicitly supports restricting a deprecated API to existing consumers. Good models for boundary policy, but granting visibility does not validate the objects passed through that boundary. [Nx boundaries](https://nx.dev/docs/core-features/code-organization/enforce-module-boundaries), [Bazel visibility](https://bazel.build/concepts/visibility)
- **Ruff per-file-ignores:** expresses that selected rules do not apply to matching files. Right for genuine contextual differences. Used for migration debt, it also exempts future violations in those files. [Ruff settings](https://docs.astral.sh/ruff/settings/#lint_per-file-ignores)

### Scope expresses correctness; a baseline records non-compliance

The test for a scope exclusion: *Would this code remain acceptable here after the migration is finished?*
If yes, encode the distinction in policy. If no, and the detector is trustworthy, record debt. If unresolved, keep the rule experimental until the policy decision is made.

Applied to the examples:

- **Core importing an approved product facade:** legitimate architecture, not debt.
- **Historical migrations resolving historical models:** different semantics from production reaching through the Django app registry. Scope accordingly.
- **Untyped values:** a facade's exported signature is a meaningful place to enforce contract shape. A universal ban on dictionaries in implementation code is a different policy. Typed JSON payloads, bare `dict`, `Any` and ORM instances should not be treated as interchangeable.
- **Presentation code:** can legitimately have stricter constraints because the architecture gives it a specific responsibility. But presentation-only enforcement would be wrong if the real invariant is that *all external callers* receive contracts.
- **Tests creating foreign product models:** not automatically noise. Product unit tests and intentionally cross-product integration tests have different purposes. Classify integration tests explicitly and keep their dependency consequences for test selection. A blanket test exclusion hides real coupling.

Levels add a further distinction: a rule may be applicable and ratcheted everywhere, while a level requires its debt to reach zero. That supports unfinished products without giving them permission to deteriorate. Avoid selectors that depend circularly on the very level they help compute; otherwise "fails isolation" can become "the isolation rules no longer apply".

### A ratchet preserves improvements; it does not cause them

Requirement (c), products actually improving, needs a funded migration program next to enforcement. Choose a few concrete campaigns, each with a responsible team, dependencies on other teams and an executable outcome; for example, replace foreign-model fixtures with supported test helpers in one product, then show that its tests run under the intended boundary.

Track new and retired semantic dependencies, completed boundary migrations, actual test-selection or runtime isolation benefits, remaining shared infrastructure blockers, and debt reductions caused by code changes separately from those caused by rule changes.

The "who owns how much" report should support this work, not become a league table. A consumer team may own the offending line while a provider team must first supply a usable facade.

Use review dates for temporary exceptions and migration commitments. Do not expire thousands of baseline rows at once or require every unrelated PR to remove one: those policies reward easy deletions and superficial rewrites, not useful isolation. Without allocated work, accepted debt can stay forever under every baseline format considered here.

### Noisy rules need a lifecycle, not merely a severity switch

| State | Behavior |
|---|---|
| Off | Invalid, superseded, or too unreliable to produce useful results |
| Experimental | Runs in a separate report; no accepted-debt ledger, no blocking hook output, no positive evidence for test skipping |
| Enforced | Correctly scoped and actionable; blocks new debt, with existing real violations recorded |

Advisory findings can help when a developer can act on them, but thousands of routine warnings in a hook that runs on every commit will train both humans and agents to ignore the tool.

For a rule that is 90% noise and 10% valuable, identify the reliable subset and enforce that subset. Keep the unresolved cases experimental. Do not baseline the whole rule to rescue the useful tenth.

The tool's owners should own detector quality and operational readiness. Architectural applicability should be agreed with the teams responsible for the affected boundary. Promotion should require representative positive and negative cases, documented scope and limits, sampled precision, actionable diagnostics, and a named owner. Give experimental rules a review date that forces a promotion, revision or retirement decision, not automatic activation.

There is no credible guarantee of zero false positives. The defensible guarantee is that known noise is never knowingly admitted as debt, and disputed categories cannot become blocking debt without resolution.

### For a hook on every commit, three changes

**First, configuration changes are not neutral.** Changing a zone, tier, selector, growth key or level definition can erase more enforcement than adding an exception. Produce a semantic policy diff that shows which findings disappeared because code improved versus because policy changed, which areas lost coverage, which products gained or lost qualification, and which exceptions expanded or became stale. Removing an exception is an improvement only if the violation disappeared or the allowance became unnecessary, not if the rule was disabled.

A growth gate that lets growth through whenever the scanner itself changes relies on human review alone. Use explicit rule-version migrations instead: identify what newly detected debt already existed at the base revision, separately from violations the PR introduces.

**Second, make the normal repair path safe for agents.** Diagnostics should explain the edge, the matched labels, the rule, and the permitted alternative. A ledger update command should prune resolved debt and perform supported transfers, but refuse arbitrary new debt. Do not recommend regeneration as the generic answer to a failure.

Use the hook for fast feedback and CI for authoritative enforcement. Whole-graph rules need correct reverse-dependency invalidation; scanning only edited files is not enough. Handle staged versus unstaged content deliberately, and never treat an incomplete scan as a successful isolation check.

**Third, separate "boundary rules passed" from "safe to narrow tests".** The repository's own architecture page explains why these differ: URL-driven tests, model discovery, signals and registration channels create dependencies without ordinary imports. A richer graph helps, but unresolved dynamic behavior stays unresolved. Keep conservative watched inputs, require behavioral evidence, and run full-suite comparison checks during rollout. Debt or exceptions that affect test-selection soundness must stay visible to the test selector even when they do not block a commit.

The three largest risks:

1. **False confidence leading to skipped tests that should have run.**
2. **Enforcement erosion through broad growth buckets, configuration changes or automatic regeneration.**
3. **Noise and friction on every commit encouraging cosmetic compliance while substantive coupling remains.**

### One logical ledger is right; one physical file is optional

Ten thousand lines is not excessive for generated machine state. Keep one schema, one deterministic generator and one global validation pass, but store shards by responsible source product or stable core area, under a centrally governed directory. That gives smaller review diffs and fewer unrelated merge conflicts without separate enforcement systems. Sharding must not change debt identity or let moves between shards reset history. Record the target product too; derive team attribution from ownership files instead of rewriting every row when teams change.

Do not shard per file or per current team: those are unstable units. Do not replace identities with summaries: generate counts and reports from the ledger.

Keep human exception rationales separate from generated debt. But reject the blanket idea that every exception addition is architecturally bad: a justified sanctioned integration may reveal missing policy. Temporary violations belong in exceptions; enduring legitimate patterns belong in the architecture.

### The strongest argument against this recommendation

Its complexity. Semantic identities, transfer handling, policy migrations, graph invalidation and separate test eligibility could turn hoglint into another substantial system that needs permanent maintenance. The mitigation: launch fewer trustworthy rules, and support ordinary renames before sophisticated transfers. If move friction proves too expensive, narrower count buckets may be a reasonable compromise, but then the guarantee must be described honestly as "no net increase within these buckets", not "products cannot get worse".

## What was taken

From the decision log (`docs/history/decision-log-3.md`, Nov 11):

- **Agreement with the architect's view:** a precise per-identity ledger; no baseline of the output as it stood; counts never as the sole protection; zones say where a rule is right, levels recognize progress.
- **The loophole, confirmed in code.** The architect checked the growth module: growth was `after <= before` per growth key, and most growth keys held no source package (model-crossings: `dst.symbol, edge.label`; public-surface: `dst.symbol, edge.kind`; facade-signature: `dst.symbol`). A coupling removed in product A paid for a new one in product B. The promise was "no net increase per target", not "no product gets worse".
- **D67 (proposed by the architect after the second opinion; decided by the owner, Nov 11): growth is judged per product.** Every rule's growth key gains the source package, so a coupling removed in one product no longer pays for a new one in another. Moves inside a product stay free; a move across products goes through move admission. To build: a growth key may name `src.package` also where the identity holds `src.module`.
- **Decided by the architect:** the count-based ledger is withdrawn; a rule gets an off state; the hook's messages never suggest regenerating the ledger.
- **D66 (decided by the owner, Nov 11), and how it is met:** the ledger must not grandfather noise. It is met at the rules, before the first ledger write (defects fixed, rule settings changed), not by weakening the ledger. A ledger line means real debt someone chose to carry.
- **On the scoping question:** rules already select on zone, tier, context and shapes per export (`facade-signature` selects `src.zone == 'facade'`). Selecting on a product's level or claimed maturity was not built.
- **Recorded, not decided:** the scope test ("would this code remain acceptable here after the migration is finished?"); the three rule states (off, experimental, enforced) with promotion by hand and a review date; that a ratchet preserves and does not cause improvement; one logical ledger stored in shards per source product; semantic policy diffs because config changes are not neutral; "boundary rules passed" kept apart from "safe to narrow tests"; launching fewer, trustworthy rules.
- **A rule for the architect, recorded the same day:** before proposing a change to a decided design, read the decision and its alternatives first, and say in the proposal what was decided before and why.
