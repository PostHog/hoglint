# Replacing tach `depends_on`

> The decision on what replaces tach's declared dependency lists, with the architect's first view and the fan-in measurement, Nov 5, 2026, after two rounds with a second opinion.
> Partly replaced: the cycle, infrastructure and foundation rules are in `docs/design/04-rules.md` and `presets/posthog.toml`; the second opinion's full text is `docs/research/depends-on-second-opinion.md`.

The question: tach's `depends_on` lists in the PostHog monorepo declare, per product, which other products it may depend on.
What does hoglint put in their place?
The architect wrote a view first, then a second opinion from another model answered independently (round 1), then both answered each other (round 2).
All numbers are measurements of the public PostHog monorepo on Nov 5, 2026.

## What `depends_on` protects

- **A checkpoint on new package dependencies,** even facade-only ones. Two commits show it: `861859df2e8` added a dashboards → canvas dependency, and `649e788ef48` added canvas → access_control. Both used the intended public facade, and both still needed a declaration.
- **Not a policy anyone signs.** A declaration list that does not need the dependency owner's approval is bookkeeping, not a review.
- **Not test selection.** PostHog's CI test selector uses real imports from `tach map`, not `depends_on`.
- **Not acyclicity or direction.** The declared graph has one strongly connected component (SCC) of 41 products, 43 two-way product pairs, and 206 cyclic directed product edges. There are 107 core → product declarations. A read-only import scan by the second opinion agrees (a 41-product SCC, 37 two-way pairs, and 28 declared edges never observed in imports).
- **Readers to migrate:** a maturity score that awarded 15 points for zero cross-product declarations (misleading, dropped), and a lint that checked only referential integrity.

## Result (both agree after round 2)

1. **No checked-in edge list.** `hoglint compare` prints added and removed package edges with representative imports in PR output.
2. **Cycle rule: no new cyclic directed package edge.** The ledger is the set of cyclic edges (206 on the declared dependencies; to be recomputed on the real import graph in build slice 2 of the first plan). Growth key: (src package, dst package).
   Rejected: SCC size, SCC membership, two-way pairs, enumerated cycles, per-import identity.
3. **Infrastructure direction, not namespace direction.** Core is split into zones:
   - composition: `posthog/api/__init__.py`, `posthog/api/rest_router.py`, `posthog/urls.py`, `posthog/ee_urls.py`, `posthog/product_urls.py`, `posthog/products.py`, `posthog/apps.py`, `posthog/settings/**`. May depend on product registration interfaces.
   - infrastructure: `common/**`, `posthog/egress`, `posthog/db_backends`, `posthog/kafka_client`, `posthog/personhog_client`, `posthog/clickhouse/client`, `posthog/redis.py`, `posthog/cache_utils.py`. Must not depend on products. Estimated ledger: 1 edge, from one infrastructure module into a foundation product's error class.
   - the remainder of `posthog` and `ee`: legacy and mixed, no blanket ban; capabilities are promoted to infrastructure after inspection.
4. **Foundation tier now:** access_control, notifications, event_definitions. Rule: a foundation product never depends on a feature product. Ledger: 0 edges. The existing chain notifications → access_control → event_definitions is allowed.

The architect's original "core gains no product dependency" was too blunt: composition and legacy code live in `posthog`.
The second opinion's round-1 edge inventory added maintenance without decision value.
The second opinion moved the foundation tier from "later" to "now" because it has zero debt.

## What the foundation tier changes

- Feature-specific notification rendering or permission integration goes in the consuming feature or an adapter, never into a foundation product.
- The next candidates need a split first: flag evaluation (inside feature_flags), reusable query execution (inside product_analytics), data_tools, analytics_platform. Cycle breaking starts by removing upward dependencies from heavily consumed capabilities.
- Small capability facades, not feature-aware umbrella facades.
- Foundation stays product-owned; it never means moving code into `posthog` core.
- High fan-in alone does not qualify (tasks and signals orchestrate).

## The architect's view before the second opinion

Written Nov 5, before the second opinion answered.

Replace tach `depends_on` with three hoglint rules:
1. No new or growing package cycles (the cycle primitive). The SCC of that day (41 products on declared dependencies) and the 43 two-way pairs go in the ledger, shrink-only.
2. Base layer: core (`posthog`, `ee`, `common`) gains no product dependency. The 109 declared core → product edges are ledgered and drained by inversion (the `register_routes` pattern).
3. Later, Nx-style tag constraints, for example "foundational products must not depend on feature products".

No hand-kept or generated edge list.
New facade dependencies that close no cycle pass silently.

Basis:
- `depends_on` is admission control without an owner's approval.
- The test selector reads the import map, not `depends_on`.
- The list grew from 163 to 600 entries and never prevented a 41-product cycle.
- Web prior art: the Packwerk retrospective (base-layer value; todo files grew), Nx tags plus no cycles, the Acyclic Dependencies Principle, Go's import-cycle ban, Parnas's uses hierarchy.

Open at that point: whether a foundational/feature split already made sense, which products, and whether it informs other decisions.
Round 2 settled the first two (see "Result" above).

## Fan-in measurement

The architect counted declared product-to-product dependencies (core excluded): fan-in is how many other products declare a dependency on this one, fan-out how many this one declares.
Instability is fan-out / (fan-in + fan-out), as in Martin's metric.

| product | fan-in | fan-out | instability |
|---|---:|---:|---:|
| access_control | 49 | 1 | 0.02 |
| product_analytics | 20 | 10 | 0.33 |
| tasks | 18 | 16 | 0.47 |
| warehouse_sources | 17 | 12 | 0.41 |
| notifications | 15 | 1 | 0.06 |
| feature_flags | 15 | 11 | 0.42 |
| signals | 14 | 27 | 0.66 |
| cohorts | 13 | 2 | 0.13 |
| actions | 13 | 6 | 0.32 |
| cdp | 12 | 3 | 0.20 |
| dashboards | 12 | 14 | 0.54 |
| data_modeling | 10 | 5 | 0.33 |
| alerts | 9 | 4 | 0.31 |
| event_definitions | 8 | 0 | 0.00 |
| exports | 8 | 8 | 0.50 |
| experiments | 8 | 15 | 0.65 |
| data_tools | 7 | 0 | 0.00 |
| ai_observability | 7 | 5 | 0.42 |
| error_tracking | 7 | 7 | 0.50 |
| surveys | 6 | 7 | 0.54 |
| workflows | 6 | 9 | 0.60 |
| data_warehouse | 6 | 10 | 0.62 |
| notebooks | 5 | 5 | 0.50 |
| alerts_platform | 4 | 1 | 0.20 |
| batch_exports | 4 | 2 | 0.33 |

The table lists the top 25 by fan-in.
The same count found 43 two-way pairs.
