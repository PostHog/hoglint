# Test impact analysis: what published systems do (research, November 2026)

A boundary linter holds a graph of the code. It is tempting to let that graph answer a second question: which tests does this change affect? This page records what published systems and studies say about that question, so that the answer is designed with their lessons and not against them.

Nothing here is built. It is input for a later design.

## Findings

**1. A static import graph alone misses affected tests, and a safe one saves little.**
A 2026 study built a Python dataset with ground truth for the test files each commit affects. A file-level import baseline selected every affected test on 76.6 % of commits. Made safe with parent package files and recorded dynamic imports, the same baseline was safe on every commit and skipped only 7.4 % of test files, because a test file imports most of the source tree. A method that follows the names a test uses did so on 99.6 % of commits, while skipping about 70 % of test files; it needs run-time records for reflection and dynamic imports, and its dataset holds libraries, no framework application. The open-source static selectors for Python state the same limits themselves: dynamic imports, run-time side effects, fixtures and implicit imports are outside what they see.

**2. Production systems combine several sources of evidence.**
The published selectors that run in CI on large code bases do not rely on one source. A coverage-based selector for a large Django code base takes the union of: tests whose per-test coverage touches a changed file; a static import scan, for files that coverage cannot see; explicit mappings for inputs that are not code; the changed test files; and a list of tests that always run. A selector for a large Ruby code base rejects static analysis for dynamic code and records at run time which files each test opens. Every system with a run-time map also has hand-written rules for what the map cannot see. No source reports a way to prove such a list complete.

**3. Some changes must force the full suite.**
Code that registers itself at import time has no per-test evidence, because it runs at module load and at application start-up, before any test. The published list of full-suite triggers reads: test configuration files and test plugins; the lockfile and the project file; migrations; settings; the root URL configuration; the application initializer; registries filled at import time (options, feature flags, audit events); signal definitions and receivers; monkey patches; the database router.

**4. Three kinds of test are invisible to both coverage and import graphs.**
- A test that reads source files without running them (for example one that parses every file with `ast`). A source change affects it, and nothing links the two.
- A test that discovers its inputs by listing a folder.
- A test that depends on a file that is not code: a JSON fixture, a schema, a generated artifact.
The published remedy is the same in each system: an always-run list for the first kind, explicit mappings for the other two. One system reports a broken main branch from the first kind before the test joined its always-run list.

**5. The run-time map has its own failure modes.**
It can be stale: the published systems write a new map at the end of each full build and select from the newest completed one. A test that failed before is run again whatever the selection says. A label or switch forces the full suite.
A technical limit of the tooling in Python: the standard coverage tool's fast `sys.monitoring` core does not support per-test contexts. A context set in the configuration makes the tool fall back to the slower tracer with a warning. Contexts set through the API, which is what the pytest plugin does, were silently dropped under the fast core until a fix in August 2026: each line was credited to the first test that ran it. One published selector reported a median skip rate of 73 % that fell to about 21 % once the slower tracer was forced. A map that must name every test per file needs the slower tracer, or a collector that re-arms the line events at the start of each test. No current benchmark of the cost of per-test recording in Python was found.

**6. Build systems answer the question at package level, and their soundness comes from the sandbox.**
Turborepo 2.11 (September 2026) has experimental support for Python through uv workspaces. Its unit is a workspace member and its edges are the declared dependencies; it does not read imports. A Django application under one project file is one node. Of the general build tools, Pants reads Python imports natively, and stops at static imports. No tool that was found models Django signals, registries, URL routing or task names.
Where selection from a declared graph is sound, it is because each test runs in a file tree that holds only its declared closure: an undeclared dependency fails loudly. The build systems' own documents say the check is not complete in all cases, and none publishes a miss rate. Their users treat the graph as complete and measure only the selectors they put on top of it.

**7. A boundary that a linter enforces is not a sandbox.**
A linter gives the declaration and the check. All code stays loaded in one process, so a signal receiver or a registry entry behind a boundary still runs for every test. No public account was found of a monolith that skips tests on the strength of linter-enforced boundaries, with numbers. The organizations that built or use boundary linters select tests from observed signals (call graphs, file opens, coverage); the largest public Django monolith with import contracts runs its full suite on every merge. Where a module boundary appears in a published selector, it is the conservative layer: run every test in the touched module. Tests in other modules are chosen from recorded coverage, not dropped because of the boundary. The rule "tests of one module may not exercise another module's internals" is enforced, where it is enforced at all, by starting the test with only that module and its declared dependencies loaded.

**8. No system lets selection be the last check.**
Each system that skips tests before merge runs everything at a defined later point: after approval, in the merge gate, on the main branch, periodically, or at release. The choice is a trade between safety and how late a breakage is seen. The systems that rely on a net after merge also have culprit finding and automatic quarantine of the test that broke. One large project has tried since 2023 to drop its full run after selection and could not produce an accuracy number it trusts, mainly because flaky and infrastructure failures pollute it.

**9. Recall is measured rarely, and only for selectors that observe runs or learn from history.**
Six systems publish a recall or miss figure, all between 85 % and 99.94 %. For those whose method is public, the ground truth came from deliberate full runs or a replay of history, flaky failures were separated first, and the unit was a faulty change or a culprit commit, not a failing test. In one study 38 of 39 apparent misses were flaky tests. One browser project computes recall per test suite and moves only suites with full recall out of the pre-submit run. Commercial tools recommend an observation mode before any test is skipped. No production selector for Python publishes a figure.

