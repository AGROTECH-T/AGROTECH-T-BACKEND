# Escrita a mano siguiendo el patrón de 0002-0007 (BUILD F6).
"""Añade los eventos de concesión y retiro de la capacidad de crear fincas."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trazabilidad', '0007_alter_auditevent_event_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditevent',
            name='event_type',
            field=models.CharField(choices=[('farm_created', 'Finca creada'), ('farm_updated', 'Finca actualizada'), ('farm_deactivated', 'Finca desactivada'), ('farm_reactivated', 'Finca reactivada'), ('member_role_changed', 'Rol de integrante cambiado'), ('member_revoked', 'Integrante revocado'), ('member_left', 'Integrante abandonó la finca'), ('admin_transferred', 'Administración transferida'), ('email_verified', 'Correo verificado'), ('email_changed', 'Correo cambiado'), ('permission_granted', 'Permiso concedido'), ('permission_revoked', 'Permiso revocado'), ('permission_restricted', 'Permiso restringido'), ('permission_unrestricted', 'Restricción retirada'), ('session_opened', 'Sesión abierta'), ('session_closed', 'Sesión cerrada'), ('invitation_created', 'Invitación creada'), ('invitation_resend_started', 'Reenvío de invitación iniciado'), ('invitation_resent', 'Invitación reenviada'), ('invitation_delivery_failed', 'Entrega de invitación fallida'), ('invitation_role_changed', 'Rol de invitación cambiado'), ('invitation_cancelled', 'Invitación cancelada'), ('invitation_expired', 'Invitación expirada'), ('invitation_accepted', 'Invitación aceptada'), ('member_joined', 'Integrante incorporado'), ('member_reactivated', 'Integrante reactivado'), ('farm_creation_granted', 'Capacidad de crear fincas concedida'), ('farm_creation_revoked', 'Capacidad de crear fincas retirada')], max_length=40),
        ),
    ]
