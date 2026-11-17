from abc import ABC, abstractmethod


class ExportTarget(ABC):
    @abstractmethod
    def send(self, rows: list[str]) -> int: ...
