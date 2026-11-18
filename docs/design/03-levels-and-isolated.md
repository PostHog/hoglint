# 3. Levels and the Isolated test skip

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 17, 2026.

Background: in the PostHog monorepo, a product on the Isolated rung skips other products' test suites when only that product changes.
The skip is sound only if no coupling is missed.
The doctrine is in the monorepo's `products/architecture.md` ("What makes the skip sound").
hoglint expresses rungs like this one as **levels**: named sets of rules that a product passes or fails.

## Decided

**What Isolated means** (owner, Nov 16, D108; its reader scope widened to any code outside the product by D123, the architect, Nov 17, for the owner's call).
A product's Isolated level is blocked only by what can make its test skip miss tests, and these block even when the ledger accepts them:
1. code outside the product (core, `ee`, `common`, tools, `bin`, or another product; production or test) reaches a product file past the product's export, or reads a name a facade module only passes on, and the product's contract-check inputs leave out the file, or a file on the name's path to the file that defines it (T7, D120);
2. a product's own presentation reaches its own internals past its facade;
3. unwatched wiring, with its incomplete items.

A product also earns the level only with the structure: a facade with `backend/facade/contracts.py` (or a `contracts/` package) and a `backend/facade/api.py` that defines a function.
Everything else (loose types, shapes, model crossings, a crossing to a watched file) is a lint matter only and does not count for the level.
No hand-kept list of products: the request path is a watched channel (D103).

How the preset says it:
- `reached-watched` is a `coverage` rule with three edge covers. The first: every product file that a source outside the product (core, a tool or another product) reaches past the export, read at the module the code names (the surface), must be in the contract-check inputs (`crosses-package`).
  A watched file is fine: a change there runs the full suite anyway.
  A product's own code and tests do not count; another product's do (D123): `turbo-discover.js` cascades into a product's dependents only when its contract-check is affected, so a change to an unwatched file retests only the product that changed, and a reader in another product misses it as surely as core does.
  "The product" is the level's group, the `package` field: product code still under `posthog/` (`posthog/temporal/ai_observability/**`) is its product's own, and no product has a sub-package (the warehouse sources vendors are a `source_vendor` label, not a package), so `within_group` has nothing to split here.
  Its rows are a list (advisory, no ledger); `public-surface` holds the crossing as debt.
- A facade re-export does not end the reach (T7, the owner's approval of Nov 16, D120, reading (a) of the facade-origin measurement; D120 took D108's reader scope, D123 widens it to any code outside the product).
  When such a source reads, outside type checking, a name a facade module hands out, and a product file outside the facade defines the name, that file and every module on the name's path to it, from the one the code names, are in the inputs (`: chain`): each of them runs at import, and a change to one changes what the facade hands out.
  The facade module may be the one the code names (`surface.*`) or one the name passes through (`via.*`), as when a test patches a name a product's view took from its facade.
  When the module the code names belongs to the reader (`surface.package == src.package`), that module's own import is the crossing, and the covers judge that import: a product's own test that patches a name at its own module, which took the name from another product's facade, asks for none of its own files (the ws trace, Nov 17).
  A facade that defines the name itself (a `def`, a `class`, an assigned value) is the end: the behavior behind it is the product's own tests' (D101).
  `facade/testing.py` hands names out to tests only, so only a test's read of it counts.
  A registration point (`wiring = "registration"`) that only puts the hand-out into a list, tuple, set or dict literal registers it and does not read it (`edge.listed == '1'`, case 1 of the measurement); any other use counts, and so does a use the facts cannot tell (unknown is never clean).
  A list's own file is read, not the files of what sits in it: entry modules only (Nov 9).
- `presentation-uses-facade` is read through the level key `within_group`: a row whose subjects fall in two groups is not counted, so only a product's own presentation counts.
- `has-facade` is a `layout` rule: `files = ["backend/facade/{contracts.py,contracts}", "backend/facade/api.py: function"]`, a list (advisory, no ledger).
  A `files` entry may hold `{a,b}` alternatives, a file or a directory, and may end in `: function` or `: class`: the file must then also define a symbol with that declaration.
- `wiring-watched` also asks for what the old product lint kept in a narrowing product's inputs (see "What the contract-check inputs watch" below).
- The level requires these four rules and makes all four strict.
  Its `select` reads `path startswith 'products/'`: a product is judged where its facade and its contract-check config live.
- Whether a product skips is still its own config (`package.json` and `turbo.json`); the level says whether that skip would be sound, and a product that skips must hold it (next entry).

Its limits, in plain words:
1. `reached-watched` reads the file the code names and, for a facade hand-out, the files on the name's path to its definition; not what those files import, nor the `__init__.py` files above them (as the request path, D103).
2. An edge whose source tier cannot be told is an incomplete item of `reached-watched` on the source module, so it blocks no product.
3. A name read through a module alias of an export module (`from ...facade import api as x`, then `x.f()`) is read once per scope and attribute chain, at its first use; the use has no statement flags, so one under `if TYPE_CHECKING:` counts as a read.
4. The files of what sits in a handed-out list are not asked for; a registration point that only lists the hand-out asks for nothing. A preset that derives watched sets lifts this with an `: items` cover (below).

**A product that narrows must pass Isolated** (Nov 16).
A product whose own `turbo.json` narrows its contract-check inputs claims the level.
If it fails the level, `check` fails, and no ledger line or exception can accept that: an unsound skip is never debt.
`check` names the level's reasons for the product and the two ways out: fix the reasons, or drop the narrowing.
Dropping the narrowing is a person's decision, because the product is then no longer isolated.
- In core words, a level may name the outside input in which a group claims it: `claimed_by`, a coverage adapter and its options, the table a coverage rule's `inputs` takes.
  The adapter tells, per directory, whether the inputs that decide there are narrowed, and by which file (`narrowed(handle, directory)`).
  A group claims the level when one of its module files sits in such a directory.
- A claiming group that fails the level fails `check` and `compare` (exit 1), and so does a level whose claims cannot be read (no repo root, an input that does not read, a narrowed file whose group cannot be told): unknown is never clean.
  A claiming group that the level's `select` leaves out fails too: it cannot pass the level it claims.
- The turbo adapter: a package narrows a task when its own `turbo.json` gives the task an `inputs` list without `$TURBO_DEFAULT$`.
  Inputs a package inherits from the root config narrow nothing of its own; that is how the monorepo's test selection reads the skip.
- The preset: `[levels.isolated] claimed_by = { adapter = "turbo", task = "backend:contract-check" }`.
- The adapter's files are part of the run's key, as a coverage rule's are, and the stored run summary keeps each level's claims (protocol 5), so a check that a stored summary answers fails the same way.

Why a level key and not a rule that reads a level's result:
- Rules run before levels, and a level reads rules; a rule that reads a level would need a second order between rules, and its findings would sit next to the level they help to judge (selectors that depend on the level they help compute were turned down on Nov 11).
- A rule's finding is a ledger line or an advisory row, so "no ledger accepts it" would need an exemption; a failed claim is no finding at all, like a stale exception.
- The claim lives in the outside input the coverage rules already read, so the level needs no new field, provider or rule.

**Test selection reads the watched files from hoglint** (owner, Nov 17; the design `watched-live-design.md`, the architect's picks).
A repo can drop its hand-kept input lists: `hoglint watched` derives each group's watched files, and the selector reads them.
- `hoglint watched [--level NAME] [--out FILE] [--map FILE]` prints one JSON object, format 1: `format`, `hoglint` (the version), `engine` (the engine id), `policy` (the policy digest), `level`, `complete`, `problems`, and per group of the level `claimed` (the file that claims it, or null), `passes`, `reasons` (cut to a count over 20) and `watched` (path -> the first reason that asks for it).
  Keys are sorted and nothing in it names a time or a machine: one tree, policy and engine give the same bytes.
  It runs every rule as `check` does and judges nothing: exit 0 with an answer, 2 on a config error, 3 when it could not run.
  `--level` defaults to the one level with `claimed_by`.
  `--map` also writes the dependents map of the same graph, the bytes `map` prints, so a CI job that needs both builds the graph once.
- A group's watched files are the union of:
  1. every path a coverage rule the level requires needs;
  2. every tracked file under the level's `watch_zones`, matched against each zone's `paths` inside the group's zone root, non-Python files too;
  3. for a location that one watched file covers (a `: location` cover), its entry: the package's `__init__.py`, else the module files directly inside the directory that the cover keeps, else every module under it;
  4. the `__init__.py` of each package above a watched file, in the same group, which Python runs on import.
  A path belongs to the group of its module, so a group's paths outside its directory appear too.
- **Isolated for selection** is `complete`, a claim and `passes`.
  `complete` is false when an unknown can make a set too small: a claim that cannot be read, an unknown of a required rule with no subject (a failed producer), a file that cannot be read, a watched path in no group.
  A selector treats anything less than a complete answer as no product isolated: more tests, never fewer.
- A coverage rule with no `inputs` records the paths it needs and emits no finding; what it cannot tell stays incomplete.
  The turbo and globs adapters stay for repos that keep input lists.
- The claim adapter `yaml-key` (`claimed_by = { adapter = "yaml-key", file = "product.yaml", key = "isolated" }`): a directory is claimed when the nearest such file at or above it sets the key to true.
  A value that is not a boolean, or a file that is no YAML mapping, is a claim error.
  It declares the files as its inputs, so a claim change reaches the run's key.
  It answers claims only; a coverage rule cannot name it in `inputs`.
  The claim texts follow the adapter: with an input list, a missing file is added to it; with a key there is no list, so the ways are to fix the reasons in the code or, as a person's decision, remove the claim.
- **The item reading** (the architect's pick 1): a registration point that registers a list asks for the module that builds the list and the module that defines each item (`<edge selector>: items`).
  The module that builds the list is where the name core reads is bound, past every re-export (the architect, Nov 18): it decides what core registers, as the items' modules do.
  This lifts limit 4 of the level for derived sets.
  **The rule** (the adversary's second recheck, Nov 18, t30's call): a name a registration point reads is read exactly only when every module-level and class-body binding, change and use of it is a form the reader understands; any other binding, change or use makes it unread, an incomplete item of its module, so its product cannot be isolated.
  A name the link cannot read is never a name with no items.
  The forms the reader understands are written in code, in one place each: what a value holds (`sequence_items`, `json_items`, `call_items` in `src/link/produce.rs`), which rows bind or change a name (`touches`), and which uses leave a name as it is (`understood_use` in `src/walker.rs`); a new form is added there, never as a list here.
  The extractor writes each use it does not understand as an `escapes` row: a name handed to a call that may change it, an attribute store or another method, `setattr`, `globals()` or `sys.modules[__name__]` (every name).
  An escape makes a value that can grow (a list, set or dict, a call's result, another name's) unread; a constant, a tuple and a type alias cannot grow, so names bound to them stay readable however they are bound or used.
  A rebinding that reads the name itself is exact only where an earlier binding, an import before it, or (in a class body) the module's name is what it reads; else it is unread.
  A call item is its callee's and the list module's (class (a)); a library call handed something that can be registered (a class, a function, or a name that holds one) is unread, as the library may hand it on.
  A module a registration point reads other than by `module.NAME` (handed to a call, read through a dunder, by a computed name), or loads from a path string (`import_module("m")`, `sys.modules["m"]`) without reading an attribute right after, is an incomplete item of that module.
  A registered name that resolves to nothing inside a module of the graph (a name only the module's `__getattr__` serves) is that module's incomplete item.
  A registration point that registers a list the cover's selection leaves out (a core module's) carries the cover through to each list it holds, at any depth; an item of such a core list that the link cannot read, whatever its shape, may hide any product's list and no group owns it, so it is a problem of the whole answer: incomplete, every group blocked.
  The table lives on the link next to the hand-outs and outlives the fact rows; it is no edge, so no table moves.
- **The declaration reading** (the architect, Nov 18): what a registry's loader reads is registered like a list core imports by name (`<entity selector>: registered`, for the modules the selection keeps).
  In a registry's file the link reads the attribute the registry names (`[registries.<name>] attribute`) as a declaration: as a list's items, except that a call also holds what its arguments name, so the records a declaration builds hold their items (`TEMPORAL = TemporalRegistration(queues=(QueueBinding(workflows=WORKFLOWS),), schedules=(reconcile,))` registers the items of `WORKFLOWS` and `reconcile`).
  The call's callee counts as an item, as in a list; core's record classes (`TemporalRegistration`, `QueueBinding`) are core's files, so no product watches them.
  A name a declaration names is read as a declaration where its module binds it, in the same file or across an import, under the same rule, and stored under its own key, so an unknown there is its module's.
  It reads the arguments from the record's JSON text, as the item reading does for a call and for the parts of an assigned conditional or comprehension, because the link's value trees keep no call's arguments.
  The attribute bound by an import is unread (its value is built where no loader reads it as a declaration); a file that does not bind it, or binds it by a def or a class, registers no value.
  The preset declares `[registries.temporal]` (`products/{product}/backend/facade/temporal.py`, `TEMPORAL`, read by `posthog.products.load_product_modules` at worker start).
  The derived form selects that registry only (`registry == 'temporal'`): the admin console's `console` attribute is the imported registry object, which this reading would call unread.
- **Hook callback bodies** (the architect's pick 2): a call of a declared hook records the callables it hands on, the `callback` attr of its `installs` edge, as the decorator form always did.
  The Django pack asks the extractor for `calls` rows of the declared hooks' names (`watch_hooks`), also where a file imports a hook under another name, and in those rows a call argument keeps its callee.
  A `[django] hooks` entry is a qualified name, or a table `{ name = "...", callables = ["hook", 1] }` that names the arguments that hand the hook a callable, by keyword and position (the architect, Nov 17).
  Of a hook given by name alone every argument may be a callable; of a table, only the declared ones, and the others are data by declaration.
  A callable argument that names a function or a class is a callable, and of a table's declared argument a module-level value too; a call of a first-party class hands on its instance; a constant or a lambda hands on none; a module, a variable or an enum member in an argument a table does not declare is data.
  A callable argument the facts cannot tell (a parameter, a local variable, a name that does not resolve, a call of a first-party function, any other expression, a `*` argument before a declared position) makes `callback` unknown, and the calling module's group cannot pass.
  The import edges that run when a callable runs get `hook_callback`: `body` for an import inside a callable the file defines, `binds` for the import that binds a callable the file imports.
  An import inside a function that the running `ready()` defines gets `app_setup = "callback"`.
  One hop: what those modules import is not followed.
  A connect helper is no declared hook, so its calls record no callable.
- `compare` lists, as advisory rows, the files a group watches at the base and no longer at the head, both under the head's policy, so a change that narrows a set by moving code shows.

Its limits, in plain words:
1. A list that a function changes after its assignment (`def f(): WORKFLOWS.append(x)`) is read as assigned; one changed at module level is read with the change, and one changed in a module that imports it (`from x import W` then `W.append(B)`) is read as its own module binds it.
2. A callable handed to a hook through a parameter or a local variable is not followed, so it is unknown; a wrapper that forwards its parameter to a hook is followed where its callers call it, when the wrapper is a declared hook too.
3. The modules a registered item's module imports, a callback's own imports past one hop, and the helpers behind a watched file are not watched (the Nov 11 deferral; the owner let them leave selection, Nov 17).
4. Nothing compares a derived set with an independent list: the same run makes the set and the level's verdict.
5. A class attribute the class body does not bind (inherited, from a metaclass, or set in a method) reads as the class, which defines no list.
6. An unknown in a core module that a declaration reaches is that module's, which no group owns; one in a core list a registration point registers is a problem of the answer (above).
8. A registration point that imports a module by a computed name (`import_module(path)` over a list of paths, `import_module(f"...")`, `sys.modules[NAME]`) asks for nothing from that read: the module is unknown, and making it a problem of the answer would stop every answer, since a live registration point (`posthog/temporal/health_checks/registry.py`) does exactly this over a constant list. Open, the owner's call (t30, Nov 18).
7. A worker the preset does not mark as a registration point, and a management command core runs by name (`call_command`), ask for nothing: no cover reads them.

**The linter carries the skip's safety** (owner, Nov 4).
Rules that feed the skip block, and unknowns count against the product.

**Isolated is specified apart from lint acceptance** (owner, Nov 9):
- ledger lines and exceptions never remove impact edges;
- a safety-relevant unknown, or wiring that is not watched, forbids the skip;
- other architectural debt may coexist with Isolated, while impact stays conservatively covered;
- the `requires` list of the Isolated level is published.

**An incomplete result belongs to whoever caused it** (owner, Nov 9):
- a gap a PR introduced belongs to its author and blocks that PR;
- an extractor failure belongs to the tool's owners;
- an existing wiring gap belongs to the product's owners;
- an inherited gap may merge with the full required test suite, and stays visible;
- violations and completeness are reported apart.

**Levels are evaluated from rules, never from a baseline** (architect, Nov 4).
The rung of the time was read from a baseline file, which is circular.
- `level` rules run after the rules they read.
  That is the only ordering between rules.
- The Isolated level must not read the wiring rule except through ledger acceptance.

**Hook callbacks: entry modules only** (owner, Nov 9).
Following their imports would add about 170 watched files.

**New producers that feed the skip** (owner, Nov 9): signal edges, installed callbacks, mock patch targets, dynamic imports.

**The request path is a watched channel** (owner, Nov 12).
A product module that core's request pipeline reaches by imports that run at import time is a watched file, like a receiver or a hook callback: it must be in the product's contract-check inputs.
There is no list and no special level; a product keeps the skip for the code the pipeline does not reach.
- The rule `request-path` is a `reach` with `through_denied`: it starts at the modules the Django settings name, and at the module of the class every API view inherits.
  Out of a settings module it follows every settings entry except those under a short list of keys that are not the per-request pipeline (app registration, task and shell imports, the root URL conf, the application entry points, static files, the API schema, logging, caches, and two login-flow keys that a single URL runs).
  An entry whose setting cannot be named is an incomplete item; a key missing from the list is followed, so a stale list adds rows and never hides one.
  A path string in a settings module is followed whatever function holds it.
  It follows module-level imports through core and on through product code, so every product module on a chain is a row.
- Its rows are a list, not debt: the rule is advisory and keeps no ledger lines.
  `wiring-watched` requires the file of every module on each row's chain.
- What the rule cannot tell, `wiring-watched` cannot tell either: an import on the path that does not resolve, or a settings entry whose setting cannot be named, is an incomplete item of `wiring-watched`.
  An advisory rule's own incomplete items do not reach a check's output, so they must be carried.
- A seed written as a path that matches no file is reported (`config:stale-name`), never dropped.

Its limits, in plain words:
1. An import inside a function is not followed.
   A decision that core reaches only through such an import is not on the path.
2. The `__init__.py` of a package above a reached module is not followed, although Python runs it.
3. A name is followed to the module that defines it, not to the module the code wrote.
   A facade that only re-exports the name is skipped, together with what that facade imports for its own functions.

**What the contract-check inputs watch, at parity with the old product lint** (Nov 16).
Besides the drives, request-path, receiver, hook-callback and patch-target covers, `wiring-watched` asks for:
- the model surface: every module of the `models` and `migrations` zones, since a model is reachable without an import (`apps.get_model` strings, migrations, admin);
- every module a registry loads by name (`registry != none`): routes, scopes, data freshness, webhook consumers, health checks, integration usages and hooks, today's highlights, and the admin console's `console.py` pages;
- every declared permanent interface (`permanent`);
- each wiring location core runs by a name it reads at run time (`runs_by_name`: tasks, temporal, Max tools), as one location that any one watched file in it watches, as the old lint reads it (the owner, Nov 16); query runners stay with `drives`, which reads their channel;
- every product module a settings module names by dotted path, under any key, not only the keys the request path follows;
- the modules of its own product that a product's AppConfig module (`app_config`) imports at setup: at module level, and in the body of the installed AppConfig's `ready()` (the architect, Nov 16, under the owner's parity bar).
  Django calls `ready()` once at setup in every process, and the old lint watches these modules.
  The Django pack marks such an import edge `app_setup = "ready"`; the cover reads it next to the `function_local` context.
- a product's `admin` module that the admin app's autodiscovery imports (`autodiscover_modules("admin")`, with no import that names it; core admin tests run what it registers), a package's `__init__.py`, and the modules of its own product it imports at module level, as for the AppConfig module (the architect, Nov 16).
  The Django pack's `loads` edge (shape `admin`) names the module, for each installed app when the admin app is installed; the pack marks the module-level import edges of that module `autodiscovered = "admin"`.

A coverage rule may select modules by an entity selector: `"<entity selector>: module"` needs the file of each module the selector keeps.
`"<entity selector>: location"` needs one watched file in each location those modules stand in: the outermost package or directory without `__init__.py` that the selector keeps, else the module's own file.
A module it cannot tell it keeps is an incomplete item.

Its limits, in plain words:
1. An import inside a function that `ready()` defines or hands on, or inside any other function of the AppConfig module, is not followed: a callback runs later, if ever, and hook callbacks and receivers are watched at their entry module (Nov 9).
2. A registry the preset does not declare is not known, so a new loader call needs a registry entry.
   The old lint found every `load_product_modules` call by itself.
3. The model surface is the two zones; a model class defined elsewhere in a product is not asked for.
4. A location run by name is watched through any one file in it, so a change to another file in it does not reach the check.
5. What an admin module's own imports import is not asked for: entry modules only, as for the AppConfig module (Nov 9). An admin module's import of another product's module is lint only.

**A wiring location without `__init__.py` is watched through its files** (Nov 12).
Each module file directly inside the directory stands for it, as `__init__.py` stands for a package: `wiring-watched` requires each of them.
Files further down and the tests under it are not asked for.
A directory with no file directly inside falls back to every file under it.
This holds for the locations `drives` names; a location run by name is watched through any one file in it (Nov 16, above).

## Why

- A green lint result is not a permit to skip tests.
  Ledgered debt says "accepted", not "covered" (the second opinion's first ranked change, Nov 9).
- Blocking a PR on a gap it did not cause repeats a known failure (Coverity, Google's static analysis experience).
- Entry modules match how facades and signal receivers are treated.
- D108: a loose type or a shape does not make a skip miss tests, so it is the wrong lever for the level; the safety setting blocks exactly what can, and every product it blocks can pass by watching files.
  Of the settings the owner weighed (no findings at all; the preset as first built; safety plus imports inside functions; a seal on every import past the door; safety), safety keeps the most skips that are sound.

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| Take the rung from a baseline file | Circular | Architect, Nov 4 | `docs/research/data-model-2.md` |
| Never block on any incompleteness | Too broad as a universal rule; gaps a PR introduces still block | Second opinion, Nov 9 | `docs/research/prior-art-second-opinion.md` |
| One runtime boot as proof of completeness | Overclaims coverage | Second opinion, Nov 9 | `docs/research/prior-art-second-opinion.md` |
| Declared contract tests replace the full suite | Overclaims coverage | Second opinion, Nov 9 | `docs/research/prior-art-second-opinion.md` |
| Rules disabled below a level | Products below the level can get worse, and can stay just below forever | Second opinion; owner, Nov 11 | `docs/research/second-opinion-ledger.md` |
| Selectors that depend on the level they help compute | "Fails isolation" turns into "the rules no longer apply" | Second opinion, Nov 11 | `docs/research/second-opinion-ledger.md` |
| Hook callbacks with their import closure | About 170 more watched files | Owner, Nov 9 | not published |
| A level blocked by every unknown type of twelve rules | A loose type does not make a skip miss tests | Owner, Nov 16 (D108) | not published |
| A level blocked by every import past the door, from anywhere | Counts imports between products, which the owner left to lint | Owner, Nov 16 (D108) | not published |
| A hand-kept list of products that may not skip | The request path is a watched channel (D103) | Owner, Nov 12 and Nov 16 | not published |
| Every file of a wiring location run by name | Each file of the location becomes a watched input; the owner takes the old lint's reading, any one watched file in it | Owner, Nov 16 | not published |
| Only the `__init__.py` of a wiring location run by name, as for `drives` | The owner takes the old lint's reading, any one watched file in it | Owner, Nov 16 | not published |
| The non-product reach as one more `covers` entry of `wiring-watched` | It would move that rule's rows and ledger lines and make each such file a lint obligation; a list read by the level moves none | Builder, Nov 16 | not published |
| A facade re-export ends the reach (D108 as first built) | Core can call a function the facade only passes on; a change to the file that defines it skipped the core suite, in the merge queue too | Owner, Nov 16 (D120, T7) | not published |
| Only code that is not a product reads past the export (D108's reader scope) | `turbo-discover.js` retests a product's dependents only when its contract-check is affected, so another product's reader misses a change to an unwatched file (D108's spectrum paper had it "to verify") | Architect, Nov 17 (D123), for the owner's call | not published |
| T7 asks for a hand-out a registration point only lists (reading (b)) | Registration is not a read; engineering_analytics would lose the skip on its worker lists | Owner, Nov 16 (D120) | not published |
| A derived location reads its entry only | A renamed workflow in a product's `temporal/` skips the core test that reads every registered workflow | Architect, Nov 17 (the item reading) | not published |
| A derived location reads every file under it | One product pays most of its skip for files no registration names | Architect, Nov 17 (the item reading) | not published |
| Hook callback recording behind a new `[django]` key | Declaring a hook already says its callables run later; the key would be a second switch for the same fact | Architect, Nov 17 | not published |
| A parameter or a local variable in a hook call is data | It can carry a callable, and unknown is never clean; a hook's declaration says which arguments are callables instead | Architect, Nov 17 | not published |
| Watch every aliased callee (patch, connect) for hook calls | It would add `calls` rows the core reads for paths and could move edges; only declared hook names are watched through an alias | Architect, Nov 17 | not published |
| Every file of an admin package, or what the admin module's imports import | Entry modules only, as for the AppConfig module and hook callbacks | Owner, Nov 9; architect, Nov 16 | not published |

## Measured state

- Nov 9, the first rule engine on the PostHog monorepo: the Isolated level passed for far fewer products than the rung of the time listed.
  This was flagged as parity work, not as a result.
- Nov 11, after the N3b rewrite, on a snapshot copy: the pass count went up and moved by one product between two review rounds.
  The move was a consequence of D63 (fields of handed-out classes count wherever the class is defined).

## What would change the answer

- The second opinion's rule: keep "boundary rules passed" apart from "safe to narrow tests".
  Debt that affects test-selection soundness must stay visible to the selector, even when it does not block a commit.
- A runtime oracle for signals, loader imports, background tasks and hook lists was proposed in the prior-art round.
  It was agreed only as a runtime audit, never as proof.

## Open

- **The published `requires` list.**
  Agreed on Nov 9; the preset holds it since D108 (Nov 16).
- **Selecting a rule by a product's level or claimed maturity** is not built (architect, Nov 11).
- **A level ratchet** (a product that holds a level cannot drop).
  It works through the ledger's level lines and the equality check.
  No decision entry builds anything beyond that.

## Sources

`docs/research/data-model.md` and `data-model-2.md` (sections 7, 13, 18, 20); `docs/research/prior-art.md`; `docs/research/prior-art-second-opinion.md`; `docs/research/second-opinion-ledger.md`; `docs/history/decision-log-3.md`; not published (the plan, the design page, the progress log).
