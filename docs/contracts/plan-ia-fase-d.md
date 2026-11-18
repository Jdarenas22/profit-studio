# Fase D de los planes con IA: planes de una clienta, progreso y clonar a borrador

Estado: **APROBADO el 2026-11-18** por la persona, con las 6 recomendaciones de la sección 11 (1A a 6A). Propuesta escrita el 2026-11-17. **Backend implementado** en la rama `feature/fase-d` (registro y diferencias en la sección 13, que manda sobre las secciones 5 a 8); las plantillas son de `frontend-architect`. Autor: arquitecto-backend. Complementa `plan-ia.md` (secciones 8.2, 8.3, 14 y 18; la 18 manda sobre diferencias) y el bloque «Fase D: contrato D-01 a D-04» de `requests.md`. Las pantallas las construye `frontend-architect` contra ese bloque.

## 0. Resumen en ocho líneas

1. Faltan tres piezas de la fase D: **D-01** «Planes de una clienta» (`trainer_client_plans`, antes F-15), **D-02** «Progreso de la clienta» (`trainer_client_progress`, antes F-16) y **D-03** «Clonar a borrador» (`trainer_plan_clone`).
2. **Sin modelos nuevos, sin migraciones y sin claves foráneas nuevas a `User`**: `ERASE_POLICY` no cambia. Todo se arma con `Plan`, `PlanEvent`, `PlanGenerationJob`, `BodyMeasurement`, `RoutineDayLog` y `MealChoice`, que ya existen.
3. Las tres rutas viven en `apps/plans`, solo para entrenadores, con las mismas protecciones de siempre: 404 si la función está apagada, 404 si la clienta no es accesible, `Cache-Control: no-store`.
4. **Clonar** copia el contenido de un plan aprobado, reemplazado o archivado a un borrador nuevo (`in_review`). La clienta sigue viendo su plan vigente hasta que el entrenador apruebe la copia. No llama a la IA.
5. **Gráficos sin librerías**: el servidor entrega las series ya convertidas en puntos para dibujar un `<svg>` en línea, y la tabla de medidas siempre se muestra (alternativa accesible). Una librería por CDN queda como pregunta para la persona.
6. **Adherencia** = días de rutina marcados (`RoutineDayLog`) y comidas marcadas (`MealChoice.completed_at` solo sobre opciones activas), con la misma ventana y los mismos umbrales que `rules/adaptation.py`.
7. **Sin consentimiento `health_data` vigente** el entrenador no ve medidas, comidas ni porcentajes (sección 4.3).
8. F-17 `trainer_plan_jobs` **no se construye** (decisión ya tomada: la bandeja lista `attention_jobs` y `active_jobs`).

---

## 1. Qué ya está hecho y qué se construye

| Pieza | Estado |
| --- | --- |
| `member_meal_complete` (marcar comida hecha) | Hecha (sección 18 de `plan-ia.md`, «Fase D (parcial): marcar comida hecha») |
| `trainer_plan_badge` (contador del menú) | Hecha («Fase D (parcial): contador del menú») |
| `trainer_plan_jobs` (F-17) | **No se construye** (ver la nota final del bloque del contador en la sección 18) |
| `trainer_client_plans` (**D-01**) | Este contrato |
| `trainer_client_progress` (**D-02**) | Este contrato |
| `trainer_plan_clone` (**D-03**) | Este contrato |

El estado actual del código que este contrato toca (leído el 2026-11-17):

- `Plan` ya tiene `parent`, `routine`, `health_profile`, `profile_version`, `measurement`, `flags`, `ai_item_ids`, `reviewed_by`, `approved_*`, `valid_until`, y `PlanEvent.ACTION_CLONED` ya existe (sin uso).
- `approve_plan` ya pone en `superseded` al aprobado anterior y desactiva su rutina; `is_stale(plan)` compara `plan.health_profile_id` con la ficha vigente.
- `RoutineDayLog` guarda **una fila por día de rutina marcado** (`completed_at` es un `DateField`; lo escribe `mark_day_complete`, que solo acepta días de rutinas activas de la propia clienta).
- La adaptación (`apps/plans/adaptation.py`) ya cuenta con `option__is_active=True` las comidas marcadas.
- **El árbol de trabajo NO está limpio**: hay una rama de datos nutricionales sin commit (`MealOption.nutrient_snapshot`, nuevos campos de `Food`, migración `plans/0005`) que toca `models.py`, `nutrition.py` y `services.py`, los mismos archivos que usará D-03. Ver la sección 12.

---

## 2. Decisiones de arquitectura (resumen)

| Tema | Decisión | Por qué |
| --- | --- | --- |
| Modelos | **Ninguno nuevo.** Clonar crea filas de los modelos que ya existen | No hay claves foráneas nuevas a `User` ni migración; `ERASE_POLICY` y la eliminación de cuentas siguen igual |
| Capas | Vista (HTTP) -> `services.py` / `nutrition.py` (casos de uso) -> consultas. Cálculos de progreso en un módulo **puro** `rules/progress.py` y lectura de la base en `progress.py`; planes y trabajos de una clienta en `overview.py` | Igual que la adaptación: lo que se calcula se prueba sin base de datos |
| Gráficos | SVG en línea armado con puntos que calcula Python (sin JavaScript ni librerías) + tabla siempre visible | `requests.md` prohíbe cargar scripts de terceros en páginas con datos de salud |
| Clonar | Copia **idéntica** del contenido a un borrador `in_review`; la procedencia (ficha, medida base) se conserva y las advertencias se recalculan con la ficha de hoy | El borrador no se hace pasar por «generado con la ficha de hoy»: si la ficha cambió, sale como obsoleto y exige confirmación |
| Idempotencia de clonar | Bloqueo de fila del plan origen + una sola copia en borrador por plan origen | El doble clic devuelve la misma copia |
| Un borrador por tipo | Clonar no se permite si la clienta ya tiene otro borrador del mismo tipo (o uno generándose) | Evita borradores paralelos que se pisan |
| Consentimiento | Sin `health_data` vigente: sin medidas, sin comidas, sin porcentajes ni texto de banderas | Regla del proyecto (el entrenador solo ve datos de salud con autorización vigente) |
| Función apagada | Las 3 rutas usan `plans_feature_required` (`HEALTH_FEATURES_ENABLED` y `AI_PLANS_MODE` distinto de `off`): 404 si no | Igual que el resto de `/plans/trainer/` |
| Verbos | `trainer_plan_clone` queda como `POST /plans/trainer/<pk>/clone/`, igual que `approve`/`archive`/`regenerate` (ya aprobado en la tabla 8.3). Es una acción interna del panel, no una API pública | Consistencia con las rutas existentes |

---

## 3. Rutas (todas en `apps/plans/urls.py`)

| Método | Ruta | Nombre | Quién | Qué hace |
| --- | --- | --- | --- | --- |
| GET | `/plans/trainer/clients/<client_pk>/` | `trainer_client_plans` | entrenador / superusuaria | **D-01** planes por tipo con versiones, trabajos recientes y botón «Generar borrador» |
| GET | `/plans/trainer/clients/<client_pk>/progress/` | `trainer_client_progress` | entrenador / superusuaria | **D-02** series de medidas, línea base y adherencia de 4 semanas |
| POST | `/plans/trainer/<pk>/clone/` | `trainer_plan_clone` | entrenador / superusuaria | **D-03** crea un borrador nuevo a partir del plan `<pk>` y redirige a su detalle |

La ruta `/plans/trainer/clients/<client_pk>/generate/` (`trainer_plan_generate`) ya existe y no cambia de forma. `/plans/trainer/jobs/` (`trainer_plan_jobs`) **no se crea**.

Decoradores, en este orden (como las demás vistas del entrenador): `@plans_feature_required`, `@trainer_required`, `@require_GET` (o `@require_POST`).

---

## 4. Reglas comunes

### 4.1 Autorización

| Quién | D-01 / D-02 (GET) | D-03 (POST) |
| --- | --- | --- |
| Anónimo | redirige al inicio de sesión | igual |
| Clienta | 403 (`trainer_required`) | 403 |
| Entrenador asignado | sí | sí |
| Otro entrenador | 404 | 404 |
| Superusuaria | sí, todas | sí |
| Clienta sin entrenador asignado | solo la superusuaria; los demás 404 | igual |
| Cuenta eliminada (lápida) | 404 (`clients_for_trainer` la excluye) | 404 |
| Cuenta desactivada | **se ve** (solo lectura) | **rechazado** con `INACTIVE_CLIENT_MESSAGE`: «Esta cuenta está desactivada. Reactívala para hacer cambios.» |

