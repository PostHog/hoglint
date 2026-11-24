from django.db.models.signals import post_save

from posthog.models.team import Team


def on_team_saved(sender, instance, **kwargs):
    return None


post_save.connect(on_team_saved, sender=Team)
