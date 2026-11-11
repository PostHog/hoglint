# Replacing tach `depends_on`: second opinion

> Two rounds of a second opinion from another model on what replaces tach's declared dependency lists, Nov 5, 2026.
> Partly replaced: the agreed result is in `docs/research/depends-on.md`, and the rules are in `docs/design/04-rules.md`.

Round 1 answered the question independently.
Round 2 answered the architect's view (`docs/research/depends-on.md`, "The architect's view before the second opinion").
Both rounds were read-only measurements of the public PostHog monorepo.

## Round 1

**Recommendation: replace blanket `depends_on` declarations with enforced graph constraints plus a generated edge inventory. Keep explicit dependency allowlists only where there is a specific architectural reason. Ratchet cycles by directed package edge, not SCC size.**

The second opinion inspected a checkout of the PostHog monorepo from Oct 13, 2026.

### 1. `depends_on` protects dependency choice, independently of public-surface access

A public-surface rule answers "which parts of B may callers use?".
A dependency declaration answers "may A depend on B at all?".
A perfectly encapsulated facade call still creates coupling.

History shows the distinction:

- `861859df2e8` added **dashboards → canvas**, explicitly for resolving a canvas widget through its facade.
- `649e788ef48` added **canvas → access_control**, explicitly through the public permission facade.
- Both changes needed dependency declarations despite using the intended public surface. Removing declaration enforcement would remove that additional checkpoint.
- Conversely, an existing declaration permits more imports along that relationship without another dependency-policy change. It does not constrain how deeply A relies on B's public API.

**That checkpoint is not an approval by the dependency's owner.** A declaration list only adds a step; it does not make the owner of the depended-on package agree.

The consumers also matter:

| Consumer | What it actually does |
|---|---|
| tach lint wrapper | Checks dependencies excluding tests; checks public surfaces including tests. |
| product maturity score | Awards 15 points for zero cross-product declarations, subtracting five per dependency. Ignores `posthog` and `ee`. |
| product lint | Checks that declaration references exist. This is referential integrity, not architectural judgment. |
| CI test selector | Uses **real imports from `tach map`**, including tests, for dependent cascades. It explicitly does not use `depends_on`. |

The maturity advice is misleading: it suggests removing declarations by routing through facades.
**Facade calls remain dependencies**, as the two commits above show.
Do not preserve this scoring heuristic.

History shows evolving bookkeeping, not a coherent approved dependency DAG: isolated and non-isolated layers were introduced while explicitly saying enforcement was not in CI, later flattened, and declarations were repeatedly extended for product moves and integrations.
The lists preserve visibility and stop undeclared relationships.
They do not preserve acyclicity or a foundational-layer direction.

### 2. Replace the universal allowlist with complementary mechanisms

| Option | Catches | Misses / cost |
|---|---|---|
| Keep every declared dependency | Every new direct relationship, including an acyclic, facade-only one | Allows cycles and bad direction once declared. O(edges) handwritten policy, stale entries, mechanical updates. Valuable only if someone actually evaluates additions. |
| Generate package-edge inventory | Makes every new observed relationship reviewable; removes stale declarations automatically | Visibility alone blocks nothing architectural. Requires deterministic generation and a CI freshness check; reviewers can still rubber-stamp it. |
| Cycle rule | Circular coupling of any length | Acyclic spaghetti and wrong-direction dependencies. Cheap graph analysis, but requires meaningful package boundaries and debt handling. |
| Tier / zone / tag rules | Wrong-direction and forbidden-domain relationships, even through public APIs | Allowed-direction overcoupling and same-tier cycles. Smaller policy, but classification and exceptions require judgment. |
| Targeted allowlist | A deliberately closed subsystem's dependency budget | Manual upkeep, but limited to places where the restriction has a stated purpose. |

Recommendation:

