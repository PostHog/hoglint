"""A model in a type slot of what a facade hands out is reported exactly once, on generated trees.

A tree is one choice from each of three axes: where the slot sits (`PLACEMENTS`), how its type is
written (`WRAPPERS`) and how the facade hands the thing out (`HAND_OUTS`). Each tree runs with the
probes in the slot: a model of the product, `int`, a name that does not resolve and, under the bare
wrapper, `Any`.

The invariant, on the native engine's tables (`clean_dump`):
- the model gives exactly one finding, from the rule the slot table of the design page names, and it
  names the model;
- `int` gives no finding;
- the unresolved name and `Any` give exactly one finding each, from that rule.

A wrapper the design page reads as unknown (a `NewType`, a class-body alias) gives one finding
that names the wrapper, with every probe. A `TypeVar` bound to the probe and a functional `TypedDict`
with a field of it are judged by what they stand for, as an alias is. A member of a class
core defines gives no finding: it is core's contract. A field of a class core defines that a slot
names gives a finding for a denied kind only: the model, where the wrapper lets it be read.

A tree that does not meet the invariant today is a row of `LIMITS`: the test asserts it still gives
exactly what that row says. When a fix lands, its rows fail and must be removed.

Many trees run in one repo, one product per tree, so one dump serves a batch.
The suite runs a subset (see `selected`); `HOGLINT_SLOTS_FULL=1` runs every tree.
"""

from __future__ import annotations

import os
import re
import random
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hoglint import facts
from hoglint.native import analysis as native_analysis
from hoglint.native.parity import read_lines
from rules.support import PRESET

# The shared harness: the native engine's tables of a repo, which must fail no file.
from native.test_contract_slots import clean_dump
from native.test_facade_rules import MODEL, ROOT_CONFIG, SITE_PACKAGES

# Every tree is written for the product `shop` and its core module `posthog/coreslot.py`, then moved
# to a product of its own in the batch.
D = "products/shop/backend"
S = "products.shop.backend"
CORE = "posthog.coreslot"
CORE_PATH = "posthog/coreslot.py"
EXPORT = "export-shape"
SIGNATURE = "facade-signature"
RULES = (EXPORT, SIGNATURE)
Files = dict[str, str]
# The number of findings per rule; a rule left out gives none.
Verdict = dict[str, int]


# ── The probes: what sits in the slot. ──
@dataclass(frozen=True)
class Probe:
    name: str
    type_name: str
    imports: str
    # A finding for this probe names this, as a regular expression over its identity.
    named: str


MODEL_PROBE = Probe("model", "Order", f"from {S}.models import Order\n", r"\.models\.Order\b")
CLEAN_PROBE = Probe("int", "int", "", "")
UNRESOLVED_PROBE = Probe("unresolved", "Missing", f"from {S}.nowhere import Missing\n", "")
ANY_PROBE = Probe("Any", "Any", "from typing import Any\n", r"\btyping\.Any\b")
PROBES = (MODEL_PROBE, CLEAN_PROBE, UNRESOLVED_PROBE, ANY_PROBE)


# ── Axis 2: how the type is written. ──
@dataclass(frozen=True)
class Typed:
    """A slot type as written: the annotation and what the module must hold for it."""

    annotation: str
    imports: str = ""
    module_lines: str = ""
    class_lines: str = ""
    files: Files = field(default_factory=dict)


@dataclass(frozen=True)
class Wrapper:
    name: str
    write: Callable[[Probe], Typed]
    # The name a finding gives in place of the probe, where the design page reads the wrapper as
    # unknown whatever it holds.
    opaque: str = ""
    in_class_only: bool = False


def around(template: str) -> Callable[[Probe], Typed]:
    return lambda probe: Typed(template.format(X=probe.type_name), probe.imports)


def aliased(lines: str, annotation: str = "Ref") -> Callable[[Probe], Typed]:
    return lambda probe: Typed(annotation, probe.imports, lines.format(X=probe.type_name))


def imported_alias(lines: str) -> Callable[[Probe], Typed]:
    def write(probe: Probe) -> Typed:
        types = "from __future__ import annotations\n\n" + probe.imports + "\n\n" + lines.format(X=probe.type_name)
        return Typed("Ref", f"from {S}.types import Ref\n", files={f"{D}/types.py": types})

    return write


