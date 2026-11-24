# Prior art, round 2: module systems, registries, and deploying static analysis

> A study of module systems, plugin registries, and how static analysis tools were rolled out, compared against the Hoglint design draft, Nov 9, 2026.
> Partly replaced: the synthesis is `docs/research/prior-art.md`, the claims were checked in `docs/research/prior-art-second-opinion.md`, and the exceptions and registry design is in `docs/design/02-policy-exceptions-ledger.md` and `docs/design/09-contract-surfaces.md`.

Tags: [src: URL] means the study read that source.
[src-summary: URL] means it saw only a search summary of it.
[mem] means memory, not checked here.
This pass does not repeat Packwerk, Kraken, ArchUnit, Bazel, Nx and Sentry from `docs/research/research-round-1-prior-art.md`.
"The draft" is the Hoglint design draft of that date (not published).

## 1. Items: what they do and what went wrong

### Line A: module systems and registries

- **JPMS (`exports ... to`, `opens`, `--add-opens`).** Modules declare what they export, and they declare `opens` separately for deep reflection. JDK 9 shipped with `--illegal-access=permit` as the default "as an aid to migration". That default stayed through JDK 15, became `deny` in 16, and turned into a no-op in 17. Only per-package `--add-opens` or the `Add-Opens` JAR attribute remains [src: https://openjdk.org/jeps/403]. What went wrong: library maintainers "have been telling application developers that illegal reflective-access warnings can safely be ignored" [src: https://openjdk.org/jeps/396]. Library authors saw "negative benefits": the module path and the class path diverged, and a modular jar placed on the class path loses every check [src: https://blog.joda.org/2018/03/jpms-negative-benefits.html]. A 2021 partial scan of Maven Central found 4,691 jars with `module-info.class` and 10,276 with only `Automatic-Module-Name` [src: https://foojay.io/today/modules-modules-everywhere].
- **ServiceLoader.** On the class path, providers are listed by class name in `META-INF/services` text files. In modules, the consumer declares `uses` and the provider declares `provides ... with` [src: https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/ServiceLoader.html]. `stream()` gives provider types without instantiating them, which is lazy by design [src: same]. What went wrong: the text-file form is unchecked strings, so stale entries fail at runtime [mem]. `provides` is checked by the compiler [mem].
- **OSGi.** Bundles declare `Import-Package` with version ranges, plus `uses` constraints. What went wrong: resolver failures ("uses conflicts") are hard to diagnose and can depend on install order [src-summary: https://spring.io/blog/2008/11/22/diagnosing-osgi-uses-conflicts]. In one Equinox case, a maintainer traced the bad ranges to hand-written manifests that bnd would not have generated [src-summary: https://www.eclipse.org/lists/equinox-dev/msg08668.html]. The OSGi practice that held up was generating manifests from bytecode with bnd [mem].
- **Eclipse extension points (`plugin.xml`).** The declarative part is loaded into a registry at startup, and plugin code activates only when used. Plugin authors were warned that cross-plugin references at startup cascade into loading "large amounts of unneeded code" [src-summary: https://wiki.eclipse.org/FAQ_When_does_a_plug-in_get_started%3F]. What went wrong: class names written as strings in XML drift during refactors [mem].
- **Zope ZCML, Grok, Pyramid and venusian.** Grok replaced explicit XML (ZCML) with convention, read from Python by martian [src-summary: https://blog.startifact.com/posts/implementing-grok/]. Venusian, martian's successor, defers decorators to a scan that "would recursively import each module in the package", with `ignore` patterns to skip tests [src: https://docs.pylonsproject.org/projects/venusian/en/latest/]. Pyramid argues "Python programmers do not own the module scope code path", prefers imperative configuration, and calls the ZCA registry API "sometimes ... just plain obtuse" [src: https://docs.pylonsproject.org/projects/pyramid/en/latest/designdefense.html]. Discovery by import costs startup, causes ordering bugs, and hides wiring.
- **Django autodiscover.** Admin autodiscovers `admin` modules. The docs warn about `AppRegistryNotReady`, import loops, and `ready()` running twice in tests [src: https://docs.djangoproject.com/en/5.2/ref/applications/]. The same import-time side effects as venusian.
- **.NET `InternalsVisibleTo`.** The provider assembly names its friend assemblies. Unit tests are the first listed use. The grant covers all internals and is not transitive [src: https://learn.microsoft.com/en-us/dotnet/standard/assembly/friend]. What went wrong: the grant is all or nothing, so test access opens every internal [mem].
- **Kotlin `internal`.** `@Suppress("INVISIBLE_REFERENCE")` calls another module's internals. The compiler team plans to prohibit suppressing errors [src-summary: https://slack-chats.kotlinlang.org/t/501553/hello-there-i-ve-heard-that-the-plan-is-to-eventually-disall].
- **Python entry points.** A distribution advertises plugins in a group in its packaging metadata [src: https://packaging.python.org/en/latest/specifications/entry-points/]. The declaration is static, and consumers read it without importing the plugin. What went wrong: `pkg_resources` scanned every installed distribution on each startup [src-summary: https://gitlab.com/BuildStream/buildstream/issues/172]. Entry points are recorded at install time, so a new one needs a reinstall in dev [mem].
- **Sorbet packages (Stripe), verified in the repo.** `__package.rb` supports `import` (with `uses_internals:`), `export`, `export_all!`, `visible_to`, `strict_dependencies` (`'false'`, `'layered'`, `'layered_dag'`, `'dag'`), `layer`, `prelude!`, `test!`, and a minimum `typed` level [src: https://github.com/sorbet/sorbet/blob/master/rbi/sorbet/packages.rbi]. Only `test!` packages may use `uses_internals: true`. A `layered` or stricter package may import only `layered` or stricter packages [src: https://github.com/sorbet/sorbet/blob/master/website/docs/error-reference.md]. Errors 3508 and 3509 require syntactic literals "so that Sorbet can analyze it without performing any inference" [src: same]. Escape hatches: the CLI flag `--allow-relaxed-packager-checks-for <name>`, and `--gen-packages`, which autocorrects missing imports into the manifest [src: https://github.com/sorbet/sorbet/blob/master/website/docs/cli-ref.md]. No public post-mortem exists. Stripe's own docs are internal. So "what went wrong" is unverified. The manifest needing a generator is itself a signal.
- **GitLab bounded contexts.** A working group in 2023-24 mapped namespaces. A RuboCop cop now enforces top-level namespaces from `config/bounded_contexts.yml`, with existing offenses in the TODO file [src-summary: https://docs.gitlab.com/development/software_design]. A Packwerk proof of concept found that enabled checks "will log a lot of violations", and that each package first needs a "vision" so developers know how to fix them [src: https://gitlab.com/gitlab-com/content-sites/handbook/-/raw/main/content/handbook/engineering/architecture/design-documents/modular_monolith/proof_of_concepts.md]. They state that "the main challenge is organizational, rather than technical", and ADR-003 assigns stewards to every module [src: https://gitlab.com/gitlab-com/content-sites/handbook/-/raw/main/content/handbook/engineering/architecture/design-documents/modular_monolith/_index.md]. The design docs are still "proposed".

### Line B: deploying static analysis

- **Coverity, "A Few Billion Lines of Code Later" (2010).** Deliberately unsound; skipped hard code. Above 30% false positives "people ignore the tool", and distrust spreads: "If people don't understand an error, they label it false". Users want "the same result from run to run", "even if they upgraded the tool", so churn is capped under 5% per release. An upgrade from 2,400 to 3,600 errors left a manager "furious"; customers chose upgrade models like "Never before a release". Laws: "No Check = No Bug", "You can't check code you don't see" [src: https://web.stanford.edu/~engler/BLOC-coverity.pdf].
- **Google, "Lessons from Building Static Analysis Tools at Google" (2018).** FindBugs failed three times: a nightly dashboard outside the workflow; hand-filed bugs (16% of reviewed warnings fixed in a fixit week); review comments, where false positives and "developer customization resulted in an inconsistent view". What worked: compiler errors with "no effective false positives", only after a codebase-wide cleanup (ClangMR, JavacFlume, Rosie); review checks with up to 10% effective false positives; a "Not useful" click files a bug to the analyzer's owners, and above 10% Tricorder "disables the analyzer until the author(s) improve it". Warnings were ignored, so Clang used errors. "No mandate from management" [src: https://storage.googleapis.com/gweb-research2023-media/pubtools/4365.pdf].
- **Error Prone.** A default ERROR check needs "no false positives" [src: https://errorprone.info/docs/criteria].
- **Facebook Infer (2019).** A nightly batch got "near silence" and a fix rate near zero, at under 20% false positives. The same analysis at diff time reached over 70%, because of context switching and relevance. Issue identity is a location-independent hash [src: https://6826.csail.mit.edu/2020/papers/facebook-infer-cacm.pdf].
- **SonarQube new code.** The quality gate judges only new code, with four ways to define it: previous version, X days, a reference branch, or a specific analysis. The leak: an unfixed new-code issue "becomes part of the overall code" after 30 days [src: https://docs.sonarsource.com/sonarqube-server/latest/user-guide/about-new-code/].
- **ESLint bulk suppressions.** These work much like Hoglint's ledger. `eslint-suppressions.json` holds existing violations, fixed violations fail with "unused suppressions" until you run `--prune-suppressions`, and `--pass-on-unpruned-suppressions` turns that off [src: https://eslint.org/docs/latest/use/suppressions].

## 2. Escape hatches: how they spread, what contained them

**How they spread:** a permissive global default with warnings (JDK 9 `permit` for four releases, warnings ignored; Clang saw the same and switched to errors); hatches in places nobody reviews (`--add-opens` in launch scripts [mem], Sorbet's CLI relax flag, per-user FindBugs settings); all-or-nothing grants (`InternalsVisibleTo`, `export_all!`); debt files that only grow (Packwerk) or age out (Sonar's 30 days).

**What contained them:** narrow grants (per package `--add-opens`, per audience `exports ... to`, `visible_to`); grants written by the provider, not the caller (all three of those); purpose-limited grants (Sorbet: `uses_internals` only from `test!` packages); scheduled default flips (JDK 9 permit, 16 deny, 17 obsolete); prune on fix (ESLint); one shared configuration (Tricorder, after per-user settings failed).

Compared with hoglint:
- The exceptions file matches one (src, dst, shape) entry. That is narrow, a good fit.
- It sits in a reviewed file with reason, owners and `review_by`, which is better than CLI flags.
- The ledger has prune on fix through the equality check.
- Gaps:
  - The tool's owners approve exceptions, not the owners of the target package. The prior art puts the grant with the provider.
  - Nothing says what happens on `review_by`. Sonar shows that silent aging launders debt. Failing every PR on that date repeats the Coverity churn problem.
  - Nothing limits an exception's purpose. Sorbet allows test-only internals, while Hoglint says "test path alone never grants exemptions" and leaves no reviewed test-scoped grant.
- The proposal that relaxations in `hoglint.toml` need review by the tool's owners is right. It is the lesson of `--illegal-access` and the Sorbet relax flag: a global relaxation is the hatch that spreads. Judging a relaxation by its effect on base code goes further than any system above did.

## 3. Registration: convention or declared manifest

What the history says:
- **Static tooling:** both forms work if the tool can read them without running code. Eclipse `plugin.xml`, entry points, `provides`, and `__package.rb` with literals (Sorbet 3509) all qualify. So does a fixed file path with a literal attribute. What fails is discovery by import: venusian scan, Django autodiscover, and `pkgutil` walks.
- **Lazy loading:** declarations win when the runtime can read them without importing the provider (Eclipse, entry points, `ServiceLoader.stream()`). A file-name convention also allows lazy loading, because the loader can list paths without importing them. The laziness is lost only if the loader imports every match to read the attribute. That is what PostHog's `load_product_modules` did at the time.
- **Drift:** hand-written manifests rot. Examples are unchecked `META-INF/services` strings, hand-written OSGi ranges, and Sorbet needing `--gen-packages` to keep imports current. Manifests stay healthy when a tool generates them or checks them (bnd, `provides` checked by the compiler).
- **Readability:** Pyramid and Grok went back and forth. Explicit ZCML was too heavy, conventions hid the wiring, and Pyramid ended up preferring imperative configuration in one visible place. JPMS made both sides explicit: the consumer writes `uses`, the provider writes `provides`.

**Recommendation.** Keep the file-name convention as the only source of membership.
`products/{p}/backend/<registry>.py` plays the role of `provides`.
Keep the registry declared once in the preset (loader, module, attribute, shape), which plays the role of `uses`.
Add three rules:
1. The registry attribute must be a literal list of names that Hoglint resolves without importing (Sorbet's literal rule). Anything else makes the registry incomplete.
2. A file that matches a convention path but no declared registry is a config error, and so is a registry with no loader call.
3. `hoglint explain <registry>` lists the members, so readers do not need to know the convention.

If lazy startup later needs an index, generate it as a projection and check it for equality, as the ledger is checked.
Do not hand-write it in `product.yaml`.
That keeps `product.yaml` ownership-only (the draft) and avoids two sources of truth.
The second opinion's manifest option (`docs/research/exports-and-shapes-second-opinion.md`, round 4) is sound only if Hoglint also verifies it against the files, and then the files already carry the information.

## 4. Soundness and noise; who owns an incomplete result

Coverity users rejected result changes they did not cause, even from tool upgrades.
Google allows a blocking check no effective false positives and routes noise to the analyzer's owners.
Infer shows reports get acted on only when they concern the change in hand.
To a PR author, an incomplete result their PR did not cause is an effective false positive; blocking on it is a compiler error that is 100% false for that PR.

So unknown should never be clean for the claim it affects: Isolated and test skipping.
The safe failure there costs CI time, not a blocked developer.
That half of Hoglint is right and matches "No Check = No Bug".
For merge blocking, attribute incompleteness like growth:
- **Introduced by the PR:** a new dynamic-import fact, an unresolved name in changed code, or a new registry without a loader. The author owns it, and it blocks.
- **Inherited from the base or caused by a scanner or policy bump:** the tool owner owns it, Tricorder style. It does not block. It removes Isolated for the affected products, so the full suite runs, and it pages or files to the tool owner with a fix-by target.
- A scanner or parser bump must land with incomplete-delta = 0 on master. That is Coverity's churn cap, enforced at the one PR that changes the tool.

## 5. Repeat, avoid, partly avoid

**Avoids:** privacy by directory (exports with a mandatory shape, the draft); stale baselines (ledger equality, the draft); upgrade churn counted as code growth (rule growth passes, the draft); line-number identity (like Infer's hash); per-user configuration (one root config); a restriction with no benefit (JPMS "negative benefits"; Isolated pays back in skipped tests); fixes nobody can find (GitLab's "vision" problem; findings carry remedies).

**Partly avoids**
- Unknown is never clean (`docs/research/user-guide.md`, run result; the draft "Who owns an incomplete result"). Right for Isolated. Not yet attributed for blocking.
- Exit 2 wins over exit 1 (the draft). If CI makes inherited incompleteness non-blocking, the precedence hides a real violation. The exit status must come from attributed findings, not from precedence.
- Exceptions (user guide, the draft) are narrow and reviewed. But the tool owner grants them instead of the target's owners, `review_by` has no defined behavior, and there is no test-scoped grant.
- Shrink caused by a rule change (the draft, open). This is Coverity's manager problem in reverse: "debt went down" without code changing. It needs its own reviewed line in `compare`.

**Repeats**
- `hoglint query --by owners` as the burn-down surface is FindBugs' dashboard, which saw "little use", and Infer's batch mode with a fix rate near zero. Nothing puts existing ledger lines in front of the person editing that code.
- One central human gate for every exception, relaxation and move admission (the draft, user guide "Moves"). Google had "no mandate from management", and GitLab calls the problem organizational. A single tool-owner queue becomes the hatch that gets rubber-stamped or the bottleneck. This is the study's inference, not a documented failure.

**Changes, ranked**
1. Attribute incompleteness the way growth is attributed. PR-introduced unknowns block. Inherited unknowns drop Isolated, route to the tool owner, and never block. Compute the exit status from attributed results, so exit 2 cannot hide exit 1.
2. Pin the Hoglint version and require scanner or policy bumps to show incomplete-delta = 0, plus listed rule growth or shrink, on master. Treat shrink without a code change as a relaxation that needs review.
3. Add a noise budget per rule: count exception requests and "not useful" marks. Above a threshold, demote the rule to advisory until it is fixed (Tricorder's 10%).
4. Exceptions:
   - Approval from the owners of the target package (CODEOWNERS on the dst package), with the tool owner only for policy.
   - A test-only scope, like Sorbet's `uses_internals`.
   - Defined expiry: an expired entry becomes a finding routed to its owners and shown in their PRs. It never ages silently and never blocks unrelated PRs.
5. Registries: convention plus literal attribute plus registry declared in the preset, as in the draft. Generate any index; keep `product.yaml` ownership-only.
6. Diff-time debt: in `compare`, show advisory ledger lines for modules the PR touches (Sonar-style "touch it, see it"). Keep the owners dashboard secondary.
7. Spread review across people. Give each product a steward (GitLab ADR-003) who approves exceptions into it. The tool owner keeps relaxations of the preset.

## Gaps

Not verified: Sorbet packages in practice at Stripe (no public account).
OSGi, Eclipse, Grok, Kotlin and GitLab's software-design page were seen only as search summaries.
`InternalsVisibleTo` over-granting and the drift in `META-INF/services` and `plugin.xml` are from memory.
