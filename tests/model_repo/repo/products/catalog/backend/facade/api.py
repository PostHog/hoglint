from collections.abc import Mapping
from typing import Any

from django.db.models import QuerySet

from posthog.models import Integration, Organization, OrganizationMembership, Project, Team, User
from posthog.models.ghost import Ghost
from products.catalog.backend.management.commands.import_entries import Command as ImportCommand
from products.catalog.backend.facade.contracts import EntryInfo
from products.catalog.backend.logic import pricing
from products.catalog.backend.models import CatalogEntry, EntryManager
from products.reports.backend.facade.api import report_count

_LAZY = {"EntrySerializer": "products.catalog.backend.api.serializers"}


def __getattr__(name):
    raise AttributeError(name)


def price(entry_id: int) -> int:
    return pricing.price_for(entry_id)


def count_entries(team_id: int) -> int:
    return CatalogEntry.objects.filter(team_id=team_id).count()


def summarize(entries: list[EntryInfo]) -> str:
    lines = []
    for entry in entries:
        if entry.title.startswith("_"):
            continue
        title = entry.title.strip()
        if len(title) > 40:
            title = title[:40]
        lines.append(f"{entry.entry_id}: {title}")
    lines.sort()
    header = f"{len(lines)} entries"
    footer = f"reports: {report_count(0)}"
    return "\n".join([header, *lines, footer])


def get_entry(entry_id: int) -> CatalogEntry:
    return pricing.entry(entry_id)


def entries_for(team_id: int) -> QuerySet[CatalogEntry]:
    return pricing.entries(team_id)


def entry_manager() -> EntryManager:
    return pricing.manager()


def for_team(team: Team) -> int:
    return team.pk


def for_user(user: User) -> int:
    return user.pk


def for_organization(organization: Organization) -> int:
    return organization.pk


def for_membership(membership: OrganizationMembership) -> int:
    return membership.pk


def for_project(project: Project) -> int:
    return project.pk


def for_integration(integration: Integration) -> int:
    return integration.pk


def team_of(entry_id: int) -> Team:
    return pricing.owner(entry_id, "Team")


def user_of(entry_id: int) -> User:
    return pricing.owner(entry_id, "User")


def organization_of(entry_id: int) -> Organization:
    return pricing.owner(entry_id, "Organization")


def membership_of(entry_id: int) -> OrganizationMembership:
    return pricing.owner(entry_id, "OrganizationMembership")


def project_of(entry_id: int) -> Project:
    return pricing.owner(entry_id, "Project")


def integration_of(entry_id: int) -> Integration:
    return pricing.owner(entry_id, "Integration")


def take_object(value: object) -> int:
    return 0


def take_any(value: Any) -> int:
    return 0


def take_bare_dict(value: dict) -> int:
    return len(value)


def take_str_map(value: dict[str, str]) -> int:
    return len(value)


def take_payload(value: dict[str, Any]) -> int:
    return len(value)


def give_payload(entry_id: int) -> dict[str, Any]:
    return {"id": entry_id}


def take_object_payload(value: dict[str, object]) -> int:
    return len(value)


def give_object_payload(entry_id: int) -> dict[str, object]:
    return {"id": entry_id}


def take_unannotated(value) -> int:
    return 0


def take_union(value: dict[str, Any] | list[Any]) -> int:
    return len(value)


def take_mapping(value: Mapping[str, Any]) -> int:
    return len(value)


def take_bare_list(value: list) -> int:
    return len(value)


def take_team_map(value: dict[str, Team]) -> int:
    return len(value)


def give_list_any(entry_id: int) -> list[Any]:
    return [entry_id]


def for_ghost(ghost: Ghost) -> int:
    return 0


def run_import(command: ImportCommand) -> int:
    return 0


def import_command(entry_id: int) -> ImportCommand:
    return ImportCommand()