- **Generate the actual package-edge inventory,** with added and removed edges and representative source locations in review output. Route relevant changes to source and target owners. This keeps the useful visibility without pretending the inventory is normative policy.
- **Enforce cycles and selected tier or zone constraints.** Examples: foundational permission logic must not acquire feature dependencies; shared query execution must not depend on dashboards or AI orchestration.
- **Keep explicit allowlists as an optional `edge` rule** for genuinely closed components. Do not require every product to repeat its imports in configuration.
- **Keep separate graph views:** production dependencies for architecture; tests and supported registration and dynamic references for CI impact. Move the CI test selector to hoglint's actual graph, not its inventory or debt ledger.
- Make unclassified modules and unresolved labels visible failures where a rule requires classification. Three-valued selectors must not turn "unknown tier" into permission.

This fits the useful lessons from the cited systems:

- **Packwerk:** dependency permission and public API access are distinct. Shopify's retrospective names protecting the base layer as a concrete success, and warns that imposing the wrong package graph creates costly indirection. Its strict dependency mode also prevents automatic debt growth. [Shopify retrospective](https://shopify.engineering/a-packwerk-retrospective), [Packwerk usage](https://github.com/Shopify/packwerk/blob/main/USAGE.md).
- **Nx:** multidimensional tag constraints express architectural categories more economically than enumerating every legal pair. They still require sensible tags; tags do not design the architecture. [Nx boundary rules](https://nx.dev/docs/kb/enforce-module-boundaries).
- **ArchUnit:** layers and cycle checks are independent executable architectural assertions. Its freezing mechanism shows incremental adoption, but hoglint should choose package-edge identities rather than incidental diagnostic locations. [ArchUnit guide](https://www.archunit.org/userguide/html/000_Index.html).
- **Go:** forbids direct and indirect package import cycles without a separate internal dependency permission list. That shows the value of graph properties, not that Python imports have Go's runtime semantics. [Go specification](https://go.dev/ref/spec#Import_declarations).
- **Martin's ADP and SDP:** acyclicity and dependency direction toward stability are separate requirements. Fan-in indicates responsibility to consumers; it does not establish a stable contract. Do not turn a fan-in/fan-out formula into policy automatically. [Martin](https://objectmentor.com/resources/articles/Principles_and_Patterns.pdf).
- **Parnas and Lakos:** the payoff is usable and testable lower-level subsets and independently understandable components. Imports approximate Parnas's semantic "uses" relation; levelizing SCCs only orders the knots, it does not untangle them. [Parnas](https://users.ece.utexas.edu/~perry/education/SE-Intro/parnas.pdf), [Lakos](https://www.informit.com/store/large-scale-c-plus-plus-volume-i-process-and-architecture-9780133927719).

### 3. The graph supports foundational capabilities, but not yet a clean binary classification of whole products

Unique directed `depends_on` edges between declared product modules, first excluding `posthog` and `ee`.
These are declarations governing production dependencies, not proof that every declaration has a live production import.
The 95 nodes include legacy and stub declarations.

| Measurement | Products only | Products + `posthog` + `ee` |
|---|---:|---:|
| Nodes | 95 | 97 |
| Directed edges | 365 | 599 |
| Nontrivial SCCs | One, containing **41** products | One, containing **79** nodes |
| Singleton SCCs | 54 | 18 |
| Two-way pairs | **43** | **143** |
| Directed edges inside nontrivial SCCs | **206** | **588** |

The 41-product SCC spans most of the analytics, data, AI and workflow products.
The two-way pairs are much broader than a few accidental reciprocal imports.

Fan-in and fan-out below count **other products**, not core:

| Product | Fan-in | Fan-out | Interpretation |
|---|---:|---:|---|
| access_control | 49 | 1 | Strong foundational candidate |
| product_analytics | 20 | 10 | Shared query capabilities mixed with feature integration |
| tasks | 18 | 16 | Shared execution plus orchestration; not automatically foundational |
| warehouse_sources | 17 | 12 | Shared capability with substantial coupling |
| feature_flags | 15 | 11 | Foundational capability inside a larger product |
| notifications | 15 | 1 | Strong foundational candidate |
| signals | 14 | 27 | Largest outbound orchestrator |
| actions | 13 | 6 | Shared analytics domain |
| cohorts | 13 | 2 | Shared analytics domain |
| dashboards | 12 | 14 | Composition surface, not a base layer |
| event_definitions | 8 | 0 | Strong foundational candidate |
| data_tools | 7 | 0 | Foundational data primitives candidate |
| analytics_platform | 3 | 0 | Shared computation infrastructure candidate |

Core coupling is large:

- `posthog → products`: **73**; `products → posthog`: **84**.
- `ee → products`: **34**; `products → ee`: **41**.
- `posthog ↔ ee` adds two edges.

**Do not label all of `posthog` "foundation".**
It contains application composition and legacy product code as well as infrastructure.
A blanket "core never gains product dependencies" rule would obstruct legitimate registration and product extraction.
Apply that direction rule to identified infrastructure modules; give composition roots a separate zone and narrow permitted entrypoints.

The second opinion also did a read-only textual import comparison (the dev environment was not set up, so tach's resolver could not run).
The scan excluded test and eval paths and collapsed absolute Python imports:

- 339 candidate product edges; **338 after removing one inspected docstring false positive**.
- Those 338 contain a 41-product SCC and 37 two-way pairs.
- 28 declared edges were not observed.
- The one observed-but-undeclared edge was a `TYPE_CHECKING` import.

This is corroboration, **not an authoritative replacement graph**: it misses relative and dynamic imports and does not generally distinguish type-only code or docstrings.
"Not observed" is not "safe to delete".

For foundational classification:

- **Start with access_control, notifications, event_definitions, and selected data_tools and analytics_platform capabilities.** Their dependency shape supports it. `access_control → event_definitions` and `notifications → access_control` already form a sensible downward chain.
- **Split capabilities before blessing whole feature_flags or product_analytics.** Flag evaluation and configuration are foundational; dependencies back into feature products contradict a whole-product foundation label.
- **Product analytics' query layer is a candidate, not already clean.** Separate reusable query primitives from presentation and integration orchestration.
- **High fan-in alone is insufficient.** tasks and signals have substantial orchestration responsibilities and outgoing coupling. API churn was not measured, so these are structural candidates, not demonstrated longitudinal stability.

This informs cycle-breaking order: remove upward dependencies from heavily consumed capabilities first, then protect them.
It informs facade design: expose small capabilities rather than one broad product facade.
It informs isolation priorities, but does **not** replace the Isolated criteria: prioritize demonstrated CI savings and tractable boundaries.
Foundational domain code can stay product-owned under `products/`; moving it into `posthog` does not improve its architecture.

### 4. Ratchet the set of cyclic package edges

```text
cyclic_edges(G) = {(A, B) ∈ edges(G) where B can reach A}

growth_key = (rule_id, source_package_id, target_package_id)
```

Baseline the existing set.
Fail any newly cyclic directed package edge; remove debt entries when the relationship disappears or stops being cyclic.

For the declared product graph, that is **206 entries**, not thousands of import sites or a potentially enormous number of individual cycles.
Generate the ledger grouped by source package.
Use stable package identities, not SCC hashes, line numbers or witness paths.

Why the alternatives fail:

- **SCC size:** permits arbitrary new coupling among the existing 41 members. A size budget can also permit replacing members.
- **Exact SCC membership or subset:** prevents expansion and merging, but still permits new cycles and edges inside the existing knot.
- **Two-way pairs:** misses `A → B → C → A`.
- **Enumerated cycles:** combinatorial, redundant and unstable under minor changes.
- **Per-import debt:** much too fine for package-cycle policy; punishes internal file moves without changing architectural coupling.

The edge rule catches both new chords inside an existing SCC and SCC mergers.
Previously acyclic edges that become cyclic also become new violations.
Deduplicate the user-facing report around the introduced edge and a short return-path witness.

Its deliberate limitation: **another import along an already cyclic package edge does not grow cycle debt**.
Public-surface and use-shape rules govern that finer detail.
A rule that limits concentration of coupling would be a separate rule.

Start with the product projection and separately scoped foundational-module rules.
Keep the full core-inclusive graph visible, but do not mistake its 79-node SCC for an actionable architectural unit.
The ledger describes accepted bad relationships; it must never become a permission to create arbitrary new relationships among accepted bad neighbors.

## Round 2

### 1. Drop the checked-in edge inventory. The architect is right.

The second opinion could not name a decision the inventory supports that rules plus `hoglint compare` cannot support.
Print added and removed package edges and representative imports in PR output.
Check in policy and violation ledgers only.

### 2. Protect infrastructure, not the `posthog` and `ee` namespaces. Hold the objection, but start narrower.

The sampled imports are materially different:

- `posthog.api.__init__` delegates to `rest_router`, which discovers product route registrations.
- `posthog.urls` mounts product views; settings register product apps and schema hooks.
- `posthog.models.__init__` re-exports event-definition models: compatibility debt, not generic infrastructure.
- `ee.hogai` is product orchestration.
- `posthog.caching` includes dashboard and insight computation; `posthog.storage` includes flag metadata and LLM prompt caches. Directory names alone do not establish infrastructure.

Proposed initial classification, as **config shape, not hoglint syntax of that date**:

```yaml
zones:
  composition:
    files:
      - posthog/api/__init__.py
      - posthog/api/rest_router.py
      - posthog/urls.py
      - posthog/ee_urls.py
      - posthog/product_urls.py
      - posthog/products.py
      - posthog/apps.py
    trees: [posthog/settings]

  infrastructure:
    trees:
      - common
      - posthog/egress
      - posthog/db_backends
      - posthog/kafka_client
      - posthog/personhog_client
      - posthog/clickhouse/client
    files: [posthog/redis.py, posthog/cache_utils.py]

  legacy_mixed:
    remainder: [posthog, ee]

rules:
  infrastructure_product_direction:
    forbid: infrastructure -> products/*
    growth_key: [source_capability, target_product]
    ledger: shrink_only
```

**Estimated initial ledger: 1 edge,** from one infrastructure module into a foundation product's error class.
A production Python import scan found no others in these roots; this is an estimate pending resolver verification.

Composition may depend on product registration interfaces.
Existing direct view mounts stay subject to public-surface rules and can migrate to registration interfaces.
`legacy_mixed` gets no blanket product-dependency ban: extraction must remain possible.
It is explicitly **not certified infrastructure**; promote individual capabilities after inspection.

This protects less than the architect's blanket rule, but protects something meaningful.
Registration discovery changes wiring; it does not remove the application's dependency on registered products.

### 3. Enforce a small foundational tier now. The emphasis moves from candidates to an immediate rule.

```yaml
tiers:
  foundation:
    - products/access_control
    - products/notifications
    - products/event_definitions
  feature:
    remaining_products: true

rules:
  foundation_direction:
    forbid: foundation -> feature
    graph: production_imports
    growth_key: [source_product, target_product]
    ledger: shrink_only
```

**Initial ledger: 0 cross-product edges,** on both declared dependencies and the sampled imports.
The existing permitted chain is:

```text
notifications → access_control → event_definitions
```

Use whole-product labels for these three at first.
Leave data_tools and analytics_platform outside the minimal foundation set until their contracts are assessed; zero outbound product dependencies alone is not enough.

Concrete decisions this changes:

- Feature-specific notification rendering or permission integration belongs in the consuming feature or an adapter, not inside these foundations.
- Break cycles around flag evaluation and reusable query execution by separating feature orchestration before promoting those capabilities to foundation.
- Expose small permission, notification and event-definition contracts; avoid feature-aware umbrella facades.
- Keep these capabilities product-owned. "Foundation" does not mean moving domain code into `posthog`.

This first rule constrains direct cross-product dependencies; it does **not** certify independence through legacy `posthog` helpers.

### 4. Corrections

- The round-1 inventory recommendation added maintenance without additional decision value.
- The architect's "tiers later" leaves a useful, zero-debt constraint unenforced.
- Round 1's **107** against the architect's **109**: **107 core → product declarations**; including `posthog ↔ ee` gives **109**, but those two are not product edges.
- Keep the agreed baseline of **206 cyclic directed product edges**, to be recalculated on the actual import graph. Keep the CI test selector on that real graph.
