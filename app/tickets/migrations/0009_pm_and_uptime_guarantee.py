# Generated for PM schedules and CAMC uptime guarantees

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0008_telegram_support"),
    ]

    operations = [
        migrations.AddField(
            model_name="equipment",
            name="contract_start",
            field=models.DateField(blank=True, null=True, verbose_name="contract start date"),
        ),
        migrations.AddField(
            model_name="equipment",
            name="uptime_guarantee_percent",
            field=models.DecimalField(decimal_places=2, default=95.0, help_text="Contractual uptime guarantee (e.g. 95.00% or 98.00%).", max_digits=5, verbose_name="uptime guarantee %"),
        ),
        migrations.AddField(
            model_name="equipment",
            name="penalty_rate_per_day",
            field=models.DecimalField(decimal_places=2, default=0.0, help_text="Fixed deduction per day of excess downtime beyond allowable limits.", max_digits=10, verbose_name="penalty per day (₹)"),
        ),
        migrations.AddField(
            model_name="equipment",
            name="annual_camc_cost",
            field=models.DecimalField(decimal_places=2, default=0.0, help_text="Annual contract value for pro-rata penalty calculation if daily penalty is not set.", max_digits=12, verbose_name="annual CAMC cost (₹)"),
        ),
        migrations.CreateModel(
            name="PreventiveMaintenance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(help_text="For example: Quarterly Calibration & Quality Assurance", max_length=160)),
                ("frequency_months", models.PositiveSmallIntegerField(choices=[(1, "Monthly (every month)"), (3, "Quarterly (every 3 months)"), (6, "Half-yearly (every 6 months)"), (12, "Annual (every 12 months)")], default=3, verbose_name="frequency")),
                ("last_performed_at", models.DateField(blank=True, null=True, verbose_name="last completed")),
                ("next_due_date", models.DateField(help_text="Target date for next maintenance check.", verbose_name="next due date")),
                ("advance_notice_days", models.PositiveSmallIntegerField(default=7, help_text="Auto-raise a maintenance ticket this many days prior to due date.", verbose_name="advance notice (days)")),
                ("active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True, help_text="SOP checklist, service points, or guidelines.")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("assigned_to", models.ForeignKey(blank=True, help_text="Optional designated engineer/agent.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pm_schedules", to=settings.AUTH_USER_MODEL)),
                ("equipment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pm_schedules", to="tickets.equipment")),
                ("last_ticket", models.ForeignKey(blank=True, help_text="The latest ticket generated or linked to this PM schedule.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pm_source", to="tickets.ticket")),
            ],
            options={
                "verbose_name": "preventive maintenance schedule",
                "ordering": ["next_due_date", "equipment__name"],
            },
        ),
        migrations.AddField(
            model_name="ticket",
            name="pm_schedule",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tickets", to="tickets.preventivemaintenance"),
        ),
    ]
