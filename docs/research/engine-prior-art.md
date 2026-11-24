# Engine prior art: native core, scripting layer, and what to compute

> A prior-art study for the engine round, Nov 9, 2026: how Python-fronted tools split work between a native core and a scripting layer, how demand-driven engines handle negation and absence, what Python objects cost, and how rules run over a native fact store.
> Partly replaced: the decisions it fed are in `docs/history/decision-log.md` (D1, D2, D5, D8) and the engine as built is in `docs/design/05-engine.md`. The evidence itself is current as of Nov 9, 2026.

**Tags.**
[src: URL] means the source was read during the study.
[mem] means from memory, not checked.
[run] means measured during the study.
"Computed" means a [run] rate times a row count from the workload notes of the PostHog monorepo; it is not a PostHog measurement.

**Scope.** This study fills gaps in two earlier ones.
An earlier caching study (not published) covers caching and incrementality.
`docs/research/prior-art-fact-stores.md` covers fact stores.
Neither is repeated here.
Sections 3 and 4 restate claims tagged in section 2 and point back with [§2.x].

**[run] setup.** A developer laptop (macOS).

- Corpus: Django 5.2.8 (883 files, 5.7 MB), on CPython 3.12.14.
- Row and GC tests: CPython 3.14.7. Engine tests: CPython 3.13, polars 2.1.0, duckdb 2.0.0, numpy 2.5.4, pyarrow 26.0.1, pydantic 2.14.3.
- Single-thread timings moved 30 to 40% between runs, so ranges are given.

## 1. Short answer

1. Fast Python-fronted tools moved data and hot loops native and kept crossings coarse. grimp's batch queries got 3.3x to 13,000x faster while one small call got 30x slower.
2. Per-node Python objects erase a native parser's gain: LibCST parses in Rust yet runs 11.4x slower than `ast.parse` on Django [run].
3. Native-only tools (ruff, pks, Pyrefly, tach) have no plugins, so framework knowledge moves into the core: Pyrefly builds in static Django support; pks rewrote Zeitwerk inflection in Rust.
4. Tools that keep a scripting layer restrict it: pure, hashable Pants rules; frozen Starlark; pattern DSLs (semgrep, GritQL, ast-grep); oxlint's lazily read raw AST.
5. The scripted half that compiles the native program can dominate: pydantic's schema builder took 14 s and 9 GB before 2.11; Pants' type-driven rule solver hit limits.
6. Demand: dropping relations that no output reads is free. Magic sets equal full evaluation only without negation, so Soufflé computes negated relations in full. Pyrefly solves whole modules, not identifiers.
7. Absence: Datalog, QL and SQL read a missing fact as false, and NULL logic drops rows silently [run]. Tools that care store unknown as a value (CodeQL `TUnknown`, rustc `ErrorGuaranteed`, Skyframe errors).
8. semgrep shows the cost of not doing so: it reported findings in failed files as "fixed" until Dec 2025.
9. Object tax: about 2.5M fact rows take 0.42 to 0.72 GB as Python objects and about 40 MB as columns (computed). A frozen slots dataclass builds 5x slower than a tuple [run].
10. GC hacks (thresholds, `gc.freeze`) gave mypy about 30% on one CPython and regressed on 3.14.
11. Rule cost per row [run]: compiled predicates (Polars, numpy, DuckDB SQL) 1 to 2 ns; a plain Python loop about 50 ns; a Python callback inside a native engine 64 to 380 ns.
12. Every debugger found (`explain`, `EXPLAIN`, provenance, Quick Evaluation) shows what was computed. None shows why something is absent.

## 2. Tool by tool

### 2.0 Overview of the native/scripting split

| Tool | Native | Scripted | What crosses | Extensions | Measured gain |
|---|---|---|---|---|---|
| grimp | parse, scan, graph, queries | discovery, cache adapter, API, import-linter contracts | Python objects per import at build; coarse queries after | Python over `ImportGraph` | layers contract 123 s to 27 s; queries 3.3x to 13,000x |
| tach | almost all | CLI, TUI, git, pytest plugin | config pyclass in, diagnostic pyclasses out | none | 18.8x imports; 16.5 s to 1.85 s sync |
| pks | all | none | YAML files in and out | none | 2.2 s vs 21 to 30 s |
| ruff | all | none | n/a | none (issue open since 2022) | n/a here |
| pydantic-core | validator tree, JSON | schema builder, custom validators | schema dicts once; Python objects read in place | Python callables | 4x to 50x vs v1 |
| Pyrefly | all | none | n/a | none; Django built in | Instagram 13.4 s vs 100+ s |
| mypyc | compiled Python | the rest | normal Python calls | plain Python | 1.5x to 5x; mypy 4x; Black 2x |
| LibCST | tokenizer, parser | every node, visitors, codemods | the whole tree as Python objects | Python visitors | slower than `ast` [run] |
| semgrep | parse, match, taint | rule loading, plan, output (being ported) | subprocess, rule shards and JSON | YAML patterns | n/a |
| Gigahorse | Soufflé Datalog | fact generation, driver | relational files | Datalog or any language | n/a |
| Pants | graph, memo, store, exec | all rules | `@rule` calls via PyO3 | pure Python rules | n/a |
| oxlint (contrast) | all built-in rules | JS plugins | raw memory layout, lazy | ESLint-style JS | 48 ms native, 236 ms with one JS rule |

### 2.1 Native core and scripting layer

#### grimp (import-linter's graph library)

