"""The Django pack. Importing it registers the pack for the `[django]` section."""

from hoglint.packs.django.pack import DJANGO_PACK
from hoglint.plugins import REGISTRY

REGISTRY.add_pack(DJANGO_PACK)
