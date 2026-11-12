# The model repository: cases

`repo/` is a miniature of the PostHog monorepo's layout, checked with the real preset (`presets/posthog.toml`) in both engines by `test_model_repo.py` and `bin/model-repo`.
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

## Cases

| Case | Files | Property | Verdict here | Why, and the decision |
|---|---|---|---|---|
| `PS01` | `posthog/api/widget_report.py` | Core imports a product's logic module and a function from it | public-surface finding (both imports); no-product-lists-in-core finding | Past the facade |
| `PS02` | `products/legacy/backend/logic/widgets.py` | A later use of an imported model name, three times | public-surface finding on the import only | A use is judged at its import (`uses_at_import`) |
| `PS03` | `posthog/api/widget_report.py` | A use through a module attribute (`module.CONSTANT`) | public-surface finding (reference) | The module import does not name the constant, so the use is judged on its own |
| `PS04` | `posthog/api/widget_report.py` | Later calls of an imported function | clean: a call makes no reference row | |
| `PS05` | `posthog/api/sharing.py`, `ee/hogai/tools.py` | Core imports a product's facade | public-surface clean; no-product-lists-in-core finding | Open: whether one facade import stays out of the core rule (D104) |
| `PS06` | `products/reports/backend/logic/build.py`, `products/legacy/backend/presentation/views.py`, `services/sync/worker.py`, `tools/loose.py` | A product, a service and a tool import a facade | clean | The facade is the public surface |
| `PS07` | `products/reports/backend/logic/build.py` | A product imports another product's logic | public-surface finding | Past the facade |
| `PS08` | `products/catalog/backend/facade/models.py`, `products/reports/backend/logic/build.py` | A model handed out by a facade re-export, then queried | public-surface clean; model-crossings finding (judged at the origin) | |
| `PS09` | `posthog/test/test_auth.py`, `products/legacy/backend/tests/test_widgets.py` | A test builds another product's model as a fixture | public-surface finding, one row per module and model; model-crossings clean (test) | D93 |
| `PS10` | `posthog/test/test_auth.py`, `products/legacy/backend/tests/test_widgets.py` | A test patches another product's internals by dotted path | public-surface clean; wiring-watched finding for the unwatched target, clean for the watched one | The patch target is wiring-watched's (Nov 12) |
| `PS11` | `posthog/settings/web.py` | Settings name product apps, a middleware and a logger key | public-surface clean; no-product-lists-in-core finding per product module | Settings may name product paths (Nov 12); the core rule is unchanged (D104) |
| `PS12` | `posthog/migrations/0002_entry_table.py` | A core migration imports product code | public-surface clean; no-product-lists-in-core finding | Migrations are out of public-surface (Nov 12) |
| `PS13` | `posthog/management/commands/start_temporal_worker.py`, `posthog/hogql_queries/query_runner.py` | A registration point names a product's wiring location | public-surface clean; no-product-lists-in-core finding | The wiring channel (Nov 12) |
| `PS14` | `products/batch_exports/backend/sql.py`, `posthog/clickhouse/schema.py`, `posthog/clickhouse/test/test_schema.py`, `products/reports/backend/logic/build.py`, `products/catalog/backend/sql.py` | A declared permanent interface used by core production code, by a core test and by another product; next to it an undeclared one | public-surface clean for core production code, finding for the test and the product; the undeclared one a finding everywhere | Permanent interfaces for core production code only (D99 note) |
| `PS15` | `posthog/api/__init__.py`, `posthog/urls.py` | Composition names a viewset outside `routes.py`; next to it the registration export and an `include` of it | public-surface and no-product-lists-in-core findings for the viewset; clean for `routes.py` | |
| `PS17` | `products/reports/backend/presentation/views.py`, `products/legacy/backend/presentation/views.py`, `products/catalog/backend/presentation/views.py` | Presentation imports another product's viewset that subclasses the mixin; next to it one that does not | clean for the subclass; public-surface finding for the other | The adapter export grants by base class (Nov 9); known gap |
| `PS18` | `products/legacy/backend/presentation/views.py` | Presentation imports another product's serializer | public-surface finding (see `PF05`) | |
| `PS19` | `products/legacy/backend/facade/contracts.py` | A contract imports another product's internal module | public-surface finding (see `CN01`) | |
| `PS20` | `products/catalog/backend/facade/shapes.py` | A facade module imports another product's model for its types | public-surface finding | |
| `PS21` | `tools/codegen/generate.py`, `bin/build_types.py`, `livestream/bot.py`, `manage.py` | Code in tool packages (a `tools/` folder, `bin`, `livestream`, the root) names product internals | public-surface finding | Every first-party file has a package; tier `tool` (D105) |
| `PS23` | `tools/codegen/emit.py`, `livestream/bot.py`, `posthog/schema.py` | Two files of one tool package; a third-party import | clean | |
| `PS24` | `posthog/tasks/scheduled.py` | A core registration point names several products' tasks | public-surface clean; no-product-lists-in-core finding per product | D104: the hub rule stays |
| `PS25` | `ee/api/widgets.py` | `ee` queries a product's model | public-surface, no-product-lists-in-core and model-crossings findings | |
| `PS26` | `posthog/models/resource_transfer/visitors.py` | A core file that names three products' models in a table | public-surface, no-product-lists-in-core and model-crossings (`in-collection`) findings, one per product | D104, D100 |
| `SY01` | `products/legacy/backend/api/widgets.py` | A viewset under `backend/api/` uses an access-control mixin | clean | The mixins are exported by name to zone `api` (exports `symbols`, Nov 12) |
| `SY02` | `products/legacy/backend/api/widgets.py` | The same module imports another product's viewset that inherits the mixin | public-surface finding | By name: a subclass gets no door |
| `ES01` | `products/catalog/backend/facade/models.py` | A facade module that only re-exports a model | export-shape finding | |
| `ES02` | `products/catalog/backend/facade/api.py` | A model, queryset and manager in facade signatures | clean for export-shape (see `FS01`-`FS03`) | One rule per slot (`slots = "shape"`) |
| `ES03` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py` | The five core models in signatures and fields | clean | `crosses` (D98) |
| `ES06` | `products/inventory/backend/facade/contracts.py`, `products/catalog/backend/facade/shapes.py` | An exception handed out; one with a class attribute typed as another product's model; a Protocol with such an attribute | clean for all | Exceptions and Protocols are allowed (Nov 12); an annotated attribute is handed out like a field and judged (Nov 12; wanted, with `ME01`) |
| `ES07` | `products/catalog/backend/facade/shapes.py` | A pydantic model; one with a field typed as another product's model | clean; facade-signature finding for the field | pydantic fields are slots (`dataclass_bases`); allowed shapes are walked (D63) |
| `ES08` | `products/catalog/backend/facade/shapes.py` | A `Protocol`; one whose call takes another product's model | clean; facade-signature finding for the call (`FS08`) | D93 |
| `ES09` | `products/catalog/backend/facade/shapes.py` | A mutable dataclass | export-shape finding | |
| `ES10` | `products/inventory/backend/facade/contracts.py`, `products/inventory/backend/facade/enums.py`, `products/catalog/backend/facade/contracts.py`, `products/catalog/backend/facade/shapes.py` | Frozen dataclasses (house decorator and stdlib), a str enum, a frozen class with a list field | clean | |
| `ES11` | `products/legacy/backend/facade/backfill_command.py` | A management command class defined in a facade | export-shape finding | |
| `ES12` | `products/catalog/backend/facade/testing.py` | `facade/testing.py` returns a model | clean | The fixture surface (D93) |
| `CM01` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py`, `products/catalog/backend/management/commands/import_entries.py` | A command class as a parameter and a return of a facade function, and as a field of a handed-out dataclass | facade-signature finding, one each (three) | `facade-signature` denies `command` (507d1b6); the field is that rule's slot too |
| `FS01` | `products/catalog/backend/facade/api.py` | A facade function returns a model | facade-signature finding | |
| `FS02` | `products/catalog/backend/facade/api.py` | A facade function returns a queryset | facade-signature finding (queryset and model) | |
| `FS03` | `products/catalog/backend/facade/api.py` | A facade function returns a manager | facade-signature finding | |
| `FS04` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py`, `posthog/models/team/team.py` | Each of the five core models in a parameter, a return and a field | clean | D98 |
| `FS05` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py` | `Integration` in a parameter, a return and a field | facade-signature finding (three) | D98, D104 |
| `FS06` | `products/catalog/backend/facade/api.py` | `object`, `Any`, bare `dict` as parameters; `dict[str, Any]` as a return | facade-signature unknown (four: the return reports its `Any`) | D93 |
| `FS07` | `products/catalog/backend/facade/api.py` | A parameter with no annotation | facade-signature unknown | |
| `FS08` | `products/catalog/backend/facade/shapes.py` | A `Protocol` call that takes another product's model | facade-signature finding | |
| `FS10` | `products/legacy/backend/facade/backfill_command.py` | A method of a class in a facade with unannotated parameters | facade-signature unknown (two) | |
| `FS12` | `products/inventory/backend/facade/api.py`, `products/gatekeeper/backend/facade/api.py` | Facade functions typed with scalars and contracts | clean | |
| `TM01` | `products/catalog/backend/facade/api.py` | Typed mappings: `dict[str, str]`, `dict[str, Any]`, `Mapping[str, Any]`, `dict[str, Team]` parameters; `dict[str, Any] \| list[Any]` and a `list[Any]` return | clean for the mappings; facade-signature unknown for the `Any` of the list (two) | `argument_of`, typed mappings (D93; Nov 12) |
| `TM02` | `products/catalog/backend/facade/api.py` | A bare `list` parameter | clean | Open since Nov 12; wanted as unknown |
| `CR01` | `products/catalog/backend/facade/shapes.py` | A core model on the list in a field (`EntryOwners`) | clean | `crosses` on a symbol assignment |
| `CR02` | `products/catalog/backend/facade/shapes.py` | Nested fields: `list[Team]` and `list[Integration]` | clean for `Team`, facade-signature finding for `Integration` | `crosses` read at any depth (D98) |
| `CR03` | `products/catalog/backend/facade/api.py` | A parameter typed with a name that does not resolve (`Ghost`) | facade-signature unknown; the unresolved import is also an unknown row in facade-no-drf, prod-imports-test and the cycle rule | `crosses` cannot be told: never clean |
| `FL01` | `products/inventory/backend/facade/api.py`, `products/access_control/backend/facade/api.py`, `products/notifications/backend/facade/api.py`, `products/reports/backend/facade/api.py`, `products/legacy/backend/facade/api.py` | Thin facade functions and a mapper in `api.py` | clean | D94; only capability submodules (Nov 12) |
| `FL02` | `products/catalog/backend/facade/api.py` | `api.py` holds a function that queries the ORM and a large one without a query | clean | The query is `FQ01`; the large function has no rule (Nov 12) |
| `FL03` | `products/legacy/backend/facade/queries.py` | A capability submodule (by name) with a body | facade-logic finding, named per definition | D94 |
| `FL04` | `products/inventory/backend/facade/tasks.py`, `products/catalog/backend/facade/models.py` | A capability submodule that only re-exports | clean | |
| `FL05` | `products/inventory/backend/facade/hooks.py`, `products/catalog/backend/facade/testing.py`, `products/legacy/backend/facade/backfill_command.py` | Bodies in other facade modules (a hook registration, a fixture, a class) | clean | |
| `HZ01` | `products/legacy/backend/facade/workers.py`, `products/legacy/backend/temporal/jobs.py` | A facade module that hands out a name from a wiring location and has a body | facade-logic finding | A capability by what it hands out (`handed_zones`) |
| `HZ02` | `products/legacy/backend/facade/labels.py` | A facade module that hands out a name from logic and has a body | clean | |
| `HZ03` | `products/legacy/backend/facade/nsworkers.py` | A facade module that hands out a namespace directory under a wiring location and has a body | facade-logic finding | The directory's zone is read (Nov 12 handed_zones fix) |
| `HZ04` | `products/legacy/backend/facade/lazyworkers.py` | A facade module with a body that hands out wiring lazily (`_LAZY`, `__getattr__`) | facade-logic finding | A lazy name has an import edge that does not run on import (`function_local`, attr `lazy`) |
| `HZ05` | `products/legacy/backend/facade/aliasworkers.py` | A facade module with a body that hands out `import a.b as name` from wiring, listed in `__all__` | facade-logic finding | `import a.b as name` hands out the module when `__all__` lists the name |
| `HZ06` | `products/legacy/backend/facade/aboveworkers.py` | A facade module with a body and a relative import above the top package in `__all__` | facade-logic incomplete; export-shape unknown; unknown rows in public-surface, facade-no-drf, prod-imports-test and the cycle rule | The import keeps the name it binds, so the hand-out is unresolved and `handed_zones` unknown |
| `HZ07` | `products/legacy/backend/facade/lazylabels.py` | A facade module with a body that hands out a name from its product's logic lazily | clean | Neighbor of `HZ04` |
| `HZ08` | `products/legacy/backend/facade/aliaslabels.py` | A facade module with a body that imports wiring as `import a.b as name` and does not list the name in `__all__` | clean | Neighbor of `HZ05` |
| `HZ09` | `products/legacy/backend/facade/relativelabels.py` | A facade module with a body that hands out a name from logic by a relative import that resolves | clean | Neighbor of `HZ06` |
| `FQ01` | `products/catalog/backend/facade/api.py` | A facade function runs an ORM query itself (`count_entries`) | clean | D95, D96 (wanted) |
| `FQ02` | `products/inventory/backend/facade/api.py` | A facade function maps a list that its logic returned | clean | D96 |
| `FD01` | `products/legacy/backend/facade/api.py` | A facade imports `django.http` | facade-no-drf finding | |
| `FD02` | `products/legacy/backend/facade/api.py` | A facade imports a DRF exception | facade-no-drf finding | |
| `FD03` | `products/legacy/backend/facade/api.py` | A facade imports its own presentation | facade-no-drf finding | |
| `FD04` | `products/catalog/backend/facade/api.py` | A facade lazily re-exports a DRF serializer | export-shape finding; facade-no-drf clean | The lazy edge reaches the rule, but the serializer sits in zone `api`, which the rule does not deny; an eager import of it is clean too. Open: a preset question (wanted) |
| `FD05` | `products/inventory/backend/facade/api.py`, `products/catalog/backend/facade/testing.py` | A facade that raises its own errors; `facade/testing.py` that imports DRF | clean | `facade-code` leaves fixtures out |
| `ME01` | `products/catalog/backend/logic/errors.py`, `products/catalog/backend/facade/errors.py` | Methods that return or take another product's model on a handed-out exception, pydantic model and Protocol defined outside the facade | public-surface finding for the model import; the methods clean | Narrow form decided, not built (wanted) |
| `WK01` | `products/catalog/backend/logic/views_out.py`, `products/catalog/backend/facade/handout.py` | A pydantic class defined in the product's logic, handed out, with a field typed as another product's model | facade-signature finding at the facade | Fields of a handed-out class count where it is defined (D63) |
| `WK02` | `tools/viewkit/shapes.py`, `products/catalog/backend/facade/handout.py` | The same class defined in a tool package | facade-signature finding | `walk_where` true for tier `tool` |
| `WK03` | `sandbox/shapes.py`, `products/catalog/backend/facade/handout.py` | The same class in a first-party top folder no assignment claims (tier unknown) | facade-signature finding (walked); incomplete rows for every edge of the folder | `walk_where` unknown walks (Nov 12 review) |
| `WK04` | `scripts/shapes.py`, `products/catalog/backend/facade/handout.py` | The same class in a top folder outside `[discovery] first_party` | incomplete rows for the folder's edges and the facade's import; the field is not walked | Defect: read as third-party with no facts (wanted) |
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
| `TC01` | `products/legacy/backend/management/commands/seed_widgets.py` | A dev seeder that is not on the named list | thin-commands finding | Seeders by name only (D104) |
| `TC02` | `products/legacy/backend/management/commands/backfill_widget_names.py` | A one-off backfill imports logic | thin-commands finding | D93, D104 |
| `TC03` | `products/legacy/backend/management/commands/sync_widgets.py` | An ordinary command imports its product's tasks | thin-commands finding | |
| `TC04` | `products/inventory/backend/management/commands/show_stock.py` | A command that goes through its facade | clean | |
| `TC05` | `products/visual_review/backend/management/commands/seed_dummy_runs.py` | A dev seeder on the named list writes through its models | clean | Named as test code (D104) |
| `AT01` | `products/legacy/backend/presentation/views.py` | Presentation opens a transaction (`with` and decorator) | clean: no reference edge for a call, `with` or decorator | The rule cannot fire (wanted) |
| `AT02` | `products/inventory/backend/logic/stock.py`, `products/legacy/backend/api/widgets.py` | A transaction outside presentation | clean | |
| `PT01` | `products/legacy/backend/tasks/cleanup.py` | A production task imports a test factory | prod-imports-test finding | |
| `PT02` | `products/catalog/backend/facade/testing.py` | `facade/testing.py` imports the product's test factory | clean | Test support (Nov 12) |
| `PT03` | `products/legacy/backend/tests/test_widgets.py` | A test imports a test factory | clean | |
| `MC01` | `products/legacy/backend/logic/widgets.py` | A product queries another product's model; next to it an annotation, `DoesNotExist`, `cast`, a `values()` chain and the team-extension registry | model-crossings finding for the query; clean for the others | The use shapes of Nov 12 |
| `MC02` | `posthog/test/test_auth.py`, `products/legacy/backend/migrations/0001_initial.py` | A crossing in a test and in a migration | clean | |
| `GM01` | `products/legacy/backend/logic/widgets.py` | `apps.get_model` names another product's model | get-model finding | |
| `GM02` | `products/legacy/backend/logic/widgets.py` | `apps.get_model` names its own model | clean | |
| `GM03` | `products/legacy/backend/logic/widgets.py` | `apps.get_model` with a variable label | get-model unknown; a cycle unknown onto `?` (`CY03`) | Open: unknown finding or incomplete |
| `GM04` | `products/legacy/backend/migrations/0001_initial.py` | `apps.get_model` in a migration | clean | |
| `RA01` | `products/legacy/backend/models.py` | An unsealed relation into another product's model | reverse-accessor finding | |
| `RA02` | `products/legacy/backend/models.py` | An unsealed relation into a core model | reverse-accessor finding | |
| `RA03` | `posthog/models/sharing.py` | A core model with an unsealed relation into a product | reverse-accessor finding | |
| `RA04` | `products/inventory/backend/models.py`, `products/reports/backend/models.py`, `posthog/models/organization.py` | Sealed relations and core-to-core relations | clean | |
| `RD01` | `products/legacy/backend/receivers.py` | A receiver on another product's sender, in a watched module | receiver-direction finding; public-surface finding for the import; model-crossings clean (`sender`); wiring-watched clean | Cross-check of Nov 12; Nov 12 |
| `RD02` | `products/legacy/backend/stock_listeners.py` | The same in an unwatched module | the same, and a wiring-watched finding | |
| `RD03` | `products/inventory/backend/logic/stock.py` | A receiver on its own product's sender | clean | |
| `IN01` | `products/legacy/backend/apps.py`, `products/legacy/backend/hooks.py` | A product registers a function into a core hook from an unwatched module | installs clean (a list); wiring-watched finding | `installs` is a list (Nov 12) |
| `IN02` | `products/legacy/backend/apps.py` | A product registers a model class into a core registry | clean beyond `IN01` | D100 (wanted) |
| `IN03` | `products/reports/backend/apps.py`, `products/inventory/backend/facade/hooks.py` | A product registers into another product's facade hook | wiring-watched finding | |
| `IN04` | `products/inventory/backend/apps.py` | A product registers into its own hook | clean | |
| `UI01` | `products/gatekeeper/backend/wiring.py` | An install whose hook cannot be resolved, in an unwatched module of a narrowed product | installs incomplete; wiring-watched incomplete; the product is not Isolated | `wiring-watched` is strict for the level |
| `UI02` | `products/notifications/backend/wiring.py` | The same in a product with no contract-check task | the same; the product is not Isolated | |
| `DR01` | `posthog/test/test_widget_query.py`, `products/catalog/backend/hogql_queries/entries.py` | A core test runs a product's query kind | drives finding; wiring-watched finding for the location's `__init__.py` | |
| `DR02` | `posthog/test/test_widget_query.py`, `products/reports/backend/hogql_queries/widget.py` | The same into a location without `__init__.py`, unwatched | drives finding; wiring-watched clean | Defect: nothing asks for the location (wanted) |
| `DR03` | `products/reports/backend/tests/test_widget_query.py` | A product's own test runs its kind | clean | |
| `WW01` | `posthog/auth.py`, `products/gatekeeper/backend/facade/api.py`, `products/gatekeeper/backend/logic/decisions.py` | Core's request code imports a facade at module level; the logic behind it is unwatched | no-product-lists-in-core finding; wiring-watched clean | D103 (wanted) |
| `WW02` | `posthog/permissions.py`, `products/access_control/backend/logic/rules.py` | The same through an import inside a function | no-product-lists-in-core finding; wiring-watched clean | A written limit of D103 |
| `WW03` | `posthog/settings/web.py`, `products/gatekeeper/backend/middleware.py`, `products/gatekeeper/backend/logic/session.py` | Settings wire a product middleware whose logic is watched | clean for wiring-watched | D103 |
| `UK01` | `scripts/new_tool.py` | A file in a top folder no assignment names imports product internals (the source end unknown) | incomplete in every rule that crosses packages; product-code-at-home incomplete | D105: a new top folder reads unknown |
| `UK02` | `products/legacy/backend/logic/widgets.py`, `scripts/helpers.py` | A product imports from such a folder (the destination end unknown) | public-surface and cycle incomplete | D105 |
| `DI01` | `products/legacy/backend/logic/widgets.py` | A computed import outside a registry loader | dynamic-imports incomplete | |
| `DI02` | `posthog/products.py` | The registry loader's computed import | clean | |
| `CY01` | `products/catalog/backend/facade/api.py`, `products/reports/backend/logic/build.py` | Two products that depend on each other | no-new-package-cycles finding, one per direction | |
| `CY02` | `products/reports/backend/logic/build.py`, `products/catalog/backend/facade/shapes.py` | One-way dependencies | clean | |
| `CY03` | `products/legacy/backend/logic/widgets.py`, `products/legacy/backend/facade/aboveworkers.py` | An unresolved model label; an unresolved relative import | no-new-package-cycles unknown onto `?` (one identity, two rows) | Open (see `GM03`) |
| `FO01` | `products/access_control/backend/logic/rules.py` | A foundation product imports a feature product's facade | foundation-direction finding | |
| `FO02` | `products/access_control/backend/logic/rules.py` | A foundation product imports another foundation product | clean | |
| `IF01` | `posthog/egress/client.py` | Infrastructure imports a product's facade | infrastructure-direction and no-product-lists-in-core findings | |
| `IF02` | `posthog/kafka_client/client.py`, `ee/billing/quota.py` | Infrastructure imports a proto-product | infrastructure-direction finding | |
| `IF03` | `posthog/clickhouse/client/execute.py` | Infrastructure imports `common/` | clean | |
| `LV01` | `packages/owners_yaml/django_glue.py` | A leaf imports Django and core | leaves-stay-leaves finding (two) | |
| `LV02` | `packages/owners_yaml/parse.py` | A leaf with stdlib imports | clean | |
| `OI01` | `posthog/egress/client.py` | A framework package takes a model | orm-independent finding | |
| `OI02` | `posthog/egress/client.py` | A framework package takes `**kwargs: Any` | orm-independent incomplete | Loose types are incomplete here (Nov 12, from D93's noise list) |
| `OI03` | `posthog/egress/client.py` | A framework package with typed parameters | clean | |
| `MD01` | `products/legacy/backend/migrations/0001_initial.py`, `products/db_routing.yaml` | A migration on its own database depends on a default-database migration | migration-database finding | |
| `MD02` | `products/inventory/backend/migrations/0001_initial.py` | Both on the default database | clean | |
| `OD01` | `products/inventory/backend/models.py` | Every product model | own-database incomplete | No producer sets `database` |
| `PH01` | `posthog/temporal/ai_observability/summarize.py` | Product code under `posthog/` | product-code-at-home finding, one per file | |
| `PH02` | `posthog/temporal/common/worker.py` | Core code next to it | clean | |
| `RS01` | `products/inventory/backend/health_checks.py`, `products/catalog/backend/webhook_consumers.py` | Well-formed registry slot files | registry-slots incomplete | No producer records module-level assignments |
| `RS02` | `products/legacy/backend/health_checks.py` | A slot file without its attribute | registry-slots incomplete | Wanted as a finding |
| `RS03` | `products/legacy/backend/webhook_consumers.py` | A slot member that does not subclass the base | registry-slots incomplete | Wanted as a finding |
| `GP01` | `posthog/schema.py`, `posthog/schema_enums.py` | Generated files | generated-producer incomplete | No producer sets `generated` |
| `HD01` | `products/reports/backend/health_checks.py` | A health check imports `posthog.dags` | health-checks-no-dags finding (advisory) | |
| `HD02` | `products/inventory/backend/health_checks.py` | A health check without it | clean | |
| `DO01` | `products/legacy/backend/logic/widgets.py`, `posthog/ingress/dispatch.py` | A product imports a core package past its door | clean | D107 (wanted) |
| `DO02` | `products/catalog/backend/webhook_consumers.py`, `posthog/ingress/contracts.py` | A product imports the door | clean | |
| `LE01` | `posthog/temporal/ai_observability/summarize.py` | The Isolated level | `ai_observability` passes: no rule of the level reports product code in core; `visual_review` passes; every other product fails | Open |

## Unknown inputs of the rule language's abilities

Each ability has a case where its input cannot be told; the verdict there must never be clean.

| Ability | Unknown input | Case | Verdict here |
|---|---|---|---|
| `crosses-package` and the tier `tool` | A file in an unassigned top folder at the source end, at the destination end | `UK01`, `UK02` | incomplete |
| `walk_where` | A handed-out class whose tier is unknown | `WK03` | walked: finding |
| `walk_where` | A handed-out class from a top folder outside `first_party` | `WK04` | not walked; only incomplete rows on its edges (defect, wanted) |
| `handed_zones` | A handed name that is a namespace directory | `HZ03` | finding |
| `handed_zones` | A lazy hand-out; `import a.b as name`; a relative import above the top package | `HZ04`, `HZ05`, `HZ06` | clean for facade-logic (defects, wanted) |
| `dst.zone` | A directory two source roots place apart | none | The tree has one source root; a second would change every other case's module names |
| `crosses` | A slot typed with a name that does not resolve | `CR03` | unknown |
| `installs` as a list | A hook call that does not resolve | `UI01`, `UI02` | incomplete; the product is not Isolated |
| Namespace-directory labels | A drives location without `__init__.py` | `DR02` | clean for wiring-watched (defect, wanted) |
| `uses_at_import` | A use whose import the rule does not report | `PS03` | the use is a finding of its own; with this preset no import is reported clean while its use is not, so no stronger case can be built (`tests/native/test_evaluator.py` holds it) |
| `slots = "shape"` | A module the signature rule cannot tell about | none | A file's zone is never unknown in this preset (the zone field has no domain); `tests/native/test_facade_rules.py` holds it |
| Namespace-directory labels | A directory whose files disagree on zone | none | Zones come from globs only here, so a directory reads one answer; a provider-set zone would be needed |

## Kinds from the findings triage

Every kind the triage of the rules found on the monorepo, and the case that shows it.
"None" means no case shows it yet, with the reason.

| Rule | Kind | Case |
|---|---|---|
| public-surface | A later use of a name already imported | `PS02` |
| public-surface | A use through a module attribute whose module import is a finding | `PS03` |
| public-surface | An unresolved string read as a dotted path (host names, task names, fixtures) | None: the dotted-path producer now reads only path sinks (`path_callees`) |
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
| export-shape | A behavior class handed out | None |
| export-shape | A mutable data shape | `ES09` |
| export-shape | A management command in a facade | `ES11` |
| facade-signature | The core model list | `FS04` |
| facade-signature | `dict[str, Any]` as a parameter or field | `FS06` |
| facade-signature | A typed map of `object` | None |
| facade-signature | Bare `object` | `FS06` |
| facade-signature | The `frozen` decorator read as a facade function | None: no longer reported (`ES10`) |
| facade-signature | A function left out of `__all__` | None |
| facade-signature | A type alias read as unknown | None |
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
| facade-no-drf | An unresolved type-only import routed here | None |
| presentation-uses-facade | An import through a facade re-export | `PF01` (clean on the facade itself) |
| presentation-uses-facade | The same whose origin is a model | None |
| presentation-uses-facade | `routes.py` imports viewsets from `backend/api/` | `PF03` |
| presentation-uses-facade | Presentation imports its own models or logic | `PF02` |
| presentation-uses-facade | Presentation imports another product's presentation | `PF05` |
| presentation-uses-facade | Presentation imports another product's models | None: same shape as `PF02` across products |
| no-new-package-cycles | An unresolved label onto `?` | `CY03` |
| no-new-package-cycles | A pair whose edges go through a facade | `CY01` (one direction) |
| no-new-package-cycles | A pair with non-facade edges | `CY01` (other direction) |
| drives | A test outside the product runs its query kind | `DR01`, `DR02` |
| drives | A test imports a runner by name | None |
| thin-commands | An import through the product's own facade | `TC04` |
| thin-commands | Dev seeders | `TC01` |
| thin-commands | One-off backfills | `TC02` |
| thin-commands | Operational commands | `TC03` |
| product-code-at-home | Product code under `posthog/temporal/` | `PH01` |
| prod-imports-test | A string literal read as a dotted path | None: see public-surface |
| prod-imports-test | A stale import | None |
| prod-imports-test | A sanctioned test-support module imports test factories | `PT02` |
| prod-imports-test | Dev scripts import test code | None |
| prod-imports-test | A production module named `test_*.py` | None |
| prod-imports-test | Shipped code imports test helpers | `PT01` |
| installs | A product registers a callback into a core or facade hook | `IN01`, `IN03` |
| installs | The hook lives in another product's internals | None |
| installs | An install made from a test | None |
| installs | A hook call that does not resolve | None |
| wiring-watched | An unwatched module that installs a hook callback | `IN01`, `IN03` |
| wiring-watched | A test module treated as a hook source | None |
| wiring-watched | A patch target resolved to its origin | `PS10` (patched where defined) |
| wiring-watched | A drives location reported as its `__init__.py` | `DR01` |
| foundation-direction | A foundation model points at a feature product | None: `FO01` shows the import form |
| infrastructure-direction | Infrastructure imports a product | `IF01` |
| no-product-lists-in-core | Hub files that list products | `PS24` |
| no-product-lists-in-core | Core imports a product facade | `PS05`, `WW01`, `WW02`, `IF01` |
| no-product-lists-in-core | Core imports product models, internals or wiring | `PS01`, `PS13`, `PS25` |
| no-product-lists-in-core | Migrations | `PS12` |
| no-product-lists-in-core | Settings | `PS11` |
| no-product-lists-in-core | Sanctioned exports used from non-composition core | None |
| model-crossings | Core uses a product model | `PS25` |
| model-crossings | A product uses another product's model | `MC01` |
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
| receiver-direction | Signals with no sender; a variable sender | None |

## Adding a case

1. Write down the properties the case must have and the verdict each rule should give, then write the smallest invented code that has them.
   Never copy code from a real repository; use a path or dotted name from the preset only where the preset spells it out.
2. Add a clean neighbor: code that looks close and must not be a finding.
3. Add a row to the table above.
4. Run `bin/model-repo --refresh`, read the diff of `expected.toml`, and replace each `"?"` with the case id.
   A row you cannot tie to a case is a bug in the tree or a missed case.
5. A verdict that is decided but not given yet goes under `[[wanted]]` with its decision.
