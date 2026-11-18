# The model repository: cases

`repo/` is a miniature of the PostHog monorepo's layout, checked with the real preset (`presets/posthog.toml`) in the native engine by `test_model_repo.py` and `bin/model-repo`.
Every name and body in it is invented; only the paths and dotted names that the preset spells out are real.
`site/` holds stub sources of `django` and `rest_framework`, read as site-packages.

Each case below is one code shape with the verdicts the rules give it on this preset.
A case that shows a finding has a clean neighbor next to it: code that looks close and must not be a finding.
Every ability of the rule language has a case where its input cannot be told, and the verdict there is never clean (the `UK`, `WK`, `UI`, `CR03` cases and the unknown-input table below).
`expected.toml` holds every finding and incomplete row, each tied to a case id; its `[[wanted]]` entries are verdicts that are decided and not given yet, run as expected failures.

Verdicts: **finding**, **unknown** (a finding that exists because something could not be told), **incomplete**, **clean** (no row).
The last column names the decision behind a verdict, or "open" where none is recorded.

## The products and their roles

| Product | Role |
|---|---|
| `inventory` | The clean product: thin facade, frozen contracts, logic, presentation through its facade, a registry slot, a hook it fills itself. |
| `catalog` | Hands out models, loose types, commands and classes defined elsewhere through its facade; the contract-shape cases. |
| `legacy` | Every kind of debt: views in `backend/api/`, presentation past its facade, receivers, hooks, commands, a test import in production code. |
| `reports` | Depends on `inventory` through its facade and on `catalog` past it; the drives and cycle cases. |
| `gatekeeper` | The product the request pipeline asks for decisions. |
| `access_control`, `notifications` | Foundation products; `access_control` holds the exported view mixins. |
| `batch_exports`, `visual_review` | Paths the preset names: a declared permanent interface, a dev seeder named as test code. |
| `shelf` | The product that passes the Isolated level: a contracts package, an api module with a function, narrowed inputs, and the reaches the level lets pass. |
| `racks` | A facade whose api module only re-exports. |
| `depot` | The watched set's parts (`WA` cases): a registered list, hook callbacks of every form, the zones, the packages above, a claim in `product.yaml`. |

## Cases

