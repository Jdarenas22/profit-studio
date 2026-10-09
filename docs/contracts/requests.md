# Pedidos entre backend y frontend

Archivo compartido: cada agente agrega lo suyo o marca como resuelto lo suyo; no borra lo del otro.
Contrato de referencia: `docs/contracts/plan-ia.md` (propuesta pendiente de aprobación; nada se construye hasta que la persona la apruebe).

## De arquitecto-backend para frontend-architect

Estado: contrato aprobado. **Fases A1 y A2 implementadas en el backend** (ver "Contrato A1 para el frontend" y "Contrato A2 para el frontend" más abajo; si algo difiere de las filas F-01 a F-05 de las tablas, mandan esos bloques); las demás fases siguen **PENDIENTES** hasta que se construyan.

### Convenciones (igual que las vistas actuales)

- Plantillas de la clienta extienden `base.html`; las del entrenador, `trainer/base.html`. Estilo: tema oscuro, acento `#FF6B00`, Tailwind CDN, Alpine.js y HTMX (ya presentes). Sin build.
- Cada vista con formulario pasa al contexto `errors` (diccionario `{campo: "mensaje"}`) y `form` (el `request.POST` para repoblar), como en `body_measurement_add.html`. Todo error de campo debe pintarse junto al input (`errors.<campo>`).
- Los textos de la interfaz van en español de Colombia. Nada de `innerHTML`/`|safe` con datos de la clienta; texto libre siempre escapado.
- Las páginas con datos de salud no deben cargar scripts o imágenes de terceros nuevos (solo los ya usados) ni poner datos de salud en la URL.
- Cada pantalla con formulario necesita `{% csrf_token %}`. Los botones que envían (guardar, generar, aprobar, otra opción) se deshabilitan mientras envían.
- Descargo de responsabilidad (texto exacto en `plan-ia.md`, sección 10): visible en F-06, F-11, en la vista del plan de ejercicio aprobado, y en la versión para imprimir.

### Fase A1: medidas y consentimiento

| Id | Pantalla | Plantilla sugerida | Contexto que entrega el backend |
| --- | --- | --- | --- |
| F-01 | Mis medidas (historial + botón "Registrar medidas") | `assessments/member_measurements.html` | `measurements` (propias, más reciente primero, con `date`, `weight`, `height`, `waist_cm`, `hip_cm`, `body_fat_pct`, `imc`, `imc_classification`, `source`, `can_delete`), `series` (listas para gráfico: fechas, peso, cintura), `needs_consent` (bool) |
| F-02 | Registrar medidas (formulario) | `assessments/member_measurement_add.html` | campos `weight`, `height`, `waist_cm`, `hip_cm`, `body_fat_pct`, `notes`; `errors`, `form`; `height_required` (bool: pedir estatura solo la primera vez); `last_height` (para precargar). Acepta coma decimal; estatura en m o cm. Mostrar ayuda de cómo medirse (cintura a la altura del ombligo, etc.) |
| F-03 | Consentimiento de datos de salud (y de IA, mismo diseño) | `health/consent.html` | `purpose`, `text_version` (número), `body` (texto completo), `already_granted` (bool), `granted_at`. Casilla **sin marcar**, requerida; botón "Acepto" deshabilitado hasta marcarla; enlace a la política de tratamiento de datos. Botón de revocar en `health/consent_manage` dentro de F-05. Para la IA: casilla aparte y explicar que es opcional para guardar la ficha |

También: en el panel del entrenador (`trainer/body_measurement_add.html` y `trainer/client_detail.html`) mostrar columnas nuevas (cadera, % grasa, origen "clienta"/"entrenador") y botones Editar/Borrar por medición (`trainer_measurement_edit`, `trainer_measurement_delete`, esta última con confirmación POST).

Navegación: enlace "Mis medidas" en el menú de la clienta (`partials/navbar` y dashboard de miembro).

### Fase A2: ficha de salud

| Id | Pantalla | Plantilla sugerida | Notas |
| --- | --- | --- | --- |
| F-04 | Ficha de salud y alimentación (asistente por pasos) | `health/profile_form.html` | Una sola página con pasos en Alpine (el servidor valida todo al guardar): 1 Datos básicos y meta; 2 Salud (condiciones, medicamentos, lesiones, cirugías, embarazo/lactancia, antecedentes alimentarios); 3 Cuestionario de aptitud (7 preguntas Sí/No, texto cercano y sin alarmar); 4 Alimentación (dieta, alergias, intolerancias, comidas por día y horarios, cocina, presupuesto); 5 Entrenamiento (días, minutos, lugar, equipo, nivel, actividad, sueño). Listas cerradas = casillas/selectores con los códigos exactos de `plan-ia.md` sección 2.2. Aviso arriba: "Responder sobre salud es voluntario; sin esta información tu entrenador armará el plan manualmente". Si la clienta es menor de 18: mostrar solo "Consulta con tu entrenador" |
| F-05 | Mi ficha: resumen, confirmar, exportar, borrar, retirar consentimientos | `health/profile_detail.html` | Muestra `profile` (versión vigente), `needs_confirmation` (la corrigió el entrenador: botón "Confirmar mis datos" que hace POST a `member_health_profile_confirm`), enlaces a exportar (descarga) y a borrar (página de confirmación con texto claro de lo que se pierde). Tarjeta "Consentimientos" con fecha/versión y botón revocar |