- D-01 y D-02 cargan a la clienta con `get_client_for_trainer(request, client_pk)`. D-03 carga el plan con `services.get_plan_for_trainer(request.user, pk)` (que filtra con `clients_for_trainer`); lo no accesible = 404, sin revelar si existe.
- D-03 además comprueba dentro del servicio (`services._check_writable`: acceso + cuenta activa), como `approve_plan`.
- Un `pk` de otra clienta o de otro plan jamás cambia lo que se muestra: ninguna pantalla recibe datos por la URL fuera del `pk` propio de la ruta.

### 4.2 Interruptores y cabeceras

- Función apagada (`HEALTH_FEATURES_ENABLED` falso o `AI_PLANS_MODE=off`): las tres rutas responden **404**. D-02 no usa la IA, pero queda bajo el mismo interruptor que el resto de `/plans/` (ver pregunta 6).
- `Cache-Control: no-store` en todas (lo pone `plans_feature_required` con `never_cache`).
- Ningún dato de salud en la URL (solo `pk` numéricos) ni en los logs. Las vistas D-01 y D-02 no escriben logs. D-03 registra solo ids: `plan cloned plan_id=<nuevo> source_id=<origen> actor_id=<id>`.
- D-03 exige CSRF (como todo POST del panel) y responde 405 a GET.

### 4.3 Qué ve el entrenador sin consentimiento `health_data` vigente

`content_visible = health.trainer_can_see_content(client)` (el consentimiento `health_data` vigente sobre el texto vigente).

| Dato | Con consentimiento | Sin consentimiento |
| --- | --- | --- |
| Versiones, estados, fechas, quién aprobó, tipo y origen de cada plan (D-01) | sí | sí (es información del plan, no de la ficha) |
| Cuántas advertencias tiene un plan (`flags_count`) | sí | sí (solo el número) |
| Texto de las banderas de un trabajo bloqueado (`flag_texts`, `blocked_flags`) | sí | **no** (llegan vacíos; se muestra el texto genérico) |
| Estado de los trabajos y su código técnico | sí | sí |
| Requisitos que faltan (`missing`), incluido «falta la autorización» | sí | sí |
| Serie de medidas, tabla, línea base y cambios (D-02) | sí | **no** (`measurements` y `charts` vacíos, `baseline` en `None`) |
| Días de rutina marcados (conteo por semana) | sí | sí (no es un dato de salud: es asistencia al entrenamiento) |
| Meta de días por semana (se lee de la ficha) y porcentaje de constancia | sí | **no** (`expected`, `rate` y `level` en `None`) |
| Comidas marcadas | sí | **no** (`meals` en `None`) |

En D-02 la plantilla muestra el aviso: «La clienta no tiene una autorización vigente para tratar sus datos de salud: no se muestran sus medidas ni sus comidas.»

Observación (no cambia este contrato): `trainer/client_detail.html` ya muestra las últimas 10 mediciones sin mirar el consentimiento. La pregunta 2 decide si se iguala.

### 4.4 Consultas acotadas

D-01 y D-02 arman todo con un número **fijo** de consultas (sin una consulta por plan, por trabajo ni por medida: `select_related`, `Exists` y agregados). Cada prueba fija el número exacto medido al construir con `assertNumQueries` y comprueba que **no crece** al triplicar planes, trabajos, opciones o medidas.

### 4.5 Fechas

«Hoy» es `health_dates.today()` (`timezone.localdate()`, `America/Bogota`). Las fechas viajan como `date` y la plantilla las formatea; los textos que redacta el servidor usan `dd/mm/aaaa`.

---

## 5. D-01: Planes de una clienta (`trainer_client_plans`, `trainer/client_plans.html`)

Extiende `trainer/base.html`, bloque `trainer_content`. Es una pantalla de **lectura** más los botones «Generar borrador» y «Clonar a borrador» (que son formularios POST a rutas que ya existen o a D-03).

### 5.1 Contexto exacto

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `client` | `User` | la clienta |
| `client_active` | bool | `client.is_active` |
| `inactive_message` | str | `INACTIVE_CLIENT_MESSAGE` si la cuenta está desactivada, si no `''` |
| `content_visible` | bool | ver 4.3 |
| `ai_mode` | str | `'simulated'` o `'live'` (con `off` la ruta da 404). Con `simulated` se muestra la franja «MODO DE PRUEBA» |
| `groups` | lista de 2 `dict` | siempre en este orden: ejercicio, alimentación (5.2) |
| `generate` | `dict` con claves `'exercise'` y `'nutrition'` | datos del botón «Generar borrador» de cada tipo (5.3) |
| `jobs` | lista de `dict` (máx. 20) | trabajos recientes de la clienta, el más reciente primero (5.4) |
| `jobs_truncated` | bool | hay más de 20 |

### 5.2 `groups[i]`

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `kind` | str | `'exercise'` o `'nutrition'` |
| `label` | str | «Plan de ejercicio» / «Plan de alimentación» |
| `current` | `row` o `None` | la fila del plan **aprobado** vigente |
| `draft` | `row` o `None` | la fila del borrador o plan en revisión más reciente (si hay) |
| `rows` | lista de `row` | todas las versiones del tipo, la más reciente primero, **máx. 15** |
| `rows_truncated` | bool | hay más de 15 versiones |
| `total` | int | versiones que existen del tipo |

Cada `row`:

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `plan` | `Plan` | con `version`, `status`, `created_at`, `approved_at`, `approved_by` (cargado con `select_related`; mostrarlo con `trainer/partials/person_name.html`), `valid_until`, `is_simulated` |
| `status_label` | str | `plan.get_status_display()`: «Borrador de la IA», «En revisión», «Aprobado», «Reemplazado», «Archivado» |
| `is_current` | bool | `status == 'approved'` |
| `is_pending` | bool | `status` es `draft_ai` o `in_review` |
| `origin` | str | `'clone'` si tiene un evento `cloned`, si no `'ai'` |
| `origin_label` | str | «Copia de la versión N» (con `parent_version`), «Generado con IA» o «Regenerado con IA (de la versión N)» cuando hay `parent_version` y no es copia |
| `parent_version` | int o `None` | versión de la que viene (`plan.parent.version`; `None` si no tiene o se borró) |
| `flags_count` | int | cuántas advertencias guardó el plan (`len(plan.flags)`); el texto está en el detalle |
| `stale` | bool o `None` | solo para `is_pending` e `is_current`: la ficha cambió después de generarlo (misma regla que `services.is_stale`, con la ficha vigente leída **una vez**); `None` en los demás |
| `expired` | bool | aprobado y `valid_until` ya pasó |
| `can_clone` | bool | el estado es `approved`, `superseded` o `archived` **y** la cuenta está activa (si no, el botón se oculta o se deshabilita con `inactive_message`) |

Las URL se arman en la plantilla (`trainer_plan_detail`, `trainer_plan_clone`).

Se leen **todos los planes de la clienta en una consulta** (tope 61 filas ordenadas por `-version`) y se reparten por tipo en Python.

### 5.3 `generate[kind]`

El servidor evalúa con `services.assess(client, kind, request.user)` (una vez por tipo; lectura pura, nunca crea un trabajo).

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `label` | str | «Generar borrador de rutina con IA» / «Generar borrador de alimentación con IA» |
| `request_token` | str (UUID) | uno nuevo en cada carga de la pantalla, para el formulario POST a `trainer_plan_generate` (`kind` + `request_token`) |
| `can_request` | bool | `eligibility.can_request` **y** la cuenta está activa **y** no hay un pedido en curso. Con `False` el botón se deshabilita |
| `blocked` | bool | hay una bandera roja (o `D06`) que impide generar con IA |
| `blocked_text` | str | texto para el entrenador (5.5) o `''` |
| `blocked_flags` | lista de `{code, text, action}` | las banderas rojas explicadas (`services.describe_codes`), **sin** `D06_*` ni `NUTRITION_CLINICAL_PAUSED` (esos tienen su propio `blocked_text`); **vacía sin consentimiento** |
| `missing` | lista de `{code, text, action, url}` | requisitos que faltan, en voz del entrenador (5.5); `url` es `None` cuando solo la clienta lo puede resolver |
| `active_job` | `PlanGenerationJob` o `None` | pedido en curso (enlazar a `trainer_plan_job`); con uno en curso `can_request` es `False` |
| `has_pending_draft` | bool | ya hay un borrador o plan en revisión de este tipo (aviso: «Ya hay un borrador de este tipo; generar otro lo deja aparte»; no bloquea) |

Con `blocked` verdadero **no se debe ofrecer el botón** (evita crear un trabajo bloqueado y un correo al propio entrenador); en su lugar se muestra `blocked_text` y las banderas.

### 5.4 `jobs[i]`

