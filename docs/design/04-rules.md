# 4. Rules: primitives, scoping, states

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 16, 2026.

## Decided

**Eleven primitives; everything else is config** (architect, Nov 4):
`edge`, `entrypoint`, `export`, `signature`, `use-shape`, `reach`, `cycle`, `layout`, `coverage`, `level`, `inventory`.
- No rule DSL.
  Selectors have no joins, no recursion and no aggregation.
  Anything that needs one of those is a derived-edge producer.
- Rules are TOML.
  The primitives are a closed set, and no plugin adds one (D38, then D39).
- A rule type is declared once, on its class: native mode, entities, and the keys it reads per mode.
  The schema, the plan and the test generator derive from that declaration (D53).
- The native evaluator judges every rule; the rule's class words each row it gives (D51).
  How one destination is judged from its selection, resolution, unknown type, verdict and `unless` is the table `tests/rules/judgment.txt`; how `and`, `or` and `not` carry an unknown is `tests/rules/connectives.txt`.
- A key a rule does not read is a config error (D52).
  A limit is a config error (D46).
  Set fields hold strings only and have one text form (D45).

**Projection v1: typed labels, three-valued selectors, PostHog as a preset** (agreed by exclusion, Nov 9).
- Each field declares its domain (`required_on`).
  Outside it, the value is a known `none`.
- Typos are config errors.
- A rule defined twice is an error; replacing a rule and adding or removing list items are explicit (review blockers 1 and 2).
- Product files outside the zone table fall into a default private zone.

**A side with no value** (owner, Nov 12).
A field outside its domain reads a known `none`.
A field inside its domain that nothing sets reads `unknown`.
A comparison reads them so:

| Comparison | One side `none` | Both sides `none` | One side `unknown` |
|---|---|---|---|
| `a == b` | false | true | unknown |
| `a != b` | true | false | unknown |
| `a in b`, `a startswith b` | false | false | unknown |
| `a not in b` | true | true | unknown |
| `a subset_of b` | `none` is the empty set | true | unknown |
| a bare `a` | false | | unknown |
| `not (...)` | the opposite | the opposite | unknown |

- The keyword `none` is a side with no value: `a == none` is true when `a` has none.
- Why: a rule that denies must not pass an edge because a label is missing.
  So a missing side differs from every value, and only `unknown` stays open.
- The cost: two ends that both have no value count as equal.
  A rule that must not match third-party code says so (`dst.external == none`).
- The evaluator and the Python selector parser read one table of these cases (`tests/rules/missing_side.txt`).

**Named conditions** (owner, Nov 12).
`[conditions]` maps a name to selector text; a selector names it wherever an expression may stand, and the name reads as its text in parentheses.
- The config expands each name when it loads, so neither evaluator sees one.
- An `and` inside an `and` joins it, and so does an `or` inside an `or`, whether parentheses or a name made it: a rule stages each part of its selection on its own.
- A name is one word with no dot, and never a field, a built-in attribute, a keyword or `src`, `dst`, `edge`.
- Conditions are flat: one that names another is a config error, so a rule still reads by itself with one lookup.
- A condition no selector uses is a config error, as a key no code reads is (D52).

**How a rule is scoped.**
A rule selects on labels, never on a folder alone:
- **zones**: for example `facade`, `presentation`, registries, `internal`.
  They come from `[zones]` with `within = "products/{product}"`, so every product gets every rule;
- **tiers and roles**: infrastructure, foundation, feature, composition, proto-product;
- **contexts**: `prod`, `test`, `type_only`, `function_local`, `migration`.
  Type-only imports are out of dependencies, reach and the cascade, and in entrypoint rules (architect default);
- **exports with shapes**: see `09-contract-surfaces.md`.
- Example: the loose-type check `facade-signature` already selects `src.zone == 'facade'`, so it does not flag presentation code.

**Graph rules replace a hand-kept `depends_on` list** (owner, in principle, Nov 5, subject to a deliberation with a second opinion; that deliberation is done; confirmed by exclusion Nov 9):
1. no new cyclic directed package edge; the ledger holds the cyclic edges of the day;
2. infrastructure never depends on a product; a composition zone may use product registration;
3. foundation never depends on feature.
- `compare` prints new (package pair, edge kind, context) triples.
  No edge list is checked in.

**Which rule families block at adoption: all of them, with the existing debt in the ledger** (owner, Nov 9).

**Rule states today:** enforced (blocks new debt; existing debt is in the ledger) and `off` (added by the architect, Nov 11).

**A reach stops at a denied module, unless it says otherwise** (Nov 12).
A `reach` reports each denied module it meets and does not enter it, whatever the order of the imports.
With `through_denied = true` it goes on through a denied module, so every denied module on a chain past the first is reported too, each with its chain.

