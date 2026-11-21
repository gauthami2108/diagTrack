from django.conf import settings
from django.db import migrations, models


def copy_forward(apps, schema_editor):
    Policy = apps.get_model("tickets", "InsurancePolicy")
    for p in Policy.objects.exclude(responsible_old=None):
        p.responsible.add(p.responsible_old_id)


def copy_back(apps, schema_editor):
    Policy = apps.get_model("tickets", "InsurancePolicy")
    for p in Policy.objects.all():
        first = p.responsible.first()
        if first:
            p.responsible_old_id = first.pk
            p.save(update_fields=["responsible_old"])


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0014_user_insurance_access"),
    ]

    operations = [
        migrations.AlterField(
            model_name="insurancepolicy", name="responsible",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="+",
                                    to=settings.AUTH_USER_MODEL),
        ),
        migrations.RenameField(model_name="insurancepolicy", old_name="responsible", new_name="responsible_old"),
        migrations.AddField(
            model_name="insurancepolicy", name="responsible",
            field=models.ManyToManyField(
                blank=True, related_name="insurance_policies", to=settings.AUTH_USER_MODEL,
                help_text="Optional. One or more people. They also receive the expiry reminders if they have Insurance access."),
        ),
        migrations.RunPython(copy_forward, copy_back),
        migrations.RemoveField(model_name="insurancepolicy", name="responsible_old"),
    ]
