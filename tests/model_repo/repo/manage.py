import os

from products.legacy.backend.logic.widgets import widget_total

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posthog.settings")
print(widget_total(0))
