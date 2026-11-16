from django.apps import AppConfig
from django.db.models.signals import post_delete, post_save

from products.gatekeeper.backend.logic import session

# A write to these models ends the sessions the gate holds.
SESSION_MODELS = ("posthog.Team", "posthog.User")


class GatekeeperConfig(AppConfig):
    name = "products.gatekeeper.backend"
    label = "gatekeeper"

    def ready(self):
        for model in SESSION_MODELS:
            post_save.connect(session.forget, sender=model, dispatch_uid=f"gate-save-{model}")
            post_delete.connect(session.forget, sender=model, dispatch_uid=f"gate-delete-{model}")
        for label in session.watched_models():
            post_save.connect(session.forget, sender=label)