Panel del entrenador: `trainer/health_profile.html` (ficha completa de solo lectura + botón "Corregir", con las banderas calculadas: rojas, amarillas y datos faltantes, cada una con su texto y color) y `trainer/health_profile_form.html` (mismo formulario que F-04 con aviso "la clienta deberá confirmar tus cambios"). Enlace desde `trainer/client_detail.html`.

### Fase B: plan de ejercicio

| Id | Pantalla | Plantilla sugerida | Contexto |
| --- | --- | --- | --- |
| F-06 | Pedir mi plan | `plans/request.html` | `eligibility`: `can_request` (bool), `missing` (lista de requisitos: consentimientos, ficha, confirmar ficha, medidas recientes, límite de uso, con enlace a cada pantalla), `blocked` (bool, sin diagnósticos), `kinds` disponibles (`exercise`, `nutrition` desde fase C), campo oculto `request_token` (UUID). Si `blocked`: mostrar el mensaje de derivación del contrato (sección 3.3) y que el entrenador fue avisado |
| F-07 | Estado del trabajo | `plans/request_status.html` + fragmento `plans/partials/job_status.html` | `job.status` (`queued`, `running`, `succeeded`, `failed`, `blocked`), `job.progress_done/total`. Sondeo con HTMX cada 3 s sobre `member_plan_request_poll`; el backend responde **HTTP 286** cuando termina para detener el sondeo. Mensajes: en proceso, "listo: tu entrenador lo revisará", error genérico. Nunca mostrar contenido del plan aquí |
| F-08 | Mi plan (portada de planes) | `plans/member_plans.html` | `exercise_plan` (aprobado o `None`), `nutrition_plan`, `pending` (solo estado: "en revisión por tu entrenador", sin contenido), `valid_until`. El ejercicio aprobado se sigue viendo en `routines/member_routine.html`: añadir el descargo y el nombre/credencial del validador |
| F-09 | Bandeja del entrenador (versión mínima) | `trainer/plan_inbox.html` | `plans` (borradores y en revisión de SUS clientes): cliente, tipo, estado, fecha, insignias de bandera amarilla (texto de cada una), marca "opción regenerada por la clienta", `is_simulated` (franja "MODO DE PRUEBA"). Filtros por estado |
| F-10 | Revisión y aprobación | `trainer/plan_detail.html` | `plan`, `flags` (amarillas con explicación), `stale` (ficha cambió después de generar), `profile_summary` (resumen de ficha para decidir), `events` (historial). Ejercicio: resumen de la rutina + botón "Editar en el constructor" (`trainer_routine_builder`). Formularios: Aprobar (`note_for_client`, casilla obligatoria "Revisé las advertencias" si hay amarillas, casilla extra si `stale`), Regenerar (`notes`, máx. 500 caracteres), Archivar (motivo). Insignia de nutrición solo habilitada si `can_validate_nutrition` |

Menú del entrenador: entrada "Planes por revisar" con contador (en D se carga como fragmento aparte).
Mensajes de éxito/error con `messages` como en el resto del panel.

### Fase C: plan de alimentación

| Id | Pantalla | Plantilla sugerida | Contexto |
| --- | --- | --- | --- |
| F-11 | Mi alimentación | `plans/member_nutrition.html` | `plan` (objetivos del día: kcal, proteína, carbohidratos, grasa), `slots` (cada una con exactamente **3** `options` activas: nombre, preparación, `items` con `food.name`, `household` (medida casera ya formateada), `grams`, `kcal`, macros), `today_choices`, `date` seleccionable (7 días). Seleccionar una opción por comida (POST a `member_meal_choice`), mostrar total estimado del día vs. objetivo (el backend lo entrega en `day_totals`). Descargo visible. Modo impresión |
| F-12 | "Otra opción" por comida | dentro de F-11 | Botón por comida (POST a `member_meal_alternative`); mostrar cupo restante (`alternatives_left_week`), estado de espera (reutiliza el sondeo de F-07 sobre ese trabajo) y la insignia "nueva propuesta, pendiente de revisión del entrenador" |
| F-13 | Intercambios y lista de compras | `plans/member_exchanges.html`, `plans/member_shopping_list.html` | `groups` con equivalencias por porción; `shopping_items` (alimento, gramos totales, medida casera) para los próximos 7 días |
| F-14 | Edición de una opción (entrenador) | `trainer/meal_option_form.html` | Lista de `items` con selector de alimento (solo del catálogo `Food`), gramos; el backend devuelve kcal y macros recalculados y avisos; botones guardar / volver al plan |

### Fase D: seguimiento

| Id | Pantalla | Plantilla sugerida | Contexto |
| --- | --- | --- | --- |
| F-15 | Planes de una clienta (entrenador) | `trainer/client_plans.html` | `plans` por tipo con versiones y estados, `jobs` recientes (incluye fallidos y bloqueados con su código y texto amigable), botón "Generar borrador" (`trainer_plan_generate`) con aviso si faltan consentimientos |
| F-16 | Progreso de la clienta (entrenador) | `trainer/client_progress.html` | `series` (fecha, peso, cintura, cadera, IMC), `adherence` (días de rutina y comidas marcadas en 4 semanas), `baseline` (medición del plan). Gráficos con lo que ya cargue el proyecto; si hace falta una librería nueva, consultarlo antes |
| F-17 | Trabajos con problemas / contador | `trainer/plan_jobs.html`, fragmento `trainer/partials/plan_badge.html` | Lista de trabajos `failed`/`blocked` de SUS clientes con el código de motivo; contador numérico para el menú (HTMX `hx-get` + `hx-trigger="load, every 60s"`). En la ficha de adherencia de la clienta (F-11): casilla "Cumplí esta comida" (POST `member_meal_complete`) |

