# Glossary

The words of hoglint that plain English does not explain, as the code, the config and these pages use them.
Each entry says where the term lives: a config key, a file, or a module.
Some words have two meanings; the entry names both.
`README.md` has the short list of terms that the design pages use most.

## Facts and the graph

- **Fact, record** (`src/facts.rs`, `hoglint/facts.py`).
  A fact is one row that the extractor writes about a file: an import, a def, an assignment, a use of a name.
  A record holds all the facts of one file.
  The code calls this layer L1; L3 is the labels (`hoglint/labels.py`).
- **Link** (`src/link/`).
  The Rust graph over the facts of a whole tree: module names, name resolution, class ancestry and the core edges.
  The Python side holds it as one opaque handle (`binding/src/link.rs`).
- **Edge** (`hoglint/model.py` `Edge`).
  One coupling from a site in a file to the code that the site names.
  Nothing deletes an edge: the ledger and exceptions only decide whether a finding blocks.
- **Edge kind** (rule key `kinds`; `CORE_EDGE_KINDS` in `hoglint/config/schema.py`).
  The family of an edge: `import`, `reference`, `inherit`, `exposes`, `dotted-path` and others; packs add their own, such as `signal`.
- **Surface** (edge field `surface`).
  The name as the code wrote it, never replaced.
  "Surface" also names a module of an export, as in "contract surface" and "surface file" (see **Slot**).
- **Endpoint: surface, origin, via** (rule key `endpoint`).
  Which end of an edge a rule reads as `dst`: the module the code named, the definition it resolves to after re-exports, or the modules in between.
  The default is `origin`.
  Whatever `dst` reads, `surface.*` reads the module the code named; in a coverage rule's edge cover, `via.*` reads a module in between, and the cover holds when its selection holds at any one of them.
- **Use** (edge field `use`; `CORE_USES`).
  How a reference uses the name: `call`, `attribute`, `subscript`, `annotation`, `base` and others.
  Core sets it with no framework meaning.
- **Chain** (edge attr `chain` of a reference edge).
  The attribute names, calls (`()`) and subscripts (`[]`) the code applies past the name the edge resolves to, as written: `.objects.unscoped().filter()` after a model.
- **Reference names** (`[discovery] reference_names`).
  Full dotted names whose every use is a reference edge, one per scope and attribute chain.
  Core makes none otherwise for a third-party name or a first-party function.
  An entry must name a definition, not a re-export; otherwise it is a `config:stale-name` item.
- **Flow shape, instance write** (edge attrs `flow_shape`, `instance_write`; the Django pack).
  The flow shape is the shape of a manager chain together with the later uses of the local it is assigned to (`qs = ...; qs.update()` is `write(update)`).
  The instance write names `save` or `asave` when the code calls it on the use's value, in its own chain or through such a local.
- **Listed** (edge attr `listed`; fact kind `listed`).
  `listed = "1"` on a reference edge whose name sits in a list, tuple, set or dict literal (past stars, `+` and the like), and on an import edge when every load of the name it binds in the file does.
  A name the file never loads, or loads in any other way, has no attr.
- **Context** (rule key `contexts = { include, exclude }`).
  Where the code of an edge runs: `prod`, `test`, `type_only`, `function_local` or `migration`.
- **Resolution, unresolved** (edge field `resolution`).
  Whether an edge's name was found: `resolved`, `candidates`, `unresolved`, `external` or `not-applicable`.
  A rule reports an unresolved edge as its `on_unresolved` key says: `finding`, `block` or `incomplete`.
- **Scope, ordinal** (`edge.scope`, `edge.ordinal`).
  The scope is the qualified name of the def that holds a site, or the module itself: the part of a site that does not move with an edit.
  The ordinal is an edge's place among the edges with the same module, scope, kind and surface.
- **Producer** (edge field `producer`, written `name@version`).
  Code that writes edges or refines them.
  Core producers run in the link; pack producers run in Python or in the link.
  A producer that fails makes the rules that need its edges incomplete.
