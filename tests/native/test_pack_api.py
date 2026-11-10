"""The pack API (D22) in both engines: refinements by handle, conflicts, failures, and grouped
judgments over members that differ in what their group does not hold."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from hoglint.core.harness import Output, ProducerPlan, ProducerStep
from hoglint.model import Context, Edge
from hoglint.native.parity import compare_dumps, read_lines
from hoglint.plugins import REGISTRY, PackReader, PackSink, Refinement

from native.test_parity import dumps

SECTION = "refiner"
PRODUCER = "refiner.{}@1"


class Refiner:
    """A test pack. Each `[[refiner.steps]]` entry is one producer that refines the core
    `reference` edges spelled `Invoice` (`shape`), adds an edge at the same site in the test
    context (`twin`), or fails (`fail`)."""

    name = SECTION
    allowed_keys = frozenset({"steps"})
    edge_kinds = frozenset({"reference"})

    def produce(self, section: Mapping[str, Any], reader: PackReader, sink: PackSink, root: Path) -> None:
        steps = [self.step(reader, entry) for entry in section.get("steps", [])]
        ProducerPlan(steps).run(sink)

    @staticmethod
    def step(reader: PackReader, entry: Mapping[str, Any]) -> ProducerStep:
        def run() -> list[Output]:
            if entry.get("fail"):
                raise RuntimeError("this producer fails")
            out: list[Output] = []
            for edge in reader.edges_spelled("reference", ["Invoice"]):
                if entry.get("twin"):
                    twin = Edge(
                        edge.kind,
                        edge.site,
                        edge.surface,
                        edge.targets,
                        edge.resolution,
                        frozenset({Context.TEST}),
                        PRODUCER.format(entry["name"]),
                    )
                    out.append(twin)
                if "shape" in entry:
                    out.append(Refinement(edge.handle, entry["shape"], "", tuple(entry.get("attrs", {}).items())))
            return out

        return ProducerStep(PRODUCER.format(entry["name"]), frozenset({"reference"}), run)


if SECTION not in REGISTRY.packs:
    REGISTRY.add_pack(Refiner())


def repo(steps: str, rules: str) -> dict[str, str]:
    return {
        "hoglint.toml": f"""
schema = 1

[discovery]
source_roots = ["."]
first_party = ["core", "products"]

[vocabulary]
sections = ["refiner"]
use_shapes = ["write", "lock"]

[fields]
package = {{ type = "id" }}
tier = {{ type = "enum", values = ["core", "product"] }}

[[assign]]
match = "products/{{product}}/**"
set = {{ package = "{{product}}", tier = "product" }}

[[assign]]
match = "core/**"
set = {{ package = "core", tier = "core" }}

