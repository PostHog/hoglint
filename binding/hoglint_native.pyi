"""The native link of hoglint (Rust, via PyO3). One `Link` is one linked snapshot.

Bulk results are `bytes` of little-endian u32 edge ids (`array("I", ...)` or
`memoryview(...).cast("I")` reads them); Python builds Edge objects only for ids it asks for.
"""

FACTS_ID: str
"""The id of the `[facts]` sources in engine-sources.txt (the extractor): the fact cache key."""
NATIVE_ID: str
"""The id of the `[native]` sources (extractor, link, binding). It hashes text, never the
binary, so every platform agrees; a loaded module must report its sources' id."""
PROTOCOL: int
"""The binding's call protocol."""
FACT_LAYOUT: int
"""The fact database's table layout: part of the database file name."""

Pair = tuple[str, str]
Hit = tuple[str, list[Pair]]
"""(kind, chain): kind is class, def, variable, module, missing or member; the chain runs from
the name the code used to the origin, as (module, symbol) pairs."""

LookupResult = tuple[str, Hit | None, bool]
"""(surface, hit or None, local)."""

ImportRow = tuple[int, int, int, int, str | None, int, str, str | None]
"""(scope, line, column, flags, module or None, level, name, asname or None)."""

EdgeTuple = tuple[
    str,
    str,
    str,
    str,
    int,
    int,
    str,
    tuple[Pair, ...],
    str,
    int,
    tuple[Pair, ...],
    str,
    tuple[Pair, ...],
    str,
    str,
    str,
]
"""(kind, path, module, scope, line, column, surface, targets, resolution, context bits, via, use,
attrs, shape, shape detail, producer), with what refinements set. Context bits follow
`CONTEXT_BITS` in hoglint.native.graph."""

