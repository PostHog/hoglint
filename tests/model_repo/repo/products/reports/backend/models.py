from django.db import models


class ReportRun(models.Model):
    team = models.ForeignKey("posthog.Team", on_delete=models.CASCADE, related_name="+")
    entry = models.ForeignKey("catalog.CatalogEntry", on_delete=models.CASCADE, related_name="+")