Los 20 trabajos más recientes de la clienta (cualquier estado y tipo, incluido «otra opción» de comida), con `select_related('plan')`.

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `job` | `PlanGenerationJob` | `pk`, `status`, `created_at`, `finished_at`, `progress_done`, `progress_total`, `requested_by_role`, `plan_id` |
| `kind_label` | str | «Plan de ejercicio», «Plan de alimentación» u «Otra opción de comida» |
| `status` | str | `queued`, `running`, `succeeded`, `failed` o `blocked` |
| `status_label` | str | `job.get_status_display()` |
| `tone` | str | `info` (en cola o generando), `good` (listo), `warning` (bloqueado), `danger` (falló) |
| `summary` | str | frase ya redactada (tabla de 5.6) |
| `flag_texts` | lista de str | solo con `blocked` y consentimiento: los textos de las banderas rojas del bloqueo (sin `D06_*` ni `NUTRITION_CLINICAL_PAUSED`, que ya van en `summary`); si no, `[]` |
| `technical_code` | str | `error_code` (o `''`), para mostrar chico como «Código técnico» |
| `requested_by_label` | str | «Clienta» o «Entrenador/a» (sin nombres) |
| `plan_id` | int o `None` | si el trabajo creó un borrador, para enlazar a `trainer_plan_detail` |

Los trabajos en curso (`queued`/`running`) enlazan a `trainer_plan_job` (que ya sondea el estado).

### 5.5 Textos para el entrenador (voz del entrenador, no de la clienta)

Los textos de `rules/eligibility.py` (`REQUIREMENT_TEXTS`) están escritos para la clienta («tus datos»). En D-01 se usan estos, definidos en `apps/plans/texts.py` (`TRAINER_REQUIREMENT_TEXTS`):

| Código | `text` | `action` | `url` |
| --- | --- | --- | --- |
| `D00_NOT_AVAILABLE` | «Los planes con IA no están disponibles ahora (función apagada o textos legales sin revisar).» | «Avisa a la administradora.» | `None` |
| `D01_CONSENT_HEALTH` | «La clienta aún no autorizó el tratamiento de sus datos de salud.» | «Pídele que lo autorice desde su cuenta.» | `None` |
| `D01_CONSENT_AI` | «La clienta no autorizó el envío sin identificar de sus datos a la IA.» | «Pídele que lo autorice, o arma el plan a mano.» | `None` |
| `D02_PROFILE` | «La clienta aún no llena su ficha de salud y alimentación.» | «Pídele que la llene desde su cuenta.» | `None` (el entrenador no crea fichas) |
| `D02_PROFILE_CONFIRM` | «La clienta no ha confirmado la última corrección de su ficha.» | «Pídele que la confirme.» | `None` |
| `D03_MEASURE` | «La clienta no tiene medidas (peso y estatura) de los últimos 30 días.» | «Registra una medición.» | `trainer_measurement_add` |
| `D04_INCOHERENT` | «Hay datos que no cuadran (peso, estatura o fecha de nacimiento).» | «Revisa la ficha y las medidas.» | `trainer_health_profile` |
| `D05_QUOTA` | «Se alcanzó el límite diario de generaciones.» | «Inténtalo mañana.» | `None` |
| `D07_NUTRITION_DETAILS` | «Falta el nivel de actividad diaria para armar el plan de alimentación.» | «Corrígelo en la ficha («Más detalles») o pídele que lo complete.» | `trainer_health_profile_edit` |

Texto de `blocked_text` según el código que bloquea:

| Código | `blocked_text` |
| --- | --- |
| banderas rojas (cualquiera) | «La ficha tiene una bandera que exige valoración profesional: no se genera borrador con IA. Arma el plan a mano o deriva a la clienta.» |
| `D06_FEW_EXERCISES` | «No hay suficientes ejercicios compatibles con la ficha (revisa las etiquetas del catálogo). Arma el plan a mano.» |
| `D06_FEW_FOODS` | «No hay suficientes alimentos compatibles con la ficha. Arma la guía de alimentación aparte.» |
| `NUTRITION_CLINICAL_PAUSED` (`wellness.OUT_OF_SCOPE`) | «Los menús de bienestar no cubren este caso: la alimentación clínica está pausada.» |

Sin consentimiento, `blocked` sigue siendo verdadero y `blocked_text` se muestra, pero `blocked_flags` queda vacía.

(Esos textos reemplazan también el mensaje de `trainer_plan_generate` «Aún no se puede generar: …», que hoy usa la voz de la clienta. Es un cambio pequeño de texto en `views.py`; se anota en la sección 8.)

### 5.6 Resumen amable de un trabajo (`jobs[i].summary`)

Lo arma `overview.job_summary(job)`. Primero por estado; para `failed` se busca el código exacto y luego por prefijo.

| Estado o `error_code` | `summary` |
| --- | --- |
| `queued` | «En cola: empezará en un momento.» |
| `running` | «Generando el borrador.» (con `progress_total > 1`: «Generando el borrador (paso {done} de {total}).») |
| `succeeded`, ejercicio o alimentación | «Listo: se creó el borrador (versión {n}).» (sin plan: «Listo: se creó el borrador.») |
| `succeeded`, otra opción de comida | «Listo: se creó una opción nueva para la clienta.» |
| `blocked` con `D06_FEW_EXERCISES` | «Bloqueado por seguridad: no hay suficientes ejercicios compatibles con la ficha.» |
| `blocked` con `D06_FEW_FOODS` | «Bloqueado por seguridad: no hay suficientes alimentos compatibles con la ficha.» |
| `blocked` con `NUTRITION_CLINICAL_PAUSED` | «Bloqueado: la alimentación clínica está pausada.» |
| `blocked` (otro) | «Bloqueado por seguridad: la ficha requiere valoración profesional antes de un plan automático. No se llamó a la IA.» |
| `failed`: `timeout`, `ai_timeout`, `ai_network` | «La IA tardó demasiado o no respondió. Puedes volver a intentarlo.» |
| `failed`: `ai_http_429` | «El servicio de IA estaba saturado. Inténtalo de nuevo en unos minutos.» |
| `failed`: `ai_http_5xx` (500 a 599) | «El servicio de IA tuvo un problema temporal. Inténtalo de nuevo.» |
| `failed`: `ai_http_400/401/403/404`, `ai_not_configured`, `ai_bad_schema_field` | «La conexión con la IA no está bien configurada. Avisa a la administradora.» |
| `failed`: `ai_blocked_prompt`, `ai_blocked_safety` | «El servicio de IA bloqueó la solicitud por seguridad. Arma el plan a mano.» |
| `failed`: `ai_invalid_json`, `ai_unreadable_response`, `ai_no_candidates`, `ai_finish_*` | «La IA devolvió una respuesta que no se pudo usar. Puedes volver a intentarlo.» |
| `failed`: `V01_SCHEMA` a `V09_RECIPE` y `validation_failed:*` | «El borrador no pasó las revisiones de seguridad y no se guardó. Puedes volver a intentarlo.» |
| `failed`: `recipes_unavailable` | «No había suficientes recetas compatibles con la ficha y las porciones. Revisa los alimentos disponibles o arma la guía a mano.» |
| `failed`: `not_ready:<Dxx>` | «Cuando se iba a generar faltaba un requisito: {texto de 5.5 del código Dxx}.» |
| `failed`: `worker_lost`, `lost_lock` | «El proceso se interrumpió (por ejemplo, un reinicio del servidor). Vuelve a intentarlo.» |
| `failed`: `client_inactive` | «La cuenta estaba desactivada cuando se iba a generar.» |
| `failed`: `kind_not_available` | «Ese tipo de plan no está disponible.» |
| `failed`: `slot_missing`, `plan_not_active`, `option_changed` | «El plan cambió mientras se generaba la opción. La clienta puede volver a pedirla.» |
| `failed`: cualquier otro | «No se pudo generar el borrador. Código técnico: {código}.» |

Una prueba recorre **todos** los `JobFailed('...')` y códigos de `ai/gemini.py` y comprueba que ninguno cae en «cualquier otro» (así un código nuevo obliga a redactar su texto).

### 5.7 Casos borde de D-01

- Clienta sin planes ni trabajos: `groups[i].rows == []`, `current` y `draft` en `None`, `jobs == []` (la plantilla muestra el estado vacío con el botón de generar).
- Clienta desactivada: se ve todo; `generate[*].can_request` es `False` y `inactive_message` explica por qué; `row.can_clone` es `False`.
- Trabajos de otra clienta nunca aparecen (filtro por `client`).
- Un plan sin ficha enlazada (`health_profile` borrada): `stale` es `True` en pendientes y vigente.
- `ai_mode == 'simulated'` (solo desarrollo): franja «MODO DE PRUEBA»; los planes `is_simulated` llevan su propia etiqueta (ya existente en el detalle).