- **Native:** import parsing (ruff's parser), file scanning on Rust threads, the graph, and every graph query: chains, layers, upstream and downstream sets, cycle breakers [src: https://github.com/python-grimp/grimp/blob/main/rust/src/lib.rs, rust/src/graph/mod.rs].
- **Scripted:** package and module discovery, the cache adapter, the `build_graph` use case, and the public `ImportGraph` API. import-linter contracts, including custom contract types, are Python over that API [src: https://github.com/python-grimp/grimp/blob/main/src/grimp/application/usecases.py].
- **Layout:** module names live in a process-wide string interner. Modules are `SlotMap` keys. Imports are adjacency sets of keys, plus a details map keyed by `(importer, imported)` [src: rust/src/graph/mod.rs].
- **Crossing:** the Rust scanner builds Python `Module` and `DirectImport` value objects. Python then loops and calls `graph.add_module` and `graph.add_import` once per import. So each import crosses twice as a Python object [src: rust/src/import_scanning.rs `to_py_direct_imports`; usecases.py `_assemble_graph`]. The cache is one JSON file per package set; Rust reads and writes it but returns Python dicts [src: rust/src/caching.rs].
- **Extensions:** Python code against `ImportGraph`. The fast path is a batch query that runs whole in Rust, such as `find_illegal_dependencies_for_layers`.
- **What went wrong:**
  - The first Rust layers PR (2023) cut one contract from 123 s to 27 s [src: https://github.com/python-grimp/grimp/pull/120].
  - The Rust graph PR (Feb 2025) made batch queries 3.3x to 13,000x faster. It also made a fine-grained call 30x slower: `test_no_chain` went from 39.7 µs to 1,184 µs. Reading the `modules` property became a 34 ms benchmark of its own [src: CodSpeed table on https://github.com/python-grimp/grimp/pull/166].
  - Moving the import scanner to Rust while keeping the Python ports and fake filesystems first made things 18% slower on CodSpeed [src: https://github.com/python-grimp/grimp/pull/229]. A bulk Rust scan that broke the fake-filesystem tests showed a 77% regression on one benchmark [src: https://github.com/python-grimp/grimp/pull/222].
  - Grimp 3.10 was yanked: a real large graph went from 4.6 s to 108 s to build [src: https://github.com/python-grimp/grimp/issues/241]. 3.11 fixed it with Rust threads instead of joblib processes [src: https://github.com/python-grimp/grimp/pull/236].
  - An earlier multiprocessing PR helped one repo (37 s to 8 s) and slowed another from 0.6 s to 35 s [src: https://github.com/python-grimp/grimp/pull/142].
  - A full Rust `build_graph` demo was closed. The author could not keep the fake-filesystem test approach "without compromising performance" [src: https://github.com/python-grimp/grimp/pull/263].
- **Gains:** on a 65,757-module, 221,797-import repo: Python build 3.13 s, full-Rust demo 1.93 s cold and 0.97 s warm [src: PR #263 body]. Uncached build on a very large graph: about 11 s to about 8 s with Rust threads [src: PR #236].
- **[run]** grimp 3.19 on Django 5.2.8 (883 modules, 3,043 internal imports), Python 3.12: `build_graph` uncached 0.07 s. `find_modules_that_directly_import` called once per module: 0.9 µs per call. `find_upstream_modules("django.db.models")`: 0.04 ms.

#### tach

- **Native:** config parsing, import extraction (ruff's parser), the check itself, the dependency map, diagnostics formatting and JSON output, the computation cache key, and an LSP server [src: https://github.com/gauge-sh/tach/blob/main/src/lib.rs].
- **Scripted:** the CLI (1,329 lines), the interactive TUI, git operations, and the pytest plugin [src: https://github.com/gauge-sh/tach/tree/main/python/tach].
- **Crossing:** coarse. Python passes a `ProjectConfig` pyclass and a path. Rust returns a list of `Diagnostic` pyclasses, and Rust also formats them [src: src/lib.rs `check_internal`, `format_diagnostics`; python/tach/cli.py].
- **Extensions:** none. Behavior comes from `tach.toml` only.
- **History and gains:**
  - v0.2.6 (May 2024) added a Rust extension to a Python tool [src: https://github.com/gauge-sh/tach/releases/tag/v0.2.6].
  - Moving `get_project_imports` to Rust gave 18.8x on Sentry (about 3,000 files). The profile showed most time in "simply _visiting_ all the nodes" and building ASTs [src: https://github.com/gauge-sh/tach/pull/82].
  - Rayon over files: `tach sync` went from 16.5 s to 1.85 s on a 10M-line repo [src: https://github.com/gauge-sh/tach/pull/533].
  - The rest moved piece by piece, for example `check_external` in v0.25.6 [src: https://github.com/gauge-sh/tach/pull/616].
- **What went wrong:**
  - Unknown imports were classified, not reported: "wildcard import always treated as external" (fixed in v0.15.4) [src: https://github.com/gauge-sh/tach/releases/tag/v0.15.4].
  - The vendor team stopped. The repo moved to `tach-org`, and the docs now say "Tach now only has one maintainer" [src: https://github.com/gauge-sh/tach/blob/main/docs/contributing/making-changes.md; release notes v0.32+].
- **Workload fact:** tach builds PostHog's module import map in 2.7 to 3.6 s (workload notes).

#### pks / packs (Rust packwerk)

- **Native:** everything. "A 100% Rust implementation of packwerk" [src: https://github.com/alexevanczuk/packs/blob/main/README.md].
- **Scripted:** nothing. Ruby is not involved at run time.
- **Crossing:** files only. pks reads `packwerk.yml` and `package.yml`. It writes `package_todo.yml` in packwerk's format. It keeps a per-file cache keyed by an MD5 of the path, holding a content digest [src: src/packs/caching/mod.rs, per_file_cache.rs].
- **Extensions:** none. "Not yet supported: custom inflections, custom load paths, extensible plugin system" [src: README]. The packwerk-extensions checkers (privacy, visibility, layer, folder privacy) were rewritten in Rust [src: src/packs/checker/].
- **What went wrong:**
  - Framework meaning was copied into native code. Zeitwerk naming lives in an `inflector_shim.rs`, and acronym namespaces are "not yet supported" [src: src/packs/parsing/ruby/; README].
  - Packwerk "can only resolve references to constants that are defined in code loaded by the application's Zeitwerk autoloader" [src: https://github.com/Shopify/packwerk].
  - Shopify removed privacy checks in packwerk 3.0. They had made packwerk "an API design tool" [src: https://railsatscale.com/2024-01-26-a-packwerk-retrospective/].
  - pks proves equivalence with an oracle, not by construction: "Run `packs update` ... Confirm the output of `git diff` is empty" [src: README].
- **Gains:** whole codebase, hot cache: pks 2.2 s vs packwerk 21.4 to 29.7 s. Single file: 0.58 s vs 6.7 to 16.7 s [src: https://github.com/alexevanczuk/packs/blob/main/BENCHMARKS.md].

#### ruff (and why no Python plugins)

- **Native:** everything. Rules are Rust modules inside the binary.
- **Extensions:** none. The plugin meta issue has been open since 2022, with 102 comments [src: https://github.com/astral-sh/ruff/issues/283].
- **Why, in the maintainers' words** [src: same issue]:
  - A Ruff maintainer (2022): plugins in Rust would "maintain a focus on performance, and avoid requiring extensive cross-language FFI". Rust has no stable ABI.
  - Shared logic: rules share visibility tracking and docstring detection. A plugin API would make it "harder to anticipate all the data a plugin might need".
  - Options named: a DSL like ast-grep, or a Rust or Python API like ESLint's. In 2024 the team named GritQL as the leading candidate, after the multi-file rewrite.
- **What the JS world did instead (contrast):**
  - oxlint added ESLint-compatible JS plugins. It passes the AST by "raw transfer", which uses "Rust's native memory layout as the serialization format" and deserializes lazily [src: https://oxc.rs/blog/2025-10-09-oxlint-js-plugins].
  - Numbers: vuejs/core lint took 48 ms native-only, 236 ms with one custom JS plugin, and 4,116 ms with ESLint [src: same]. Later: Node.js repo, 104 Rust rules plus 98 JS rules, 21 s vs ESLint's 1 min 43 s [src: https://oxc.rs/blog/2026-03-11-oxlint-js-plugins-alpha].
  - Their stated reason: without raw transfer, "the cost of sending data back and forth between Rust and JS can be so high that it offsets that gain" [src: same].
  - Biome ships GritQL plugins and, in developer preview, JS plugins. GritQL "plugins run on every file, regardless of whether the content of the file matches the query". For JS plugins, "the memory used by the plugins can't be controlled by Biome" [src: https://biomejs.dev/linter/plugins/].

#### pydantic-core

- **Native:** a tree of small validators and serializers, built once from a "core schema", plus a native JSON parser [src: https://pydantic.dev/articles/pydantic-v2.md].
- **Scripted:** `GenerateSchema` reads type hints and builds the core schema as nested Python dicts. Custom validators are Python callables.
- **Crossing:**
  - The schema crosses once, as dicts, when a `SchemaValidator` is built. Validation reads Python objects in place through PyO3.
  - `model_validate_json` keeps JSON on the native side. The two-step form parses JSON in Python, "then converted to a dict, then it's validated internally" [src: https://pydantic.dev/docs/validation/latest/concepts/performance/].
  - Wrap validators "require that data is materialized in Python during validation" [src: same].
- **Extensions:** Python function validators (before, after, wrap, plain). There is no native plugin API.
- **What went wrong:** the Python side that compiles the schema became the cost. On a Kubernetes model file: v2.7.2 took 14.06 s to start and allocated 8.95 GB; v2.11.0 took 1.52 s and allocated 311 MB [src: https://pydantic.dev/articles/pydantic-v2-11-release]. The fixes were all on the Python side: lazy annotation evaluation, schema cleaning, and reusing prebuilt `SchemaValidator`s [src: same]. "Each time a `TypeAdapter` is instantiated, it will construct a new validator" [src: performance docs].
- **Gains:** "between 4x and 50x faster than pydantic V1.9.1"; about 17x for a model with common fields [src: pydantic-v2 article].
- **Note:** the standalone `pydantic-core` repo is archived as of Jan 2026 [src: GitHub API `repos/pydantic/pydantic-core`]. The study did not check where the code moved.

#### Pyrefly

- **Native:** everything, in Rust. Pyrefly replaces Pyre (OCaml) for Instagram.
- **Extensions:** none. Pyrefly "does not expose mypy's plugin API or execute arbitrary mypy plugin modules". Pydantic, Django and attrs support is built in [src: https://pyrefly.org/en/docs/migrate/mypy/plugins/].
- **Framework meaning:** Django support "is static". It works "rather than running Django or introspecting settings". It "follows stubs and models the code" [src: same]. The docs warn that "some relationship-manager and highly dynamic metaclass cases can produce different types" [src: same].
- **Design:** three steps: exports per module, then bindings per module in isolation, then solving. Pyrefly does "not try and solve a specific identifier first (like Roslyn or TypeScript)". It does "not use fine-grained incrementality (like Rust Analyzer using Salsa)". It aims "for raw performance and a simpler module-centric design" [src: https://github.com/facebook/pyrefly/blob/main/ARCHITECTURE.md].
- **Lesson from Pyre:** "Designing these abstractions from the beginning is much easier than retrofitting them, which we tried with Pyre" [src: https://pyrefly.org/blog/introducing-pyrefly/].
- **Gains:** "1.8 million lines of code per second" [src: same]. Meta's benchmark: the whole Instagram codebase in 13.4 s, "compared to 100+ seconds with Pyre" [src: https://www.infoq.com/news/2025/05/meta-pyrefly-python-typechecker, reporting Meta's numbers].

#### mypyc (compile Python instead of rewriting it)

- **What it does:** compiles type-annotated Python to C extensions. It uses early binding, native classes with vtables, and runtime checks of annotations at the "boundaries between dynamic and static typing" [src: https://mypyc.readthedocs.io/en/latest/introduction.html].
- **Gains:** annotated code is "often 1.5x to 5x faster"; tuned code is "5x to 10x". mypy itself got 4x [src: same]. Black got 2x overall, and about 50% on files it changes [src: https://sichard.ca/blog/2022/05/compiling-black-with-mypyc-part-3].
- **Limits:**
  - Amdahl: "if you spend 40% of time outside compiled code, even if compiled code would go 100x faster, overall performance will only be 2.5x faster" [src: https://mypyc.readthedocs.io/en/latest/performance_tips_and_tricks.html].
  - Slow constructs: "Heavy reliance on interpreted Python libraries", callable values, Python (non-native) classes [src: same].
  - Profilers "only work well with non-compiled code" [src: same].
  - Wheels are per Python minor version, because mypyc uses APIs beyond `abi3` [src: introduction].
- **What went wrong (Black):**
  - "it still has a bunch of bugs". Builds needed build isolation turned off. A newer mypyc "broke our usage of dataclasses" [src: sichard.ca part 3].
  - "since pre-commit downloads hooks from source, the compiled wheels won't be used" [src: same].
  - Black 23.9.0 shipped without compiled wheels [src: Black changelog via search, not opened].
- **Extensions:** plain Python. A plugin author writes the same code that runs interpreted.

#### LibCST native parser

- **Native:** tokenizer and PEG parser in Rust, close to CPython's grammar. Goal 1 is lower grammar upkeep. Performance is listed as a "[future]" goal [src: https://github.com/Instagram/LibCST/blob/main/native/libcst/README.md].
- **Scripted:** all CST nodes, visitors, metadata providers and codemods stay Python.
- **Crossing:** the whole tree. Each Rust node implements `IntoPy`, which "translate[s] `Foo` back into a Python object" [src: same]. So every node becomes a Python object.
- **Timeline:** native became the default in 1.0.0 (May 2023) [src: https://github.com/Instagram/LibCST/blob/main/CHANGELOG.md]. The pure parser entry points were removed in 1.8.3 (Aug 2025) [src: same].
- **[run]** Django 5.2.8, 883 files, 5.7 MB, Python 3.12: `ast.parse` took 0.71 s. `libcst.parse_module` took 8.15 s, 11.4x slower. Both produce Python object trees; LibCST's tree is a full-fidelity CST with whitespace nodes.

#### semgrep

- **Native:** semgrep-core (OCaml) parses targets, matches patterns, runs taint, and combines patterns. The combinator logic moved from the Python wrapper into OCaml: "another performance boost was gained upon porting that section of the logic to OCaml" [src: https://semgrep.dev/blog/2022/static-analysis-speed].
- **Scripted:** pysemgrep (Python) loads and validates rules, builds the scan plan, and formats output. The OCaml rewrite (osemgrep) replaces it piece by piece [src: https://github.com/semgrep/semgrep/blob/develop/CHANGELOG.md, many `osemgrep` entries].
- **Crossing:** a subprocess. pysemgrep writes rule shards and a targets file and runs `semgrep-core -rules <file> -targets <file> -json`. It reads progress bytes, then one JSON blob from stdout [src: https://github.com/semgrep/semgrep/blob/develop/cli/src/semgrep/core_runner.py lines ~1021-1102, ~336-412].
- **Crossing cost:** the rule handoff is large enough to need sharding. Shards target 2 MiB, "measured optimum on a 10,240-rule / 114MiB ruleset" [src: core_runner.py].
- **Extensions:** YAML rules in a pattern language, plus restricted `metavariable-*` filters evaluated in OCaml. No Python per match.
- **What went wrong, on absence:**
  - "When `metavariable-type` cannot be evaluated then it defauls to 'false', that is, it filters out the range" (v1.39.0, 2023). Unknown means no finding [src: CHANGELOG].
  - v1.146.0 (Dec 2025) added `skipped_paths` "to report files that failed to scan due to errors (timeout, OOM, etc.), preventing the app from incorrectly marking findings in those files as fixed" [src: CHANGELOG].
  - v1.173.0 (Aug 2026): "A target file whose path filtering failed was silently omitted from the scan, appearing in neither the results nor the list of skipped targets" [src: CHANGELOG].
  - v1.183.0 (Oct 2026): `--no-suppress-errors` "now exits with code 2 (instead of 0)" on incomplete dependency resolution [src: CHANGELOG].

#### Python fronts over a Datalog engine or DuckDB

- **Gigahorse (EVM decompiler):**
  - Native: Soufflé Datalog, compiled to C++ binaries, which can be cached or run with `--interpreted` [src: https://github.com/nevillegrech/gigahorse-toolchain/blob/master/gigahorse.py].
  - Scripted: `gigahorse.py` generates facts, drives the pipeline and runs clients.
  - Crossing: relational files on disk.
  - Extensions: "Client analyses can be written in any language by reading the relational files". Datalog clients get "preferential treatment" [src: https://github.com/nevillegrech/gigahorse-toolchain/blob/master/README.md].
  - What went wrong: soundness is traded for scale. A timeout triggers a "scalable-fallback" configuration, and `ENABLE_LIMITSIZE` caps the size of key relations [src: README; gigahorse.py].
- **Securify2:** a Python front with "context-sensitive static analysis written in Datalog" on Soufflé [src: https://github.com/eth-sri/securify2/blob/master/README.md].
- **sitting_duck (DuckDB community extension):**
  - It parses source with tree-sitter into AST tables you query in SQL (`read_ast`, `parse_ast`) [src: https://duckdb.org/community_extensions/extensions/sitting_duck].
  - `parse_ast()` arguments "must be literals at bind time" [src: search summary of its docs].
  - The study found no published performance numbers and no cross-file resolution.
- **ast-grep-py:** Rust (PyO3) matching. Python passes a rule as kwargs or a config to `find_all` and gets back matched `SgNode`s. "`SgNode` is immutable" [src: https://ast-grep.github.io/guide/api-usage/py-api.html].

#### Pants engine and Starlark (rules in a restricted Python)

- **Pants:**
  - "Pants is best conceptualized as a Python program that makes frequent foreign function interface (FFI) calls into Rust code" [src: https://www.pantsbuild.org/dev/docs/contributions/development/developing-rust].
  - The Rust engine owns the graph, memoization, the store and process execution.
  - Rules are Python `async` functions under `@rule`. "A rule is a pure function", and it "must not directly or indirectly rely on side-effecting code like `print()`, `subprocess.run()`, or `requests`". Inputs and outputs "must be hashable, and therefore must be immutable" [src: https://www.pantsbuild.org/stable/docs/writing-plugins/the-rules-api/concepts].
  - What went wrong: type-driven rule wiring needed a solver, the "rule graph", "akin to 'compiling' the set of @rules". Maintainers reported "still hitting some limits with larger @rule graphs" [src: https://internals.rust-lang.org/t/requesting-the-rust-compiler-communitys-assistance-with-a-compiler-for-build-logic/12964]. Pants then moved to call-by-name, which uses "the explicitly specified arguments to skip solving". `Get` is deprecated [src: https://github.com/pantsbuild/pants/issues/19730; https://www.pantsbuild.org/2.31/docs/writing-plugins/the-rules-api/migrating-gets].
- **Starlark (Bazel; Buck2 uses a Rust implementation [mem]):** Python syntax with the dynamic parts removed.
  - "Global variables are immutable", "because Bazel builds use parallel execution". "After a context finishes, its values become immutable."
  - "Recursion is not allowed". Dict iteration order is deterministic.
  - Source: https://bazel.build/rules/language.

### 2.2 Demand-driven evaluation

Clones read, not built: Soufflé `2adf8c0` (2026-10-31), differential-dataflow `e7c2470` (2026-11-05), Salsa `1f48f4a` (2026-11-09), rustc `495625e` (2026-11-09).
Salsa red-green caching, CodeQL overlay and Bazel action caching are in the earlier caching study (not published).

**Magic sets (the theory).**

- Bancilhon, Maier, Sagiv and Ullman named the rewrite in "Magic sets and other strange ways to implement logic programs", PODS 1986 [src: https://vldb.org/dblp/db/conf/pods/BancilhonMSU86.html].
- Beeri and Ramakrishnan generalized it to any sideways information-passing strategy (SIPS). They showed that goal-directed strategies "may always be implemented by rewriting" the program and evaluating it bottom-up. They also covered safety and optimality (JLP 10(3-4), 1991) [src: https://cris.huji.ac.il/en/publications/on-the-power-of-magic/].
- The guarantee: the rewritten program gives the same answers for the query predicate as the original program. The proof covers positive (negation-free) Datalog [mem].
- Negation breaks the guarantee. "When we apply the generalized magic sets to a stratified database, the resultant database may be unstratified" [src: https://acs.uwinnipeg.ca/ychen2/journalpapers/StratifiedDB.pdf, Chen 1997, citing Balbin et al., JLP 1991].
- Fixes need either a labelling of contexts (Balbin et al.; Chen) or a weaker semantics. Ross used modular stratification and well-founded answers for this (J. ACM 1994) [mem; abstract via search summary only].

**Soufflé.**

- Default evaluation is full and bottom-up. A pass drops relations that "do not contribute to output", so coarse demand at the relation level is always on [src: https://github.com/souffle-lang/souffle/blob/master/src/ast/transform/RemoveRedundantRelations.h].
- Magic sets are opt-in per relation: `--magic-transform="A1,…"`, `=*`, or `.pragma "magic-transform"`. `magic-transform-exclude` turns it off per relation [src: https://souffle-lang.github.io/magicset].
- The docs call the rewrite "sound and complete". The SIPS "always selects the left-most atom with at least one bound argument" [src: same].
- The docs say only positive Datalog is supported. Relations with negation, functors or aggregates in their body or in a dependency are skipped. The paper they cite is Balbin et al. 1991 [src: same].
- The code has moved past the docs. `MagicSet.h` runs four stages: normalise, label ("to support negation"), adorn, magic. Negative labelling copies every negated relation, and every relation used inside an aggregate, under an `@neglabel` name. Magic is never applied to those copies, so they are computed in full. The code comment says this keeps them from "affecting stratification once magic dependencies are added" [src: https://github.com/souffle-lang/souffle/blob/master/src/ast/transform/MagicSet.h, MagicSet.cpp `NegativeLabellingTransformer::transform`].
- Soufflé also refuses to apply magic to these relations [src: MagicSet.cpp `getWeaklyIgnoredRelations`, `getStronglyIgnoredRelations`]:
  - relations with float comparisons, order-dependent functors (`/`, `%`), counters or subsumption;
  - every stratum that depends on one of those, applied to a fixpoint.
- Negation must be stratifiable. "Such circular definitions are forbidden" [src: https://souffle-lang.github.io/rules].
- On absence, provenance (`-t explain`) builds proof trees for tuples that exist. It cannot explain a missing tuple on its own: "it is not technically feasible to automatically generate explanations for non-existence". The user must pick a rule and bind its variables by hand (`explainnegation`) [src: https://souffle-lang.github.io/provenance].
- No public measurement of magic-set gains in Soufflé turned up.

**CodeQL evaluator.**

- The semantics is stratified Datalog. A program "can be stratified to a sequence of layers". A predicate may not strictly depend on another predicate in its own layer, and negation makes a dependency strict. "If a QL program has no valid stratification, then the program itself is not valid", and an implementation "must detect all invalid programs and refuse to evaluate them" [src: https://codeql.github.com/docs/ql-language-reference/ql-language-specification/].
- Recursion is "only allowed under an even number of negations" [src: https://codeql.github.com/docs/ql-language-reference/recursion/].
- Demand comes from optimizer rewrites, not from the semantics. Magic sets are on by default. `pragma[nomagic]` exists to stop "taking information from the context of a predicate call and pushing it into the body", and "nomagic implies noinline" [src: https://codeql.github.com/docs/ql-language-reference/annotations/].
- `cached` does the opposite: the predicate is "evaluated in its entirety and stored". The cost is storage, and the predicate loses call-site specialization [src: same]. So each predicate is either specialized to its demand or computed once in full and shared, never both.
- `bindingset` marks predicates that are finite only when some arguments are bound, so they can only run on demand. The compiler checks every call for range restriction and usually reports an error otherwise [src: annotations page; https://codeql.github.com/docs/ql-language-reference/evaluation-of-ql-programs/].
- Rewrites are expected to preserve the layered least-fixed-point meaning [mem]. Users only see this indirectly: GitHub tells authors not to use `nomagic` or `noopt` "unless recommended to do so by GitHub" [src: annotations page].
- Absence is closed-world. "A negation formula matches any tuple that does not match the nested formula" [src: QL spec]. A missing fact therefore reads as false.
- The Python library models unknown values as values: `TUnknown`, `TUnknownClass`, `TUndefined`, and `AbsentModuleObjectInternal` ("absent from the database, but has been inferred to likely exist") [src: https://github.com/github/codeql/blob/main/python/ql/lib/semmle/python/objects/ObjectAPI.qll, ObjectInternal.qll].
- Failed type inference is a fact with a reason, such as "Missing base", "Decorator not understood" or "Failed to infer metaclass" [src: https://github.com/github/codeql/blob/main/python/ql/lib/semmle/python/pointsto/PointsTo.qll `failedInference`].
- Queries use that fact to suppress results: `not t.failedInference(_)` appears in NonCallableCalled, IllegalRaise, NonIteratorInForLoop, HashedButNoHash and others. A meta query, `analysis/FailedInference.ql`, lists the failures [src: `gh search code --repo github/codeql failedInference`]. Here unknown means "stay quiet", which is the opposite polarity to Hoglint.

**Bazel Skyframe.**

- Evaluation is driven by requests. "A build is achieved by evaluating the node that represents the build request". Each function requests the nodes it needs until it reaches the leaves [src: https://bazel.build/reference/skyframe].
- Missing dependencies cause a restart. `getValue` returns null, the function returns null, Skyframe computes the dependency, and then calls `compute` again ("Skyframe restart") [src: https://github.com/bazelbuild/bazel/blob/master/src/main/java/com/google/devtools/build/skyframe/SkyFunction.java].
- Equivalence to a clean build depends on purity. "Well-behaved SkyFunction implementations avoid accessing data in any other way than requesting dependencies". Read side effects "are not" acceptable [src: skyframe page]. A changed node whose new value equals the old one revives its dependents (change pruning) [src: same].
- Errors are values with a class. A persistent error is "completely deterministic in terms of the node's Skyframe dependencies" and "may be cached". A transient error is evaluated again on the next request. An I/O failure is transient in the node that did the I/O and persistent in its callers [src: https://github.com/bazelbuild/bazel/blob/master/src/main/java/com/google/devtools/build/skyframe/SkyFunctionException.java].
- `ErrorInfo` carries `isTransitivelyTransient` up the graph [src: ErrorInfo.java].
- A cycle is reported as data: `CycleInfo` holds the cycle and the path to it [src: CycleInfo.java].
- `--keep_going`: "While the target that failed and those that depend on it cannot be analyzed, other prerequisites of these targets can be" [src: https://bazel.build/reference/command-line-reference].
- A function may recover from a dependency's error and return a value in keep-going mode. The classic example is `TargetPatternFunction`, which returns "the list of all found targets" for `//foo/...` even when some packages fail [src: SkyFunction.java `inErrorBubbling`].
- Partial results get their own exit codes [src: https://bazel.build/run/scripts]:
  - `bazel query` exits 3 for "Partial success … therefore the results of the operation are not 100% reliable".
  - `bazel test` exits 4 for "Build successful but no tests were found even though testing was requested".

**Differential dataflow, DDlog and DBSP (incremental, not demand-driven).**

- DDlog computes "*all* possible derived facts" bottom-up. It contrasts itself with top-down engines that answer one query at a time [src: https://github.com/vmware-archive/differential-datalog README].
- DDlog's selling point is incrementality: "the minimum amount of work necessary to compute changes to output relations" [src: same].
- DDlog's repo is archived. The last push was 2023-07-07 [src: `gh api repos/vmware-archive/differential-datalog`].
- DDlog negation must be stratified ("No cycle in a graph can contain an edge labeled with negative polarity"). Negative literals may not introduce variables [src: https://github.com/vmware-archive/differential-datalog/blob/master/doc/language_reference/language_reference.md].
- Differential dataflow builds `antijoin` from signed multiplicities: `self.concat(self.semijoin(other).negate())`. It is a true antijoin only if `other` has multiplicity 0 or 1, so the docs say to apply `distinct` first [src: https://github.com/TimelyDataflow/differential-dataflow/blob/master/differential-dataflow/src/collection.rs].
- `iterate` does not promise to finish. "If any recursive definitions fail to reach a fixed point the computation diverges" [src: differential-dataflow `interactive/README.md`; `mdbook/src/chapter_2/chapter_2_7.md`].
- The claimed guarantee is that incremental output equals evaluation on the accumulated input: "correct (not just consistent) answers" [src: differential-dataflow README].
- DBSP, by the same people, generalizes incremental view maintenance to "arbitrary DBSP programs", including "monotonic and non-monotonic recursion" [src: https://arxiv.org/abs/2203.16684]. A machine-checked proof exists [mem].

**rustc queries.**

- rustc is "demand-driven", and its query system is "the key" to that [src: https://rustc-dev-guide.rust-lang.org/query.html].
- A provider "must be a pure function in the sense that for the same key it must always yield the same result" [src: https://rustc-dev-guide.rust-lang.org/queries/query-evaluation-model-in-detail.html].
- Red-green reuse: a query is run again when an input turns red. If the hash of its result is unchanged, it turns green. The `reads(Q)` list must be revisited "in same order as they occurred in the original compilation" [src: https://rustc-dev-guide.rust-lang.org/queries/incremental-compilation.html].
- The equivalence argument is stated against the previous session, not against a clean build [src: same].
- Known holes are named in the docs [src: https://rustc-dev-guide.rust-lang.org/queries/incremental-compilation-in-detail.html]:
  - `eval_always` queries "can read inputs (from files, global state, etc)", so they always run again;
  - a hash collision causes "a missed update".
- Cycles are fatal by default: "emit a cycle error and halt compilation". The source is `default()`, which runs `emit_err()` and then `raise_fatal()` [src: https://github.com/rust-lang/rust/blob/main/compiler/rustc_middle/src/query/modifiers.rs, compiler/rustc_query_impl/src/handle_cycle_error.rs].
- Cycle recovery used to exist and "was later removed because it is not entirely clear what the theoretical consequences of this are" [src: query-evaluation-model page].
- A custom cycle handler such as `fn_sig` returns an error type that carries a guarantee: `err.delay_as_bug()` then `Ty::new_error(tcx, guar)` [src: handle_cycle_error.rs].
- `ErrorGuaranteed` is "unconstructable outside of the rustc_errors crate". It lets the compiler "statically check that an error code path leads to a failure" [src: https://rustc-dev-guide.rust-lang.org/diagnostics/error-guaranteed.html].
- A delayed bug that no real error follows becomes an internal compiler error (ICE). Issue #87274 shows the message: "no errors encountered even though `delay_span_bug` issued" [src: https://github.com/rust-lang/rust/issues/87274].

**Swift request evaluator.**

- Swift moved to a request evaluator because lazy type checking on mutable state caused "compiler bugs that only manifest in multi-file targets". Its ad hoc recursion led to "wrong results or infinite recursion" [src: https://github.com/swiftlang/swift/blob/main/docs/RequestEvaluator.md].
- `-debug-cycles` dumps the request tree of each cycle.
- Real cycle diagnostics stay "disabled because they fire too often in real-world code" [src: same].

**Salsa cycles (a gap left by earlier studies).**

- By default Salsa panics on a cycle. Fixpoint iteration is opt-in through `cycle_initial` and `cycle_fn`. It is "only usable if the queries … are monotone and operate on a value domain which is a partial order with fixed height" [src: https://github.com/salsa-rs/salsa/blob/master/book/src/cycles.md].
- The query that is first in demand order becomes the cycle head. "In order to be robust against varying query execution order", every query in a cycle must set `cycle_initial` [src: same].
- Salsa panics after 200 iterations [src: same].

**XSB tabling (demand-driven with three truth values).**

- SLG resolution is top-down and tabled, and computes the well-founded semantics [mem].
- A negative literal succeeds only on a *completed* subgoal: "S is failed in F if S is completed and the tree for S contains no answers" [src: https://userweb.fct.unl.pt/~lmp/publications/online-papers/iclp09-iac.pdf].
- An answer that rests on a loop through negation is kept as a conditional answer, "undefined at the time of their addition" [src: same].
- The engine errs toward undefined. "In a few pathological cases its engine treats atoms as undefined that are true or false in WFS" [src: same].

### 2.3 The Python object tax

**grimp (native graph).** Graph assembly and the closed full-Rust build are in §2.1.

- The Rust graph holds no strings. Module names go through a global `StringInterner`; modules are `slotmap` tokens; imports are `FxHashSet<ModuleToken>` adjacency maps, forward and reverse; import details sit in a side map keyed by token pair [src: https://github.com/python-grimp/grimp/blob/main/rust/src/graph/mod.rs].
- The PR that moved the graph to Rust says it plainly: "There are no strings anymore - the graph is just ints" [src: https://github.com/python-grimp/grimp/pull/175].
- The same PR names the boundary trap. `if module in self.modules` in Python copied "the entire graph ... from rust to a python set (every time)". The fix was a `contains_module` call. The author asks to make `modules` a method "to show more clearly that work is being done" [src: PR #175].
- PR #175 also short-circuits layer and chain queries with one `chain_exists` check first, because most contracts are kept [src: PR #175].

**CPython rows, measured.** One edge row = (src id, dst id, kind, line), ids up to 31k, 1M rows [run]:

| Representation | Bytes/row | Build 1M | Full `gc.collect()` with 1M live |
|---|---:|---:|---:|
| tuple | 184 | 0.13 s | 6 ms |
| namedtuple | 193 | 0.40 s | 33 ms |
| dict | 289 | 0.38 s | 39 ms |
| `@dataclass(frozen=True)` | 209 | 0.53 s | 42 ms |
| `@dataclass(frozen=True, slots=True)` | 169 | 0.81 s | 56 ms |
| class with `__slots__` | 169 | 0.45 s | 71 ms |
| 4 numpy columns (int32, int32, int8, int32) | 13 | n/a | 0 |

- Bytes/row include the boxed ints and the list slot. `sys.getsizeof` of one tuple, namedtuple or slots row is only 64 to 80 bytes; the three boxed ints (ids above 256) add about 84 [run].
- `frozen=True` costs construction time: 280 ns/row frozen+slots vs 77 ns mutable+slots vs 54 ns tuple, GC off [run]. Frozen init goes through `object.__setattr__` [mem].
- Tuples of atomic values are untracked by the cycle GC on 3.14.7: `gc.is_tracked` is False right after creation [run]. A slots instance stays tracked [run].
- GC tax while building 3M rows: slots dataclass 1.22 s with GC on vs 0.79 s off (3.14.7); 2.21 s vs 1.61 s (3.12.14). Tuples: no difference on 3.14.7; 0.59 s vs 0.41 s on 3.12.14 [run].
- Computed for hoglint: imports, defs, assignments, uses and call sites total about 2.5M rows. As objects that is 0.42 to 0.72 GB before strings; as columns about 35 to 40 MB.

**sys.intern.** 1M module-name strings with 30k distinct values: 89 MB as fresh strings, 12 MB interned [run]. Interning only helps when the producer interns at creation; strings decoded from a cache or a native call arrive fresh [mem].

**gc.freeze and gc tuning.**

- Instagram disabled the cycle GC in forked Django workers and reported about 10% more capacity [src: https://instagram-engineering.com/dismissing-python-garbage-collection-at-instagram-4dca40b29172, via search summary; the site refused connections].
- `gc.freeze()` (bpo-31558, Python 3.7) moves all tracked objects to a permanent generation. The issue gives no measured numbers [src: https://bugs.python.org/issue31558].
- CPython core developers disagree on its value. One says refcount writes still dirty shared pages. Another says a full collection writes the GC header of every tracked object, and freeze stops that. A third says immortal objects are untracked "at least since 3.13" [src: https://discuss.python.org/t/it-seems-to-me-that-gc-freeze-is-pointless-and-the-documentation-misleading/71775].
- mypy uses GC tricks in its build loop. It sets `gc.set_threshold(200 * 1000, 30, 30)` because "we can spend a large fraction of cpu in gc" [src: https://github.com/python/mypy/blob/master/mypy/main.py]. It runs `gc.disable()` while loading the graph, because "parsed ASTs are trees". Afterwards `gc.freeze(); gc.unfreeze()` moves everything to the oldest generation [src: https://github.com/python/mypy/blob/master/mypy/build.py].
- The freeze/unfreeze hack gave "~30% perf win" on a warm `mypy -c 'import torch'` [src: https://github.com/python/mypy/pull/19681]. It cost about 8% extra memory and made compiled tests twice as slow on CI runners [src: https://github.com/python/mypy/pull/19766].
- On Python 3.14 "repeated freezes are very slow"; mypy disabled the hack in parallel workers [src: https://github.com/python/mypy/pull/21207], then brought back one freeze for a 4-5% gain [src: https://github.com/python/mypy/pull/21302].
- Measured here: after `gc.freeze()`, a full collection over 1M frozen rows takes 0.0 ms instead of 6 to 71 ms [run].

**Columnar arrays and CSR graphs.**

- numpy holds the same 1M edge rows in 13 bytes/row [run].
- A random 30k-node, 340k-edge graph (sizes from the workload notes): 36 MB as dict-of-sets, 1.8 MB as SciPy CSR. 100 BFS runs: 2,781 ms in Python, 98 ms in `scipy.sparse.csgraph` (28x). The graph is random and reaches nearly every node, so this is a worst case [run].
- Arrow defines an ABI-stable C struct pair (`ArrowSchema`, `ArrowArray`) for "zero-copy sharing ... between independent runtimes ... in the same process", with no dependency on the Arrow library [src: https://arrow.apache.org/docs/format/CDataInterface.html]. Polars to Arrow: 3.6 ms per 1M rows; DuckDB result to Arrow: 2.5 ms [run].

**mypyc.**

- Annotated code runs "1.5x to 5x faster" compiled, tuned code "5x to 10x". This is how "mypy achieved a 4x performance improvement" [src: https://mypyc.readthedocs.io/en/latest/introduction.html].
- Classes compile to C extension classes with vtables. Calls into other compilation units, nested functions, `*args`, and Python classes stay slow. With 40% of time outside compiled code, even 100x faster compiled code gives only 2.5x overall [src: https://mypyc.readthedocs.io/en/latest/performance_tips_and_tricks.html].
- Black: about 2x overall in 22.1.0 (1.82x in an early benchmark). The port needed `@trait` mixins, dropped `@dataclass` on `Generic`/`ABC` classes, `ClassVar` fixes, a `__name__` workaround, a negative-constant crash workaround, and skips for mock-based tests [src: https://sichard.ca/blog/2022/05/compiling-black-with-mypyc-part-1/].

**Cython.**

- pydantic v1 compiled its unchanged Python with Cython for "a 30-50% performance improvement". It is "incompatible with Cython v3" [src: https://github.com/pydantic/pydantic/blob/1.10.X-fixes/docs/install.md]. v2 replaced this with a Rust core [mem].
- spaCy gathers data into C structs (`Lexeme.c` holds a `LexemeC`) to "shed the Python container", and one Python object owns each memory `Pool` [src: https://spacy.io/api/cython]. Its `Token` and `Span` objects are views over a `Doc`'s struct array [mem].

### 2.4 Rules over a native fact store

**One rule, seven styles.** Rule: src in product A, dst in product B, dst not a facade module. 1M rows, 14,110 violations [run]:

| Style | Time per 1M rows | Per row |
|---|---:|---:|
| Python loop over tuples | 45-66 ms | ~50 ns |
| numpy boolean mask | 1.2-1.7 ms | ~1.5 ns |
| Polars expression | 0.8-1.1 ms | ~1 ns |
| DuckDB SQL over an Arrow table | 1.4-2.2 ms | ~2 ns |
| DuckDB scalar Python UDF | 128-140 ms | ~130 ns |
| DuckDB Arrow (batch) Python UDF | 117-131 ms | ~120 ns |
| Polars `map_elements` (Python per row) | 206-380 ms | ~200-380 ns |

A Python callback inside a native engine ran 2 to 6x slower than a plain Python loop [run].

**Predicates compiled to native filters.**

- Polars builds a lazy plan with predicate and projection pushdown, common subplan elimination, constant folding and join ordering [src: https://docs.pola.rs/user-guide/lazy/optimizations/]. `explain()` printed the rule reordered, with "PROJECT 2/4 COLUMNS" [run].
- DuckDB `EXPLAIN` printed a filter, a MARK hash join for `NOT IN`, and a Python Arrow scan [run]. `EXPLAIN ANALYZE` adds per-operator timings [mem].
- Soufflé from Python: the SWIG binding loads and writes whole relations as files (`loadAll`, `printAll`). It has no per-tuple API [src: https://souffle-lang.github.io/swig].
- Soufflé provenance (`-t explain`) prints proof trees on demand. It stores the producing rule and a height per tuple [src: https://souffle-lang.github.io/provenance]. Overhead: 1.27x runtime and 1.45x memory in the preprint, 1.31x runtime in the POPL 2021 abstract [src: https://arxiv.org/abs/1907.05045, https://popl21.sigplan.org/details/POPL-2021-research-papers/64/Debugging-Large-Scale-Datalog-A-Scalable-Provenance-Evaluation-Strategy, via search summary].
- Absence has no automatic proof: "it is not technically feasible to automatically generate explanations for non-existence". `explainnegation` asks the user to pick a rule and bind variables [src: souffle-lang.github.io/provenance].
- egglog-python: Python classes and `@egraph.register` rewrite rules build terms that the Rust engine runs [src: https://egglog-python.readthedocs.io/latest/].
- pyDatalog is pure Python; its wheels stop at CPython 3.10 [src: https://pypi.org/project/pyDatalog, via search summary].
- CodeQL "Quick Evaluation" runs one selected predicate or formula and shows its rows [src: https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/scan-from-vs-code/running-codeql-queries, via search summary].

**A Python callback per row.**

- pydantic-core, 1M ints: native `list[int]` 9 ns/item; with an `AfterValidator` 30 ns; with a `WrapValidator` 102 ns [run]. The docs say wrap validators are slower because they "require that data is materialized in Python" [src: https://pydantic.dev/docs/validation/latest/concepts/performance/].
- Polars says `map_elements` "is much slower than the native expressions API" and is "effectively running python 'for' loops" [src: https://docs.pola.rs/api/python/stable/reference/expressions/api/polars.Expr.map_elements.html].
- Polars disassembles the lambda's bytecode (`BytecodeParser`) and raises `PolarsInefficientMapWarning` with the equivalent expression [src: https://github.com/pola-rs/polars/blob/main/py-polars/polars/_utils/udfs.py, read in the installed 2.1.0].
- DuckDB native UDFs get "a single tuple at a time" [src: https://duckdb.org/docs/2.0/clients/python/function.html]. A no-op scalar UDF cost 64 ns/row [run].

**Batch APIs.**

- DuckDB Arrow UDFs get "a `pyarrow.ChunkedArray` of at most 2048 rows", and the docs call them "much more efficient than native" [src: duckdb.org/docs/2.0/clients/python/function.html].
- Measured, a no-op Arrow UDF made 489 calls and took 83 ms, about 170 µs per batch. That was slower than the no-op scalar UDF at 64 ms [run]. The docs' claim did not hold for cheap predicates here.
- Polars `map_batches` passes the whole Series with "a single Python call" [src: https://docs.pola.rs/user-guide/expressions/user-defined-python-functions/].
- ast-grep's rule for its JS bindings: "reduce FFI ... calls". A manual traversal makes "as many FFI calls as the tree node number", and napi "isn't always faster than pure JavaScript". One `findAll` or `findInFiles` call replaces the traversal [src: https://ast-grep.github.io/guide/api-usage/performance-tip.html].
- Turning native rows back into Python objects, per 1M rows: Polars `rows()` 100-141 ms, `to_dicts()` 235-379 ms, DuckDB `fetchall()` 148-237 ms, numpy `tolist()` 7 ms [run].

**Absence and NULL.**

- DuckDB `exception_handling="return_null"` turns a raising UDF into NULL [src: duckdb docs above]. In a `WHERE` that NULL drops the row: 1 of 3 rows counted, with one exception and one NULL input both silently gone [run].
- `dst NOT IN (subquery)` returned 0 rows once the subquery held one NULL [run].
- Polars `filter` dropped a null row, but an `anti` join kept a null key [run]. Operators disagree about unknown.

## 3. Patterns across tools

**Where the line sits**

1. **The line moves down over time, in the same order everywhere:** parsing first, then the graph and its queries, then orchestration [§2.1].
   - grimp: layers (2023), graph (Feb 2025), parsing (May 2025), threads (Sep 2025).
   - tach: imports (v0.3.0), check, then `check_external` (v0.25.6).
   - semgrep: pattern combinators, then the osemgrep CLI.
   - Pants: "planning to port additional functionality from Python to Rust".

   No tool here moved work back to the scripted side.
2. **Gains come from fewer crossings, not from the language.**
   - grimp batch queries got 3.3x to 13,000x faster, while a small call got 30x slower.
   - LibCST: a native parser with Python node objects runs 11.4x slower than `ast` [run].
   - oxlint needed raw transfer before JS rules paid off [§2.1].
   - A Python callback inside Polars or DuckDB ran slower than a plain Python loop [§2.4].
3. **Crossing formats, from cheap to costly:**

   | Format | Users | Cost |
   |---|---|---|
   | Opaque handles with coarse calls | tach diagnostics, grimp queries, ast-grep `find_all` | about 1 µs per call [run] |
   | Shared native memory layout, read lazily | oxlint raw transfer, Arrow C data interface | about 3 ms per 1M rows to Arrow [run] |
   | Files or a subprocess with JSON | semgrep, Gigahorse, pks | serialize plus parse; semgrep shards a 114 MiB ruleset |
   | One Python object per row or node | grimp graph assembly, LibCST, pydantic wrap validators, `fetchall()` | 100 to 380 ms per 1M rows [run]; the cost that hurt in every case |
4. **Accessors that look free copy the whole store.** Examples: grimp's `modules` property (fixed by an API change, PR #175), Polars `rows()`, DuckDB `fetchall()` [§2.3, §2.4].
5. **The scripted step that builds the native program can dominate.** Examples: pydantic's schema builder (14 s, 9 GB), Pants' rule-graph solver, semgrep's rule handoff [§2.1].

**Extensions**

6. **Native-only tools move framework knowledge into the core.** Pyrefly builds in Django, Pydantic and attrs. pks has a Rust inflector shim. ruff calls bundling "a feature and not a bug". Users cannot add a pattern, and the core team owns each framework change [§2.1].
7. **Tools that keep authors in a scripting language restrict what the script can do.** Pants requires purity and hashable values. Starlark freezes values and forbids recursion. semgrep, GritQL and ast-grep use DSLs. oxlint keeps native rules on the hot path [§2.1].
8. **Compiling Python keeps one language and adds a dialect.** mypyc gives 1.5x to 5x but needs traits, has dataclass limits and checks types at run time. Cython gave pydantic v1 30% to 50% and then pinned it to Cython 2 [§2.1, §2.3].

**Data layout**

9. **Native graphs win by storing ints, not names.** Examples: grimp's interner and slotmap tokens, CSR arrays, spaCy's struct arrays [§2.3].
10. **Python tools at scale tune the GC instead of leaving it alone, and the tuning breaks across versions.** mypy sets thresholds, disables GC during load, and freezes. That gave about 30%, then regressed on 3.14. Instagram turned GC off [§2.3].

**Tests and equivalence**

11. **Test seams fight the native move.** grimp's Python fake filesystem slowed the Rust scanner. It also blocked a full-Rust build that was 1.6x to 2.7x faster [§2.1].
12. **Equivalence with the old tool comes from an oracle.** pks expects an empty `git diff` after `update`. Pyrefly warns that Django types may differ. grimp used snapshot tests [§2.1].

**Demand and absence**

13. **Coarse demand is free; fine demand needs a proof, and the proof stops at negation.** Soufflé drops unread relations. Magic sets are "sound and complete" for positive programs only. Soufflé computes negated relations in full. CodeQL picks per predicate: specialized to its demand, or `cached` and computed once in full, never both [§2.2].
14. **Absence is closed-world unless unknown is stored as data.**
    - Stored: CodeQL `TUnknown` and `failedInference`, WFS undefined, rustc `ErrorGuaranteed`, Skyframe error values.
    - Silently dropped: semgrep `metavariable-type`, tach wildcard imports, Gigahorse `limitsize`, DuckDB `return_null`, `NOT IN` with a NULL [§2.1, §2.2, §2.4].
15. **Errors flow to dependents as values, with a class.** Skyframe marks errors persistent or transient. A dependent may hide follow-on errors only with proof that the run fails: rustc's delayed bug becomes an internal compiler error if no real error follows [§2.2].
16. **Partial results get their own exit status, often added only after a wrong "clean" or "fixed".** Bazel `query` exits 3 and `test` exits 4. semgrep added `skipped_paths` (Dec 2025) and exit 2 for incomplete dependency resolution (Oct 2026) [§2.1, §2.2].
17. **Cycles are fatal by default, and demand order can change results.** Salsa requires `cycle_initial` on every query in a cycle. rustc removed cycle recovery. Swift had bugs that showed only in multi-file runs [§2.2].
18. **No engine explains absence for free.** Soufflé says non-existence cannot be explained automatically. Its provenance costs about 1.3x runtime. CodeQL ships a meta query for failed inference [§2.2, §2.4].

## 4. Fit for hoglint: lessons and traps

Evidence only, no design.
Requirement numbers refer to the requirements given to the blind architects of the engine round (`docs/research/engine-blind-design-a.md` and `-b.md`).

**Crossing and data layout**

- **Trap: one Python object per node or per call site.**
  - LibCST took 8.15 s for Django's 5.7 MB [run]. At PostHog's 267 MB that is about 380 s on one core (computed). `ast.parse` would take about 33 s (computed). The workload notes measure a Rust parser at about 7 s.
  - The same tax applies to any design that hands Python one object per call site (1.19M) or per name use (460k).
- **Lesson: per-call crossing is cheap; materializing rows is not.**
  - A grimp adjacency query costs 0.9 µs per call [run]. One call per import (344k) is about 0.3 s (computed).
  - grimp's build-time losses came from Python-side orchestration and object building.
    - 3.10, after a scanner refactor (#237), took one large uncached build from 4.6 s to 108 s.
    - 3.11 fixed it by moving parallelism from joblib processes to Rust threads.
    - The per-import assembly from Python objects is still there.
  - Its one slow single call came from a whole-graph copy behind a property, not from the call itself (PR #175).
- **Trap: the house `@frozen` dataclass as the row type.**
  - It costs 280 ns per row to build against 54 ns for a tuple [run].
  - It stays tracked by the cycle GC; tuples of atomic values do not on 3.14 [run].
  - About 2.5M rows take 0.42 to 0.72 GB as objects and about 40 MB as columns (computed).
- **Lesson: make costly accessors look costly.** grimp's `modules` property copied the whole graph on every `in` check, and the fix was an API change (PR #175).
- **Trap: GC hacks as a performance plan.** mypy's freeze trick gained about 30% on one CPython, cost about 8% memory, and regressed on 3.14 (mypy #19681, #19766, #21207). Data layout avoids the question.

**Extension model (requirements 5 and 6)**

- **Trap: a native-only core that bakes in Django.**
  - Pyrefly and pks show the result: framework support becomes a core-team backlog.
  - Dynamic cases drift ("highly dynamic metaclass cases can produce different types").
  - That conflicts with a generic core and with pack authors who are Python developers.
- **Lesson: scripted layers that stayed fast restrict the script.** Pants rules are pure, with hashable inputs and outputs. Starlark freezes values. Each restriction matches a Hoglint requirement: determinism, and equality to a clean run.
- **Trap: a Python callback per row inside a native engine.** It ran 2x to 6x slower than a plain Python loop, at 64 to 380 ns per row [run]. Compiled predicates ran at 1 to 2 ns per row [run].
- **Lesson: authors can debug compiled predicates.** Polars `explain()`, DuckDB `EXPLAIN` and CodeQL Quick Evaluation show the plan and the rows. None of them shows why a row is missing.
- **Trap: wiring by type.** Pants needed a solver to pick rules by signature. The solver became the scaling limit, and Pants moved to call-by-name. A pack registry that resolves producers by declared fact type takes on the same job.
- **Trap: test fakes that only Python can see.** grimp's fake filesystem blocked its fastest native build. A native core needs test seams that native code can use, or it loses speed to keep the tests.

**Distribution (requirement 6: no Rust toolchain)**

- **Lesson: pre-commit must install a wheel, not a source tree.**
  - Black's compiled wheels went unused under pre-commit, which "downloads hooks from source".
  - ruff ships a separate `ruff-pre-commit` repo "to enable installing Ruff via prebuilt wheels from PyPI" [src: https://github.com/astral-sh/ruff-pre-commit].
- **Lesson: the wheel matrix is ongoing work.**
  - PyO3 `abi3` gives one wheel per OS and architecture across Python versions, with fewer optimizations [src: https://pyo3.rs/main/building-and-distribution.html].
  - Free-threaded CPython "cannot load `abi3` wheels"; it needs `abi3t` [src: same].
  - grimp 3.18 fixed missing macOS 3.14 wheels and skipped some 3.15 targets [§2.1, grimp changelog].
  - mypyc wheels are per Python version.
- **Trap: mypyc as the middle path.** It gives 1.5x to 5x. Time spent in interpreted libraries caps it (40% outside compiled code caps the gain at 2.5x). Black's maintainer advises using it only if "willing to feel a bit like a beta tester".

**Unknown is never clean (requirement 2)**

- **Trap: unknown read as "no finding".**
  - semgrep took until Dec 2025 to stop marking findings in failed files as fixed. It took until Aug 2026 to stop dropping targets from both the results and the skipped list.
  - tach classed wildcard imports as external.
  - DuckDB `return_null`, `NOT IN` with a NULL, and filter-on-null all lost rows with no signal [run].
  - Polars `filter` and `anti` join disagree on null [run].
- **Trap: copying CodeQL idioms flips the guarantee.** CodeQL's Python queries use `not failedInference(_)` to stay quiet on unknown classes. Ported as is, that idiom reads "unknown" as "clean".
- **Lesson: store unknown as data that negation must check.** Every engine that needed a third state stored it: `TUnknown`, WFS undefined, `ErrorGuaranteed`, Skyframe error values.
- **Lesson: absence-shaped rules need complete base relations under any demand scheme.** These are reach (contracts-no-django), cycle (no-new-package-cycles), coverage (wiring-watched) and "no forbidden import". Soufflé computes negated relations in full, and XSB waits for completion. A "no path" claim holds only when every edge in the demand cone is resolved or marked unknown.
- **Lesson: carry an incomplete marker that cannot be dropped silently.** rustc's `ErrorGuaranteed` turns "suppressed downstream" into "the run still fails". Bazel's exit 3 and exit 4 match Hoglint's exit 2. Keep-going recovery that returns "all found targets" is the failure mode to avoid.
- **Lesson: "incomplete" needs its own record of what was not computed.** No engine explains absence, and provenance costs about 1.3x runtime and 1.45x memory (Soufflé).

**Demand and determinism (requirements 2 and 3)**

- **Lesson: whole-module granularity has the strongest evidence.**
  - Pyrefly chose it at Instagram scale, "if solving all bindings in a module is fast enough".
  - The workload notes measure a median of 5 files for "changed plus dependents by changed name".
  - Per-identifier demand (Salsa-style, magic sets) brings the cycle-order and negation hazards above.
- **Trap: evaluation order that differs by mode.** Hoglint runs the same rules in pre-commit, `explain`, `query` and CI. Salsa's cycle head depends on which query is asked first, and Swift had multi-file-only bugs. Any lazy path is a second evaluation order to keep equal.
- **Lesson: purity is the whole equivalence argument.** Skyframe, rustc and Pants each name their leaks (`eval_always`, read side effects). For Hoglint, the inputs that can bypass the snapshot are:
  - the environment;
  - Django settings;
  - files outside the git index;
  - the owners files.

## Gaps

- No public numbers on magic-set speedups (Soufflé, CodeQL). The Beeri-Ramakrishnan and Ross proofs are paywalled and were not read in full.
- No primary source on LibCST or pylint visitor callback cost. No tool moved off per-node Python callbacks with published numbers.
- Instagram's GC posts and the Soufflé provenance overhead come from search summaries. instagram-engineering.com refused connections.
- Pants' reasons for call-by-name come from its tracking issue (#19730), not from a design note. The motivating issue (#18905) did not load.
- semgrep's pysemgrep-to-osemgrep rationale has no primary source. The changelog shows the port but not the reason.
- `pattern-where-python` (semgrep's old per-match Python hook) appears deprecated in a mirrored changelog. The study could not confirm its removal in the official one.
- sitting_duck publishes no performance numbers.
- The study did not check where `pydantic-core` moved after its repo was archived.

## What the engine round took from this study

- The line between Rust and Python moves down, and the cost is the per-row crossing, not the language: D1 and D2 (Rust holds resolution and indexes; coarse calls and opaque handles, no Python object per row).
- Unknown as data that negation must check, with a marker that cannot be dropped: D8.
- Whole-module granularity and the cycle-order hazards of per-identifier demand: D5 (every run evaluates the whole repo) and D21 (re-export cycles proven independent of query order).
- GC tricks only as an interim step; data layout as the real fix: D23 (no whole-snapshot expansion into Python).
- Pre-commit installs a prebuilt abi3 wheel: D2 and D15.
- The purity list of inputs that can bypass the snapshot: D4, D32 and D33.
