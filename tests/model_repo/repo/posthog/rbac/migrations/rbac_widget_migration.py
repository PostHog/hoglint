from django.apps import apps


# A data migration run by hand: frozen history, as a Django migration is.
def forwards() -> None:
    widget = apps.get_model("legacy", "Widget")
    widget.objects.update(name="")
