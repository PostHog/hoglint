# Prior art, round 2: second opinion

> A second opinion from another model on the second prior-art round and the second blind round, Nov 9, 2026. It checked the repository claims and the evidence behind each recommendation, then ranked the design changes.
> Partly replaced: the decisions that followed are in `docs/history/decision-log.md` and its continuation files.

The second opinion read the architect's synthesis (`docs/research/prior-art.md`), the three studies, and the blind-round results.
It worked read-only on the PostHog monorepo at `d1051a3875f`.

**Verdict: keep the architecture; tighten the safety contract before enforcement.**
The repository measurements largely reproduce.
Several recommendations are sound engineering judgments presented as stronger empirical conclusions than the citations warrant.

## Repository claims

The Python environment was not set up, so the counts below were reproduced with read-only text scans and git history, not with the designers' AST scripts.

| Repository claim | Verdict |
|---|---|
| Wiring channels a lint must watch | **Confirmed as a design need:** a boundary lint must watch every wiring channel that a test skip depends on. |
| Hardcoded product app list is complete | **Confirmed: 91 entries, 91 product `apps.py` files; no missing or extra products.** |
| Hub co-edit ratios | **Confirmed:** Celery worker **93/119 (78%)**; beat schedule **97/108 (90%)**; Temporal schedules **89/107 (83%)**; query runner **108/137 (79%)**, since May 10. Claimed CI savings remain **unverified**: co-editing does not establish causation or safe replacement tests. |
| 421 single-product schema classes | **Confirmed with a scope correction:** **1,462 classes across `schema.py` + `schema_enums.py`**, not `schema.py` alone. Direct named-import buckets reproduce **421 / 153 / 112 / 224 / 552**. Sole importer establishes usage, **not ownership**. |
| Cross-product URLs | **Confirmed by text scan:** **302 builders, 318 distinct file/key uses, 161 product pairs** through the shared URL surface. These are navigation dependencies, not automatically forbidden implementation dependencies. |

## Prior-art recommendations

| Prior-art recommendation | Evidence verdict |
|---|---|
| Prefer diff-time feedback | **Supported.** Infer reports near-zero batch fixes versus **over 70%** at diff time using the same analysis. This does not prove the same effect for resurfacing old debt. [Infer paper](https://6826.csail.mit.edu/2020/papers/facebook-infer-cacm.pdf) |
| Never block on uncaused incompleteness | **Stretched as a universal rule.** Google supports actionable checks and analyzer-owner accountability; it does not establish that inherited uncertainty is "100% false", or justify an absolute zero-incomplete-delta upgrade gate. [Google paper](https://storage.googleapis.com/gweb-research2023-media/pubtools/4365.pdf) |
| Reject manifests because they become stale | **Stretched.** Missing providers can fail at runtime, but that supports validation, not the inherent superiority of convention. Several cited failure anecdotes are explicitly memory-only. [ServiceLoader specification](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/ServiceLoader.html) |
| Report kinds within already-allowed package pairs | **Reasonable inference, overstated attribution.** Passos documents incorrectly filtered dependency types; that is not precisely hoglint's pair-only comparison problem. [Passos](https://homepages.dcc.ufmg.br/~mtov/pub/2010_ieeesw.pdf) |
| Ship live feedback earlier | **Supported directionally.** The reported **~60% reduction** is experimental evidence, not a forecast for PostHog; the author explicitly calls for replication. [Knodel](https://fb-swt.gi.de/fileadmin/FB/SWT/Softwaretechnik-Trends/Verzeichnis/Band_29_Heft_2/06-knodel.pdf) |

## Ranked design changes

1. **Specify Isolated independently of lint acceptance.** Resolves an open decision; preserves "unknown is never clean". Publish required rules and per-rule debt treatment. Ledger entries and exceptions never erase impact edges. Safety-relevant unknowns or uncovered wiring prohibit skipping; architectural debt may coexist with Isolated only when impact remains conservatively covered.
2. **Separate completeness, violations and merge disposition.** Changes exit-precedence handling; resolves ownership. PR-introduced gaps belong to the author; extractor failures belong to the tool owner; existing wiring gaps involve product owners. Inherited incompleteness may permit merging with the full required suite, but remains visibly incomplete. Never let a non-blocking exit 2 conceal blocking violations.
3. **Close wiring coverage before narrowing further.** Extends existing producer work. Watch bootstrap, settings middleware, hooks and relevant initialization dependencies. Add registry reachability checks and lifecycle-specific runtime audits. Runtime observations detect omissions; they cannot prove all possible dynamic edges absent.
4. **Choose conventions, retain explicit bootstrap configuration.** Resolves convention versus `product.yaml`; revises the blanket product-list ban. Keep `product.yaml` ownership-only; declare loader, slot, shape and lifecycle centrally. Preserve the reviewed product app list, middleware ordering and worker topology. Validate membership; propagate nested import failures.
5. **Keep the exceptions-file split; define its semantics.** Preserves the agreed split. Specify match keys, required fields, stale-entry pruning and review-date behavior. Require affected provider owners for boundary allowances, the tool owner for policy relaxations. Expiry must remain visible without suddenly blocking unrelated PRs.
6. **Make comparison explain attribution and moves.** Refines existing decisions. Show new **pair/kind/context** combinations, zero-net consumer swaps, and code-versus-policy removals. Evaluate policy effects on both snapshots; recognize verified relocation cleanup. Document every rule's identity and growth key, and version the schema.
7. **Require declared aggregate provenance; stage TypeScript support.** Refines generated-code policy. File ownership suffices for simple outputs; aggregates need symbol origins. Use the 421 classes as migration candidates only. Plan syntax-based TS facts with SCIP as an offline oracle, retaining a conservative fallback until coverage exists.
8. **Repair the guide and bring advisory feedback forward.** Changes rollout priority. Add a known private-zone fallback, explicit FK, receiver and DTO semantics, Isolated examples and move examples. Blind task 2 missed consumer swaps; task 4 contradicted its preassigned package identity. The answer key itself leaves FK and move-admission semantics unsettled: this is not a clean usability pass.

## Rejected

- Automatically moving all 421 types.
- Requiring every ABC subclass to register.
- Treating a single runtime boot as completeness proof.
- Replacing full-suite coverage with merely declared contract tests.
- Automatic safety-rule demotion at a borrowed 10% noise threshold.
- "Most-specific glob wins" without demonstrated need.

These either infer intent from usage, overclaim coverage, or weaken explicit policy.