**10. Selectors that were removed went for upkeep, not for a missed regression.**
A hosted Python selector, a browser's commit-queue selector, a Java plugin profile and several small teams' coverage-database plugins were taken out of CI between 2023 and 2026. The stated reasons are maintenance cost and a map that could not be kept valid. Several had failed silently in both directions: they ran everything while appearing to select, or selected too little without a warning.

**11. Results from papers shrink on real CI data.**
History-based selection did worse once flaky failures were removed. Set-up overhead cut the savings. Three Java tools that were described as safe had 27 bugs between them. On one data set of 285 million test results, batching changes beat selection without missing failures. Module-level selection saves little while the module graph is dense: in one study of 22 projects every module was selected on 65 % of commits.

**12. Static analysis of dynamic Python is precise and incomplete.**
Source-only call graphs reach about 99 % precision by their authors' figures; the one independent study on real projects found 49 % of the executed call edges in the static graph. Framework dispatch (signals, the application registry, string references to models, settings that name classes, task queues) is modelled by hand or read from a booted process; no tool infers it, and the public models leave most of it out. For test selection the studies favor coarse static units: they come close to dynamic selection, and fine static selection is poor, with reflection as the measured cause of misses. Analyzers that report what they could not resolve as a first-class result are recent; in one study, feeding only the unresolved items to a cheap dynamic pass raised call-graph recall from 76 % to 88 %.

## What it means for a linter that holds the graph

- The answer from the graph is deterministic: the same change on the same tree gives the same selection. That is its value, and it is one input.
- It must report what it could not resolve, and an unknown must widen the selection, never narrow it. "Could not resolve" and "this channel is not modelled" are two different marks.
- An enforced boundary makes a coarse skip plausible. Whether it holds is a fact of a run: which tests outside the boundary execute code behind it. A design that skips on the boundary needs that observation, at least from time to time, and a full run at a defined later point.
- Dispatch by name (a query kind, a task name, a signal, a registry key) is where a name-based reach is worth a spike: the graph already holds those names.
- It should never be the only evidence. A design needs run-time evidence from a full run, full-suite triggers for import-time registration, an always-run list for tests that read the code base, and a recall figure from a shadow period before any selection is trusted. The recall figure needs flaky failures removed first and a faulty change as its unit.
- A selector should say what it did: which rule selected or skipped, and when it fell back. The removed ones failed without saying so.

## Sources

- "Names Are All You Need: Effective and Safe Regression Test Selection for Python", ISSTA 2026, arXiv 2605.25356 (2026-05-25): https://arxiv.org/abs/2605.25356
- Sentry, selective testing script (read 2026-11-11): https://github.com/getsentry/sentry/blob/master/.github/workflows/scripts/compute-sentry-selected-tests.py and https://github.com/getsentry/sentry/pull/129556
- Stripe, "Selective test execution at Stripe" (2026-04-09): https://stripe.dev/blog/selective-test-execution-at-stripe-fast-ci-for-a-50m-line-ruby-monorepo
- Turborepo releases (2.11.x, read 2026-11-11): https://github.com/vercel/turborepo/releases
- coverage.py change log, on `sys.monitoring` and contexts (7.4.0 to 7.15.3): https://coverage.readthedocs.io/en/latest/changes.html ; issues 2150 and 2200: https://github.com/coveragepy/coveragepy/issues/2200
- Bazel, sandboxing, dependencies and the test encyclopedia: https://bazel.build/docs/sandboxing , https://bazel.build/concepts/dependencies , https://bazel.build/reference/test-encyclopedia
- Shopify, "A Packwerk Retrospective" (2024) and "Spark joy by running fewer tests" (2020): https://shopify.engineering/a-packwerk-retrospective , https://shopify.engineering/spark-joy-by-running-fewer-tests
- Adyen, "Test selection at Adyen" (2024-09-23): https://adyen.com/knowledge-hub/test-selection-at-adyen
- Kraken Technologies, "How we ship in 2026" (2026-03-09): https://engineering.kraken.tech/news/2026/03/09/how-we-ship-2026.html
- Nx, affected tasks and recorded file reads: https://nx.dev/docs/features/ci-features/affected , https://nx.dev/docs/features/ci-features/remote-cache
- Spring Modulith, integration testing of application modules: https://docs.spring.io/spring-modulith/reference/testing.html
- Tach, commands and FAQ: https://docs.gauge.sh/usage/commands , https://docs.gauge.sh/usage/faq
- "Understanding and Improving Regression Test Selection in Continuous Integration" (2019): https://par.nsf.gov/servlets/purl/10175678
- Chromium, regression test selection analysis (2026-02-18) and evaluation method: https://groups.google.com/a/chromium.org/g/chromium-dev/c/mI_FaAL4DeM , https://chromium.googlesource.com/infra/luci/luci-go/+/f8d1caba1b92/rts/doc/eval.md
- Mozilla, "Testing Firefox more efficiently with machine learning" (2020): https://hacks.mozilla.org/2020/07/testing-firefox-more-efficiently-with-machine-learning/
- Static selectors for Python and their stated limits: https://github.com/alexpasmantier/snob , https://promptromp.github.io/pytest-impacted/ , https://testmon.org/

Marks: the study's abstract, the selection script, its pull request, the Stripe post, the Turborepo releases page and the coverage.py change log were read for this page. The remaining sources, and the figures in findings 8 to 12 that have no source in this list, come from research passes that read their sources and marked each claim; they were not rechecked for this page.
