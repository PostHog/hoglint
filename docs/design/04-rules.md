# 4. Rules: primitives, scoping, states

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 16, 2026 (evening).

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
The preset has no `off` key yet: a rule that is off is a commented-out table with its reason (`own-database`, `atomic-outside-views`, D118).

**The PostHog preset's rule scope** (D118, Nov 16).
`generated-producer` and `registry-slots` left the preset: the monorepo's own generated-files test and the registry loader check the same at run time, and neither rule had a producer.
`own-database` (no producer, a policy never decided) and `atomic-outside-views` (no edge reads `transaction.atomic`) are off for now.
`migration-database` stays, slim (D117, below).

**Rows the first-ledger triage placed elsewhere** (Nov 16; the architect's answers to the triage).
- `no-product-lists-in-core` takes `public-surface`'s allowances: frozen migrations, settings naming product paths, a declared permanent interface for core.
- `no-product-lists-in-core` judges only the core registration points (the hub rule, the architect, Nov 16): the modules the preset marks `wiring = "registration"` (the Temporal worker and schedules, the query runner, the Celery schedule, `posthog/apps.py`, the Max presets).
  Core calling a product's facade elsewhere is allowed (D93); a threshold on how many products a module names was rejected, since it reports facade calls and loses the per-product Max presets.
  Limit: a new hard-coded product list in a core module that is not marked as a registration point is not caught.
- `object` as a mapping's value is an opaque payload in a facade parameter or field, as `Any` is; never in a return value.
- An edge whose end hoglint cannot tell is incomplete in the graph rules (`no-new-package-cycles`, `foundation-direction`), and the cycle rule leaves migrations out: a migration names models as they were.
- `receiver-direction` and `get-model` read an unplaced sender and an unreadable label as incomplete, not as findings.
- `model-crossings` leaves a use to `public-surface` when that rule reports the module the code named (A5, one rule per coupling): the selector reads that module as `surface.<field>`, next to `dst` at the origin. Kept (the architect, Nov 16): a re-route through a facade drawer shows again as a `model-crossings` row, and `export-shape` flags the drawer.

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

**Three import contracts of the monorepo are preset rules** (Nov 16).
They had no hoglint rule, so a new violation would fail the old import checker and pass hoglint.
Each reads every import, in tests, under `TYPE_CHECKING` and inside functions, as the old checker does, and each contract's ignore list is part of the rule.
- `sources-vendors-use-sdk` (`edge`): a warehouse source vendor imports no core, `ee`, `common` or product module outside the sources tree.
  The SDK may import core and products, its testing module `common`, the tree's conftest products.
- `sources-vendors-independent` (`reach`): the members of the sources tree (each vendor, the SDK and the conftest; field `source_vendor`) do not import each other, directly or through other modules of the tree.
  An import of the SDK is no path: the vendors share it.
- `webhook-consumers-use-facade` (`edge`): a product's webhook consumers module imports product backend code only through a facade.

Their limits, in plain words:
1. The independence rule follows a chain only inside the sources tree.
   A chain that leaves the tree starts with a finding of `sources-vendors-use-sdk`.
2. A webhook consumers module that imports a test module of a product is `prod-imports-test`'s finding, not this rule's.

**`migration-database` is slim: the repo's own migrations only** (owner, Nov 16, D117; it replaces the stage 2c reading of third-party migrations).
The monorepo's Django test stays the authority for the migration graph; hoglint's rule is early feedback.
- The Django pack reads no migration of an installed third-party app, even where `[discovery] external` gives the run its sources; a dependency key into such an app makes no node and no edge.
- A key list in a `Migration` class body that hoglint cannot read (`dependencies += [...]`, a computed value) is an unresolved edge, and the rule reads an unresolved edge as incomplete: never silent, never a block.
- `bin/parity` explains an edge or a crossing of the Django test that has a third-party app at either end by D117, checked per item: the app's module is no first-party module or package.

Reading a package from `[discovery] external` still gives its models and the names it re-exports to every other rule.

**Two couplings the engine places** (Nov 16).
- A module-level function that hands one of its parameters on to `<signal>.connect(...)` is a hook, found in the code like the configured ones: a call of it is an `installs` edge from the caller's module, the callback's entry module.
  Only a Django signal's `connect` counts; a database driver's `connect` makes no hook.
- The `surface` endpoint of an edge that resolves to library code reads as the graph module the code names, when one names it: a patch target `products....module.client.send` is the product module's, since the test depends on that module binding `client`.

**A call through an instance attribute the class types is a call of that class's member** (the architect, Nov 16).
In a method, `self.family.register(...)` names the member `register` of the one class `self.family` holds, so a call whose last name is a hook's but that resolves to such a member is no hook call.
The extractor records what each method assigns to attributes of its first parameter; the class is read from the attribute's annotations (in the class body or on an assignment), else from every value the class assigns it: a call of a class, or an item of a module-level container whose annotation (`dict[K, V]`, `list[V]`, `tuple[V, ...]` and their `typing` and `collections.abc` names) or literal items give one class.
Only the class's own body and methods count, never a base class or code outside the class.
Anything else stays unresolved, and so incomplete: a static or class method, a nested function's `self`, two classes, a value it cannot type (a function's result, an assignment by unpacking, `+=`, a loop or `with` target, one in a function the method defines), an attribute the class also defines as a method or property.
A member is named by its class (`Family.register`).
A hook the config names through a module-level instance (`registry.console.register_moved_page`, `console = ConsoleRegistry()`) is a method of the instance's class: a call that resolves to that member of that class is a call of the hook.
A call that resolves to the member of a class the instance's class inherits, or of any class when the instance is no call of a class hoglint can tell, may reach the hook too: it is an unresolved `installs` edge, so incomplete, never clean.
A same-named member of an unrelated class is no hook call.

