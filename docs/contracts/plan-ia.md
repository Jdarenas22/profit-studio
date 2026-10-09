# Contrato: ficha de salud, medidas de la clienta y planes con IA (ejercicio + alimentación)

- Estado: **PROPUESTA para aprobación. No hay código construido.**
- Fecha: 2026-10-08. Rama: `plan-ia-nutricion`. Autor: arquitecto-backend.
- Alcance de esta ronda: solo diseño. Nada de lo que sigue está implementado.
- Aviso: las reglas de salud de este documento (umbrales, banderas, pisos calóricos) son una propuesta técnica y están marcadas **[A VALIDAR]**: deben revisarlas un profesional de la salud/nutrición (y un abogado, en lo legal) antes de producción. No es asesoría médica ni legal.

---

## 0. Resumen en diez líneas

1. La clienta registra sus medidas y su ficha de salud (con consentimiento registrado). El entrenador puede corregir; la clienta confirma.
2. Un **motor de reglas en Python** decide si se puede generar (banderas rojas), calcula calorías y macros, y filtra los catálogos (ejercicios y alimentos) según alergias, dieta, lesiones y equipo.
3. A Gemini solo se le envían datos **sin identificar** y **ya filtrados**: nunca nombre, correo, teléfono, ni diagnósticos. Elige **solo de los catálogos** (por id) y no escribe cifras clínicas: las kcal y macros las calcula Python.
4. La salida de la IA se valida (esquema, ids permitidos, alergias, bandas de kcal, restricciones). Si falla: un reintento; si no, el intento queda `fallido` con el motivo y nunca se muestra.
5. El resultado es un **borrador** (`borrador-IA`) que llega a la bandeja del entrenador; la clienta no ve nada hasta que sea `aprobado`.
6. Ejercicio: el borrador es una `Routine` real **inactiva** (`is_active=False`) que el entrenador edita con el constructor existente; al aprobar se activa.
7. Alimentación: por comida, **exactamente 3 opciones equivalentes**; la clienta elige una por comida cada día y puede pedir "otra opción" (con tope y marcada para el entrenador).
8. La ejecución es asíncrona **sin Celery/Redis**: cola en la base de datos + hilo en el worker de gunicorn + recuperación de trabajos huérfanos.
9. Gemini en producción con datos reales **exige plan de pago (facturación habilitada)**: el nivel gratuito usa los datos para mejorar productos de Google y puede tener revisión humana (ver sección 4).
10. Se entrega en fases desplegables por separado (sección 14). La fase B incluye una bandeja mínima del entrenador, porque sin ella el borrador no tiene a dónde llegar.

---

## 1. Decisiones de arquitectura (resumen)

| Tema | Decisión | Por qué |
| --- | --- | --- |
| Apps nuevas | `apps/health` (consentimiento + ficha) y `apps/plans` (planes, trabajos, catálogo de alimentos, motor de reglas, cliente de IA) | Separa lo legal/sensible de la lógica de planes. `assessments` solo gana campos y vistas de medidas. |
| Capas | Vistas (HTTP) -> `services.py` (casos de uso) -> modelos/consultas. Reglas puras en `plans/rules/` (sin Django ni red). IA detrás de una interfaz en `plans/ai/` | Igual que el resto del proyecto, más testeable sin red. |
| Autorización | Toda vista con pk de cliente/plan/opción pasa por `apps/accounts/permissions.py`. Clienta: solo lo suyo y solo `approved`. No accesible = 404 | Regla obligatoria del proyecto. |
| Ficha | Filas versionadas (`HealthProfile`, una `is_current` por clienta) | Auditoría: cada plan guarda con qué versión se generó. |
| Catálogo de alimentos | **Nuevo `Food`** (tabla de composición con alérgenos y etiquetas de dieta). La IA elige `food_id`; Python calcula kcal/macros/medidas caseras | Más seguro que "palabras clave sobre texto libre": las alergias se excluyen por construcción. Ver pregunta 1. |
| Plan de ejercicio | `Routine` real inactiva + `Plan` que la enlaza | Reutiliza el constructor y toda la UI existente; las consultas de la clienta ya filtran `is_active=True`. |
| Cola de trabajos | Tabla `PlanGenerationJob` + hilo + recuperación | Sin infraestructura nueva. Ver sección 6. |
| Límites de costo | Contados en la **base de datos** (no en caché) | `ratelimit.py` usa LocMem por proceso con 2 workers: no sirve para dinero. |
| Admin de Django | **No registrar** modelos de salud/planes (excepto `ConsentTextVersion`, solo superusuaria) | El admin se salta la regla de asignación. |
| Dependencias | Una nueva: `requests` (versión fija, a verificar al construir). Sin SDK de Google | Menos dependencias transitivas; control total de timeouts; la API cambia rápido. Requiere tu permiso para instalar. |

---

## 2. Fase A: medidas de la clienta, ficha de salud y consentimiento

Se divide en **A1** (medidas + consentimiento, resuelve "no puedo colocar mis medidas") y **A2** (ficha completa).

### 2.1 Cambios a `BodyMeasurement` (app `assessments`, migración 0005)

| Campo | Tipo | Nota |
| --- | --- | --- |
| `source` | CharField(`trainer`/`member`), default `trainer` | Las filas existentes quedan `trainer` (todas las creó el entrenador). |
| `hip_cm` | Decimal(5,1), null | Rango 30-300 cm, igual que cintura. |
| `body_fat_pct` | Decimal(4,1), null | Rango 3-70 %. |
| `trainer` | ya existe, null | Vacío cuando la registra la clienta. |

- Validación: **se reutilizan** `BodyMeasurementForm`, `HeightField`, `_weight_field` y `_check_imc` de `apps/assessments/forms.py` (coma decimal, cm -> m, rangos, IMC 8-100). Se extiende con `hip_cm` y `body_fat_pct` (`DecimalInRangeField`). Un solo formulario sirve a la clienta y al entrenador.
- Estatura: obligatoria si la clienta no tiene ninguna estatura previa (ni en `InitialAssessment` ni en otra medición); si ya existe, opcional y se hereda para el IMC.
- Plausibilidad (aviso, no bloqueo): cambio de peso mayor a 5 kg respecto a la última medición de menos de 14 días -> se guarda con una nota interna `posible error de digitación` y se avisa al entrenador.
- `date` sigue siendo `auto_now_add` (no se puede poner fecha pasada). Tope: 3 mediciones por día por clienta.
- Peso y estatura **no se duplican en la ficha**: el servicio `latest_anthropometrics(user)` devuelve la última medición (peso, estatura con respaldo en `BodyMeasurement.height` -> `InitialAssessment.height`) y su antigüedad.
- La clienta puede **borrar solo sus propias mediciones del mismo día** (corregir un error). El entrenador puede editar o borrar cualquiera de sus clientes.

### 2.2 App `health` (migración 0001)

**`ConsentTextVersion`** (el texto de cada versión queda guardado en la base)

| Campo | Tipo |
| --- | --- |
| `purpose` | `health_data` (tratamiento de datos de salud) / `ai_processing` (envío sin identificar a Google Gemini) |
| `version` | entero; `unique(purpose, version)` |
| `body` | texto completo mostrado a la clienta |
| `is_current`, `published_at` | publicación; una vigente por finalidad |

Solo la superusuaria lo gestiona (admin). Una versión publicada no se edita: se crea la siguiente. El texto inicial es un **borrador con marcadores** (`[RAZÓN SOCIAL]`, `[NIT]`, `[CORREO DE DERECHOS]`) que debe aprobar un abogado antes de activar la función (variable `HEALTH_FEATURES_ENABLED`, falsa por defecto en producción).

**`ConsentRecord`**

| Campo | Tipo |
| --- | --- |
| `user` | FK clienta (CASCADE) |
| `text_version` | FK `ConsentTextVersion` (PROTECT) |
| `granted_at` | datetime |
| `revoked_at` | datetime, null |

- Solo la propia clienta puede otorgar o revocar (nunca el entrenador en su nombre).
- Casilla obligatoria **sin marcar** y **una casilla por finalidad** (la de IA es aparte y requerida solo para pedir planes con IA; se puede guardar la ficha sin ella).
- Se guarda fecha, usuario y versión. No se guarda IP (minimización).
- Si se publica una versión nueva, se pide consentimiento de nuevo antes del siguiente uso.
- Revocar `health_data`: ofrece borrar la ficha y los planes (sección 9).

**`HealthProfile`** (una fila por versión; `is_current` único por clienta)

