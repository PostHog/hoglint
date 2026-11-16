from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("reports", "0001_initial")]
    # An augmented key list: its keys are not read, so the migration's parents are unknown.
    dependencies += [("legacy", "0001_initial")]

    operations = []
