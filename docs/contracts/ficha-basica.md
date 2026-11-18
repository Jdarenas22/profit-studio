# Ficha básica: menos preguntas, lo médico solo si hace falta

Estado: **APROBADO el 2026-11-17** por la persona, con todas las recomendaciones de la sección 9: **1A** (una pregunta que despliega; el texto de 3.1 sigue marcado **[A VALIDAR por un profesional de la salud]** antes de producción), **2A**, **3A**, **4A**, **5A**, **6A** y **7A**. Backend implementado en la rama `feature/ficha-basica` (registro en la sección 10). Las pantallas (FB-01 a FB-04) las construye `frontend-architect`. Complementa `plan-ia.md` (secciones 2.2, 2.3, 3 y 9) y el bloque «Contrato A2 para el frontend» de `requests.md`. Propuesta escrita el 2026-11-16.

## 1. Objetivo y principio

Pedido de la persona: «que no se solicite tanta información al cliente; si hay algún tema de salud o alimenticio que tenga que señalarse, que se despliegue el menú; si no, que el formulario sea básico. No quiero obligarlo a dar datos médicos si no lo requiere.»

Principio: **la ficha pide lo mínimo para entrenar.** Lo médico y lo alimentario se abren con una pregunta Sí/No. Lo demás pasa a «Más detalles (opcional)», con valores por defecto **que quedan marcados como «no declarado»**, para que el plan no suponga más de lo que la clienta dijo.

Lo que **no** cambia:
- Consentimiento `health_data` versionado, obligatorio para guardar. El de IA sigue siendo aparte.
- Ficha en línea solo para mayores de 18 años.
- Cada guardado crea una versión nueva y queda una sola `is_current`.
- `HealthAccessLog`.
- La corrección del entrenador queda sin confirmar hasta que la clienta la confirme.
- Exportar y borrar la ficha.
- Las banderas solo las ve el entrenador.
- A la IA no se envían datos de salud nuevos.
- **Lo desplegable es solo experiencia de uso.** Las reglas las aplica el servidor (sección 4).

Minimización (Ley 1581, art. 4, principio de finalidad y libertad): se recogen menos datos sensibles. **No hace falta publicar una versión nueva del texto de consentimiento**, porque no se agrega ninguna finalidad ni ninguna categoría de datos. Conviene que el abogado lo confirme al revisar los textos.

## 2. Pantalla resultante (pasos del asistente)

| Paso | `key` | Título | Campos | Obligatorio |
| --- | --- | --- | --- | --- |
| 1 | `basics` | Datos básicos y meta | `birth_date`, `sex_for_calculation`, `training_goal` | sí |
| 2 | `health` | Salud | `health_screen` (Sí/No). **Si Sí:** `parq_q1`..`parq_q7`, `conditions`, `condition_controlled`, `medical_clearance`, `clearance_date`, `medications`, `injuries`, `recent_surgery`, `pregnancy_status`, `eating_disorder_history` y los 3 textos libres | la pregunta, sí; el detalle, solo con Sí |
| 3 | `food` | Alimentación | `food_screen` (Sí/No). **Si Sí:** `diet_type`, `allergies`, `allergy_severity`, `intolerances` | la pregunta, sí; el detalle, solo con Sí |
| 4 | `training` | Entrenamiento | `training_days_per_week`, `session_minutes`, `training_place`, `experience_level` | sí |
| 5 | `details` | Más detalles (opcional) | `target_weight_kg`, `meal_slots`, `meal_time_*`, `cooking_access`, `budget_level`, `eats_out_per_week`, `liked_foods_text`, `disliked_foods_text`, `equipment`, `activity_level`, `sleep_hours` | no |

- Se puede guardar desde el paso 4 sin abrir el 5.
- El paso 5 avisa: «Si quieres un plan de alimentación, cuéntanos al menos tu nivel de actividad diaria» (ver sección 6.3).
- `WIZARD_STEPS` y `SECTION_FIELDS` (`apps/health/forms.py`) cambian a estos 5 pasos. `error_steps` sigue funcionando igual.
- El cuestionario de aptitud deja de ser un paso propio y pasa a estar dentro de «Salud».