class Link:
    def timings(self) -> list[tuple[str, float]]: ...
    def heap(self) -> list[tuple[str, int]]:
        """Estimated heap bytes per part of the link, for memory reports."""
    def release_kinds(self, kept: list[str]) -> bool:
        """Drops the rows of the kinds only pack queries read (refs, strs, calls, uses, callees,
        names, tags) that are not in `kept`. A query of a dropped kind raises."""
    def release_facts(self) -> bool:
        """Drops the records' fact rows once the pack phase ends. Resolutions and row queries
        raise after it. False when the rows are shared and stay."""
    def edge_count(self) -> int:
        """Every edge, core and pack: ids 0..edge_count()."""
    def core_count(self) -> int:
        """The core edges: ids below it; pack edges come after them."""
    def add_edges(
        self,
        kinds: list[str],
        producers: list[str],
        sites: list[tuple[str, str, str, int, int]],
        surfaces: list[str],
        targets: list[list[Pair]],
        resolutions: list[str],
        contexts: list[int],
        via: list[list[Pair]],
        uses: list[str],
        shapes: list[tuple[str, str]],
        attrs: list[list[tuple[str, str]]],
    ) -> int:
        """Adds pack edges after every edge (one value per edge in each list; a site is (path,
        module, scope, line, column), a shape (shape, shape detail)). Returns the first new id."""
    def paths(self) -> list[str]: ...
    def scope_files(self) -> list[int]: ...
    def scopes(self) -> list[tuple[int, str, bool, int]]: ...
    def def_scopes(self, scope: int) -> tuple[list[str], bytes]:
        """Each def's qualified name and flags (bit 0: inside a function body; bit 1: a class),
        by def index, for the analyzed file with this scope index."""
    def file_id(self, path: str) -> int | None: ...
    def raw(self, file: int, key: str) -> bytes | None:
        """One key of a record as the extractor's JSON text, read from that record alone."""
    def has_rows(self, file: int, key: str) -> bool:
        """Whether a record's list under `key` has rows, with no JSON text read."""
    def keys(self, file: int) -> list[str]: ...
    def table(self, module: str) -> tuple[bool, list[tuple[str, tuple[object, ...]]], list[str], list[Pair]] | None: ...
    def symbols(self) -> list[tuple[str, str, str]]: ...
    def symbol_columns(self) -> tuple[list[str], bytes, bytes]:
        """`graph.symbols` of the core as columns in insertion order: names, a kind code per
        symbol (0 def, 1 async, 2 class) and a u32 file id per symbol."""
    def ids_of_kind(self, kind: str) -> bytes: ...
    def ids_to(self, kind: str, targets: list[Pair]) -> bytes: ...
    def ids_from(self, kind: str, module: str) -> bytes: ...
    def source_modules(self, kind: str) -> list[str]: ...
    def ids_spelled(self, kind: str, names: list[str]) -> bytes: ...
    def rows_named(self, kind: str, names: list[str]) -> list[tuple[int, bytes]]:
        """Rows of `calls` or `callees` in the analyzed files whose callee's last dotted name is
        one of `names`, as (file id, row JSON) in fact order: what `PackQueries.calls_named` reads."""
    def import_rows_named(self, names: list[str]) -> list[tuple[int, ImportRow]]:
        """`imports` rows in the analyzed files that import one of `names` by name (`from x
        import name`), as (file id, row) in fact order, then row order."""
    def imported_modules(self, file: int) -> list[str]:
        """The module each import of an analyzed file names, in row order: `import a.b` names
        a.b, `from a import b` names a.b."""
    def uses_to(self, targets: list[Pair]) -> list[tuple[int, int, int, int, int, LookupResult]]:
        """`uses` rows with attributes whose root a first-party import binds and whose chain
        resolves to a class among `targets` with an attribute resolved past the root: (scope,
        at scope, line, column, count, lookup) in scope order, then row order."""
    def words(self, ending: list[str], inside: list[str]) -> int:
        """Registers mention words: a string that ends in one of `ending` or holds one of
        `inside`. Returns the id `mentions` takes."""
    def mentions(self, file: int, kinds: list[str], words: int) -> bool:
        """Whether a record's rows of any of `kinds` hold a string the registered `words` match,
        read off the link's own rows. A kind the link does not read raises."""
    def statuses(self) -> list[str | None]:
        """Each record's status in fact order, None for a record with no status."""
    def blob_keys(self) -> list[str | None]:
        """The fact cache key of each record in fact order: blob oid, or the content hash of a
        third-party file; None for an unreadable file, a file that changed during the run or a
        JSONL input."""
    def drifted(self) -> list[str]:
        """Working tree files whose bytes did not hash to the snapshot's oid when the run read
        them: no fact entry and no run summary is stored for this run's view of them."""
    def subclasses(self, qualified: str) -> list[Pair]:
        """Module-level classes of the analyzed modules whose MRO holds `qualified`, the class
        itself included, in scope and table order: `is_subclass` asked of every class at once."""
    def class_facts(self, kind: str, owner_attr: str) -> list[tuple[str, str, list[str], bool]]:
        """(owner, surface, names, frozen) of the inherit or decorate edges, core and pack."""
    def add_symbol_facts(self, facts: list[tuple[str, list[tuple[str, str]]]]) -> None:
        """Records the facts packs wrote for some symbols: each symbol's (key, value) pairs replace
        whatever was written for it before, as `NativeSymbols.update` does."""
    def kind_order(self) -> list[str]: ...
    def candidates(self, kinds: list[str], include: int, exclude: int) -> bytes: ...
    def edges(self, ids: bytes) -> list[EdgeTuple]: ...
    def refine(
        self, ids: bytes, shapes: list[str | None], details: list[str | None], attrs: list[list[tuple[str, str]]]
    ) -> None:
        """Refines core edges (D22): per edge, the shape and shape detail to set (None keeps the
        edge's) and attrs to add after its own."""
    def columns(self, ids: bytes, names: list[str]) -> list[list[object]]:
        """Projected columns of edges: one list per name (path, module, scope, line, column,
        surface, target_module, target_symbol, producer, attr:<name>), one value per edge."""
    def resolve_name(self, module: str, name: str) -> Hit | None: ...
    def resolve_dotted(self, dotted: str) -> tuple[Hit | None, list[str]]: ...
    def resolve_value(self, module: str, value: object) -> Hit | None: ...
    def resolve_class(self, module: str, value: object) -> Pair | None: ...
    def mro(self, module: str, symbol: str) -> list[Pair]: ...
    def bases(self, module: str, symbol: str) -> list[Pair]: ...
    def is_subclass(self, module: str, symbol: str, qualified: str) -> bool: ...
    def located(self, module: str, symbol: str) -> tuple[int, int] | None: ...
    def value_lookup(self, scope: int, value: object, at: int, line: int) -> LookupResult | None: ...
    def chain_lookup(self, scope: int, chain: str, at: int, line: int) -> tuple[LookupResult, list[str]]: ...