def type_checking_import(probe: Probe) -> Typed:
    if not probe.imports:
        return Typed(probe.type_name)
    return Typed(probe.type_name, "if TYPE_CHECKING:\n" + indent(probe.imports, "    "))


def class_body_alias(probe: Probe) -> Typed:
    return Typed("Ref", probe.imports, class_lines=f"Ref = {probe.type_name}\n")


WRAPPERS = (
    Wrapper("bare", around("{X}")),
    Wrapper("X | None", around("{X} | None")),
    Wrapper("list[X]", around("list[{X}]")),
    Wrapper("dict[str, X]", around("dict[str, {X}]")),
    Wrapper("tuple[X, ...]", around("tuple[{X}, ...]")),
    Wrapper("a string annotation", around("'{X}'")),
    Wrapper("Annotated[X, ...]", around("Annotated[{X}, 'meta']")),
    Wrapper("a starred tuple", around("tuple[int, *tuple[{X}, ...]]")),
    Wrapper("an import under if TYPE_CHECKING", type_checking_import),
    Wrapper("a module-level alias", aliased("Ref = {X}\n")),
    Wrapper("an alias of an alias", aliased("Inner = {X}\nRef = Inner\n")),
    Wrapper("an imported alias", imported_alias("Ref = {X}\n")),
    Wrapper("an imported alias of an alias", imported_alias("Inner = {X}\nRef = list[Inner]\n")),
    Wrapper("a type statement", aliased("type Ref = {X}\n")),
    Wrapper(
        "an alias bound once per branch",
        aliased("if TYPE_CHECKING:\n    Ref = {X}\nelse:\n    Ref = int\n"),
    ),
    Wrapper("a class-body alias", class_body_alias, opaque="Ref", in_class_only=True),
    Wrapper("NewType", aliased("Ref = NewType('Ref', {X})\n"), opaque="Ref"),
    Wrapper("a TypeVar with the bound", aliased("T = TypeVar('T', bound={X})\n", "T")),
    Wrapper("a functional TypedDict", aliased("Props = TypedDict('Props', {{'x': {X}}})\n", "Props")),
)


# ── Axis 1: where the slot sits. ──
@dataclass(frozen=True)
class Site:
    """A tree before its hand-out: the files, the name the facade hands out and where it lives."""

    files: Files
    carrier: str
    # The module that defines the carrier, relative to the product backend: `errors` (outside the
    # facade), `facade.api` or `facade.contracts`.
    home: str


@dataclass(frozen=True)
class Placement:
    name: str
    build: Callable[[Typed], Site]
    in_class: bool
    in_facade: bool
    # The rule that reports the slot, by the table of docs/design/09-contract-surfaces.md.
    owner: str
    # The facade holds a copy of the slot where it hands the carrier out under a public name: a
    # function, or a field of a dataclass-like class, defined outside the facade.
    copied: bool = False
    # A member of a class core defines is core's contract: no finding with any probe.
    cores_contract: bool = False
    # A field of a class core defines: a finding for a denied kind only.
    core_field: bool = False


TYPING_NAMES = ("TYPE_CHECKING", "Annotated", "NewType", "Protocol", "TypeVar", "TypedDict")


def indent(text: str, prefix: str) -> str:
    return "".join(prefix + line if line.strip() else line for line in text.splitlines(keepends=True))


def uses(text: str, name: str) -> bool:
    return re.search(rf"\b{name}\b", text) is not None


def module(body: str, typed: Typed, extra: str = "") -> str:
    """A module with the slot's type in `{T}` and its class-body lines in `{CB}` (indented)."""
    filled = body.replace("{T}", typed.annotation)
    filled = re.sub(r"( *)\{CB\}", lambda match: indent(typed.class_lines, match.group(1)), filled)
    text = typed.imports + typed.module_lines + filled
    lines = ["from __future__ import annotations", ""]
    if uses(text, "dataclasses"):
        lines.append("import dataclasses")
    typing = [name for name in TYPING_NAMES if uses(text, name)]
    if typing:
        lines.append(f"from typing import {', '.join(typing)}")
    if uses(text, "BaseModel"):
        lines.append("from pydantic import BaseModel")
    head = "\n".join(lines) + "\n" + extra + typed.imports
    return head + ("\n\n" + typed.module_lines if typed.module_lines else "") + "\n\n" + filled