### Piezas transversales

- Pantalla de error amigable cuando el servicio de IA está apagado (`AI_PLANS_MODE=off`): "Esta función aún no está disponible".
- Franja visible "MODO DE PRUEBA" cuando `plan.is_simulated` es verdadero.
- Estados vacíos (sin medidas, sin ficha, sin plan) con una llamada a la acción clara.
- Accesibilidad: formularios con etiquetas, errores enlazados a su campo, contraste suficiente en el tema oscuro, gráficos con alternativa en tabla.
- Pruebas visuales: las plantillas se prueban con los datos de ejemplo del `ScenarioTestCase`; el backend entregará un comando para sembrar una clienta de prueba cuando se construya cada fase.

### Contrato A1 para el frontend (IMPLEMENTADO por el backend, 2026-10-08)

Este bloque es la referencia final de la fase A1; si algo difiere de las filas F-01 a F-03 de arriba, manda este. Las plantillas F-01, F-02 y F-03 ya entregadas **coinciden** con él (revisadas contra las vistas).

**Nombres de URL** (todos existen): `member_measurement_list` (`/assessments/me/measurements/`), `member_measurement_add` (`.../add/`), `member_measurement_delete` (pk, POST), `member_consent` (`/health/consent/<purpose>/`, `purpose` = `health_data` | `ai_processing`), `member_consent_revoke` (POST), `trainer_measurement_edit` (pk), `trainer_measurement_delete` (pk, POST), `trainer_measurement_add` (client_pk, ya existía).

**Acceso:** las rutas de la clienta exigen sesión + rol cliente + membresía vigente (el entrenador recibe 403; sin membresía ven `accounts/no_membership.html` / `membership_expired.html`) y `HEALTH_FEATURES_ENABLED`. Si esa función está apagada (es el valor por defecto en producción) **todas esas rutas responden 404**. **Pedido:** el enlace "Mis medidas" de la navbar y el resumen del panel de la clienta deben mostrarse solo `{% if HEALTH_FEATURES_ENABLED %}` (variable global de plantilla, ya inyectada por `apps.health.context_processors.health_flags`); si no, en producción el enlace llevaría a un 404.

**Contexto por plantilla**

- `assessments/member_measurements.html`: `measurements` (más reciente primero, máx. 100; cada una con `pk`, `date`, `weight`, `height`, `waist_cm`, `hip_cm`, `body_fat_pct`, `imc`, `imc_classification`, `source`, `can_delete`), `series` (dict `dates` ISO, `weight`, `waist_cm`, `hip_cm`; listas del mismo largo, más antigua primero, hasta 60 puntos; use `{{ series|json_script:"series" }}` si algún día hay gráfico), `needs_consent` (bool: falta consentimiento vigente o se publicó un texto nuevo), `is_minor` (bool: no puede registrar; mostrar "consulta con tu entrenador" en vez del botón), `daily_limit_reached` (bool: ya registró 3 hoy; deshabilitar "Registrar medidas").
- `assessments/member_measurement_add.html`: `errors` (claves `weight`, `height`, `waist_cm`, `hip_cm`, `body_fat_pct`, `notes`, y `general` para el error del tope diario), `form` (valores crudos del POST para repoblar; `{}` en GET), `height_required` (bool), `last_height` (Decimal en metros o `None`), `daily_limit_reached`. Si falta el consentimiento la vista **redirige** a `member_consent` con `?next=<esta pantalla>`, así que la plantilla nunca se muestra sin consentimiento. Tras guardar redirige al historial con mensaje de éxito (y un aviso aparte si el peso cambió más de 5 kg frente a la última medición de 14 días: se guarda igual con `needs_review`).
- `health/consent.html`: `purpose`, `purpose_label`, `text_version` (número), `body`, `already_granted`, `granted_at`, `text_available`, `next` (ruta interna segura o `''`; reenviarla en el formulario como campo oculto `next` para volver a la pantalla de origen), `privacy_policy_url` (de la variable de entorno `PRIVACY_POLICY_URL`; vacío = no se muestra), `errors` (`accept`, `text_version` y `general`; solo `accept` se pinta junto a la casilla, los demás llegan además como `messages`), `form`. POST: `accept=on` + `text_version` oculto. Revocar: POST a `member_consent_revoke` (puede llevar `next`).

**Plantillas NUEVAS pendientes del frontend** (las vistas ya las usan; hoy no existen):

1. `health/minor_blocked.html` (se entrega con HTTP **403**; sin contexto especial): "Consulta con tu entrenador". La usan el consentimiento y "Registrar medidas" cuando lo último que se sabe de la edad (valoración inicial) es menor de 18.
2. `trainer/body_measurement_edit.html` (panel del entrenador, `trainer/base.html`): mismo formulario que `trainer/body_measurement_add.html`, repoblado. Contexto: `client`, `measurement` (objeto), `errors` (mismas claves que el alta: `weight`, `height`, `waist_cm`, `hip_cm`, `body_fat_pct`, `notes`), `form` (valores como texto; en GET vienen de la medición). POST a la misma URL (`trainer_measurement_edit`).

