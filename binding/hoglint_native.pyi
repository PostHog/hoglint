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
    def uses_to(self, targets: list[Pair]) -> list[tuple[int, int, int, int, int, LookupResult]]:
        """`uses` rows with attributes whose root a first-party import binds and whose chain
        resolves to a class among `targets` with an attribute resolved past the root: (scope,
        at scope, line, column, count, lookup) in scope order, then row order."""
    def mentions(self, file: int, kinds: list[str], ending: list[str], inside: list[str]) -> bool:
        """Whether a record's rows of any of `kinds` hold a string that ends in one of `ending` or
        holds one of `inside`, read off the link's own rows. A kind the link does not read raises."""
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
    def kind_order(self) -> list[str]: ...
    def candidates(self, kinds: list[str], include: int, exclude: int) -> bytes: ...
    def split_irregular(self, ids: bytes) -> tuple[bytes, bytes]:
        """(the ids a group may speak for, the irregular ones: several targets, or attrs that
        override endpoint labels)."""
    def endpoint_keys(
        self, ids: bytes, role: str, src_scope: bool, dst_entity: bool
    ) -> tuple[bytes, bytes, bytes, bytes]: ...
    def edge_keys(self, ids: bytes, fields: list[str]) -> tuple[bytes, bytes]: ...
    @staticmethod
    def group(ids: bytes, layers: list[tuple[bytes, bytes]]) -> tuple[bytes, bytes]: ...
    @staticmethod
    def keep_keys(ids: bytes, key_of: bytes, keep: bytes) -> tuple[bytes, bytes]: ...
    def src_sites(self, ids: bytes) -> list[tuple[str, str, str]]: ...
    def dst_parts(self, ids: bytes) -> list[tuple[str, str, str | None, str]]: ...
    def stable_keys(self, ids: bytes, which: str) -> tuple[bytes, int, bytes, bytes]: ...
    def ids_seen_from(self, kind: str, module: str, include: int, exclude: int) -> bytes: ...
    @staticmethod
    def keep_classes(ids: bytes, key_of: bytes, class_of: bytes, keep: bytes) -> bytes: ...
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
