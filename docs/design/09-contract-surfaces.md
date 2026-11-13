# 9. Contract surfaces: what an export hands out

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 13, 2026.

A PostHog facade (`products/<name>/backend/facade/`) is one export in hoglint's terms.
The engine's word is `export`; "facade" is a name in the PostHog preset (D48).

## Decided

**An export is a door plus a shape** (round 3, Nov 6; a core concept, owner, Nov 6; agreed by exclusion Nov 9).
- A named export has symbols or paths, an audience, and a mandatory shape policy.
  An export without a shape policy is a config error.
- Shape is checked recursively (fields, signatures, bases, through re-exports).
  `Result(model: Team)` fails even inside a dataclass.
- Presentation splits into `registration`, `presentation-adapter` (by base class plus an audience; owner, Nov 9) and private code.
- An export can name its members exactly with `symbols`: only the listed symbols are in it, not the classes that inherit them (T3).
  An export by base class holds every subclass too, so a viewset that uses a listed mixin is itself an adapter.
  The PostHog preset uses `symbols` for viewsets that have not moved to `presentation/` yet: they may use the five access-control mixins and nothing else.
  The five names are a closed list.
  A sixth needs its name added to that export in the preset, through code-owner review like any other policy line.

**The public surface is first class in the engine** (owner, Nov 10; D48).
The owner's principle: a facade, or whatever other patterns call it, is a first-class concept.
It was built as phase N3b in the hoglint repo (7f26c49, then fix rounds up to 4719fd5, verified on Nov 11).

**What "hands out" means** (architect, D62 point 4, D64):
- `__all__` in every literal form, and a form built from a module-level literal such as `sorted(_LAZY)`;
- `import x as x`;
- `import a.b as name`, when a clause holds for `name` (Nov 12); a plain `import a.b` binds `a` and hands out nothing;
- lazy names;
- every import of a module that has no top-level function, except a name the module's own code uses (D64).
  That exception removed 86 findings; 60 of them were the `frozen` decorator reported as part of its own module's contract.
- An unreadable `__all__` leaves an unresolved marker.
  So does a handed-out import that resolves to nothing, such as a relative import above the top package: its edge keeps the name it binds (Nov 12).
  A star import hands out its source's names: its `__all__`, else its public names with those of its own star imports, never a name a `from __future__ import` binds (N3b review, items 4 and 5; Nov 13).
  In an export module each name a star binds is an import edge of its own, resolved as a named import of the name, at the star's line, with attr `star`; the star's own edge stays.
  So a star hands out what the same names imported by name hand out, to every rule.
  A star whose source, or a source it star-imports, cannot be read hands out `*`, and the export rule reports it as an import that did not resolve.
- A top-level assignment of one bare name to a name or an attribute chain that resolves, whole, to a class or a function (`NotFound = errors.NotFound`, `Status = Run.Status`) hands that class or function out under the new name, by the clause of a definition: when the name is public or `__all__` lists it, whether or not the module has a function (Nov 13).
  A class nested in a class counts.
  In an export module it has an edge of the kind `rebind` (`binds` the name), so no rule that reads imports sees a second edge for one name; the export rule, the copies for the signature rule and `handed_zones` read it.
- Every name a readable `__all__` lists is accounted for (Nov 13): a def or a class, an import or another hand-out edge (lazy, star, `rebind`), or a literal constant (a number, a string, `None`, a bool, or a tuple, list, set or dict of those).
  Any other listed name is unknown: the export rule reports `export-unknown` for it.
  A def or class with a leading `_` that `__all__` lists is handed out as a public one is: the export rule judges it, and in a contract module its slots get contract-mode rows.
  The signature rule reads public callables only, so the export rule judges such a def's slots in full.
- What a hand-out by star or by assignment does not read, in plain words:
  1. Star: a source that makes its names by code (`globals().update(...)`, a loop) is read by its table, so those names are not seen; `__all__ = a.__all__ + b.__all__` cannot be read, so it is unknown. A name a source binds under a condition or deletes again is read as bound. In a module with a function and no `__all__` a star hands out nothing, as a named import there hands out nothing (D64). Outside export modules a star import stays one edge.
  2. Assignment: a member past a class (`Account.DoesNotExist`, `Order.objects`, `Status.choices`), a call (`acreate = sync_to_async(create)`), a list of classes, a tuple target (`A, B = X, Y`), two targets (`A = B = X`) and the first of two assignments to one name are not handed out. Not listed, they are not judged anywhere; listed in `__all__`, they are unknown. A generic or union alias (`Thing = list[Order]`) is judged where a slot names it, where the producer expands it.
  3. Listed names: a negative number (`-1`) is an operation in the value tree, not a literal, so a listed one is unknown. A value that names a type by a builtin (`Key = str`) does not resolve to a class the engine reads, so a listed one is unknown.
