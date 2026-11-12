from django.db import transaction
from rest_framework import viewsets

from products.access_control.backend.presentation.access_control import AccessControlViewSetMixin
from products.legacy.backend.models import Widget


class WidgetViewSet(AccessControlViewSetMixin, viewsets.ModelViewSet):
    def perform_create(self, serializer):
        with transaction.atomic():
            Widget.objects.create(name=serializer.name)