**Cambios a plantillas del entrenador existentes** (pedido): (a) `trainer/body_measurement_add.html`: agregar los campos `hip_cm` ("Cadera (cm)") y `body_fat_pct` ("Grasa corporal (%)") con su `errors.hip_cm` / `errors.body_fat_pct` junto al input (el backend ya no los avisa por `messages`) y columnas Cadera, % grasa, Origen en el historial. (b) `trainer/client_detail.html` y el historial: columnas cadera, % grasa y origen (`m.source` = `member` → "Clienta", `trainer` → "Entrenador"), insignia "Revisar" si `m.needs_review`, y por medición un enlace Editar (`trainer_measurement_edit`) y un botón Borrar (`trainer_measurement_delete`, formulario POST con `{% csrf_token %}` y confirmación).

**Resuelto del frontend:** `privacy_policy_url` → se pasa desde la vista (ver arriba). No se creó una página de política: el enlace aparece cuando se defina `PRIVACY_POLICY_URL`.

### Contrato A2 para el frontend (IMPLEMENTADO por el backend, 2026-10-08)

Referencia final de la fase A2 (ficha de salud y alimentación: F-04 y F-05 y las pantallas del entrenador). Si algo difiere de las filas F-04/F-05 de arriba, **manda este bloque**. Las 5 plantillas de abajo **no existen todavía**: las vistas ya las usan y las pruebas del backend las reemplazan con plantillas mínimas (`stub_missing_templates` en `apps/health/tests/helpers.py`, solo para las que faltan). Las pruebas comprueban el **contexto**, no el HTML, así que cuando existan las reales basta con que se rendericen sin error con este contexto; al integrar se pueden quitar las entradas A2 de `_STUBS`.

**Nombres de URL** (todos existen):

| Nombre | Ruta | Métodos | Quién |
| --- | --- | --- | --- |
| `member_health_profile` | `/health/profile/` | GET, POST | clienta |
| `member_health_profile_confirm` | `/health/profile/confirm/` | POST | clienta |
| `member_health_export` | `/health/profile/export/` | GET | clienta (descarga `mis-datos-de-salud.json`; no es una pantalla: usar un enlace normal, sin `fetch`) |
| `member_health_delete` | `/health/profile/delete/` | GET, POST | clienta |
| `trainer_health_profile` | `/health/clients/<client_pk>/profile/` | GET | entrenador / superusuaria |
| `trainer_health_profile_edit` | `/health/clients/<client_pk>/profile/edit/` | GET, POST | entrenador / superusuaria |
| `member_consent_revoke` (ya existía) | `/health/consent/<purpose>/revoke/` | POST | clienta. **Cambio**: al retirar `health_data` y tener ficha, redirige a `member_health_delete?from_revoke=1` para ofrecer el borrado |

**Acceso**

- Rutas de la clienta: sesión + rol cliente + membresía vigente + `HEALTH_FEATURES_ENABLED`. El entrenador recibe 403; sin membresía, `accounts/no_membership.html` / `membership_expired.html`; con la función apagada **todas responden 404**. Siempre trabajan sobre la sesión: ninguna recibe el id de otra persona. Menor de 18 (por `birth_date` de su ficha; sin ficha, por la edad de su valoración inicial): `member_health_profile` responde **403 con `health/minor_blocked.html`**.
- Rutas del entrenador: `trainer_health_profile` y `trainer_health_profile_edit` solo para el entrenador que tiene asignada a la clienta o la superusuaria (lo demás, 404; la clienta, 403). **`trainer_health_profile` no depende de `HEALTH_FEATURES_ENABLED`** (el entrenador siempre puede revisar); **`trainer_health_profile_edit` sí** (apagada = 404, no se recogen datos nuevos).
- Todas llevan `Cache-Control: no-store`; no pongas datos de salud en la URL ni cargues scripts/imágenes de terceros nuevos.
- Guardar la ficha exige el consentimiento `health_data` vigente (no el de IA). Si falta, las vistas **redirigen** a `member_consent` con `?next=` (la plantilla del formulario nunca se muestra sin consentimiento). Ver, exportar y borrar la ficha propia **no** exigen consentimiento (derechos de la titular).
- El entrenador **solo corrige una ficha existente** (no la crea): sin ficha, o sin consentimiento vigente de la clienta, las vistas redirigen a `trainer_health_profile` con un mensaje. Su corrección crea una versión nueva **sin confirmar**.

**Contexto por plantilla**

1. `health/profile_form.html` (F-04, clienta, `base.html`) y 5. `trainer/health_profile_form.html` (entrenador, `trainer/base.html`) reciben **el mismo diccionario**:
   - `mode` (`'member'` | `'trainer'`), `client` (la clienta; solo en modo `trainer`, si no `None`), `profile` (versión vigente o `None`), `is_edit` (bool: ya hay ficha; texto del botón "Guardar cambios" vs "Guardar ficha").
   - `steps`: lista de `{number, key, title}` (los 5 pasos, ver "Pasos del asistente").
   - `choices`: diccionario de listas `[(código, etiqueta)]` para pintar casillas y selectores: `sex`, `goals`, `conditions`, `medications`, `injuries`, `surgery`, `pregnancy`, `eating_disorder`, `diet`, `allergies`, `allergy_severity`, `intolerances`, `meal_slots`, `cooking`, `budget`, `training_place`, `equipment`, `experience`, `activity`. Uso: `{% for code, label in choices.conditions %}`. **No escribas los códigos a mano: pinta desde `choices`.**
   - `meal_rows`: las 5 comidas en orden del día, cada una `{code, label, required, checked, field, time}` (`field` = nombre del input de la hora, p. ej. `meal_time_lunch`; `time` = valor `HH:MM` o `''`). `required_meal_slots` = `['breakfast','lunch','dinner']`.
   - `parq_rows`: las 7 preguntas, cada una `{key, number, text, field, value}` (`field` = `parq_q1`..`parq_q7`; `value` = `'yes'` | `'no'` | `''`).
   - `errors`, `error_steps`, `first_error_step`, `form`: ver "Errores y `form`".
   - `last_height` (Decimal en metros o `None`: para mostrar "tu estatura registrada es 1,65 m" junto al peso meta), `birth_date_min` / `birth_date_max` (ISO `AAAA-MM-DD`: atributos `min`/`max` del `<input type="date">`; 100 y 18 años) y `today` (ISO).
   - Mensajes (`messages`): al fallar la validación llega el aviso "Revisa los datos marcados: hay respuestas por corregir."; los éxitos se muestran en la pantalla siguiente.
   - POST a la misma URL (`member_health_profile` o `trainer_health_profile_edit`).