- A lazy name has an import edge, written by the core producer: an import of the name from the module the lazy map names, at the map's line (Nov 12).
  Its context is `function_local`, because `__getattr__` runs the import when the name is first read, not when the module is imported; its attr `lazy` tells it apart.
  So every rule that reads what a module hands out finds it with no special path, and every edge rule judges it as it judges any other import.
  A lazy entry whose module does not exist is an import edge that does not resolve and keeps its name, so the hand-out is unknown; only an entry whose name and value read as a name and a dotted path counts, so another dict of text is no lazy map (Nov 12).
  A relative value (`'.foo'`, read by `import_module(value, __package__)`) is anchored at the module's package, as a relative import is; one that does not resolve counts only in a dict where another entry resolves.
- What a lazy map can say that hoglint does not read, in plain words:
  1. A map where no entry resolves and every value is one bare name (`{'Foo': 'foo'}`): it hands out nothing, because a dict of plain words looks the same.
  2. A value that names the attribute itself (`'pkg.mod:Name'`): it hands out nothing.
  Where `__all__` lists such a name, the name is unknown (Nov 13).

**Signature slots** (architect, D62; the key was named in N3b):
- `[shapes.<name>] signatures = true` says signatures are part of the contract.
  The PostHog preset sets it on `contract`, not on `registration`.
- In such a surface, core exposes every slot with every type name, builtins included (`builtins.dict`, `typing.Any`), and `annotation=missing` for an unannotated parameter.
  A starred part (`tuple[int, *tuple[Order, ...]]`, `*args: *tuple[Order, int]`) is read through.
  A part that is no type expression (an operator other than `|`, a conditional expression, a call, `or`, a display, a string that does not parse) gives the slot one row with `annotation=unread`, in every file and in an alias's value too, so the slot is unknown where a rule judges it (Nov 13).
  `Annotated[X, ...]` is read as `X`: its metadata is no type and is not read; so are a `Literal`'s values and `...`.
- The identity names the callable.
  The old producer's identity had `-` there in 1,673 of 2,738 ledger lines.
- Receivers (`self`, `cls`) are left out by position in a method, not by name.
- An annotated class-body attribute of a class that is no dataclass is a slot `attr:<name>` of the class, written where the class is defined (Nov 13).
  It is written as a method's slots are: every type name in a surface file, only a name that is no plain builtin elsewhere.
  Its names, and a field's, are looked up as a method's are: in the class body first, then the module, as Python does; a bare annotation (`uuid: uuid.UUID`) binds no name.
  A `ClassVar` is an attribute too, of a dataclass-like class as well: it is no field of `__init__`, callers read it.

**Dataclass fields** (D62 C1 and C4, D63, N3b review item 7):
- `dataclass_decorators` is a global `[discovery]` setting.
  The PostHog preset adds `posthog.dataclasses.frozen` and `pydantic.dataclasses.dataclass`.
- A decorator is resolved, not matched by spelling: `dataclass as stdlib_dataclass` counts.
- Fields along the MRO count.
- **Fields of a handed-out class count wherever the class is defined** (owner, D63, Nov 11).
  A product may define its types in its own modules and import them thinly into the facade, as long as its layers still hold.
  This added 1,742 rows and 94 new findings in 18 products.

**Aliases** (N3b review item 3): an alias is expanded wherever it is defined, imported aliases included (depth 32, stop on a cycle).
The producer expands an alias in every file (Nov 13): in a slot outside a surface file too, where its expansion's rows follow that mode's filter (a name that resolves to a plain builtin gets no row).
An alias the module binds more than once (`if TYPE_CHECKING: Ref = Order` / `else: Ref = int`, or the arms of a `try`) stands for every binding: its expansion is the union of every binding's expansion, in source order, each binding's names looked up where it is assigned.
When one of the bindings is no type alias (a call, a class, an import), the name is no alias and is unknown, as a single such binding is.
A name in a slot the export rule judges in full that is no class, no function and no alias the producer expanded is unknown, as the signature rule reads the same name in a facade function.
That holds whatever else the module does with the name: a module that hands the name out itself does not make its slot clean.
That holds for a name at the top of a module (a `NewType`, a `TypeVar`, a functional `TypedDict`, a value built by a call) and for a name assigned in a class body (`Ref = Order` in a class, then `-> Ref` in its method or as the type of its attribute or field): the producer does not expand a class-body alias, so it is unknown, not clean, also next to a module-level name of the same spelling.
An alias nested deeper than the producer follows is one unknown row, its marker.
The test is made for each name of the slot: an expanded alias marks only itself, so a name next to it in the slot, or inside its value, is still asked (`Maybe = Ref | None` with `Ref` a `NewType` is unknown).
A name that one slot leaves to a signature rule is still judged where another slot reaches it and the export rule judges that slot.
The reasoning matches D63: where something is defined must not change the verdict.
**An alias whose expansion is known is not a finding** (D70, Nov 11; reverses D62 C3).

