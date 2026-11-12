from django.db import models


class Organization(models.Model):
    name = models.CharField(max_length=200)


class OrganizationMembership(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    user = models.ForeignKey("posthog.User", on_delete=models.CASCADE)