## 3. Las dos preguntas que despliegan

### 3.1 Salud: `health_screen` **[A VALIDAR por un profesional de la salud]**

Texto propuesto. Tiene que cubrir todo lo que preguntan las 7 del PAR-Q (`PARQ_QUESTIONS`) y además embarazo, cirugía y trastornos alimentarios:

> **¿Tienes algún problema de salud, lesión o tomas medicamentos?**
> Responde **Sí** si te aplica **cualquiera** de estas:
> - Un médico te ha dicho que tienes un problema del corazón o de la presión arterial, o tomas medicamentos para el corazón o la presión.
> - Sientes dolor en el pecho al hacer actividad física, o lo has sentido en reposo en el último mes.
> - Te mareas hasta perder el equilibrio o te has desmayado alguna vez.
> - Tienes una lesión, o un problema de huesos o articulaciones que empeora con la actividad física.
> - Tienes una enfermedad diagnosticada (por ejemplo diabetes, asma, tiroides, riñón, hígado o celiaquía) o tomas algún medicamento de forma habitual.
> - Estás en embarazo o lactancia, o tuviste un bebé hace menos de 6 meses.
> - Te operaron en el último año.
> - Has tenido un trastorno de la conducta alimentaria.
> - Conoces cualquier otra razón por la que no deberías hacer actividad física.
>
> Si tienes dudas, o prefieres no contestar alguna de estas, elige **Sí**: podrás marcar «Prefiero no decir».

Opciones: `yes` «Sí, hay algo que contar» · `no` «No, ninguna me aplica». Ninguna marcada al empezar.

Equivalencias con lo que ya existe:

| Línea del texto | Cubre |
| --- | --- |
| 1 | `q1`, `q6`, `heart_disease`, `arrhythmia`, `hypertension`, `antihypertensives`, `beta_blockers` |
| 2 | `q2`, `q3` |
| 3 | `q4` |
| 4 | `q5`, `injuries`, `osteoporosis`, `herniated_disc` |
| 5 | resto de `conditions` y `medications` |
| 6 | `pregnancy_status` |
| 7 | `recent_surgery` (`under_6m`, `6_12m`) |
| 8 | `eating_disorder_history` (`yes` y `prefer_not_say`) |
| 9 | `q7` |

### 3.2 Alimentación: `food_screen`

> **¿Tienes alergias, intolerancias o una dieta especial?**
> Por ejemplo: alergia al maní o a los mariscos, intolerancia a la lactosa, o una dieta vegetariana, vegana, halal o kosher.

Opciones: `yes` / `no`. Ninguna marcada al empezar. Nota en la interfaz: «Si eres celíaca, márcalo también en Salud».

## 4. Reglas del servidor (`HealthProfileForm`)

### 4.1 `health_screen`

- **Obligatorio**, `yes` o `no`. Si falta: «Cuéntanos si tienes algún problema de salud (Sí o No).» Va en el paso 2.
- **Con `no`, el servidor descarta todo lo médico que llegue en el POST**: no lo valida, no da errores y no lo guarda. Guarda estos valores neutros explícitos:

| Campo | Valor con `no` |
| --- | --- |
| `parq` | `{'q1': False, …, 'q7': False}` (las 7 claves) |
| `conditions`, `medications`, `injuries` | `[]` |
| `condition_controlled`, `medical_clearance` | `False` |
| `clearance_date` | `None` |
| `recent_surgery` | `'none'` |
| `pregnancy_status` | `'none'` |
| `eating_disorder_history` | `'no'` |
| `other_condition_text`, `medications_text`, `injuries_text` | `''` |

  Cómo se hace: en `__init__`, si el POST trae `health_screen == 'no'`, los campos médicos y `parq_q*` pasan a `required=False` y en `clean()` se sobrescriben con la tabla. Así un valor basura en un campo oculto no bloquea a la clienta, y lo que llegó nunca se guarda.

