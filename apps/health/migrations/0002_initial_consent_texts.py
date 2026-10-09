"""Textos de consentimiento v1 (BORRADOR con marcadores).

Los marcadores entre corchetes ([RAZÓN SOCIAL], [NIT], [CORREO DE DERECHOS]) los reemplaza la
superusuaria publicando una versión nueva en el admin (Textos de consentimiento) DESPUÉS de que
un abogado revise el texto. Mientras haya marcadores, `HEALTH_FEATURES_ENABLED` debe seguir
apagado en producción y la fase B no enviará nada a la IA (services.ai_texts_ready).

Es idempotente: si la versión 1 ya existe, no la toca. Los textos van aquí (y no importados del
código) para que esta migración siga igual aunque el código cambie. No es asesoría legal.
"""
from django.db import migrations
from django.utils import timezone

HEALTH_DATA_V1 = (
    "AUTORIZACIÓN PARA EL TRATAMIENTO DE DATOS DE SALUD\n"
    "\n"
    "Responsable del tratamiento: [RAZÓN SOCIAL], NIT [NIT] (en adelante, ProFit Studio).\n"
    "\n"
    "Autorizo de manera previa, expresa e informada a ProFit Studio para recolectar, almacenar, "
    "usar y consultar mis medidas corporales (peso, estatura, cintura, cadera, porcentaje de grasa "
    "corporal) y los demás datos de salud que decida registrar en la plataforma.\n"
    "\n"
    "Finalidad: que mi entrenador o entrenadora conozca mi punto de partida, haga seguimiento a mi "
    "progreso y personalice mis rutinas y recomendaciones. Solo accederán a estos datos mi "
    "entrenador o entrenadora asignado y la administración de ProFit Studio.\n"
    "\n"
    "Entiendo que:\n"
    "- Estos son datos sensibles y que responder sobre ellos es voluntario: no estoy obligada a "
    "registrarlos y, si no lo hago, mi entrenador podrá atenderme igual con la información que yo "
    "le dé directamente.\n"
    "- Declaro que soy mayor de 18 años.\n"
    "- Mis datos pueden almacenarse en servidores ubicados fuera de Colombia, bajo medidas de "
    "seguridad razonables.\n"
    "- Puedo conocer, actualizar, rectificar y suprimir mis datos, y retirar esta autorización en "
    "cualquier momento, escribiendo a [CORREO DE DERECHOS] o desde mi cuenta. Retirarla no "
    "afecta lo ya tratado, pero ya no podré registrar nuevas medidas.\n"
    "- Puedo solicitar la política de tratamiento de datos personales de ProFit Studio a "
    "[CORREO DE DERECHOS]."
)

AI_V1 = (
    "AUTORIZACIÓN PARA EL ENVÍO DE DATOS SIN IDENTIFICAR A LA INTELIGENCIA ARTIFICIAL\n"
    "\n"
    "Responsable del tratamiento: [RAZÓN SOCIAL], NIT [NIT] (en adelante, ProFit Studio).\n"
    "\n"
    "Autorizo que mis datos de salud, sin mi nombre ni datos de contacto, sean enviados a Google "
    "(Gemini) para generar una propuesta de plan de ejercicio y alimentación que mi entrenador "
    "revisará. Entiendo que Google puede procesarlos en servidores fuera de Colombia, que puedo "
    "retirar esta autorización cuando quiera y que sin ella el entrenador puede armar mi plan "
    "manualmente.\n"
    "\n"
    "Esta autorización es independiente de la de datos de salud y es opcional: no es necesaria "
    "para registrar mis medidas ni para recibir atención de mi entrenador. Puedo ejercer mis "
    "derechos escribiendo a [CORREO DE DERECHOS]."
)

TEXTS = [
    ('health_data', 1, HEALTH_DATA_V1),
    ('ai_processing', 1, AI_V1),
]


def create_texts(apps, schema_editor):
    ConsentTextVersion = apps.get_model('health', 'ConsentTextVersion')
    for purpose, version, body in TEXTS:
        ConsentTextVersion.objects.get_or_create(
            purpose=purpose, version=version,
            defaults={'body': body, 'is_current': True, 'published_at': timezone.now()},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('health', '0001_initial'),
    ]

    operations = [
        # Sin reversa: borrar un texto ya aceptado destruiría la evidencia del consentimiento
        migrations.RunPython(create_texts, migrations.RunPython.noop),
    ]
