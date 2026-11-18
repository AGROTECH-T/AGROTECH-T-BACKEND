from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("iot", "0004_coordinatorbootwindow_coordinatorcredential"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="coordinatorcredential",
            name="iot_coordcred_key_version_valid",
        ),
        migrations.RenameField(
            model_name="coordinatorcredential",
            old_name="key_version",
            new_name="master_key_version",
        ),
        migrations.AddConstraint(
            model_name="coordinatorcredential",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("master_key_version__gte", 1),
                    ("master_key_version__lte", 65535),
                ),
                name="iot_coordcred_key_version_valid",
            ),
        ),
    ]
