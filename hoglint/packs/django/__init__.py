"""The Django pack. Importing it registers the pack for the `[django]` section."""

from hoglint.packs.django.pack import DJANGO_PACK
from hoglint.plugins import REGISTRY

REGISTRY.add_pack(DJANGO_PACK)
REGISTRY.add_headlines(
    {
        "model-label": "{src} loads the model {dst} by its label",
        "signal": "{src} listens to a signal of {dst}",
        "migration-parent": "the migration {src} depends on the migration {dst}",
        "installs": "{src} registers a callback into {dst}",
        "reverse-accessor": "the relation {declaring_model}.{field} gives {dst} the reverse accessor {accessor}",
    }
)