**One fact, one place:** a handed-out definition is reported once, at its own surface module (N3b review item 8: -75 findings).
This holds for a class a facade module defines.
It holds too for a class defined outside the facade that one facade module hands out and another facade module names in a slot: the copy at the first module is the signature rule's, and the export rule leaves the slot to it.
Written limits:
- A class another product's facade hands out stays reported by both rules, one row per product: a product passes or fails by its own files only.
- A module that binds one name to two classes can leave a slot to the copy of the other class, because the copy is found by its name.
- A member that has no copy (a method of a handed-out exception) stays one `export-shape` row per module that names the class.

**Which models may cross a contract surface is preset policy** (owner, D65, Nov 11).
The owner decided that a preset holds a named list of future-proof core models that may cross.
Any model that core owns is not enough, because a core model may later move into a product.
Which models a company lists is its own policy and is not published.
The preset keeps the list once, as a symbol label, and both contract rules read it: the signature rule (`unless`) and the shape policy (`allow_where`, a symbol it holds for is allowed as it stands).
So the verdict on a listed model does not depend on how deep it sits, in a slot or in a field of a class a slot names.

**What a facade signature may name** (the rule-change batch of Nov 12, after the findings triage).
- A slot type is unknown when it is `Any`, `object`, a bare `dict` or no annotation.
  `object` stays unknown because a slot typed `object` would let any model cross unseen.
  A mapping given type arguments (`dict[str, str]`) is typed.
- `Any` as an argument of a mapping (`dict[str, Any]`) is an opaque payload: allowed in a parameter and a field, a finding in a return value.
  `Any` in any other generic (`list[Any]`, `Optional[Any]`) stays unknown.
  The slot row of a type argument names the generic it is given to (`argument_of`), so a rule can tell the two apart.
