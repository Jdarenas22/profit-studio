"""Listas cerradas de la ficha de salud y alimentación (docs/contracts/plan-ia.md, sección 2.2).

Cada lista es `[(código, etiqueta en español)]`. El servidor valida SIEMPRE contra estos
códigos; las plantillas pintan casillas/selectores a partir de ellos (contexto `choices`).

Contenido clínico marcado [A VALIDAR]: las preguntas del cuestionario de aptitud (PAR-Q) están
parafraseadas, no copiadas, y deben revisarse frente a la versión oficial vigente del PAR-Q+ en
español por un profesional de la salud antes de producción.
"""

SEX_CHOICES = [
    ('F', 'Femenino'),
    ('M', 'Masculino'),
    ('NA', 'Prefiero no decir'),
]

CONDITION_CHOICES = [
    ('diabetes_t1', 'Diabetes tipo 1'),
    ('diabetes_t2', 'Diabetes tipo 2'),
    ('prediabetes', 'Prediabetes'),
    ('hypertension', 'Hipertensión (presión alta)'),
    ('heart_disease', 'Enfermedad del corazón'),
    ('arrhythmia', 'Arritmia'),
    ('stroke_history', 'Antecedente de derrame o ataque cerebral'),
    ('kidney_disease', 'Enfermedad de los riñones'),
    ('liver_disease', 'Enfermedad del hígado'),
    ('fatty_liver', 'Hígado graso'),
    ('thyroid_disease', 'Enfermedad de la tiroides'),
    ('asthma', 'Asma'),
    ('osteoporosis', 'Osteoporosis'),
    ('herniated_disc', 'Hernia discal'),
    ('cancer_active', 'Cáncer (en tratamiento o seguimiento)'),
    ('bariatric_surgery', 'Cirugía bariátrica'),
    ('metabolic_disorder', 'Trastorno metabólico'),
    ('gout', 'Gota'),
    ('reflux_gastritis', 'Reflujo o gastritis'),
    ('ibs', 'Colon irritable'),
    ('celiac', 'Enfermedad celíaca'),
    ('other', 'Otra condición'),
]

MEDICATION_CHOICES = [
    ('insulin', 'Insulina'),
    ('hypoglycemic_drugs', 'Medicamentos para bajar el azúcar'),
    ('antihypertensives', 'Medicamentos para la presión'),
    ('beta_blockers', 'Betabloqueadores'),
    ('anticoagulants', 'Anticoagulantes'),
    ('corticosteroids', 'Corticoides'),
    ('thyroid_meds', 'Medicamentos para la tiroides'),
    ('other', 'Otro medicamento'),
]

INJURY_CHOICES = [
    ('knee', 'Rodilla'),
    ('lower_back', 'Espalda baja'),
    ('shoulder', 'Hombro'),
    ('hip', 'Cadera'),
    ('ankle', 'Tobillo'),
    ('wrist_elbow', 'Muñeca o codo'),
    ('neck', 'Cuello'),
]

SURGERY_CHOICES = [
    ('none', 'Ninguna'),
    ('under_6m', 'Hace menos de 6 meses'),
    ('6_12m', 'Hace entre 6 y 12 meses'),
    ('over_12m', 'Hace más de 12 meses'),
]

PREGNANCY_CHOICES = [
    ('none', 'Ninguno'),
    ('pregnant', 'Embarazo'),
    ('lactating', 'Lactancia'),
    ('postpartum_under_6m', 'Posparto de menos de 6 meses'),
]

EATING_DISORDER_CHOICES = [
    ('no', 'No'),
    ('yes', 'Sí'),
    ('prefer_not_say', 'Prefiero no decir'),
]

DIET_CHOICES = [
    ('omnivore', 'Como de todo'),
    ('vegetarian', 'Vegetariana'),
    ('vegan', 'Vegana'),
    ('pescatarian', 'Pescetariana'),
    ('halal', 'Halal'),
    ('kosher', 'Kosher'),
    ('other', 'Otra'),
]

ALLERGY_CHOICES = [
    ('gluten', 'Gluten'),
    ('milk', 'Leche'),
    ('egg', 'Huevo'),
    ('peanut', 'Maní'),
    ('tree_nut', 'Frutos secos'),
    ('fish', 'Pescado'),
    ('shellfish', 'Mariscos'),
    ('soy', 'Soya'),
    ('sesame', 'Ajonjolí'),
]

ALLERGY_SEVERITY_CHOICES = [
    ('mild', 'Leve o moderada'),
    ('anaphylaxis', 'Grave (puede causar anafilaxia)'),
]

