from dataclasses import dataclass


@dataclass(frozen=True)
class StockAuditNote:
    text: str
