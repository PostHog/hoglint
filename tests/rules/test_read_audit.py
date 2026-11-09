from __future__ import annotations

import datetime
from pathlib import Path

from hoglint.model import Edge, Resolution
from hoglint.rules.audit import ReadAudit, audit_rule
from hoglint.rules.base import EdgeEnv, EdgeRule, context_class
from hoglint.rules.edges import EdgePrimitive
from hoglint.rules.engine import Engine, primitive_of

from rules.support import GraphBuilder, config_from

FIELDS = """
schema = 1
extends = ["hoglint:django"]

[fields]
package = { type = "id", required_on = ["products/**", "core/**"] }
tier = { type = "enum", values = ["core", "product"], required_on = ["products/**", "core/**"] }
zone = { type = "zone" }

[[assign]]
match = "products/{product}/**"
set = { package = "{product}", tier = "product" }

[[assign]]
match = "core/**"
set = { package = "core", tier = "core" }

[zones]
within = "products/{product}"
facade = { paths = ["facade/**"] }
logic = { paths = ["logic.py"] }
models = { paths = ["models.py"] }

[roles]
group = "package"

[kinds]
model = { subclass_of = "django.db.models.Model" }

[identities]
crossing = ["dst.symbol", "src.module", "edge.label"]

[shapes.contract]
deny = ["model"]

[exports.admin]
within = "products/{product}"
paths = ["logic.py"]
audience = "src.module == 'core.views'"
shape = "contract"

[rules.no-product-from-core]
primitive = "edge"
kinds = ["import"]
from = "src.tier == 'core'"
deny = "dst.tier == 'product'"
identity = "module-pair"
ledger = false

[rules.public-surface]
primitive = "entrypoint"
kinds = ["import"]
select = "src.package != dst.package and dst.tier == 'product'"
identity = "module-symbol"
ledger = false

[rules.model-crossings]
primitive = "use-shape"
kinds = ["reference"]
select = "dst.kind == 'model' and src.package != dst.package"
allow_shapes = ["annotation"]
identity = "crossing"
ledger = false

[rules.facade-signature]
primitive = "signature"
select = "src.zone == 'facade'"
deny = "dst.kind == 'model'"
identity = ["edge.callable", "edge.slot", "dst.symbol"]
ledger = false

[rules.no-cycles]
primitive = "cycle"
view = "packages"
select = "src.tier == 'product' and dst.tier == 'product'"
identity = "package-pair"
ledger = false
"""


def graph() -> GraphBuilder:
    return (
        GraphBuilder()
        .module("products.billing.facade.api", "products.billing.logic", "products.billing.models")
        .module("products.alerts.logic", "core.api", "core.views")
        .symbol("products.billing.models.Invoice")
        .symbol("products.billing.logic.charge", "def")
        .edge(
            "inherit",
            "products.billing.models",
            "django.db.models.Model",
            attrs=(("class", "Invoice"),),
            resolution=Resolution.EXTERNAL,
        )
        .edge("import", "core.api", "products.billing.logic.charge")
        .edge("import", "core.views", "products.billing.logic.charge")
        .edge("import", "products.alerts.logic", "products.billing.logic.charge")
        .edge("import", "products.billing.logic", "products.alerts.logic")
        .edge("reference", "products.alerts.logic", "products.billing.models.Invoice", shape="annotation")
        .edge("reference", "products.alerts.logic", "products.billing.models.Invoice", shape="call")
        .edge("import", "core.api", "products.gone.thing", resolution=Resolution.UNRESOLVED)
        .edge(
            "exposes",
            "products.billing.facade.api",
            "products.billing.models.Invoice",
            attrs=(("callable", "charge"), ("slot", "return"), ("annotation", "Invoice")),
        )
    )


class ReadsSurface(EdgePrimitive):
    """A primitive whose decision reads a field it does not declare."""

    def droppable(self, env: EdgeEnv) -> bool:
        return env.edge.surface.endswith("charge") and super().droppable(env)


def audited(tmp_path: Path, rules: dict[str, type[EdgeRule]] | None = None) -> list[str]:
    config = config_from(tmp_path, FIELDS)
    built = graph().graph
    engine = Engine(config, built, tmp_path, today=datetime.date(2026, 11, 9))
    ctx = context_class(engine.labeler.index)(config, built, engine.labeler, tmp_path)
    audit = ReadAudit()
    with audit.installed():
        for spec in config.rules.values():
            make = (rules or {}).get(spec.id) or primitive_of(spec)
            rule = make(ctx, spec)
            assert isinstance(rule, EdgeRule)
            audit_rule(audit, rule, built.edges)
    return [failure.render() for failure in audit.failures]


def test_every_built_in_decision_reads_only_what_its_rule_declares(tmp_path: Path) -> None:
    assert audited(tmp_path) == []
    assert "__getattribute__" not in Edge.__dict__


def test_a_decision_that_reads_an_undeclared_field_fails_the_audit(tmp_path: Path) -> None:
    failures = audited(tmp_path, {"no-product-from-core": ReadsSurface})
    assert failures and all(f.startswith("no-product-from-core: reads edge.surface") for f in failures)
