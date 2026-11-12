from django.db import models


class SessionRecording(models.Model):
    session_id = models.CharField(max_length=200)
