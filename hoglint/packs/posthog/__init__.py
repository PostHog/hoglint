"""The PostHog pack. Importing it registers the pack for the `[posthog]` section and the
`owners-yaml` provider."""

from hoglint.packs.posthog.owners import OWNERS_YAML
from hoglint.packs.posthog.pack import POSTHOG_PACK
from hoglint.plugins import REGISTRY

REGISTRY.add_pack(POSTHOG_PACK)
REGISTRY.add_headlines({"drives": "the test {src} runs {dst} ({label})"})
REGISTRY.add_provider(OWNERS_YAML)