| Grupo | Campos (cerrados = lista de códigos validada en servidor) |
| --- | --- |
| Control | `user`, `version`, `is_current`, `created_at`, `created_by`, `created_by_role` (`member`/`trainer`), `consent` (FK al consentimiento vigente), `confirmed_by_client_at` |
| Básicos | `birth_date`; `sex_for_calculation` (`F`/`M`/`NA`, se pide solo para las fórmulas); la meta es la que ya existe en `User.training_goal` (no se duplica); `target_weight_kg` opcional |
| Condiciones (cerrada, varias) | `diabetes_t1`, `diabetes_t2`, `prediabetes`, `hypertension`, `heart_disease`, `arrhythmia`, `stroke_history`, `kidney_disease`, `liver_disease`, `fatty_liver`, `thyroid_disease`, `asthma`, `osteoporosis`, `herniated_disc`, `cancer_active`, `bariatric_surgery`, `metabolic_disorder`, `gout`, `reflux_gastritis`, `ibs`, `celiac`, `other` |
| Control médico | `condition_controlled` (declara que están controladas y bajo seguimiento), `medical_clearance` + `clearance_date` (su médico autorizó hacer ejercicio; vence a los 12 meses) |
| Medicamentos (cerrada) | `insulin`, `hypoglycemic_drugs`, `antihypertensives`, `beta_blockers`, `anticoagulants`, `corticosteroids`, `thyroid_meds`, `other` |
| Lesiones (cerrada) | `knee`, `lower_back`, `shoulder`, `hip`, `ankle`, `wrist_elbow`, `neck` |
| Otros | `recent_surgery` (`none`/`under_6m`/`6_12m`/`over_12m`); `pregnancy_status` (`none`/`pregnant`/`lactating`/`postpartum_under_6m`); `eating_disorder_history` (`no`/`yes`/`prefer_not_say`) |
| PAR-Q (7 preguntas Sí/No) | `parq` = `{q1..q7}`: 1 cardiopatía con ejercicio solo supervisado; 2 dolor de pecho al hacer actividad; 3 dolor de pecho en reposo el último mes; 4 mareo o pérdida de conocimiento; 5 problema óseo/articular que empeora con la actividad; 6 medicamentos para presión o corazón; 7 otra razón para no hacer actividad. **[A VALIDAR]** frente a la versión oficial vigente del PAR-Q+ en español (se parafrasea, no se copia) |
| Texto libre (solo para el entrenador, **nunca a la IA**) | `other_condition_text`, `medications_text`, `injuries_text`, `disliked_foods_text`, `liked_foods_text` (máx. 300 caracteres c/u; se limpian y se muestran siempre escapados) |
| Alimentación | `diet_type` (`omnivore`/`vegetarian`/`vegan`/`pescatarian`/`halal`/`kosher`/`other`), `allergies` (`gluten`, `milk`, `egg`, `peanut`, `tree_nut`, `fish`, `shellfish`, `soy`, `sesame`), `allergy_severity` (`mild`/`anaphylaxis`), `intolerances` (`lactose`, `fructose`, `fodmap`, `other`), `meal_slots` (**siempre** `breakfast`, `lunch` y `dinner`; opcionalmente `mid_morning` y/o `snack`; entre 3 y 5 en total, decisión confirmada en A2), `meal_times` ({slot: "HH:MM"}), `cooking_access` (`full`/`basic`/`none`), `budget_level` (`low`/`medium`/`high`), `eats_out_per_week` (0-21) |
| Entrenamiento | `training_days_per_week` (1-6), `session_minutes` (20-120), `training_place` (`gym`/`home`), `equipment` (`none`, `dumbbells`, `bands`, `bench`, `barbell`, `machines`, `cardio_machine`, `mat`), `experience_level` (`none`/`beginner`/`intermediate`/`advanced`), `activity_level` (`sedentary`/`light`/`moderate`/`active`), `sleep_hours` (3-12) |

Fase C añade (migración `health` 0004; la 0003 es la de la ficha de A2) dos relaciones `disliked_foods` / `liked_foods` hacia `Food` para elegir del catálogo; hasta entonces solo existen los textos libres.

**`HealthAccessLog`**: quién (`actor`), de qué clienta, acción (`view`/`edit`/`export`/`delete`) y cuándo. Se escribe cada vez que un entrenador o la superusuaria abre o edita una ficha (principio de seguridad y acceso restringido de la Ley 1581).

### 2.3 Quién puede editar qué

| Dato | Clienta | Entrenador asignado | Superusuaria |
| --- | --- | --- | --- |
| Sus propias medidas | crear; borrar las del mismo día | crear/editar/borrar de sus clientes | todas |
| Ficha de salud | crear y editar (nueva versión) | corregir (nueva versión `created_by_role=trainer`); la clienta debe **confirmar** | todas |
| Consentimientos | otorgar/revocar solo los suyos | solo ver (que existen y su versión) | ver |
| Texto de consentimiento | no | no | publicar versiones |

- Regla de la confirmación: una ficha corregida por el entrenador queda con `confirmed_by_client_at = null`. **No se puede generar un plan con IA hasta que la clienta la confirme** (así lo que va a la IA siempre es lo que ella declaró o aceptó).
- Menores de 18 años (por `birth_date`): **no se habilita la ficha en línea** ni el consentimiento propio; pantalla "consulta con tu entrenador" (Ley 1581, art. 7: datos de niños y adolescentes; además Google exige 18+ para sus servicios). El entrenador gestiona manualmente.
- Acceso: `login_required` + membresía vigente (`membership_required`), igual que `member_routine`. Un entrenador que entra por rutas de clienta recibe 403.

### 2.4 Rutas de la fase A

| Método | Ruta | Nombre | Quién | Qué hace |
| --- | --- | --- | --- | --- |
| GET | `/assessments/me/measurements/` | `member_measurement_list` | clienta | historial propio + gráfico |
| GET, POST | `/assessments/me/measurements/add/` | `member_measurement_add` | clienta | formulario de medidas (si falta el consentimiento `health_data`, lo pide primero) |
| POST | `/assessments/me/measurements/<pk>/delete/` | `member_measurement_delete` | clienta | solo propias, `source=member`, mismo día |
| GET, POST | `/assessments/measurements/<pk>/edit/` | `trainer_measurement_edit` | entrenador | `ensure_client_access(request, m.user)` |
| POST | `/assessments/measurements/<pk>/delete/` | `trainer_measurement_delete` | entrenador | idem |
| GET, POST | `/health/consent/<purpose>/` | `member_consent` | clienta | muestra texto vigente; POST otorga (`accept=on`, `text_version`) |
| POST | `/health/consent/<purpose>/revoke/` | `member_consent_revoke` | clienta | revoca |
| GET, POST | `/health/profile/` | `member_health_profile` | clienta | ver/editar ficha (cada guardado = nueva versión) |
| POST | `/health/profile/confirm/` | `member_health_profile_confirm` | clienta | confirma la versión vigente |
| GET | `/health/profile/export/` | `member_health_export` | clienta | descarga JSON de sus datos |
| GET, POST | `/health/profile/delete/` | `member_health_delete` | clienta | confirmación y borrado de ficha y planes |
| GET | `/health/clients/<client_pk>/profile/` | `trainer_health_profile` | entrenador | ver ficha + banderas calculadas (escribe `HealthAccessLog`) |
| GET, POST | `/health/clients/<client_pk>/profile/edit/` | `trainer_health_profile_edit` | entrenador | corregir (nueva versión sin confirmar) |

Todas las respuestas con datos de salud llevan `Cache-Control: no-store`. Ningún dato de salud viaja en la URL.

### 2.5 Pruebas de la fase A

Autorización (matriz sobre cada ruta de entrenador: superusuaria, entrenador A propio, entrenador B ajeno -> 404, cliente sin asignar solo superusuaria, clienta -> 403, anónimo -> login); la clienta A no puede ver ni borrar mediciones de B (404); no borra mediciones de otro día ni del entrenador; validaciones (coma decimal, cm, IMC incoherente, cintura fuera de rango, `hip_cm`, `body_fat_pct`); consentimiento (sin casilla no guarda, versión vieja pide de nuevo, el entrenador no puede otorgar); ficha (cada guardado crea versión y deja una sola `is_current`; corrección del entrenador queda sin confirmar; menor de edad bloqueado; exportar y borrar); `HealthAccessLog` se escribe; ninguna vista de salud responde sin `no-store`.

---

## 3. Motor de reglas determinista (`apps/plans/rules/`, Python puro)

Entrada: última medición, versión vigente de la ficha, meta, catálogos. Salida: `Eligibility` = banderas rojas, amarillas, datos faltantes, alcance permitido (`exercise`, `nutrition`) y parámetros calculados. **La IA nunca decide si se puede generar.**

### 3.1 Requisitos previos (bloquean con mensaje de qué falta)

| Código | Condición | Acción |
| --- | --- | --- |
| `D01_CONSENT` | falta consentimiento `health_data` o `ai_processing` vigente | pedirlo |
| `D02_PROFILE` | no hay ficha o no está confirmada por la clienta | completar/confirmar |
| `D03_MEASURE` | última medición con más de 30 días o sin estatura | registrar medidas |
| `D04_INCOHERENT` | IMC fuera de 8-100, o edad calculada fuera de 18-100 | corregir datos |
| `D05_QUOTA` | superó los límites de uso (sección 6.4) | esperar / avisar |

### 3.2 Banderas rojas (bloquean la generación automática y derivan) **[A VALIDAR por profesional de salud/nutrición]**

Alcance: `E` = ejercicio, `N` = alimentación. Sin anulación en la v1 (pregunta 3): el entrenador arma el plan a mano con el constructor existente.

| Código | Disparador | Bloquea | Acción |
| --- | --- | --- | --- |
| `R01_MINOR` | menor de 18 años | E + N | derivar; plan manual con acudiente/profesional |
| `R02_PREGNANCY` | embarazo, lactancia o posparto de menos de 6 meses | E + N | derivar a ginecología/nutrición |
| `R03_EATING_DISORDER` | antecedentes `yes` o `prefer_not_say` | N | derivar a profesional; ejercicio queda en amarilla |
| `R04_PARQ_SYMPTOMS` | PAR-Q 2, 3 o 4 = Sí (dolor de pecho, mareo/desmayo) | E | derivar a médico; nutrición en amarilla |
| `R05_CARDIAC` | PAR-Q 1 = Sí, o `heart_disease`, `arrhythmia`, `stroke_history` | E + N | derivar a médico/cardiología |
| `R06_NEEDS_CLEARANCE` | PAR-Q 5, 6 o 7 = Sí **sin** autorización médica vigente (menos de 12 meses) | E | pedir autorización médica; con ella pasa a amarilla `Y05` |
| `R07_BMI_LOW` | IMC menor de 17,5; o 17,5-18,49 con meta de bajar de peso o tonificar | E + N (primer caso), N (segundo) | derivar a nutrición clínica |
| `R08_BMI_VERY_HIGH` | IMC de 40 o más | N | derivar a médico/nutrición; ejercicio en amarilla |
| `R09_DIABETES_T1_INSULIN` | `diabetes_t1`, o medicamento `insulin` o `hypoglycemic_drugs` | E + N | derivar (riesgo de hipoglucemia) |
| `R10_KIDNEY` | `kidney_disease` (la ficha no puede estadificar: se asume lo más seguro) | N | derivar a nefrología/nutrición renal; ejercicio amarilla |
| `R11_LIVER_METABOLIC` | `liver_disease` (cirrosis/hepatitis), `metabolic_disorder`, `bariatric_surgery` | N | derivar |
| `R12_CANCER` | `cancer_active` | E + N | derivar a oncología |
| `R13_RECENT_SURGERY` | cirugía de menos de 6 meses | E | derivar; volver con alta médica |
| `R14_NOT_CONTROLLED` | condición intermedia (hipertensión, diabetes tipo 2, tiroides, asma) sin `condition_controlled` o sin autorización médica vigente | E (y N si diabetes) | pedir control/autorización |
| `R15_AGE_HIGH` | 80 años o más | E | derivar a valoración médica |

