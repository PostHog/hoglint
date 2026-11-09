from __future__ import annotations

from pathlib import Path

import pytest

from hoglint.config import ConfigError
from hoglint.labels import CodeownersFile, Labeler, render
from hoglint.model import Resolution
from hoglint.rules.engine import Engine

from rules.support import GraphBuilder, config_from

CONFIG = """
schema = 1

[fields]
package = { type = "id", required_on = ["products/**", "posthog/**"] }
tier = { type = "enum", values = ["core", "product"] }
zone = { type = "zone" }
stage = { type = "enum", values = ["proto-product"] }
silo = { type = "enum", values = ["cell", "control"], on = "symbol", required_on = "kind == 'model'" }
owners = { type = "set" }

[providers.codeowners]
use = "codeowners"
file = "CODEOWNERS"
sets = ["owners"]

[[assign]]
match = "products/{product}/**"
except = { product = ["legacy"] }
set = { package = "{product}", tier = "product" }

[[assign]]
match = "posthog/**"
set = { package = "core", tier = "core" }

[[assign]]
match = "posthog/egress/**"
set = { package = "egress", zone = "infrastructure" }
override = true

[[assign]]
symbols = { decorated_by = "lib.cell_silo_model" }
set = { silo = "cell" }

[kinds]
model = { subclass_of = "django.db.models.Model", abstract = false }

[zones]
within = "products/{product}"
infrastructure = {}
backend = { paths = ["backend/**"] }
facade = { parent = "backend", paths = ["backend/facade/**"] }
contracts = { parent = "facade", paths = ["backend/facade/contracts.py"] }
tests = { paths = ["**/tests/**"], override = true }
"""

CODEOWNERS = """
/posthog/egress/ @team-infra
*.py @team-fallback
/products/billing/ @team-billing
"""


@pytest.fixture
def labeler(tmp_path: Path) -> Labeler:
    config = config_from(tmp_path, CONFIG, {"CODEOWNERS": CODEOWNERS})
    graph = (
        GraphBuilder()
        .module(
            "products.billing.backend.facade.contracts",
            "products.billing.backend.apps",
            "products.billing.scripts.seed",
            "products.billing.backend.tests.test_api",
            "products.legacy.thing",
            "posthog.egress.client",
            "posthog.models.team",
            "tools.script",
        )
        .symbol("posthog.models.team.Team")
        .symbol("posthog.models.team.Mystery")
        .edge("inherit", "posthog.models.team", "django.db.models.Model", attrs=(("class", "Team"),))
        .edge("decorate", "posthog.models.team", "lib.cell_silo_model", attrs=(("decorated", "Team"),))
        .edge(
            "inherit",
            "posthog.models.team",
            None,
            "something.Unknown",
            resolution=Resolution.UNRESOLVED,
            attrs=(("class", "Mystery"),),
        )
        .graph
    )
    return Labeler(config, graph, tmp_path)


@pytest.mark.parametrize(
    "module, field, expected",
    [
        ("products.billing.backend.facade.contracts", "zone", "contracts  (inside facade, backend)"),
        ("products.billing.backend.apps", "zone", "backend"),
        # product files outside the zone table get the default private zone, never unknown
        ("products.billing.scripts.seed", "zone", "internal"),
        ("products.billing.backend.tests.test_api", "zone", "tests"),
        ("products.billing.backend.apps", "package", "billing"),
        ("products.legacy.thing", "package", "unknown"),
        ("posthog.egress.client", "package", "egress"),
        ("posthog.egress.client", "zone", "infrastructure"),
        ("posthog.models.team", "zone", "none"),
        # outside the field's domain a missing value is a known none
        ("tools.script", "package", "none"),
        ("posthog.models.team", "stage", "none"),
        ("products.billing.backend.apps", "owners", "@team-billing"),
        ("posthog.egress.client", "owners", "@team-fallback"),
    ],
)
def test_module_labels(labeler: Labeler, module: str, field: str, expected: str) -> None:
    value, _ = labeler.field_value(module, None, field)
    assert render(value) == expected


def test_symbol_labels_kinds_and_domains(labeler: Labeler) -> None:
    assert render(labeler.kind("posthog.models.team.Team")[0]) == "model"
    assert render(labeler.field_value("posthog.models.team", "posthog.models.team.Team", "silo")[0]) == "cell"
    # a base that did not resolve leaves the kind unknown, so silo's domain is unknown too
    assert render(labeler.kind("posthog.models.team.Mystery")[0]) == "unknown"
    # without facts a third-party symbol has no kind, a first-party one an unknown kind: asked in
    # that order, the first answer must not stick to the second question
    assert render(labeler.kind("posthog.models.team.Ghost", first_party=False)[0]) == "none"
    assert render(labeler.kind("posthog.models.team.Ghost")[0]) == "unknown"


@pytest.mark.parametrize(
    "pattern, path, owned",
    [
        ("/docs/*", "docs/a.md", True),
        ("/docs/*", "docs/sub/b.md", False),
        ("/docs/", "docs/sub/b.md", True),
        ("docs/", "x/docs/a.md", True),
        ("/apps/github", "apps/github/x/y.py", True),
        ("apps/github", "lib/apps/github/x.py", False),
        ("*.js", "a/b/c.js", True),
        ("**/logs", "a/logs/x.txt", True),
        ("docs/**", "docs/a/b.md", True),
        ("/scripts/*.sh", "scripts/x/a.sh", False),
        ("/src/{x}/", "src/y/a.py", False),
    ],
)
def test_codeowners_patterns_follow_github(pattern: str, path: str, owned: bool) -> None:
    assert (CodeownersFile(f"{pattern} @team").owners(path) is not None) is owned


def test_conflicting_assignments_are_a_config_error(tmp_path: Path) -> None:
    text = CONFIG.replace(
        'set = { package = "egress", zone = "infrastructure" }\noverride = true', 'set = { package = "egress" }'
    )
    config = config_from(tmp_path, text, {"CODEOWNERS": CODEOWNERS})
    graph = GraphBuilder().module("posthog.egress.client").graph
    with pytest.raises(ConfigError, match="field 'package' set differently"):
        Engine(config, graph, tmp_path).evaluate()


def test_explain_names_each_source(labeler: Labeler) -> None:
    rows = {name: (value, source) for name, value, source in labeler.explain("posthog.egress.client")}
    assert rows["package"][0] == "egress"
    assert "posthog/egress/**" in rows["package"][1]
    assert rows["owners"][1].startswith("provider codeowners")