KINDS = {
    "an exception": ("Rejected", "class Rejected(Exception):\n"),
    "a frozen dataclass": ("Info", "@dataclasses.dataclass(frozen=True)\nclass Info:\n"),
    "a pydantic model": ("Row", "class Row(BaseModel):\n"),
    "a Protocol": ("Hook", "class Hook(Protocol):\n"),
}
MEMBERS = {
    "a method parameter": "    def item(self, x: {T}) -> None:\n        pass\n",
    "a method return": "    def item(self) -> {T}:\n        raise ValueError\n",
    "an __init__ parameter": "    def __init__(self, x: {T}) -> None:\n        pass\n",
    "a property": "    @property\n    def item(self) -> {T}:\n        raise ValueError\n",
    "an annotated attribute": "    x: {T}\n",
}
HOMES = {False: ("errors", f"{D}/errors.py"), True: ("facade.contracts", f"{D}/facade/contracts.py")}


def member_of(kind: str, member: str, in_facade: bool) -> Placement:
    carrier, head = KINDS[kind]
    home, path = HOMES[in_facade]
    body = head + "    {CB}\n" + MEMBERS[member]

    def build(typed: Typed) -> Site:
        return Site({**typed.files, path: module(body, typed)}, carrier, home)

    where = "a class the facade defines" if in_facade else "a class handed out from another module"
    field = member == "an annotated attribute" and kind in ("a frozen dataclass", "a pydantic model")
    return Placement(
        f"{member} of {kind}, {where}",
        build,
        in_class=True,
        in_facade=in_facade,
        owner=SIGNATURE if in_facade or field else EXPORT,
        copied=field and not in_facade,
    )


def function(slot: str, in_facade: bool) -> Placement:
    signature = (
        "def {name}(x: {T}) -> None:\n    pass\n"
        if slot == "parameter"
        else "def {name}() -> {T}:\n    raise ValueError\n"
    )
    name, home, path = (
        ("get", "facade.api", f"{D}/facade/api.py") if in_facade else ("place", "errors", f"{D}/errors.py")
    )

    def build(typed: Typed) -> Site:
        return Site({**typed.files, path: module(signature.replace("{name}", name), typed)}, name, home)

    where = "a facade function" if in_facade else "a function handed out from another module"
    return Placement(
        f"a {slot} of {where}", build, in_class=False, in_facade=in_facade, owner=SIGNATURE, copied=not in_facade
    )


NESTED = {
    "a method return": "class Rejected(Exception):\n    class Detail:\n        {CB}\n        def item(self) -> {T}:\n"
    "            raise ValueError\n",
    "an annotated attribute": "class Rejected(Exception):\n    class Detail:\n        {CB}\n        x: {T}\n",
}


def nested(member: str, in_facade: bool) -> Placement:
    home, path = HOMES[in_facade]

    def build(typed: Typed) -> Site:
        return Site({**typed.files, path: module(NESTED[member], typed)}, "Rejected", home)

    where = "a class the facade defines" if in_facade else "a class handed out from another module"
    return Placement(
        f"{member} of a class nested in an exception, {where}",
        build,
        in_class=True,
        in_facade=in_facade,
        owner=SIGNATURE if in_facade else EXPORT,
    )


PRODUCT_ANCESTORS = {
    "a method return": (
        "class Base(Exception):\n    {CB}\n    def item(self) -> {T}:\n        raise ValueError\n",
        "Rejected",
        "class Rejected(Base):\n    pass\n",
    ),
    "a field": (
        "@dataclasses.dataclass(frozen=True)\nclass Base:\n    {CB}\n    x: {T}\n",
        "Info",
        "@dataclasses.dataclass(frozen=True)\nclass Info(Base):\n    total: int = 0\n",
    ),
}


def product_ancestor(member: str, in_facade: bool) -> Placement:
    base, carrier, child = PRODUCT_ANCESTORS[member]

    def build(typed: Typed) -> Site:
        if not in_facade:
            return Site({**typed.files, f"{D}/errors.py": module(base + "\n\n" + child, typed)}, carrier, "errors")
        contracts = module(child, Typed(""), extra=f"from {S}.errors import Base\n")
        return Site(
            {**typed.files, f"{D}/errors.py": module(base, typed), f"{D}/facade/contracts.py": contracts},
            carrier,
            "facade.contracts",
        )

    where = "a class the facade defines" if in_facade else "a class handed out from another module"
    field = member == "a field"
    return Placement(
        f"{member} inherited from a product ancestor, {where}",
        build,
        in_class=True,
        in_facade=in_facade,
        owner=SIGNATURE if field else EXPORT,
        copied=field and not in_facade,
    )