### 3.3 Banderas amarillas (se genera, con advertencias visibles y **revisión obligatoria** del entrenador; el botón Aprobar exige marcar "revisé las advertencias")

| Código | Disparador | Efecto determinista |
| --- | --- | --- |
| `Y01_HYPERTENSION` | hipertensión controlada | sin ejercicios con `breath_hold`; series máx. 3; alimentos `high_sodium` excluidos |
| `Y02_DIABETES_T2` | diabetes tipo 2 o prediabetes controlada | alimentos `high_free_sugar` excluidos; reparto de carbohidratos parejo; sin déficit mayor a 15 % |
| `Y03_THYROID_ASTHMA` | tiroides/asma controlados | intensidad máx. `moderate` en las primeras 2 semanas |
| `Y04_JOINT` | lesión articular, osteoporosis, hernia discal, PAR-Q 5 | se excluyen ejercicios con `risk_tags` de esa zona o `high_impact` |
| `Y05_CLEARED` | PAR-Q con Sí pero con autorización médica vigente | revisión reforzada |
| `Y06_ANTICOAGULANTS` | `anticoagulants` | sin `high_impact`; **nota**: la dieta con anticoagulantes (vitamina K) la debe revisar un nutricionista |
| `Y07_SEVERE_ALLERGY` | alergia con `anaphylaxis`, o `celiac` | exclusión estricta de trazas (`trace_tags`); el entrenador debe revisar cada alimento |
| `Y08_AGE_SENIOR` | 70-79 años | sin saltos ni cargas axiales pesadas; volumen bajo |
| `Y09_BMI_HIGH` | IMC 35-39,9 | sin `high_impact`; progresión lenta |
| `Y10_ED_EXERCISE` | antecedentes de trastorno alimentario (`R03`) | sin cardio compensatorio adicional; sin meta de peso |
| `Y11_REHAB_GOAL` | meta `rehab` | requiere lesión declarada; ejercicios solo de baja intensidad |
| `Y12_OTHER_TEXT` | texto libre en "otra condición/medicamento" | el texto no se envía a la IA; el entrenador debe leerlo |
| `Y13_GI` | reflujo, colon irritable, gota, intolerancias | alimentos etiquetados del catálogo excluidos |
| `I01_SLEEP` | menos de 5 h de sueño | solo informativa |

Mensaje a la clienta ante una bandera roja (genérico, sin diagnósticos): "Por la información de salud que registraste, lo más seguro es que tu médico o nutricionista te valore antes de armar un plan. Tu entrenador ya fue avisado y te contactará." El entrenador recibe el trabajo en estado `blocked` con los **códigos** de bandera (no texto médico en el correo).

### 3.4 Cálculo energético y macros (Python, con `Decimal`; la IA no inventa cifras) **[A VALIDAR]**

| Paso | Regla |
| --- | --- |
| TMB (Mifflin-St Jeor) | `10*kg + 6.25*cm - 5*edad + s`, con `s = +5` (M), `-161` (F), `-78` (NA, promedio) |
| GET | TMB x factor de actividad: sedentaria 1,2; ligera 1,375; moderada 1,55; activa 1,725 |
| Meta -> ajuste | bajar de peso: déficit = menor entre 20 % del GET y 500 kcal (Y02: 15 %); tonificar con IMC de 25 o más: menor entre 10 % y 300 kcal, si no mantenimiento; ganar músculo: superávit = menor entre 10 % y 300 kcal; cardio/rendimiento/salud: mantenimiento |
| Piso calórico | mujer 1200, hombre 1500, NA 1350 kcal/día. Si el piso supera el GET, el objetivo es el GET (mantenimiento) y se avisa "no se aplicó déficit" |
| Ritmo máximo | el déficit permitido implica menos de 0,5 kg/semana (kcal x 7 / 7700); se muestra a la clienta |
| Peso meta | si `target_weight_kg` implica IMC menor de 20, se recorta y se marca amarilla |
| Proteína | 1,6 g/kg (bajar), 1,8 g/kg (ganar), 1,2 g/kg (otras), sobre `peso de referencia = min(peso, peso con IMC 27)`; tope 2,0 g/kg y 35 % de la energía |
| Grasa | 28 % de la energía (mínimo 20 % y 0,6 g/kg) |
| Carbohidratos | el resto; fibra mínima 25 g (14 g por 1000 kcal) |
| Redondeo | kcal a 10; gramos a 1 |

Reparto por comida (`meal_slots` de la ficha): 3 comidas = desayuno 30 / almuerzo 40 / cena 30; 4 = 25 / 35 / merienda 15 / 25; 5 = 25 / media mañana 10 / 30 / merienda 10 / 25 (%). Como la ficha exige desayuno, almuerzo y cena y permite media mañana y/o merienda, falta definir 4 comidas **con media mañana y sin merienda**: propuesta 25 / 10 / 35 / 30 **[A VALIDAR]** (la fase B debe confirmarla).

### 3.5 Filtrado de catálogos (antes de llamar a la IA)

- **Alimentos permitidos** = `Food` activos, menos: alérgenos y trazas declarados (por etiqueta), dieta (`excluded_by_diets`), intolerancias, no me gusta (cuando exista el M2M), y los que choquen con banderas amarillas (`high_sodium`, `high_free_sugar`, `fodmap`, `high_purine`...). Si quedan menos de 40 alimentos o no hay proteína disponible en cada comida -> `D06_FEW_FOODS` (bloquea, avisa al entrenador).
- **Ejercicios permitidos** = `Exercise.is_active` y `level` menor o igual al nivel de la clienta, equipo contenido en el disponible, sin `risk_tags` que choquen con sus lesiones/banderas, `intensity` dentro del tope. Los ejercicios **sin etiquetar** se excluyen cuando la clienta tiene cualquier lesión o bandera amarilla (prudencia).
- La IA recibe solo los permitidos, con `id`, nombre, categoría y datos mínimos.

### 3.6 Validación posterior de la salida de la IA (nada sin validar llega a pantalla)

| Código | Comprobación |
| --- | --- |
| `V01_SCHEMA` | JSON parseable, `schema_version` correcto, campos exactos (sin claves extra), tipos y rangos; **exactamente 3 opciones** por comida; `finishReason` normal (no `SAFETY` ni `MAX_TOKENS`) |
| `V02_IDS` | todo `food_id`/`exercise_id` pertenece al conjunto permitido enviado (cualquier id fuera -> rechazo) |
| `V03_ALLERGEN` | segunda capa: lista de palabras clave (sin tildes, en minúsculas, con sinónimos colombianos: maní/cacahuate, camarón/langostino/jaiba, leche/suero/crema/queso/kumis, trigo/harina/pan/pasta/avena por trazas de gluten, etc.) sobre **todo texto libre** (nombre del plato, preparación, observaciones, notas) contra alergias, intolerancias y dieta declaradas |
| `V04_KCAL` | tras el ajuste automático de porciones (escala 0,7-1,3, redondeo a 5 g) cada opción queda a +/-5 % del objetivo de su comida; para **toda combinación** de opciones el total diario queda en la banda `[max(piso, objetivo -5 %), objetivo +5 %]` |
| `V05_MACROS` | proteína diaria mínima de 90 % del objetivo; grasa de 20 % o más de la energía (todo calculado por Python con la tabla `Food`) |
| `V06_VARIETY` | las 3 opciones de una comida comparten menos de 50 % de alimentos y no repiten nombre |
| `V07_EXERCISE` | días = los pedidos; 3-8 ejercicios por día y no más de `session_minutes/6`; sin repetir ejercicio en un día; series 1-6; repeticiones con patrón `^\d{1,2}(-\d{1,2})?$` o `^\d{1,3} ?seg$`; descanso 20-180 s; respeta topes de las banderas amarillas |
| `V08_TEXT` | longitudes máximas (coinciden con `RoutineDay.name` 100, `reps` 50); sin HTML ni enlaces ni correos ni teléfonos; sin recomendar suplementos, medicamentos, ayunos prolongados ni "detox"; sin promesas médicas |

Si falla: **un reintento** (se añaden a la petición los códigos de error, sin datos personales). Si vuelve a fallar: el trabajo queda `failed` con `error_code` (`validation_failed:V03_ALLERGEN`...). La salida no validada **no se guarda** (solo los códigos).

---

## 4. Cliente de IA (Gemini)

### 4.1 Verificación en la documentación oficial (consultada el 2026-10-08)

