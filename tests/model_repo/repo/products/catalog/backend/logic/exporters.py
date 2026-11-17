from abc import ABC, abstractmethod

from posthog.exporters import ExportTarget


class CatalogExport(ExportTarget):
    def send(self, rows: list[str]) -> int:
        return len(rows)


class LocalTarget(ABC):
    @abstractmethod
    def flush(self) -> int: ...


class CatalogFlush(LocalTarget):
    def flush(self) -> int:
        return 0
