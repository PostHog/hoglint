# Spike N1: labels and plain edge rules per edge in Rust

> A throwaway prototype and its measurements, Nov 10, 2026: the gate at the start of engine phase N1.
> Partly replaced: phase N1 was built afterwards and gained no time on one thread; see "N1 as built" in `docs/research/native-engine-design.md`, and `docs/design/05-engine.md`.

**The question.**
How fast are labels and the plain edge rules when Rust runs them per edge, with no grouping, and do the judgments equal the Python engine's exactly?
The Python engine of the time grouped edges into classes and judged one representative per class, to make per-edge Python affordable.
The spike had to give a CPU time that can be trusted, plus proof that every label and judgment equals the Python per-edge path.

The prototype ran on the fixed commit dc2271eba47.
Nothing was committed.
It used a captured snapshot of the target repo with the preset laid over it.

## Scope reached

- All 17 rules of primitive `edge` and `entrypoint` in the preset, public-surface included. None left out.
- Plan: one JSON (3.7 MB) from the real loader and the real `Labeler`: fields and domains, module assignments, zones, registries, exports with audiences, kind member and unknown sets (carried, unused: no rule in scope reads `kind`), and per rule its kinds, context bits, endpoint role, `on_unresolved` and selector syntax trees.
- Labels in Rust: every module field that is not provider-set or symbol-level (package, tier, role, zone, stage, django, testcode, generated, producer), plus `registry`/`lifecycle` and export coverage per module.
- Judging in Rust: `EdgeRule.decide` per (edge, destination), no grouping, no classes; Kleene logic as `selectors.evaluate`. A Rayon pool over (rule, 4096-edge chunk).
- One column read from Python: export membership by class (`subclass_of`, the presentation-adapter export), as {symbol: true|unknown} for symbols under covered paths. It needs class ancestry (inherit facts plus pack-written `bases`). It affects only `dst.exported` reads of destinations under `backend/presentation/`. It is built in the plan step (0.29 s CPU for the whole plan, not in the Rust timings).

## Proofs

- Labels: 290,380 (module, field) values, 29,038 modules x 10 columns, against `Labeler.field_value(module, None, field)` (registry against `module_labels`). Differences: 0. Conflicts: 0 on both sides.
- Judgments: for each rule, the multiset of (edge id, judgment) that is not clean, against the Python per-edge path (`link.candidates`, then `rule.edge_envs` and `rule.decide` per edge). 3,648,152 pairs judged on both sides; 30,290 kept on both sides; differences: 0 in every rule.
  - Kept by rule: public-surface 21,095 (37 unknown-selection, 5 unresolved), no-product-lists-in-core 2,709 (1 unknown-violation, 33 unresolved), facade-logic 3,302, presentation-uses-facade 1,557, drives 396, thin-commands 344, get-model 245, reverse-accessor 242, prod-imports-test 138, installs 98, dynamic-imports 75, receiver-direction 60, facade-no-drf 25, foundation 2, infrastructure 2, leaves 0, migration-database 0.
  - The kept counts also equal the grouped engine's survivors per rule.
- The Python per-edge oracle itself takes 12.3 s CPU.

## Measurements

`time.process_time` around each call; three pairs, each Rust run followed by the Python engine's rules phase in the same minute, measured under load.

| Pair | Rust labels CPU | Rust judge 1 thread CPU | Rust judge 4 threads CPU / wall | Python: in-scope rules CPU | Python: labels (`find_conflicts`) | Python: `Grouper.survivors` (all 23 grouped rules) | Python: rules phase |
|---|---|---|---|---|---|---|---|
| 1 | 0.076 s | 0.195 s | 0.195 / 0.055 s | 0.64 s (public-surface 0.42) | 0.24 s | 0.62 s | 2.12 s |
| 2 | 0.078 s | 0.197 s | 0.202-0.296 / 0.058-0.113 s | 0.69 s (public-surface 0.47) | 0.24 s | 0.68 s | 2.26 s |
| 3 | 0.077-0.089 s | 0.201 s | 0.201 / 0.057 s | 0.68 s (public-surface 0.43) | 0.24 s | 0.68 s | 2.19 s |

- Rust plan parse 0.006 s. The Rust judge figures include candidate selection and selector compilation; the one-thread column is the median of three.
- "Python: in-scope rules" is the sum of the 17 rules' inclusive timers (grouping, `Edge` objects and findings included). Grouping cannot be split per rule from the timers; over all grouped rules `Grouper.survivors` is about 30 % of the rules phase, `LinkEdges.get` another 15 %.
- Pairs judged 3,648,152 (the Python engine's 5.25 M includes use-shape, signature and cycle rules, out of scope); kept 30,290.
- Net on this scope, one thread: about 0.9 s (rules 0.66 + labels 0.24) becomes about 0.28 s (0.20 + 0.08), so about 0.6 s CPU saved. Four threads: judging wall 0.055 s, 3.6x.
- Not like for like: Rust returns (edge id, judgment) only; the Python rule time also makes findings (identity, owners, subjects, messages; `FindingClasses.finding` 0.09 s over all rules).
- Against the N1 gate (rules phase down by 0.7 s or more): this scope alone gives about 0.6 s. The rest of the phase (export, coverage, reach, signature, use-shape, cycle, levels: about 1.2 s at the time) is N1's identity and findings work and N2.
- Code written: Rust 1,500 lines (1,379 non-blank in one module, plus 2 lines to register it); Python 310 lines.

## Irregular or hard meaning met

- `equals` is asymmetric: zone chains compare by ancestry against a scalar but by name against another zone; a frozenset matches a member scalar, a list literal does not (Python compares its repr). `in`/`not in` use chain members; `subset_of` splits plain strings on commas.
- Bools render as `true`/`false` in comparisons, but config values fill via `str()` (`True`), and a set field unions `str(value)`.
- `read_endpoint` order matters: `symbol` answers even for an unresolved endpoint; builtins (`registry`, `lifecycle`, `subject`) come before `src.*`/`dst.*` edge attrs; attrs count only when the edge has any endpoint attr; an external endpoint's fields are none.
- `edge.shape_detail` is not a simple edge field: it reads an attr named `shape_detail` (so none at the time).
- The `src` endpoint's path is the site path, not the module's; `module_of_symbol` is the longest graph-module prefix.
- `Labeler.exported` caches by (src module, src path, dst module, symbol) and ignores endpoint attrs that an audience could read; Rust computes uncached. No difference on this snapshot, but it is a latent Python bug for pack edges with `src.zone`-style attrs.
- Two leaf zones matching with one override; default zone only under `within`.

## What a full N1 still has to solve

- Findings: identity projection (`render_value` of identity columns), owners (provider field), subjects, messages, remedies, ledger flags, incompletes for `on_unresolved = incomplete` and unknown selection, in the engine's rule order.
- Class ancestry and export membership by class in Rust (here a Python column); `kind`, `decl`, symbol-level fields (`database`), selector domains, provider fields (owners from CODEOWNERS).
- Failed producers and `expected` kinds (run_rules turns those rules into incompletes), conflict messages with sources.
- `edge.ordinal`; use-shape flow shapes; signature, export, reach, cycle, coverage, layout, inventory primitives (N2).
- A plan made without Python (config loader in Rust) and the stage switch, so Python and Rust run side by side in the parity tables.
