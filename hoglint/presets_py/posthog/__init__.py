"""PostHog's Python plugins. Importing this module registers the `[posthog]` pack."""

from hoglint.plugins import REGISTRY
from hoglint.presets_py.posthog.pack import POSTHOG_PACK

REGISTRY.add_pack(POSTHOG_PACK)
