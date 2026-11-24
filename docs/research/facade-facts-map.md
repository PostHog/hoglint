# Facade facts map

> A read-only map of the PostHog pack's facade producer (`posthog.facade-facts`) and of who reads its edges, Nov 10, 2026; the input to moving facade facts into the engine (N3b).
> Replaced: N3b built facade facts into the engine as its export analysis (D48, D62-D64); see `docs/design/09-contract-surfaces.md` and `docs/history/decision-log-3.md`. File and line references point at the Hoglint code of Nov 10 and are stale.

Paths: F = `hoglint/presets_py/posthog/facade.py`, L = `hoglint/presets_py/posthog/legacy_ledger.py`, P = `hoglint/presets_py/posthog/pack.py`, T = `presets/posthog.toml`.
Read only; nothing was run.

Two facts change the design:
(a) no engine rule reads an `export` edge, and
(b) core already emits `declares`, `decorate`, `inherit` and `exposes` for every module.

## 1. What facade.py produces

Producer id `posthog.facade-facts@1` (F:30), contexts {PROD} (F:35,319), `use`/`shape` empty.
Every edge gets `origin` (qualified name) and `origin_kind` (class|func|module|assign|external|unresolved|nested) appended last (F:311).
Edge build: F:309-324.
- Targets and resolution (F:296-301): unresolved -> `()`, UNRESOLVED; external -> `(entity(first name outside repo),)`, EXTERNAL; else `(entity(origin),)`, RESOLVED. `via` = chain hops minus surface and origin (F:303).
- Facade modules: F:271-281, files `products/<p>/backend/facade/**.py`, not `test_*`/`*_test.py`. Order per module (F:529-539): declares, signatures_of, exports, reexported_signatures. Modules in `reader.files` order.