---

## 6. D-02: Progreso de la clienta (`trainer_client_progress`, `trainer/client_progress.html`)

Extiende `trainer/base.html`. Pantalla de **solo lectura**. Parcial reutilizable para cada gráfico: `trainer/partials/progress_chart.html`.

### 6.1 Contexto exacto

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `client` | `User` | la clienta |
| `client_active` | bool | `client.is_active` |
| `content_visible` | bool | ver 4.3. Con `False` la plantilla muestra el aviso de 4.3 en lugar de medidas y comidas |
| `today` | `date` | hoy |
| `current_plans` | `dict` | `{'exercise': plan_info o None, 'nutrition': plan_info o None}`; `plan_info` = `{'version', 'approved_on', 'valid_until', 'expired'}` (fechas `date`, `expired` bool) |
| `measurements` | lista de `dict` | tabla de medidas, la más reciente primero, **máx. 60** (6.2); `[]` sin consentimiento |
| `measurements_count` | int | cuántas medidas tiene en total (0 sin consentimiento) |
| `measurements_truncated` | bool | hay más de 60 |
| `charts` | lista de `dict` | **4** gráficos, en este orden: peso, cintura, cadera, IMC (6.3); `[]` sin consentimiento |
| `baseline` | `dict` o `None` | medida de referencia del plan y cambios desde entonces (6.4); `None` sin consentimiento |
| `last_measurement` | `dict` o `None` | `{'date', 'days_old'}` de la medida más reciente; `None` si no hay o sin consentimiento |
| `measure_due` | bool | más de 14 días sin medir, o sin medidas (invita a registrar; el plan de ejercicio pide medir cada 14 días, sección 8.2 de `plan-ia.md`) |
| `measure_due_text` | str | «Hace {n} días que no registra medidas.» o «Todavía no tiene medidas registradas.»; `''` si `measure_due` es falso o no hay consentimiento |
| `adherence` | `dict` | constancia de las últimas 4 semanas (6.5) |

El botón «Agregar medición» (`trainer_measurement_add`) se muestra solo con `client_active`; el servidor ya rechaza escribir en una cuenta desactivada.

### 6.2 `measurements[i]`

| Clave | Tipo |
| --- | --- |
| `pk` | int |
| `date` | `date` |
| `weight` | `Decimal` (kg) |
| `waist_cm` | `Decimal` o `None` |
| `hip_cm` | `Decimal` o `None` |
| `body_fat_pct` | `Decimal` o `None` |
| `imc` | `Decimal` o `None` |
| `imc_classification` | str |
| `source` | `'member'` o `'trainer'` |
| `source_label` | «Clienta» o «Entrenador/a» |
| `needs_review` | bool (posible error de digitación del peso: mostrar «Revisar») |
| `is_baseline` | bool (es la medida de referencia de `baseline`) |

Se leen las **últimas 60 medidas** en una consulta (el mismo tope que `assessments.services.SERIES_MAX_POINTS`). La tabla es la alternativa accesible a los gráficos y **se muestra siempre**, no solo con un clic.

### 6.3 Gráficos sin librerías (`charts[i]`)

El servidor entrega todo lo necesario para dibujar un `<svg>`; la plantilla no calcula nada y no usa JavaScript. Lo arma `rules/progress.build_chart` (Python puro).

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `key` | str | `weight`, `waist_cm`, `hip_cm` o `imc` |
| `label` | str | «Peso», «Cintura», «Cadera», «IMC» |
| `unit` | str | `kg`, `cm`, `cm`, `''` |
| `count` | int | puntos con dato (las medidas sin ese dato se saltan) |
| `has_chart` | bool | `count >= 2` (con 0 o 1 punto se muestra el texto de `summary` y no la línea) |
| `view_box` | str | `'0 0 640 240'` (atributo `viewBox`; fijo) |
| `plot` | `{left, right, top, bottom}` | strings con el rectángulo de dibujo: `'56'`, `'624'`, `'16'`, `'200'` (para ejes y rejilla) |
| `points` | lista de `{x, y, date, value, text, needs_review, is_baseline}` | **`x` e `y` son strings con un decimal y punto** (p. ej. `'123.4'`), `date` es `date`, `value` es `Decimal`, `text` es «72,5 kg el 03/10/2026» |
| `polyline` | str | `'56.0,120.5 300.0,98.2 ...'` para `<polyline points="...">` (`''` si no hay línea) |
| `y_ticks` | lista de 3 `{y, label}` | mínimo, medio y máximo del eje vertical (`y` string con punto; `label` redondeado a 1 decimal, con coma) |
| `x_ticks` | lista de 1 o 2 `{x, label}` | primera y última fecha (`dd/mm/aaaa`) |
| `summary` | str | frase para `<title>`/`<desc>` y para lectores de pantalla |

Reglas de dibujo (para que las pruebas sean deterministas):

- Puntos ordenados de la fecha más antigua a la más reciente. El eje X es proporcional a los días entre la primera y la última medida (`span = max(días, 1)`); con un solo punto, queda al centro.
- El eje Y va de `mín - margen` a `máx + margen`, con `margen = 10 % de (máx - mín)`; si todos los valores son iguales, `margen = 1`.
- **Las coordenadas son strings** porque Django las localizaría con coma decimal (`es-CO`) y romperían los atributos SVG. Nunca se pasan como `float` a la plantilla.
- `summary`, ejemplos: «Peso: de 74,0 kg (03/10/2026) a 72,5 kg (31/10/2026); cambió -1,5 kg.» · «Peso: una sola medición, 74,0 kg (03/10/2026).» · «Sin datos de peso.»
- Marcar con símbolo distinto (y texto, no solo color) los puntos con `needs_review` y el punto `is_baseline`.
- Solo atributos SVG (`stroke`, `fill`, `points`, `cx`, `cy`...): sin `style` en línea ni `<script>`. Accesible: `role="img"` + `aria-labelledby` apuntando a `<title>` y `<desc>`.

### 6.4 `baseline`

La **medida de referencia** es `Plan.measurement` (la medida con la que se calculó el plan; se conserva al clonar).

Regla: entre los planes **aprobados hoy** (ejercicio y/o alimentación) que tengan `measurement`, se usa el de la medida **más antigua** (si hay empate, el de ejercicio). Si ninguno tiene medida (por ejemplo, la clienta borró esa medida del mismo día y la clave quedó en `NULL`), `baseline` es `None`.

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `plan_kind`, `plan_kind_label`, `plan_version` | str, str, int | de qué plan viene |
| `date` | `date` | fecha de la medida de referencia |
| `weight`, `waist_cm`, `hip_cm`, `imc` | `Decimal` o `None` | valores de referencia |
| `latest_date` | `date` o `None` | la medida más reciente **sin** `needs_review` (si todas están marcadas, `None`) |
| `days_between` | int o `None` | días entre `date` y `latest_date` |
| `same_measurement` | bool | la última medida confiable **es** la de referencia (todavía no hay nada nuevo: no mostrar cambios) |
| `delta` | `dict` | `{'weight', 'waist_cm', 'hip_cm', 'imc'}`: `Decimal` con signo (último menos referencia), `None` si falta alguno de los dos valores o si `same_measurement` |

### 6.5 `adherence` (constancia de 4 semanas)

Misma definición que `apps/plans/adaptation.py` y `rules/adaptation.py` (las constantes `WINDOW_DAYS = 28`, `MIN_DAYS_OF_HISTORY = 14`, `ADHERENCE_LOW = 0.5` y `ADHERENCE_HIGH = 0.8` se **importan**, no se copian):