| Tema | Hallazgo | Fuente |
| --- | --- | --- |
| Uso de datos, servicios **sin costo** | Google usa lo que envías y las respuestas "para proveer, mejorar y desarrollar productos y servicios de Google y tecnologías de aprendizaje automático"; **revisores humanos pueden leer** las entradas y salidas; advierte "no envíes información sensible, confidencial o personal" | Términos de la API de Gemini, "Unpaid Services", actualizados 2026-04-28 |
| Uso de datos, servicios **de pago** (facturación habilitada) | "Google no usa tus prompts ni respuestas para mejorar nuestros productos"; guarda registros solo para detectar violaciones de la política de uso prohibido, de forma transitoria | Mismos términos, "Paid Services" |
| Menores | Se debe ser mayor de 18 años y no crear servicios dirigidos a menores | Mismos términos |
| Cómo pasar a pago | AI Studio > Projects > "Set up billing"; Nivel 1 = cuenta de facturación activa vinculada; prepago mínimo USD 5 (o pospago si es elegible). La clave debe pertenecer al proyecto con facturación | Billing y Rate limits (2026-09-02) |
| Disponibilidad en Colombia | Colombia aparece en las regiones disponibles | Available regions (2026-04-28) |
| Modelos | Familia estable `gemini-3.x-flash` y `-flash-lite`; los `2.5` quedan con acceso limitado; `gemini-2.0-flash` ya fue **apagado** (los IDs cambian: por eso el modelo va en variable de entorno) | Models (2026-10-06) |
| Precios (pago, por 1 M de tokens, entrada/salida) | Flash 3.6/3.7/3.8: USD 0,75 / 3,75 **válido hasta 2026-12-31; se duplica desde 2027-01-01**. 3.5 Flash-Lite: 0,30 / 2,50. 3.1 Flash-Lite: 0,25 / 1,50 | Pricing (2026-10-07) |
| Endpoint | `generateContent` sigue vigente y recomendado (la API "Interactions" es adicional) | Changelog |
| Salida estructurada | JSON con esquema (subconjunto de JSON Schema: `enum`, `minItems`/`maxItems`, `minimum`/`maximum`, `required`, `additionalProperties`); es JSON sintácticamente válido pero **hay que validar en la aplicación** | Structured output |

**El implementador debe reconfirmar** antes de codificar: el nombre exacto de los campos de `generationConfig` (`responseMimeType` + `responseSchema` o `responseJsonSchema`) en https://ai.google.dev/api/generate-content, el modelo a usar y los precios. Si no se puede verificar, no se asume.

Costo orientativo (estimación mía, no promesa): una comida usa unos 6 mil tokens de entrada (catálogo filtrado) y 1 mil de salida; con los precios de Flash de arriba, un plan de alimentación completo (5 llamadas) más uno de ejercicio queda en el orden de **unos pocos centavos de dólar**. Los tokens de "pensamiento" se cobran como salida. Se mide con `tokens_in/out` por trabajo y se confirma con los primeros casos reales.

### 4.2 Requisito de producción (bloqueante)

> Con datos reales de clientas, la clave de Gemini **debe pertenecer a un proyecto con facturación habilitada (servicios de pago)**. El nivel gratuito puede usar los datos para mejorar productos de Google y permite revisión humana, lo cual es incompatible con datos de salud.

El código no puede verificar el nivel, así que exige una confirmación explícita: `AI_PLANS_MODE=live` solo arranca si existen `GEMINI_API_KEY`, `GEMINI_MODEL` **y** `GEMINI_BILLING_CONFIRMED=true`. Si falta cualquiera, `production.py` no arranca en modo `live` (mismo patrón que `SECRET_KEY`).

### 4.3 Modos y desarrollo

| `AI_PLANS_MODE` | Comportamiento |
| --- | --- |
| `off` (por defecto en producción) | La función no se ofrece. |
| `simulated` (por defecto con `DEBUG`) | `FakeAIClient` devuelve respuestas de ejemplo válidas, construidas con los ids del catálogo permitido (así pasan las validaciones con cualquier catálogo). No hay red ni costo. Los planes quedan `is_simulated=True`, con aviso visible "MODO DE PRUEBA", y **no se pueden aprobar fuera de `DEBUG`**. |
| `live` | `GeminiClient` (REST con `requests`). |

Interfaz `AIClient.generate_json(task, system, payload, schema, job_ref) -> AIResult(data, usage, model, finish_reason)`; errores `AITransientError` (429, 5xx, timeout), `AIPermanentError` (400/401/403), `AIBlockedError` (seguridad). Se inyecta con `get_ai_client()`; las pruebas lo reemplazan. La clave va en la cabecera `x-goog-api-key` (no en la URL, porque las URL acaban en registros y excepciones), nunca en el código ni en logs.

### 4.4 Minimización de lo que se envía

- **Se envía**: identificador aleatorio del trabajo (`job_ref`), sexo para cálculo, edad en años, meta, nivel, días y minutos, preferencias, objetivos ya calculados, y los catálogos filtrados. Sin diagnósticos: las restricciones de salud ya se aplicaron al filtrar; a la IA solo le llega un booleano genérico `conservative=true` cuando hay banderas amarillas.
- **No se envía nunca**: nombre, correo, teléfono, usuario, id de usuario, fecha de nacimiento, entrenador, ni texto libre de la ficha. La única entrada de texto libre es la nota del entrenador al regenerar (máx. 500 caracteres, limpiada) y su salida se valida igual.
- Los datos van en un JSON estructurado dentro del mensaje; la instrucción de sistema fija el rol, ordena ignorar cualquier instrucción dentro de los datos y responder solo JSON (defensa contra inyección de instrucciones).
- Logs: solo `job_id`, `kind`, estado, tokens, latencia y `error_code`. **Nunca** prompts, respuestas, ficha ni el cuerpo de error de Google. Prompts versionados en archivos (`PROMPT_VERSION` guardado en el trabajo).

### 4.5 Ejemplos de contrato con la IA

**Entrada (comida del almuerzo, ya filtrada)**

```json
{
  "task": "meal_slot",
  "job_ref": "5f0c2a4e-9d1b-4c7a-8b1e-0a1b2c3d4e5f",
  "slot": {"code": "lunch", "target_kcal": 560, "protein_g": 35, "carbs_g": 65, "fat_g": 17},
  "client": {"age": 34, "sex": "F", "goal": "weight_loss", "diet": "omnivore",
             "cooking": "basic", "budget": "low", "conservative": false},
  "allowed_foods": [
    {"id": 31, "name": "Pechuga de pollo", "group": "proteina_animal",
     "kcal100": 165, "p100": 31.0, "c100": 0.0, "f100": 3.6},
    {"id": 58, "name": "Arroz blanco cocido", "group": "cereales", "kcal100": 130, "p100": 2.7, "c100": 28.0, "f100": 0.3}
  ],
  "liked_food_ids": [31],
  "avoid_repeating_names": ["Pollo guisado con arroz"],
  "trainer_notes": null
}
```

**Salida de alimentación por comida (`meal-slot/1`)**: solo ids y gramos de orientación; las kcal, macros y medidas caseras las calcula Python desde `Food`.

```json
{
  "schema_version": "meal-slot/1",
  "slot_code": "lunch",
  "options": [
    {"name": "Pollo guisado con arroz y ensalada",
     "items": [{"food_id": 31, "grams": 120}, {"food_id": 58, "grams": 140}, {"food_id": 102, "grams": 100}],
     "preparation": "Guisa el pollo con cebolla y tomate; sirve con el arroz y la ensalada."},
    {"name": "Sudado de res con papa y habichuela", "items": [{"food_id": 44, "grams": 110}], "preparation": "..."},
    {"name": "Lentejas con arroz y huevo cocido", "items": [{"food_id": 77, "grams": 180}], "preparation": "..."}
  ]
}
```

(El esquema exige `options` con `minItems = maxItems = 3`; `items` de 2 a 6; `grams` 5-600.)

**Salida de ejercicio (`exercise-plan/1`)**

```json
{
  "schema_version": "exercise-plan/1",
  "routine_name": "Plan de 4 semanas: tonificación (borrador IA)",
  "general_notes": "Calienta 5 minutos antes de cada sesión.",
  "days": [
    {"name": "Día 1: tren inferior",
     "exercises": [
       {"exercise_id": 12, "sets": 3, "reps": "10-12", "rest_seconds": 60, "observations": "Controla la bajada."},
       {"exercise_id": 19, "sets": 3, "reps": "12", "rest_seconds": 60, "observations": ""}
     ]}
  ]
}
```

---

## 5. Plan de ejercicio (Fase B): cómo se vincula

- **Catálogo de ejercicios**: la migración `exercises` 0004 añade a `Exercise`: `equipment` (lista de códigos), `risk_tags` (lista: `knee`, `lower_back`, `shoulder`, `neck`, `wrist_elbow`, `hip`, `ankle`, `high_impact`, `breath_hold`, `heavy_axial`) e `intensity` (`low`/`moderate`/`high`), todos opcionales. **Prerrequisito de la fase B**: el equipo de ProFit etiqueta los ejercicios activos (un comando lista los que faltan; el formulario de ejercicio del entrenador gana estos campos). Sin etiquetas, el motor no puede garantizar las restricciones.
- **Borrador = `Routine` real con `is_active=False`**, con sus `RoutineDay` y `RoutineExercise` creados desde la salida validada. `Plan.routine` (OneToOne) lo enlaza.
- **Por qué sin riesgo de filtración**: todas las vistas de la clienta (`member_routine`, `dashboard`) ya filtran `is_active=True`. Hay **un hueco existente**: `mark_day_complete` no filtra `is_active`; en la fase B se corrige (`routine__is_active=True`) y se prueba.
- El entrenador edita con el constructor actual (`trainer_routine_builder`, que ya valida acceso con `clients_for_trainer` y no filtra por `is_active`). La bandeja enlaza a él.
- **Al aprobar**: transacción -> `routine.is_active=True`, el `Plan` pasa a `approved`, el plan anterior de la clienta pasa a `superseded` y su rutina se desactiva. Verificaciones: la rutina tiene al menos un día con ejercicios.
- Las ediciones del entrenador son de su responsabilidad, pero al aprobar se vuelve a correr el chequeo de restricciones y se **muestra como advertencia** (no bloquea al entrenador) cualquier ejercicio que ahora choque con la ficha.
- Vigencia: 4 semanas (`Plan.valid_until`); pasada esa fecha se invita a la clienta a registrar medidas y pedir revisión.

---

## 6. Ejecución asíncrona y robustez

