# Pedidos entre backend y frontend

Archivo compartido: cada agente agrega lo suyo o marca como resuelto lo suyo; no borra lo del otro.
Contrato de referencia: `docs/contracts/plan-ia.md` (propuesta pendiente de aprobación; nada se construye hasta que la persona la apruebe).

## De arquitecto-backend para frontend-architect

Estado: contrato aprobado. **Fase A1 implementada en el backend** (ver "Contrato A1 para el frontend" más abajo); las demás fases siguen **PENDIENTES** hasta que se construyan.

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

## De frontend-architect para arquitecto-backend

### Fase A1: plantillas entregadas (F-01, F-02, F-03) y lo que asumen de las vistas

Plantillas listas: `assessments/member_measurements.html`, `assessments/member_measurement_add.html`, `health/consent.html` (más parciales `assessments/partials/measurement_field.html` y `partials/health_privacy_note.html`). Resumen en `accounts/member_dashboard.html` y enlace "Mis medidas" en `partials/navbar.html`. Usan estos nombres de URL del contrato (sección 2.4): `member_measurement_list`, `member_measurement_add`, `member_measurement_delete` (pk), `member_consent` (purpose), `member_consent_revoke` (purpose). **El panel de la clienta y la navbar fallan con NoReverseMatch hasta que existan `member_measurement_list`, `member_measurement_add` y `member_consent`.**

Contexto que las plantillas esperan (si el contrato A1 difiere, avisar y se ajusta):

- F-01: `measurements` (objetos con `pk`, `date`, `weight`, `height` en metros, `waist_cm`, `hip_cm`, `body_fat_pct`, `imc`, `imc_classification`, `source` = `'member'`/`'trainer'`, `can_delete` bool), `needs_consent` bool. `series` no se usa (sin gráfico por ahora).
- F-02: `errors` (dict campo -> texto; claves: `weight`, `height`, `waist_cm`, `hip_cm`, `body_fat_pct`, `notes`; opcional `general` para un error no ligado a campo; no usar claves con guion bajo inicial, Django no las lee), `form` (valores crudos para repoblar), `height_required` bool, `last_height` (estatura en metros, se muestra como "{{ last_height }} m"). Campos POST: `weight`, `height`, `waist_cm`, `hip_cm`, `body_fat_pct`, `notes`.
- F-03: `purpose` (`health_data` / `ai_processing`), `text_version` (número; se reenvía como campo oculto `text_version`), `body`, `already_granted`, `granted_at`; opcional `errors.accept`. POST: `accept=on`. Revocar: POST a `member_consent_revoke`.
- Dashboard: usa `user.measurements.all|slice:":3"` (sin cambio de vista; el modelo ordena por `-date,-pk`).

**Dependencia backend (nueva):** no existe una página de política de tratamiento de datos. F-03 muestra el enlace solo si el contexto trae `privacy_policy_url`. Pedido: o bien una página pública (nombre de URL a definir) o bien pasar esa variable desde la vista. Mientras tanto la clienta ve el canal de WhatsApp para ejercer derechos.

## Resueltos

(ninguno)
