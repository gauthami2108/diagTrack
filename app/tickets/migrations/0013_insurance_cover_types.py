from django.db import migrations

# (name, expected at every centre)
DEFAULTS = [
    ("Fire", True), ("Building", True), ("Equipment / machinery breakdown", True), ("Contents / stock", True),
    ("Burglary", False), ("Public liability", False), ("Professional indemnity", False),
    ("Medical malpractice", False), ("Vehicle", False), ("Cyber", False), ("Other", False),
]


def add_cover_types(apps, schema_editor):
    Type = apps.get_model("tickets", "InsuranceCoverType")
    for name, core in DEFAULTS:
        Type.objects.get_or_create(name=name, defaults={"core": core})


class Migration(migrations.Migration):
    dependencies = [("tickets", "0012_insurance")]
    operations = [migrations.RunPython(add_cover_types, migrations.RunPython.noop)]