| kind | site (module, scope, line) | surface | attrs, in order | where |
|---|---|---|---|---|
| declares | facade module, def name, def line | `<facade>.<name>` | def_kind (def/async/class), stub, wiring_decorator (true/false), origin, origin_kind (class/func); no via | F:495-504 |
| exposes (own defs) | facade module, symbol (`f`, `C.m`, `C.__init__`), line 0 | one edge per qualified type name in the slot; `""` when unannotated | signature (ordinal), slot (`return`, `param:<n>`), whole_any, [annotation=missing], origin, origin_kind | F:414-457 |
| exposes (dataclass fields) | same, symbol `C.__init__` | as above | signature=`field-<n>`, slot=`param:<field>` (not `field:`) | F:459-472 |
| exposes (re-exported function) | facade module, bound name | as above | reexport, rel, hops, defined_here, signature, slot, whole_any, [annotation], origin, origin_kind | F:474-491 |
| export (handed name) | facade module, bound name, line 0 | `<mod>.<name>` imported | bound, rel, mode (module/all/alias/lazy), [members = N or "unknown", only when origin_kind=assign], origin, origin_kind | F:394-402 |
| export (collection member) | facade module, parent bound | member qualified name | bound, rel (parent's), member_of, origin, origin_kind | F:405-408 |
| decorate | DEFINING module of the class, class name, class line | qualified decorator, `""` if unknown | origin, origin_kind only | F:518-521 |
| inherit | same site as decorate | qualified base | position, origin, origin_kind | F:522-527 |

Notes.
- `exposes` skips `_` names (F:447,454; `__init__` kept), `self`/`cls` (F:439), ClassVar (F:470). It emits annotation=missing for an unannotated param (F:423); a missing return emits nothing (F:436). It keeps builtins as external targets.
- `rel` is `backend/...` relative to the product, or `""` (F:266-269,345).
- `mode` (F:328-349): a facade module with no non-class top def is "pure" and every non-type-only import is `module`; else `__all__` -> `all`; else `import x as x` -> `alias`; plus `lazy` from the PEP 562 map.
- decorate/inherit run only for classes reached by an export (F:403,409) and recurse into first-party bases. `classes_done` is global, so each class is emitted once per run (F:508). Facade-declared classes that are not handed out get `declares` but no decorate/inherit.

## 2. Who reads what

| edge / attr | reader | note |
|---|---|---|
| `export` kind, all attrs (bound, rel, mode, members, member_of) | L:221,308,376,383,390 (`FacadeLedger`, `producer == FACADE_PRODUCER`); L:452 | Only reader. `export` is not in CORE_EDGE_KINDS (`hoglint/config/schema.py`:41-52) nor in T:7 `edge_kinds = ["drives"]`, so no rule can name it. T never lists `export` as a kind. `ExportPrimitive` never reads it: it says `edge.kind` is "export" only in its identity resolver (`hoglint/rules/exports.py`:289, `src/link/rules/export.rs`:644) |
| `origin`, `origin_kind` | L:234,351,381,392 | no engine reader |
| `rel` | L:264,309,323,391 | no engine reader |
| `bound`, `member_of`, `members`, `mode` | L:380,376-390 (`bound`, `member_of`, `members`); `mode` none | `mode` has no reader at all |
| `hops`, `defined_here`, `reexport`, `signature` | L:260-283 | no engine reader |
| `whole_any` | L:296 | no engine reader |
| `stub`, `wiring_decorator` | L:332 | no engine reader. T:254 reads `edge.body`/`edge.decl` (core attrs), absent on facade edges; a missing attr is `none`, and `none == '1'` is false (`hoglint/rules/base.py`:488, `hoglint/selectors.py`:485), so facade `declares` edges are skipped |
| `def_kind` | none | |
| `exposes` kind | `SignaturePrimitive` (`hoglint/rules/edges.py`:98-111; `src/link/rules/signature.rs`:64-95); T:241 `facade-signature` (select `src.zone == 'facade'`); `ExportPrimitive.exposes_under`/`member_handles` (`hoglint/rules/exports.py`:228-266, export.rs:543-588); `index.exposes` (`hoglint/index.py`:293-298, `native/rules.py`:667,676) | Pack edges are read as ordinary exposes edges |
| `slot` | edges.py:80,129,141; exports.py:271; export.rs:595; T:215 identity `facade-slot` | |
| `annotation` | edges.py:121; exports.py:270; export.rs:594, signature.rs:100 | only the facade emits `missing` (core skips unannotated, producers.py:380,472) |
| `callable` | edges.py:80,99; index.py:297; export.rs:214; T:215 | facade edges have none; the fallback is `site.scope` (the same string) |
| `inherit`, `decorate` kind | `SymbolFacts` (`hoglint/index.py`:121-131, `src/link/rules/symbols.rs`:41-55, `src/link/mod.rs`:539-582) | keys off attrs `class` / `decorated` / `frozen`. Facade edges lack them, so they are skipped. `edges_to("decorate")` in `hoglint/packs/django/installs.py`:109 and `signals.py`:244 reads only the site |
| `inherit`/`decorate` `position` | L:344 | only reader |
| `declares` kind | T:253 (core attrs); L:329 (facade attrs) | see above |
| `edge.slot`, `edge.whole_any`, `edge.stub`, `edge.wiring_decorator`, `edge.mode`, `edge.rel`, `edge.origin_kind`, `edge.members`, `edge.reexport`, `edge.hops`, `edge.defined_here`, `edge.annotation` as selectors | T: none (only `edge.label` T:212, `edge.body`/`edge.decl` T:255, identity `edge.callable`/`edge.slot` T:215). The monorepo's `hoglint.toml`: `edge.backend` only | |

Rust: `EdgeField::Attr` reads any attr by name (`src/link/rules/selector.rs`:287); no Rust code names a facade-only attr.
The only hits in `src/` are `annotation`, `slot` and `callable`.
Also: the monorepo's older `ast` baseline in its isolation command is a separate implementation, not a reader.
L runs only as a parity script (L:436-456) and in tests.

## 3. PostHog-specific vs generic

| item | line | what a config key must hold |
|---|---|---|
| `facade_files`: `products/*/backend/facade/**`, `.py`, skip `test_*` and `*_test.py` | F:271-281 | the export's module list (`within`+`paths`) plus an exclusion (path globs or "not testcode"). Export membership has no exclusion yet |
| `surface_rel` / `backend_product` / `backend_rel` (layout.py:25-40) | F:266-269,28 | `rel` = path under the `{product}` capture of `within` (labels.py:616-622 already computes it); `""` outside |
| `DATACLASS_DECORATORS = {dataclass, frozen}` by last name segment | F:31,456 | a list of decorator names. Core has `dataclass_decorators` (core/config.py:11,111) defaulting to `("dataclasses.dataclass",)` and compares resolved names; `@frozen` (`posthog.dataclasses`) is not in it, so core exposes no fields for it |
| `has_wiring_decorator`: `shared_task`, `*.defn` | F:54-61 | a `wiring_decorators` name list (spelling match, not resolved; L:54 has the resolved set) |
| pure-module rule (`has_functions` false means every import is handed out) | F:86,329-330 | a house convention for contracts and enums modules; a switch, or drop |
| `lazy` mode (PEP 562 map) | F:347 | generic |
| facade-only `rel` / `mode` / `members` / `member_of` | F:394-408 | only the legacy ledger needs them; do not carry them over |
| Generic Python: `SPECIAL_FORMS`, `TYPING_MODULES`, whole_any, `MUTATORS`, `_` privacy, `self`/`cls`, ClassVar, `__all__`, type-only skip, qualify/alias expansion, `Origin` resolution | F:32-38,54,109,151,162-204,242-264,447,470 | none; no PostHog name in them |
| L only (not in F): PostHog policy constants (approved wiring, library lists, carve-outs, model-crossing policy) | L:40-60 | PostHog policy; stays in the pack |

## 4. How the engine decides which modules belong to an export

Tables: `[exports.<n>]` keys `within`, `paths`, `shape`, `audience`, `subclass_of` (`hoglint/config/schema.py`:493-521, `ExportSpec` in `config/model.py`:112-119; `paths` required, `shape` required, at most 64 exports).
Python: `export_root` strips `within` (labels.py:616), `find_path_exports(path)` returns the export names whose `paths` match (624-631, memoized at 227), `export_covers` (638), `find_export_member` adds `subclass_of` by ancestry, symbol-level only (642-658), `exported` applies the audience (660-691).
Rust: `Export::covers` (`src/link/rules/labels.rs`:349-358) fills `Labels.exports_of[module]` as a bitmask (labels.rs:371,494-498).

Yes, "the modules of export X" is listable: Python `ExportPrimitive.covered_modules` (exports.py:95-106), Rust `ExportInputs::read` (export.rs:178-193, sorted by module name).
Gaps: a pack's `produce` gets only its own section; the `PackReader` has no config or labeler (`hoglint/plugins.py`:73-86); and export membership has no test-file exclusion (zone `tests` is separate, T:123).
Whether labels can be computed before pack producers run: not determined.

## 5. What the export primitive computes and takes

ExportPrimitive reads (`hoglint/rules/base.py`:62-66 lists `import, exposes, inherit, decorate`):
- core: module-scope `import` edges (exports.py:127-138, export.rs:340-375), `graph.symbols` (core declares: `top_level`, index.py:279), `inherit`/`decorate` via SymbolFacts (ancestors, decl, dataclass, frozen), symbol kinds from the labeler.
- core and pack mixed: `exposes` edges of the owner module of each checked symbol (exports.py:255-266).
- facade producer: `export` edges are not read. Its `exposes` are read only when the checked symbol lives in a facade module, where they repeat core's, except `annotation=missing` slots (core never emits those) and the `param:` slots of dataclass fields (core uses `field:`).

If the facade producer did not run: `export-shape` findings stay the same, except the `no-annotation` unknown on unannotated params and fields of facade-defined callables (exports.py:270).
`facade-signature` loses re-exported function slots, missing-annotation unknowns and `param:` field slots.
A producer failure marks rules reading `export` and `exposes` incomplete (P:19, failure kinds), so `export-shape` goes incomplete.

## 6. Overlap with core edges

Core `DeclarationProducer` (`hoglint/core/producers.py`:317-481; Rust twin `src/link/produce.rs`:11-21) emits for every module:
- `declares`: site scope = enclosing scope, surface `<mod>.<qual>`, attrs `decl`, `body`.
- `decorate`: attrs `decorated`, `called`, `frozen`. `inherit`: attrs `class`, `index`.
- `exposes`: attrs `callable`, `slot` (`return`, `param:x`, `field:x`), `position` (whole/nested); skips unannotated, builtins, nested, private-by-reader.

Facade edges against these:

| kind | duplicate of core | adds |
|---|---|---|
| declares | yes: same surface; `def_kind`=`decl`, `stub`=not `body` | `wiring_decorator`, `origin*`; site scope is the def name |
| decorate | yes, same class and decorator | `origin*`, via chain; lacks `decorated`/`called`/`frozen`, so SymbolFacts ignores them |
| inherit | yes | `position` (core `index`), `origin*`, via |
| exposes | partly: `SignaturePrimitive` drops a pack edge whose (callable, slot, targets) core already has (edges.py:101-111, signature.rs:78-91) | re-exported function slots, annotation=missing, `param:` field slots, `whole_any`, builtins, `@frozen`-detected fields |

The P:17-18 comment is right that a facade failure leaves these three kinds complete (core has them); "copy" is loose, because the attr names differ.
Nothing in the engine reads the facade copies of declares, decorate and inherit.

## Not determined

- Whether the duplicate `param:`/`field:` slots give two findings with different identity (`edge.slot`) for one dataclass field.
- The order in which Rust and Python hand back pack versus core edges beyond "core then pack".
- Whether the labeler can run before packs.

Later finding (D62, recorded in `docs/history/decision-log-3.md`): the builtins row in section 6 mattered. With the facade producer out, 2,946 findings would go, 1,540 of them on builtin types that only the producer supplied. A summary of this map that left builtins out had made N3b look like "three additions"; the brief was corrected before coding.
