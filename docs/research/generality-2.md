# Generality checks: config sketches for Sentry, Saleor and Zulip

> The hoglint config sketches written during the generality checks on three public Django repos, Nov 4, 2026. The checks themselves and the projection layer they led to are in `docs/research/generality.md`.
> Partly replaced: these sketches use the pre-projection config schema (packages, templates and zones); the projection layer v1 in `docs/research/generality.md` and the rules in `docs/design/04-rules.md` replaced that schema.

Each sketch expresses the repo's own boundary rules (from its docs, tests and lint config) in the config schema of the PostHog fit test, in TOML form.
They are generality checks, not proposals to those projects.
`[NEW]` or `NEW` marks a concept the fit-test schema lacked.

## Sentry (`getsentry/sentry`)

```toml
# hoglint.toml sketch for getsentry/sentry (fit-test schema, TOML form)
# [NEW] marks a concept the fit-test schema lacks.

source_roots = ["src", "."]          # [NEW] module name = path below a root; src/sentry/x.py -> sentry.x
first_party = ["sentry", "social_auth", "sudo", "bitfield", "apigw", "tests", "fixtures"]
exclude = ["fixtures/stubs-for-mypy/**", "fixtures/safe_migrations_apps/**"]

[django]
settings = "sentry.conf.server"     # not posthog.settings.web; INSTALLED_APPS is an annotated tuple
# no db_routing.yaml: the DB alias follows the model's silo through sentry.db.router.SiloRouter

# Packages: one per top-level domain dir. sentry/models/ is a legacy flat drawer (121 files,
# 155 models, many domains), owned per file in CODEOWNERS, so ownership needs a file-level source.
[[packages]]
glob = "src/sentry/*"
template = "domain"
[[packages]]
name = "sentry.models"
paths = ["src/sentry/models"]
owner_source = { codeowners = ".github/CODEOWNERS" }   # [NEW] per-file owner, no manifest
[[packages]]
name = "testutils"
paths = ["src/sentry/testutils"]
[[packages]]
name = "vendored"
paths = ["src/social_auth", "src/sudo", "src/bitfield"]
utility = true

[templates.domain.zones]             # optional zones: most domains have only some of them
models = ["models.py", "models/**"]
migrations = "migrations/**"
endpoints = ["api/endpoints/**", "endpoints/**"]
rpc_service = "services/*/service.py"   # zone glob with a wildcard segment = one zone per service
rpc_model = "services/*/model.py"
rpc_impl = "services/*/impl.py"
rpc_serial = "services/*/serial.py"
tasks = ["tasks.py", "tasks/**"]

# [NEW] symbol attribute from a decorator, not a package attribute. 8 of 33 model domains
# mix cell and control models, and app `sentry` holds both, so a package or app attribute is wrong.
[symbol_attributes.silo]
from_decorator = { "sentry.db.models.base.cell_silo_model" = "cell", "sentry.db.models.base.control_silo_model" = "control", "sentry.api.base.cell_silo_endpoint" = "cell", "sentry.api.base.control_silo_endpoint" = "control", "sentry.api.base.all_silo_endpoint" = "all", "sentry.silo.base.cell_silo_function" = "cell", "sentry.silo.base.control_silo_function" = "control" }
applies_to = ["kind:model", "kind:endpoint", "function"]
required_on = ["kind:model"]         # validate_models_have_silos, exemptions = {}

[symbol_kinds]
model = { subclass_of = "django.db.models.Model" }
endpoint = { subclass_of = "sentry.api.base.Endpoint" }
rpc_service = { subclass_of = "sentry.hybridcloud.rpc.service.RpcService" }
rpc_model = { subclass_of = "sentry.hybridcloud.rpc.RpcModel" }

# test_silo_models.py: no FK between models of different silos (static parity: 0 found, 0 exempt)
[rules.same-attribute-relations]     # [NEW] same-attribute over relation edges, symbol attribute
refs = ["relation"]
attribute = "silo"
exceptions_file = "tests/sentry/db/test_silo_models.py:fk_exemptions"   # stale exemption = error
# HybridCloudForeignKey is a plain integer column: no relation edge, so it is the allowed escape.

[rules.hcfk-target]                  # validate_hcfk_has_global_id
symbol = "sentry.db.models.fields.hybrid_cloud_foreign_key.HybridCloudForeignKey"
target_attribute = { silo = "control", or_kind = "snowflake_model" }

# Cross-silo calls go through RPC services; impl is picked by silo mode at runtime.
[rules.layers]
rpc_model = { deny = ["models@other", "ext:django.db"] }   # RPC models are pydantic wire types
[rules.public]
zones = ["rpc_service", "rpc_model"]          # impl/serial are private to their service
skip_from = ["tests"]

[rules.edge-attribute]               # [NEW] reference from silo X code to a silo Y model (runtime SiloLimit today)
from_attribute = "silo"
to = "kind:model"
deny = "different-silo"
on_unresolved = "finding"            # most code has no silo marker; inherits from endpoint/task reach

[rules.forbid]
items = [
  { from = "src/**", except_from = ["src/sentry/testutils/**"], to = "sentry.testutils", kind = "import" },   # S007
  { to_exact = "sentry.models", kind = "import" },                                                      # S005: import the defining module, not the re-export
]

[rules.registry-membership]          # [NEW] layout: a service must sit under a listed discovery package
glob = "src/sentry/*/services/*/service.py"
allowed_from = "src/sentry/hybridcloud/rpc/service.py:service_packages"

[rules.cross-repo-exports]           # [NEW] getsentry imports sentry symbols; renames need an alias
consumer = "getsentry"               # outside this repo; needs an exported-symbol snapshot as input

[debt]
file = "hoglint.debt"
```