- **Con `yes`**, se aplican las reglas actuales sin cambios:
  - Las 7 `parq_q*` son obligatorias.
  - `recent_surgery` y `eating_disorder_history` son obligatorios.
  - `pregnancy_status` es obligatorio, salvo con sexo `M`: ahí el servidor fuerza `'none'` (hoy da error si llega otro valor; se mantiene el error).
  - La autorización médica exige fecha.
  - La gravedad de la alergia se exige si hay alergias.
- **«Sí» sin nada marcado** (pregunta abierta 2; recomendado: error). Si con `yes` todo queda neutro (7 «No», listas vacías, sin cirugía, sin embarazo, antecedentes `no` y sin textos), el error es: «Marcaste que sí, pero no indicaste qué. Márcalo abajo o cambia tu respuesta a No.» Va en `health_screen`.

### 4.2 `food_screen`

- **Obligatorio**, `yes` o `no`.
- **Con `no`:** `diet_type = 'omnivore'`, `allergies = []`, `allergy_severity = ''` e `intolerances = []`. Lo que llegue de esos campos se descarta sin validarlo.
- **Con `yes`:**
  - `diet_type` es obligatorio. El selector arranca vacío; la interfaz no preselecciona «Como de todo».
  - Gravedad obligatoria si hay alergias.
  - Si queda `omnivore` sin alergias ni intolerancias, el error es: «Marcaste que sí, pero no indicaste cuál. Márcalo o cambia tu respuesta a No.» (misma decisión que en 4.1).

### 4.3 Paso 5 «Más detalles» y `defaulted_fields`

Un campo vacío del paso 5 toma un valor por defecto. **Si el campo puede cambiar un plan, su nombre queda en `defaulted_fields`**: la lista de lo que la clienta no declaró. Si lo envía con valor, cuenta como declarado y sale de la lista.

| Campo | Por defecto | ¿En `defaulted_fields`? | Por qué |
| --- | --- | --- | --- |
| `target_weight_kg` | `None` | no | Ya era opcional. Solo produce una advertencia en el cálculo. |
| `meal_slots` | desayuno, almuerzo y cena | no | Es el mínimo obligatorio de hoy y el reparto 30/40/30 ya existe. Siempre llega por los campos ocultos. |
| `meal_times` | `{}` | no | Solo es una sugerencia de hora en el plan. |
| `cooking_access` | `basic` | sí | Valor neutro: el prompt solo cambia con `none`. No supone una cocina completa ni que no pueda cocinar. |
| `budget_level` | `medium` | sí | Valor neutro: el prompt solo cambia con `low`. |
| `eats_out_per_week` | `0` | sí | Ya era así (vacío = 0). Ningún plan lo usa hoy. |
| `liked_foods_text`, `disliked_foods_text` | `''` | no | Texto libre opcional. Nunca va a la IA. |
| `equipment` | en casa: `['none']`; en el gimnasio: `[]` | sí, solo en casa y vacío | Lo más prudente: en casa solo se ofrecen ejercicios sin equipo. Hoy un «en casa» sin equipo da error; con esto se acepta. Si el catálogo queda corto, aparece `D06_FEW_EXERCISES` (pregunta abierta 6). Marcar «Ninguno» cuenta como declarado. |
| `activity_level` | `sedentary` | sí | En el plan de ejercicio es lo prudente: se supone menos acondicionamiento. **En el de alimentación no se usa el valor por defecto: bloquea** (sección 6.3), porque cambia las calorías hasta en un 44 % (factor 1,2 frente a 1,725). |
| `sleep_hours` | `None` | no | No se inventa. Ya existe `M07_NO_SLEEP`, que solo informa. |

Al editar una ficha, `form_values_from_profile` devuelve **vacíos** los campos que están en `defaulted_fields`. Así la clienta ve sin responder lo que no respondió, y al guardar de nuevo no se convierte en «declarado» sin querer.