Row = tuple[int, int, int, list[str], str, str | None]
"""(edge id, destination index, judgment code, identity, source module, destination module or None).
Codes: 1 unresolved, 2 unknown selection, 3 unknown violation, 4 violation."""

PairRow = tuple[int, int, int, tuple[str, str] | None]
"""(edge id, destination index, judgment code, the pair's (source, destination) groups or None)."""

Found = tuple[str, str, list[tuple[int, int]], int]
"""(what, start module, path of (edge id, destination index), verdict code). What: "start" (a start
whose `from` is unknown; no path), "unresolved", "unknown" (a hop the rule cannot tell whether to
follow) or "denied". Verdict codes: 1 true, 2 unknown, 0 for anything not denied."""

class Rules:
    """The labels and compiled rules of one run over a link (src/link/rules), from a plan
    (`hoglint.native.plan`). Evaluation runs on the threads the link was built with."""

    def __init__(self, link: Link, plan: str) -> None: ...
    def judge(self, rule: int) -> list[Row]:
        """Every destination an edge, entrypoint or signature rule does not judge clean, in judging
        order."""
    def pairs(self, rule: int) -> list[PairRow]:
        """What a cycle rule makes of each destination it cannot leave alone, in judging order."""
    def reach(self, rule: int) -> list[Found]:
        """The search of a reach rule from every start its `from` selects, in the order the Python
        search finds things."""
    def select(self, rule: int) -> bytes:
        """The candidate edges of a rule judged in Python that its selection does not rule out."""
    def passing(self, kinds: list[str], selector: str) -> bytes:
        """The edges of `kinds`, kind by kind in graph order, that a selector (its plan tree as
        JSON) does not make false, each read with its source as both ends."""
    def conflicts(self) -> list[tuple[str, str, str]]:
        """Every label `Labeler.find_conflicts` finds set two ways: (what, entity, field), what
        one of module, zones, kind, symbol, provided."""
    def met(self) -> list[tuple[str, str, str]]:
        """The labels set two ways that the rules met while they ran, as `conflicts` gives them."""
    def column(self, field: str) -> list[str]:
        """One field's value (or `registry`, `lifecycle`) on every graph module, rendered."""
    def values(self, field: str) -> tuple[list[tuple[str, list[str]]], bytes]:
        """A module field's value on every graph module: its distinct values as (type, items),
        and each module's place among them as u32s, 0xFFFFFFFF where Python reads it itself."""
    def registries(self) -> bytes:
        """Each graph module's registry by its place in the plan, as u32s; 0xFFFFFFFF for none."""

def link_jsonl(path: str, config: str, threads: int = 0) -> Link: ...
def link_blobs(
    repo: str,
    blobs: list[tuple[str, str, bool]],
    external: list[tuple[str, str]],
    extract: str,
    config: str,
    db: str | None = None,
    threads: int = 0,
) -> tuple[Link, tuple[int, int]]: ...
