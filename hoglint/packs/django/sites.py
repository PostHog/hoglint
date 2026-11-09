"""Sites and contexts for the edges the Django pack makes."""

from __future__ import annotations

from pathlib import PurePosixPath

from hoglint.model import Context, Edge, Resolution, Site
from hoglint.packs.django.symbols import MODULE_SCOPE, scope_name
from hoglint.spike.core import FUNC, IF, SITE_MARKER, TRY, TYPE_ONLY, Core, module_of_path

PRODUCER_ERROR = "producer-error"

TEST_DIRS = frozenset({"test", "tests"})
MIGRATION_DIR = "migrations"


class PathKinds:
    """Which files are tests or migrations, from the path alone."""

    @staticmethod
    def is_test(path: str) -> bool:
        parts = PurePosixPath(path).parts
        name = parts[-1]
        if TEST_DIRS & set(parts):
            return True
        return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"

    @staticmethod
    def is_migration(path: str) -> bool:
        return MIGRATION_DIR in PurePosixPath(path).parts

    @classmethod
    def contexts(cls, path: str, flags: int = 0) -> frozenset[Context]:
        found: set[Context] = set()
        if cls.is_test(path):
            found.add(Context.TEST)
        if cls.is_migration(path):
            found.add(Context.MIGRATION)
        if flags & TYPE_ONLY:
            found.add(Context.TYPE_ONLY)
        if flags & FUNC:
            found.add(Context.FUNCTION_LOCAL)
        if flags & (IF | TRY):
            found.add(Context.CONDITIONAL)
        if not found & {Context.TEST, Context.MIGRATION}:
            found.add(Context.PROD)
        return frozenset(found)


def make_site(record: dict, module: str, scope: int, line: int, column: int = 0) -> Site:
    return Site(record["path"], module, scope_name(record.get("defs", []), scope), line, column)


def file_site(path: str) -> Site:
    return Site(path, module_of_path(path)[0], MODULE_SCOPE, 0)


def failure_edge(kind: str, producer: str, site: Site, error: Exception) -> Edge:
    """One item a producer could not read: unknown on that item, while the other items still count."""
    return Edge(
        kind=kind,
        site=site,
        surface="",
        targets=(),
        resolution=Resolution.UNRESOLVED,
        contexts=PathKinds.contexts(site.path),
        producer=producer,
        attrs=(("reason", PRODUCER_ERROR), ("error", f"{type(error).__name__}: {error}")),
    )


def first_party_records(core: Core, roots: tuple[str, ...]) -> list[tuple[str, dict]]:
    """Parsed repo files under the first-party roots, by path."""
    out = []
    for path in sorted(core.store.records):
        if SITE_MARKER in path or path.split("/", 1)[0] not in roots:
            continue
        record = core.store.records[path]
        if record.get("status") == "ok":
            out.append((path, record))
    return out