CORE_ANCESTORS = {
    "a method return": (
        "class CoreBase(Exception):\n    {CB}\n    def item(self) -> {T}:\n        raise ValueError\n",
        "Rejected",
        "class Rejected(CoreBase):\n    pass\n",
    ),
    "a field": (
        "@dataclasses.dataclass(frozen=True)\nclass CoreBase:\n    {CB}\n    x: {T}\n",
        "Info",
        "@dataclasses.dataclass(frozen=True)\nclass Info(CoreBase):\n    total: int = 0\n",
    ),
}


def core_ancestor(member: str, in_facade: bool) -> Placement:
    base, carrier, child = CORE_ANCESTORS[member]
    home, path = HOMES[in_facade]

    def build(typed: Typed) -> Site:
        product = module(child, Typed(""), extra=f"from {CORE} import CoreBase\n")
        return Site({**typed.files, CORE_PATH: module(base, typed), path: product}, carrier, home)

    where = "a class the facade defines" if in_facade else "a class handed out from another module"
    return Placement(
        f"{member} inherited from a core ancestor, {where}",
        build,
        in_class=True,
        in_facade=in_facade,
        owner=SIGNATURE,
        copied=not in_facade,
        cores_contract=member == "a method return",
    )


DEPTHS = {
    "a field at depth 1": ("Info", "@dataclasses.dataclass(frozen=True)\nclass Info:\n    {CB}\n    x: {T}\n"),
    "a field at depth 2": (
        "Outer",
        "@dataclasses.dataclass(frozen=True)\nclass Info:\n    {CB}\n    x: {T}\n\n\n"
        "@dataclasses.dataclass(frozen=True)\nclass Outer:\n    inner: Info\n",
    ),
    "a method at depth 1": (
        "Rejected",
        "class Rejected(Exception):\n    {CB}\n    def item(self) -> {T}:\n        raise ValueError\n",
    ),
}


def named_in_a_slot(depth: str, in_facade: bool) -> Placement:
    named, classes = DEPTHS[depth]

    def build(typed: Typed) -> Site:
        if not in_facade:
            body = classes + f"\n\ndef place() -> {named}:\n    raise ValueError\n"
            return Site({**typed.files, f"{D}/errors.py": module(body, typed)}, "place", "errors")
        api = f"from __future__ import annotations\n\nfrom {S}.errors import {named}\n\n\ndef get() -> {named}:\n    raise ValueError\n"
        return Site(
            {**typed.files, f"{D}/errors.py": module(classes, typed), f"{D}/facade/api.py": api}, "get", "facade.api"
        )

    where = "a facade function" if in_facade else "a function handed out from another module"
    return Placement(
        f"{depth} of a class the return of {where} names", build, in_class=True, in_facade=in_facade, owner=EXPORT
    )


CORE_NAMED = {
    "a core pydantic model": "class CoreThing(BaseModel):\n    {CB}\n    x: {T}\n",
    "a core frozen dataclass": "@dataclasses.dataclass(frozen=True)\nclass CoreThing:\n    {CB}\n    x: {T}\n",
}


def core_named_in_a_slot(kind: str, in_facade: bool) -> Placement:
    """A field of a dataclass-like class core defines, named in the return of a function."""

    def build(typed: Typed) -> Site:
        core = {CORE_PATH: module(CORE_NAMED[kind], typed)}
        signature = "() -> CoreThing:\n    raise ValueError\n"
        if not in_facade:
            errors = module(f"def place{signature}", Typed(""), extra=f"from {CORE} import CoreThing\n")
            return Site({**typed.files, **core, f"{D}/errors.py": errors}, "place", "errors")
        api = module(f"def get{signature}", Typed(""), extra=f"from {CORE} import CoreThing\n")
        return Site({**typed.files, **core, f"{D}/facade/api.py": api}, "get", "facade.api")

    where = "a facade function" if in_facade else "a function handed out from another module"
    return Placement(
        f"a field of {kind} the return of {where} names",
        build,
        in_class=True,
        in_facade=in_facade,
        owner=EXPORT,
        core_field=True,
    )


