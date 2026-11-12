from django.db import models


class Integration(models.Model):
    kind = models.CharField(max_length=40)
