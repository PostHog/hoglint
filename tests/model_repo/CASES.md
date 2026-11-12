# The model repository: cases

`repo/` is a miniature of the PostHog monorepo's layout, checked with the real preset (`presets/posthog.toml`) in both engines by `test_model_repo.py` and `bin/model-repo`.
Every name and body in it is invented; only the paths and dotted names that the preset spells out are real.
`site/` holds stub sources of `django` and `rest_framework`, read as site-packages.

Each case below is one code shape with the verdicts the rules give it on this preset.
A case that shows a finding has a clean neighbor next to it: code that looks close and must not be a finding.
`expected.toml` holds every finding and incomplete row, each tied to a case id; its `[[wanted]]` entries are verdicts that are decided and not given yet, run as expected failures.

Verdicts: **finding**, **clean** (no row), **incomplete**.
"Decided" names the verdict a decision asks for where it differs from what this preset gives today.

## The products and their roles

| Product | Role |
|---|---|
| `inventory` | The clean product: thin facade, frozen contracts, logic, presentation through its facade, a registry slot, a hook it fills itself. |
| `catalog` | Hands out models and loose types through its facade; the contract-shape cases. |
| `legacy` | Every kind of debt: views in `backend/api/`, presentation past its facade, receivers, hooks, commands, a test import in production code. |
| `reports` | Depends on `inventory` through its facade and on `catalog` past it; the drives and cycle cases. |
| `gatekeeper` | The product the request pipeline asks for decisions. |
| `access_control`, `notifications` | Foundation products; `access_control` holds the exported view mixin. |

## Cases