### 4.4 Defensa en el servicio (`_check_profile_data` / `save_profile`)

- `health_screen` y `food_screen` deben ser `yes` o `no` (`legacy` no se acepta en un guardado nuevo).
- Con `health_screen == 'no'`, el servicio vuelve a imponer la tabla de 4.1, aunque el formulario ya lo haya hecho.
- Con `food_screen == 'no'`, vuelve a imponer 4.2.
- `defaulted_fields` solo puede contener códigos de la lista de 4.3.
- `equipment` vacío con `training_place == 'home'` ya no es inválido: el servicio pone `['none']`.

## 5. Modelo y migración (`health` 0005)

Tres campos nuevos en `HealthProfile`. Agregarlos es seguro: solo hay que agregar columnas con valor por defecto; no se transforman datos ni se borra nada.

```python
SCREEN_CHOICES = [('yes', 'Sí'), ('no', 'No'), ('legacy', 'Ficha completa anterior')]   # choices.py
health_screen = models.CharField(max_length=6, choices=ch.SCREEN_CHOICES, default='legacy',
                                 verbose_name='Respuesta a la pregunta de salud')
food_screen = models.CharField(max_length=6, choices=ch.SCREEN_CHOICES, default='legacy',
                               verbose_name='Respuesta a la pregunta de alimentación')
defaulted_fields = models.JSONField(default=list, blank=True, verbose_name='Datos no declarados (valor por defecto)')
```

- **Fichas existentes:** quedan en `legacy`, que significa «respondió el cuestionario completo». Clínicamente valen lo mismo que `yes`. `defaulted_fields = []`, porque en esas fichas todo era obligatorio y está declarado. **No hace falta migración de datos.**
- **Restricción en la base (`CheckConstraint` `health_profile_screen_no_neutral`):** si `health_screen = 'no'`, entonces `recent_surgery = 'none'`, `pregnancy_status = 'none'`, `eating_disorder_history = 'no'`, `medical_clearance = false` y `condition_controlled = false`. Las listas JSON se controlan en el servicio, porque comparar JSON en un `CHECK` no se comporta igual en SQLite y en PostgreSQL. La restricción se crea sin problema: ninguna fila existente tiene `no`.
- **Reversa:** quitar los 3 campos y la restricción.
- `PROFILE_FIELDS` (services) incluye los 3 campos, así que viajan en `export_data` y en el historial de versiones.
- Propiedades nuevas de solo lectura:
  - `health_declared_none` (`health_screen == 'no'`).
  - `food_declared_none`.
  - `defaulted_labels`: etiquetas en español de `defaulted_fields`.

### Edición de fichas `legacy`

`form_values_from_profile` preselecciona la respuesta así:
- `health_screen`: `yes` si hay **algún** dato médico distinto del neutro; si todo es neutro, `no`.
- `food_screen`: `yes` si la dieta no es `omnivore` o hay alergias o intolerancias; si no, `no`.

Al guardar ya queda `yes` o `no`.

### Cambiar de «Sí» a «No» al editar

La versión nueva queda neutra (4.1). Las versiones anteriores se conservan, como hoy. La interfaz avisa antes de guardar (pregunta abierta 5).

## 6. Efectos en el motor de reglas y en los planes con IA

### 6.1 ¿Cambia la seguridad?

- **Con `no`**, las banderas se calculan igual que si hubiera respondido «No» a todo: `flags.evaluate` recibe los mismos `Facts` neutros. **Ninguna roja ni amarilla de salud se activa.**
  - **Riesgo real:** una pregunta compuesta se lee con menos cuidado que 7 preguntas por separado, y el PAR-Q+ oficial pide responder cada una. Por eso:
    - El texto enumera cada caso (3.1) y dice «si tienes dudas, elige Sí».
    - El entrenador ve que fue «No» (6.2).
    - **Todo plan con IA sigue exigiendo la revisión y aprobación del entrenador** (plan-ia.md, 8.2).
    - La valoración inicial presencial sigue existiendo.
  - Sigue siendo **[A VALIDAR]**: un profesional de la salud debe aprobar que esta pregunta sustituya al PAR-Q visible (pregunta abierta 1).