def placements() -> Iterator[Placement]:
    for in_facade in (True, False):
        for slot in ("parameter", "return"):
            yield function(slot, in_facade)
        for kind in KINDS:
            for member in MEMBERS:
                if kind == "a Protocol" and member == "an __init__ parameter":
                    continue
                yield member_of(kind, member, in_facade)
        for member in NESTED:
            yield nested(member, in_facade)
        for member in PRODUCT_ANCESTORS:
            yield product_ancestor(member, in_facade)
        for member in CORE_ANCESTORS:
            yield core_ancestor(member, in_facade)
        for depth in DEPTHS:
            yield named_in_a_slot(depth, in_facade)
        for kind in CORE_NAMED:
            yield core_named_in_a_slot(kind, in_facade)


PLACEMENTS = tuple(placements())


# ── Axis 3: how the name is handed out. ──
@dataclass(frozen=True)
class HandOut:
    name: str
    # The files a hand-out adds or replaces, from the site; None where the form does not apply.
    write: Callable[[Site], Files | None]


def from_home(site: Site, names: str) -> str:
    return f"from {S}.{site.home} import {names}\n"


def outside(write: Callable[[Site], Files]) -> Callable[[Site], Files | None]:
    """A form that hands out a name of a module outside the facade."""
    return lambda site: None if site.home.startswith("facade.") else write(site)


def listed(site: Site) -> Files:
    """`__all__` in a module with a function, so the list alone says what it hands out."""
    if site.home.startswith("facade."):
        path = f"{D}/{site.home.replace('.', '/')}.py"
        return {path: site.files[path] + f"\n\n__all__ = ['{site.carrier}']\n"}
    noop = "\n\ndef noop() -> None:\n    pass\n"
    return {f"{D}/facade/errors.py": from_home(site, site.carrier) + f"\n__all__ = ['{site.carrier}']\n" + noop}


def private(site: Site) -> Files:
    if site.home.startswith("facade."):
        path = f"{D}/{site.home.replace('.', '/')}.py"
        renamed = re.sub(rf"\b{site.carrier}\b", f"_{site.carrier}", site.files[path])
        return {path: renamed + f"\n\n__all__ = ['_{site.carrier}']\n"}
    return {
        f"{D}/facade/errors.py": from_home(site, f"{site.carrier} as _{site.carrier}")
        + f"\n__all__ = ['_{site.carrier}']\n"
    }


def second_module(site: Site) -> Files:
    """Two facade modules of the product hand the name out."""
    hand = from_home(site, site.carrier) + f"\n__all__ = ['{site.carrier}']\n"
    if site.home.startswith("facade."):
        return {f"{D}/facade/__init__.py": hand}
    return {f"{D}/facade/errors.py": hand, f"{D}/facade/more.py": hand}


def star(site: Site) -> Files:
    path = f"{D}/errors.py"
    return {
        path: site.files[path] + f"\n\n__all__ = ['{site.carrier}']\n",
        f"{D}/facade/errors.py": f"from {S}.errors import *\n",
    }


def named_by_another(site: Site) -> Files | None:
    """`facade/errors.py` hands out a class defined outside the facade; `facade/api.py` names it."""
    if site.home.startswith("facade.") or not site.carrier[0].isupper():
        return None
    use = from_home(site, site.carrier) + f"\n\ndef use(x: {site.carrier}) -> None:\n    pass\n"
    hand = from_home(site, site.carrier) + f"\n__all__ = ['{site.carrier}']\n"
    return {f"{D}/facade/errors.py": hand, f"{D}/facade/api.py": use}


