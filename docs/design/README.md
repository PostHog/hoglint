# hoglint design record, by topic

> The design record of hoglint, sorted by topic, compiled Nov 11, 2026.
> Current as of Nov 11, 2026. The chronological history is in `docs/history/`; the studies behind it are in `docs/research/`.

hoglint is a boundary linter for large Python and Django monorepos, built at PostHog.
Its design was worked out between Nov 3 and Nov 11, 2026, by its owner and an architect (an AI agent), with many reviews, spikes and second opinions.
Everything was logged as it happened.
That log is complete but chronological, and decisions were revised in place.
These pages sort it by topic.

Each page has the same sections: what is decided now, why, what was considered and rejected (with the reason and the evidence), what would change the answer, what is open, and the sources.

## Before you propose a change

1. Find the topic below, and read its **Decided** and **Considered and rejected** sections.
2. Read `traps.md`.
   Most proposals that look obviously right are in it.
3. If your idea is in a "rejected" row, say in your proposal what changed since: a new measurement, a changed condition from "What would change the answer", or a new decision by the owner.
4. Then read the cited source itself.
   These pages are a map; the sources are the evidence.
5. When something is decided, add it to the topic page and to the decision log.

## Who decided

- **The owner:** a decision the owner stated.
  Pages say "owner, <date>".
- **The architect:** everything else.
  The owner can veto any of these.
- **By exclusion:** choices where every alternative failed in some review round; the owner confirmed them on Nov 9.
- A proposal stays a proposal until someone decides it.
  An owner's question or remark is not a decision unless it answers a specific choice.

## Pages

| Page | One line |
|---|---|
| `01-purpose-and-principles.md` | What hoglint replaces; the fixed principles (the test skip's safety, no edge deleted, unknown never clean, a cache never wrong, no framework names in core, exact runs, one graph engine with every changed row named) |
| `02-policy-exceptions-ledger.md` | Three files; ledger identity, equality and growth; growth per product (D67); move admission; owners; no noise in the ledger |
| `03-levels-and-isolated.md` | The Isolated test skip specified apart from lint acceptance; who owns an incomplete result; levels from rules |
| `04-rules.md` | Eleven primitives; selectors and domains; scoping by zone, tier, context and export; graph rules; rule states |
| `05-engine.md` | A Python shell over PyO3; Rust per-row work; one engine that builds the graph and judges every rule, Python wording its rows; port order; the pack API |
| `06-fact-cache-and-summaries.md` | The fact database keyed by blob; run summaries keyed by input set; the cache rules; cache size and bound |
| `07-speed-and-memory.md` | Every measured state with date and conditions; what worked and what did not; the lessons about estimates |
| `08-fast-hook.md` | The hook on every commit in 2-3 s; exact reuse in stages; salsa, prewarm and a fast subset mode turned down |
| `09-contract-surfaces.md` | Exports with shapes; what a facade hands out; signature slots, aliases, dataclass fields |
| `10-findings-triage-method.md` | How findings are triaged before the first ledger write; kinds of noise and tool defects |
| `11-repo-and-packaging.md` | The own repo, the wheel, the toolchain |
| `12-how-work-is-done.md` | Gates, independent verification, reviews; what went wrong and the rule each mistake left |
| `traps.md` | Ideas that look right and were turned down: design, engine and process |
| `glossary.md` | The words a newcomer cannot guess, each with the config key or module where it lives |

## Terms

The most used terms are below; `glossary.md` has the rest, each with where it lives in the config and the code.

- **Edge:** a coupling from one module or symbol to another (an import, a call, a subclass, a framework wiring).
- **Finding:** an edge or entity that a rule judges a violation.
- **Ledger:** the generated file of accepted existing findings, one line per (rule, identity).
- **Identity:** the key of a ledger line; **growth key:** the subset of the identity that growth is judged on.
- **Pack:** a producer of framework-specific edges (Django, or a company's own, such as PostHog's), under `hoglint/packs/`.
- **Preset:** a reusable policy file (for example `presets/posthog.toml`).
- **Export:** a declared public surface of a package, with a shape policy; PostHog calls one kind a facade.
- **Level:** a named set of rules that a product passes or fails.
- **Named delta:** a change between two engine versions where every changed row is listed and explained.
- **Phases** 3 to 4c and N1 to N6 are build steps of the native engine; **spikes** N1 to N9 are measurements in scratch before a decision; **stages** S0 to S7 are steps of the reuse plan.

## What is not here

PostHog's own policy decisions, the findings triage of PostHog's code, adoption plans, and the owner's own words are not published.
Where a page cites one of these as a source, it says "not published".
