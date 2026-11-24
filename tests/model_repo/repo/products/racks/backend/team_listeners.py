from django.db.models import signals

from posthog.models.team import Team


def on_team_saved(sender, instance, **kwargs):
    return None


# The signal is read by a computed attribute: hoglint cannot tell which signal this connects to.
getattr(signals, "post_save").connect(on_team_saved, sender=Team)
