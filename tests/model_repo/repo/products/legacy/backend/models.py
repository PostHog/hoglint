from django.db import models


class Widget(models.Model):
    team = models.ForeignKey("posthog.Team", on_delete=models.CASCADE)
    owner = models.ForeignKey("posthog.User", on_delete=models.CASCADE, related_name="+")
    stock = models.ForeignKey("inventory.StockItem", on_delete=models.CASCADE)
    flag = models.ForeignKey("posthog.FeatureFlag", on_delete=models.CASCADE, related_name="+")
    name = models.CharField(max_length=200)
