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
        (".objects.filter().values_list().first()", "return", Shape("scalar-chain", "values_list")),
        (".objects.values().order_by().distinct()", "assign:rows", Shape("scalar-chain", "values")),
        (".objects.values().filter().update()", "expr", Shape("write", "update")),
        ("", "kw:sender:receiver", Shape("sender")),
        ("", "kw:sender:post_save.connect", Shape("sender")),
        ("", "bin:| bin:| assign:Target", Shape("annotation")),
        ("", "bin:| annassign:Target", Shape("annotation")),
        ("", "typealias:Target", Shape("annotation")),
        ("", "bin:| arg:1:isinstance", Shape("other", "BinOp")),
        ("", "bin:+ assign:total", Shape("other", "BinOp")),
        ("", "bin:| elt:tuple slice assign:Cache", Shape("annotation")),
        ("", "elt:tuple slice assign:Cache", Shape("in-collection")),
        (".objects.filter().order_by()[].values()", "assign:rows", Shape("scalar-chain", "values")),
        (".objects.filter()[].values", "assign:rows", Shape("instance-many", "filter")),
        (".objects.select_for_update()[].values()", "expr", Shape("lock")),
    ],
)
def test_classify(chain: str, up: str, expected: Shape) -> None:
    assert OrmShapes().classify(UsePosition.of(chain, up)) == expected


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
    positions = [UsePosition.of(chain, "expr") for chain in later]
    assert shapes.refine(UsePosition.of(source, "assign:qs"), positions) == expected
