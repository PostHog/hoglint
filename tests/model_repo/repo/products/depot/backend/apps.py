from django.apps import AppConfig

from posthog.api.file_system.deletion import register_post_delete_hook
from posthog.models.resource_transfer.registered_models import register_model
from products.inventory.backend.facade.hooks import register_restock_hook
from products.inventory.backend.facade.hooks import register_restock_hook as when_restocked


def _transfer_bins(kind):
    from products.depot.backend.logic.transfer import move_bins

    move_bins(kind)


def _recount(item_id):
    from products.depot.backend.logic.alias_target import recount

    return recount(item_id)


def _not_handed():
    from products.depot.backend.logic.cold import plain_note

    return plain_note()


class DepotConfig(AppConfig):
    name = "products.depot.backend"
    label = "depot"

    def ready(self):
        from products.depot.backend import registration  # noqa: F401

        def _on_restock(item_id):
            from products.depot.backend.logic.restock_rules import apply_rules

            apply_rules(item_id)

        register_restock_hook(_on_restock)
        when_restocked(_recount)
        register_model("depot-bin", _transfer_bins)
        self._register_audits()
        self._quiet()

    def _register_audits(self):
        def _audit(record):
            from products.depot.backend.logic.audit_trail import record_audit

            record_audit(record)

        register_post_delete_hook(_audit)

    def _quiet(self):
        def _unused():
            from products.depot.backend.logic.cold import quiet_note

            return quiet_note()

        return _unused

    def _not_called(self):
        def _never():
            from products.depot.backend.logic.cold import never_note

            return never_note()

        return _never