HAND_OUTS = (
    HandOut("defined in the facade module", lambda site: {} if site.home.startswith("facade.") else None),
    HandOut("a named import", outside(lambda site: {f"{D}/facade/errors.py": from_home(site, site.carrier)})),
    HandOut(
        "an aliased import",
        outside(lambda site: {f"{D}/facade/errors.py": from_home(site, f"{site.carrier} as Handed")}),
    ),
    HandOut("__all__", listed),
    HandOut("a private name", private),
    HandOut(
        "two names",
        outside(
            lambda site: {
                f"{D}/facade/errors.py": from_home(site, f"{site.carrier}, {site.carrier} as Other")
                + f"\n__all__ = ['{site.carrier}', 'Other']\n"
            }
        ),
    ),
    HandOut("a star import", outside(star)),
    HandOut(
        "an assignment",
        outside(
            lambda site: {
                f"{D}/facade/errors.py": f"from {S} import errors\n\nThing = errors.{site.carrier}\n\n__all__ = ['Thing']\n"
            }
        ),
    ),
    HandOut("a second facade module", second_module),
    HandOut(
        "a private name, then a public name",
        outside(
            lambda site: {
                f"{D}/facade/errors.py": from_home(site, f"{site.carrier} as _{site.carrier}, {site.carrier}")
                + f"\n__all__ = ['_{site.carrier}', '{site.carrier}']\n"
            }
        ),
    ),
    HandOut("one facade module hands a class out, another names it in a slot", named_by_another),
)


# ── The trees. ──
@dataclass(frozen=True)
class Tree:
    placement: Placement
    wrapper: Wrapper
    hand_out: HandOut
    files: Callable[[Probe], Files]

    @property
    def name(self) -> str:
        return f"{self.placement.name} | {self.wrapper.name} | {self.hand_out.name}"


def tree_files(placement: Placement, wrapper: Wrapper, hand_out: HandOut) -> Callable[[Probe], Files]:
    def files(probe: Probe) -> Files:
        site = placement.build(wrapper.write(probe))
        written = hand_out.write(site)
        assert written is not None
        return {**site.files, **written}

    return files


def trees() -> list[Tree]:
    found = []
    for placement in PLACEMENTS:
        for wrapper in WRAPPERS:
            if wrapper.in_class_only and not placement.in_class:
                continue
            for hand_out in HAND_OUTS:
                site = placement.build(wrapper.write(MODEL_PROBE))
                if hand_out.write(site) is None:
                    continue
                found.append(Tree(placement, wrapper, hand_out, tree_files(placement, wrapper, hand_out)))
    return found


TREES = trees()


def probes_of(tree: Tree) -> tuple[Probe, ...]:
    """`Any` runs under the bare wrapper only: the other wrappers ask how a name is read, not what."""
    return PROBES if tree.wrapper.name == "bare" else (MODEL_PROBE, CLEAN_PROBE, UNRESOLVED_PROBE)


def runs_of(trees: list[Tree]) -> list[tuple[Tree, Probe]]:
    return [(tree, probe) for tree in trees for probe in probes_of(tree)]


# ── Many trees in one repo: one product per tree. ──
BATCH = 200


def relocate(files: Files, product: str) -> Files:
    """A tree written for `shop` moved to `product`, with a core module of its own."""
    moves = (
        ("products.shop.", f"products.{product}."),
        ("products/shop/", f"products/{product}/"),
        (CORE, f"{CORE}_{product}"),
        (CORE_PATH.removesuffix(".py"), f"{CORE_PATH.removesuffix('.py')}_{product}"),
    )
    moved = {}
    for path, text in files.items():
        for old, new in moves:
            path, text = path.replace(old, new), text.replace(old, new)
        moved[path] = text
    return moved


def batch_repo(trees: list[Files]) -> Files:
    """One repo holding each tree as product `t<index>`."""
    products = [f"t{index}" for index in range(len(trees))]
    apps = ", ".join(repr(f"products.{product}.backend") for product in products)
    repo = {
        "hoglint.toml": ROOT_CONFIG,
        "tools/hoglint/presets/posthog.toml": PRESET.read_text(),
        "posthog/__init__.py": "",
        "posthog/settings.py": f"INSTALLED_APPS = ['posthog', {apps}]\n",
        "products/__init__.py": "",
    }
    for product, files in zip(products, trees):
        repo.update(
            {
                f"products/{product}/__init__.py": "",
                f"products/{product}/backend/__init__.py": "",
                f"products/{product}/backend/models.py": MODEL.format(name="Order"),
                f"products/{product}/backend/facade/__init__.py": "",
            }
        )
        repo.update(relocate(files, product))
    return repo


Finding = tuple[str, str]