- **Con `yes`**, todo es igual que hoy.
- **Con `legacy`**, todo es igual que hoy.
- Las banderas que no dependen de lo médico se calculan igual: edad (R01, R15, Y08), IMC (R07, R08, Y09), meta de rehabilitación (Y11) y datos faltantes.
- **La IA no recibe nada nuevo.** `health_screen`, `food_screen` y `defaulted_fields` nunca entran al payload. `conservative` se sigue calculando solo con las amarillas.

### 6.2 Banderas nuevas (`flags.py`, `RULESET_VERSION = 'flags/2'`)

`Facts` gana `health_screen: str = 'legacy'`, `food_screen: str = 'legacy'` y `defaulted_fields: tuple = ()`. `build_facts` los llena desde la ficha.

| Código | Tipo | Alcance | Cuándo | Texto para el entrenador / acción |
| --- | --- | --- | --- | --- |
| `I03_HEALTH_SCREEN_NO` | informativa | — | `health_screen == 'no'` | «Respondió No a la pregunta general de salud: no vio el cuestionario detallado.» / «Si en la valoración notas algo, corrige la ficha (la clienta deberá confirmarlo).» |
| `I04_DEFAULTS` | informativa | — | `defaulted_fields` no vacío | «Datos que no declaró (se usó un valor por defecto): {etiquetas}.» / «Opcional: pregúntaselos.» |
| `M10_NO_ACTIVITY` | dato faltante | `nutrition` | `'activity_level' in defaulted_fields` | «No declaró su nivel de actividad diaria; sin él no se calculan las calorías.» / «La clienta debe completarlo en "Más detalles".» |

Las informativas no bloquean nada. Se muestran en el grupo «Informativas» de `trainer/health_profile.html`, que ya existe.

### 6.3 Un dato faltante bloquea solo el plan de alimentación

Requisito previo nuevo en `eligibility.py`:
- Código: `D07_NUTRITION_DETAILS`.
- Correspondencia: `M10_NO_ACTIVITY` → `D07_NUTRITION_DETAILS`.
- Va en `_ORDER` después de `D04_INCOHERENT`.
- Texto para la clienta: «Para armar tu plan de alimentación necesitamos tu nivel de actividad diaria. Complétalo en "Más detalles" de tu ficha.»

Efectos:
- El plan de **ejercicio no se bloquea**: usa `sedentary`, que es lo prudente.
- **La ficha nunca se bloquea.**
- Como el alcance de M10 es solo `nutrition`, la regla actual (`kind in flag.scopes`) ya lo limita a ese plan.
- Pasa igual cuando el pedido lo hace el entrenador: verá D07 y podrá corregir la ficha, y la clienta tendrá que confirmarla.

Los demás datos por defecto **no bloquean**:
- `cooking_access` y `budget_level` solo matizan las preferencias del prompt.
- `meal_slots` siempre tiene el mínimo válido.
- `equipment` (`none`) solo reduce el catálogo; si queda corto, aplica `D06_FEW_EXERCISES`, que ya existe.

### 6.4 Lo que no se toca

- `rules/energy.py`, `rules/foods.py`, `rules/catalog.py` y los prompts no cambian. Con `food_screen == 'no'`, `diet = 'omnivore'` y sin alergias, que es exactamente lo que el filtro ya maneja.
- Planes ya generados: la regla de «plan obsoleto» (por `profile_version`) sigue igual. Una versión nueva de la ficha marca como obsoletos los borradores anteriores, como hoy.
- `apps/plans/views.py` (resumen para el entrenador): `activity` muestra «No indicado» si está en `defaulted_fields`.

## 7. Pantallas (pedido a `frontend-architect`)