**A receiver with no sender listens to its signal** (the architect, Nov 16).
`@receiver(signal)` or `signal.connect(fn)` with no `sender` is a `signal` edge to the signal itself: the module that defines a signal is what fires it, so `receiver-direction` and `wiring-watched` read the signal's package as the sender's.
A receiver on Django's or Celery's own signals goes to third-party code and crosses no package; one on a core or another product's custom signal crosses.
A signal that does not resolve leaves the edge unresolved, as a variable sender does.
A `connect` in a hook's own body (a module-level function that hands its parameter on to it) has the attr `hook`; `wiring-watched` does not ask for that module, since the hook's callers are placed as `installs` edges.

**A loop variable bound over a literal sequence takes each of its values** (the architect, Nov 16).
In `for model in RULE_MODELS:` the extractor gives a watched call in the loop's body one `calls` row per value, with the variable replaced where the call passes it as a whole argument, so each `post_save.connect(..., sender=model)` is one placed signal edge per sender.
The sequence is a tuple or list literal, written in the `for` or bound to a name the file binds exactly once, in the module or in the loop's own scope.
Anything else stays as written, and so unknown: a name bound twice or by anything but that assignment, a list whose attributes the file uses (`X.append(...)` can change it), a starred element, a loop variable the body binds again, a call in a lambda or a comprehension (it runs later), an empty sequence, and more than 64 rows for one call.

**A package with a declared door reads `door`** (architect, D107, Nov 12; built Nov 16).
`door` is a built-in attribute of a module: true when an export's `within` holds its path.
- It reads the path and the config only, as a zone does, so a change that adds or moves files needs nothing new from the change predictor.
- A rule that guards the way into such a package selects `dst.door`; no list of package names stands in the rule.
- The other reading, "a package that has a module an export covers", depends on which files exist anywhere in the package: rejected.
- A directory without `__init__.py` reads it from the path that labels it; a third-party module reads `none`.
- An export with an empty `within` holds every path, so every module reads `door` as true.

**A string is a path only where the code holds one** (Nov 16, from the first-ledger triage).
A string that names a module is a `dotted-path` edge in a sink or a path place, and text anywhere else.
- Sinks: a patch target; the first argument of a loader or of a callee `[discovery] path_callees` names (by last name, or by last dotted parts such as `patch.dict`), and an element of a list or tuple there (an argument vector, as in `python -m`).
  A string there is an edge also when it names nothing.
- Path places: a module a pack names as one whose strings are all paths (the Django pack's settings modules, from `[django] settings`), a value of a module's lazy (PEP 562) map, a string whose text a loader or a sink's callee takes as its first argument through a loop over a literal sequence (`for path in MODULES: import_module(path)`), and any string of a file that hands a computed value (a name, an f-string) to a sink's callee.
  A string there is an edge when it names a module.
- Why: the old product lint's data and its tests (`frozenset({"products.warehouse_sources"})`, parametrized product names) read as couplings and failed two products' level.
- The written limit: the facts hold no flow from a name to where the code uses it, so a file that patches or loads through a name keeps every string of it that names a module, data too.

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
