"""Textos fijos de los planes (contrato, sección 10). Deben revisarlos un abogado y un profesional
de la salud antes de producción."""

# Descargo de responsabilidad: visible al pedir el plan, en "Mi plan" y en la rutina aprobada
DISCLAIMER = (
    'Orientación de bienestar y entrenamiento con menús cotidianos e internacionales, '
    'preparada con apoyo de inteligencia artificial y revisada por tu entrenador. '
    'La atención clínica y las dietas para tratar enfermedades están pausadas en ProFit Studio. '
    'Si un ejercicio produce dolor o malestar, detén la actividad.'
)


# ─── Fase D (docs/contracts/plan-ia-fase-d.md, sección 5.5): textos en voz del ENTRENADOR ──────────
# Los de `rules/eligibility.py` (`REQUIREMENT_TEXTS`) están escritos para la clienta («tus datos»).
# Cada código -> (texto, qué hacer). La dirección (`url`) la pone `overview.py`.
TRAINER_REQUIREMENT_TEXTS = {
    'D00_NOT_AVAILABLE': (
        'Los planes con IA no están disponibles ahora (función apagada o textos legales sin revisar).',
        'Avisa a la administradora.'),
    'D01_CONSENT_HEALTH': (
        'La clienta aún no autorizó el tratamiento de sus datos de salud.',
        'Pídele que lo autorice desde su cuenta.'),
    'D01_CONSENT_AI': (
        'La clienta no autorizó el envío sin identificar de sus datos a la IA.',
        'Pídele que lo autorice, o arma el plan a mano.'),
    'D02_PROFILE': (
        'La clienta aún no llena su ficha de salud y alimentación.',
        'Pídele que la llene desde su cuenta.'),
    'D02_PROFILE_CONFIRM': (
        'La clienta no ha confirmado la última corrección de su ficha.',
        'Pídele que la confirme.'),
    'D03_MEASURE': (
        'La clienta no tiene medidas (peso y estatura) de los últimos 30 días.',
        'Registra una medición.'),
    'D04_INCOHERENT': (
        'Hay datos que no cuadran (peso, estatura o fecha de nacimiento).',
        'Revisa la ficha y las medidas.'),
    'D05_QUOTA': (
        'Se alcanzó el límite diario de generaciones.',
        'Inténtalo mañana.'),
    'D07_NUTRITION_DETAILS': (
        'Falta el nivel de actividad diaria para armar el plan de alimentación.',
        'Corrígelo en la ficha («Más detalles») o pídele que lo complete.'),
}

TRAINER_BLOCKED_GENERIC = ('La ficha tiene una bandera que exige valoración profesional: no se genera borrador '
                           'con IA. Arma el plan a mano o deriva a la clienta.')
TRAINER_BLOCKED_TEXTS = {
    'D06_FEW_EXERCISES': ('No hay suficientes ejercicios compatibles con la ficha (revisa las etiquetas del '
                          'catálogo). Arma el plan a mano.'),
    'D06_FEW_FOODS': ('No hay suficientes alimentos compatibles con la ficha. Arma la guía de alimentación '
                      'aparte.'),
    'NUTRITION_CLINICAL_PAUSED': ('Los menús de bienestar no cubren este caso: la alimentación clínica está '
                                  'pausada.'),
}

# Resumen de un trabajo que falló (sección 5.6). Primero el código exacto, luego los prefijos.
JOB_TEXT_SLOW = 'La IA tardó demasiado o no respondió. Puedes volver a intentarlo.'
JOB_TEXT_BUSY = 'El servicio de IA estaba saturado. Inténtalo de nuevo en unos minutos.'
JOB_TEXT_DOWN = 'El servicio de IA tuvo un problema temporal. Inténtalo de nuevo.'
JOB_TEXT_CONFIG = 'La conexión con la IA no está bien configurada. Avisa a la administradora.'
JOB_TEXT_SAFETY = 'El servicio de IA bloqueó la solicitud por seguridad. Arma el plan a mano.'
JOB_TEXT_UNREADABLE = 'La IA devolvió una respuesta que no se pudo usar. Puedes volver a intentarlo.'
JOB_TEXT_VALIDATION = ('El borrador no pasó las revisiones de seguridad y no se guardó. '
                       'Puedes volver a intentarlo.')
JOB_TEXT_RECIPES = ('No había suficientes recetas compatibles con la ficha y las porciones. Revisa los '
                    'alimentos disponibles o arma la guía a mano.')
JOB_TEXT_LOST = 'El proceso se interrumpió (por ejemplo, un reinicio del servidor). Vuelve a intentarlo.'
JOB_TEXT_INACTIVE = 'La cuenta estaba desactivada cuando se iba a generar.'
JOB_TEXT_KIND = 'Ese tipo de plan no está disponible.'
JOB_TEXT_CHANGED = 'El plan cambió mientras se generaba la opción. La clienta puede volver a pedirla.'
JOB_TEXT_INTERNAL = ('Ocurrió un error interno al generar el borrador. Inténtalo de nuevo y, si se repite, '
                     'avisa a la administradora.')