- A contract may hand out a `Protocol` (a hook's callback type) and core's query syntax tree (the preset names its base as a kind).
  An allowed shape covers the class itself; its fields and, in a contract, its methods are still judged when the product defines the class.
  A class core defines (its query syntax tree, its Protocols) is core's contract: a product's facade hands it out as it stands (`walk_where`).
  One rule decides which members of a class are read (Nov 13): `walk_where` decides the methods and attributes of a class, the handed-out class and each ancestor alike, a `ClassVar` included.
  So the methods of a frozen dataclass core defines, handed out by a product's facade, are core's contract and are not judged, as the methods a product class inherits from a class core defines are not.
  The fields of a frozen dataclass and of a pydantic model are always read, wherever a slot names the class and whoever defines it: they are constructor slots of the class, and of a class that inherits them.
  A dataclass core defines that is no data shape (its query syntax tree) is core's contract as a whole: `walk_where` decides its fields too.

**What an export may hand out besides data** (the same batch).
- Exception classes (core shape `exception`: the lineage holds one of Python's own exceptions) and pydantic models (a preset kind) are allowed contract types: callers catch a facade's errors, and the doctrine allows `BaseModel` where aliases are needed.
- One slot, one rule (Nov 13): the export rule names the signature rules it leaves slots to, `leave_slots_to = ["facade-signature"]` in the preset.
  Each name must be a signature rule of the config; any other name is a config error.
  The named rule's selection must split into conditions that read only `src.*` fields (where it reads rows) and conditions that read none (which slots it judges), joined by `and`; a condition that mixes the two is a config error.
  A row is left to the rule only where its contexts take the row's, as the rule itself reads it.
  The export rule leaves a slot to a named rule only where that rule reads the slot's row for this walk: the row's own module is one the rule's source selection holds for, or such a module holds the producer's copy of the slot (a handed-out function, a data shape's fields, a contract dataclass's inherited fields), under the name it is handed out or defined by.
  The copy is looked for in every file of the same export and the same captured root as the module in hand (the same product's facade), under each public name such a file hands the class out by, and in the module that defines the class; one index of these hand-outs is built before the walk.
  A private name holds no copy the rule reads, and a file of another export or another root is not asked.
  That module must also be a surface of the shape policy the walk runs under: a registration surface that returns a contract class judges that class's fields by its own policy, in full.
  There it judges only the shape of a class in the slot; a model, an unknown type, a missing annotation or an unresolved name is the signature rule's finding, under its own identity.
  Every other slot it judges in full, as it judges a private method.
  The same test holds at every depth: a field of a class found in a slot is left to the signature rule where it reads that field's row, so a contract class that one facade module defines and another facade module's function returns is reported once, where the class is defined.

  | Slot | Reported by |
  |---|---|
  | A public function, method or attribute of a class the facade defines | `facade-signature` |
  | A function or a data shape's fields handed out from outside the facade (the copy at the facade) | `facade-signature` |
  | A public method, `__init__`, property or attribute of an allowed class handed out from outside the facade | `export-shape`: one row per type and module, the trail in the message |
  | A private method of a walked class | `export-shape` |
  | A method or attribute (a `ClassVar` too) of a class core defines, handed out or inherited | none: core's contract |
  | A field of a frozen dataclass or a pydantic model core defines, handed out, inherited or named in a slot | as a field of the handed-out class |
  | A member of a class named in a slot (one level deeper) | as the rows above, by where its row is read |
  | Every slot above, at every depth | judged by one slot policy: the named rule's selection |

  A slot has one policy, the named rule's, at every depth and whoever reports it: where the export rule judges a slot row in full, it first reads that row with the parts of each named rule's selection that do not read only the source. Where every named rule's selection is false for the row, the type the row names is not judged as unknown there either.
  So an opaque payload (`Any` as a mapping's argument) in a parameter or a field is clean in a member of a class handed out from outside, as it is in a facade function, and a finding in a return value.
  A model, a class of no allowed shape, an unresolved name and a missing annotation are judged as before.

  For example, a facade module hands out `OrderRejected`, an exception its product defines in a logic module, and `OrderRejected.order()` returns another product's model: `export-shape` reports the model at the facade module, with `OrderRejected -> Order` in the message.
- **A written limit** (Nov 13): outside a surface file, a bare `dict`, a bare `object` and a missing annotation in a method of a handed-out class have no row, so no rule judges them.
  Rows outside a surface file are written only for a name that is no plain builtin; a bare `Any` (`typing.Any`) is no builtin, so it has a row and is judged.
  A class nested in a class of a surface file is read in plain mode, so the same holds there.
  The model repository keeps both as wanted cases (`ME09`, `ME10`).
- A pydantic model's annotated class attributes are its fields (`[discovery] dataclass_bases`), so a model typed there is a finding like one in a dataclass field, also when core defines the pydantic model and a slot names it.
- **A base that is no class makes the lineage unknown** (Nov 13).
  The producer marks an inherit edge whose base is a call (`class Rejected(Exception, make())`, also `make()[T]`) with `called = "1"`; the edge stays resolved to the callee, because the class statement does call it.
  Ancestry reads such a base as unknown, and so a base that resolves to first-party code that is not declared as a class, such as a function.
  A base that resolves to a first-party name that is no class, or through a name the module binds more than once, is followed by the producer through every module-level binding of that name, which the module table keeps beside the binding a lookup reads.
  A binding is known when its value is a name or an attribute chain that resolves to a class (a third-party name counts as a class, as a third-party base does), or a subscript of one (`X = Base[A, B]` stands for `Base`); a name bound once per branch (`if TYPE_CHECKING: X = Real` / `else: X = object`, or the arms of a `try`) is known when every binding is, and stands for every class its bindings name.
  The producer writes the result on the inherit edge as `classes` (the classes, sorted, or `?`); the edge's target stays the lookup's.
  Anything else is unknown: a name bound by a call (`Base = make()`), a binding with another value, a name with one known and one unknown binding, a cycle, a chain deeper than the alias depth.
  A class keeps its declaration when a pack gives it another kind: a model's abstract model base is a class.
  A subscript is read through to its head, so a generic base (`Generic[T]`, `Source[Config]`) stays a class.
  An unknown base gives the class an incomplete lineage: the export rule reports a handed-out class with one as `export-unknown`, and a contract dataclass with a call base gets an unresolved `fields` row for the fields that base may add.
  Written limits: a third-party base written as a plain name cannot be told from a third-party function (`from lib import make_base as Base`); a metaclass given by a call and a class decorator that returns another class are not read; a subscript of a third-party name is read as a generic class.
  The model repository has the call base, the base bound by a call and a branch-bound base as `ES20`, and the metaclass as a wanted case (`ES21`).

**The fixture module is outside the contract** (the doctrine names `facade/testing.py` as the place for fixture-only seeding).
Other packages import it, so it stays in the export and its audience holds; but the contract rules (`export-shape` by its `from`, `facade-signature`, `facade-logic`, `facade-no-drf`) do not judge it: its helpers take and return models on purpose.

**Importing a contract module runs no Django** (D93, as decided on Nov 12).
- The rule follows what runs when the contract module is imported: its module-level imports and theirs, through other modules.
  An import inside a function body runs later, so it is left out, and so is a type-only import.
- Known gap: an import of `a.b.c` also runs the `__init__` of `a` and `a.b`; the reach does not follow that, so a package `__init__` that loads Django is not seen.

**A capability submodule holds no body** (D94 and the port back to the old check, Nov 12).
- A capability submodule hands out wiring: code core runs, such as tasks, workflows and query runners, or model classes.
  The preset says which modules are one: a list of module names at the top of the facade, and any module whose hand-outs come from a wiring zone (`src.handed_zones`, the zones of the modules a module hands out names from, by import or by assigning a name again).
  The data module of a facade and its contract, enum and fixture modules never are one.
- Each top-level definition with a body in such a module is one finding, named by its symbol, not a count per module.
  A function in the facade's data module is never a finding of this rule: adding one is how a product offers something new.
- Known gap: the old check also flagged a stub that carries a task or workflow decorator. The rule reads one edge at a time and does not see the decorator.

## Why

- Publicness by location turned Packwerk's `app/public` into a catch-all drawer.
  A door without a shape repeats that.
  This is the owner's standing doctrine: publicness comes from the shape of what is handed out, never from the folder.
- D62: the N3b build agent stopped before coding and showed that its brief was wrong.
  With the facade producer taken out, 2,946 findings would go, 1,540 of them on builtin types that only the producer supplied.
- Signatures per shape: a facade is a call contract.
  A presentation adapter is exported to be subclassed; its method signatures belong to the web framework.
  Count with plain `ast` (Nov 10): facades 43 of 2,578 public functions had an unannotated parameter; registration files 0 of 79; presentation code 400 of 2,808.

## Considered and rejected

| Idea | Why rejected | Who, when | Source |
|---|---|---|---|
| Publicness by location | A catch-all drawer | Round 3; by exclusion Nov 9 | `docs/research/exports-and-shapes.md` |
| Exports as a preset macro | Keep them in core, with framework meaning in packs | By exclusion Nov 9 | `docs/research/exports-and-shapes-second-opinion.md` |
| Treat all three surfaces alike for unannotated parameters | The wrong design: adapters are subclassed, not called | Architect, Nov 10 | `docs/history/decision-log-3.md` |
| N3b as "three additions" only | The summary of the study dropped builtins | Architect corrected it, D62 | `docs/history/decision-log-3.md` |
| Copy the old producer's alias special case into core | Core does not inherit a producer's special case | Architect, D62 C3 | `docs/history/decision-log-3.md` |
| An alias names itself and its expansion (D62 C3) | Triage: noise when the expansion is known | Reversed by D70 | `docs/history/decision-log-3.md` |
| Any model core owns may cross | Core may later move a model out | Owner, D65 | `docs/history/decision-log-3.md` |
| A separate rule for 108 repeated facts | Not worth a rule; then fixed by review item 8 | Architect, Nov 11 | not published |

## What would change the answer

- Turning signatures on for registration files would add no finding (all 79 functions were annotated on Nov 10).
- Narrowing the no-functions clause further changes what the old producer reported, so it is the owner's call (N3b review item 10).

## Open

- **The per-surface signature question** (parked Nov 10, to come back with numbers): whether unannotated parameters on surfaces other than the facade count as unknown (D48 point 4).

## Sources

`docs/research/exports-and-shapes.md`; `docs/research/exports-and-shapes-second-opinion.md`; `docs/research/facade-facts-map.md`; `docs/history/decision-log-3.md` (D48, "D48 in detail", "D48, point 4 refined", D62-D65, "Code review of N3b", D70); not published (the design page, the progress log, the triage reports).