- **Ventana**: los días `inicio < día <= hoy`, con `inicio = max(hoy - 28, primer_plan_aprobado)`. `primer_plan_aprobado` es la fecha local del primer `approved_at` de la clienta en planes `approved` o `superseded` (función ya existente `adaptation._history_since`, que se vuelve pública como `history_since` sin cambiar su comportamiento). Con menos de 14 días de historial: **arranque en frío**, sin porcentaje ni nivel.
- **Entrenamiento**: cuenta las filas de `RoutineDayLog` de la clienta dentro de la ventana (una fila = un día de rutina marcado; igual que la adaptación, no se filtra por rutina ni por plan).
- **Comidas**: cuenta las filas de `MealChoice` de la clienta con `completed_at` no vacío, `option__is_active=True` y `date` dentro de su ventana. **Solo opciones activas**: lo que la clienta ve es lo que cuenta (la misma regla de `adaptation._completed_meals`).
- **Esperado de entrenamiento** = `días_por_semana × días_de_ventana / 7`, con `días_por_semana` = `training_days_per_week` de la ficha vigente. Solo se lee con `content_visible` y ficha vigente; no abre la ficha para mostrarla y por eso **no escribe `HealthAccessLog`**.
- **Esperado de comidas** = `comidas_por_día × días_de_ventana`, con `comidas_por_día` = `NutritionPlan.meals_per_day` del plan de alimentación aprobado hoy; la ventana de comidas usa el primer plan de **alimentación** aprobado (así no se esperan comidas de días en que no había plan). Sin plan de alimentación aprobado, no hay esperado.
- **Porcentaje**: `rate = min(100, hecho / esperado × 100)` redondeado a entero. **Nivel**: `low` si `< 50 %`, `high` si `>= 80 %`, `medium` en medio (mismos cortes que la adaptación; para entrenamiento el nivel coincide siempre con el del panel «Qué se tuvo en cuenta»).

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `start`, `end` | `date` | primer y último día de la ventana (`start = inicio + 1`) |
| `days` | int | días de la ventana (28 o menos) |
| `weeks` | lista de 4 `{index, first_day, last_day, training_done, meals_done}` | de la más antigua (1) a la más reciente (4); cada una cubre 7 días contados hacia atrás desde hoy. `meals_done` es `None` sin consentimiento |
| `training` | `dict` (abajo) | |
| `meals` | `dict` (abajo) o `None` | `None` sin consentimiento |

`training` y `meals` tienen las mismas claves:

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `done` | int | marcados en la ventana |
| `expected` | int o `None` | esperado redondeado (`None` si no se puede calcular) |
| `per_week` | int o `None` | meta semanal que se usó (`días_por_semana`, o `comidas_por_día × 7` en comidas) |
| `rate` | int o `None` | 0 a 100 |
| `level` | `'low'`, `'medium'`, `'high'` o `None` | |
| `level_text` | str | frase ya redactada (abajo) o `''` |
| `has_plan` | bool | hay un plan aprobado (o que lo hubo) del tipo |
| `note` | str | por qué no hay porcentaje, o `''` |

Textos de `level_text` (entrenamiento, los mismos del panel de adaptación):

- `low`: «Constancia baja: completó menos de la mitad de los días de entrenamiento del último mes.»
- `medium`: «Constancia media: completó entre la mitad y cuatro de cada cinco días de entrenamiento.»
- `high`: «Constancia alta: completó al menos cuatro de cada cinco días de entrenamiento.»

Textos de `level_text` (comidas):

- `low`: «Constancia baja: marcó menos de la mitad de sus comidas del último mes.»
- `medium`: «Constancia media: marcó entre la mitad y cuatro de cada cinco comidas.»
- `high`: «Constancia alta: marcó al menos cuatro de cada cinco comidas.»

Textos de `note` (cuando `rate` es `None`):

- Sin plan del tipo: «Todavía no tiene un plan de ejercicio aprobado.» / «Todavía no tiene un plan de alimentación aprobado.»
- Arranque en frío: «Aún no hay suficiente historial: la constancia se calcula después de 14 días con un plan aprobado.»
- Sin ficha o sin autorización (solo entrenamiento): «No se muestra el porcentaje porque falta la ficha de la clienta o su autorización.»

Con `has_plan` falso, `done` se sigue mostrando si hay marcas (una rutina creada a mano también cuenta).

### 6.6 Casos borde de D-02

- Sin medidas: `measurements == []`, `charts` con 4 gráficos de `count == 0` (cada uno con su `summary` «Sin datos de …»), `baseline` en `None`, `measure_due` verdadero.
- Una sola medida: sin línea; `summary` de una medición.
- Medidas con `needs_review`: se muestran y se marcan; los cambios (`delta`) usan la última **sin** esa marca.
- Dos medidas el mismo día (clienta y entrenador): ambos puntos se dibujan en la misma `x`.
- Cuenta desactivada: se ve todo.
- Cero marcas pero plan aprobado hace más de 14 días: `rate = 0`, `level = 'low'` (es información verdadera, no un error).
- El plan fue aprobado hace menos de 14 días: arranque en frío (sin `rate`), pero `done` y las barras por semana sí se muestran.

---

## 7. D-03: Clonar a borrador (`trainer_plan_clone`, `POST /plans/trainer/<pk>/clone/`)

### 7.1 Qué hace y qué no

Crea un **borrador nuevo** con el contenido del plan `<pk>` para que el entrenador lo ajuste y lo apruebe. **No cambia nada de lo que la clienta ve** hasta que se apruebe la copia; al aprobarla, el plan vigente pasa a `superseded` (lógica de `approve_plan`, que no cambia). No llama a la IA, no crea trabajos y no manda correos.

**Clonar no recalcula**: la copia conserva los objetivos de energía y la medida base del original. Para recalcular con las medidas de hoy se usa «Regenerar» o «Generar borrador».

### 7.2 Formulario (`CloneForm`)

| Campo | Regla |
| --- | --- |
| `reason` | opcional, máx. **300** caracteres (`CLONE_REASON_MAX`), normalizado con `clean_text`, sin `<` ni `>` (misma regla que `ArchiveForm`). Va al historial como motivo de la copia |

Sin `request_token`: la idempotencia no depende del formulario (7.5).

### 7.3 Desde qué plan se puede clonar

| Estado del origen | ¿Se puede? |
| --- | --- |
| `approved` | sí (el caso normal: ajustar el plan vigente) |
| `superseded` | sí (volver a una versión anterior) |
| `archived` | sí (recuperar un plan retirado o descartado) |
| `draft_ai`, `in_review` | **no**: «Este plan ya es un borrador: edítalo en lugar de clonarlo.» |

### 7.4 Qué se crea

**`Plan` nuevo** (siempre):

| Campo | Valor |
| --- | --- |
| `client`, `kind` | los del origen |
| `version` | `services.next_version(client, kind)` (máximo + 1, sin huecos reutilizados) |
| `status` | `in_review` |
| `reviewed_by` | quien clona (como `take_for_review`; no hace falta «Tomar para revisión») |
| `parent` | el plan origen |
| `routine` | ejercicio: la `Routine` nueva e **inactiva** (abajo); alimentación: `None` |
| `health_profile`, `profile_version`, `measurement` | **copiados del origen** (procedencia: con qué ficha y medida se hizo el contenido). Por eso, si la ficha cambió desde entonces, `is_stale` da verdadero y aprobar exige la confirmación «revisé con la ficha actual» |
| `flags` | **recalculadas**: los códigos amarillos de la ficha vigente HOY para ese tipo (vacío si no hay ficha). Si no hay advertencias, aprobar no pide la casilla |
| `is_simulated`, `model_name`, `prompt_version`, `ruleset_version` | copiados (procedencia del contenido) |
| `ai_item_ids` | **`[]`** (ver el recuadro) |
| `approved_by`, `approved_at`, `reviewer_credential`, `note_for_client`, `warnings_ack_*`, `valid_until`, `archived_at` | vacíos: cada aprobación los llena de nuevo |
| `internal_notes` | vacío (campo sin uso hoy) |

> **Por qué `ai_item_ids = []`**: la adaptación (`adaptation._trainer_removals`) compara lo que propuso la IA con lo que quedó en cada plan y cuenta «el entrenador quitó X en 2 planes distintos». Si la copia heredara `ai_item_ids`, una sola corrección del entrenador contaría dos veces (en el original y en la copia) y distorsionaría esa señal. Con la lista vacía, la adaptación ignora la copia (`exclude(ai_item_ids=[])`).

**`PlanEvent`**: **uno solo**, en el plan nuevo: `action='cloned'`, `actor=quien clona`, `note` = «Copia de la versión {n}.» + (si hay motivo) « Motivo: {reason}» + (si el origen tenía opciones sin revisar de «otra opción») « Incluye {k} opción(es) pedidas por la clienta que no estaban revisadas.», recortada a 500 caracteres. No se escribe evento en el origen (así «origen = copia» se decide solo por este evento y no se confunde).

**Ejercicio**: `Routine` nueva con `name` y `notes` del original, `user = clienta`, `trainer = quien clona`, `is_active = False`. Copia **todos** los `RoutineDay` (`name`, `order`) y los `RoutineExercise` de cada día (`exercise`, `sets`, `reps`, `rest_seconds`, `observations`, `order`), tal como están hoy en la rutina del origen (si el entrenador la editó después de aprobar, se copia lo que la clienta ve).

**Alimentación** (todo con filas nuevas, nada compartido con el original):