[refiner]
{steps}
{rules}
""",
        "core/__init__.py": "",
        "core/a.py": "from products.billing.models import Invoice\n\n\ndef f():\n    Invoice.objects.update()\n",
        "core/b.py": "from products.billing.models import Invoice\n\n\ndef g():\n    Invoice.objects.update()\n",
        "products/__init__.py": "",
        "products/billing/__init__.py": "",
        "products/billing/models.py": "class Invoice:\n    pass\n",
    }


SHAPE_IDENTITY = """
[rules.uses]
primitive = "edge"
kinds = ["reference"]
contexts = { include = ["prod", "test"] }
from = "src.tier == 'core'"
deny = "dst.tier == 'product'"
identity = ["src.module", "edge.shape", "edge.producer"]
ledger = false
"""


def findings(out: dict[str, Path], rule: str) -> list[list[str]]:
    return sorted(row[1] for row in read_lines(out["native"] / "findings.jsonl") if row[0] == rule)


def failed(out: dict[str, Path]) -> dict[str, str]:
    return {row[0]: row[1] for row in read_lines(out["native"] / "failed.jsonl")}


def same_in_both_engines(out: dict[str, Path]) -> None:
    assert [diff.render() for diff in compare_dumps(out["python"], out["native"]) if not diff.equal] == []


def test_a_refined_field_read_only_by_an_identity_is_the_refined_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = dumps(tmp_path, repo('steps = [{ name = "a", shape = "write" }]', SHAPE_IDENTITY), monkeypatch)
    same_in_both_engines(out)
    assert findings(out, "uses") == [["core.a", "write", "core-reference@1"], ["core.b", "write", "core-reference@1"]]


def test_an_edge_at_the_site_of_a_refined_edge_stays_its_own_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = 'steps = [{ name = "a", shape = "write" }, { name = "twin", twin = true }]'
    out = dumps(tmp_path, repo(steps, SHAPE_IDENTITY), monkeypatch)
    same_in_both_engines(out)
    assert findings(out, "uses") == [
        ["core.a", "-", "refiner.twin@1"],
        ["core.a", "write", "core-reference@1"],
        ["core.b", "-", "refiner.twin@1"],
        ["core.b", "write", "core-reference@1"],
    ]


def test_two_refiners_agree_or_the_second_fails_and_sets_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = (
        'steps = [{ name = "a", shape = "write", attrs = { by = "a" } }, '
        '{ name = "same", shape = "write", attrs = { also = "same" } }, { name = "other", shape = "lock" }]'
    )
    out = dumps(tmp_path, repo(steps, SHAPE_IDENTITY), monkeypatch)
    same_in_both_engines(out)
    assert list(failed(out)) == ["refiner.other@1"]
    refined = [row for row in read_lines(out["native"] / "edges.jsonl") if row[13]]
    assert [(row[13], row[15][-2:]) for row in refined] == [("write", [["by", "a"], ["also", "same"]])] * 2


def test_a_failed_refining_producer_sets_nothing_and_leaves_its_rules_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = dumps(
        tmp_path, repo('steps = [{ name = "broken", shape = "write", fail = true }]', SHAPE_IDENTITY), monkeypatch
    )
    same_in_both_engines(out)
    assert list(failed(out)) == ["refiner.broken@1"]
    assert not [row for row in read_lines(out["native"] / "edges.jsonl") if row[13]]
    assert {row[0] for row in read_lines(out["native"] / "incomplete.jsonl")} == {"uses"}


SURFACE_IDENTITY = """
[rules.surfaces]
primitive = "edge"
kinds = ["reference"]
from = "src.tier == 'core'"
deny = "dst.tier == 'product'"
identity = ["src.module", "edge.surface", "edge.shape"]
ledger = false
"""


def test_grouped_members_that_differ_outside_their_group_key_judge_alike(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = repo('steps = [{ name = "a", shape = "write" }]', SURFACE_IDENTITY)
    sources["core/b.py"] = "from products.billing import models\n\n\ndef g():\n    models.Invoice.objects.update()\n"
    out = dumps(tmp_path, sources, monkeypatch)
    same_in_both_engines(out)
    assert findings(out, "surfaces") == [
        ["core.a", "products.billing.models.Invoice", "write"],
        ["core.b", "products.billing.models.Invoice", "write"],
    ]


LABEL_DECISION = """
[rules.labels]
primitive = "edge"
kinds = ["reference"]
from = "src.tier == 'core'"
deny = "edge.label == 'write'"
identity = ["src.module", "edge.surface"]
ledger = false
"""


def test_a_decision_on_the_label_keeps_two_labels_of_one_kind_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = repo('steps = [{ name = "a", shape = "write" }]', LABEL_DECISION)
    sources["core/a.py"] = (
        "from products.billing.models import Invoice, Other\n\n\ndef f():\n"
        "    Other.objects.all()\n    Invoice.objects.update()\n"
    )
    sources["products/billing/models.py"] = "class Invoice:\n    pass\n\n\nclass Other:\n    pass\n"
    out = dumps(tmp_path, sources, monkeypatch)
    same_in_both_engines(out)
    assert findings(out, "labels") == [
        ["core.a", "products.billing.models.Invoice"],
        ["core.b", "products.billing.models.Invoice"],
    ]