Detalle en `docs/contracts/requests.md`, bloque «Ficha básica (FB-01 a FB-04)». En resumen:
- **FB-01:** asistente de 5 pasos (sección 2), en `health/partials/profile_wizard.html`, que sirve a la clienta y al entrenador.
- **FB-02:** resumen de la clienta (`profile_summary.html` / `profile_detail.html`). Con `no` muestra «No indicaste problemas de salud» en lugar de la lista médica; los campos por defecto aparecen como «No indicado».
- **FB-03:** ficha del entrenador (`trainer/health_profile.html`). Muestra la respuesta a cada pregunta y los «no declarados»; las banderas nuevas salen en sus grupos.
- **FB-04:** pantalla de pedido de plan. El texto de D07 llega por `missing_texts`, que ya existe: solo hay que comprobar que se ve el enlace a la ficha.

Contexto nuevo en `_form_context`:
- `form.health_screen` y `form.food_screen` (`'yes'` | `'no'` | `''`).
- `choices.screen` (`[('yes','Sí…'),('no','No…')]`).
- `form.defaulted_fields` (lista).
- `screen_was_yes` (bool): la versión vigente tenía datos médicos. Sirve para el aviso de la pregunta abierta 5 («al responder No se quitarán de tu ficha los datos de salud que habías registrado»).

## 8. Pruebas previstas (sin red)

**Formulario** (`test_profile_forms.py`; `valid_post` pasa a ser la ficha básica mínima):
- Mínimo: con solo los campos básicos, `health_screen=no` y `food_screen=no`, la ficha es válida y queda con los valores neutros y por defecto exactos de 4.1, 4.2 y 4.3.
- Con `no`, campos médicos basura (códigos inventados, `parq_q1=yes`, `<script>` en los textos) se descartan sin error y no aparecen en `cleaned_data`. **Contraprueba:** con `yes`, esos mismos datos dan error.
- Con `yes`: faltan las `parq_q*` → error en el paso 2. «Sí» sin nada marcado → error en `health_screen`. Con un solo dato marcado → válido.
- Igual con `food_screen`, más `diet_type` obligatorio con `yes`.
- Falta `health_screen` o `food_screen`, o llega con un valor desconocido → error.
- `defaulted_fields`: un campo vacío entra en la lista y uno enviado sale. «Ninguno» en casa cuenta como declarado. En el gimnasio, el equipo nunca queda en la lista.
- `form_values_from_profile`: los campos de `defaulted_fields` vuelven vacíos y la preselección de `legacy` sigue la sección 5 («Edición de fichas `legacy`»). `error_steps` sigue los 5 pasos nuevos (todo campo pertenece a un paso).

**Servicio y modelo** (`test_profile_services.py`):
- `save_profile` vuelve a imponer los neutros con `no`, aunque el diccionario llegue «sucio».
- La restricción de la base rechaza `no` con `recent_surgery='under_6m'`.
- Una ficha existente queda en `legacy` después de migrar.
- El guardado del entrenador con `no` queda sin confirmar.
- `export_data` incluye los 3 campos.

**Banderas y elegibilidad** (`test_flags.py`, `test_rules.py`):
- `no` produce el mismo informe que una ficha `legacy` todo en «No», más `I03`.
- `M10` → `D07` solo para `nutrition`; el ejercicio sigue disponible.
- **Contraprueba:** con `activity_level` declarado no hay D07.
- `I04` lista las etiquetas.

**Planes** (`test_jobs.py`, `test_nutrition.py`):
- El payload de ejercicio con la actividad por defecto lleva `sedentary` y ningún campo nuevo.
- Un pedido de alimentación con D07 no crea trabajo.
- Los payloads no contienen `health_screen` ni `defaulted_fields`.

**Vistas** (`test_profile_views.py`):
- Se guarda la ficha mínima de la clienta.
- El entrenador corrige una ficha `legacy`.
- Siguen igual: `no-store`, menor de edad → 403 y sin consentimiento → redirección.