2. `health/profile_detail.html` (F-05, clienta). Se muestra cuando la clienta **ya tiene ficha** y no se pidió `?edit=1`. Contexto:
   - `profile`: la ficha vigente (objeto). Además de sus campos (ver "Campos"), tiene: `version`, `age`, `sex_label`, `conditions_labels`, `medications_labels`, `injuries_labels`, `allergies_labels`, `intolerances_labels`, `equipment_labels`, `meal_slots_labels`, `meal_schedule` (`[{code, label, time}]`), `parq_answers` (`[{key, number, text, answer}]`, `answer` True = Sí), `parq_yes_count`, `target_weight_kg`, `sleep_hours`, `created_at`, `confirmed_by_client_at` y `get_<campo>_display` para los campos de opción única (`recent_surgery`, `pregnancy_status`, `eating_disorder_history`, `diet_type`, `allergy_severity`, `cooking_access`, `budget_level`, `training_place`, `experience_level`, `activity_level`). Los textos libres (`other_condition_text`, `medications_text`, `injuries_text`, `disliked_foods_text`, `liked_foods_text`) siempre escapados.
   - `needs_confirmation` (bool: la corrigió el entrenador y ella no la ha confirmado → botón "Confirmar mis datos", `POST` a `member_health_profile_confirm`), `edited_by_trainer` (bool), `age`, `goal_label` (meta en texto), `health_consent_valid` (bool: si es falso, "Corregir mis datos" y "Confirmar" llevarán primero al consentimiento; avisa que debe volver a autorizar).
   - `consent_cards`: una por finalidad `{purpose, label, active, granted_at, version, current_version, outdated}`; `outdated` = aceptó un texto que ya no es el vigente. Cada tarjeta: botón revocar (POST a `member_consent_revoke`, `purpose`) si `active`, o enlace a `member_consent` si no.
   - `versions`: historial `[{version, is_current, created_at, created_by_role, confirmed_by_client_at}]` (más reciente primero; `created_by_role` = `member` | `trainer`).
   - Enlaces/acciones: **Corregir** = `{% url 'member_health_profile' %}?edit=1`; **Descargar mis datos** = `member_health_export`; **Borrar mi ficha** = `member_health_delete`. Nunca se envían banderas ni valoraciones de riesgo a la clienta (no existe `flags` en este contexto).
3. `health/profile_delete.html` (clienta). Contexto: `profile` (vigente o `None`), `has_profile` (bool), `versions_count` (int), `from_revoke` (bool: llegó tras retirar el consentimiento; explicar "retiraste tu autorización, ¿quieres borrar también tu ficha?"). Formulario `POST` a `member_health_delete` (solo `{% csrf_token %}`; opcional campo oculto `from_revoke`). Texto claro de lo que se pierde: **todas las versiones** de la ficha; no revoca consentimientos; el registro de accesos se conserva. Tras borrar redirige a `member_measurement_list`. Botón "No, conservar" → `member_health_profile`.
4. `trainer/health_profile.html` (entrenador). Contexto:
   - `client`, `has_profile` (bool), `profile` (ficha vigente, **o `None` si no hay contenido visible**), `content_visible` (bool: hay ficha **y** la clienta tiene el consentimiento `health_data` vigente), `consent_valid` (bool), `consent_health` y `consent_ai` (registro de consentimiento vigente o `None`; tiene `granted_at` y `text_version.version`).
   - Si `has_profile` y no `content_visible`: mostrar "La clienta no tiene una autorización vigente para tratar sus datos de salud" **sin ningún dato** (no se escribe registro de lectura y `flags` es `None`).
   - `needs_confirmation` (bool: la última versión la corrigió el entrenador y la clienta no la ha confirmado), `is_minor` (bool; si es `True` y no hay ficha: "menor de 18, gestión manual"), `goal_label`, `anthropometrics` (objeto con `weight`, `height`, `measured_on`, `days_old` o `None`), `versions` (igual que arriba; `[]` si no es visible).
   - `flags` (**solo entrenador**, nunca a la clienta): objeto con `red`, `yellow`, `info`, `missing` (tuplas de banderas) más `blocked_scopes`, `yellow_scopes` (conjuntos con `'exercise'` y/o `'nutrition'`), `allowed_scopes`, `has_red`. Cada bandera: `code` (p. ej. `R05_CARDIAC`), `severity` (`red`|`yellow`|`info`|`missing`), `text`, `action` (qué hacer) y `scopes` (a qué plan afecta: `exercise` = "Ejercicio", `nutrition` = "Alimentación"). Mostrar en cuatro grupos con color propio: rojas (bloquean la generación automática), amarillas (revisión obligatoria), informativas y datos faltantes. Texto fijo visible: "Las banderas son una ayuda para el entrenador, no un diagnóstico" (**[A VALIDAR]** por un profesional de la salud). Si `flags` trae solo `missing` (sin ficha), mostrar "Pide a la clienta que llene su ficha".
   - `can_edit` (bool: hay contenido visible y la función está encendida → botón "Corregir", enlace a `trainer_health_profile_edit`; si es `False`, ocultar el botón), `feature_enabled` (bool).
