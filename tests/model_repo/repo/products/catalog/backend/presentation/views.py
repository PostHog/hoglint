from rest_framework import viewsets

from products.access_control.backend.presentation.access_control import AccessControlViewSetMixin
from products.catalog.backend.facade import api


class EntryViewSet(AccessControlViewSetMixin, viewsets.ViewSet):
    def list(self, request):
        return api.count_entries(request.team_id)


class EntryExportView(viewsets.ViewSet):
    def list(self, request):
        return api.count_entries(request.team_id)