Al terminar: toda la suite (`manage.py test --settings=config.settings.development`) y `makemigrations --check`.

## 9. Preguntas abiertas (con recomendación)

1. **¿Una sola pregunta en lugar de las 7 del PAR-Q visibles?**
   - A) Como se decidió: una pregunta que despliega.
   - B) Dejar siempre visibles las 7 preguntas Sí/No (7 toques) y desplegar solo el detalle médico.

   **Recomendación: A**, con la marca [A VALIDAR] y la validación de un profesional de la salud antes de producción. Así se cumple el pedido y los planes siempre pasan por la entrenadora. B es más fiel al PAR-Q oficial; es la alternativa si el profesional lo exige.
2. **«Sí» sin marcar nada.**
   - A) Error que pide marcar algo o cambiar a No.
   - B) Aceptar y avisar a la entrenadora con una bandera.

   **Recomendación: A** (evita fichas ambiguas).
3. **Nivel de actividad.**
   - A) Opcional, y bloquea solo el plan de alimentación (D07).
   - B) Pasarlo al formulario básico (una pregunta más).
   - C) Usar un valor por defecto también para alimentación.

   **Recomendación: A.** C no se recomienda: cambia las calorías hasta en un 44 %.
4. **¿Qué ve la entrenadora cuando la respuesta es «No»?**
   - A) Bandera informativa `I03`.
   - B) Bandera amarilla: revisión reforzada en todos esos planes.

   **Recomendación: A.** Ella ya revisa y aprueba cada plan; una amarilla para casi todas las clientas haría que dejara de prestarle atención.
5. **Cambiar de «Sí» a «No» al editar.**
   - A) La versión nueva queda neutra, con un aviso previo en pantalla.
   - B) Conservar los datos médicos anteriores.

   **Recomendación: A** (respeta lo que declara ahora y minimiza datos; las versiones viejas se conservan como hoy).
6. **Equipo en casa sin responder.**
   - A) Por defecto «Ninguno»: solo ejercicios sin equipo.
   - B) Volverlo obligatorio cuando entrena en casa.

   **Recomendación: A.** Es lo más prudente y, si faltan ejercicios, el sistema ya avisa (D06).
7. **¿La entrenadora puede responder «No» por la clienta al corregir?**
   - A) Sí, usa el mismo formulario y la clienta debe confirmarlo.
   - B) No.

   **Recomendación: A** (es el flujo que ya existe).

## 10. Registro de implementación (2026-11-17, rama `feature/ficha-basica`)

Backend completo según las secciones 4 a 6 y 8, con las decisiones 1A a 7A. Las pantallas FB-01 a FB-04 quedan para `frontend-architect`.

### Qué se construyó

