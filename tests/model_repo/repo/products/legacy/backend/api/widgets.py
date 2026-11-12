from django.db import transaction
from rest_framework import viewsets

from products.access_control.backend.presentation.access_control import AccessControlViewSetMixin
from products.catalog.backend.presentation.views import EntryViewSet
from products.legacy.backend.models import Widget


class WidgetEntryViewSet(EntryViewSet):
    pass


class WidgetViewSet(AccessControlViewSetMixin, viewsets.ModelViewSet):
    def perform_create(self, serializer):
        with transaction.atomic():
            Widget.objects.create(name=serializer.name)
