from rest_framework import viewsets

from products.access_control.backend.presentation.access_control import AccessControlViewSetMixin
from products.inventory.backend.facade import api
from products.inventory.backend.presentation.serializers import StockSerializer


class StockViewSet(AccessControlViewSetMixin, viewsets.ViewSet):
    serializer_class = StockSerializer

    def retrieve(self, request, pk=None):
        return api.get_stock(request.team_id, int(pk))