| Case | Files | Property | Rules and verdict here | Why | Decided |
|---|---|---|---|---|---|
| `PS01` | `posthog/api/widget_report.py` | Core imports a product's logic module and a function from it | public-surface finding (both imports); no-product-lists-in-core finding | Core reaches past the facade | |
| `PS02` | `products/legacy/backend/logic/widgets.py` | A later use of an imported model name, three times | public-surface finding: one import row and one reference row per use | Each use is a row of its own | Only the import row (decided Nov 12) |
| `PS03` | `posthog/api/widget_report.py` | A use through a module attribute (`module.CONSTANT`) | public-surface finding (reference) | The module import does not name the constant | Stays a finding |
| `PS04` | `posthog/api/widget_report.py` | Later calls of an imported function | clean: a call makes no reference row | Only the import is a row | |
| `PS05` | `posthog/api/sharing.py`, `ee/hogai/tools.py` | Core imports a product's facade | public-surface clean; no-product-lists-in-core finding | The facade is open to everyone; the core rule reports any product import | Open: whether a single facade import stays out of the core rule |
| `PS06` | `products/reports/backend/logic/build.py`, `products/legacy/backend/presentation/views.py` | A product imports another product's facade | clean | The facade is the public surface | |
| `PS07` | `products/reports/backend/logic/build.py` | A product imports another product's logic | public-surface finding | Past the facade | |
| `PS08` | `products/catalog/backend/facade/models.py`, `products/reports/backend/logic/build.py` | A model handed out by a facade re-export, then queried | public-surface clean; model-crossings finding (judged at the origin) | The import goes through the door; the use is still a query on another product's table | |
| `PS09` | `posthog/test/test_auth.py`, `products/legacy/backend/tests/test_widgets.py` | A test builds another product's model as a fixture | public-surface finding (import and reference); model-crossings clean (test context) | Tests seed through a facade or `facade/testing.py` | Stays a finding (D93), one row per module and model |
| `PS10` | `posthog/test/test_auth.py`, `products/legacy/backend/tests/test_widgets.py` | A test patches another product's internals by dotted path | public-surface finding; wiring-watched finding for the unwatched target, clean for the watched one | A patch target must be watched | public-surface clean (decided Nov 12); wiring-watched owns it |
| `PS11` | `posthog/settings/web.py` | Settings name product apps, a middleware and a logger key | public-surface and no-product-lists-in-core findings | Dotted paths in settings | Clean (decided Nov 12: settings are composition) |
| `PS12` | `posthog/migrations/0002_entry_table.py` | A core migration imports product code | public-surface and no-product-lists-in-core findings | Migrations are frozen history | public-surface clean (decided Nov 12) |
| `PS13` | `posthog/management/commands/start_worker.py`, `posthog/hogql_queries/query_runner.py` | A registration point names a product's wiring location | public-surface and no-product-lists-in-core findings | Workers and the query dispatcher list products | public-surface clean (decided Nov 12) |
| `PS14` | `products/catalog/backend/sql.py`, `posthog/clickhouse/schema.py`, `products/reports/backend/logic/build.py` | A permanent interface used by core and by another product | public-surface finding for both; no-product-lists-in-core finding for core | The interface is no export here | Core production use clean once the interface is declared (decided Nov 12); the other product's use stays a finding |
| `PS15` | `posthog/api/__init__.py`, `posthog/urls.py` | Composition names a viewset outside `routes.py`; next to it the registration export and an `include` of it | public-surface and no-product-lists-in-core findings for the viewset; clean for `routes.py` | Composition may name only the registration export | |
| `PS16` | `products/legacy/backend/api/widgets.py` | A viewset in legacy `backend/api/` uses the access-control mixin | public-surface finding | The mixin's audience is presentation and core | Clean (decided Nov 12: the five mixins by name) |
| `PS17` | `products/reports/backend/presentation/views.py`, `products/legacy/backend/presentation/views.py`, `products/catalog/backend/presentation/views.py` | Presentation imports another product's viewset that subclasses the mixin; next to it one that does not | clean for the subclass; public-surface finding for the other | The adapter export grants by base class | Known gap: every subclass gets a door |
| `PS18` | `products/legacy/backend/presentation/views.py` | Presentation imports another product's serializer | public-surface finding | Not an adapter | presentation-uses-facade finding too (D93) |
| `PS19` | `products/legacy/backend/facade/contracts.py` | A contract imports another product's internal module | public-surface finding (see `CN01`) | Past the facade | |
| `PS20` | `products/catalog/backend/facade/shapes.py` | A facade module imports another product's model for its types | public-surface finding (import and three references) | Past the facade | |
| `PS21` | `tools/codegen/generate.py`, `bin/build_types.py`, `livestream/bot.py` | First-party code outside the package roots names product internals | public-surface finding | The source has no package, which differs from the product | Same rows with tier `tool` (D105) |
| `PS22` | `services/sync/worker.py` | A service imports a facade | clean | | |
| `PS23` | `tools/codegen/emit.py`, `livestream/bot.py`, `posthog/schema.py` | Two ends with no package; a third-party import | clean | No package on either end; third-party code is no package | |
| `PS24` | `posthog/tasks/scheduled.py` | A core file names several products | public-surface and no-product-lists-in-core findings, one per product | Core is the product list | |
| `PS25` | `ee/api/widgets.py` | `ee` queries a product's model | public-surface, no-product-lists-in-core and model-crossings findings | Core uses product tables | |
| `ES01` | `products/catalog/backend/facade/models.py` | A facade module that only re-exports a model | export-shape finding | The drawer pattern | |
| `ES02` | `products/catalog/backend/facade/api.py` | A model, queryset and manager in facade signatures | export-shape finding (repeats facade-signature) | Shape checked through signatures | |
| `ES03` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py` | The five core models in signatures and fields | export-shape finding | Every model is denied | Clean (D98) |
| `ES04` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py` | Another core model (`Integration`) in a signature and a field | export-shape finding | Not on the list | Stays a finding |
| `ES05` | `products/catalog/backend/facade/api.py` | `dict`, `object`, `Any` and a missing annotation in signatures | export-shape finding (unknown) | Loose types promise nothing | `dict[str, Any]` in a parameter clean (D93); `object` stays unknown |
| `ES06` | `products/inventory/backend/facade/contracts.py`, `products/catalog/backend/facade/shapes.py` | An exception handed out; one with a field typed as another product's model | export-shape finding for both | An exception is not a contract shape here | The plain one clean, the one with the model a finding (wanted) |
| `ES07` | `products/catalog/backend/facade/shapes.py` | A pydantic model; one with a field typed as another product's model | export-shape finding for both | Same | Same (wanted) |
| `ES08` | `products/catalog/backend/facade/shapes.py` | A `Protocol`; one whose call takes another product's model | export-shape finding for both | Same | The plain one clean (D93) |
| `ES09` | `products/catalog/backend/facade/shapes.py` | A mutable dataclass | export-shape finding | Contracts are frozen | |
| `ES10` | `products/inventory/backend/facade/contracts.py`, `products/inventory/backend/facade/enums.py`, `products/catalog/backend/facade/contracts.py`, `products/catalog/backend/facade/shapes.py` | Frozen dataclasses (house decorator and stdlib), a str enum, a frozen class with a list field | clean | The contract shape | |
| `ES11` | `products/legacy/backend/facade/backfill_command.py` | A management command in a facade | export-shape finding | Commands are denied | |
| `ES12` | `products/catalog/backend/facade/testing.py` | `facade/testing.py` returns a model | export-shape finding | | Clean (D93: the fixture surface) |
| `FS01` | `products/catalog/backend/facade/api.py` | A facade function returns a model | facade-signature finding | | |
| `FS02` | `products/catalog/backend/facade/api.py` | A facade function returns a queryset | facade-signature finding (queryset and model) | | |
| `FS03` | `products/catalog/backend/facade/api.py` | A facade function returns a manager | facade-signature finding | | |
| `FS04` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py` | Each of the five core models in a parameter, a return and a field of a handed-out class | facade-signature finding | | Clean (D98) |
| `FS05` | `products/catalog/backend/facade/api.py`, `products/catalog/backend/facade/shapes.py` | `Integration` in a parameter, a return and a field | facade-signature finding | Not on the list | Stays a finding (D98, D104) |
| `FS06` | `products/catalog/backend/facade/api.py` | `object`, `Any`, bare `dict`, `dict[str, str]`, `dict[str, Any]` as a parameter and as a return | facade-signature finding (unknown) | No usable type | `dict[str, str]` and `dict[str, Any]` parameters clean (D93); `dict[str, Any]` return, `object`, `Any`, bare `dict` stay |
| `FS07` | `products/catalog/backend/facade/api.py` | A parameter with no annotation | facade-signature finding (unresolved) | Unknown is never clean | |
| `FS08` | `products/catalog/backend/facade/shapes.py` | A `Protocol` call that takes another product's model | facade-signature finding | | |
| `FS09` | `products/catalog/backend/facade/shapes.py` | A pydantic field and an exception attribute typed as another product's model | clean: neither is a slot | | A finding through export-shape (wanted under `ES06`, `ES07`) |
| `FS10` | `products/legacy/backend/facade/backfill_command.py` | A method of a class in a facade with unannotated parameters | facade-signature finding (unresolved) | | |
| `FS11` | `products/catalog/backend/facade/testing.py` | `facade/testing.py` returns a model | facade-signature finding | | Clean (D93) |
| `FS12` | `products/inventory/backend/facade/api.py`, `products/gatekeeper/backend/facade/api.py` | Facade functions typed with scalars and contracts | clean | | |
| `FL01` | `products/inventory/backend/facade/api.py`, `products/gatekeeper/backend/facade/api.py`, `products/access_control/backend/facade/api.py`, `products/notifications/backend/facade/api.py`, `products/reports/backend/facade/api.py`, `products/legacy/backend/facade/api.py` | Thin facade functions and a mapper in `api.py` | facade-logic finding, counted per module | Every body in a facade counts | Clean (D94; decided Nov 12: only capability submodules) |
| `FL02` | `products/catalog/backend/facade/api.py` | The same module holds a function that queries the ORM and a large one without a query | facade-logic finding, counted with the rest | | Clean for facade-logic; the query is `FQ01` |
| `FL03` | `products/legacy/backend/facade/queries.py` | A capability submodule with a body | facade-logic finding | Capability submodules re-export | Stays a finding |
| `FL04` | `products/inventory/backend/facade/tasks.py` | A capability submodule that only re-exports | clean | | |
| `FL05` | `products/inventory/backend/facade/hooks.py` | A hook registration function in a facade | facade-logic finding | | Clean (decided Nov 12) |
| `FL06` | `products/catalog/backend/facade/testing.py` | A fixture helper in `facade/testing.py` | facade-logic finding | | Clean (decided Nov 12) |
| `FL07` | `products/legacy/backend/facade/backfill_command.py` | A class method body in a facade module | facade-logic finding | | Clean for facade-logic (decided Nov 12); `ES11` stays |
| `FQ01` | `products/catalog/backend/facade/api.py` | A facade function runs an ORM query itself (`count_entries`) | no rule (only the facade-logic count) | | A finding (D95, D96; wanted) |
| `FQ02` | `products/inventory/backend/facade/api.py` | A facade function maps a list that its logic returned | clean | | Clean (D96) |
| `FD01` | `products/legacy/backend/facade/api.py` | A facade imports `django.http` | facade-no-drf finding | | |
| `FD02` | `products/legacy/backend/facade/api.py` | A facade imports a DRF exception | facade-no-drf finding | | |
| `FD03` | `products/legacy/backend/facade/api.py` | A facade imports its own presentation | facade-no-drf finding | | |
| `FD04` | `products/catalog/backend/facade/api.py` | A facade lazily re-exports a DRF serializer (`_LAZY` and `__getattr__`) | clean: no rule reads lazy names | Known gap | A finding (wanted) |
| `FD05` | `products/inventory/backend/facade/api.py` | A facade that raises its own contract errors | clean | | |
| `CN01` | `products/legacy/backend/facade/contracts.py`, `products/catalog/backend/events.py`, `posthog/kafka_client/client.py` | A contract reaches Django through a chain of module-level imports | contracts-no-django finding | Importing the contract loads Django | |
| `CN02` | `products/catalog/backend/facade/enums.py` | An enum module imports Django inside a function | contracts-no-django finding | The reach follows function-local imports | Clean (decided Nov 12: only what runs on import) |
| `CN03` | `products/inventory/backend/facade/contracts.py`, `posthog/__init__.py`, `posthog/celery.py` | A contract imports a core module whose package `__init__` loads Django | clean | A package `__init__` is not followed | Known gap, written on the rule's page |
| `CN04` | `products/catalog/backend/facade/contracts.py` | A contract with stdlib imports only | clean | | |
| `PF01` | `products/inventory/backend/presentation/views.py` | Presentation imports its own facade and presentation | clean | | |
| `PF02` | `products/legacy/backend/presentation/views.py` | Presentation imports its own models | presentation-uses-facade finding | | |
| `PF03` | `products/legacy/backend/routes.py` | `routes.py` imports a viewset from legacy `backend/api/` | presentation-uses-facade finding | | |
| `PF04` | `products/legacy/backend/presentation/views.py`, `products/legacy/backend/helpers.py` | Presentation imports its own internal module, which re-exports a facade name | clean: judged at the origin | | A finding (decided Nov 12: judged where the import lands) |
| `PF05` | `products/legacy/backend/presentation/views.py`, `products/reports/backend/presentation/views.py` | Presentation imports another product's presentation | clean: any presentation is allowed | | A finding unless an exported adapter (D93) |
| `PF06` | `products/legacy/backend/presentation/views.py` | Presentation imports another product's facade | clean | | |
| `TC01` | `products/legacy/backend/management/commands/seed_widgets.py` | A dev seeder writes models directly | thin-commands finding | | Clean once seeders are labelled dev tooling (D104) |
| `TC02` | `products/legacy/backend/management/commands/backfill_widget_names.py` | A one-off backfill imports logic | thin-commands finding | | Stays a finding (D93, D104) |
| `TC03` | `products/legacy/backend/management/commands/sync_widgets.py` | An ordinary command imports its product's tasks | thin-commands finding | | |
| `TC04` | `products/inventory/backend/management/commands/show_stock.py` | A command that goes through its facade | clean | | |
| `AT01` | `products/legacy/backend/presentation/views.py` | Presentation opens a transaction (`with` and decorator) | clean: no reference edge is made for a call, `with` or decorator | The rule cannot fire | A finding (B10; wanted) |
| `AT02` | `products/inventory/backend/logic/stock.py`, `products/legacy/backend/api/widgets.py` | A transaction outside presentation | clean | Only presentation is selected | |
| `PT01` | `products/legacy/backend/tasks/cleanup.py` | A production task imports a test factory | prod-imports-test finding | | |
| `PT02` | `products/catalog/backend/facade/testing.py` | `facade/testing.py` imports the product's test factory | prod-imports-test finding | | Open |
| `PT03` | `products/legacy/backend/tests/test_widgets.py` | A test imports a test factory | clean | | |
| `MC01` | `products/legacy/backend/logic/widgets.py` | A product queries another product's model; next to it an annotation and `DoesNotExist` | model-crossings finding for the query; clean for the others | Only instance-free shapes may cross | |
| `MC02` | `posthog/test/test_auth.py`, `products/legacy/backend/migrations/0001_initial.py` | A crossing in a test and in a migration | clean | Test and migration contexts are out | |
| `GM01` | `products/legacy/backend/logic/widgets.py` | `apps.get_model` names another product's model | get-model finding | | |
| `GM02` | `products/legacy/backend/logic/widgets.py` | `apps.get_model` names its own model | clean | | |
| `GM03` | `products/legacy/backend/logic/widgets.py` | `apps.get_model` with a variable label | get-model finding (unresolved); no-new-package-cycles finding onto `?` | | Open: unknown, not a finding (triage proposal) |
| `GM04` | `products/legacy/backend/migrations/0001_initial.py` | `apps.get_model` in a migration | clean | | |
| `RA01` | `products/legacy/backend/models.py` | An unsealed relation into another product's model | reverse-accessor finding | | |
| `RA02` | `products/legacy/backend/models.py` | An unsealed relation into a core model | reverse-accessor finding | | |
| `RA03` | `posthog/models/sharing.py` | A core model with an unsealed relation into a product | reverse-accessor finding | | |
| `RA04` | `products/inventory/backend/models.py`, `products/reports/backend/models.py`, `posthog/models/organization.py` | Sealed relations (`related_name="+"`) and core-to-core relations | clean | | |
| `RD01` | `products/legacy/backend/receivers.py` | A receiver on another product's sender, in a watched module | receiver-direction and model-crossings (`sender=`) findings; public-surface finding; wiring-watched clean | | Stays a finding (cross-check of Nov 12) |
| `RD02` | `products/legacy/backend/stock_listeners.py` | The same in an unwatched module | the same findings; wiring-watched finding | | |
| `RD03` | `products/inventory/backend/logic/stock.py` | A receiver on its own product's sender | clean | | |
| `IN01` | `products/legacy/backend/apps.py`, `products/legacy/backend/hooks.py` | A product registers a function into a core hook | installs finding; wiring-watched finding (the module that installs is unwatched) | | installs clean (decided Nov 12: the approved pattern) |
| `IN02` | `products/legacy/backend/apps.py` | A product registers a model class into a core registry | installs finding, as for a function | | A crossing that names the model (D100; wanted) |
| `IN03` | `products/reports/backend/apps.py`, `products/inventory/backend/facade/hooks.py` | A product registers into another product's facade hook | installs finding; wiring-watched finding | | installs clean (decided Nov 12) |
| `IN04` | `products/inventory/backend/apps.py` | A product registers into its own hook | clean | | |
| `DR01` | `posthog/test/test_widget_query.py`, `products/catalog/backend/hogql_queries/entries.py` | A core test runs a product's query kind | drives finding; wiring-watched finding for the location's `__init__.py` | | |
| `DR02` | `posthog/test/test_widget_query.py`, `products/reports/backend/hogql_queries/widget.py` | The same into a location without `__init__.py` | drives finding; wiring-watched clean | A directory without `__init__.py` has no path to require | wiring-watched finding once such a directory gets the product's labels (D97 point 2) |
| `DR03` | `products/reports/backend/tests/test_widget_query.py` | A product's own test runs its kind | clean | | |
| `WW01` | `posthog/auth.py`, `products/gatekeeper/backend/facade/api.py`, `products/gatekeeper/backend/logic/decisions.py` | Core's request code imports a facade at module level; the logic behind it is unwatched | no-product-lists-in-core finding (facade import); wiring-watched clean | No channel for the request path | wiring-watched finding for the logic file (D103; wanted) |
| `WW02` | `posthog/permissions.py`, `products/access_control/backend/logic/rules.py` | The same through an import inside a function | no-product-lists-in-core finding; wiring-watched clean | | Clean also with D103: function-level imports are a written limit |
| `WW03` | `posthog/settings/web.py`, `products/gatekeeper/backend/middleware.py`, `products/gatekeeper/backend/logic/session.py` | Settings wire a product middleware whose logic is watched | clean for wiring-watched (see `PS11` for the settings row) | | Clean (D103) |
| `DI01` | `products/legacy/backend/logic/widgets.py` | A computed import outside a registry loader | dynamic-imports incomplete | | |
| `DI02` | `posthog/products.py` | The registry loader's computed import | clean | | |
| `CY01` | `products/catalog/backend/facade/api.py`, `products/reports/backend/logic/build.py` | Two products that depend on each other | no-new-package-cycles finding, one per direction | | Stays (ledger lines) |
| `CY02` | `products/reports/backend/logic/build.py`, `products/catalog/backend/facade/shapes.py` | One-way dependencies | clean | | |
| `CY03` | `products/legacy/backend/logic/widgets.py` | An unresolved model label | no-new-package-cycles finding onto `?` | | Open (see `GM03`) |
| `FO01` | `products/access_control/backend/logic/rules.py` | A foundation product imports a feature product's facade | foundation-direction finding | | |
| `FO02` | `products/access_control/backend/logic/rules.py` | A foundation product imports another foundation product | clean | | |
| `IF01` | `posthog/egress/client.py` | Infrastructure imports a product's facade | infrastructure-direction finding; no-product-lists-in-core finding | | |
| `IF02` | `posthog/kafka_client/client.py`, `ee/billing/quota.py` | Infrastructure imports a proto-product | infrastructure-direction finding | | |
| `IF03` | `posthog/clickhouse/client/execute.py` | Infrastructure imports `common/` | clean | | |
| `LV01` | `packages/owners_yaml/django_glue.py` | A leaf imports Django and core | leaves-stay-leaves finding (both) | | |
| `LV02` | `packages/owners_yaml/parse.py` | A leaf with stdlib imports | clean | | |
| `OI01` | `posthog/egress/client.py` | A framework package takes a model | orm-independent finding | | |
| `OI02` | `posthog/egress/client.py` | A framework package takes `**kwargs: Any` | orm-independent finding (unknown) | `on_unknown` reports looseness | Open (triage: noise) |
| `OI03` | `posthog/egress/client.py` | A framework package with typed parameters | clean | | |
| `MD01` | `products/legacy/backend/migrations/0001_initial.py`, `products/db_routing.yaml` | A migration on its own database depends on a default-database migration | migration-database finding | | |
| `MD02` | `products/inventory/backend/migrations/0001_initial.py` | Both on the default database | clean | | |
| `FF01` | `products/inventory/backend/models.py`, `tools/hoglint/policy/featureflag-relations.toml` | A relation onto `FeatureFlag` that the classification lists | featureflag-relations finding: the check never reads the file | The run root holds no policy file | Clean |
| `FF02` | `products/legacy/backend/models.py` | A relation onto `FeatureFlag` that is not classified | featureflag-relations finding | | |
| `FF03` | `tools/hoglint/policy/featureflag-relations.toml` | A classification that matches no relation | clean: the file is not read | | A finding (stale; wanted) |
| `OD01` | `products/inventory/backend/models.py` | Every product model | own-database incomplete | No producer sets `database` on a model | |
| `PH01` | `posthog/temporal/ai_observability/summarize.py` | Product code under `posthog/` | product-code-at-home finding, one per file | | |
| `PH02` | `posthog/temporal/common/worker.py` | Core code next to it | clean | | |
| `RS01` | `products/inventory/backend/health_checks.py`, `products/catalog/backend/webhook_consumers.py` | Well-formed registry slot files | registry-slots incomplete | No producer records module-level assignments | Clean |
| `RS02` | `products/legacy/backend/health_checks.py` | A slot file without its attribute | registry-slots incomplete | Same | A finding (wanted) |
| `RS03` | `products/legacy/backend/webhook_consumers.py` | A slot member that does not subclass the base | registry-slots incomplete | Same | A finding (wanted) |
| `GP01` | `posthog/schema.py`, `posthog/schema_enums.py` | Generated files | generated-producer incomplete | No producer sets `generated` | |
| `HD01` | `products/reports/backend/health_checks.py` | A health check imports `posthog.dags` | health-checks-no-dags finding (advisory) | | |
| `HD02` | `products/inventory/backend/health_checks.py` | A health check without it | clean | | |
| `DO01` | `products/legacy/backend/logic/widgets.py`, `posthog/ingress/dispatch.py` | A product imports a core package past its door | clean: core packages have no inbound rule | | A finding once a core package declares its door (wanted) |
| `DO02` | `products/catalog/backend/webhook_consumers.py`, `posthog/ingress/contracts.py` | A product imports the door | clean | | |
| `LE01` | `posthog/temporal/ai_observability/summarize.py` | The Isolated level | `ai_observability` passes (no rule of the level reports it); every product under `products/` fails | Product code in core is outside every level rule | |

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
| public-surface | An access-control mixin used from a legacy `backend/api/` viewset | `PS16` |
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
| facade-signature | Fixture helpers in `facade/testing.py` | `FS11` |
| facade-signature | A product model crosses a facade | `FS01` |
| facade-signature | Another core model crosses | `FS05` |
| facade-signature | A queryset crosses | `FS02` |
| facade-signature | Bare `dict` | `FS06` |
| facade-signature | `Any` hides a model | `FS06` (bare `Any`) |
| facade-signature | A slot without annotation | `FS07`, `FS10` |
| facade-signature | `dict[str, Any]` returned | `FS06` |
| facade-logic | A thin wrapper | `FL01` |
| facade-logic | A mapper | `FL01` (`_to_level`) |
| facade-logic | A `facade/testing.py` helper | `FL06` |
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
| featureflag-relations | A relation with no classification | `FF02` |

## Adding a case

1. Write down the properties the case must have and the verdict each rule should give, then write the smallest invented code that has them.
   Never copy code from a real repository; use a path or dotted name from the preset only where the preset spells it out.
2. Add a clean neighbor: code that looks close and must not be a finding.
3. Add a row to the table above.
4. Run `bin/model-repo --refresh`, read the diff of `expected.toml`, and replace each `"?"` with the case id.
   A row you cannot tie to a case is a bug in the tree or a missed case.
5. A verdict that is decided but not given yet goes under `[[wanted]]` with its decision.