def findings_by_tree(out: Path, count: int) -> tuple[list[list[Finding]], list[Finding]]:
    """The findings of `RULES` per tree of a batch, as (rule, identity), and those of no tree."""
    per_tree: list[list[Finding]] = [[] for _ in range(count)]
    stray: list[Finding] = []
    for row in read_lines(out / "findings.jsonl"):
        if row[0] not in RULES:
            continue
        identity = " ".join(str(part) for part in row[1])
        match = re.search(r"\bproducts\.t(\d+)\.|\bposthog\.coreslot_t(\d+)\b", identity)
        if match is None:
            stray.append((row[0], identity))
            continue
        per_tree[int(match.group(1) or match.group(2))].append((row[0], identity))
    return per_tree, stray


def write_site_packages(site: Path) -> None:
    for path, text in SITE_PACKAGES.items():
        (site / path).parent.mkdir(parents=True, exist_ok=True)
        (site / path).write_text(text)


def run_batch(work: Path, monkeypatch: pytest.MonkeyPatch, trees: list[Files]) -> list[list[Finding]]:
    """The native engine over one batch; it must fail no file and give no finding outside a tree."""
    site = work / "site-packages"
    write_site_packages(site)
    for module_ in (facts, native_analysis):
        monkeypatch.setattr(module_, "site_packages", lambda: site)
    out = clean_dump(work, {}, batch_repo(trees), monkeypatch)
    per_tree, stray = findings_by_tree(out, len(trees))
    assert stray == []
    return per_tree


# ── The invariant. ──
def wanted(tree: Tree, probe: Probe) -> tuple[int, str, str]:
    """How many findings the tree must give with this probe, the rule that gives them, and what
    each of them names."""
    placement = tree.placement
    # The signature rule reads public names only: under a private name the export rule judges the
    # slot, whether the facade defines the carrier or holds a copy of it.
    private = tree.hand_out.name == "a private name" and (placement.copied or placement.in_facade)
    rule = EXPORT if private else placement.owner
    opaque = rf"\b{tree.wrapper.opaque}\b" if tree.wrapper.opaque else ""
    if placement.cores_contract:
        return 0, rule, ""
    if placement.core_field and (probe is not MODEL_PROBE or opaque):
        return 0, rule, ""
    if probe is CLEAN_PROBE:
        return (1, rule, opaque) if opaque else (0, rule, "")
    if probe is ANY_PROBE:
        return 1, rule, probe.named
    if probe is UNRESOLVED_PROBE:
        # An unknown is named by the name the reading stops at: the probe, or an alias of it.
        return 1, rule, ""
    return 1, rule, f"{opaque}|{probe.named}" if opaque else probe.named


def meets(tree: Tree, probe: Probe, found: list[Finding]) -> bool:
    count, rule, named = wanted(tree, probe)
    if len(found) != count:
        return False
    return all(found_rule == rule and re.search(named, identity) for found_rule, identity in found)


def verdict(found: list[Finding]) -> Verdict:
    return dict(Counter(rule for rule, _ in found))


# ── Named limits: trees that do not meet the invariant today, and exactly what they give. ──
@dataclass(frozen=True)
class Limit:
    """A group of trees with one cause. An empty axis holds every part of it."""

    why: str
    # The sentence of docs/design/09-contract-surfaces.md that states the limit, or "not written".
    page: str
    gives: Verdict
    placements: tuple[str, ...] = ()
    wrappers: tuple[str, ...] = ()
    hand_outs: tuple[str, ...] = ()
    probes: tuple[str, ...] = ()

    def holds_for(self, tree: Tree, probe: Probe) -> bool:
        parts = (
            (self.placements, tree.placement.name),
            (self.wrappers, tree.wrapper.name),
            (self.hand_outs, tree.hand_out.name),
            (self.probes, probe.name),
        )
        return all(not names or name in names for names, name in parts)


def placed(test: Callable[[Placement], bool]) -> tuple[str, ...]:
    return tuple(placement.name for placement in PLACEMENTS if test(placement))


