# Prior art, round 2: code fact stores and code graphs

> A study of systems that store code as facts and edges, with a scip-typescript measurement on the PostHog monorepo, Nov 9, 2026.
> Partly replaced: the synthesis is `docs/research/prior-art.md`, and the fact cache design that followed is in `docs/design/06-fact-cache-and-summaries.md`.

Tags: [src: URL] means read in this study, [mem] means from memory and not checked, [run] means measured by the study.
Scope: systems that store code as facts and edges.
The first prior-art round (`docs/research/research-round-1-prior-art.md`) covered boundary linters; nothing here repeats it.
"The draft" is the design draft of that date (not published); "data model section N" points to `docs/research/data-model.md`.

## 1. System by system

**Kythe (Google).** Nodes carry facts; typed edges link text anchors to semantic nodes. An import site gets its own `ref/imports` edge, apart from later `ref` uses. Node id is a VName (signature, corpus, root, path, language), and the signature "need not be stable across input versions" [src: https://kythe.io/docs/schema/]. No query language; tools read serving tables in code [mem]. What went wrong: extraction hangs off the build. Extractors pull compilation units from the build system, and users outside Bazel report broken setups and compiler mismatches [src: https://groups.google.com/g/kythe/c/UwteCqF4MTE, https://kythe.io/docs/kythe-compatible-compilers.html]. The schema page says nothing on versioning.

**Glean (Meta).** Predicates are tables, facts are rows; Angle defines both schema and queries. Derived predicates are stored (computed by `glean derive`, bottom-up, one layer per command) or computed at query time [src: https://glean.software/docs/derived/]. Each schema has a `SchemaId` (content hash); once landed only compatible changes pass; a break needs a new version, and `evolves` answers old queries from new facts [src: https://glean.software/docs/schema/changing/]. Incremental indexing stacks immutable databases, but fanout limits it: a changed C++ header reprocesses every dependent, so cost is O(fanout) [src: https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/]. For TypeScript Glean does not write its own indexer; it runs scip-typescript and converts with `scip-to-glean` [src: Glean documentation, "LSIF TypeScript indexer" page].

**CodeQL / Semmle QL.** A per-language extractor writes a relational database whose schema is a `.dbscheme`; QL is object-oriented Datalog for "concise queries based on reusable libraries" [src: https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.ECOOP.2016.2]. The CLI checks the database schema against the library; a mismatch needs `database upgrade`, and there is "no public-facing specification for the syntax of schemas" [src: https://codeql.github.com/docs/codeql-overview/codeql-glossary/]. Incrementality came late: overlay databases need CLI 2.23.8+ and build-mode none, and the 2026 PR speedup covers only the default suite [src: https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/scan-from-the-command-line/incremental-analysis, https://github.blog/changelog/2026-03-24-faster-incremental-analysis-with-codeql-in-pull-requests/].

**LSIF and SCIP (Sourcegraph).** LSIF was one graph with opaque, globally incremented ids. Sourcegraph's reasons to drop it: no machine-readable schema, unreadable payloads, global ids make it "difficult ... to update an existing index", 4x larger gzipped. SCIP is per document, with readable string symbols and a protobuf schema [src: https://sourcegraph.com/blog/announcing-scip]. The spec now lives in `scip-code/scip`, active the week of the study [src: GitHub repo page].

**jQAssistant.** Scanners fill Neo4j. Rules are Cypher: concepts add labels and relations, constraints return violation rows, `requiresConcept` orders them. A concept that matches nothing only warns, "since the concept probably does not match the code structure". A baseline file keeps old violations and drops entries no longer found. The store resets on every scan by default [src: https://jqassistant.github.io/jqassistant/current/]. Large 1.x scans ran out of memory [src, thin: https://kiwix.lema.org/stackoverflow.com_en_all_2023-11/questions/38369189/jqassistant-reduce-memory-consumption].

**Soufflé and Doop.** Facts from bytecode, analyses in Datalog. Doop was ported from LogicBlox to Soufflé and gained parallelism from the engine alone [src: https://pldi17.sigplan.org/event/soap-2017-papers-porting-doop-to-souffl-a-tale-of-inter-engine-portability-for-datalog-based-analyses]. Each engine has "its unique flavor of Datalog", which blocks interoperability [src: https://www.pl.informatik.uni-mainz.de/files/2024/10/datalog-modules.pdf]. Rules in a dialect get ported when the engine changes.

**NDepend CQLinq.** C# LINQ over a code model; the default rules are editable CQLinq [src: https://www.ndepend.com/docs/cqlinq-syntax]. With a baseline, rules run on both snapshots, and `WasAdded()` and debt deltas drive quality gates [src: https://www.ndepend.com/docs/cqlinq-features]. Issues pair when rule and code element match; unpaired means added [src: https://www.ndepend.com/API/NDepend.API~NDepend.Issue.IIssuesSetDiff~IssuesAdded(ICodeElement).html]. That is Hoglint's growth check with a location-free key. What a rename does to pairing could not be verified.

**Moose and FAMIX.** One language-independent metamodel. Users said entities "have too many properties that do not make sense for them", such as Java's `isAbstract` on a Function [src: the Moose developers' mailing list archive]. FamixNG moved to per-language metamodels composed from small traits [src: https://www.academia.edu/66409229/Modular_Moose_A_new_generation_software_reverse_engineering_environment, https://modularmoose.org/developers/predefinedentities/].

**Salsa and rust-analyzer (ty also uses Salsa [mem]).** A per-file stub index works only when "the indexer can't touch other files" and names stay unresolved; Salsa tracks dependencies and stops invalidation when a recomputed value is unchanged [src: https://rust-analyzer.github.io/blog/2020/07/20/three-architectures-for-responsive-ide.html]. Invariant: "typing inside a function's body never invalidates global derived data" [src: https://rust-analyzer.github.io/book/contributing/architecture.html]. Even so, no-op changes cost up to 90 ms of re-validation until durability levels [src: https://rust-analyzer.github.io/blog/2023/07/24/durable-incrementality.html, via search summary].

**GitHub stack graphs.** Per-file name-binding graphs, cacheable per file. Archived Sep 9, 2025 [src: search result for github.com/github/stack-graphs, not opened]. Per-language binding rules were the upkeep cost [mem].

## 2. Fixed primitives or a query language

Every general language here (QL, Angle, Cypher, CQLinq, Datalog) has the same split: a small specialist group writes libraries and queries, everyone else reads results.
CodeQL ships a default suite, and GitHub's incremental work covers only that suite [src: changelog above].
Glean offers "language-neutral views" so clients avoid per-language queries [src: Glean post]; Angle authors are tool builders [mem].
NDepend users mostly tune the shipped rules [mem].
jQAssistant hands Cypher to users and then needs warnings for concepts that silently match nothing [src: jQA docs].
Soufflé shows the porting cost of a dialect.

Boundary tools that reached product teams use fixed contract types with a plugin escape: import-linter's five types plus custom ones [src: https://import-linter.readthedocs.io/en/stable/], Packwerk, tach, Nx tags (`docs/research/research-round-1-prior-art.md`).

For hoglint: a language belongs where facts are derived (Glean stored derived predicates, jQAssistant concepts); fixed primitives belong where policy is judged.
Hoglint already drew this line: derived edges are producer code, rules are 11 primitives.
The selector language ("selectors are a small expression language", in the draft) is where a query language can creep back.

## 3. Reuse: can scip-typescript feed Hoglint?

[run] scip-typescript 0.6.1 (TypeScript 6.0.3) on the PostHog checkout's one root tsconfig, which covers `frontend/` and `products/*/frontend/` (about 15k TS files, 2.9M lines in those trees).
On a developer laptop: 12 min 46 s wall, 11.2 GB max RSS, 14.6 GB peak footprint.
Output: a 672 MB index, 16,785 documents, 5.4M occurrences.
A streaming read-back took 4.4 s and 0.9 GB and gives 81k first-party file-to-file import edges.
A rough area mapping (product dir or `frontend/src/scenes/<x>`) finds 4.8k cross-area file edges; that mapping is not a Hoglint rule.
For scale: Hoglint's Python L1 parses 25.5k files in 2.55 s.

What it emits, checked on a fixture and in `dist/src/FileIndexer.js` [run]:

- An imported name points at the origin after re-exports: `import { charge } from 'lib/index'` yields `src/lib/impl.ts/charge().`.
- The module specifier gets its own occurrence on the file it names (`src/lib/index.ts/`). So the surface survives per statement, not per name. The via chain is not stored; a barrel has no symbol of its own for a re-export.
- No occurrence carries the `Import` role; the code sets only `Definition`. Telling an import from a use needs syntax.
- `import type` and value imports look the same, so `type_only` is lost.
- `await import('lib/impl')` is recorded, so literal dynamic imports are visible.
- Symbols carry the npm package of the nearest `package.json`. Product packages have a name and no version, so symbols read `npm @posthog/products-x HEAD ...`: a stable product id for free. A versioned package puts its version in every symbol; that must never reach a ledger key.
- Known scale problems: OOM on 16 GB machines [src: https://github.com/sourcegraph/scip-typescript/issues/167]; a ~2 GiB index the JS protobuf reader cannot load, still open [src: https://github.com/sourcegraph/scip-typescript/issues/407]. Speed is bound by type checking, 1k-5k lines/s [src: https://sourcegraph.com/blog/announcing-scip-typescript].

Verdict: scip-typescript can supply reference edges and re-export origins, as it does for Glean.
It cannot be L1: it needs the whole type-checked program, drops `type_only` and the per-name surface, and is not per file.
A syntax producer fits the design's seam: oxc parses TS in Rust, and oxlint's resolver reads tsconfig `paths` [src: https://oxc.rs/docs/guide/usage/linter/multi-file-analysis].
That gives per-file Binding facts (specifier, names, `type_only`, dynamic, re-exports) like the Python L1.
oxc was not measured on this tree.
Run scip-typescript the way the django pack runs oracle jobs: off the commit path, to check that oxc resolution agrees.

## 4. Does Hoglint repeat the mistakes?

| Mistake | Who hit it | Hoglint |
|---|---|---|
| Extraction tied to the build or a boot | Kythe, CodeQL traced builds | Avoids. L1 is syntax only; Django boots only in oracle jobs (as the draft has it). |
| Opaque or unstable ids | LSIF ids; Kythe signatures | Mostly avoids: string ids, ledger identity without lines. But `Edge.id` hashes `ordinal-in-scope` (data model section 4); an earlier same-surface use shifts it. Fine run-local, wrong if it persists. |
| Schema change without a version contract | CodeQL dbscheme; Glean SchemaId + `evolves` | Partly. Cache key includes schema (adversarial-review fix 10), edges carry producer version, growth runs the base scanner. Nothing says what a renamed edge kind, shape or context does to `hoglint.ledger`. ArchUnit's orphaned stores are the same failure (`docs/research/research-round-1-prior-art.md`). |
| Generic model carrying one language's fields | FAMIX | Partly. Entity id is language-qualified, but core `Edge` fixes `shape` kinds (`drf-model-serializer`, `lock`, `write`) and contexts (`migration`, `app_ready`, `admin`) that are Django words (data model sections 3, 4). The core adversarial reviewer caught the shape half only. |
| Full rebuild, incrementality retrofitted | jQAssistant; CodeQL overlay 2025-26 | Avoids: L1 cached by content, L2 full at ~6.7 s. The stub-index architecture, sound while L1 facts stay file-local. |
| Fanout | Glean C++ headers | Partly. "Inventory change re-resolves everything" is fanout; harmless while L2 is a full recompute. |
| Query language for policy | jQAssistant, Soufflé | Avoids at rule level. Watch selectors. |
| No producer for the second language | Kythe, Glean, SCIP solved it with one indexer per language, one format | Repeats. Frontend cross-product imports grew from 107 to 859 lines unchecked; no producer planned. |

## 5. Changes, ranked

1. **Plan a TS L1 producer on oxc; keep scip-typescript as an oracle.** It emits Binding facts with specifier, names, `type_only`, dynamic and re-export, resolved through tsconfig `paths`. Package id comes from the `products/{product}/` glob (99 of 101 product `package.json` names follow `@posthog/products-{name}`; desktop and mobile do not). Lesson: every fact store here covers a mixed repo with one indexer per language into one format, and scip-typescript costs a full type check and 11 GB per run. Answers B1.
2. **Move shape kinds and context values from the core Edge into the language pack, as declared enums.** Core keeps kind, src, surface, targets, via, resolution, plus a tag set the pack declares and config validates. Lesson: FAMIX split its generic metamodel into per-language traits.
3. **Hash the fact and edge schema and write it into the ledger header.** A changed hash runs growth under both schemas, as for scanner versions; a renamed vocabulary entry needs a declared mapping, like Glean's `evolves`; otherwise the run is incomplete, never silently clean. Lesson: CodeQL dbscheme, Glean SchemaId, ArchUnit stores.
4. **State that `Edge.id` is run-local and never a ledger or exception key.** Lesson: LSIF ids, Kythe signatures.
5. **Cap selectors: no joins, recursion or aggregation.** Anything needing one becomes a derived-edge producer with declared inputs, built bottom-up. Lesson: jQAssistant concepts that match nothing; Glean's derive ordering.
6. **Keep the full L2 recompute; gate wall time on the real tree.** If incrementality is ever needed, add a per-module export summary as a firewall with early cutoff, not per-edge tracking. Lesson: rust-analyzer ItemTree and durability; Glean fanout. This confirms the choice of that date.
