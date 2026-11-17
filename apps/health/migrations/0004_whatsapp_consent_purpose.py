"""Finalidad nueva `whatsapp_notices` (docs/contracts/whatsapp.md) y su texto v1 en BORRADOR.

Los marcadores [RAZÓN SOCIAL], [NIT] y [CORREO DE DERECHOS] los reemplaza la superusuaria
publicando una versión nueva (Admin > Textos de consentimiento) DESPUÉS de que un abogado revise el
texto. Con marcadores, en modo `live` no se ofrece activar los avisos ni se envía nada
(`apps.whatsapp.conf.legal_missing`). Idempotente. No es asesoría legal.
"""
from django.db import migrations, models
from django.utils import timezone

WHATSAPP_V1 = (
    "AUTORIZACIÓN PARA RECIBIR AVISOS DE MI CUENTA POR WHATSAPP\n"
    "\n"
    "Responsable del tratamiento: [RAZÓN SOCIAL], NIT [NIT] (en adelante, ProFit Studio).\n"
    "\n"
    "Autorizo de manera previa, expresa e informada a ProFit Studio para usar mi número de celular y "
    "mi primer nombre con el fin de enviarme por WhatsApp avisos de mi cuenta: el vencimiento próximo "
    "o el vencimiento de mi membresía, que mi plan está listo para ver y la confirmación de mis pagos. "
    "Estos avisos nunca incluyen datos de salud ni el contenido de mis planes.\n"
    "\n"
    "Entiendo que:\n"
    "- Recibir estos avisos es voluntario y no es necesario para usar mi cuenta.\n"
    "- Declaro que soy mayor de 18 años.\n"
    "- Los mensajes se envían a través de WhatsApp, un servicio de Meta Platforms, por lo que mi "
    "número y los avisos se transmiten y almacenan en servidores ubicados fuera de Colombia.\n"
    "- Puedo dejar de recibirlos en cualquier momento desde mi cuenta, con el botón \"No recibir "
    "avisos\" de cada mensaje o respondiendo BAJA.\n"
    "- Puedo conocer, actualizar, rectificar y suprimir mis datos, y retirar esta autorización, "
    "escribiendo a [CORREO DE DERECHOS] o desde mi cuenta. También puedo solicitar ahí la política "
    "de tratamiento de datos personales de ProFit Studio."
)


def create_text(apps, schema_editor):
    ConsentTextVersion = apps.get_model('health', 'ConsentTextVersion')
    ConsentTextVersion.objects.get_or_create(
        purpose='whatsapp_notices', version=1,
        defaults={'body': WHATSAPP_V1, 'is_current': True, 'published_at': timezone.now()},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('health', '0003_healthprofile_healthaccesslog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='consenttextversion',
            name='purpose',
            field=models.CharField(choices=[('health_data', 'Tratamiento de datos de salud'), ('ai_processing', 'Envío sin identificar a la inteligencia artificial'), ('whatsapp_notices', 'Avisos de mi cuenta por WhatsApp')], max_length=20, verbose_name='Finalidad'),
        ),
        # Sin reversa: borrar un texto ya aceptado destruiría la evidencia del consentimiento
        migrations.RunPython(create_text, migrations.RunPython.noop),
    ]