- **Pack** (a directory of `hoglint/packs/` with its `pack.toml`, and `src/link/packs/`; loaded only by `extends`, such as `"hoglint:django"` or `"hoglint:posthog"`; set up in a table such as `[django]`).
  A set of producers that add one framework's or one repo's meaning to the graph, such as Django models, signals and settings, or PostHog's `drives`.
- **Preset** (a repo-relative path in `extends`, such as `presets/posthog.toml`).
  A reusable policy file.
  The code it needs is a pack, which the root config extends next to it.
- **Core** (two meanings).
  In hoglint, core is the part that names no framework or company: core producers, core shapes, core edge kinds.
  In the PostHog preset, "core" is also a value of the `tier` field: the monorepo's shared packages, as in "a class core defines".
- **First party** (`[discovery] first_party`).
  The top-level modules that are the repo's own code; any other name is external.
- **Witness** (finding field).
  An edge that a finding names as its evidence.

## Labels

- **Field** (`[fields]`, keys `type`, `values`, `on`, `required_on`).
  A typed attribute that labels give each module, or each symbol with `on = "symbol"`.
  Types: `string`, `enum`, `bool`, `set`, `zone` and others.
- **Label** (`hoglint/labels.py`, `src/link/rules/labels.rs`).
  The value of a field on one module or symbol, with the source that set it, so `hoglint explain` can name the config line.
- **Assignment** (`[[assign]]`, keys `match`, `set`, `override`, `except`, `only`, `except_paths`, `symbols`).
  Sets field values on the files that a glob matches, or on the symbols it names.
- **Zone** (`[zones]`, keys `within`, `parent`, `paths`, `override`).
  A named area inside each package, such as `facade` or `presentation`.
  Zones nest by `parent`; a zone field matches a zone and its ancestors.
  A file that no zone claims is in the default zone, `internal`.
- **Domain** (field key `required_on`).
  Where a field applies.
  Outside its domain a field reads `none`; inside it, a field that nothing sets reads unknown.
- **Unknown, `none`** (`hoglint/selectors.py` `State`, `Truth`).
  Unknown is the third truth value: hoglint cannot tell.
  `none` is a known absence: the field does not apply.
  Unknown is never clean: a comparison that reads it is unknown, and an edge attr `unknown` lists the attrs its producer could not tell.
- **Set two ways** (`src/link/rules/conflicts.rs`, D44).
  Two sources give one field different values on one entity, and neither says `override = true`.
  It is a config error, met where something reads the label.
- **Kind** (`[kinds]`, keys `subclass_of`, `decorated_by`, `abstract`, `decl`, `expected`; read as `dst.kind`).
  A named class of symbols, such as `model` or `queryset`.
  Do not confuse it with an edge kind.
- **Provider** (`[providers.<name>]`, keys `use`, `sets`).
  A plugin that sets labels from an outside source, such as a CODEOWNERS file.
- **Registry** (`[registries.<name>]`, keys `loader`, `module`, `attribute`, `shape`, `lifecycle`).
  A file that a loader imports by its path in every package, and the attribute it reads there, such as each product's `routes.py` and its `register_routes`.
  A module reads it as the built-in attributes `registry` and `lifecycle`.
  An edge endpoint also reads `registry_loader`: the registry whose declared loader is the function or method the edge sits in, none in the registry's own files.
- **Roles: owner, group** (`[roles] owner`, `[roles] group`).
  The field that names who owns a finding, and the field whose value groups modules into packages for cycles and `compare`.
- **`handout_zones`** (built-in attribute, as in `src.handout_zones`).
  The zones, with their ancestors, of the modules whose names a module hands out by import or by a rebind.
  A rebind of a class's member (`Kind = Model.Kind`) hands out the member and adds no zone.
  It is unknown when one of those zones cannot be told.

## Selectors