### 6.1 Opción elegida: cola en base de datos + hilo + recuperación (sin Celery/Redis)

1. La vista crea `PlanGenerationJob` (`queued`) y, en `transaction.on_commit`, lanza un **hilo daemon** en el mismo worker de gunicorn. El hilo reclama el trabajo con un `UPDATE ... WHERE status='queued'` atómico (si dos hilos compiten, solo uno gana).
2. El estado vive en la base. La página de la clienta/entrenador consulta cada pocos segundos (fragmento HTMX; se detiene con HTTP 286 al terminar).
3. **Recuperación**: el hilo escribe `heartbeat_at` antes de cada llamada a Gemini. Un trabajo `running` con latido de más de 3 minutos (worker reiniciado, redeploy) vuelve a `queued` (o `failed` si agotó intentos). La recuperación corre (a) cuando alguien consulta el estado, (b) al lanzar cualquier trabajo nuevo y (c) con el comando opcional `process_plan_jobs` (se puede programar con un cron de Railway si algún día se quiere).
4. En pruebas y desarrollo: `AI_PLANS_RUN_INLINE=true` ejecuta en el mismo hilo, sin concurrencia.

Por qué no Celery (estaba declarado sin ninguna tarea y se quitó del proyecto en la limpieza de octubre de 2026): necesita Redis y un servicio worker aparte (más infraestructura a operar y pagar). Por qué no hilo "puro": se pierde trabajo en cada redeploy; la tabla + recuperación lo cubre. Si el volumen crece, la misma tabla sirve de cola para Celery sin rediseñar.

### 6.2 Tiempos y reintentos

| Parámetro | Valor |
| --- | --- |
| Conexión / lectura por llamada | 5 s / 60 s (`GEMINI_TIMEOUT_SECONDS`) |
| Reintentos por error transitorio (429, 5xx, timeout) | hasta 3 intentos, espera 2 s, 6 s, 18 s + variación aleatoria; respeta `Retry-After` |
| Errores permanentes (400, 401, 403, bloqueo de seguridad) | no se reintentan: `failed` con `error_code` |
| Falla de validación posterior | 1 reintento con los códigos de error; luego `failed` |
| Plan de alimentación | una llamada **por comida** (respuestas pequeñas, el reintento es barato); progreso "2 de 5" |
| Presupuesto total del trabajo | 4 minutos; al pasarlo `failed:timeout` |

### 6.3 Idempotencia

- `request_token` (UUID en el formulario) único: recargar o doble clic no crea otro trabajo.
- Restricción parcial: un solo trabajo `queued/running` por (clienta, tipo).
- La creación del `Plan` y del trabajo exitoso ocurre en una transacción; si el hilo muere antes de confirmar, el trabajo se recupera y reintenta sin duplicar.

### 6.4 Límites de uso (control de costos, contados en la base)

| Límite | Valor inicial (configurable) |
| --- | --- |
| Generaciones por clienta | 3 por 7 días (cuentan también las fallidas; las bloqueadas por banderas no) |
| "Otra opción" por clienta | 3 por comida por semana y 6 por día |
| Regeneraciones pedidas por un entrenador | 10 por día por entrenador |
| Global | 100 trabajos por día (`AI_PLANS_DAILY_JOB_LIMIT`); interruptor de apagado `AI_PLANS_MODE=off` |

Los trabajos guardan `tokens_in/out` para ver el gasto mensual (visible a la superusuaria).

### 6.5 Errores al usuario

Mensajes genéricos en español ("No pudimos generar el borrador. Tu entrenador fue avisado." / "El servicio está ocupado, lo intentaremos de nuevo."). Nunca trazas ni texto de Google. Un manejador registra con `logger.exception` solo clase de error y `job_id`.

---

## 7. Plan de alimentación (Fase C)

### 7.1 Modelos (app `plans`, migración 0002)

| Modelo | Campos clave |
| --- | --- |
| `Food` (catálogo) | `name`, `group` (cereales, tubérculos, verduras, frutas, lácteos, proteína animal, leguminosas, grasas, azúcares, bebidas), `kcal100`, `protein100`, `carbs100`, `fat100`, `fiber100`, `household_label` ("1 taza cocida"), `household_grams`, `allergen_tags`, `trace_tags`, `diet_tags` (qué dietas lo permiten), `condition_tags` (`high_sodium`, `high_free_sugar`, `fodmap`, `high_purine`), `source` (p. ej. ICBF TCAC 2018, a verificar licencia), `validated_by`, `is_active`, `is_common` |
| `NutritionPlan` | `plan` (OneToOne `Plan`), `kcal_target`, `protein_g`, `carbs_g`, `fat_g`, `fiber_g`, `tmb`, `get`, `activity_factor`, `adjustment_pct`, `floor_applied`, `meals_per_day`, `formula` |
| `MealSlot` | `nutrition_plan`, `code`, `name`, `time_hint`, `kcal_target`, `protein_g`, `carbs_g`, `fat_g`, `order` |
| `MealOption` | `slot`, `position` (1-3, entre las activas), `name`, `preparation`, `kcal`/`protein_g`/`carbs_g`/`fat_g` (calculados), `origin` (`ai`/`trainer`/`client_regen`), `needs_review`, `is_active`, `replaces` (FK a sí misma) |
| `MealOptionItem` | `option`, `food` (FK), `grams`; la medida casera ("1 1/2 tazas, 140 g") se calcula, no la escribe la IA |
| `MealChoice` | `client`, `slot`, `option`, `date`, `completed_at` (adherencia); único por (`client`, `slot`, `date`) |

### 7.2 Cómo se "juega" con las propuestas

- Cada comida muestra **3 opciones equivalentes** (misma energía +/-5 %). La clienta elige una por comida y por día (`MealChoice`); puede cambiar de opción el mismo día y combinar libremente: toda combinación cae en la banda diaria (validación `V04`).
- **Intercambios**: la lista la calcula Python desde `Food` (mismo grupo, porción de igual energía), no la IA. Ejemplo: "1 porción de cereal = 1/2 taza de arroz = 1 papa mediana = 1 arepa pequeña".
- **Lista de compras (opcional)**: suma de gramos por alimento según las elecciones de los próximos 7 días (si no hay elecciones, supone rotar A-B-C).
- **"Otra opción"** (`POST .../slots/<id>/alternatives/`): crea un trabajo `meal_alternative` para esa comida con el mismo filtro y validación; la opción rechazada se archiva (`is_active=False`, `replaces`) y la nueva queda `origin=client_regen`, `needs_review=True`: el entrenador la ve en su bandeja con una insignia, sin bloquear a la clienta (el contenido ya pasó la validación determinista). Sujeta a los límites de la sección 6.4. Se mantiene siempre un máximo de 3 opciones activas por comida.
- Mejora opcional (pregunta 11): generar 3 opciones de reserva por comida aprobadas por el entrenador para que "otra opción" sea instantánea y sin IA.

---

## 8. Estados, versiones y bandeja del entrenador (Fases B mínima + D completa)

### 8.1 Modelos (app `plans`, migración 0001)

| Modelo | Campos clave |
| --- | --- |
| `Plan` | `client`, `kind` (`exercise`/`nutrition`), `version`, `status`, `parent` (versión anterior), `routine` (OneToOne, ejercicio), `job` (origen), `health_profile` (FK `SET_NULL`), `measurement` (medición base), `flags` (JSON de códigos amarillos), `warnings_ack_by/at`, `is_simulated`, `model_name`, `prompt_version`, `ruleset_version`, `reviewed_by`, `approved_by`, `approved_at`, `note_for_client`, `internal_notes`, `reviewer_credential`, `valid_until`, `archived_at`. Restricción única parcial: un `approved` por (`client`, `kind`) |
| `PlanGenerationJob` | `kind` (`exercise`/`nutrition`/`meal_alternative`), `client`, `requested_by`, `status` (`queued`/`running`/`succeeded`/`failed`/`blocked`), `plan`, `slot`, `request_token`, `health_profile`, `attempts`, `max_attempts`, `locked_at`, `heartbeat_at`, `started_at`, `finished_at`, `error_code`, `flags` (códigos, no texto), `progress_done/total`, `tokens_in/out`, `trainer_notes` |
| `PlanEvent` | `plan`, `actor`, `action` (`created`, `taken_for_review`, `edited`, `regenerate_requested`, `approved`, `archived`, `cloned`, `client_alternative`), `note`, `at` |
| `TrainerCredential` | `user` (OneToOne), `can_validate_nutrition`, `registration_label` (p. ej. "Nutricionista-dietista, RETHUS N.º ..."), gestionado por la superusuaria |

Estados del plan: `draft_ai` -> `in_review` (pasa solo cuando el entrenador lo abre para editar o pulsa "Tomar para revisión") -> `approved` -> `superseded` (llegó uno aprobado nuevo) o `archived` (descartado/regenerado). El intento fallido vive en el trabajo (`failed`/`blocked`), no como plan, para no guardar salida sin validar.

### 8.2 Reglas

- Un plan `approved` es **inmutable**. Para cambiarlo, el entrenador pulsa "Clonar a borrador": crea la versión siguiente en `in_review`; al aprobarla, la anterior queda `superseded`. Eso da el historial de versiones.
- **Regenerar con notas**: el entrenador escribe una nota; se crea un trabajo nuevo con `parent`; al terminar la versión nueva queda `draft_ai` y la anterior `archived` (motivo en `PlanEvent`).
- **Aprobar** exige: pertenecer al entrenador asignado o ser superusuaria; si hay banderas amarillas, marcar "revisé las advertencias"; el plan no está **obsoleto** (si la ficha cambió después de generarlo, se muestra "generado con una ficha anterior" y se exige confirmación explícita); no `is_simulated` fuera de `DEBUG`; y, para alimentación, que el aprobador tenga `can_validate_nutrition` (pregunta 2). La frase de la credencial se imprime en el plan.
- **Aviso**: indicador numérico en el menú del panel del entrenador (se carga como fragmento aparte, `inbox/badge/`, para no consultar en cada página) y **correo** al entrenador asignado (o a las superusuarias si la clienta no tiene entrenador) con la salida de `send_mail` ya configurada (SMTP o consola). El correo solo dice "Hay un borrador para revisar" + enlace; **sin datos de salud**. Un fallo de correo se registra y no interrumpe nada. Al aprobar, se avisa a la clienta ("Tu plan está listo") sin contenido.
- **Seguimiento**: vista de progreso por clienta con la serie de `BodyMeasurement` (peso, cintura, cadera, IMC) y adherencia de las últimas 4 semanas = días de rutina marcados (`RoutineDayLog`, ya existe) + comidas marcadas (`MealChoice.completed_at`). Se invita a registrar medidas cada 14 días.

