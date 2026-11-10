# A "Long text" answer type (big box) for extra questions; Billing software's Remarks uses it.

from django.db import migrations, models


def remarks_big_box(apps, schema_editor):
    CategoryField = apps.get_model("tickets", "CategoryField")
    CategoryField.objects.filter(
        category__name__iexact="Billing software", label="Remarks", kind="text",
    ).update(kind="longtext")


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0010_category_asks_description"),
    ]

    operations = [
        migrations.AlterField(
            model_name="categoryfield",
            name="kind",
            field=models.CharField(
                choices=[("text", "Text"), ("longtext", "Long text (big box)"), ("number", "Number"),
                         ("choice", "Pick from a list")],
                default="text", max_length=10),
        ),
        migrations.RunPython(remarks_big_box, migrations.RunPython.noop),
    ]