5. `trainer/health_profile_form.html`: mismo contexto que F-04 con `mode='trainer'` y `client`. Aviso fijo arriba: "La clienta deberá confirmar tus cambios." No incluye el consentimiento ni crea fichas nuevas.

**Campos del formulario (nombres POST) y listas cerradas**

El servidor valida **todo** al guardar; el navegador solo ayuda. Las casillas múltiples se envían como el mismo nombre repetido. Aviso: **un `<input>` deshabilitado no se envía**: las tres comidas obligatorias van marcadas, pero si las bloqueas usa un `<input type="hidden">` con el mismo valor.

| Paso | Campo POST | Tipo / regla |
| --- | --- | --- |
| 1 | `birth_date` | fecha `AAAA-MM-DD` (también acepta `dd/mm/aaaa`); obligatoria; de 18 a 100 años |
| 1 | `sex_for_calculation` | `F` Femenino · `M` Masculino · `NA` Prefiero no decir (se usa solo en las fórmulas de calorías) |
| 1 | `training_goal` | meta (la misma de `User.training_goal`): `weight_loss` Pérdida de peso · `muscle_gain` Ganancia muscular · `cardio` Resistencia cardiovascular · `rehab` Rehabilitación / Salud general · `sports` Rendimiento deportivo · `toning` Tonificación y definición |
| 1 | `target_weight_kg` | opcional; 30 a 250, acepta coma decimal; debe ser coherente con la estatura registrada |
| 2 | `conditions` (varias) | `diabetes_t1` Diabetes tipo 1 · `diabetes_t2` Diabetes tipo 2 · `prediabetes` Prediabetes · `hypertension` Hipertensión (presión alta) · `heart_disease` Enfermedad del corazón · `arrhythmia` Arritmia · `stroke_history` Antecedente de derrame o ataque cerebral · `kidney_disease` Enfermedad de los riñones · `liver_disease` Enfermedad del hígado · `fatty_liver` Hígado graso · `thyroid_disease` Enfermedad de la tiroides · `asthma` Asma · `osteoporosis` Osteoporosis · `herniated_disc` Hernia discal · `cancer_active` Cáncer (en tratamiento o seguimiento) · `bariatric_surgery` Cirugía bariátrica · `metabolic_disorder` Trastorno metabólico · `gout` Gota · `reflux_gastritis` Reflujo o gastritis · `ibs` Colon irritable · `celiac` Enfermedad celíaca · `other` Otra condición. Vacío = ninguna |
| 2 | `condition_controlled` | casilla (`on`); solo tiene sentido si hay condiciones (si no, el servidor la apaga) |
| 2 | `medical_clearance` | casilla (`on`): "mi médico me autorizó a hacer ejercicio" |
| 2 | `clearance_date` | fecha; **obligatoria si `medical_clearance`** (no futura); si no hay autorización, se ignora |
| 2 | `medications` (varias) | `insulin` Insulina · `hypoglycemic_drugs` Medicamentos para bajar el azúcar · `antihypertensives` Medicamentos para la presión · `beta_blockers` Betabloqueadores · `anticoagulants` Anticoagulantes · `corticosteroids` Corticoides · `thyroid_meds` Medicamentos para la tiroides · `other` Otro medicamento |
| 2 | `injuries` (varias) | `knee` Rodilla · `lower_back` Espalda baja · `shoulder` Hombro · `hip` Cadera · `ankle` Tobillo · `wrist_elbow` Muñeca o codo · `neck` Cuello |
| 2 | `recent_surgery` | obligatorio: `none` Ninguna · `under_6m` Hace menos de 6 meses · `6_12m` Hace entre 6 y 12 meses · `over_12m` Hace más de 12 meses |
| 2 | `pregnancy_status` | obligatorio: `none` Ninguno · `pregnant` Embarazo · `lactating` Lactancia · `postpartum_under_6m` Posparto de menos de 6 meses. El servidor lo rechaza si el sexo es `M` (puedes ocultarlo con ese sexo y enviar `none`) |
| 2 | `eating_disorder_history` | obligatorio: `no` No · `yes` Sí · `prefer_not_say` Prefiero no decir (tono cuidadoso; es voluntario y se explica para qué se usa) |
| 2 | `other_condition_text`, `medications_text`, `injuries_text` | texto libre opcional, máx. 300 caracteres |
| 3 | `parq_q1` … `parq_q7` | obligatorios, `yes` o `no` (radios Sí/No sin ninguno marcado de inicio). Textos en `parq_rows[].text` (**[A VALIDAR]**) |
| 4 | `diet_type` | obligatorio: `omnivore` Como de todo · `vegetarian` Vegetariana · `vegan` Vegana · `pescatarian` Pescetariana · `halal` Halal · `kosher` Kosher · `other` Otra |
| 4 | `allergies` (varias) | `gluten` Gluten · `milk` Leche · `egg` Huevo · `peanut` Maní · `tree_nut` Frutos secos · `fish` Pescado · `shellfish` Mariscos · `soy` Soya · `sesame` Ajonjolí. Vacío = ninguna |
| 4 | `allergy_severity` | **obligatoria si hay alergias** (si no, se ignora): `mild` Leve o moderada · `anaphylaxis` Grave (puede causar anafilaxia) |
| 4 | `intolerances` (varias) | `lactose` Lactosa · `fructose` Fructosa · `fodmap` FODMAP · `other` Otra |
| 4 | `meal_slots` (varias) | **siempre** `breakfast` Desayuno, `lunch` Almuerzo y `dinner` Cena; opcionales `mid_morning` Media mañana y `snack` Merienda (3 a 5 en total) |
| 4 | `meal_time_breakfast`, `meal_time_mid_morning`, `meal_time_lunch`, `meal_time_snack`, `meal_time_dinner` | hora opcional `HH:MM` (`<input type="time">`); la de una comida no elegida se descarta |
| 4 | `cooking_access` | obligatorio: `full` Puedo cocinar con todo lo necesario · `basic` Tengo lo básico para cocinar · `none` No puedo cocinar |
| 4 | `budget_level` | obligatorio: `low` Bajo · `medium` Medio · `high` Alto |
| 4 | `eats_out_per_week` | opcional, entero 0 a 21 (vacío = 0) |
| 4 | `disliked_foods_text`, `liked_foods_text` | texto libre opcional, máx. 300 caracteres |
| 5 | `training_days_per_week` | obligatorio, entero 1 a 6 |
| 5 | `session_minutes` | obligatorio, entero 20 a 120 |
| 5 | `training_place` | obligatorio: `gym` En el gimnasio · `home` En casa |
| 5 | `equipment` (varias) | **obligatorio solo si `training_place=home`**: `none` Ninguno (excluyente) · `dumbbells` Mancuernas · `bands` Bandas elásticas · `bench` Banco · `barbell` Barra · `machines` Máquinas · `cardio_machine` Máquina de cardio · `mat` Colchoneta. En el gimnasio no se pregunta (el servidor lo vacía) |
| 5 | `experience_level` | obligatorio: `none` Nunca he entrenado · `beginner` Principiante · `intermediate` Intermedia · `advanced` Avanzada |
| 5 | `activity_level` | obligatorio: `sedentary` Sedentaria (casi no me muevo) · `light` Ligera (me muevo algo en el día) · `moderate` Moderada (activa casi todos los días) · `active` Activa (trabajo o rutina muy activa) |
| 5 | `sleep_hours` | opcional, 3 a 12, acepta coma decimal (un decimal) |