### 8.3 Rutas de planes (app `plans`, fases B-D)

**Clienta** (login + membresía; solo lo suyo, solo `approved`)

| Método | Ruta | Nombre | Fase |
| --- | --- | --- | --- |
| GET | `/plans/` | `member_plans` | B |
| GET, POST | `/plans/request/` | `member_plan_request` | B (comprueba elegibilidad; el POST crea el trabajo) |
| GET | `/plans/requests/<pk>/` | `member_plan_request_status` | B |
| GET | `/plans/requests/<pk>/status/` | `member_plan_request_poll` (fragmento HTMX) | B |
| GET | `/plans/nutrition/` | `member_nutrition_plan` | C |
| POST | `/plans/nutrition/choices/` | `member_meal_choice` (`slot`, `option`, `date`) | C |
| POST | `/plans/nutrition/choices/<pk>/complete/` | `member_meal_complete` | D |
| POST | `/plans/nutrition/slots/<slot_pk>/alternatives/` | `member_meal_alternative` | C |
| GET | `/plans/nutrition/exchanges/` y `/plans/nutrition/shopping-list/` | `member_exchanges`, `member_shopping_list` | C |

El ejercicio aprobado se ve en la pantalla existente `member_routine`.

**Entrenador / superusuaria** (`clients_for_trainer`; no accesible = 404)

| Método | Ruta | Nombre | Fase |
| --- | --- | --- | --- |
| GET | `/plans/trainer/inbox/` | `trainer_plan_inbox` (borradores y en revisión de SUS clientes, con banderas y marca de "otra opción") | B (mínima), D |
| GET | `/plans/trainer/inbox/badge/` | `trainer_plan_badge` | D |
| GET | `/plans/trainer/<pk>/` | `trainer_plan_detail` | B |
| POST | `/plans/trainer/<pk>/approve/` | `trainer_plan_approve` | B |
| POST | `/plans/trainer/<pk>/regenerate/` | `trainer_plan_regenerate` | B |
| POST | `/plans/trainer/<pk>/archive/` | `trainer_plan_archive` | B |
| POST | `/plans/trainer/<pk>/clone/` | `trainer_plan_clone` | D |
| GET, POST | `/plans/trainer/<pk>/options/<opt_pk>/edit/` | `trainer_meal_option_edit` (cambia alimentos/gramos del catálogo; recalcula kcal y revalida) | C |
| GET | `/plans/trainer/clients/<client_pk>/` | `trainer_client_plans` (planes, versiones, trabajos) | D |
| POST | `/plans/trainer/clients/<client_pk>/generate/` | `trainer_plan_generate` | B |
| GET | `/plans/trainer/clients/<client_pk>/progress/` | `trainer_client_progress` | D |
| GET | `/plans/trainer/jobs/` | `trainer_plan_jobs` (fallidos y bloqueados) | D |

Reglas de pertenencia: `Plan`, `MealOption`, `MealSlot` y `PlanGenerationJob` se cargan con `.filter(client__in=clients_for_trainer(request.user))` (o `ensure_client_access` sobre `plan.client`); lado clienta, `.filter(client=request.user, status='approved')`. Una opción o comida que no pertenezca al plan aprobado de la clienta -> 404 (evita IDOR).

---

## 9. Privacidad y Ley 1581 de 2012 (datos sensibles de salud)

Referencias: Ley 1581 de 2012, art. 5-6 (datos sensibles: tratamiento solo con autorización explícita; la clienta no está obligada a responder sobre ellos), art. 7 (niños y adolescentes), art. 26 (transferencia internacional), Decreto 1377 de 2013 (autorización, transmisión a encargados). **No es asesoría legal: un abogado debe revisar.**

| Requisito | Cómo se cumple |
| --- | --- |
| Autorización previa, expresa e informada, **registrada** | `ConsentRecord` (fecha, usuario, versión; texto de la versión en base). Dos finalidades separadas. Casilla sin marcar. |
| Información en el aviso | Responsable (`[RAZÓN SOCIAL]`, `[NIT]`), finalidad, que responder es facultativo, derechos (conocer, actualizar, rectificar, suprimir, revocar), canal (`[CORREO DE DERECHOS]`), enlace a la política de tratamiento. **Dato que falta**: confirmar que la web ya publica la política de tratamiento y si corresponde inscribir la base en el RNBD. |
| Minimización | Ficha con listas cerradas; texto libre acotado y solo para el entrenador; a Gemini nunca van identificadores ni diagnósticos (4.4). |
| Datos fuera de Colombia | El aviso de IA dice que los datos sin identificar se procesan por Google en servidores que pueden estar fuera de Colombia. Google actúa como encargado (transmisión). Requiere facturación habilitada (4.2). |
| Seguridad | Acceso por asignación, `HealthAccessLog`, `no-store`, nada de salud en logs ni correos ni URLs, modelos fuera del admin. Cifrado de campos en reposo no incluido en la v1 (riesgo abierto 6). |
| Derechos de la titular | Exportar (JSON) y borrar desde su cuenta; revocar consentimiento; el entrenador no puede otorgar consentimiento por ella. |
| Retención | Propuesta: conservar ficha y planes mientras haya membresía y **hasta 24 meses** después de la última actividad (`HEALTH_RETENTION_MONTHS`); luego borrado con el comando `purge_health_data` (manual al inicio). Los trabajos de IA pierden su detalle (banderas y notas) a los 90 días y conservan solo conteos y tokens. |
| Borrado de cuenta | `trainer_client_delete` ya borra al usuario en cascada: ficha, planes, elecciones, trabajos y consentimientos se van con él. Pregunta abierta al abogado: si debe conservarse evidencia de la autorización después de borrar. |

Texto del consentimiento de IA (borrador): "Autorizo que mis datos de salud, **sin mi nombre ni datos de contacto**, sean enviados a Google (Gemini) para generar una propuesta de plan de ejercicio y alimentación que mi entrenador revisará. Entiendo que Google puede procesarlos en servidores fuera de Colombia, que puedo retirar esta autorización cuando quiera y que sin ella el entrenador puede armar mi plan manualmente."

---

## 10. Descargo de responsabilidad y normativa profesional

**Texto propuesto** (se muestra al solicitar el plan, en cada plan, en la impresión/PDF y en la página de nutrición):

> Este plan es una orientación general de ejercicio y alimentación, elaborada con apoyo de inteligencia artificial y revisada por tu entrenador. No reemplaza la valoración, el diagnóstico ni el tratamiento de un médico o de un nutricionista-dietista. Si sientes dolor en el pecho, mareo, falta de aire, desmayo, dolor intenso o cualquier síntoma que te preocupe, suspende el ejercicio y consulta a un profesional de la salud. Si tienes una condición médica, estás embarazada o en lactancia, o has tenido dificultades con la alimentación, consulta a tu médico antes de seguir este plan.

**Normativa (a validar con abogado)**: la Ley 1164 de 2007 (talento humano en salud) regula las profesiones de la salud, entre ellas nutrición y dietética, e impone registro en el RETHUS. Si una "dieta" individual es un acto propio del nutricionista-dietista, un entrenador sin ese título podría estar excediendo su alcance. **Mitigaciones incorporadas**:

1. Todo caso con enfermedad que exija dieta terapéutica queda en bandera roja (no se genera).
2. Lenguaje de "guía de alimentación saludable", no "dieta terapéutica" ni "prescripción".
3. La aprobación de planes de alimentación exige una persona con `can_validate_nutrition` (nutricionista-dietista con registro), y su credencial se muestra en el plan.
4. Descargo visible y consentimiento informado.

Si nadie del equipo tiene el título, la recomendación es contratar a un nutricionista-dietista como validador antes de activar la fase C (pregunta 2). El plan de ejercicio sí lo puede validar la entrenadora.

---

## 11. Pruebas (sin red)

- **Cliente simulado inyectable**: `FakeAIClient` con modos guionados (`ok`, `invalid_json`, `wrong_count`, `unknown_id`, `allergen_in_text`, `kcal_out_of_band`, `timeout`, `rate_limited`, `safety_block`). `get_ai_client()` se reemplaza con `override_settings`/`mock.patch`. Los hilos no se usan en pruebas (`AI_PLANS_RUN_INLINE`).
- **Autorización** (sobre `ScenarioTestCase` existente): para cada ruta de entrenador, superusuaria ve todo; entrenador A ve lo suyo; entrenador B -> 404; cliente sin asignar solo la superusuaria; clienta -> 403; anónimo -> login. Clienta A no accede a ficha, plan, opción, elección ni trabajo de B (404).
- **La clienta no ve borradores**: con planes en `draft_ai`, `in_review`, `archived`, `superseded` y rutina inactiva, `member_plans`, `member_nutrition_plan`, `member_routine`, `dashboard` y acceso directo por pk no los muestran; `mark_day_complete` sobre día de rutina inactiva -> 404; tras aprobar sí.
- **Banderas rojas**: prueba por tabla, una por cada código de 3.1-3.3 (incluye bordes: 17 y 18 años, IMC 17,49/17,5, autorización médica vencida a los 12 meses). Si hay bandera roja, `get_ai_client()` **no se llama** (el doble registra 0 llamadas).
- **Cálculo**: valores conocidos de Mifflin-St Jeor, piso calórico, tope de déficit, reparto de macros y de comidas.
- **Validación de salida**: un plato con maní/mariscos/lácteos/gluten (con tildes, mayúsculas y sinónimos) en texto libre se rechaza; un `food_id` fuera del permitido se rechaza; opción fuera de +/-5 % se corrige por escala o se rechaza; cuatro opciones o dos -> rechazo; tras un reintento fallido el trabajo queda `failed` y **no existe `Plan`**.
- **Privacidad**: el payload capturado por el doble no contiene nombre, correo, teléfono, usuario ni id; `assertLogs` confirma que los logs no contienen valores de la ficha ni el prompt; la clave de Gemini no aparece en excepciones.
- **Robustez**: trabajo `running` sin latido se recupera; doble envío con el mismo `request_token` no duplica; límites diario/semanal/por entrenador; recuperación tras reinicio; consentimiento revocado bloquea.
- **Aprobación**: transición de estados válidas e inválidas, un solo `approved` por (clienta, tipo), plan obsoleto exige confirmación, `is_simulated` no se aprueba fuera de `DEBUG`, nutrición exige `can_validate_nutrition`.

