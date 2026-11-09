"""PostHog's Django pack settings. The preset TOML maps onto these once the config layer reads it."""

from __future__ import annotations

from pathlib import Path

from hoglint.packs.django.config import DatabaseRouting, DjangoPackConfig, SettingsProxy

FIRST_PARTY = ("posthog", "ee", "products", "common")
REPO_PACKAGES = frozenset(FIRST_PARTY)

POSTHOG_DJANGO = DjangoPackConfig(
    first_party=FIRST_PARTY,
    settings_modules=("posthog.settings", "posthog.settings.*", "ee.settings"),
    routing=DatabaseRouting(Path("products/db_routing.yaml"), alias_template="{database}_db_writer"),
    settings_proxies=(SettingsProxy("oauth2_settings", "OAUTH2_PROVIDER_"),),
    setting_name_helpers=(SettingsProxy("setting_name", "SOCIAL_AUTH_"),),
    hooks=("**.register_*",),
    receiver_decorators=(
        "django.dispatch.dispatcher.receiver",
        "posthog.models.signals.mutable_receiver",
    ),
)