## Saleor (`saleor/saleor`)

```toml
# hoglint.toml sketch for saleor/saleor (generality check, not their config).
# Policy source: AGENTS.md "Code structure and layering", "Architecture", "Webhooks and Events".

[project]
first_party = ["saleor"]
source_roots = ["."]
settings = "saleor/settings.py"          # INSTALLED_APPS, AUTH_USER_MODEL; no db_routing file
exclude = ["saleor/static/**", "saleor/core/development/**"]   # dev-only data generators

# --- packages -------------------------------------------------------------
# A package is a DOMAIN, not a directory: saleor/<d>/ plus its mirror saleor/graphql/<d>/.
# NEW CONCEPT: one package from two globs joined on a capture ({d}).
[[packages]]
glob = ["saleor/{d}/**", "saleor/graphql/{d}/**"]
template = "domain"
not = ["graphql", "tests", "static", "core", "plugins", "webhook"]

[[packages]]
name = "core"                             # shared kernel, every domain uses it
paths = ["saleor/core", "saleor/graphql/core", "saleor/graphql/utils", "saleor/permission"]
utility = true

[[packages]]
name = "extensions"                       # plugin manager + webhook dispatch; everyone calls it
paths = ["saleor/plugins", "saleor/webhook", "saleor/graphql/plugins", "saleor/graphql/webhook"]
utility = true

[[packages]]
name = "api-root"
paths = ["saleor/graphql/api.py", "saleor/graphql/schema.graphql", "saleor/graphql/views.py"]

# --- layers ---------------------------------------------------------------
# NEW CONCEPT: a repo-wide layer, assigned by path BEFORE packages, cutting across all of them.
# Zones become (package, layer, zone). Rules can address layer:graphql without naming a package.
[layers]
graphql = "saleor/graphql/**"
domain  = "saleor/**"                     # first match wins, so graphql is carved out
order   = ["graphql", "domain"]           # graphql may import domain, never the reverse

[templates.domain.zones]                  # paths relative to each glob root, per layer
models     = ["models.py", "models/**"]
migrations = "migrations/**"
tasks      = "tasks.py"
locks      = "lock_objects.py"
api.schema    = "schema.py"               # only in the graphql layer
api.mutations = ["mutations/**", "bulk_mutations/**"]
api.types     = "types/**"
api.loaders   = "dataloaders/**"
tests      = ["**/tests/**", "**/test_*.py", "conftest.py"]

# --- symbol kinds and registries -----------------------------------------
[symbol_kinds]
model  = { subclass_of = "django.db.models.Model" }
plugin = { subclass_of = "saleor.plugins.base_plugin.BasePlugin" }
mutation = { subclass_of = "saleor.graphql.core.mutations.BaseMutation" }

[registries.plugins]                      # dotted strings in settings, plus entry points at runtime
keys = "saleor/settings.py:BUILTIN_PLUGINS"
targets = "kind:plugin"
unresolved = "all-candidates"             # EXTERNAL_PLUGINS come from importlib.metadata: not static

# --- rules ----------------------------------------------------------------
[rules.layer-order]                       # "Non-GraphQL code must never import from the graphql layer"
primitive = "edge"
view = "file-imports"
from = "layer:domain"
deny = ["layer:graphql"]
skip_from = ["zone:tests"]
on_unresolved = "finding"

[rules.dev-only]                          # "Never import saleor.core.development.* outside tests"
primitive = "edge"
to = "path:saleor/core/development/**"
allow_from = ["zone:tests", "path:saleor/core/management/commands/populatedb.py"]

[rules.no-signals]                        # "Do not use Django signals", except post_migrate
primitive = "use-shape"
symbols = ["django.db.models.signals.*", "django.dispatch.receiver"]
deny = ["construct", "decorate", "call(connect)"]
except_symbols = ["django.db.models.signals.post_migrate"]

[rules.call-event]                        # manager.<event>(...) only through call_event(...)
primitive = "use-shape"
symbols = ["saleor.plugins.manager.PluginsManager.*"]
deny = ["call"]                           # needs receiver typing: `manager` is a parameter
allow = ["passed-as-arg(saleor.core.utils.events.call_event)"]
except_from = ["zone:tests", "path:saleor/plugins/**"]

[rules.global-imports]                    # "Place all imports at the top of the file"
primitive = "edge"
select = { contexts_in = ["function_local"] }
skip_from = ["zone:tests"]

# Not expressed: depends_on per domain (no such policy; prod domain graph is one 52-node SCC),
# public zones / facades (no such concept), levels (no maturity ladder), same-attribute DB rule
# (one writer DB; replica routing is a router, not an app attribute).

[debt]
file = "hoglint.debt"                     # day one: 73 layer-order lines, 173 global-imports lines
key = ["rule", "src.module", "dst.module"]
stale = "error"
```

