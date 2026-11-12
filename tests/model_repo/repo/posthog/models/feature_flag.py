from django.db import models


class FeatureFlag(models.Model):
    key = models.CharField(max_length=200)