**An attribute a producer could not tell reads unknown** (Nov 12).
An edge's `unknown` attribute lists, comma-separated, the attributes its producer could not tell.
A rule reads each of them as `unknown`, never as a missing value.
The Django pack uses it for `setting`, the name of the setting a string of a settings module sits in.

**A name the config writes out in full must exist** (Nov 12).
A literal path of an assignment that no file has, and a symbol an assignment or an export names that no symbol has, are incomplete items of `config:stale-name`.
The config loads before the tree is read, so only a run can tell.

**A prefetched model is a model crossing** (Nov 16).
`prefetch` is no allowed shape of the PostHog preset's `model-crossings`: the rows a `Prefetch(..., queryset=Model.objects...)` loads are instances of the model on the consumer's objects, and the old crossings scanner counts them.
No decision had allowed the shape.

**Unresolved names:** the default is a `finding` with an `unresolved-*` kind, after a `not-applicable` state.
A producer failure makes the rule incomplete.
Never `ignore` (architect default, Nov 4).

## Why

- Primitives plus config: the 54 rules of the five tools mapped to 11 primitives (47 mapped, 3 dropped, 4 plugins).
- Before a rule counts as "config", it needs a re-derivation of the old tool's output, not only a schema fit (lesson from a second opinion's review, Nov 4).
- Graph rules: `depends_on` never prevented cycles (one strongly connected component of 41 products, 43 two-way pairs at the time).
  It is admission control, not isolation, and no dependency owner signs it.
- All families block: the ledger is generated, so blocking adds no second config.
  An advisory rule turns into a batch burndown, which the prior art warns about (FindBugs and Infer: a fix rate near zero for batch reports, against over 70 % for findings on the diff).
- A rule type declared once: the same facts were restated in seven places, and each fix corrected one copy (a review of the evaluator's structure, Nov 10).

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| Keep a hand-kept `depends_on` edge list | 425 to 700 edges since August 2026, and nobody reviews it | By exclusion, Nov 9 | `docs/research/depends-on.md` |
| A rank field (infrastructure < foundation < feature) | Cannot express same-rank bans, audiences or cycles | Round 3, Nov 6; by exclusion | `docs/research/exports-and-shapes.md` |
| "Core gains no product dependency" | Too blunt: composition and legacy code live in the core package | Second opinion, round 2, Nov 5 | `docs/research/depends-on-second-opinion.md` |
| New families start advisory | Batch burndown | Owner, Nov 9 | not published |
| Automatic demotion to advisory by a noise budget | Weakens explicit policy; the 10 % threshold was borrowed, not measured | Second opinion, Nov 9 | `docs/research/prior-art-second-opinion.md` |
| "Most-specific glob wins" | No demonstrated need | Second opinion, Nov 9 | `docs/research/prior-art-second-opinion.md` |
| A rule DSL or a general query language | Only specialists write them (QL, Cypher) | Architect, Nov 4 and Nov 9 | `docs/research/data-model.md`; `docs/research/prior-art.md` |
| Rule grouping (one verdict per label class) | Three holes found by reading; a whole class of bugs | Deleted in N1 (architect) | `docs/history/decision-log-2.md` |
| Ruff-style per-file ignores for migration debt | They also exempt future violations in those files | Second opinion, Nov 11 | `docs/research/second-opinion-ledger.md` |

## What would change the answer

- The second opinion's test for a scope: "Would this code remain acceptable here after the migration is finished?"
  Yes: scope the rule.
  No: it is debt.
  Unclear: keep the rule out of enforcement.
- A cycle graph measured on real imports.
  The cyclic-edge count of Nov 5 was taken on declared dependencies.

## Open

- **Ruff-like rule codes** (preset rule codes, engine diagnostic codes, `hoglint rule <code>`): proposed Nov 5, not decided.
- **A warn severity.**
  tach has one; hoglint only blocks or ledgers.
  The question (Nov 9): was a warn state considered, or must every state be deterministic?
  No answer is recorded.
- **An `experimental` state** (from the second opinion) and an advisory `check --changed` (agreed in the prior-art round) are not specified together.
- **Rule renames:** a `renamed_from` key was proposed for the owner to decide.

## Sources

`docs/research/data-model.md` (sections 7, 11, 12, 15, 16, 19); `docs/research/depends-on.md`; `docs/research/exports-and-shapes.md`; `docs/research/adversarial-reviews.md`; `docs/research/prior-art.md`; `docs/research/prior-art-second-opinion.md`; `docs/history/decision-log-2.md` (D38, D39, D45, D46); `docs/history/decision-log-3.md` (D52, D53); `docs/research/second-opinion-ledger.md`; not published (the design page, the progress log).
