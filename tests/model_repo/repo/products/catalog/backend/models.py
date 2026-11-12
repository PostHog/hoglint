from django.db import models


class EntryQuerySet(models.QuerySet):
    pass


class EntryManager(models.Manager):
    pass


class CatalogEntry(models.Model):
    team = models.ForeignKey("posthog.Team", on_delete=models.CASCADE, related_name="+")
    title = models.CharField(max_length=200)

    objects = EntryManager()
