from django.db import models


class AccessRule(models.Model):
    team = models.ForeignKey("posthog.Team", on_delete=models.CASCADE, related_name="+")
    level = models.CharField(max_length=20)
