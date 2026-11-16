from django.dispatch import Signal

widget_cleared = Signal()


def connect_widget_cleared(receiver) -> None:
    widget_cleared.connect(receiver)