JOB_FAILED_EXACT = {
    'timeout': JOB_TEXT_SLOW, 'ai_timeout': JOB_TEXT_SLOW, 'ai_network': JOB_TEXT_SLOW,
    'ai_http_429': JOB_TEXT_BUSY,
    'ai_not_configured': JOB_TEXT_CONFIG, 'ai_bad_schema_field': JOB_TEXT_CONFIG,
    'ai_blocked_prompt': JOB_TEXT_SAFETY, 'ai_blocked_safety': JOB_TEXT_SAFETY,
    'ai_invalid_json': JOB_TEXT_UNREADABLE, 'ai_unreadable_response': JOB_TEXT_UNREADABLE,
    'ai_no_candidates': JOB_TEXT_UNREADABLE, 'ai_unknown': JOB_TEXT_UNREADABLE,
    'recipes_unavailable': JOB_TEXT_RECIPES,
    'worker_lost': JOB_TEXT_LOST, 'lost_lock': JOB_TEXT_LOST,
    'client_inactive': JOB_TEXT_INACTIVE,
    'kind_not_available': JOB_TEXT_KIND,
    'slot_missing': JOB_TEXT_CHANGED, 'plan_not_active': JOB_TEXT_CHANGED, 'option_changed': JOB_TEXT_CHANGED,
    'internal_error': JOB_TEXT_INTERNAL,
}
JOB_FAILED_GENERIC = 'No se pudo generar el borrador. Código técnico: {code}.'
JOB_FAILED_NOT_READY = 'Cuando se iba a generar faltaba un requisito: {text}'

# Constancia (sección 6.5). Entrenamiento: los mismos textos del panel «Qué se tuvo en cuenta».
TRAINING_LEVEL_TEXT = {
    'low': 'Constancia baja: completó menos de la mitad de los días de entrenamiento del último mes.',
    'medium': 'Constancia media: completó entre la mitad y cuatro de cada cinco días de entrenamiento.',
    'high': 'Constancia alta: completó al menos cuatro de cada cinco días de entrenamiento.',
}
MEALS_LEVEL_TEXT = {
    'low': 'Constancia baja: marcó menos de la mitad de sus comidas del último mes.',
    'medium': 'Constancia media: marcó entre la mitad y cuatro de cada cinco comidas.',
    'high': 'Constancia alta: marcó al menos cuatro de cada cinco comidas.',
}
NOTE_NO_EXERCISE_PLAN = 'Todavía no tiene un plan de ejercicio aprobado.'
NOTE_NO_NUTRITION_PLAN = 'Todavía no tiene un plan de alimentación aprobado.'
NOTE_NO_CURRENT_NUTRITION_PLAN = 'No tiene un plan de alimentación vigente.'
NOTE_COLD_START = ('Aún no hay suficiente historial: la constancia se calcula después de 14 días con un plan '
                   'aprobado.')
NOTE_NO_PROFILE = 'No se muestra el porcentaje porque falta la ficha de la clienta o su autorización.'

# Clonar a borrador (sección 7)
CLONE_MESSAGES = {
    'is_draft': 'Este plan ya es un borrador: edítalo en lugar de clonarlo.',
    'no_content': 'Este plan no tiene contenido que copiar.',
    'simulated': 'Este plan se generó en MODO DE PRUEBA: no se puede clonar.',
    'job_running': 'Hay un borrador generándose para esta clienta. Espera a que termine.',
    'red_flag': ('La ficha actual de la clienta requiere valoración profesional antes de armar un plan de '
                 'alimentación. No se puede clonar.'),
    'out_of_scope': ('Los menús de bienestar no cubren esta solicitud. La alimentación clínica está pausada.'),
    'race': 'Se creó otro borrador al mismo tiempo. Revisa los borradores de la clienta.',
    'has_copy': 'Ya creaste una copia de este plan: ábrela para seguir.',
    'other_draft': 'Ya hay un borrador de {kind} (versión {version}) en revisión. Ábrelo o archívalo antes de clonar otro.',
    'created_with_current': ('Creamos la versión {new} como borrador. La clienta sigue viendo la versión '
                             '{current} hasta que apruebes esta.'),
    'created_alone': 'Creamos la versión {new} como borrador. La clienta no verá nada hasta que la apruebes.',
    'existing': 'Ya habías creado este borrador (versión {version}). Lo abrimos para que sigas.',
    'conflicts_exercise': ('{count} ejercicio(s) ya no son compatibles con la ficha de la clienta. '
                           'Revísalos antes de aprobar.'),
    'conflicts_nutrition': ('{count} opción(es) incluyen alimentos que ya no son compatibles con la clienta. '
                            'Edítalas antes de aprobar: sin eso no se puede aprobar el plan.'),
    'reason_invalid': 'Revisa el motivo.',
}