## Zulip (`zulip/zulip`)

```toml
# hoglint.toml sketch for zulip/zulip (generality check, not a proposal to Zulip)

[discovery]
roots = ["analytics", "confirmation", "corporate", "pgroonga", "scripts", "tools", "zerver", "zilencer", "zproject"]
scripts = { shebang = "python", paths = ["tools/**", "scripts/**", "puppet/**"] }   # NEW: extensionless entrypoints
settings = { module = "zproject.computed_settings", variants = { dev = "zproject.dev_settings", prod = "zproject.prod_settings_template" } }  # NEW

[[packages]]
name = "zerver"
paths = ["zerver"]
template = "layered-app"

[[packages]]
name = "confirmation"
paths = ["confirmation"]

[[packages]]
name = "analytics"
paths = ["analytics"]
optional = true                      # NEW: in EXTRA_INSTALLED_APPS

[[packages]]
name = "corporate"
paths = ["corporate"]
optional = true                      # not in the production tarball

[[packages]]
name = "zilencer"
paths = ["zilencer"]
optional = true

[[packages]]
name = "prod_scripts"
paths = ["scripts"]
deny_external = ["django"]           # scripts run before Django and the venv exist (plausible, unchecked)

[templates.layered-app.zones]        # zones without facades: these ARE the architecture
models = "models/**"
lib = "lib/**"
actions = "actions/**"
views = ["views/**", "tornado/views.py"]
webhooks = "webhooks/**"
worker = "worker/**"
management = "management/**"
migrations = "migrations/**"
tests = ["tests/**", "transaction_tests/**", "lib/test_*.py"]

[symbol_kinds]
model = { subclass_of = "django.db.models.Model" }
queue_worker = { decorated_by = "zerver.worker.base.assign_queue" }
webhook_view = { decorated_by = "zerver.decorator.webhook_view" }

[registries.queue]
keys = { decorator = "zerver.worker.base.assign_queue", arg = 0 }
callers = ["zerver.lib.queue.queue_json_publish_rollback_unsafe", "zerver.lib.queue.queue_event_on_commit"]
targets = "zone:worker"
unresolved = "all-candidates"

[registries.webhook]
keys = { file = "zerver/lib/integrations.py", list = "INCOMING_WEBHOOK_INTEGRATIONS" }
target_template = "zerver.webhooks.{dir_name}.view.api_{dir_name}_webhook"   # NEW: convention-built dotted path

[rules.layers]                       # inside ONE package; the important part for Zulip
models  = { deny = ["actions", "views", "worker"] }
lib     = { deny = ["actions", "views"] }
actions = { deny = ["views"] }
"*"     = { deny = ["tests"], contexts_out = ["tests"] }

[rules.call-site]                    # NEW primitive (or forbid+use:call with an allow zone)
send_event = { symbols = ["zerver.tornado.django_api.send_event_on_commit", "zerver.tornado.django_api.send_event_rollback_unsafe"], only_in = ["zone:actions", "zone:tests"] }

[[rules.forbid]]                     # mirrors custom_check.py access rules, typed instead of regex
symbol = "zerver.models.Message"
use = "instance-single(get)"
except_from = ["zone:tests", "zerver/management/commands/export.py"]
remedy = "zerver.lib.message.access_message"

[[rules.forbid]]
symbol = "zerver.models.Stream"
use = "instance-single(get)"
from = "zone:views"
remedy = "zerver.lib.streams.access_stream_by_id"

[rules.optional-packages]            # NEW: core never needs an optional package at import time
from = ["zerver", "confirmation"]
to = ["corporate", "zilencer"]
allow_contexts = ["function_local", "conditional(settings.CORPORATE_ENABLED|settings.ZILENCER_ENABLED)"]

[debt]
file = "hoglint.debt"
key = ["rule", "src", "dst", "kind"]
counted = true
stale = "error"
```
