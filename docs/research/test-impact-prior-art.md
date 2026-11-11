# Test impact analysis: what published systems do (research, November 2026)

A boundary linter holds a graph of the code. It is tempting to let that graph answer a second question: which tests does this change affect? This page records what published systems and studies say about that question, so that the answer is designed with their lessons and not against them.

Nothing here is built. It is input for a later design.

## Findings

**1. A static import graph alone misses affected tests.**
A 2026 study built a Python dataset with ground truth for the test files each commit affects. A file-level import baseline selected every affected test on 76.6 % of commits. A method that follows the names a test uses (no call graph) did so on 99.6 % of commits, while skipping about 70 % of test files. The open-source static selectors for Python state the same limits themselves: dynamic imports, run-time side effects, fixtures and implicit imports are outside what they see.

**2. Production systems combine several sources of evidence.**
The published selectors that run in CI on large code bases do not rely on one source. A coverage-based selector for a large Django code base takes the union of: tests whose per-test coverage touches a changed file; a static import scan, for files that coverage cannot see; explicit mappings for inputs that are not code; the changed test files; and a list of tests that always run. A selector for a large Ruby code base rejects static analysis for dynamic code and records at run time which files each test opens.

**3. Some changes must force the full suite.**
Code that registers itself at import time has no per-test evidence, because it runs at module load and at application start-up, before any test. The published list of full-suite triggers reads: test configuration files and test plugins; the lockfile and the project file; migrations; settings; the root URL configuration; the application initializer; registries filled at import time (options, feature flags, audit events); signal definitions and receivers; monkey patches; the database router.

**4. Three kinds of test are invisible to both coverage and import graphs.**
- A test that reads source files without running them (for example one that parses every file with `ast`). A source change affects it, and nothing links the two.
- A test that discovers its inputs by listing a folder.
- A test that depends on a file that is not code: a JSON fixture, a schema, a generated artifact.
The published remedy is the same in each system: an always-run list for the first kind, explicit mappings for the other two. One system reports a broken main branch from the first kind before the test joined its always-run list.

**5. The run-time map has its own failure modes.**
It can be stale: the published systems write a new map at the end of each full build and select from the newest completed one. A test that failed before is run again whatever the selection says. A label or switch forces the full suite.
A technical limit of the tooling: with the fast `sys.monitoring` core of coverage.py, a line is credited to the first test that ran it. A map that must name every test per file needs the slower tracer.

**6. Build systems answer the question at package level only.**
Turborepo 2.11 (September 2026) has experimental support for Python through uv workspaces. Its unit is a workspace member and its edges are the declared dependencies; it does not read imports. A Django application under one project file is one node. Of the general build tools, Pants reads Python imports natively, and stops at static imports. No tool that was found models Django signals, registries, URL routing or task names.

**7. Recall has to be measured, and few publish it.**
One browser project computes recall per test suite (the share of failed changes that would still have failed without the suite) and moves only suites with full recall out of the pre-submit run. Commercial tools recommend an observation mode before any test is skipped. No production selector for Python or Django that was found publishes a recall figure.

## What it means for a linter that holds the graph

- The answer from the graph is deterministic: the same change on the same tree gives the same selection. That is its value, and it is one input.
- It must report what it could not resolve, and an unknown must widen the selection, never narrow it.
- Dispatch by name (a query kind, a task name, a signal, a registry key) is where a name-based reach is worth a spike: the graph already holds those names.
- It should never be the only evidence. A design needs run-time evidence from a full run, full-suite triggers for import-time registration, an always-run list for tests that read the code base, and a recall figure from a shadow period before any selection is trusted.

## Sources

- "Names Are All You Need: Effective and Safe Regression Test Selection for Python", ISSTA 2026, arXiv 2605.25356 (2026-05-25): https://arxiv.org/abs/2605.25356
- Sentry, selective testing script (read 2026-11-11): https://github.com/getsentry/sentry/blob/master/.github/workflows/scripts/compute-sentry-selected-tests.py and https://github.com/getsentry/sentry/pull/129556
- Stripe, "Selective test execution at Stripe" (2026-04-09): https://stripe.dev/blog/selective-test-execution-at-stripe-fast-ci-for-a-50m-line-ruby-monorepo
- Turborepo releases (2.11.x, read 2026-11-11): https://github.com/vercel/turborepo/releases
- coverage.py change log, on `sys.monitoring` and contexts: https://coverage.readthedocs.io/en/latest/changes.html
- Chromium, regression test selection analysis (2026-02-18): https://groups.google.com/a/chromium.org/g/chromium-dev/c/mI_FaAL4DeM
- Static selectors for Python and their stated limits: https://github.com/alexpasmantier/snob , https://pypi.org/project/pytest-impacted/ , https://pypi.org/project/pytest-testmon/

Marks: the study, the script, the pull request, the post and the releases page were read for this page. The remaining sources were read by a research pass and not rechecked.
