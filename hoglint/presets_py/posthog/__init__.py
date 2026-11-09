"""PostHog's Python plugins. Importing this module registers the `[posthog]` pack and the
`owners-yaml` provider."""

from hoglint.plugins import REGISTRY
from hoglint.presets_py.posthog.owners import OWNERS_YAML
from hoglint.presets_py.posthog.pack import POSTHOG_PACK

REGISTRY.add_pack(POSTHOG_PACK)
REGISTRY.add_provider(OWNERS_YAML)
