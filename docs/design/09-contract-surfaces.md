# 9. Contract surfaces: what an export hands out

> Design record by topic, compiled Nov 11, 2026, from the decision log and the research.
> Current as of Nov 11, 2026.

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
  A star import hands out its source's public names (N3b review, items 4 and 5).
- A lazy name has an import edge, written by the core producer: an import of the name from the module the lazy map names, at the map's line (Nov 12).
  Its context is `function_local`, because `__getattr__` runs the import when the name is first read, not when the module is imported; its attr `lazy` tells it apart.
  So every rule that reads what a module hands out finds it with no special path, and every edge rule judges it as it judges any other import.
  A lazy entry whose module does not exist is an import edge that does not resolve and keeps its name, so the hand-out is unknown; only an entry whose name and value read as a name and a dotted path counts, so another dict of text is no lazy map (Nov 12).

**Signature slots** (architect, D62; the key was named in N3b):
- `[shapes.<name>] signatures = true` says signatures are part of the contract.
  The PostHog preset sets it on `contract`, not on `registration`.
- In such a surface, core exposes every slot with every type name, builtins included (`builtins.dict`, `typing.Any`), and `annotation=missing` for an unannotated parameter.
- The identity names the callable.
  The old producer's identity had `-` there in 1,673 of 2,738 ledger lines.
- Receivers (`self`, `cls`) are left out by position in a method, not by name.

**Dataclass fields** (D62 C1 and C4, D63, N3b review item 7):
- `dataclass_decorators` is a global `[discovery]` setting.
  The PostHog preset adds `posthog.dataclasses.frozen` and `pydantic.dataclasses.dataclass`.
- A decorator is resolved, not matched by spelling: `dataclass as stdlib_dataclass` counts.
- Fields along the MRO count.
- **Fields of a handed-out class count wherever the class is defined** (owner, D63, Nov 11).
  A product may define its types in its own modules and import them thinly into the facade, as long as its layers still hold.
  This added 1,742 rows and 94 new findings in 18 products.

**Aliases** (N3b review item 3): an alias is expanded wherever it is defined, imported aliases included (depth 32, stop on a cycle).
The reasoning matches D63: where something is defined must not change the verdict.
**An alias whose expansion is known is not a finding** (D70, Nov 11; reverses D62 C3).

**One fact, one place:** a handed-out definition is reported once, at its own surface module (N3b review item 8: -75 findings).

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

**What an export may hand out besides data** (the same batch).
- Exception classes (core shape `exception`: the lineage holds one of Python's own exceptions) and pydantic models (a preset kind) are allowed contract types: callers catch a facade's errors, and the doctrine allows `BaseModel` where aliases are needed.
- One slot, one rule: with `slots = "shape"` the export rule judges only a class's shape in a slot of what it hands out. A model, an unknown type, a missing annotation or an unresolved name there is the signature rule's finding, under its own identity. The fields of a class found in a slot are still judged in full by the export rule.
- A pydantic model's annotated class attributes are its fields (`[discovery] dataclass_bases`), so a model typed there is a finding like one in a dataclass field.

**The fixture module is outside the contract** (the doctrine names `facade/testing.py` as the place for fixture-only seeding).
Other packages import it, so it stays in the export and its audience holds; but the contract rules (`export-shape` by its `from`, `facade-signature`, `facade-logic`, `facade-no-drf`) do not judge it: its helpers take and return models on purpose.

**Importing a contract module runs no Django** (D93, as decided on Nov 12).
- The rule follows what runs when the contract module is imported: its module-level imports and theirs, through other modules.
  An import inside a function body runs later, so it is left out, and so is a type-only import.
- Known gap: an import of `a.b.c` also runs the `__init__` of `a` and `a.b`; the reach does not follow that, so a package `__init__` that loads Django is not seen.

**A capability submodule holds no body** (D94 and the port back to the old check, Nov 12).
- A capability submodule hands out wiring: code core runs, such as tasks, workflows and query runners, or model classes.
  The preset says which modules are one: a list of module names at the top of the facade, and any module whose hand-outs come from a wiring zone (`src.handed_zones`, the zones of the modules a module hands out names from).
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
