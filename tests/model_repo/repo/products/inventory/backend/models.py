from django.db import models


class StockItem(models.Model):
    team = models.ForeignKey("posthog.Team", on_delete=models.CASCADE, related_name="+")
    flag = models.ForeignKey("posthog.FeatureFlag", on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=200)
    quantity = models.IntegerField()