| Modelo | Qué se copia | Qué no |
| --- | --- | --- |
| `NutritionPlan` | todos los campos (objetivos, `tmb`, `get`, `activity_factor`, `adjustment_pct`, `weekly_change_kg`, `floor_applied`, `warnings`, `meals_per_day`, `formula`) | |
| `MealSlot` | `code`, `name`, `time_hint`, `order` y los objetivos de la comida | |
| `MealOption` | solo las **activas**: `position`, `name`, `preparation`, `kcal`, `protein_g`, `carbs_g`, `fat_g`, `origin` (se conserva la procedencia), `out_of_band`, `nutrient_snapshot` (copia profunda del JSON: las fuentes quedan «congeladas» como en el original), `is_active = True` | las inactivas (reemplazadas por «otra opción»), `replaces` (queda `None`), `needs_review` (queda `False`: la copia entera pasa por la revisión del entrenador antes de aprobar) |
| `MealOptionItem` | `food`, `grams`, `order` | |
| `MealChoice` | **nada**: las elecciones y marcas de la clienta pertenecen al plan aprobado | |

Si una comida del origen no tiene 3 opciones activas, se copia como está y **aprobar la copia queda bloqueado** por `approval_problems` (regla ya existente).

### 7.5 Cómo se ejecuta (orden fijo de comprobaciones)

`services.clone_plan(plan, actor, reason='') -> (nuevo, creado)`, dentro de `transaction.atomic()`:

1. `_check_writable(plan, actor)`: acceso y cuenta activa (`PlanNotAllowed` / `PlanClientInactive`).
2. `select_for_update()` del plan origen (el doble clic en paralelo espera al primero y luego ve su copia).
3. Estado del origen (7.3).
4. Contenido: ejercicio sin rutina, o alimentación sin `NutritionPlan`: «Este plan no tiene contenido que copiar.»
5. Plan de prueba fuera de `DEBUG`: «Este plan se generó en MODO DE PRUEBA: no se puede clonar.» (no podría aprobarse).
6. **Idempotencia.** Se busca un plan de la misma clienta y tipo en `draft_ai` o `in_review` con `parent = origen` y un evento `cloned`. Si existe: `(ese_plan, False)`, sin crear nada ni escribir otro evento. Si hay **otro** borrador del mismo tipo: «Ya hay un borrador de {tipo} (versión {n}) en revisión. Ábrelo o archívalo antes de clonar otro.»
7. **Pedido en curso**: si `services.active_job(client, kind)` existe: «Hay un borrador generándose para esta clienta. Espera a que termine.»
8. **Solo alimentación**: se evalúa con `nutrition.current_allowed_ids(origen)`. Con bandera roja vigente: «La ficha actual de la clienta requiere valoración profesional antes de armar un plan de alimentación. No se puede clonar.» Fuera del alcance de bienestar (`NUTRITION_CLINICAL_PAUSED`): «Los menús de bienestar no cubren esta solicitud. La alimentación clínica está pausada.» (La copia no se podría editar ni aprobar, así que no se crea.) **En ejercicio no se bloquea**: el entrenador siempre puede armar o ajustar el plan a mano, y los choques con la ficha se muestran como advertencia (regla ya existente de la sección 5 de `plan-ia.md`).
9. Se crea el plan, su contenido y el evento. Un `IntegrityError` por el número de versión (otro clon simultáneo de otro origen) se atrapa: se repite la búsqueda del paso 6 y, si hay otro borrador, se informa: «Se creó otro borrador al mismo tiempo. Revisa los borradores de la clienta.»

### 7.6 Respuesta de la vista

| Resultado | Respuesta |
| --- | --- |
| Creada | 302 a `trainer_plan_detail` del plan nuevo + mensaje de éxito |
| Ya existía (doble clic) | 302 a la copia existente + mensaje informativo |
| Error de formulario (`reason`) | 400 y se vuelve a pintar `trainer/plan_detail.html` del **origen** con `clone_errors` (igual que archivar y regenerar) |
| Error de negocio (`PlanError`) | 302 de vuelta a `trainer_plan_detail` del origen + `messages.error` |
| Plan inexistente o de otra clienta | 404 |
| Función apagada | 404 |

Mensajes ya redactados:

- Éxito con plan vigente: «Creamos la versión {n} como borrador. La clienta sigue viendo la versión {m} hasta que apruebes esta.»
- Éxito sin plan vigente: «Creamos la versión {n} como borrador. La clienta no verá nada hasta que la apruebes.»
- Aviso adicional (`messages.warning`) si la copia ya tiene choques con la ficha: «{k} ejercicio(s) ya no son compatibles con la ficha de la clienta. Revísalos antes de aprobar.» / «{k} opción(es) incluyen alimentos que ya no son compatibles con la clienta. Edítalas antes de aprobar: sin eso no se puede aprobar el plan.»
- Ya existía: «Ya habías creado este borrador (versión {n}). Lo abrimos para que sigas.»
- Errores de negocio: los textos de 7.3 y 7.5, más «Esta cuenta está desactivada. Reactívala para hacer cambios.» y, para el motivo, «El motivo admite máximo 300 caracteres.» / «No uses los signos < ni >.»

### 7.7 Incompatibilidades con la ficha de hoy

| Caso | Qué pasa |
| --- | --- |
| Alimentación: una opción trae un alimento que ya no es compatible (alérgeno, dieta, etiquetas de advertencia, alimento inactivo o sin validar cuando la validación está activa) | La copia **se crea igual** (idéntica). `nutrition.option_conflicts` / `review_context['conflicts']` las lista en el detalle y **aprobar queda bloqueado** hasta editarlas con `trainer_meal_option_edit` (que solo ofrece alimentos permitidos). Es la regla ya existente de `approval_problems` |
| Ejercicio: un ejercicio ya no pasa el filtro de la ficha o del catálogo | La copia se crea igual; `services.routine_conflicts` lo lista como advertencia y **no bloquea** (regla existente: «es responsabilidad del entrenador») |
| Alimentación con bandera roja o fuera del alcance | **No se clona** (7.5, paso 8) |
| La ficha cambió o se borró desde el original | La copia sale como obsoleta (`stale`) y aprobar exige la confirmación |
| Advertencias nuevas (amarillas) por cambios en la ficha | `flags` se recalcula: aprobar exige «Revisé las advertencias» |

### 7.8 Qué cambia en `trainer/plan_detail.html` (contexto nuevo de `_plan_detail_context`)

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `clone` | `dict` o `None` | `None` si el plan es borrador o está en revisión (no se muestra nada de clonar) |
| `clone_errors` | `dict` | `{'reason': '...'}` tras un error de formulario; `{}` normalmente |

`clone` (cuando no es `None`):

| Clave | Tipo | Contenido |
| --- | --- | --- |
| `allowed` | bool | el botón «Clonar a borrador» está habilitado |
| `blocked_reason` | str | por qué no (`''` si `allowed`): cuenta desactivada, plan de prueba, ya hay una copia o ya hay otro borrador |
| `existing_draft` | `Plan` o `None` | copia de **este** plan que sigue en borrador: mostrar «Abrir el borrador (versión N)» en lugar del botón |
| `other_draft` | `Plan` o `None` | otro borrador del mismo tipo (no es copia de este) |
| `replaces_version` | int o `None` | versión vigente que la copia reemplazaría al aprobarse (`None` si no hay plan aprobado) |
| `reason_max` | int | 300 |

`_plan_detail_context` solo hace las comprobaciones **baratas** (estado, cuenta, plan de prueba, existencia de borradores, plan vigente). Las caras (bandera roja de alimentación) las hace el servicio al pulsar el botón y se informan con el mensaje.

### 7.9 Casos borde de D-03

- Origen aprobado con opciones sin revisar de «otra opción»: se copian con `needs_review = False` y se cuenta cuántas eran en la nota del evento.
- Origen aprobado cuya rutina el entrenador editó a mano: se copia lo que la clienta ve hoy.
- Clonar un plan `superseded` y aprobarlo: reemplaza al vigente (es «volver a una versión anterior»); el texto de confirmación usa `replaces_version`.
- Dos entrenadores (o el entrenador y la superusuaria) clonan el mismo plan a la vez: una sola copia; el segundo recibe «Ya habías creado este borrador».
- Cuenta desactivada o eliminada durante la operación: el bloqueo de fila y la comprobación del paso 1 lo rechazan; no queda nada a medias (transacción).
- Membresía vencida: se puede clonar (el entrenador prepara el plan; la clienta lo verá cuando renueve).
- Al aprobar la copia, la clienta **vuelve a elegir** sus comidas (sus elecciones están ligadas a las comidas del plan anterior; es lo que ya pasa con cualquier plan de alimentación nuevo). Sus comidas marcadas siguen contando en el progreso y en la adaptación (siguen siendo opciones activas de un plan anterior). Ver pregunta 4.
- La eliminación de la cuenta borra la copia igual que el resto de sus planes (`Plan.client` es `CASCADE` y la rutina cae con `Routine.user`).