Textos libres: el servidor los normaliza (espacios, caracteres invisibles) y **rechaza `<` y `>`**; muéstralos siempre escapados. Peso y estatura **no** van en la ficha: viven en las medidas (`member_measurement_add`). Una clienta que no tiene medidas no puede pedir plan (fase B), así que conviene un enlace "Registrar mis medidas" al terminar.

**Errores y `form` (convención)**

- `errors`: diccionario plano `{nombre_del_campo: "mensaje"}` (un solo texto por campo; varios se unen). Hay una clave por cada campo de la tabla (`birth_date`, `meal_slots`, `parq_q3`, `meal_time_lunch`, `equipment`…) y, además, `general` cuando falla algo que no es de un campo (p. ej. "Se guardó otra versión al mismo tiempo…"). Pinta `errors.<campo>` junto a cada control (para los 7 radios del cuestionario y los grupos de casillas, junto al grupo). No uses claves con guion bajo inicial.
- `form`: **diccionario con forma estable** (nunca el `request.POST` crudo, porque un `QueryDict` entrega solo el último valor de una lista). Todas las claves existen siempre: los campos de varias opciones (`conditions`, `medications`, `injuries`, `allergies`, `intolerances`, `meal_slots`, `equipment`) son **listas** (`{% if 'knee' in form.injuries %}checked{% endif %}`); las casillas únicas (`condition_controlled`, `medical_clearance`) son `True`/`False`; todo lo demás son **textos** (`''` si está vacío), incluidos `parq_q1`..`parq_q7` (`'yes'`/`'no'`/`''`), `meal_time_<comida>`, `birth_date` y `clearance_date` en ISO. En el primer GET sin ficha vienen vacíos salvo `meal_slots = ['breakfast','lunch','dinner']` y `training_goal` si la clienta ya tenía meta; al editar vienen de la ficha vigente.
- `error_steps`: lista ordenada de pasos (1 a 5) que tienen errores, p. ej. `[3, 5]`; `first_error_step`: el primero o `None`. Al re-renderizar con errores, abrir el asistente en `first_error_step`, marcar en rojo los pasos de `error_steps` y no perder lo escrito en los demás pasos (todos los pasos están en un solo `<form>`, los pasos ocultos solo se esconden con Alpine; **no uses `x-if`**, que no los enviaría).

**Pasos del asistente** (`steps`): 1 Datos básicos y meta · 2 Salud (condiciones, control médico, medicamentos, lesiones, cirugía, embarazo/lactancia, antecedentes alimentarios, textos libres) · 3 Cuestionario de aptitud (7 preguntas Sí/No) · 4 Alimentación · 5 Entrenamiento. Los campos de cada paso son los de la columna "Paso" de la tabla. Aviso fijo arriba del asistente: "Responder sobre salud es voluntario; sin esta información tu entrenador armará el plan manualmente." Menor de 18: solo `health/minor_blocked.html`.

