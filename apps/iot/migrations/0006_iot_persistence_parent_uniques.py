"""Parent UNIQUE keys required by IoT-04.3-B composite foreign keys."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("iot", "0005_rename_key_version_master_key_version")]

    operations = [
        migrations.AddConstraint(
            model_name="coordinatorcredential",
            constraint=models.UniqueConstraint(
                fields=("coordinator", "id"), name="iot_coordcred_coord_id_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="sensor",
            constraint=models.UniqueConstraint(
                fields=("id", "coordinator", "channel", "metric"),
                name="iot_sensor_id_coord_ch_metric_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="coordinatorgateway",
            constraint=models.UniqueConstraint(
                fields=("id", "coordinator", "gateway", "farm"),
                name="iot_cg_id_coord_gw_farm_uniq",
            ),
        ),
    ]