| Case | Files | Property | Verdict here | Why, and the decision |
|---|---|---|---|---|
| `PS01` | `posthog/api/widget_report.py` | Core imports a product's logic module and a function from it | public-surface finding (both imports); no-product-lists-in-core clean (no registration point) | Past the facade; the core rule judges registration points only (the architect, Nov 16) |
| `PS02` | `products/legacy/backend/logic/widgets.py` | A later use of an imported model name, three times | public-surface finding on the import only | A use is judged at its import (`uses_at_import`) |
| `PS03` | `posthog/api/widget_report.py` | A use through a module attribute (`module.CONSTANT`) of a module whose import is a finding | public-surface clean for the use | Judged at the module's import, as a use of an imported name is (Nov 16) |
| `PS04` | `posthog/api/widget_report.py` | Later calls of an imported function | clean: a call makes no reference row | |
| `PS05` | `posthog/api/sharing.py`, `ee/hogai/tools.py` | Core imports a product's facade | public-surface and no-product-lists-in-core clean | Core calling a product's facade is allowed (D93); the core rule judges registration points only (the architect, Nov 16) |
| `PS06` | `products/reports/backend/logic/build.py`, `products/legacy/backend/presentation/views.py`, `services/sync/worker.py`, `tools/loose.py` | A product, a service and a tool import a facade | clean | The facade is the public surface |
| `PS07` | `products/reports/backend/logic/build.py` | A product imports another product's logic | public-surface finding | Past the facade |
| `PS08` | `products/catalog/backend/facade/models.py`, `products/reports/backend/logic/build.py` | A model handed out by a facade re-export, then queried | public-surface clean; model-crossings finding (judged at the origin) | The use came through an exported door, so model-crossings judges it (A5, Nov 16) |
| `PS09` | `posthog/test/test_auth.py`, `products/legacy/backend/tests/test_widgets.py` | A test builds another product's model as a fixture | public-surface finding, one row per module and model; model-crossings clean (test) | D93 |
| `PS10` | `posthog/test/test_auth.py`, `products/legacy/backend/tests/test_widgets.py` | A test patches another product's internals by dotted path | public-surface clean; wiring-watched finding for the unwatched target, clean for the watched one | The patch target is wiring-watched's (Nov 12) |
| `PS11` | `posthog/settings/web.py` | Settings name product apps, a middleware and a logger key | public-surface and no-product-lists-in-core clean | Settings may name product paths (Nov 12); the core rule takes the same allowance (Nov 16) |
| `PS12` | `posthog/migrations/0002_entry_table.py`, `posthog/clickhouse/migrations/0001_entry_table.py` | A core Django migration and a ClickHouse migration import product code | public-surface and no-product-lists-in-core clean | Migrations are frozen history in both rules (Nov 12, Nov 16) |
| `PS13` | `posthog/management/commands/start_temporal_worker.py`, `posthog/hogql_queries/query_runner.py` | A registration point names a product's wiring location | public-surface clean; no-product-lists-in-core finding | The wiring channel (Nov 12) |
| `PS14` | `products/batch_exports/backend/sql.py`, `posthog/clickhouse/schema.py`, `posthog/clickhouse/test/test_schema.py`, `products/reports/backend/logic/build.py`, `products/catalog/backend/sql.py` | A declared permanent interface used by core production code, by a core test and by another product; next to it an undeclared one | public-surface clean for core production code; public-surface finding for the test and the product; the undeclared one a public-surface finding everywhere; no-product-lists-in-core clean (`schema.py` is no registration point) | Permanent interfaces for core production code only (D99 note; the core rule takes the same allowance, Nov 16) |
| `PS15` | `posthog/api/__init__.py`, `posthog/urls.py` | Composition names a viewset outside `routes.py`; next to it the registration export and an `include` of it | public-surface finding for the viewset; clean for `routes.py`; no-product-lists-in-core clean (no registration point) | |
| `PS17` | `products/reports/backend/presentation/views.py`, `products/legacy/backend/presentation/views.py`, `products/catalog/backend/presentation/views.py` | Presentation imports another product's viewset that subclasses the mixin; next to it one that does not | clean for the subclass; public-surface finding for the other | The adapter export grants by base class (Nov 9); known gap |
| `PS18` | `products/legacy/backend/presentation/views.py` | Presentation imports another product's serializer | public-surface finding (see `PF05`) | |
| `PS19` | `products/legacy/backend/facade/contracts.py` | A contract imports another product's internal module | public-surface finding (see `CN01`) | |
| `PS20` | `products/catalog/backend/facade/shapes.py` | A facade module imports another product's model for its types | public-surface finding | |
| `PS21` | `tools/codegen/generate.py`, `bin/build_types.py`, `livestream/bot.py`, `manage.py` | Code in tool packages (a `tools/` folder, `bin`, `livestream`, the root) names product internals | public-surface finding | Every first-party file has a package; tier `tool` (D105) |
| `PS23` | `tools/codegen/emit.py`, `livestream/bot.py`, `posthog/schema.py` | Two files of one tool package; a third-party import | clean | |
| `PS24` | `posthog/tasks/scheduled.py` | A core registration point names several products' tasks | public-surface clean; no-product-lists-in-core finding per product | D104: the hub rule stays |
| `PS25` | `ee/api/widgets.py` | `ee` queries a product's model | public-surface finding; model-crossings clean; no-product-lists-in-core clean (no registration point) | The model came in past the export: the coupling is public-surface's (A5, Nov 16) |
| `PS26` | `posthog/models/resource_transfer/visitors.py` | A core file that names three products' models in a table | public-surface findings, one per product; model-crossings clean; no-product-lists-in-core clean: the file is no registration point | The written limit of the hub rule: a hard-coded product list outside a marked registration point is not caught (the architect, Nov 16); the uses are public-surface's (A5) |
| `PS27` | `posthog/test/test_auth.py`, `products/legacy/backend/logic/widgets.py` | A core test patches a library name through a product module (`...widgets.importlib.import_module`) | wiring-watched finding for the product module; public-surface clean | The test depends on the module importing the name: the product module is the watched file (Nov 16) |
| `DP01` | `tools/lint/isolation.py` | A tool module holds product package names as data (a `frozenset`, a comparison) | clean | A string is a path only where the code holds one (Nov 16, first-ledger triage) |
| `DP02` | `tools/lint/tests/test_isolation.py` | A test parametrizes product package names as data | clean; `shelf` stays Isolated | The strings that made two real products fail their level (Nov 16) |
| `DP03` | `posthog/models/team/team.py`, `posthog/models/team/extensions.py` | A team extension names a product model by its dotted path | public-surface finding; no-product-lists-in-core clean (no registration point) | `TeamExtensionDescriptor` takes a path (`path_callees`) |
| `DP04` | `products/reports/backend/stock_names.py` | A lazy map names another product's module; next to it a dict of plain text in the same module | public-surface findings (the lazy import and the map's value); clean for the text | A lazy map's value is a path place |
| `DP05` | `products/reports/backend/skill_build.py` | `subprocess.Popen([sys.executable, "-m", <module>])` | public-surface finding | An element of a path callee's argument vector is a path |
| `DP06` | `products/reports/backend/restock_dispatch.py` | A Celery task sent by a name held in a module constant | public-surface finding | A file that hands a computed value to a path callee keeps its strings that name a module (the owner, Nov 16: dispatch by name is real) |
| `DP07` | `posthog/temporal/health_checks/registry.py` | A registration point lists product modules and imports each through a loop over the list | no-product-lists-in-core finding; public-surface clean (a registration point names a wiring location); dynamic-imports clean: the loop gives the loader each value | The same, for a loader; a string a loop hands to a loader is a path place (Nov 16) |
| `NS01` | `ee/hogai/tool_registry.py`, `products/__init__.py` | Core imports the namespace package all products sit in, to walk it | clean | `products/__init__.py` is tier `core`, with no package: it names no product (Nov 16) |
| `ST01` | `products/reports/backend/logic/stock_states.py`, `products/inventory/backend/facade/contracts.py`, `products/inventory/backend/facade/enums.py`, `products/inventory/backend/logic/notes.py` | A product imports through a facade module two names it keeps to itself: one the facade's enums module hands out, one from the product's logic | public-surface clean for the enum; finding for the logic class | A kept name is exported when the same export hands out its origin (Nov 16) |
| `SY01` | `products/legacy/backend/api/widgets.py` | A viewset under `backend/api/` uses an access-control mixin | clean | The mixins are exported by name to zone `api` (exports `symbols`, Nov 12) |
| `SY02` | `products/legacy/backend/api/widgets.py` | The same module imports another product's viewset that inherits the mixin | public-surface finding | By name: a subclass gets no door |
| `ES01` | `products/catalog/backend/facade/models.py` | A facade module that only re-exports a model | export-shape finding | |
| `ES02` | `products/catalog/backend/facade/api.py` | A model, queryset and manager in facade signatures | clean for export-shape (see `FS01`-`FS03`) | One rule per slot (`slots_reported_by`) |
| `ES03` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py` | The five core models in signatures and fields | clean | `may_cross` (D98) |
| `ES06` | `products/inventory/backend/facade/contracts.py`, `products/catalog/backend/facade/shapes.py` | An exception handed out; one with a class attribute typed as another product's model; a Protocol with such an attribute | clean for the first; facade-signature finding for each attribute | Exceptions and Protocols are allowed (Nov 12); an annotated attribute of a class that is no dataclass is a slot `attr:<name>` of the class (Nov 13) |
| `ES07` | `products/catalog/backend/facade/shapes.py` | A pydantic model; one with a field typed as another product's model | clean; facade-signature finding for the field | pydantic fields are slots (`dataclass_bases`); allowed shapes are walked (D63) |
| `ES08` | `products/catalog/backend/facade/shapes.py` | A `Protocol`; one whose call takes another product's model | clean; facade-signature finding for the call (`FS08`) | D93 |
| `ES09` | `products/catalog/backend/facade/shapes.py` | A mutable dataclass | export-shape finding | |
| `ES10` | `products/inventory/backend/facade/contracts.py`, `products/inventory/backend/facade/enums.py`, `products/catalog/backend/facade/contracts.py`, `products/catalog/backend/facade/shapes.py` | Frozen dataclasses (house decorator and stdlib), a str enum, a frozen class with a list field | clean | |
| `ES11` | `products/legacy/backend/facade/backfill_command.py` | A management command class defined in a facade | export-shape finding | |
| `ES12` | `products/catalog/backend/facade/testing.py` | `facade/testing.py` returns a model | clean | The fixture surface (D93) |
| `ES20` | `products/catalog/backend/facade/bases.py` | An exception whose base is a call; one whose base is a module-level name bound by a call; one whose base is a name bound to a class in each branch of `if TYPE_CHECKING`; one with a generic base (`Generic[T]`) | export-shape unknown for the first two; clean for the branch-bound and the generic one | A base that is no class makes the lineage unknown; a name bound once per branch stands for every class it is bound to; a subscript is read through (T14, Nov 13) |
| `ES21` | `products/catalog/backend/facade/bases.py` | An exception whose metaclass is given by a call | clean; wanted: export-shape unknown | Written limit: a metaclass given by a call is not read (T14, Nov 13) |
| `EX01` | `products/catalog/backend/facade/refusals.py`, `products/catalog/backend/logic/refusals.py` | A facade hands out an exception whose base is a DRF exception; the run reads DRF's sources (the stub `APIException` in `site/`) | clean | A third-party class's bases are read from its sources where the run reads them (Nov 16) |
| `CM01` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py`, `products/catalog/backend/management/commands/import_entries.py` | A command class as a parameter and a return of a facade function, and as a field of a handed-out dataclass | facade-signature finding, one each (three) | `facade-signature` denies `command` (507d1b6); the field is that rule's slot too |
| `FS01` | `products/catalog/backend/facade/api.py` | A facade function returns a model | facade-signature finding | |
| `FS02` | `products/catalog/backend/facade/api.py` | A facade function returns a queryset | facade-signature finding (queryset and model) | |
| `FS03` | `products/catalog/backend/facade/api.py` | A facade function returns a manager | facade-signature finding | |
| `FS04` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py`, `posthog/models/team/team.py` | Each of the five core models in a parameter, a return and a field | clean | D98 |
| `FS05` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py` | `Integration` in a parameter, a return and a field | facade-signature finding (three) | D98, D104 |
| `FS06` | `products/catalog/backend/facade/api.py` | `object`, `Any`, bare `dict` as parameters; `dict[str, Any]` as a return | facade-signature unknown (four: the return reports its `Any`) | D93 |
| `FS13` | `products/catalog/backend/facade/api.py` | `dict[str, object]` as a parameter and as a return | clean for the parameter; facade-signature unknown for the return | `object` as a mapping's value is an opaque payload, as `Any` is (Nov 16) |
| `FS07` | `products/catalog/backend/facade/api.py` | A parameter with no annotation | facade-signature unknown | |
| `FS08` | `products/catalog/backend/facade/shapes.py` | A `Protocol` call that takes another product's model | facade-signature finding | |
| `FS10` | `products/legacy/backend/facade/backfill_command.py` | A method of a class in a facade with unannotated parameters | facade-signature unknown (two) | |
| `FS12` | `products/inventory/backend/facade/api.py`, `products/gatekeeper/backend/facade/api.py` | Facade functions typed with scalars and contracts | clean | |
| `TA01` | `products/catalog/backend/facade/generics.py` | `TypeVar`s in facade signatures: one bound to a model, one bound to it by a string, one with two scalar constraints, one with neither | facade-signature finding for each bound's model (two slots each); clean for the constraints; facade-signature unknown for the one with neither, as before | A `TypeVar` is judged by its bound or constraints (Nov 16) |
| `TA02` | `products/catalog/backend/facade/generics.py` | A functional `TypedDict` and a class-form one with the same fields (a model and a `str`), each in a facade function's parameter | the same rows for both: export-shape finding for the class, facade-signature finding for its model field | A functional `TypedDict` declares a class as the class form does (the architect, Nov 16) |
| `TA03` | `products/catalog/backend/facade/generics.py` | PEP 695 type parameters: a function's bound to a model, a function's with no bound, a `Protocol`'s with two constraints | facade-signature finding for the bound's model; unknown for the one with no bound, as before; clean for the constraints | A PEP 695 parameter is judged as a `TypeVar` (Nov 16) |
| `TA04` | `products/catalog/backend/facade/lazy_types.py`, `products/catalog/backend/logic/refs.py` | A lazy map hands out two type aliases: a union with a model, a union of scalars | facade-signature finding for the model (slot `alias`); clean for the other | A lazy name is read as the alias it maps to (Nov 16) |
| `AC01` | `products/catalog/backend/facade/limits.py`, `products/catalog/backend/logic/limits.py` | Handed-out constants whose values a call makes, annotated `int`, `Final[int]` and a mapping to a model class; one the facade assigns itself (`int`); next to them an unannotated one and a `TypeAlias` | facade-signature finding for the model class (slot `constant`); clean for the `int`s; export-shape unknown for the unannotated one, as before; facade-signature finding for the `TypeAlias`'s model, as before | A constant with an annotation is judged by its annotation (Nov 16) |
| `TM01` | `products/catalog/backend/facade/api.py` | Typed mappings: `dict[str, str]`, `dict[str, Any]`, `Mapping[str, Any]`, `dict[str, Team]` parameters; `dict[str, Any] \| list[Any]` and a `list[Any]` return | clean for the mappings; facade-signature unknown for the `Any` of the list (two) | `argument_of`, typed mappings (D93; Nov 12) |
| `TM02` | `products/catalog/backend/facade/api.py` | A bare `list` parameter | clean | Open since Nov 12; wanted as unknown |
| `CR01` | `products/catalog/backend/facade/shapes.py` | A core model on the list in a field (`EntryOwners`) | clean | `may_cross` on a symbol assignment |
| `CR02` | `products/catalog/backend/facade/shapes.py` | Nested fields: `list[Team]` and `list[Integration]` | clean for `Team`, facade-signature finding for `Integration` | `may_cross` read at any depth (D98) |
| `CR03` | `products/catalog/backend/facade/api.py` | A parameter typed with a name that does not resolve (`Ghost`) | facade-signature unknown; the unresolved import is also an unknown row in facade-no-drf and prod-imports-test, and an incomplete item of the cycle rule | `may_cross` cannot be told: never clean |
| `FL01` | `products/inventory/backend/facade/api.py`, `products/access_control/backend/facade/api.py`, `products/notifications/backend/facade/api.py`, `products/reports/backend/facade/api.py`, `products/legacy/backend/facade/api.py` | Thin facade functions and a mapper in `api.py` | clean | D94; only capability submodules (Nov 12) |
| `FL02` | `products/catalog/backend/facade/api.py` | `api.py` holds a function that queries the ORM and a large one without a query | clean | The query is `FQ01`; the large function has no rule (Nov 12) |
| `FL03` | `products/legacy/backend/facade/queries.py` | A capability submodule (by name) with a body | facade-logic finding, named per definition | D94 |
| `FL04` | `products/inventory/backend/facade/tasks.py`, `products/catalog/backend/facade/models.py` | A capability submodule that only re-exports | clean | |
| `FL05` | `products/inventory/backend/facade/hooks.py`, `products/catalog/backend/facade/testing.py`, `products/legacy/backend/facade/backfill_command.py` | Bodies in other facade modules (a hook registration, a fixture, a class) | clean | |
| `HZ15` | `products/reports/backend/facade/run_status.py`, `products/reports/backend/models.py` | A facade module with a body that rebinds a model's nested class (`RunStatus = ReportRun.Status`) | facade-logic clean; export-shape finding for the nested class | A rebind of a class's member hands out the member, not the model (Nov 16) |
| `HZ16` | `products/reports/backend/facade/run_handle.py` | The same with a rebind of the model itself (`Run = ReportRun`) | facade-logic finding; export-shape finding | Neighbor of `HZ15`: a module that hands out a model is a capability |
| `HZ01` | `products/legacy/backend/facade/workers.py`, `products/legacy/backend/temporal/jobs.py` | A facade module that hands out a name from a wiring location and has a body | facade-logic finding | A capability by what it hands out (`handout_zones`) |
| `HZ02` | `products/legacy/backend/facade/labels.py` | A facade module that hands out a name from logic and has a body | clean | |
| `HZ03` | `products/legacy/backend/facade/nsworkers.py` | A facade module that hands out a namespace directory under a wiring location and has a body | facade-logic finding | The directory's zone is read (Nov 12 handout_zones fix) |
| `HZ04` | `products/legacy/backend/facade/lazyworkers.py` | A facade module with a body that hands out wiring lazily (`_LAZY`, `__getattr__`) | facade-logic finding | A lazy name has an import edge that does not run on import (`function_local`, attr `lazy`) |
| `HZ05` | `products/legacy/backend/facade/aliasworkers.py` | A facade module with a body that hands out `import a.b as name` from wiring, listed in `__all__` | facade-logic finding | `import a.b as name` hands out the module when `__all__` lists the name |
| `HZ06` | `products/legacy/backend/facade/aboveworkers.py` | A facade module with a body and a relative import above the top package in `__all__` | facade-logic incomplete; export-shape unknown; unknown rows in public-surface, facade-no-drf and prod-imports-test; a cycle incomplete item | The import keeps the name it binds, so the hand-out is unresolved and `handout_zones` unknown |
| `HZ07` | `products/legacy/backend/facade/lazylabels.py` | A facade module with a body that hands out a name from its product's logic lazily | clean | Neighbor of `HZ04` |
| `HZ08` | `products/legacy/backend/facade/aliaslabels.py` | A facade module with a body that imports wiring as `import a.b as name` and does not list the name in `__all__` | clean | Neighbor of `HZ05` |
| `HZ09` | `products/legacy/backend/facade/relativelabels.py` | A facade module with a body that hands out a name from logic by a relative import that resolves | clean | Neighbor of `HZ06` |
| `HZ10` | `products/legacy/backend/facade/lazygoneworkers.py` | A facade module with a body whose lazy map names a module that does not exist | facade-logic incomplete; export-shape, facade-signature, facade-no-drf and prod-imports-test unknown; one more cycle incomplete item (`CY03`) | The entry is an import edge that does not resolve and keeps its name: unknown is never clean (Nov 12) |
| `HZ11` | `products/legacy/backend/facade/lazytext.py` | A facade module with `__getattr__` and a module-level dict of text | clean | Neighbor of `HZ10`: a value that is no dotted path is no lazy entry |
| `HZ12` | `products/legacy/backend/facade/relativeworkers.py` | A facade module with a body whose lazy map names wiring by a relative value (`'..temporal.jobs'`) | facade-logic finding | A relative value is anchored at the module's package, as a relative import is (Nov 12) |
| `HZ13` | `products/legacy/backend/facade/barelazy.py` | A lazy map of one bare name where no entry resolves | export-shape unknown for the name `__all__` lists; clean for `handout_zones` | A written limit: a dict of plain words looks the same for `handout_zones`; a listed name nothing binds readably is unknown (T14, Nov 13) |
| `HZ14` | `products/legacy/backend/facade/colonlazy.py` | A lazy value that names the attribute (`'pkg.mod:Name'`) | export-shape unknown for the name `__all__` lists; clean for `handout_zones` | A written limit for `handout_zones`; a listed name nothing binds readably is unknown (T14, Nov 13) |
| `FQ01` | `products/catalog/backend/facade/api.py` | A facade function runs an ORM query itself (`count_entries`), a read by team | clean | A read by id, by team or by the caller's filters is allowed (D115, which replaces D95's wider rule) |
| `FQ02` | `products/inventory/backend/facade/api.py` | A facade function maps a list that its logic returned | clean | D96 |
| `NS01` | `products/legacy/backend/facade/api_state.py` | A facade function creates a row (`objects.create`) | facade-no-state finding (`write(create)`) | D115: a facade changes no state |
| `NS02` | `products/legacy/backend/facade/api_state.py` | A facade function holds a queryset in a local and updates it | facade-no-state finding, labelled by the source use (`instance-many(filter)`) | The write is read through the local (`flow_shape`) |
| `NS03` | `products/legacy/backend/facade/api_state.py` | A facade function loads a row by id into a local, sets a field and saves it | facade-no-state finding (`instance-single`) | `save` writes one instance (`instance_write`); the flow shape, which model-crossings reads, does not change |
| `NS04` | `products/legacy/backend/facade/api_state.py` | A facade function builds an instance and saves it | facade-no-state finding (`construct`) | A built instance that is saved is a write |
| `NS05` | `products/legacy/backend/facade/api_state.py` | A facade function reads a row with `select_for_update()` | facade-no-state finding (`lock`) | D115: a row lock |
| `NS06` | `products/legacy/backend/facade/api_state.py` | A facade function opens `transaction.atomic()` around a call of its logic; another is decorated with it | facade-no-state finding, one row with count 2 | D115: a transaction; a use of `transaction.atomic` is a reference edge by `[discovery] reference_names` |
| `NS07` | `products/legacy/backend/facade/api_state.py` | A facade function reads through `objects.unscoped()` | facade-no-state finding (`scalar-chain(values_list)`) | D115: a read that leaves the team scope |
| `NS08` | `products/legacy/backend/facade/api_state.py` | A facade function reads through the `all_teams` manager | facade-no-state finding (`scalar-chain(count)`) | D115 |
| `NS09` | `products/legacy/backend/facade/api_state.py`, `posthog/models/scoping.py` | A facade function runs a call inside the `unscoped()` context | facade-no-state finding | D115; a reference by `[discovery] reference_names` |
| `NS10` | `products/legacy/backend/facade/api_state.py` | Facade functions read by id, by team and by the caller's filters, with the same labels as `NS03`, `NS07` and `NS08`; one maps a queryset it holds in a local | clean | D115: reads mapped to contract types stay allowed |
| `NS11` | `products/inventory/backend/facade/api.py`, `products/inventory/backend/logic/stock.py` | The facade calls the operation its logic holds (a transaction, a read, a save) | clean | The shape the rule's message asks for |
| `NS12` | `products/legacy/backend/facade/api_state.py` | A facade function raises on what a read returned and writes nothing | clean | A written limit (D115): a decision on what the database returned that leads to no write |
| `NS13` | `products/legacy/backend/facade/api_state.py` | A facade function carries a definition in a read's filter (`name__startswith="live-"`) | clean | A written limit (D115) |
| `NS14` | `products/legacy/backend/facade/api_state.py`, `products/legacy/backend/logic/loading.py` | A facade function saves an instance a logic function returned; another saves each row of a loop over a queryset | clean | A written limit: a write on a value that came from a parameter, a helper, a loop or a related manager is not seen |
| `NS15` | `products/legacy/backend/facade/api_state.py` | A facade function calls `unscoped()` later in a chain (`objects.filter(...).unscoped()`) | clean | A written limit: the chain is read from its start |
| `NS17` | `hoglint.toml` (planted line), `posthog/models/scoping.py` | A `reference_names` entry that resolves and that nothing uses (`team_scope`) | clean | A listed name with no use is fine |
| `NS18` | `hoglint.toml` (planted line) | A `reference_names` entry that names nothing (`django.db.transaction.atomik`) | config:stale-name incomplete | A misspelled name would make its clause report nothing: incomplete, never silent |
| `NS19` | `hoglint.toml` (planted line), `posthog/models/scoping_compat.py` | A `reference_names` entry that names a re-export of `unscoped` | config:stale-name incomplete | Uses resolve to the definition, so a re-export's name would match no use |
| `NS16` | `products/legacy/backend/facade/api_scope_gone.py` | A facade function runs inside a scope context whose import does not resolve | facade-no-state clean (a use that resolves to no listed name makes no edge); facade-no-drf and prod-imports-test unknown; a no-new-package-cycles incomplete item | The import's own unknown rows are what the run shows |
| `FD01` | `products/legacy/backend/facade/api.py` | A facade imports `django.http` | facade-no-drf finding | |
| `FD02` | `products/legacy/backend/facade/api.py` | A facade imports a DRF exception | facade-no-drf finding | |
| `FD03` | `products/legacy/backend/facade/api.py` | A facade imports its own presentation | facade-no-drf finding | |
| `FD04` | `products/catalog/backend/facade/api.py` | A facade lazily re-exports a DRF serializer from `backend/api/` | facade-no-drf finding; export-shape finding | The lazy edge is judged like any import; `backend/api/` counts as presentation for this rule (Nov 12) |
| `FD06` | `products/catalog/backend/facade/serializers.py` | A facade eagerly re-exports the same serializer | facade-no-drf finding; export-shape finding | The eager neighbor of `FD04`: the same verdict |
| `FD05` | `products/inventory/backend/facade/api.py`, `products/catalog/backend/facade/testing.py` | A facade that raises its own errors; `facade/testing.py` that imports DRF | clean | `facade-code` leaves fixtures out |
| `ME01` | `products/catalog/backend/logic/errors.py`, `products/catalog/backend/facade/errors.py`, `products/catalog/backend/facade/rows.py`, `products/catalog/backend/facade/sinks.py` | Methods that return or take another product's model on a handed-out exception, pydantic model and Protocol defined outside the facade, each handed out by its own facade module; next to them a method that returns a contract type and an attribute typed as one | public-surface finding for the model import; export-shape finding at each of the three facade modules (one row per type and module); the contract-type method and attribute clean | The give-away follows the row: no signature rule reads a member row outside the facade, so the export rule judges it (Nov 13) |
| `ME02` | `posthog/errors.py`, `products/catalog/backend/logic/errors.py`, `products/catalog/backend/facade/errors.py` | Handed-out exceptions that inherit a method naming a model: from a class core defines, from a base in the same product, and one whose base does not resolve | clean for the core base; export-shape finding for the product base; export-shape unknown for the base that does not resolve, with a prod-imports-test unknown and two no-new-package-cycles incomplete items for its import (counted in the `CR03` row) | `owned_elsewhere` is tested per ancestor: what a product class inherits from core is core's contract (Nov 13) |
| `ME03` | `products/catalog/backend/logic/errors.py`, `products/catalog/backend/facade/errors.py`, `products/catalog/backend/facade/rows.py` | An opaque payload (`dict[str, Any]`) in an `__init__` parameter of a handed-out exception defined outside the facade; next to it the same payload as a method's return value, handed out by another facade module | clean for the parameter; export-shape unknown for `typing.Any` in the return value | A slot has one policy, the named rule's: facade-signature's selection leaves an opaque payload out of a parameter or a field, never out of a return value (Nov 13) |
| `ME04` | `products/catalog/backend/logic/views_out.py`, `products/catalog/backend/facade/records.py` | A contract dataclass the facade defines inherits a field typed as another product's model from a dataclass in the product's logic | facade-signature finding for the inherited field at the facade; export-shape clean | The copy of an inherited field is found where the class is defined (fix round, Nov 13) |
| `ME05` | `products/catalog/backend/logic/views_out.py`, `products/catalog/backend/facade/private_names.py` | A pydantic model handed out under a private name; a private function handed out under a public name that takes the product's own model | export-shape finding for the model behind the private name (no rule reads its copy); facade-signature finding for the public name's copy, export-shape clean | "Public" is asked of the callable the signature rule reads (fix round, Nov 13) |
| `ME06` | `products/inventory/backend/facade/owners.py`, `products/inventory/backend/routes.py` | A registration function returns a contract class whose field is a core model on the `may_cross` list | export-shape finding at `routes` (the registration policy denies every model); clean in the facade | A slot is left only to a rule that judges it under the walk's shape policy (fix round, Nov 13) |
| `ME07` | `products/catalog/backend/logic/views_out.py`, `products/catalog/backend/facade/named.py` | A facade module hands out a pydantic model and a function that returns it | facade-signature finding for the copy of the field; export-shape clean, whatever the order of the module's definitions and hand-outs | The hand-out name is known at every depth (fix round, Nov 13) |
| `ME08` | `products/catalog/backend/logic/errors.py`, `products/catalog/backend/facade/batches.py` | A method of a handed-out exception returns a generic alias (`StockItems = list[StockItem]`) defined in the product's logic; next to it a method that returns a union-of-literals alias | export-shape finding for the model the alias stands for; the literals alias clean; model-crossings clean for the alias's own line (its import is public-surface's, A5) | What an alias stands for is a fact in every file: the producer expands it outside a surface file too (Nov 13) |
| `ME09` | `products/catalog/backend/logic/errors.py`, `products/catalog/backend/facade/notes.py` | A method of a handed-out exception defined outside the facade takes a bare `dict`, a bare `object` and a parameter with no annotation | clean | A written limit: outside a surface file these have no row, so no rule judges them (wanted, not clean) |
| `ME10` | `products/catalog/backend/facade/outlines.py` | A class nested in a contract dataclass the facade defines has a method that takes the same three | clean | A written limit: a nested class is read in plain mode, also in a surface file (wanted, not clean) |
| `FC01` | `products/catalog/backend/logic/views_out.py`, `products/catalog/backend/facade/shipments.py`, `products/catalog/backend/facade/tracking.py` | One facade module hands out a pydantic model defined in the product's logic; another facade module's function returns it | facade-signature finding for the copy of the field at the module that hands it out; export-shape clean at the module that returns it | The copy is looked for in every file of the same export and root (T14, Nov 13) |
| `FC02` | `products/catalog/backend/logic/views_out.py`, `products/catalog/backend/facade/private_names.py`, `products/catalog/backend/facade/private_tracking.py` | The neighbor: the model is handed out only under a private name (`ME05`), and another facade module's function returns it | export-shape finding at the module that returns it, and at `private_names` (counted in the `ME05` row) | A private name holds no copy the signature rule reads (T14, Nov 13) |
| `HO01` | `products/catalog/backend/logic/handouts.py`, `products/catalog/backend/facade/starred.py` | A facade module star-imports a logic module whose `__all__` lists an exception with a method that returns a model, a plain class, a class with a nested class, a function that takes a model and a factory | export-shape finding for the model and for each plain class; facade-signature finding for the copy of the function | Each name a star binds in an export module is an import edge of its own (T14, Nov 13) |
| `HO02` | `products/catalog/backend/facade/aliases.py` | A public name bound to a model by assignment, not listed in `__all__`, in a module with a function | export-shape finding | A public assigned name is handed out by the clause of a definition (T14, Nov 13) |
| `HO03` | `products/catalog/backend/facade/alias_listed.py` | Names `__all__` lists, bound by assignment: a plain class under a private name, a class nested in a class, a function that takes a model, a factory's result, a constant expression, a union type alias with a model | export-shape finding for each class; facade-signature finding for the function's copy and for the alias's expansion (slot `alias`); export-shape unknown for the factory's result; the constant clean | A listed name is a def, an edge, a constant expression, a type alias judged by its expansion, or unknown (T14, Nov 13) |
| `HO04` | `products/catalog/backend/facade/aliases.py` | A public name bound by a call, not listed in `__all__` | clean | A written limit: a call is no hand-out, and only a listed name is accounted for (wanted, not clean) |
| `WK01` | `products/catalog/backend/logic/views_out.py`, `products/catalog/backend/facade/handout.py` | A pydantic class defined in the product's logic, handed out, with a field typed as another product's model | facade-signature finding at the facade | Fields of a handed-out class count where it is defined (D63) |
| `WK02` | `tools/viewkit/shapes.py`, `products/catalog/backend/facade/handout.py` | The same class defined in a tool package | facade-signature finding | `owned_elsewhere` false for tier `tool`: walked |
| `WK03` | `sandbox/shapes.py`, `products/catalog/backend/facade/handout.py` | The same class in a first-party top folder no assignment claims (tier unknown) | facade-signature finding (walked); incomplete rows for every edge of the folder | `owned_elsewhere` unknown walks (Nov 12 review) |
| `WK04` | `scripts/shapes.py`, `products/catalog/backend/facade/handout.py` | The same class in a top folder outside `[discovery] first_party` | incomplete rows for the folder's edges and the facade's import; the field is not walked | Defect: read as third-party with no facts (wanted) |
| `BH01` | `posthog/exporters.py`, `products/catalog/backend/logic/exporters.py`, `products/catalog/backend/facade/exporters.py` | Handed-out classes that implement an abstract base: one core defines, one the product defines | clean for the core base; export-shape finding (`export-shape(class)`) for the product base | `behavior`: a class whose abstract base core owns is an allowed behavior class, and its own members are judged (Nov 16) |
| `CN01` | `products/legacy/backend/facade/contracts.py`, `products/catalog/backend/events.py`, `posthog/kafka_client/client.py` | A contract reaches Django through a chain of module-level imports | contracts-no-django finding | |
| `CN02` | `products/catalog/backend/facade/enums.py` | An enum module imports Django inside a function | clean | Only what runs on import (Nov 12) |
| `CN03` | `products/inventory/backend/facade/contracts.py`, `posthog/__init__.py`, `posthog/celery.py` | A contract imports a core module whose package `__init__` loads Django | clean | Known gap (wanted) |
| `CN04` | `products/catalog/backend/facade/contracts.py` | A contract with stdlib imports only | clean | |
| `PF01` | `products/inventory/backend/presentation/views.py` | Presentation imports its own facade and presentation | clean | |
| `PF02` | `products/legacy/backend/presentation/views.py` | Presentation imports its own models | presentation-uses-facade finding | |
| `PF03` | `products/legacy/backend/routes.py` | `routes.py` imports a viewset from `backend/api/` | presentation-uses-facade finding | |
| `PF04` | `products/legacy/backend/presentation/views.py`, `products/legacy/backend/helpers.py` | Presentation imports its own internal module, which re-exports a facade name | presentation-uses-facade finding | Judged where the import lands (Nov 12) |
| `PF05` | `products/legacy/backend/presentation/views.py`, `products/reports/backend/presentation/views.py` | Presentation imports another product's presentation (a serializer; a module with an adapter and a plain view) | presentation-uses-facade finding per module; clean for `reports`, which imports only the adapter | D93; Nov 12 |
| `PF06` | `products/legacy/backend/presentation/views.py` | Presentation imports another product's facade | clean | |
| `PF07` | `products/legacy/backend/presentation/review_views.py`, `products/legacy/backend/reviews/tests/fixtures.py` | Presentation imports a directory without `__init__.py` (`reviews/tests`) whose files are all in zone `tests` | presentation-uses-facade clean; prod-imports-test finding | The surface endpoint reads the directory's labels, not the graph module's above it (Nov 12) |
| `PF08` | `products/legacy/backend/presentation/review_views.py`, `products/legacy/backend/drafts/build.py` | The same into a directory whose files are in zone `backend` | presentation-uses-facade finding | Neighbor of `PF07` |
| `RG01` | `products/legacy/backend/routes.py`, `products/legacy/backend/route_helpers.py` | `routes.py`, which defines a function and has no `__all__`, imports a function whose parameter is a model | presentation-uses-facade finding; export-shape clean | F1 of proof round 2: an import of a module with a function is handed out only by `__all__` (D64), so the slot is judged by no rule (wanted, open) |
| `RG02` | `products/inventory/backend/routes.py`, `products/inventory/backend/route_helpers.py` | The same function listed in `__all__` | presentation-uses-facade finding; export-shape finding (`export-denied(model)`) | Neighbor of `RG01`: a listed import is handed out and its slots judged |
| `TC01` | `products/legacy/backend/management/commands/seed_widgets.py` | A dev seeder that is not on the named list | thin-commands finding | Seeders by name only (D104) |
| `TC02` | `products/legacy/backend/management/commands/backfill_widget_names.py` | A one-off backfill imports logic | thin-commands finding | D93, D104 |
| `TC03` | `products/legacy/backend/management/commands/sync_widgets.py` | An ordinary command imports its product's tasks | thin-commands finding | |
| `TC04` | `products/inventory/backend/management/commands/show_stock.py` | A command that goes through its facade | clean | |
| `TC05` | `products/inventory/backend/management/commands/restock_now.py` | A command imports a name its facade's tasks module hands out from the product's tasks | clean | Judged at the origin, excused when the surface is the product's facade (Nov 16) |
| `TC05` | `products/visual_review/backend/management/commands/seed_dummy_runs.py` | A dev seeder on the named list writes through its models | clean | Named as test code (D104) |
| `AT01` | `products/legacy/backend/presentation/views.py` | Presentation opens a transaction (`with` and decorator) | clean | atomic-outside-views is retired with D115: facade-no-state judges transactions in facades, and no rule judges them in presentation |
| `AT02` | `products/inventory/backend/logic/stock.py`, `products/legacy/backend/api/widgets.py` | A transaction outside presentation | clean | |
| `PT01` | `products/legacy/backend/tasks/cleanup.py` | A production task imports a test factory | prod-imports-test finding | |
| `PT04` | `posthog/temporal/ingestion_acceptance_test/runner.py`, `posthog/temporal/ingestion_acceptance_test/test_cases_discovery.py` | Production code imports a production module named like a test | clean | The test-code label leaves that one named file out (Nov 16) |
| `PT02` | `products/catalog/backend/facade/testing.py` | `facade/testing.py` imports the product's test factory | clean | Test support (Nov 12) |
| `PT03` | `products/legacy/backend/tests/test_widgets.py` | A test imports a test factory | clean | |
| `SV00` | `products/warehouse_sources/` | A product with only a sources tree, no backend | has-facade finding (two); the level fails it | D108: the structure |
| `SV01` | `products/warehouse_sources/sources/alpha/source.py` | A vendor imports core and another product's facade; next to them the SDK and another vendor | sources-vendors-use-sdk finding for core and the product; clean for the sources tree | import-linter "sources vendors import shared code only from the sources SDK" (Nov 16) |
| `SV02` | `products/warehouse_sources/sources/sdk/base.py`, `products/warehouse_sources/sources/sdk/testing.py` | The SDK imports core and `common`; its testing module imports `common` | sources-vendors-use-sdk finding for `common` in the SDK; clean for core and for the testing module | The contract's ignore list: the SDK may import core and products, its testing module `common` |
| `SV03` | `products/warehouse_sources/sources/conftest.py` | The tree's conftest imports a product and core | clean for the product; sources-vendors-use-sdk finding for core | The contract's ignore list: conftest may import products |
| `SV04` | `products/warehouse_sources/sources/alpha/source.py`, `products/warehouse_sources/sources/alpha/lazy.py`, `products/warehouse_sources/sources/__init__.py` | A vendor imports another vendor directly, and through the tree's own package; next to them a chain through the SDK | sources-vendors-independent finding for both; clean through the SDK | import-linter "sources vendors do not import each other" (independence, chains included); its ignore list cuts every import of the SDK |
| `SV05` | `products/warehouse_sources/sources/sdk/catalog.py` | The SDK imports a vendor | sources-vendors-independent finding | The SDK is one of the independent members; only imports into it are cut |
| `WC01` | `products/legacy/backend/webhook_consumers.py` | A webhook consumers module imports its product's logic, and another product's facade | webhook-consumers-use-facade finding for the logic; clean for the facade | import-linter "webhook consumers must only import facade" (Nov 16) |
| `MC01` | `products/legacy/backend/logic/widgets.py` | A product queries another product's model; next to it an annotation, `DoesNotExist`, `cast`, a `values()` chain and the team-extension registry | model-crossings clean for all: the model came in past the export, so public-surface owns the coupling (A5); `PS08` is the query through a door | The use shapes of Nov 12; A5 (Nov 16) |
| `MC03` | `products/legacy/backend/logic/widgets.py` | A product prefetches another product's model (`Prefetch(..., queryset=Model.objects...)`) | model-crossings clean: the import is public-surface's (A5) | The prefetched rows are instances on the consumer's objects; no decision allowed the shape (Nov 16) |
| `MC02` | `posthog/test/test_auth.py`, `products/legacy/backend/migrations/0001_initial.py` | A crossing in a test and in a migration | clean | |
| `GM01` | `products/legacy/backend/logic/widgets.py` | `apps.get_model` names another product's model | get-model finding | |
| `GM02` | `products/legacy/backend/logic/widgets.py` | `apps.get_model` names its own model | clean | |
| `GM03` | `products/legacy/backend/logic/widgets.py` | `apps.get_model` with a variable label | get-model incomplete; a cycle incomplete item (`CY03`) | Incomplete, not a finding (Nov 16) |
| `GM04` | `products/legacy/backend/migrations/0001_initial.py` | `apps.get_model` in a migration | clean | |
| `RA01` | `products/legacy/backend/models.py` | An unsealed relation into another product's model | reverse-accessor finding | |
| `RA02` | `products/legacy/backend/models.py` | An unsealed relation into a core model | reverse-accessor finding | |
| `RA03` | `posthog/models/sharing.py` | A core model with an unsealed relation into a product | reverse-accessor finding | |
| `RA04` | `products/inventory/backend/models.py`, `products/reports/backend/models.py`, `posthog/models/organization.py` | Sealed relations and core-to-core relations | clean | |
| `GM05` | `posthog/rbac/migrations/rbac_widget_migration.py` | A data migration under `posthog/rbac/migrations/` (run by hand) names a product model with `apps.get_model` | clean | `posthog/rbac/migrations/` takes the migration context: frozen history (Nov 16) |
| `RD01` | `products/legacy/backend/receivers.py` | A receiver on another product's sender, in a watched module | receiver-direction finding; public-surface finding for the import; model-crossings clean (`sender`); wiring-watched clean | Cross-check of Nov 12; Nov 12 |
| `RD02` | `products/legacy/backend/stock_listeners.py` | The same in an unwatched module | the same, and a wiring-watched finding | |
| `RD03` | `products/legacy/backend/tests/test_stock_signals.py` | The same in a product's test file | public-surface finding for the import; receiver-direction and wiring-watched clean | A receiver in a test is no wiring (Nov 12) |
| `RD04` | `products/reports/backend/stock_receivers.py` | A receiver whose sender is a call's result | receiver-direction incomplete; wiring-watched incomplete | A sender hoglint cannot place is an item, not a finding (Nov 16); placing it is the facts' work |
| `RD03` | `products/inventory/backend/logic/stock.py` | A receiver on its own product's sender | clean | |
| `RD05` | `posthog/models/stock_events.py`, `products/legacy/backend/rotation_listeners.py`, `products/legacy/backend/signals.py` | Receivers with no sender in an unwatched module: on a core signal, on the product's own signal, on Django's `request_started` (a stub in `site/`) | wiring-watched finding for the core signal's receiver; clean for the other two | A receiver with no sender is placed at its signal: the signal's module is what fires it (Nov 16) |
| `RD06` | `products/inventory/backend/facade/count_signals.py`, `products/reports/backend/count_watch.py` | A receiver with no sender on another product's signal, in an unwatched module | receiver-direction finding (the signal's product as the sender's); wiring-watched finding | The same (Nov 16) |
| `RD07` | `products/legacy/backend/signals.py`, `products/inventory/backend/logic/rotation.py` | A hook's own body connects its parameter: to its module's signal, and to a core signal from an unwatched module | clean | The hook's callers are placed as installs, so its body is not asked again (`hook` attr; Nov 16) |
| `IN01` | `products/legacy/backend/apps.py`, `products/legacy/backend/hooks.py` | A product registers a function into a core hook from an unwatched module | installs clean (a list); wiring-watched finding | `installs` is a list (Nov 12) |
| `IN02` | `products/legacy/backend/apps.py` | A product registers a model class into a core registry | clean beyond `IN01` | D100 (wanted) |
| `IN03` | `products/reports/backend/apps.py`, `products/inventory/backend/facade/hooks.py` | A product registers into another product's facade hook | wiring-watched finding | |
| `IN04` | `products/inventory/backend/apps.py` | A product registers into its own hook | clean | |
| `IN05` | `products/inventory/backend/facade/stock_signals.py`, `products/reports/backend/stock_watch.py`, `products/inventory/backend/restock_watch.py` | A product connects a receiver through another product's helper that hands it on to `<signal>.connect`, from an unwatched module; next to it the owner's own call | installs edge from the caller; wiring-watched finding for the caller; clean for the owner | A function that hands its argument on to `connect` is a hook its callers install into (Nov 16) |
| `IN06` | `posthog/admin/console/registry.py`, `products/reports/backend/logic/console_pages.py`, `hoglint.toml` | A configured hook named through a module-level instance (`registry.console.register_moved_page`), called through an attribute typed with the instance's class, from an unwatched module; next to it a same-named method of another class, and a hook named through an instance a function makes (`registry.actions`), called the same way | installs edge and wiring-watched finding for the typed call; clean for the other class; installs and wiring-watched incomplete for the hook whose instance's class cannot be told | A typed call of the instance's class is a call of the hook; one through a base class or an untyped instance stays unresolved, never clean (Nov 16) |
| `UI01` | `products/gatekeeper/backend/wiring.py` | An install whose hook cannot be resolved, in an unwatched module of a narrowed product | installs incomplete; wiring-watched incomplete; the product is not Isolated | `wiring-watched` is strict for the level |
| `UI02` | `products/notifications/backend/wiring.py` | The same in a product with no contract-check task | the same; the product is not Isolated | |
| `DR01` | `posthog/test/test_widget_query.py`, `products/catalog/backend/hogql_queries/entries.py` | A core test runs a product's query kind | drives finding; wiring-watched finding for the location's `__init__.py` | |
| `DR02` | `posthog/test/test_widget_query.py`, `products/reports/backend/hogql_queries/widget.py`, `chart.py` | The same into a location without `__init__.py`: one file directly inside unwatched, one watched | drives finding; wiring-watched finding for `widget.py` only | Each module file directly inside such a location stands for it, as `__init__.py` does for a package (Nov 12) |
| `DR03` | `products/reports/backend/tests/test_widget_query.py` | A product's own test runs its kind | clean | |
| `DR04` | `posthog/test/test_widget_query.py`, `products/legacy/backend/hogql_queries/audit.py` | A core test runs a kind of a location without `__init__.py` whose files are all watched | drives finding (a list); wiring-watched clean | Clean neighbor of `DR02` |
| `WW01` | `posthog/auth.py`, `products/gatekeeper/backend/facade/api.py`, `products/gatekeeper/backend/logic/decisions.py` | Core's request code imports a facade at module level; the logic behind it is unwatched | request-path rows for the facade and the logic; wiring-watched finding for `logic/decisions.py`; no-product-lists-in-core clean (D93) | D103 |
| `WW02` | `posthog/middleware.py`, `posthog/permissions.py`, `products/access_control/backend/logic/rules.py` | The same through an import inside a function of a module the request path reaches | no request-path row; wiring-watched clean | A written limit of D103 |
| `WW03` | `posthog/settings/web.py`, `products/gatekeeper/backend/middleware.py`, `products/gatekeeper/backend/logic/session.py` | Settings wire a product middleware whose logic is watched | request-path rows for the middleware and the logic; clean for wiring-watched | D103 |
| `WW04` | `posthog/settings/web.py`, `products/gatekeeper/backend/debug_gate.py` | A middleware in a list that `MIDDLEWARE` splats in (`*_DEBUG_GATES`), unwatched | request-path row; wiring-watched finding; no-product-lists-in-core clean (settings may name product paths, Nov 16) | The splatted list belongs to `MIDDLEWARE` (Nov 12) |
| `WW05` | `ee/settings.py`, `products/gatekeeper/backend/admin_gate.py` | A second settings module inserts a middleware (`MIDDLEWARE.insert`), unwatched | the same | `insert` names `MIDDLEWARE` (Nov 12) |
| `WW06` | `posthog/settings/web.py`, `products/gatekeeper/backend/throttles.py` | A request key extended through a subscript (`REST_FRAMEWORK[...].append`): the setting cannot be told | wiring-watched incomplete (`rule request-path cannot tell`); `throttles.py` is not followed, but settings name it, so wiring-watched asks for it (`WW18`) | Unknown is never clean: the setting reads unknown, never absent (Nov 12) |
| `WW07` | `hoglint.toml` (planted line) | A request seed whose path matches no file | config:stale-name incomplete | A path the config writes out in full and the tree lacks is incomplete (Nov 12) |
| `WW08` | `posthog/middleware.py`, `posthog/request_log.py` | An import on the request path that does not resolve | prod-imports-test unknown; wiring-watched incomplete (`rule request-path cannot tell`) | Unknown is never clean (Nov 12) |
| `WW10` | `products/gatekeeper/backend/middleware.py`, `products/gatekeeper/backend/facade/lazy_gate.py`, `products/gatekeeper/backend/logic/lazy_rules.py` | A facade on the request path hands out its logic lazily (`_LAZY`, `__getattr__`); the logic is unwatched | request-path row for the facade only; wiring-watched clean | A written limit of D103: a lazy edge (`function_local`) does not run on import, so the reach does not follow it |
| `WW11` | `posthog/settings/web.py`, `products/gatekeeper/backend/session_gate.py` | `AUTHENTICATION_BACKENDS = list(_GATE_BACKENDS)`: a list built from another name, unwatched | request-path row; wiring-watched finding | The request path follows every settings entry except an exclude list (Nov 12) |
| `WW12` | `posthog/settings/web.py`, `products/gatekeeper/backend/token_check.py` | A settings list a function returns, unwatched | the same two | A path string in a settings module is followed whatever function holds it (Nov 12) |
| `WW13` | `posthog/settings/web.py`, `products/gatekeeper/backend/validator.py` | A key no list names (`OAUTH2_PROVIDER`) points at a product class, unwatched | the same two | A key the exclude list lacks is followed (Nov 12) |
| `WW14` | `posthog/settings/web.py`, `products/gatekeeper/backend/login_flow.py` | A key on the exclude list (`SOCIAL_AUTH_PIPELINE`) points at a product function, unwatched | no request-path row; wiring-watched finding through the settings cover (`WW18`), none through the request path | Clean neighbor of `WW11` to `WW13` for the request path: a login flow behind one URL is not the per-request pipeline (Nov 12) |
| `WW15` | `products/catalog/backend/migrations/`, `products/inventory/backend/migrations/` | A narrowing product's migrations, unwatched; next to them a product that watches its models and migrations | wiring-watched finding for each unwatched file; clean for `inventory` | The model surface: a model is reachable without an import (Nov 16) |
| `WW16` | `products/legacy/backend/routes.py`, `products/legacy/backend/webhook_consumers.py`, `products/legacy/backend/health_checks.py`, `products/reports/backend/health_checks.py`, `products/inventory/backend/routes.py` | Registry slots core loads by name in narrowing products, unwatched; next to them watched ones | wiring-watched finding for each unwatched slot; clean for `inventory` | `load_product_modules` imports them with no import edge (Nov 16) |
| `WW17` | `products/legacy/backend/tasks/cleanup.py`, `products/legacy/backend/temporal/jobs.py`, `products/reports/backend/temporal/workflows.py`, `products/catalog/backend/hogql_queries/entries.py` | Wiring locations core runs by name with no watched file inside: packages, and a directory without `__init__.py` (`legacy`'s `temporal/`); next to them a query runner no outside test drives | wiring-watched finding, one per location (the directory, not each file in it); clean for the query runner (`drives` reads that channel) | The product lint's wiring locations by presence (Nov 16) |
| `WW20` | `products/catalog/backend/tasks/sweeps/stale.py`, `products/catalog/turbo.json` | A wiring location core runs by name whose only watched file sits two levels down; its `__init__.py` and the files directly inside are unwatched | clean | Any one watched file inside watches the location, as in the product lint (the owner, Nov 16) |
| `WW18` | `posthog/settings/web.py`, `products/catalog/backend/apps.py`, `products/gatekeeper/backend/login_flow.py`, `products/gatekeeper/backend/throttles.py` | Settings name a product module under a key the request path does not follow (`INSTALLED_APPS`, a login flow) or cannot tell | wiring-watched finding for each unwatched one; clean for `inventory`'s and `gatekeeper`'s watched apps modules | Every settings key, not only the request pipeline's (Nov 16) |
| `WW19` | `products/catalog/backend/apps.py`, `products/catalog/backend/events.py`, `products/inventory/backend/apps.py` | An AppConfig module imports its product's module at module level, unwatched; next to it an import of the product's own facade | wiring-watched finding for `events.py`; clean for the facade | The AppConfig module's imports at setup (Nov 16); the body of `ready()` is `WW23` |
| `WW21` | `products/shelf/backend/logic/menus.py`, `products/gatekeeper/backend/wiring.py` | A call through an instance attribute whose class the class tells (`self.family = FAMILIES[kind]`, `FAMILIES: dict[str, ShelfFamily]`) and whose member shares its last name with a configured hook; next to it a call through an attribute the class cannot type (`self.registry = registry_factory()`) | clean for the typed call (a member of `ShelfFamily`, no hook), and `shelf` keeps the level; installs and wiring-watched incomplete for the other | A call that resolves to something other than the hook is no hook call; unknown is never clean (the architect, Nov 16) |
| `WW22` | `products/gatekeeper/backend/apps.py`, `products/gatekeeper/backend/logic/session.py` | `ready()` connects a receiver once per label of a module-level tuple (`for model in SESSION_MODELS: post_save.connect(..., sender=model)`), in a watched module; next to it a loop over what a function returns | one placed signal edge per sender and signal, clean (`apps.py` is watched, the senders are core models); the other is incomplete in receiver-direction and wiring-watched | A loop variable bound over a literal tuple or list takes each of its values; anything else stays unknown (the architect, Nov 16); an unplaced sender is an item, not a finding (`RD04`, Nov 16) |
| `WW23` | `products/legacy/backend/apps.py`, `products/reports/backend/apps.py`, `products/inventory/backend/apps.py`, `products/inventory/backend/logic/audit.py`, `products/inventory/backend/logic/reorder.py` | An installed AppConfig's `ready()` imports its product's module in its body, unwatched (`legacy`'s `hooks.py`, `reports`' `listeners.py`); next to them a watched one (`inventory`'s `logic/stock.py`), an import inside a function `ready()` defines and hands on to a hook (`audit.py`), and one inside a module-level function it hands on (`reorder.py`) | wiring-watched finding for each unwatched module the body imports; clean for the others | Django calls `ready()` at setup in every process, as the old lint reads it; a callback runs later, if ever, and is its own entry module (the architect, Nov 16, under the owner's parity bar) |
| `AD01` | `posthog/settings/web.py`, `products/catalog/backend/admin.py`, `products/catalog/backend/logic/refusals.py` | Django's admin app is installed (`SimpleAdminConfig`); a product's `admin.py` imports its product's module at module level, unwatched; next to it an import inside a function and an import of another product's facade | wiring-watched finding for `admin.py` and `logic/refusals.py`; clean for the others | Admin autodiscovery imports the module by name, and core admin tests run what it registers (the architect, Nov 16) |
| `AD02` | `products/inventory/backend/admin/__init__.py`, `products/inventory/backend/admin/stock_admin.py`, `products/inventory/backend/logic/notes.py` | An admin package whose `__init__.py` imports a submodule, which imports the product's logic | wiring-watched finding for `admin/__init__.py` and `admin/stock_admin.py`; clean for `logic/notes.py` | Entry modules only: no transitive closure (Nov 9) |
| `AD03` | `products/shelf/backend/admin.py` | An `admin.py` of a product whose app is not installed | clean; `shelf` stays Isolated | Autodiscovery imports the `admin` module of installed apps only |
| `CO01` | `products/catalog/backend/console.py`, `products/inventory/backend/console.py`, `products/inventory/turbo.json` | A product's `console.py`, which the admin console imports by name, unwatched; next to it a watched one | wiring-watched finding for `catalog`'s; clean for `inventory`'s | The admin console's discovery is a registry (`registries.admin_console`, round 2, Nov 17) |
| `SN01` | the preset | The preset writes out monorepo paths and symbols this miniature does not hold; the tree runs the real preset and holds only the files its cases need, so these rows stay | config:stale-name incomplete for each | A path or symbol the config writes out in full and the tree lacks is incomplete, never silent (Nov 12) |
| `UK01` | `scripts/new_tool.py` | A file in a top folder no assignment names imports product internals (the source end unknown) | incomplete in every rule that crosses packages; product-code-at-home incomplete | D105: a new top folder reads unknown |
| `UK02` | `products/legacy/backend/logic/widgets.py`, `scripts/helpers.py` | A product imports from such a folder (the destination end unknown) | public-surface and cycle incomplete | D105 |
| `DI01` | `products/legacy/backend/logic/widgets.py` | A computed import outside a registry loader | dynamic-imports incomplete | |
| `DI02` | `posthog/products.py` | The registry loader's computed import | clean | |
| `DI03` | `products/legacy/backend/health_checks.py` | A computed import in a file a registry loads (a lazy `__getattr__`), outside the registry's loader | dynamic-imports incomplete | Only a declared loader's own call is exempt; a registry's files are judged as any other file (the architect, Nov 18) |
| `CY01` | `products/catalog/backend/facade/api.py`, `products/reports/backend/logic/build.py` | Two products that depend on each other | no-new-package-cycles finding, one per direction | |
| `CY02` | `products/reports/backend/logic/build.py`, `products/catalog/backend/facade/shapes.py` | One-way dependencies | clean | |
| `CY03` | `products/legacy/backend/logic/widgets.py`, `products/legacy/backend/facade/aboveworkers.py`, `products/legacy/backend/facade/lazygoneworkers.py` | An unresolved model label; an unresolved relative import; a lazy entry no module answers for | no-new-package-cycles incomplete, one item each | An edge whose end hoglint cannot tell names no package pair (Nov 16) |
| `FO01` | `products/access_control/backend/logic/rules.py` | A foundation product imports a feature product's facade | foundation-direction finding | |
| `FO02` | `products/access_control/backend/logic/rules.py` | A foundation product imports another foundation product | clean | |
| `IF01` | `posthog/egress/client.py` | Infrastructure imports a product's facade | infrastructure-direction finding; no-product-lists-in-core clean (D93) | |
| `IF02` | `posthog/kafka_client/client.py`, `ee/billing/quota.py` | Infrastructure imports a proto-product | infrastructure-direction finding | |
| `IF03` | `posthog/clickhouse/client/execute.py` | Infrastructure imports `common/` | clean | |
| `LV01` | `packages/owners_yaml/django_glue.py` | A leaf imports Django and core | leaves-stay-leaves finding (two) | |
| `LV02` | `packages/owners_yaml/parse.py` | A leaf with stdlib imports | clean | |
| `OI01` | `posthog/egress/client.py` | A framework package takes a model | orm-independent finding | |
| `OI02` | `posthog/egress/client.py` | A framework package takes `**kwargs: Any` | orm-independent incomplete | Loose types are incomplete here (Nov 12, from D93's noise list) |
| `OI03` | `posthog/egress/client.py` | A framework package with typed parameters | clean | |
| `MD01` | `products/legacy/backend/migrations/0001_initial.py`, `products/db_routing.yaml` | A migration on its own database depends on a default-database migration | migration-database finding | |
| `MD02` | `products/inventory/backend/migrations/0001_initial.py` | Both on the default database | clean | |
| `MD03` | `posthog/settings/web.py`, `products/db_routing.yaml`, `hoglint.toml` | A third-party app installed from site-packages (`site/tokens`) ships a migration that depends on a core migration, and routes to another database | clean | hoglint reads only the repo's own migrations; the Django test stays the authority (D117) |
| `MD04` | `products/reports/backend/migrations/0002_extend.py` | `dependencies += [...]` in a Migration class body | migration-database incomplete | An augmented or computed dependency list is never read as silent (D117); the base passed it with no edge and no item |
| `OD01` | `products/inventory/backend/models.py` | Every product model | clean: own-database is off | Off for now: no producer, and the policy was never decided (D118) |
| `PH01` | `posthog/temporal/ai_observability/summarize.py` | Product code under `posthog/` | product-code-at-home finding, one per file | |
| `TP01` | `posthog/temporal/alerts/checks.py`, `posthog/temporal/common/worker.py`, `posthog/temporal/ai_observability/summarize.py` | Code in a folder under `posthog/temporal/` that core does not own; next to it a core-owned folder and a folder already labelled a product's | temporal-placement finding for `alerts/checks.py` (counted per folder); clean for `common/`; `ai_observability` stays product-code-at-home's (`PH01`) | T9: placement is a ratchet; an allowlist of the folders core owns (round 2, Nov 17) |
| `PH02` | `posthog/temporal/common/worker.py` | Core code next to it | clean | |
| `RS01` | `products/inventory/backend/health_checks.py`, `products/catalog/backend/webhook_consumers.py` | Well-formed registry slot files | clean: no rule | registry-slots left the preset: the loader checks the registry contract at run time (D118) |
| `RS02` | `products/legacy/backend/health_checks.py` | A slot file without its attribute | clean: no rule | The same (D118) |
| `RS03` | `products/legacy/backend/webhook_consumers.py` | A slot member that does not subclass the base | clean: no rule | The same (D118) |
| `GP01` | `posthog/schema.py`, `posthog/schema_enums.py` | Generated files | clean: no rule | generated-producer left the preset: the monorepo's own generated-files test stays (D118) |
| `HD01` | `products/reports/backend/health_checks.py` | A health check imports `posthog.dags` | health-checks-no-dags finding (advisory) | |
| `HD02` | `products/inventory/backend/health_checks.py` | A health check without it | clean | |
| `DO01` | `products/legacy/backend/logic/widgets.py`, `posthog/ingress/dispatch.py` | A product imports a core package past its door (`exports.ingress` in the preset) | public-surface finding | D107, D112: the rule reaches a destination where an export's `within` holds the path (`dst.door`) |
| `DO02` | `products/catalog/backend/webhook_consumers.py`, `posthog/ingress/contracts.py` | A product imports the door | clean | |
| `DO03` | `products/legacy/backend/tests/test_widgets.py` | A product's test imports the same module past the door | clean | D112: tests may reach into a core door package; a test that reaches another product's internals stays a public-surface finding (`PS09`, D93) |
| `DO04` | `posthog/ingress/views.py` | A module of the door package imports its own internals | clean | Not another package |
| `DO05` | `products/catalog/backend/events.py`, `posthog/kafka_client/client.py` | A product imports a core package that declares no door | clean | `dst.door` is false: public-surface does not reach a core package without an export |
| `DO06` | `products/legacy/backend/logic/payloads.py`, `posthog/ingress/handlers/retry.py` | A product imports a directory without `__init__.py` inside the door package | public-surface unknown (`dst.exported` of a directory cannot be told) | The directory reads `door` from the path that labels it, as it reads a zone |
| `DO07` | `products/legacy/backend/facade/aboveworkers.py` | An import that does not resolve | public-surface unknown (`unresolved-import`, the `HZ06` row) | An end that does not resolve reads `door` as unknown, as every field |
| `LE01` | the preset | The Isolated level | `shelf` passes; every other product fails, each on a row of `LE02` to `LE09` or on unwatched wiring (`DR01`, `IN01`, `UI01`, `UI02`, `WW01`); `ai_observability` is not judged: it has no code under `products/` | D108 |
| `LE02` | `posthog/settings/web.py`, `ee/settings.py`, `posthog/tasks/scheduled.py`, `posthog/hogql_queries/query_runner.py`, `posthog/management/commands/start_temporal_worker.py`, `posthog/api/widget_report.py`, `posthog/clickhouse/schema.py`, `posthog/migrations/0002_entry_table.py`, `posthog/test/test_auth.py`, `tools/codegen/generate.py`, `bin/build_types.py` | Code outside the products (settings, registrations, core production code, a migration, a core test's patch target, a tool) reaches a product file past its export, and the product's contract-check inputs leave the file out | reached-watched finding per file; the product is not Isolated | D108 |
| `LE03` | `posthog/api/shelf_labels.py`, `products/shelf/backend/logic/labels.py` | Core reaches a product file past its export, and the file is watched | public-surface finding; reached-watched clean; `shelf` stays Isolated; no-product-lists-in-core clean (no registration point) | Clean neighbor of `LE02`: a change to a watched file runs the full suite (D108) |
| `LE04` | `products/reports/backend/logic/shelf_counts.py`, `products/shelf/backend/logic/count.py`, `products/shelf/turbo.json` | Another product reaches a product file past its export, and the file is watched | public-surface finding; reached-watched clean; `shelf` stays Isolated | Clean neighbor of `LE12`: a change to a watched file runs the full suite |
| `LE05` | `products/inventory/backend/routes.py`, `products/legacy/backend/presentation/views.py`, `products/legacy/backend/presentation/review_views.py`, `products/legacy/backend/routes.py` | A product's own presentation reaches its own internals past its facade (the rows of `PF02`, `PF03`, `PF04`, `PF08`, `RG01`, `RG02`) | presentation-uses-facade finding; the product is not Isolated | D108 |
| `LE06` | `products/shelf/backend/presentation/views.py` | Presentation reaches another product's logic (and, in `PF05`, another product's presentation) | presentation-uses-facade and public-surface findings; neither product's level counts the row; `shelf` stays Isolated | Clean neighbor of `LE05`: rows between two products are lint only (`within_group`, D108) |
| `LE07` | `products/shelf/backend/facade/contracts/__init__.py`, `products/shelf/backend/facade/api.py` | A facade with a contracts package and an api module that defines a function | has-facade clean | D108 |
| `LE08` | `products/racks/backend/facade/api.py` | A facade whose api module only re-exports a function | has-facade finding (`defines no function`); `racks` is not Isolated | Neighbor of `LE07` (D108) |
| `LE09` | `products/access_control`, `products/batch_exports`, `products/gatekeeper`, `products/notifications`, `products/reports`, `products/visual_review` | A product without a contracts module, or without a facade | has-facade finding for each missing file; the product is not Isolated | D108 |
| `LE10` | `products/catalog/turbo.json`, `products/gatekeeper/turbo.json`, `products/inventory/turbo.json`, `products/legacy/turbo.json`, `products/reports/turbo.json`, `products/shelf/turbo.json` | A product's own `turbo.json` narrows its contract-check inputs; next to them products that inherit the root's inputs or run no contract-check | each narrowing product claims Isolated; the five that fail it fail `check`, whatever the ledger; `shelf` claims it and passes | An unsound skip is never debt (Nov 16) |
| `LE11` | `products/kiosk/scripts/build_assets.py` | A product folder without `__init__.py` and with no backend (a frontend product with a build script) | has-facade finding (two); `kiosk` fails Isolated | A layout row's subject that is a folder without `__init__.py` reads its group from the folder when the package above reads none: `products/__init__.py` has no package (Nov 16) |
| `LE12` | `products/legacy/backend/facade/contracts.py`, `products/catalog/backend/events.py` | Another product reaches an unwatched product file past its export (`legacy`'s contracts import `catalog`'s events module) | reached-watched finding for `events.py` | D123: a product's dependents are retested only when its contract-check is affected, so another product's reader counts as core's does |
| `OR01` | `posthog/api/stock_report.py`, `products/inventory/backend/facade/api.py`, `products/inventory/backend/logic/restock_rules.py` | Core production code calls a function the facade passes on from its logic; next to it one the facade defines itself (`get_stock`) | reached-watched finding for `logic/restock_rules.py`; clean for `get_stock` | T7 (the owner, D120): a facade re-export does not end the reach; a facade that defines the name is the end (D101) |
| `OR02` | `posthog/test/test_stock_report.py`, `products/inventory/backend/logic/reorder_points.py` | A core test imports a name the facade passes on | reached-watched finding | T7 with D108's reader scope: a test outside the products counts |
| `OR03` | `posthog/test/test_stock_views.py`, `products/inventory/backend/presentation/views.py`, `products/inventory/backend/logic/shelf_life.py` | A core test patches a name a product view took from its facade | reached-watched finding for `logic/shelf_life.py` (`via.*`: the facade is a module the name passes through); clean for the watched view | The measurement's `:via` readers (Nov 16) |
| `OR04` | `products/reports/backend/logic/stock_tags.py`, `products/inventory/backend/logic/stock_labels.py` | Only another product reads a name the facade passes on | reached-watched finding | D123: another product's reader counts (a dependent is retested only when the contract-check is affected) |
| `OR05` | `posthog/api/stock_report.py`, `products/inventory/backend/logic/audit_notes.py` | Core reads a hand-out only under `if TYPE_CHECKING` | clean | A type-only import does not run |
| `OR06` | `posthog/management/commands/start_temporal_worker.py`, `products/inventory/backend/facade/temporal.py`, `products/inventory/backend/logic/stock_jobs.py`, `stock_warmup.py`, `stock_cleanup.py` | A registration point puts a hand-out into a literal; next to it one it calls, and one it imports and never loads | clean for `stock_jobs.py`; reached-watched finding for `stock_warmup.py` and `stock_cleanup.py`; no-product-lists-in-core finding per origin module the import names | Case 1 of the measurement (D120); a use the facts cannot tell counts (unknown is never clean) |
| `OR07` | `posthog/api/stock_report.py`, `products/inventory/backend/logic/stock_sweeps.py` | Core calls a hand-out through a module alias of the facade module (`inventory_temporal.sweep_stock()`) | reached-watched finding for `stock_sweeps.py` | A name read through a module alias of an export module is a reference edge (round 2, Nov 17); it was a written limit before |
| `OR08` | `posthog/api/stock_report.py`, `products/gatekeeper/backend/facade/lazy_gate.py`, `products/gatekeeper/backend/logic/lazy_rules.py` | Core imports a name a facade hands out lazily (`_LAZY`) | reached-watched finding for `logic/lazy_rules.py` | A lazy map's entry is a hand-out (T7) |
| `OR09` | `posthog/test/test_stock_report.py`, `products/inventory/backend/facade/testing.py`, `products/inventory/backend/tests/factories.py` | A core test imports a test helper the fixture surface passes on | reached-watched finding for `tests/factories.py` | `facade/testing.py` hands names out to tests (the coordinator, Nov 16: the root conftest loads ws helpers through it) |
| `OR10` | `posthog/api/stock_report.py`, `products/inventory/backend/facade/api.py`, `products/inventory/backend/logic/__init__.py`, `products/inventory/backend/logic/windows.py` | The facade passes a name on through its logic package's `__init__.py` | reached-watched finding for `logic/__init__.py` and `logic/windows.py` | Every module on the name's path runs at import (`: chain`; the coordinator, Nov 16) |
| `OR11` | `products/reports/backend/tests/test_stock_alerts.py`, `products/reports/backend/logic/stock_alerts.py`, `products/inventory/backend/facade/api.py`, `products/inventory/backend/logic/restock_alerts.py` | A product's own test patches a name at its own module, which imported the name from another product's facade; the origin is unwatched | clean for `logic/stock_alerts.py`; reached-watched finding for `inventory`'s `logic/restock_alerts.py` | The module the code names is the reader's own, so its own import is the crossing (the ws trace, Nov 17). The origin row is the neighbor that must stay: the module's own import reads `inventory`'s hand-out |
| `OR12` | `posthog/test/test_restock_alerts.py`, `products/inventory/backend/facade/api.py`, `products/inventory/backend/logic/restock_alerts.py` | A core test patches the same name at the facade that hands it out | reached-watched finding for `logic/restock_alerts.py` (`OR11`'s row) | Second neighbor of `OR11`: a reader outside the product that names the facade still asks for the origin |
| `WA01` | `posthog/management/commands/start_temporal_worker.py`, `products/depot/backend/facade/temporal.py`, `products/depot/backend/temporal/__init__.py`, `restock.py`, `activities.py`, `products/depot/backend/temporal/audit/__init__.py`, `products/depot/backend/temporal/audit/workflow.py`, `restock_helpers.py`, `schedule.py` | A registration point registers a list a facade hands out; one item is itself a list in a submodule; next to them a helper an item imports, and a module of the location no list names | in `hoglint watched` with the derived preset (`derive.py`, `test_watched.py`): the location's entry, each item's module and the inner list's module are watched; the helper and the unregistered module are not; no-product-lists-in-core finding for `depot`; export-shape unknown for the two lists the facade hands out | The item reading (the architect's pick 1, Nov 17); helpers behind an item may leave selection (the owner, Nov 17) |
| `WA02` | `products/depot/backend/apps.py`, `products/depot/backend/registration.py`, `products/depot/backend/logic/restock_rules.py`, `audit_trail.py`, `transfer.py`, `wording.py`, `alias_target.py`, `cold.py` | A declared hook receives a nested def of `ready()`, a nested def of a method `ready()` calls, a module-level def of `apps.py`, a function `registration.py` imports, and a def through the hook imported under another name; next to each, the same form that no hook receives (all import `logic/cold.py`) | in `hoglint watched`: the module each callback body imports, and the imported callback's module, are watched; `cold.py` is not; the `installs` edges carry `callback`, the imports `hook_callback` or `app_setup = "callback"` | The callback-body cover (the architect's pick 2, Nov 17) |
| `WA03` | `products/depot/backend/facade/README.md`, `products/depot/backend/presentation/views.py`, `products/depot/backend/migrations/max_migration.txt`, `products/depot/backend/logic/README.md` | Files under the zones a level watches, non-Python files too; next to them a file in no watched zone | in `hoglint watched`: watched; `logic/README.md` is not | `watch_zones` (the design's part S) |
| `WA04` | `products/depot/__init__.py`, `products/depot/backend/logic/__init__.py`, `products/depot/backend/tools/` | The package `__init__.py` above a watched file; next to it a package above no watched file | in `hoglint watched`: watched; `tools/` is not | Python runs every package above an imported module (the architect's pick 3, Nov 17) |
| `WA05` | `products/depot/product.yaml`, `products/shelf/product.yaml`, `products/racks/product.yaml` | `isolated: true` in a product's manifest; next to it `isolated: false` | in `hoglint watched` with the derived preset: `depot` and `shelf` claim Isolated and pass; `racks` claims nothing; with the preset as it is, nothing reads the files | The `yaml-key` claim adapter (the design's section 1) |
| `WA07` | `hoglint.toml` (`[django] hooks`), `products/depot/backend/moved_pages.py`, `products/racks/backend/hooks.py` | A hook declared as a table names its callable arguments: one with none, called with a local URL; one whose declared callable is a function's parameter | in `hoglint watched`: the URL is data, so `depot` passes; the parameter cannot be told (`callback` unknown), an incomplete item of `racks` | The architect, Nov 17: a hook's declaration says which arguments are callables; an unknown stays unknown |
| `WA06` | `products/racks/backend/facade/temporal.py`, `products/racks/backend/temporal/__init__.py` | A registered list holds an item hoglint cannot read (a subscript) | in `hoglint watched`: an incomplete item of `racks`, so `racks` cannot pass; with the preset as it is, the no-product-lists-in-core finding and the export-shape unknown of the hand-out | Unknown is never clean: the unknown is the list's product's |
| `WA08` | `posthog/management/commands/start_temporal_worker.py`; the products `lists_*` that `test_watched_shapes.py` lays over a copy | A list a registration point registers, built as a comprehension, `itertools.chain` with and without `list()`, a conditional expression, `functools.partial`, `\|`, `.copy()`, a class attribute core reads as `Class.WORKFLOWS`, read with `getattr(module, "WORKFLOWS")`, and re-listed by a core module (`HOP_ALL = [*P_WORKFLOWS]`); next to them the literal list | in `hoglint watched` with the derived preset: the module of each item is watched and the product passes | The item reading; a name the link cannot read is never a name with no items (the adversarial run on watched, Nov 18) |
| `WA09` | the products `decl_*` that `test_watched_shapes.py` lays over a copy, with core's record classes | A declaration whose record is bound to a name, whose tuples come from a name or another module, passed by `*args` or `**kwargs`, assigned from another module's declaration by attribute or by name, or built with `dataclasses.replace`; next to them the inline declaration and tuples a product function builds | in `hoglint watched`: each workflow's and schedule's module is watched; of the function, its own module only (class (a)) | The declaration reading (the architect, Nov 18) |
| `WA10` | the products `unread_*` that `test_watched_shapes.py` lays over a copy | A facade that serves the list from a module `__getattr__`, a library call handed the items, a comprehension that maps its items, `+=` in a block, a declaration bound by an import, a generator argument | in `hoglint watched`: an incomplete item of the product, so it cannot pass | Unknown is never clean |

## Unknown inputs of the rule language's abilities

Each ability has a case where its input cannot be told; the verdict there must never be clean.

| Ability | Unknown input | Case | Verdict here |
|---|---|---|---|
| `crosses-package` and the tier `tool` | A file in an unassigned top folder at the source end, at the destination end | `UK01`, `UK02` | incomplete |
| `owned_elsewhere` | A handed-out class whose tier is unknown | `WK03` | walked: finding |
| `owned_elsewhere` | A handed-out class from a top folder outside `first_party` | `WK04` | not walked; only incomplete rows on its edges (defect, wanted) |
| `handout_zones` | A handed name that is a namespace directory | `HZ03` | finding |
| `handout_zones` | A lazy hand-out; `import a.b as name`; a relative import above the top package | `HZ04`, `HZ05`, `HZ06` | finding, finding, incomplete |
| `handout_zones` | A lazy map of bare names where none resolves; a `'pkg.mod:Name'` value | `HZ13`, `HZ14` | clean for `handout_zones` (written limits); export-shape unknown where `__all__` lists the name |
| `dst.door` | An import that does not resolve | `DO07` | unknown |
| `[discovery] reference_names` | A use whose name does not resolve | `NS16` | no reference edge; the import's own rows are unknown or incomplete |
| `instance_write` (the Django pack) | A value that came from a helper or a loop | `NS14` | clean (a written limit) |
| Member slots of a handed-out class | A bare `dict`, a bare `object` or no annotation outside a surface file; the same in a class nested in a surface file's class | `ME09`, `ME10` | clean (written limits, wanted) |
| `dst.zone` | A directory two source roots place apart | none | The tree has one source root; a second would change every other case's module names |
| `may_cross` | A slot typed with a name that does not resolve | `CR03` | unknown |
| `installs` as a list | A hook call that does not resolve | `UI01`, `UI02` | incomplete; the product is not Isolated |
| Namespace-directory labels | A drives location without `__init__.py` | `DR02` | wiring-watched finding for the file directly inside |
| `through_denied` (reach) | An import on the request path that does not resolve | `WW08` | request-path incomplete, carried into wiring-watched |
| `setting` on settings edges | An entry added through a subscript (`X[...].append`) | `WW06` | the setting reads unknown: wiring-watched incomplete |
| Literal paths and named symbols of the config | A seed path that matches no file; the preset's paths this miniature lacks; a `reference_names` entry that names nothing or a re-export | `WW07`, `SN01`, `NS18`, `NS19` | config:stale-name incomplete |
| Rule covers of a coverage rule | What the covered rule cannot tell (an advisory rule's own items never reach a check's output) | `WW06`, `WW08` | wiring-watched incomplete |
| `reached-watched`'s edge cover | A source whose tier cannot be told | `UK01` | reached-watched incomplete on the source module, read by the level only (an advisory rule's items never reach a check's output); it names no product, so it blocks none (a written limit) |
| `edge.listed` (`only-registers`) | A registration point imports a hand-out and never loads it: nothing tells that it only registers it | `OR06` | reached-watched finding for `stock_cleanup.py` |
| `via.*` and `: chain` | A module on the name's path whose `exported` cannot be told | none | `via.zone` is never unknown here (the zone field has no domain); `via.exported` is unknown only for a name kept by a module whose `__all__` cannot be read, and no outside code reads a name through such a module in this miniature. The cover reads an unknown hop as it reads an unknown surface: an `unplaced` incomplete item |
| A name read through an export module's alias | An attribute the module does not bind (`api.missing()`) | none | No edge, as before round 2: the module's import stands for the use; an unknown attribute of a facade module is not built in the miniature |
| `uses_at_import` | A use whose import the rule does not report | none | with this preset no import is reported clean while its use is not, so no case can be built (`tests/native/test_evaluator.py` holds it) |
| `slots_reported_by` | A module the signature rule cannot tell about; a second signature rule the export rule does not name | none | A file's zone is never unknown in this preset (the zone field has no domain), and `orm-independent` holds for no export module and for no class the export rule walks; `tests/native/test_facade_rules.py` holds both |
| Namespace-directory labels | A directory whose files disagree on zone | none | Zones come from globs only here, so a directory reads one answer; a provider-set zone would be needed |

## Kinds from the findings triage

Every kind the triage of the rules found on the monorepo, and the case that shows it.
"None" means no case shows it yet, with the reason.

| Rule | Kind | Case |
|---|---|---|
| public-surface | A later use of a name already imported | `PS02` |
| public-surface | A use through a module attribute whose module import is a finding | `PS03`: judged at the import |
| public-surface | A string read as a dotted path outside a path sink (host names, task names, fixtures, a lint's data) | `DP01`, `DP02`: a string is an edge only in a path sink or a path place |
| public-surface | Settings name product paths | `PS11` |
| public-surface | A registration point names a wiring location | `PS13` |
| public-surface | An access-control mixin used from a legacy `backend/api/` viewset | `SY01` |
| public-surface | A migration names product code | `PS12` |
| public-surface | A declared permanent interface | `PS14` |
| public-surface | A test patches another product's internals | `PS10` |
| public-surface | A test seeds another product's models | `PS09` |
| public-surface | A test imports another product's non-model internals | `PS10` (by patch); by import: none, same shape as `PS07` |
| public-surface | Scripts and generators name product internals | `PS21` |
| public-surface | Core production code uses product models | `PS25` |
| public-surface | Product code uses another product's models | `PS20`, `MC01` |
| public-surface | Core `urls.py` mounts product views outside `routes.py` | `PS15` |
| public-surface | A product reuses another product's serializers or views | `PS17`, `PS18` |
| public-surface | Product code uses another product's non-model internals | `PS07` |
| public-surface | Core production code uses product non-model internals | `PS01` |
| export-shape | A house frozen dataclass or a pydantic frozen dataclass | `ES10` (house decorator); pydantic dataclass: none |
| export-shape | A private import read as an export | `ES10` (`frozen` in a contract module) |
| export-shape | A signature finding repeated | `ES02` |
| export-shape | An exception class | `ES06` |
| export-shape | A pydantic `BaseModel` contract | `ES07` |
| export-shape | A Temporal workflow or interceptor handed out | None: needs an approved-interface list, not decided |
| export-shape | A core HogQL class in a constants-only module | None |
| export-shape | The product's own model under the watched-models allowance | None: the allowance is not in the preset |
| export-shape | A sentinel class | None |
| export-shape | A `Protocol` hook type | `ES08` |
| export-shape | A core HogQL syntax-tree type in a signature | None |
| export-shape | A product model handed out by a re-export module | `ES01` |
| export-shape | A Django `TextChoices` handed out | None: the stub has no `TextChoices` |
| export-shape | An auth object crosses a facade | None |
| export-shape | A behavior class handed out | `BH01` |
| export-shape | A mutable data shape | `ES09` |
| export-shape | A management command in a facade | `ES11` |
| facade-signature | The core model list | `FS04` |
| facade-signature | `dict[str, Any]` as a parameter or field | `FS06` |
| facade-signature | A typed map of `object` | `FS13` |
| facade-signature | Bare `object` | `FS06` |
| facade-signature | The `frozen` decorator read as a facade function | None: no longer reported (`ES10`) |
| facade-signature | A function left out of `__all__` | None |
| facade-signature | A type alias read as unknown | `TA01` to `TA04`, `AC01` |
| facade-signature | A typed dict read as bare `dict` | `FS06` (`dict[str, str]`) |
| facade-signature | Fixture helpers in `facade/testing.py` | `ES12` |
| facade-signature | A product model crosses a facade | `FS01` |
| facade-signature | Another core model crosses | `FS05` |
| facade-signature | A queryset crosses | `FS02` |
| facade-signature | Bare `dict` | `FS06` |
| facade-signature | `Any` hides a model | `FS06` (bare `Any`) |
| facade-signature | A slot without annotation | `FS07`, `FS10` |
| facade-signature | `dict[str, Any]` returned | `FS06` |
| facade-logic | A thin wrapper | `FL01` |
| facade-logic | A mapper | `FL01` (`_to_level`) |
| facade-logic | A `facade/testing.py` helper | `FL05` |
| facade-logic | A small helper without ORM | `FL02` |
| facade-logic | A short ORM read in the facade | `FQ01` |
| facade-no-state | A write, a lock or a transaction in a facade function | `NS01` to `NS06` |
| facade-no-state | A read across teams in a facade function | `NS07` to `NS09` |
| facade-no-state | A read with branching around it that writes nothing | `NS12`: clean (the written limit) |
| facade-logic | A large function with ORM work | `FQ01` (small) |
| facade-logic | A large or branching function without ORM | `FL02` (`summarize`) |
| contracts-no-django | A chain through a function-local import | `CN02` |
| contracts-no-django | A module-level chain through another product's internals | `CN01` |
| contracts-no-django | A package `__init__` that loads Django | `CN03` |
| orm-independent | `**kwargs: Any` pass-through | `OI02` |
| orm-independent | A type alias read as unknown | None |
| facade-no-drf | `django.http` in a facade | `FD01` |
| facade-no-drf | DRF in a facade | `FD02` |
| facade-no-drf | A facade imports its own presentation | `FD03` |
| facade-no-drf | A facade imports a serializer from `backend/api/` | `FD04`, `FD06` |
| facade-no-drf | An unresolved type-only import routed here | None |
| presentation-uses-facade | An import through a facade re-export | `PF01` (clean on the facade itself) |
| presentation-uses-facade | The same whose origin is a model | None |
| presentation-uses-facade | `routes.py` imports viewsets from `backend/api/` | `PF03` |
| presentation-uses-facade | Presentation imports its own models or logic | `PF02` |
| presentation-uses-facade | Presentation imports another product's presentation | `PF05` |
| presentation-uses-facade | Presentation imports another product's models | None: same shape as `PF02` across products |
| no-new-package-cycles | An unresolved label onto `?` | `CY03`: incomplete; a migration's historical label: no row (the rule leaves migrations out) |
| no-new-package-cycles | A pair whose edges go through a facade | `CY01` (one direction) |
| no-new-package-cycles | A pair with non-facade edges | `CY01` (other direction) |
| drives | A test outside the product runs its query kind | `DR01`, `DR02` |
| drives | A test imports a runner by name | None |
| thin-commands | An import through the product's own facade | `TC04`, `TC05` (a name the facade re-exports) |
| thin-commands | Dev seeders | `TC01` |
| thin-commands | One-off backfills | `TC02` |
| thin-commands | Operational commands | `TC03` |
| product-code-at-home | Product code under `posthog/temporal/` | `PH01` |
| prod-imports-test | A string literal read as a dotted path | None: see public-surface |
| prod-imports-test | A stale import | None |
| prod-imports-test | A sanctioned test-support module imports test factories | `PT02` |
| prod-imports-test | Dev scripts import test code | None |
| prod-imports-test | A production module named `test_*.py` | `PT04`: clean |
| prod-imports-test | Shipped code imports test helpers | `PT01` |
| installs | A product registers a callback into a core or facade hook | `IN01`, `IN03` |
| installs | The hook lives in another product's internals | None |
| installs | An install made from a test | None |
| installs | A hook call that does not resolve | None |
| wiring-watched | An unwatched module that installs a hook callback | `IN01`, `IN03` |
| wiring-watched | A test module treated as a receiver or hook source | `RD03` (clean) |
| wiring-watched | A patch target resolved to its origin | `PS10` (patched where defined) |
| wiring-watched | A drives location reported as its `__init__.py` | `DR01` |
| wiring-watched | A product module the request path reaches, unwatched | `WW01` |
| request-path | Core's request code reaches product modules by module-level imports, past the first one | `WW01`, `WW03` |
| request-path | The same through an import inside a function | `WW02` (not followed) |
| request-path | The same through a lazy hand-out of a facade | `WW10` (not followed) |
| request-path | A list splatted into `MIDDLEWARE`, an `insert` from a second settings module | `WW04`, `WW05` |
| request-path | A request key the settings reader cannot name | `WW06` (incomplete) |
| request-path | A settings list built from another name, a list a function returns, a key no list names | `WW11`, `WW12`, `WW13` |
| request-path | A key on the exclude list | `WW14` (not followed) |
| request-path | An unresolved import on the request path | `WW08` (incomplete) |
| config:stale-name | A seed path that matches no file | `WW07` |
| config:stale-name | A `reference_names` entry that names nothing or a re-export; one that resolves with no use | `NS18`, `NS19`; `NS17` (clean) |
| wiring-watched | A location without `__init__.py`, one file unwatched, one watched; all watched | `DR02`; `DR04` (clean) |
| reached-watched | Settings, registrations, core code, a migration, a core test and a tool reach unwatched product files | `LE02` |
| reached-watched | Another product reaches an unwatched product file | `LE12` |
| reached-watched | A watched file, reached by core and by another product | `LE03`, `LE04` (clean) |
| has-facade | A contracts package; an api module that only re-exports; no contracts module or no facade | `LE07` (clean), `LE08`, `LE09` |
| foundation-direction | A foundation model points at a feature product | None: `FO01` shows the import form |
| infrastructure-direction | Infrastructure imports a product | `IF01` |
| no-product-lists-in-core | Registration points that list products | `PS24`, `PS13`, `DP07` |
| no-product-lists-in-core | A core module that lists products and is no marked registration point | `PS26`: clean (the written limit) |
| no-product-lists-in-core | Core imports a product facade | `PS05`, `WW01`, `WW02`, `IF01`: clean (D93) |
| no-product-lists-in-core | Core imports product models, internals or wiring | `PS13` (a registration point); `PS01`, `PS25`: clean (public-surface's) |
| no-product-lists-in-core | Migrations | `PS12`: clean (frozen history) |
| no-product-lists-in-core | Settings | `PS11`: clean (settings may name product paths) |
| no-product-lists-in-core | Sanctioned exports used from non-composition core | None |
| model-crossings | Core uses a product model | `PS25`: past the export, public-surface's (A5) |
| model-crossings | A product uses another product's model | `MC01`: past the export, public-surface's (A5) |
| model-crossings | A model handed out by a facade re-export, then used | `PS08` |
| model-crossings | Core visitors return product model classes | None: `IN02` shows the registry form |
| model-crossings | A chain through `values()` then more | None |
| model-crossings | `sender=Model` on a receiver | `RD01`, `RD02` |
| model-crossings | `typing.cast(Model, x)`, a type alias union | None |
| model-crossings | The team-extension registry call | None |
| get-model | Inside migrations | `GM04` |
| get-model | Calls named `get_model` that are not the app registry | None |
| get-model | A variable label | `GM03` |
| get-model | A resolved cross-product label | `GM01` |
| reverse-accessor | Product to core, product to product, core to product | `RA02`, `RA01`, `RA03` |
| reverse-accessor | A relation onto a third-party app | None |
| receiver-direction | Core listens to a product's saves | None: same edge as `RD01` from core |
| receiver-direction | A product listens to another product's saves | `RD01`, `RD02` |
| receiver-direction | Signals with no sender; a variable sender | no sender: `RD05` to `RD07`; a call's result: `RD04` (incomplete); a variable sender: `WW22` |

## Adding a case

1. Write down the properties the case must have and the verdict each rule should give, then write the smallest invented code that has them.
   Never copy code from a real repository; use a path or dotted name from the preset only where the preset spells it out.
2. Add a clean neighbor: code that looks close and must not be a finding.
3. Add a row to the table above.
4. Run `bin/model-repo --refresh`, read the diff of `expected.toml`, and replace each `"?"` with the case id.
   A row you cannot tie to a case is a bug in the tree or a missed case.
5. A verdict that is decided but not given yet goes under `[[wanted]]` with its decision.