**Enlaces que se piden en plantillas existentes**

- `trainer/client_detail.html`: enlace/botón **"Ficha de salud"** → `{% url 'trainer_health_profile' client.pk %}` (siempre visible para el entrenador; la propia pantalla explica si no hay ficha o autorización). Si quieres un indicador: `HealthProfile` se consulta desde la vista, no desde la plantilla (pídelo en `requests.md` si lo necesitas).
- Panel de la clienta (`accounts/member_dashboard.html`): tarjeta **"Mi ficha de salud"** → `{% url 'member_health_profile' %}`, solo dentro de `{% if HEALTH_FEATURES_ENABLED %}`.
- Navbar (`partials/navbar.html`): enlace **"Mi ficha"** junto a "Mis medidas", solo con `{% if HEALTH_FEATURES_ENABLED %}`.
- Opcional: en `assessments/member_measurements.html`, un enlace a "Mi ficha de salud" dentro del mismo `{% if %}`.

**Qué NO hace el backend en A2 (para no esperarlo en la UI):** no hay pantalla de historial de accesos (queda en `HealthAccessLog`, sin interfaz); no se pueden ver versiones anteriores de la ficha (solo la lista `versions`); el entrenador no ve ni gestiona consentimientos desde esta pantalla (solo ve si existen); las banderas no se guardan, se calculan cada vez.

## De frontend-architect para arquitecto-backend

### Fase A1: plantillas entregadas (F-01, F-02, F-03) y lo que asumen de las vistas

Plantillas listas: `assessments/member_measurements.html`, `assessments/member_measurement_add.html`, `health/consent.html` (más parciales `assessments/partials/measurement_field.html` y `partials/health_privacy_note.html`). Resumen en `accounts/member_dashboard.html` y enlace "Mis medidas" en `partials/navbar.html`. Usan estos nombres de URL del contrato (sección 2.4): `member_measurement_list`, `member_measurement_add`, `member_measurement_delete` (pk), `member_consent` (purpose), `member_consent_revoke` (purpose). **El panel de la clienta y la navbar fallan con NoReverseMatch hasta que existan `member_measurement_list`, `member_measurement_add` y `member_consent`.**

Contexto que las plantillas esperan (si el contrato A1 difiere, avisar y se ajusta):

- F-01: `measurements` (objetos con `pk`, `date`, `weight`, `height` en metros, `waist_cm`, `hip_cm`, `body_fat_pct`, `imc`, `imc_classification`, `source` = `'member'`/`'trainer'`, `can_delete` bool), `needs_consent` bool. `series` no se usa (sin gráfico por ahora).
- F-02: `errors` (dict campo -> texto; claves: `weight`, `height`, `waist_cm`, `hip_cm`, `body_fat_pct`, `notes`; opcional `general` para un error no ligado a campo; no usar claves con guion bajo inicial, Django no las lee), `form` (valores crudos para repoblar), `height_required` bool, `last_height` (estatura en metros, se muestra como "{{ last_height }} m"). Campos POST: `weight`, `height`, `waist_cm`, `hip_cm`, `body_fat_pct`, `notes`.
- F-03: `purpose` (`health_data` / `ai_processing`), `text_version` (número; se reenvía como campo oculto `text_version`), `body`, `already_granted`, `granted_at`; opcional `errors.accept`. POST: `accept=on`. Revocar: POST a `member_consent_revoke`.
- Dashboard: usa `user.measurements.all|slice:":3"` (sin cambio de vista; el modelo ordena por `-date,-pk`).

**Dependencia backend (nueva):** no existe una página de política de tratamiento de datos. F-03 muestra el enlace solo si el contexto trae `privacy_policy_url`. Pedido: o bien una página pública (nombre de URL a definir) o bien pasar esa variable desde la vista. Mientras tanto la clienta ve el canal de WhatsApp para ejercer derechos.

### Fase A2: plantillas entregadas (F-04, F-05 y pantallas del entrenador)

Plantillas listas, contra el bloque "Contrato A2 para el frontend": `health/profile_form.html`, `health/profile_detail.html`, `health/profile_delete.html`, `trainer/health_profile.html`, `trainer/health_profile_form.html`. Parciales: `health/partials/` (`profile_wizard`, `profile_summary`, campos reutilizables) y `trainer/partials/health_flag_group.html`. Enlaces añadidos: "Ficha de salud" en `trainer/client_detail.html`, tarjeta "Mi ficha de salud" en `accounts/member_dashboard.html` y "Mi ficha" en `partials/navbar.html` (estos dos con `HEALTH_FEATURES_ENABLED`).

- Se renderizaron con el contexto real de las vistas (ver pruebas del backend: ahora usan las plantillas reales; `stub_missing_templates` solo cubre las que faltan). **Pedido (backend):** al integrar, quitar las 5 entradas A2 de `_STUBS` en `apps/health/tests/helpers.py`.
- Sin pedidos nuevos de contrato. Observación: el dashboard de la clienta no sabe si ya tiene ficha (no se consulta desde la plantilla); la tarjeta dice "Abrir mi ficha" en ambos casos. Si se quiere "Completar mi ficha" / "Ver mi ficha", la vista del dashboard puede pasar un booleano `has_health_profile`.

## Resueltos

(ninguno)