- `apps/health/screening.py` (módulo nuevo, sin Django): una sola fuente de las reglas compartidas por el formulario, el servicio y el modelo. Valores neutros (`health_neutral_values`, `food_neutral_values`), `health_is_neutral` / `food_is_neutral`, preselección de fichas `legacy` (`legacy_screen_guess`), campos con valor por defecto (`DEFAULTABLE_FIELDS`, `DEFAULT_VALUES`, `DEFAULTABLE_LABELS`) y los mensajes de error de las dos preguntas.
- `choices.py`: `SCREEN_CHOICES` (`yes`/`no`/`legacy`), `SCREEN_FORM_CHOICES` (lo que se pinta: «Sí, hay algo que contar» / «No, ninguna me aplica») y `WIZARD_STEPS` con los 5 pasos (`basics`, `health`, `food`, `training`, `details`); cada paso trae además `optional` (solo el 5 es `True`).
- `forms.py`: `health_screen` y `food_screen` obligatorios. Con `no`, los campos médicos (o alimentarios) **se quitan del formulario** al construirlo: no se validan, no dan error y `clean()` pone los neutros. Con `yes`, reglas completas y error «Marcaste que sí, pero no indicaste qué…» en la pregunta. Con una respuesta vacía o desconocida, el único error es el de la pregunta (el detalle no se exige, para no llenar la pantalla de errores). Paso 5 opcional con `defaulted_fields`.
- Modelo y migración **`health` 0005_ficha_basica** (reversible): `health_screen`, `food_screen` (por defecto `legacy`) y `defaulted_fields` (por defecto `[]`). Propiedades: `health_declared_none`, `food_declared_none`, `has_health_details`, `has_food_details`, `defaulted_labels` y el método `is_defaulted(nombre)` (para Python; en plantillas usar `'activity_level' in profile.defaulted_fields`).
- `services.py`: `PROFILE_FIELDS` incluye los 3 campos (viajan en `export_data` y en el historial). `_check_profile_data` devuelve una copia normalizada (4.4). `build_facts` llena `health_screen`, `food_screen`, `defaulted_fields` y `defaulted_labels`.
- Motor de reglas (`flags/2`): `I03_HEALTH_SCREEN_NO`, `I04_DEFAULTS` (texto con las etiquetas), `M10_NO_ACTIVITY` (alcance `nutrition`) y requisito `D07_NUTRITION_DETAILS` después de `D04_INCOHERENT`.
- `apps/plans/views.py`: en el resumen de la ficha del entrenador, `activity` = «No indicado» si no la declaró; D07 enlaza a `member_health_profile?edit=1`.
- La IA no recibe nada nuevo: los payloads no cambian (probado).

### Diferencias con el texto de arriba (todas menores)

- **Restricción extra en la base:** además de `health_profile_screen_no_neutral` (que también exige `clearance_date` nula), se agregó `health_profile_food_no_neutral`: con `food_screen = 'no'`, `diet_type = 'omnivore'` y `allergy_severity = ''`. Las listas JSON siguen controladas en el servicio.
- **Al editar una ficha con «No»**, `form_values_from_profile` devuelve vacías las preguntas que la clienta nunca vio (`parq_q*`, `recent_surgery`, `pregnancy_status`, `eating_disorder_history` y, con «No» en alimentación, `diet_type`). Así, si cambia a «Sí», no aparecen respuestas que no dio. En una ficha `legacy` se conservan (sí las respondió).
- **Comidas:** si no llega ninguna comida, se usan desayuno, almuerzo y cena (no cuenta como «no declarado»). Si llega una lista incompleta, sigue siendo error.
- **Sexo masculino con «Sí»:** `pregnancy_status` deja de ser obligatorio y se guarda `none`; otro valor sigue dando error.
- **`form.defaulted_fields` en el contexto:** al editar, la lista guardada; después de un POST con error, lo que llegó vacío (lo que se guardaría como «no declarado»); en el primer formulario, `[]`.
- **Contexto extra:** `food_screen_was_yes` (igual que `screen_was_yes`, para avisar que se quitarán la dieta y las alergias).
- **Pruebas:** `valid_post` sigue siendo la ficha completa (con todos los detalles declarados) y deduce las dos respuestas de lo que se le pasa; la ficha básica mínima es `basic_post` (y `make_profile(..., basic=True)`).
- **Ajuste temporal en `health/partials/profile_wizard.html`:** dos radios (`health_screen` en el paso 2 y `food_screen` en la sección de alimentación), marcados «REEMPLAZAR POR frontend-architect (FB-01)», para que el asistente actual pueda guardar mientras llega FB-01. No reordena los pasos: hasta FB-01 los títulos de `steps` no coinciden con el contenido de los pasos 3 a 5.

### Contexto exacto para las plantillas

Ver «Ficha básica: FB-01 a FB-04» en `docs/contracts/requests.md` (contexto completo por pantalla).

### Pendiente

- **[A VALIDAR por un profesional de la salud]:** el texto de 3.1 y que la pregunta general sustituya al cuestionario de aptitud visible (1A).
- Que el abogado confirme que no hace falta una versión nueva del texto de consentimiento (sección 1).
- FB-01 a FB-04 (frontend).
