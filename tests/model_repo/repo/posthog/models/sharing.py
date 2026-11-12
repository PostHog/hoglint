from django.db import models


class SharingConfiguration(models.Model):
    widget = models.ForeignKey("legacy.Widget", on_delete=models.CASCADE)
    team = models.ForeignKey("posthog.Team", on_delete=models.CASCADE)
