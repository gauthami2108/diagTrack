# Lets a category replace the free-text "What happened?" box with its own questions.

from django.db import migrations, models


def billing_software_questions(apps, schema_editor):
    """Billing software tickets ask for Bill Number and Remarks instead of 'What happened?'."""
    Category = apps.get_model("tickets", "Category")
    CategoryField = apps.get_model("tickets", "CategoryField")
    for cat in Category.objects.filter(name__iexact="Billing software"):
        if cat.fields.exists():
            continue  # already has questions set up by an administrator; leave it alone
        CategoryField.objects.create(category=cat, label="Bill Number", kind="text", required=True, order=0)
        CategoryField.objects.create(category=cat, label="Remarks", kind="text", required=True, order=1)
        cat.asks_description = False
        cat.save(update_fields=["asks_description"])


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0009_pm_and_uptime_guarantee"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="asks_description",
            field=models.BooleanField(
                default=True, verbose_name="ask 'What happened?'",
                help_text="Untick to hide the free-text 'What happened?' box and use only this category's own questions."),
        ),
        migrations.RunPython(billing_software_questions, migrations.RunPython.noop),
    ]
