from django.db import migrations

from products.catalog.backend.sql import ENTRY_TABLE_SQL


class Migration(migrations.Migration):
    dependencies = [("posthog", "0001_initial")]
    operations = [ENTRY_TABLE_SQL]
