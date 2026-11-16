from django.db import models

from posthog.models.team.extensions import TeamExtensionDescriptor


class Team(models.Model):
    name = models.CharField(max_length=200)

    stock_settings = TeamExtensionDescriptor("products.inventory.backend.models.StockItem")
