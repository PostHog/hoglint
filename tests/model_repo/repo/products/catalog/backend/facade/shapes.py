from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from posthog.dataclasses import frozen
from posthog.models import Integration, Organization, OrganizationMembership, Project, Team, User
from products.catalog.backend.management.commands.import_entries import Command as ImportCommand
from products.inventory.backend.models import StockItem


@frozen
class EntryOwners:
    team: Team
    user: User
    organization: Organization
    membership: OrganizationMembership
    project: Project


@frozen
class EntryIntegration:
    integration: Integration


class EntryMissing(Exception):
    entry_id: int


class EntryOutOfStock(Exception):
    item: StockItem


class EntryView(BaseModel):
    title: str


class EntryStockView(BaseModel):
    item: StockItem


class EntryListener(Protocol):
    def __call__(self, entry_id: int) -> None: ...


class StockListener(Protocol):
    def __call__(self, item: StockItem) -> None: ...


@dataclass
class EntryDraft:
    title: str


@frozen
class EntryTitles:
    names: list[str]


@frozen
class EntryTeams:
    teams: list[Team]
    integrations: list[Integration]


@frozen
class EntryJob:
    command: ImportCommand
