# Generated for Phase 2 enhancements: archiving and weekly reports

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0006_signin_lock"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="weekly_report_enabled",
            field=models.BooleanField(default=True, verbose_name="Weekly management email on Monday"),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="last_weekly_report_at",
            field=models.DateField(blank=True, editable=False, null=True, verbose_name="last weekly report date"),
        ),
        migrations.AddField(
            model_name="ticket",
            name="is_archived",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="ticket",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