- **Selector** (`hoglint/selectors.py`, `src/link/rules/selector.rs`).
  A small boolean expression over labels with three truth values (Kleene logic), and no joins, recursion or aggregation.
  An edge selector reads `src.*`, `dst.*` and `edge.*`; an entity selector reads one module or symbol (a domain, `allow_symbols`, a level's `select`).
- **`from`, `select`, `deny`, `allow`, `require`, `unless`** (rule keys).
  `from` and `select` choose what a rule looks at; `deny`, `allow` and `require` say what is a violation; `unless` excuses one.
  How these give a judgment is the table `tests/rules/judgment.txt`.
- **Condition** (`[conditions]`).
  A named piece of selector text; the config expands each name where a selector uses it.

## Rules and results

- **Primitive** (rule key `primitive`).
  The rule type, one of eleven: `edge`, `entrypoint`, `export`, `signature`, `use-shape`, `reach`, `cycle`, `layout`, `coverage`, `level`, `inventory`.
- **View** (rule key `view`; `refs`, `file-imports`, `zones`, `packages`).
  A way to group edges that a rule or an output reads; `packages` folds many edges into one row per package pair.
- **Judgment** (`hoglint/rules/base.py` `Judgment`).
  What a rule decides for one destination: `clean`, `unresolved`, `unknown-selection`, `unknown-type`, `unknown-violation` or `violation`.
- **Rule order** (`Link::rule_order` in `src/link/rules/edges.rs`).
  The order in which a rule judges its edges: kind by kind as `kinds` lists them, then by source module, then graph order.
  Row order is part of the output, so the stored runs hold it.
- **Finding, subject** (`hoglint/rules/findings.py`).
  A finding is one violation that a rule reports.
  Its subject is the entity it is about.
- **Incomplete** (`IncompleteItem` in `hoglint/rules/base.py`; values of `on_unresolved` and `on_unknown`).
  Something a rule could not judge in full: a parse failure, an unresolved name, an unknown value, a failed producer.
  It is stored as an incomplete item, and an incomplete run exits 2, never 0.
- **Report: blocking, advisory** (rule key `report`).
  A blocking rule's new findings fail a check; an advisory rule's findings are a list that blocks nothing.
  `ledger = false` keeps no ledger lines for a rule.
  An advisory rule's unknowns never block; the report shows them as advisory unknowns.
  `compare` labels a policy change that stops findings blocking (a rule's `report`, `ledger` or `replace`) a relaxation.
- **Required file** (layout rule keys `within`, `files`).
  A path each root of `within` must hold.
  An entry may hold `{a,b}` alternatives, a file or a directory, and may end in `: function` or `: class`: the file must then also define a symbol with that declaration.
- **Cover** (coverage rule key `covers`).
  One thing a coverage rule needs covered: another rule's column (`rules.<rule>.<column>`), the paths of an edge selection at an endpoint (`<edge selector>: src`, `: dst` or `: surface`), or the files on a name's path (`: chain`: the module the code names, every module in between, and the origin), the files of the modules an entity selection keeps (`<entity selector>: module`), or the locations those modules stand in (`<entity selector>: location`).
  A location is the outermost package or directory without `__init__.py` that the selection keeps, at or above the module, else the module's own file.
  One covered file inside a location covers it.
- **Coverage adapter** (coverage rule key `inputs = { adapter = ... }`).
  A plugin that reads an outside list of covered paths, such as a build tool's task inputs, for a `coverage` rule, and for a level's `claimed_by`.

## Exports and contracts

- **Export** (`[exports.<name>]`, keys `within`, `paths`, `symbols`, `subclass_of`, `audience`, `shape`).
  A declared public surface of a package: the files, or the listed symbols, that other packages may use, with a shape policy.
  The design pages say an export is "a door plus a shape": the door is where other packages may come in, the shape is what may pass.
  The PostHog preset calls one export a "facade".
- **`door`** (built-in attribute, as in `dst.door`).
  True when an export's `within` holds the module's path, whatever its `paths` say: the module is in a package with a declared door.
  It reads the path and the config only, as a zone does; an export with an empty `within` holds every path.
- **Audience** (export key `audience`).
  A selector over the importer: who may use the export.
  It limits who uses an export, not what the export hands out.
- **Hand-out** (`src/link/surface.rs`; `HandOut` in `hoglint/model.py`).
  A name that an export module gives to its importers: its own public definitions, and the imports that the hand-out rule passes on (`__all__`, `import x as x`, lazy names, star imports, rebinds).
  An import the module keeps to itself is "kept".
  A kept name still reads as exported when the same export, under the same root, hands out its origin.
- **Slot** (edge kind `exposes`, attr `slot`).
  One typed place in a contract: a parameter, a return value, a field or an attribute of a callable or a class that an export hands out.
  Each type name in a slot is one `exposes` row.
  A surface file (a module of an export whose shape policy has `signatures = true`) gets a row for every type name, builtins included.
- **Copy** (of a slot).
  The rows of a slot that the producer also writes at a module that hands a name out, so a rule that reads that module judges them there.
- **Shape** (two meanings).
  Of a symbol: the core shape names `dataclass`, `frozen-dataclass`, `abc`, `enum`, `protocol`, `exception`, `function`, `constant`, `class`, plus the config's kinds.
  Of an edge: what a pack says a use means (edge fields `shape`, `shape_detail`), such as a Django `write` or `lock`, read by `use-shape` rules.
- **Shape policy** (`[shapes.<name>]`, keys `data`, `behavior`, `allow`, `deny`, `unknown`, `unknown_types`, `mappings`, `opaque_payload`, `signatures`, `allow_symbols`, `owned_elsewhere`).
  What an export may hand out: data shapes whose fields are walked, allowed and denied shapes, and what to do with a type it cannot tell.
  Each export names one with its `shape` key, and so may a signature rule: the config then writes the rule's selection, `deny`, `unless` and unknown types from the policy.
- **`allow_symbols`** (shape policy key).
  An entity selector: a symbol it holds for is allowed as it stands and is not walked.
  The PostHog preset uses it for the core models that may cross a contract (`may_cross == true`).
- **`owned_elsewhere`** (shape policy key).
  An entity selector: a class it holds for is its owner's contract, such as a class core defines.
  An allowed class that is no data shape is walked, so the types its members name are judged too, only where it does not hold or cannot be told; in a class it holds for, only a field of a denied kind is reported.
  With `behavior = "abc"`, a class whose lineage holds an abstract base it holds for is an allowed behavior class.
- **`slots_reported_by`** (export rule key).
  The signature rules that report the slots they read.
  In such a slot the export rule judges only the shape of a class, so one slot is reported by one rule.
- **`mappings`, `opaque_payload`** (shape policy keys, read by a signature rule that names the policy).
  The mapping types: one given type arguments is typed. With `opaque_payload = "except-return"`, an unknown type that is no mapping (`Any`) as a mapping's argument is an opaque payload: clean in a parameter or a field, unknown in a return value. The default, `"never"`, reads it as unknown everywhere.
- **`unknown_types`** (shape policy key; signature rule key).
  The types read as unknown in a slot, such as `typing.Any` and a bare `dict`.
  The export rule reads the list of the policy it walks under; a signature rule reads its own, or its policy's (`shape`).
- **Trail** (export finding).
  The chain of names from the exported symbol to the type that a finding reports, as in `OrderRejected -> Order`.

## Ledger and levels

- **Ledger** (`hoglint.ledger`; `[ledger] file`; `hoglint/ledger/`).
  The generated file of accepted existing findings, one line per (rule, identity), with a count.
  A fixed finding must leave the ledger, and growth from code fails.
- **Exceptions** (`hoglint.exceptions.toml`; `[ledger] exceptions`).
  Reviewed allowances that make a use allowed; a stale exception that matches nothing fails.
- **Identity** (rule key `identity`; `[identities]`; named identities in `NAMED_IDENTITIES`, such as `module-pair`).
  The selector refs whose rendered values key a ledger line.
- **Growth key** (rule key `growth_key`).
  The part of the identity that growth is judged on, against the merge base.
- **Attribution, move admission** (`hoglint/ledger/growth.py`).
  Attribution runs the base engine and policy on head code to tell growth from code from growth from a rule change.
  Move admission lets a move pass only when the same couplings existed before, with the same counts.
- **Level** (`[levels.<name>]`, keys `group_by`, `select`, `requires`, `strict`, `within_group`, `claimed_by`; `hoglint/rules/levels.py`).
  A named set of rules that each group, such as a product, passes or fails.
  A group passes when every required rule ran for it and none of its findings is new.
  The PostHog preset's level is `isolated`: a product on it may skip other products' test suites.
- **`strict`** (level key).
  The rules whose findings block the level even when the ledger accepts them.
- **`within_group`** (level key).
  The required rules whose rows count only inside one group: a row whose subjects fall in two or more groups does not count for the level.
- **`claimed_by`, claim** (level key; adapter method `narrowed`).
  The outside input in which a group claims a level, as a coverage adapter and its options.
  A group claims the level when one of its module files sits where the adapter says the inputs are narrowed; a claiming group that fails the level fails `check`, and no ledger line or exception accepts that.
- **Narrowed** (coverage adapter).
  Inputs that leave files of their package out, so a change there does not run the task.
  For turbo: a package's own `turbo.json` lists the task's inputs without `$TURBO_DEFAULT$`.

## Engine and checks

- **Plan** (`hoglint/native/plan.py`, `src/link/rules/plan.rs`).
  The config as data, which Python hands the evaluator once per run.
- **Evaluator** (`src/link/rules/`, `binding/src/evaluator.rs`).
  The Rust code that judges every rule and gives back rows; Python words each row.
- **Envelope** (`hoglint/ledger/envelope.py`).
  The cache key of a stored run summary: the inputs, the engine, the policy and the framework sources that made it.
- **Facts id, native id, engine id** (`engine-sources.txt`, `build.rs`, `src/sources.rs`).
  Digests of three source sets: the extractor, the native engine, and all of hoglint.
  Any change to a file of a set moves its id, a comment too, so rebuild before tests.
- **Model repository** (`tests/model_repo/`, `bin/model-repo`).
  A small repo of planted cases, with reviewed rows, table digests and every message stored.
- **Stored runs** (`tests/native/stored_rows.json`, `tests/native/generated_runs.jsonl`).
  The whole output of the hand-written rule cases and of one generated config per rule mode, stored, so a change that moves a row shows up.
- **Parity tables** (`hoglint/native/parity.py`).
  The ten normalized tables of a check (edges, symbol facts, modules, findings, levels and others) that two builds are compared on.
- **Named delta** (`AGENTS.md`).
  A change between two engine versions where every changed row is listed and explained.

## PostHog preset words

These belong to the PostHog preset, not to the engine.

- **Facade** (`[exports.facade]`).
  The preset's export for a product's public API (`products/<name>/backend/facade/`).
- **Tier** (field `tier`: `core`, `product`, `leaf`, `tool`).
  The kind of package a module is in; it is a field like any other.
- **Wiring location, wiring registration** (field `wiring`).
  A wiring location is a product module that core runs through a registration, such as tasks, workflows and query runners.
  A wiring registration is the core module that names it to register it.
- **Drives** (edge kind `drives`, producer `posthog.drives@1`).
  A test outside a product that runs the product's wiring.
- **Watched** (two meanings).
  In the preset, a watched file is one that a product's contract-check inputs hold.
  The `wiring-watched` rule checks that the wiring locations other packages drive, the receivers and hook callbacks, and the modules the request pipeline reaches are watched.
  The `reached-watched` rule checks the same for every product file that code outside the products reaches past the export.
  In the extractor, a `watched` row is a dict entry whose key is one of the `--watch-keys` strings.
- **Kind of an expression** (`Walker::kind_of`, `Shape` in `src/walker.rs`).
  What the use reading knows of an expression, from one function at bindings and uses alike: whether its type is proved builtin, the tracked lists it may be, hold items of or be an item of (its sources), whether it may be an item, whether it is a record.
  An escape of it is an escape of every source.
- **Stable class** (`Producers::stable_class` in `src/link/produce.rs`).
  A class whose attribute read returns what its body binds: metaclass `type` or `abc.ABCMeta` along its lineage, no decorator but `dataclasses.dataclass`, no store into it from any file, every occurrence of it accounted, the attribute bound once at the body's top level.
- **Module value, `!` row** (an `escapes` row `!VALUE`).
  A use of a value that may be a module, a class or a function, in a place that is no attribute read and no call; the link resolves it and trusts no record class, `dataclasses.replace` or class read back that it may reach.