---

## 12. Variables de entorno nuevas (12-factor)

| Variable | Uso | Valor por defecto |
| --- | --- | --- |
| `AI_PLANS_MODE` | `off` / `simulated` / `live` | `off` en producción, `simulated` con `DEBUG` |
| `GEMINI_API_KEY` | clave (solo entorno; nunca en repositorio, chat ni logs) | vacío |
| `GEMINI_MODEL` | ID del modelo (sin valor fijo en el código; se verifica en la documentación vigente) | obligatorio en `live` |
| `GEMINI_BILLING_CONFIRMED` | `true` confirma proyecto con facturación | `false`; obligatorio en `live` |
| `GEMINI_TIMEOUT_SECONDS` | lectura por llamada | 60 |
| `AI_PLANS_MAX_ATTEMPTS`, `AI_PLANS_DAILY_JOB_LIMIT`, `AI_PLANS_CLIENT_WEEKLY_LIMIT` | topes | 3, 100, 3 |
| `AI_PLANS_RUN_INLINE` | ejecutar sin hilo (pruebas) | `false` |
| `HEALTH_FEATURES_ENABLED` | interruptor de la ficha y medidas de la clienta | `false` en producción hasta aprobar el texto legal |
| `HEALTH_RETENTION_MONTHS` | retención | 24 |

Se documentan en `CLAUDE.md` y en un `.env.example` sin valores reales. `production.py` falla al arrancar si `AI_PLANS_MODE=live` y falta algo.

---

## 13. Matriz de acceso

| Recurso | Clienta | Entrenador asignado | Otro entrenador | Superusuaria |
| --- | --- | --- | --- | --- |
| Medidas | propias | sus clientes | 404 | todas |
| Ficha de salud | propia | sus clientes (con registro de acceso) | 404 | todas (con registro) |
| Plan borrador / en revisión | no ve (404) | sí | 404 | sí |
| Plan aprobado | solo el suyo | sí | 404 | sí |
| Pedir generación | sí (con requisitos) | sí, para sus clientes | 404 | sí |
| Aprobar ejercicio | no | sí | 404 | sí |
| Aprobar alimentación | no | solo con `can_validate_nutrition` | 404 | solo con `can_validate_nutrition` |
| Consentimientos | otorga/revoca los suyos | solo ve | 404 | solo ve |
| Clientes sin asignar | n/a | 404 | 404 | sí |

---

## 14. Fases de entrega (cada una se despliega sola)

| | Contenido | Modelos / migraciones | Rutas | Formularios | Pruebas | Pantallas (ver `requests.md`) |
| --- | --- | --- | --- | --- | --- | --- |
| **A1** | Clienta registra sus medidas + consentimiento de datos de salud | `assessments` 0005; `health` 0001 (`ConsentTextVersion`, `ConsentRecord`) + datos del texto v1 | `member_measurement_*`, `trainer_measurement_edit/delete`, `member_consent*` | `BodyMeasurementForm` ampliado, `ConsentForm` | autorización, validaciones, consentimiento | F-01, F-02, F-03 |
| **A2** | Ficha de salud y alimentación, confirmación, exportar/borrar | `health` 0003 (`HealthProfile`, `HealthAccessLog`; la 0001 y la 0002 son de A1) | `member_health_*`, `trainer_health_profile*` | `HealthProfileForm` por secciones | versiones, menores, confirmación, acceso, `no-store` | F-04, F-05 |
| **B** | Motor de reglas + Gemini + plan de ejercicio + bandeja mínima del entrenador | `exercises` 0004 (etiquetas); `plans` 0001 (`Plan`, `PlanGenerationJob`, `PlanEvent`, `TrainerCredential`); arreglo `mark_day_complete`; `requests` | `member_plans`, `member_plan_request*`, `trainer_plan_inbox/detail/approve/regenerate/archive/generate` | `PlanRequestForm`, `ApproveForm`, `RegenerateForm` | reglas, banderas, validación, cliente simulado, asincronía, borradores no visibles | F-06 a F-10 |
| **C** | Plan de alimentación con 3 opciones, intercambios, "otra opción" | `plans` 0002 (`Food`, `NutritionPlan`, `MealSlot`, `MealOption`, `MealOptionItem`, `MealChoice`); `health` 0002 (M2M); comando `load_foods` | `member_nutrition_plan`, `member_meal_choice`, `member_meal_alternative`, `member_exchanges`, `member_shopping_list`, `trainer_meal_option_edit` | `MealChoiceForm`, `MealOptionEditForm` | alérgenos, bandas, 3 opciones, cupos de "otra opción", IDOR | F-11 a F-14 |
| **D** | Seguimiento y bandeja completa | sin modelos nuevos obligatorios | `trainer_plan_badge/clone`, `trainer_client_plans`, `trainer_client_progress`, `trainer_plan_jobs`, `member_meal_complete` | `CloneForm` | versiones, progreso, adherencia, avisos | F-15 a F-17 |

Ajuste respecto a tu propuesta: una bandeja **mínima** pasa de la fase D a la B, porque sin ella el borrador del ejercicio no tiene a dónde llegar.

---

## 15. Decisiones tomadas y por qué

1. **Catálogo `Food`** (la IA elige ids y Python calcula todo): las alergias se excluyen por construcción, las cifras no se inventan y los alimentos son los típicos colombianos accesibles. La lista de palabras clave se mantiene como segunda capa para texto libre.
2. **Banderas rojas sin anulación en la v1**: lo seguro primero; el entrenador siempre puede armar el plan a mano.
3. **A la IA no se le envían diagnósticos**: las restricciones se aplican filtrando los catálogos y los objetivos; menos datos sensibles salen y la IA no puede "olvidar" una restricción.
4. **Borrador de ejercicio como `Routine` inactiva**: se reutiliza el constructor y las pantallas; la filtración se evita con el filtro `is_active` que ya usan las vistas de la clienta.
5. **Cola en base de datos + hilo, sin Celery**: cero infraestructura nueva y sin trabajo perdido por reinicios.
6. **Límites en la base de datos, no en caché**: la caché local es por proceso (2 workers) y no sirve para controlar gasto.
7. **El plan fallido no se guarda** (solo códigos): evita almacenar salida no validada.
8. **Ficha versionada y confirmación de la clienta**: auditoría y que lo enviado a la IA sea lo que ella aceptó.
9. **Ficha y planes para clientas con membresía vigente**, igual que sus rutinas.
10. **Menores fuera de la ficha en línea.**
11. **Gemini solo en `live` con facturación confirmada**; `simulated` para desarrollo.
12. **`requests` en vez del SDK de Google**: una dependencia, control de timeouts y fácil de simular.

## 16. Riesgos abiertos

1. **Contenido clínico**: umbrales, pisos y banderas necesitan revisión de un profesional de salud/nutrición.
2. **Legal**: texto de consentimiento, política de tratamiento, RNBD, transferencia internacional, retención de la evidencia tras borrar, y alcance de la prescripción dietética (Ley 1164 de 2007).
3. **Catálogo `Food`**: alguien debe construirlo y validarlo (150-250 alimentos). La TCAC del ICBF (2018, 773 alimentos) es la fuente natural, pero **hay que verificar su licencia de uso** antes de copiarla.
4. **Etiquetado de ejercicios**: sin él, el filtrado de lesiones es conservador y puede dejar pocos ejercicios.
5. **Modelos y precios de Gemini cambian** (un modelo ya fue apagado; el precio de Flash 3.6-3.8 se duplica en 2027-01-01): mitigado con variable de entorno y un comando de comprobación; hay que revisar cada cierto tiempo.
6. **Cifrado en reposo** de la ficha no incluido; la base de Railway cifra el disco, pero un volcado de la base expone la ficha. Opción futura: cifrado por campo.
7. **Hilos en gunicorn**: suficientes para el volumen actual; si crece el uso hay que pasar a un worker dedicado.
8. **Calidad culinaria**: la IA puede proponer platos poco prácticos; el entrenador es el filtro y puede regenerar con notas.
9. **El correo** sale por Gmail SMTP si está configurado; puede tener límites de envío diarios.
10. **SQLite en desarrollo** no soporta bloqueos de fila: la reclamación atómica usa `UPDATE ... WHERE`, que funciona en ambos.

## 17. Preguntas para aprobar

Las respuestas por defecto son mi recomendación; si no contestas, asumo estas.