---

## 8. Piezas de código y cambios a lo existente

**Archivos nuevos**

| Archivo | Contenido |
| --- | --- |
| `apps/plans/rules/progress.py` | **Python puro**: ventana y semanas, `adherence`, niveles, `build_chart`, línea base y cambios. Importa las constantes de `rules/adaptation.py` |
| `apps/plans/progress.py` | Lectura de la base para D-02 (medidas, marcas de rutina y de comida, planes aprobados) y armado del contexto |
| `apps/plans/overview.py` | Datos de D-01 (planes por tipo, trabajos, `job_summary`, `generate_info`) |

**Archivos que se modifican** (cambios pequeños y aditivos)

| Archivo | Cambio |
| --- | --- |
| `apps/plans/urls.py` | las 3 rutas |
| `apps/plans/views.py` | `trainer_client_plans`, `trainer_client_progress`, `trainer_plan_clone`; `clone` y `clone_errors` en `_plan_detail_context`; el mensaje de `trainer_plan_generate` pasa a los textos de voz del entrenador (5.5) |
| `apps/plans/forms.py` | `CloneForm` |
| `apps/plans/services.py` | `clone_plan`, `CLONE_REASON_MAX`, copia de la rutina |
| `apps/plans/nutrition.py` | `clone_nutrition_content` (copia de objetivos, comidas, opciones activas y alimentos) |
| `apps/plans/texts.py` | `TRAINER_REQUIREMENT_TEXTS`, `TRAINER_BLOCKED_TEXTS`, `JOB_FAILED_TEXTS` |
| `apps/plans/adaptation.py` | `_history_since` pasa a público como `history_since` (mismo comportamiento) |
| `CLAUDE.md` y `docs/contracts/plan-ia.md` | al terminar: sección «Fase D» y registro |

**No se tocan**: `models.py`, migraciones, `ERASE_POLICY`, `jobs.py`, `rules/adaptation.py`, `member_*`.

---

## 9. Pruebas (todas sin red; la IA siempre es el doble `FakeAIClient`)

Archivos: `apps/plans/tests/test_client_plans.py`, `test_client_progress.py`, `test_plan_clone.py` y `test_progress_rules.py` (puras, sin base de datos). Sobre `NutritionScenarioTestCase` (boss, trainer_a, trainer_b, client_a, client_b, client_free).

**Autorización (por cada una de las 3 rutas)**: anónimo -> inicio de sesión; clienta -> 403; entrenador A con su clienta -> 200; entrenador B con la clienta de A -> 404; clienta sin asignar: solo la superusuaria; lápida -> 404; cuenta desactivada: D-01 y D-02 dan 200 y D-03 se rechaza con el mensaje de cuenta desactivada y **sin filas nuevas**; función apagada (cada interruptor por separado) -> 404; `Cache-Control: no-store`; D-03 con GET -> 405 y con CSRF activado sin token -> 403.

**D-01**: las claves exactas del contexto (un conjunto fijo); `groups` en orden ejercicio/alimentación; versiones de la más reciente a la más antigua, tope de 15 y `rows_truncated`; `current` y `draft` correctos en cada combinación de estados; `origin` y `parent_version` (copia, regenerado, generado); `stale`, `expired`, `can_clone` por estado y con la cuenta desactivada; `generate`: requisitos en voz del entrenador con `url` o `None`, bloqueo por bandera roja sin crear nada al hacer GET, `can_request` falso con pedido en curso o cuenta desactivada, `request_token` distinto por tipo y por carga; trabajos de **otra clienta** no aparecen; el tope de 20; **una prueba tabla** que recorre todos los códigos de error posibles (de `jobs.py` y `ai/gemini.py`) y comprueba que ninguno cae en el texto genérico; sin consentimiento, `flag_texts` y `blocked_flags` vienen vacíos; número de consultas fijo y constante al triplicar los datos.

**D-02**: sin consentimiento, `measurements == []`, `charts == []`, `baseline is None`, `adherence['meals'] is None`, `training['rate'] is None` y `done` sí aparece; con consentimiento, la tabla trae las últimas 60 (con `truncated`); `needs_review` e `is_baseline` marcados; la línea base elige la medida más antigua de los planes aprobados y es `None` si la medida se borró; `delta` excluye las medidas con `needs_review`; `measure_due` en el borde de 14 días; **adherencia**: ventana parcial por primer plan aprobado, arranque en frío con 13 y 14 días, tope de 100 %, cortes de nivel en 50 % y 80 % (exactos), semanas con los bordes `hoy - 7` y `hoy - 6`, comidas marcadas sobre una opción **reemplazada** no cuentan y vuelven a contar si el entrenador la recupera, comidas de otra clienta no cuentan, y **el nivel de entrenamiento coincide con `rules.adaptation.training_adherence`** con los mismos datos; gráficos: 0, 1, 2 y N puntos, valores iguales, huecos (medidas sin cintura), coordenadas como `str` con punto (con idioma `es-CO` activo), `polyline` bien formada; número de consultas fijo.

**D-03**: matriz de estados de origen (aprobado, reemplazado y archivado sí; borrador y en revisión no); copia completa de ejercicio (días, orden, series, repeticiones, descanso, observaciones, rutina **inactiva**, original intacto) y de alimentación (objetivos, comidas, solo opciones activas, posiciones 1 a 3, alimentos y gramos, `origin` conservado, `needs_review = False`, `replaces = None`, `nutrient_snapshot` igual pero **independiente** del original, sin `MealChoice`); la clienta **no ve** la copia (`member_plans`, `member_nutrition_plan`, `member_routine`, `dashboard`; `mark_day_complete` sobre un día de la copia -> 404) y su plan vigente sigue igual; recorrido completo: clonar -> editar -> aprobar -> el origen queda `superseded`, un solo aprobado, rutinas intercambiadas; `stale` si la ficha cambió y no si no; `flags` recalculadas; incompatibilidades (alimentación: se crea y aprobar queda bloqueado hasta editar; ejercicio: se crea con advertencia y se puede aprobar); alimentación con bandera roja o fuera de alcance -> no se crea nada, ejercicio con bandera roja -> sí; plan de prueba fuera de `DEBUG` -> rechazo; pedido en curso -> rechazo; **doble envío** -> una sola copia y un solo evento `cloned`; otro borrador del mismo tipo -> rechazo; conflicto de número de versión simulado -> mensaje sin dejar filas; `ai_item_ids == []` y la adaptación **no cuenta dos veces** una corrección (ejercicio y alimentación); el doble de IA registra **0 llamadas**; sin correos; `assertLogs` comprueba que el registro solo trae ids.

**Contrapruebas** (quitar una pieza y ver que la prueba falla): quitar `option__is_active=True` en el conteo de comidas; quitar el bloqueo `select_for_update` del doble envío; copiar `ai_item_ids`; no recalcular `flags`; devolver las coordenadas como `float`; quitar el filtro `clients_for_trainer` de cada vista.

**Suite completa**: correr `python manage.py test --settings=config.settings.development` y `python manage.py makemigrations --check --dry-run` (debe decir «No changes detected»: este contrato no cambia modelos).

---

## 10. Pantallas

El detalle de cada plantilla está en `docs/contracts/requests.md`, bloque «Fase D: contrato D-01 a D-04 (APROBADO el 2026-11-18)».

| Id | Plantilla | Reemplaza a |
| --- | --- | --- |
| D-01 | `trainer/client_plans.html` | F-15 |
| D-02 | `trainer/client_progress.html` + `trainer/partials/progress_chart.html` | F-16 |
| D-03 | cambios en `trainer/plan_detail.html` y botón en D-01 | `CloneForm` de la tabla 14 |
| D-04 | enlaces en `trainer/client_detail.html` y `trainer/plan_detail.html` | n/a |

---

## 11. Preguntas para aprobar (con mi recomendación)

**Respuestas de la persona (2026-11-18): 1A, 2A, 3A, 4A, 5A y 6A** (todas las recomendaciones). Igualar `client_detail` a la regla de la pregunta 2 queda **fuera de esta fase** y anotado como pendiente (sección 12, punto 6).

