from __future__ import annotations

import pytest

from hoglint.packs.django.shapes import OrmShapes, Shape, UsePosition


@pytest.mark.parametrize(
    "chain, up, expected",
    [
        (".objects.select_for_update().count()", "expr", Shape("lock")),
        (".objects.filter().count()", "return", Shape("scalar-chain", "count")),
        (".objects.filter().update()", "expr", Shape("write", "update")),
        (".objects.filter().first()", "assign:x", Shape("instance-single")),
        (".objects.filter()", "arg:1:Prefetch", Shape("prefetch")),
        (".objects.all()", "kw:queryset:serializers.PrimaryKeyRelatedField", Shape("drf-field-queryset")),
        (".DoesNotExist", "except", Shape("exception")),
        ("", "param-ann:team", Shape("annotation")),
        ("", "assign:model", Shape("drf-model-serializer")),
        ("", "arg:1:isinstance", Shape("isinstance")),
        ("", "arg:0:cast", Shape("passed-as-class", "cast")),
        ("()", "assign:obj", Shape("construct")),
    ],
)
def test_classify(chain: str, up: str, expected: Shape) -> None:
    assert OrmShapes().classify(UsePosition.from_row(chain, up)) == expected


@pytest.mark.parametrize(
    "source, later, expected",
    [
        (".objects.filter()", [".update()"], Shape("write", "update")),
        (".objects.filter()", [".select_for_update().first()"], Shape("lock")),
        (".objects.get()", [".delete()"], Shape("write", "delete")),
        (".objects.filter()", [".count()"], None),
        ("()", [".delete()"], None),
    ],
)
def test_refine_sees_writes_through_a_local(source: str, later: list[str], expected: Shape | None) -> None:
    shapes = OrmShapes()
    positions = [UsePosition.from_row(chain, "expr") for chain in later]
    assert shapes.refine(UsePosition.from_row(source, "assign:qs"), positions) == expected