NOT_WRITTEN = "not written"
# Rows are matched in order; the first that holds for a tree gives its verdict.
LIMITS: tuple[Limit, ...] = (
    Limit(
        why="a public module-level alias of a class in a facade module hands the class out under the alias, "
        "so the model is reported for that hand-out and for the slot",
        page="A top-level assignment of one bare name to a name or an attribute chain that resolves, whole, to a "
        "class or a function (`NotFound = errors.NotFound`, `Status = Run.Status`) hands that class or function "
        "out under the new name, by the clause of a definition: when the name is public or `__all__` lists it, "
        "whether or not the module has a function (Nov 13).",
        gives={EXPORT: 1, SIGNATURE: 1},
        # The slot sits in the facade module itself, so the alias does too.
        placements=placed(
            lambda placement: placement.in_facade and placement.owner == SIGNATURE and "inherited" not in placement.name
        ),
        wrappers=("a module-level alias", "an alias of an alias", "a type statement"),
        hand_outs=("defined in the facade module", "__all__", "a second facade module"),
        probes=("model",),
    ),
    Limit(
        why="each facade module that hands the carrier out holds a copy, and each copy is a slot of its own, "
        "reported by the signature rule",
        page=NOT_WRITTEN,
        gives={SIGNATURE: 2},
        placements=placed(lambda placement: placement.copied),
        hand_outs=("a second facade module",),
    ),
    Limit(
        why="a member with no copy is reported once per facade module that hands out or names its class",
        page="A member that has no copy (a method of a handed-out exception) stays one `export-shape` row per "
        "module that names the class.",
        gives={EXPORT: 2},
        placements=placed(lambda placement: placement.owner == EXPORT),
        hand_outs=("a second facade module", "one facade module hands a class out, another names it in a slot"),
    ),
    Limit(
        why="the facade holds one copy per public name, and each copy is a slot of its own, reported by the "
        "signature rule",
        page=NOT_WRITTEN,
        gives={SIGNATURE: 2},
        placements=placed(lambda placement: placement.copied),
        hand_outs=("two names",),
    ),
)


def limit_of(tree: Tree, probe: Probe) -> Limit | None:
    """The limit row of a run; a run the invariant wants no finding from is never a limit."""
    if wanted(tree, probe)[0] == 0:
        return None
    return next((limit for limit in LIMITS if limit.holds_for(tree, probe)), None)


# ── What the suite runs. ──
SEED = 20261113
SAMPLE = 200


def selected() -> list[Tree]:
    """Every tree with `HOGLINT_SLOTS_FULL=1`. Else every placement with every wrapper under one
    hand-out form, every placement with every hand-out form under the bare wrapper, and a fixed
    sample of the rest."""
    if os.environ.get("HOGLINT_SLOTS_FULL") == "1":
        return TREES
    plain = ("a named import", "defined in the facade module")
    core = [tree for tree in TREES if tree.hand_out.name in plain or tree.wrapper.name == "bare"]
    rest = [tree for tree in TREES if tree not in core]
    return core + random.Random(SEED).sample(rest, SAMPLE)


RUNS = runs_of(selected())
BATCHES = [RUNS[at : at + BATCH] for at in range(0, len(RUNS), BATCH)]


@pytest.fixture(scope="module")
def batch_findings(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Callable[[int], list[list[Finding]]]]:
    """The findings of a batch, per run, computed the first time a test of the batch asks."""
    done: dict[int, list[list[Finding]]] = {}
    monkeypatch = pytest.MonkeyPatch()

    def of(index: int) -> list[list[Finding]]:
        if index not in done:
            work = tmp_path_factory.mktemp(f"batch{index}")
            done[index] = run_batch(work, monkeypatch, [tree.files(probe) for tree, probe in BATCHES[index]])
        return done[index]

    yield of
    monkeypatch.undo()


def run_params() -> list[object]:
    return [
        pytest.param(index, at, id=f"{tree.name} | {probe.name}")
        for index, batch in enumerate(BATCHES)
        for at, (tree, probe) in enumerate(batch)
    ]


@pytest.mark.parametrize("index, at", run_params())
def test_a_model_in_a_slot_is_reported_exactly_once(
    batch_findings: Callable[[int], list[list[Finding]]], index: int, at: int
) -> None:
    tree, probe = BATCHES[index][at]
    found = batch_findings(index)[at]
    limit = limit_of(tree, probe)
    if limit is not None:
        assert verdict(found) == limit.gives, limit.why
        return
    count, rule, named = wanted(tree, probe)
    assert len(found) == count, found
    for found_rule, identity in found:
        assert found_rule == rule, found
        assert re.search(named, identity), identity


@pytest.mark.parametrize("limit", LIMITS, ids=[limit.why for limit in LIMITS])
def test_every_limit_holds_for_a_tree(limit: Limit) -> None:
    assert any(limit.holds_for(tree, probe) for tree, probe in runs_of(TREES))