1. **Gráficos.** (A, recomendado) Dibujados por el servidor como `<svg>`, sin librerías ni JavaScript, con la tabla siempre visible. (B) Librería de gráficos por CDN (por ejemplo Chart.js, versión fija + SRI): más vistoso e interactivo, pero agrega un script de un tercero a una página con datos de salud (lo que `requests.md` prohíbe sin permiso) y obliga a tocar la cabecera de seguridad. (C) Solo tabla.
2. **Entrenador sin autorización vigente de datos de salud.** (A, recomendado) La pantalla de progreso no muestra medidas ni comidas (solo cuántos días entrenó), como pide la regla del proyecto. (B) Mostrar siempre las medidas (como hoy en la ficha del cliente) y ocultar solo las comidas. (C) No mostrar nada. Aparte: la ficha del cliente (`client_detail`) ya muestra las últimas 10 medidas sin mirar la autorización; con A queda una diferencia entre pantallas. ¿Se iguala después?
3. **Desde qué planes se puede clonar.** (A, recomendado) Aprobado, reemplazado y archivado (permite «volver a una versión anterior» y recuperar uno descartado). (B) Solo el aprobado vigente.
4. **Elecciones de comida de la clienta al aprobar la copia.** (A, recomendado) No se traspasan: la clienta vuelve a elegir sus comidas (es lo que ya pasa con cualquier plan de alimentación nuevo; sus marcas pasadas siguen contando en el progreso). (B) Traspasar a la copia las elecciones de hoy y los 6 días siguientes cuando la opción no cambió (mejor experiencia al ajustar a mitad de semana; más lógica y más riesgo). Si eliges B, se construye como un paso aparte después de A.
5. **Un solo borrador por tipo al clonar.** (A, recomendado) Si ya hay otro borrador del mismo tipo (o uno generándose), no deja clonar y ofrece abrir ese. (B) Permitir varios borradores a la vez (más confuso en la bandeja).
6. **Progreso con la IA apagada.** (A, recomendado, es lo que se pidió) La pantalla de progreso da 404 también si solo está apagada la IA, porque vive bajo `/plans/`. (B) Que solo dependa de `HEALTH_FEATURES_ENABLED` para que el entrenador vea medidas y constancia aunque no use la IA.

---

## 12. Riesgos y observaciones

1. **Datos nutricionales (resuelto).** La integración USDA FDC ya está en `main` (PR #26: `MealOption.nutrient_snapshot`, `nutrient_tables.py`, migración `plans/0005`). Hoy el snapshot se rellena en `nutrition._save_option` y `nutrition.edit_option` con `nutrient_tables.capture`, y `nutrient_tables.option_totals` lo lee. D-03 lo copia **tal cual** (copia profunda del JSON; sin volver a calcular), así la tabla nutricional de la copia es idéntica a la que la clienta ya ve.
2. **Contenido clínico**: los cortes de constancia (50 % y 80 %) vienen de la adaptación y siguen **[A VALIDAR]** por un profesional; aquí solo describen asistencia, no interpretan salud.
3. **Entrenamiento esperado**: usa los días por semana que la clienta declaró en su ficha (igual que la adaptación). Si la rutina aprobada tiene otro número de días, el esperado no cambia.
4. **Marcas de comida desde la pantalla**: la clienta solo marca hoy (el servicio acepta días anteriores). Por eso, en semanas con poco uso, el porcentaje de comidas puede quedar bajo aunque coma bien; es una limitación de lo que se mide, no un error.
5. Los textos de requisitos del entrenador (5.5) y las frases de constancia de comidas (6.5) son nuevos: revisarlos antes de producción junto con el resto de textos.
6. **Pendiente fuera de esta fase:** `trainer/client_detail.html` sigue mostrando las últimas 10 mediciones sin mirar el consentimiento `health_data` (la persona decidió, en la pregunta 2, dejarlo para después). Hasta entonces hay una diferencia entre esa pantalla y la de progreso (D-02), que sí aplica la regla.

---

## 13. Registro de implementación

### Backend (2026-11-18, rama `feature/fase-d`)

Implementado: **D-01** (`trainer_client_plans`), **D-02** (`trainer_client_progress`) y **D-03** (`trainer_plan_clone`). **Sin modelos, sin migraciones y sin claves foráneas nuevas a `User`**: `makemigrations --check --dry-run` dice «No changes detected» y `ERASE_POLICY` no cambió. Las plantillas son de `frontend-architect`: las pruebas usan plantillas de reemplazo (entradas `trainer/client_plans.html` y `trainer/client_progress.html` de `_STUBS` en `apps/health/tests/helpers.py`; se quitan al integrar). El contexto final de cada plantilla está en `requests.md`, bloque «Fase D: contrato D-01 a D-04».

**Qué se construyó**

- `rules/progress.py` (puro): ventana de 28 días y 4 semanas, constancia con los mismos cortes que la adaptación (importados), línea base, avisos de medir y gráficos como puntos de un `<svg>` con coordenadas en texto.
- `progress.py` (lectura de D-02), `overview.py` (D-01) y `cloning.py` (D-03).
- `texts.py`: textos en voz del entrenador, frases de los trabajos, frases de constancia y mensajes de clonar.
- `forms.py` (`CloneForm`), `urls.py` (3 rutas) y `views.py` (3 vistas; `clone` y `clone_errors` en `_plan_detail_context`; el mensaje de `trainer_plan_generate` pasa a la voz del entrenador).
- Sin tocar `models.py`, `services.py` ni `nutrition.py` (archivos compartidos con el otro asistente).

**Diferencias respecto a las secciones 5 a 8** (todas menores)

1. **La lógica de clonar vive en `cloning.py`** (no en `services.py` ni `nutrition.py`) para no tocar archivos compartidos. Se usa como `cloning.clone_plan(plan, actor, reason)`.
2. **`_history_since` no se hizo público**: `progress._first_approvals` calcula la misma definición (planes `approved` o `superseded` con `approved_at`) en una sola consulta con tres agregados, y una prueba comprueba que coincide con `adaptation._history_since`.
3. Los planes de una clienta se leen con un **tope de 200 filas** (el contrato decía 61).
4. Con **0 puntos**, `y_ticks` y `x_ticks` van vacíos (el contrato decía 3 y 1 o 2).
5. Sin consentimiento **ni siquiera se consulta** la medida base de los planes, las medidas, las comidas ni la ficha (una prueba mira el SQL).
6. `training.has_plan` es «hubo un plan de ejercicio aprobado»; la ventana de entrenamiento es la del primer plan de **cualquier** tipo, igual que la adaptación. El nivel coincide con el del panel «Qué se tuvo en cuenta» siempre que haya habido un plan de ejercicio aprobado (una prueba lo recorre con 275 combinaciones y otra con la base de datos).
7. Textos nuevos: «No tiene un plan de alimentación vigente.» (comidas con un plan de alimentación que ya no está vigente) y los de `internal_error` y `ai_unknown` (códigos de error que el contrato no listaba).
8. El `request_token` de «Generar borrador» se genera con `services.new_request_token()` (UUID en texto) en cada carga.
9. `trainer_plan_regenerate` **sigue** mostrando los requisitos en voz de la clienta (el contrato solo pedía cambiar `trainer_plan_generate`).

**Pruebas** (sin red; `-W error::DeprecationWarning`)

| Archivo | Pruebas |
| --- | --- |
| `test_progress_rules.py` (puras) | 20 |
| `test_plan_clone.py` | 39 |
| `test_client_plans.py` | 28 (consultas de la pantalla: 35, fijas) |
| `test_client_progress.py` | 22 (consultas de la pantalla: 11, fijas) |

Total nuevo: **109 pruebas**. Cubren autorización (anónimo, clienta, otro entrenador, superusuaria, clienta sin asignar, eliminada, desactivada), interruptores (cada uno por separado), `no-store`, CSRF, consentimiento, copia completa de ejercicio y de alimentación, que la clienta nunca ve la copia, doble clic, un borrador por tipo, bandera roja y alcance de bienestar, plan de prueba, trabajo en curso, carrera de número de versión, registros sin datos de salud, que la IA no se llama y que no salen correos, que todo código de error posible tiene texto y que el número de consultas no crece al triplicar los datos.

**Contrapruebas** (se rompe una pieza y se comprueba que la prueba falla; las 9 fallaron como debían): comidas sin filtrar opciones activas; sin bloqueo de fila del plan origen; sin comprobar si la copia ya existe; copiar `ai_item_ids`; no recalcular `flags`; coordenadas como número; y quitar el filtro `clients_for_trainer` en D-01, en D-02 y en clonar.

**Límites conocidos**

- SQLite no ejecuta `select_for_update` y las pruebas son secuenciales: la prueba del bloqueo comprueba que el servicio lo pide sobre el plan origen, pero la espera real entre dos clics simultáneos solo ocurre en PostgreSQL (la restricción única de versión y la búsqueda de la copia existente son la segunda defensa).
- No se probó HTML real (las plantillas son del frontend).
- Pendiente fuera de esta fase: `client_detail` y el consentimiento (sección 12, punto 6).