1. **Catálogo de alimentos propio (`Food`)** (recomendado) o IA libre con lista de palabras clave. El catálogo es más trabajo inicial pero mucho más seguro. ¿Quién lo valida?
2. **Validación de alimentación**: ¿alguien del equipo es nutricionista-dietista con registro (RETHUS)? Recomendado: exigirlo para aprobar planes de alimentación; si no, contratar un validador externo antes de la fase C.
3. **Banderas rojas sin anulación** (recomendado) o que la superusuaria pueda anularlas con soporte médico.
4. **Google Gemini de pago**: ¿puedes habilitar la facturación en AI Studio (prepago mínimo USD 5) antes de usar datos reales? Hasta entonces solo modo simulado. ¿Qué modelo probar primero? Recomiendo empezar con uno `flash-lite` estable y comparar calidad con un `flash` en 5-10 casos de prueba.
5. **Membresía vigente** requerida para ficha y planes (recomendado sí).
6. **Menores de edad**: sin ficha en línea y derivados al entrenador (recomendado).
7. **Textos legales**: ¿ya existe la política de tratamiento de datos en la web? ¿Cuáles son razón social, NIT y correo para ejercer derechos? ¿Puedes pasar el texto por un abogado antes de activar?
8. **Retención** de 24 meses tras la última actividad (propuesta).
9. **Ejecución**: hilo + base de datos (recomendado) o Redis + Celery (más infraestructura).
10. **Etiquetado de ejercicios**: ¿cuántos ejercicios activos hay y quién los etiqueta (equipo, zonas de riesgo, intensidad)? Es prerrequisito de la fase B.
11. **Opciones de reserva** (3 más por comida, aprobadas por el entrenador, para "otra opción" instantánea sin IA): ¿las incluimos? Recomiendo sí si el gasto de IA importa.
12. **Nueva dependencia `requests`** (versión fija): ¿autorizas instalarla al construir la fase B?
13. **Orden**: ¿empezamos por A1 (medidas + consentimiento) para resolver ya el problema de la clienta?

---

## 18. Registro de implementación

### Fase A1 (2026-10-08, rama `plan-ia-nutricion`)

Implementado: medidas de la clienta, edición/borrado por el entrenador y consentimiento (`health_data` y `ai_processing`). Diferencias respecto al texto de las secciones 2 y 14, todas menores:

- La migración de `assessments` es la **0006** (la 0005 ya existía). `health` 0001 = modelos; `health` 0002 = textos v1 (datos).
- `BodyMeasurement` ganó además `needs_review` (bool, aviso interno de "posible error de digitación" del peso; sección 2.1). Rangos: cadera 30-300 cm, grasa 3-70 %.
- Una autorización activa por (clienta, versión del texto): restricción parcial en `ConsentRecord`; no se añadió `purpose` al registro (se toma del texto).
- Tope de 3 mediciones por día: cuenta solo las registradas por la clienta (`source='member'`).
- `HEALTH_FEATURES_ENABLED` ya existe (apagada por defecto en producción; las rutas de salud de la clienta responden 404). `PRIVACY_POLICY_URL` (opcional) se agregó para el enlace a la política.
- Menores: sin fecha de nacimiento todavía, se usa la edad de la última valoración inicial (`apps.health.services.is_minor`); la ficha de A2 la reemplazará con `birth_date`.
- Textos legales: v1 con marcadores `[RAZÓN SOCIAL]`, `[NIT]`, `[CORREO DE DERECHOS]`. `apps.health.services.ai_texts_ready()` queda listo para la fase B: en producción (no DEBUG) es falso mientras algún texto vigente tenga marcadores `[...]`. En A1 no se bloquea el consentimiento de salud con marcadores; lo que lo protege es el interruptor `HEALTH_FEATURES_ENABLED`.
- Un entrenador (o la superusuaria) que abre una ruta de clienta recibe 403 (decorador `member_required`).
- El historial de consentimientos no está en el admin (solo `ConsentTextVersion`); su pantalla llega con A2 (F-05).

### Fase A2 (2026-10-08, rama `plan-ia-a2-ficha`)

Implementado: ficha de salud y alimentación (`HealthProfile`, versionada), confirmación de la clienta, corrección del entrenador, exportar, borrar, registro de accesos (`HealthAccessLog`) y el módulo puro de banderas. Pruebas sin red (formularios, servicios, vistas, banderas). Pendiente: las 5 plantillas (frontend); ver el bloque "Contrato A2 para el frontend" en `docs/contracts/requests.md`.

**Qué se construyó**

- `apps/health/choices.py` (listas cerradas con etiquetas en español, PAR-Q parafraseado **[A VALIDAR]**, pasos del asistente), `forms.py` (`HealthProfileForm`, un solo formulario para los 5 pasos, con `SECTION_FIELDS` y `error_steps`), `dates.py`, modelos `HealthProfile` y `HealthAccessLog`, migración `health` **0003**, y casos de uso en `services.py` (`save_profile`, `confirm_profile`, `delete_profile`, `export_data`, `log_access`, `profile_flags`, `consent_cards`, `is_minor` nuevo).
- Rutas: `member_health_profile` (GET detalle / formulario con `?edit=1`, POST guarda), `member_health_profile_confirm`, `member_health_export` (JSON adjunto), `member_health_delete`, `trainer_health_profile`, `trainer_health_profile_edit`. Todas con `Cache-Control: no-store`.
- `apps/plans/rules/flags.py`: paquete **Python puro** (sin Django ni red) que implementa R01-R15, Y01-Y13, I01, I02 y los datos faltantes M01-M09 (`Facts` -> `FlagReport`). `apps/plans/` **no está** en `INSTALLED_APPS` (la fase B le agrega su `AppConfig`). Los umbrales son constantes con la marca **[A VALIDAR]**. Las banderas nunca llegan a una pantalla de la clienta.

**Decisiones confirmadas por la persona (2026-10-08)**

1. `meal_slots` incluye siempre desayuno, almuerzo y cena; opcionalmente media mañana y/o merienda (3 a 5).
2. Guardar la ficha exige el consentimiento `health_data` vigente, no el de IA.
3. El entrenador ve el contenido de la ficha solo con `health_data` vigente de la clienta (si no, ve "sin autorización vigente" y no se escribe registro de lectura).
4. El entrenador solo corrige una ficha existente (no la crea) y no puede guardar una ficha de menor.
5. `birth_date` vive en la ficha y manda sobre la edad de la valoración en `is_minor`; sin ficha queda el respaldo por valoración (así una menor identificada por el entrenador nunca llega a crear ficha).
6. Exportar y borrar exigen membresía vigente (como dice la sección 2.4).
7. La meta sigue en `User.training_goal` (el formulario la pide y el servicio la actualiza).
8. `HealthAccessLog` registra también exportar y borrar de la propia clienta; el entrenador, `trainer_health_profile_edit` con interruptor (`health_feature_required`).
9. Retirar el consentimiento `health_data` ofrece borrar la ficha (`member_consent_revoke` -> `member_health_delete?from_revoke=1`).

**Diferencias respecto a las secciones 2 y 14 (todas menores)**

- La migración es la `health` **0003** (no la 0001).
- `HealthProfile.consent` es un FK al `ConsentRecord` vigente al guardar (`SET_NULL`); `created_by` también es `SET_NULL`. `HealthAccessLog.actor` es `SET_NULL` y `client` es `CASCADE` (el registro sobrevive a "borrar ficha" pero se va con la cuenta; pregunta legal abierta: conservar evidencia tras borrar la cuenta).
- Una sola ficha vigente por clienta (restricción parcial `health_profile_one_current`) y `unique(user, version)`; si dos guardados chocan, el segundo responde `ProfileConflict` y no se pierde nada.
- Obligatorios: fecha de nacimiento, sexo para el cálculo, meta, las 7 respuestas del PAR-Q, cirugía, embarazo/lactancia, antecedentes alimentarios, dieta, comidas, cocina, presupuesto, días, minutos, lugar, experiencia y nivel de actividad. Opcionales: peso meta, horas de sueño (3-12), comidas fuera por semana (vacío = 0), horarios, listas (vacío = ninguna), textos libres y equipo (obligatorio solo en casa; `none` es excluyente; en el gimnasio se vacía).
- Coherencias: embarazo/lactancia/posparto con sexo `M` -> error; autorización médica exige fecha (no futura) y sin autorización se limpia; sin condiciones se limpia "condiciones controladas"; la gravedad de la alergia es obligatoria solo si hay alergias; los horarios solo se guardan para las comidas elegidas; peso meta 30-250 y coherente con la estatura conocida (IMC 8-100); edad de 18 a 100 años.
- Textos libres: NFC, sin caracteres de control o invisibles, espacios colapsados, máx. 300 sobre el texto limpio, y **se rechazan `<` y `>`** (siempre se muestran escapados).
- Exportar entrega ficha (todas las versiones), medidas, consentimientos y registro de accesos (quién = "tú" / "entrenador", sin nombres). No incluye las valoraciones iniciales ni las notas del entrenador.
- Banderas: el contrato 3.2 se aplicó al pie de la letra (R14 exige condición controlada **y** autorización vigente; una autorización es vigente si tiene menos de 12 meses exactos y no es futura). Códigos extra de datos faltantes: `M04_NO_PROFILE` (sin ficha) y `M04_PROFILE_UNCONFIRMED` (ficha sin confirmar).
- `trainer_health_profile` no depende del interruptor (el entrenador siempre puede revisar); `trainer_health_profile_edit` sí (apagado = 404).

**Pendiente / riesgos**

- Contenido clínico (umbrales, banderas, PAR-Q parafraseado) **[A VALIDAR]** por un profesional de la salud.
- El texto legal v1 de `health_data` dice "medidas corporales y demás datos de salud que decida registrar": el abogado debe cubrir explícitamente la ficha (condiciones, medicamentos, alergias, embarazo, antecedentes alimentarios).
- Con membresía vencida la clienta no puede exportar ni borrar su ficha (así lo dice el contrato): falta otro canal (p. ej. el correo de derechos).
- Sin cifrado en reposo (riesgo 6). La función sigue apagada en producción (`HEALTH_FEATURES_ENABLED`).
