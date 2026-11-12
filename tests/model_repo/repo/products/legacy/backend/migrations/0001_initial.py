from django.db import migrations


def copy_names(apps, schema_editor):
    apps.get_model("inventory", "StockItem")


class Migration(migrations.Migration):
    dependencies = [("posthog", "0001_initial")]
    operations = [migrations.RunPython(copy_names)]