INTOLERANCE_CHOICES = [
    ('lactose', 'Lactosa'),
    ('fructose', 'Fructosa'),
    ('fodmap', 'FODMAP'),
    ('other', 'Otra'),
]

# Orden canónico del día. Siempre van desayuno, almuerzo y cena; media mañana y merienda son
# opcionales (decisión confirmada: 3 a 5 comidas).
MEAL_SLOT_CHOICES = [
    ('breakfast', 'Desayuno'),
    ('mid_morning', 'Media mañana'),
    ('lunch', 'Almuerzo'),
    ('snack', 'Merienda'),
    ('dinner', 'Cena'),
]
REQUIRED_MEAL_SLOTS = ('breakfast', 'lunch', 'dinner')
OPTIONAL_MEAL_SLOTS = ('mid_morning', 'snack')
MEAL_SLOTS_MIN, MEAL_SLOTS_MAX = 3, 5

COOKING_CHOICES = [
    ('full', 'Puedo cocinar con todo lo necesario'),
    ('basic', 'Tengo lo básico para cocinar'),
    ('none', 'No puedo cocinar'),
]

BUDGET_CHOICES = [
    ('low', 'Bajo'),
    ('medium', 'Medio'),
    ('high', 'Alto'),
]

TRAINING_PLACE_CHOICES = [
    ('gym', 'En el gimnasio'),
    ('home', 'En casa'),
]

EQUIPMENT_CHOICES = [
    ('none', 'Ninguno'),
    ('dumbbells', 'Mancuernas'),
    ('bands', 'Bandas elásticas'),
    ('bench', 'Banco'),
    ('barbell', 'Barra'),
    ('machines', 'Máquinas'),
    ('cardio_machine', 'Máquina de cardio'),
    ('mat', 'Colchoneta'),
]

EXPERIENCE_CHOICES = [
    ('none', 'Nunca he entrenado'),
    ('beginner', 'Principiante'),
    ('intermediate', 'Intermedia'),
    ('advanced', 'Avanzada'),
]

ACTIVITY_CHOICES = [
    ('sedentary', 'Sedentaria (casi no me muevo)'),
    ('light', 'Ligera (me muevo algo en el día)'),
    ('moderate', 'Moderada (activa casi todos los días)'),
    ('active', 'Activa (trabajo o rutina muy activa)'),
]

# Cuestionario de aptitud física (7 preguntas Sí/No). [A VALIDAR] frente al PAR-Q+ oficial.
PARQ_QUESTIONS = [
    ('q1', 'Un médico te ha dicho que tienes un problema del corazón y que solo deberías hacer '
           'ejercicio con supervisión médica.'),
    ('q2', 'Sientes dolor en el pecho cuando haces actividad física.'),
    ('q3', 'En el último mes has sentido dolor en el pecho estando en reposo.'),
    ('q4', 'Pierdes el equilibrio por mareo o te has desmayado alguna vez.'),
    ('q5', 'Tienes un problema de huesos o articulaciones que empeora al hacer actividad física.'),
    ('q6', 'Tomas medicamentos para la presión arterial o para el corazón.'),
    ('q7', 'Conoces alguna otra razón por la que no deberías hacer actividad física.'),
]
PARQ_KEYS = tuple(key for key, _ in PARQ_QUESTIONS)

ACCESS_ACTION_CHOICES = [
    ('view', 'Consulta'),
    ('edit', 'Edición'),
    ('export', 'Descarga'),
    ('delete', 'Borrado'),
]

# Pasos del asistente de la ficha (F-04). El servidor valida todo al guardar; `error_steps`
# dice en qué pasos hay errores para llevar a la clienta al primero.
WIZARD_STEPS = [
    {'number': 1, 'key': 'basics', 'title': 'Datos básicos y meta'},
    {'number': 2, 'key': 'health', 'title': 'Salud'},
    {'number': 3, 'key': 'parq', 'title': 'Cuestionario de aptitud'},
    {'number': 4, 'key': 'food', 'title': 'Alimentación'},
    {'number': 5, 'key': 'training', 'title': 'Entrenamiento'},
]


def codes(choices):
    return tuple(code for code, _ in choices)


def label_map(choices):
    return dict(choices)


def labels_for(choices, selected):
    """Etiquetas de una lista de códigos (los desconocidos se devuelven tal cual)."""
    mapping = dict(choices)
    return [mapping.get(code, code) for code in (selected or [])]
