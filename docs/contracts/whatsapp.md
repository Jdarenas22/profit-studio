# Contrato: avisos por WhatsApp (WhatsApp Business Cloud API de Meta)

- Estado: **APROBADO (2026-11-16)** con todas las opciones recomendadas de la sección 16, salvo la pregunta 14 (datos legales y dominio), que queda **PENDIENTE** (ver 16.2). **Backend implementado** en la rama `feature/whatsapp-avisos` (sección 17); las pantallas finales son del frontend (`docs/contracts/requests.md`).
- Fecha: 2026-11-16. Autor: arquitecto-backend.
- Aviso: lo legal (Ley 1581 de 2012, Ley 2300 de 2023, transferencia internacional) es una propuesta técnica marcada **[A VALIDAR por abogado]**. No es asesoría legal. Los datos de Meta se verificaron en su documentación oficial el **2026-11-16** (sección 14); Meta cambia precios, versiones y reglas con frecuencia: el implementador debe reconfirmarlos antes de codificar.
- Relación con lo existente: `WHATSAPP_NUMBER` / `WHATSAPP_LINK` (enlace `wa.me` del botón de soporte) **no cambian** y no tienen nada que ver con esta integración. Esta integración usa variables nuevas con el prefijo `WHATSAPP_CLOUD_` para que no se confundan.

---

## 0. Resumen en diez líneas

1. ProFit Studio envía **avisos automáticos de una sola vía** por WhatsApp (plantillas de categoría *utility*): membresía por vencer, membresía vencida, plan aprobado para la clienta y "hay algo para revisar" para la entrenadora.
2. **Cero datos de salud**: igual que los correos de `apps/plans/notifications.py`, un aviso solo dice "hay algo" y dónde verlo. Nunca banderas, ficha, medidas, contenido del plan ni el tipo de plan.
3. Solo recibe avisos quien **los activó en su cuenta** (consentimiento versionado: una finalidad nueva `whatsapp_notices` en `ConsentTextVersion` / `ConsentRecord`) **y confirmó su número** con un botón en el primer mensaje de WhatsApp.
4. Se puede dar de baja en cualquier momento: desde la web, con el botón "No recibir avisos" de cada mensaje o respondiendo **BAJA** / **STOP**.
5. Los avisos van a una **cola en la base de datos** (mismo patrón que `PlanGenerationJob`), con clave de idempotencia para no duplicar, reintentos con espera creciente y estados que actualiza el webhook de Meta.
6. La cola la procesa un **servicio cron de Railway** (`python manage.py process_whatsapp_queue`, cada 15 minutos en horario permitido). Los avisos que nacen de una acción (plan aprobado) se intentan enviar de inmediato en un hilo, y el cron es el respaldo.
7. **Webhook** en `/whatsapp/webhook/`: verificación `hub.verify_token`, firma `X-Hub-Signature-256` (HMAC-SHA256 con el app secret sobre el cuerpo crudo), idempotente, responde 200 rápido, exento de CSRF solo esa vista.
8. En la base solo se guardan **ids, códigos y fechas**: ni el texto de los mensajes enviados ni el de las respuestas recibidas. En los logs, nunca teléfonos completos ni tokens.
9. Modo `off` / `simulated` / `live` (como `AI_PLANS_MODE`): en desarrollo y pruebas nunca se llama a Meta. En `live`, `production.py` no arranca si falta una variable.
10. Sin dependencias nuevas: cliente HTTP con `urllib` de la biblioteca estándar (como `apps/plans/ai/gemini.py`).

---

## 1. Alcance de la fase 1

### 1.1 Avisos incluidos

| Código | Para | Cuándo se encola | Se cancela si, al momento de enviar… |
| --- | --- | --- | --- |
| `optin_confirm` | quien activa los avisos | al activar los avisos en la web (o al cambiar el número) | ya confirmó, retiró el consentimiento o cambió otra vez el número |
| `membership_expiring` | clienta | `WHATSAPP_CLOUD_EXPIRING_DAYS` días antes de `Membership.end_date` (por defecto 3), una vez por fecha de vencimiento | la membresía se renovó (cambió `end_date`), se desactivó o ya venció |
| `membership_expired` | clienta | el día siguiente a `end_date` (cuando `is_valid` pasa a falso), una vez por fecha de vencimiento | la membresía se renovó antes del envío |
| `plan_approved` | clienta | en el mismo punto donde hoy sale `notifications.client_plan_approved` | el plan ya no está `approved` |
| `trainer_review` | entrenador asignado, o las superusuarias si la clienta no tiene asignado (misma regla que `_trainer_recipients`) | en los mismos puntos que `trainer_draft_ready`, `trainer_alternative_ready`, `trainer_job_blocked` y `trainer_job_failed` | — (agrupado: máximo uno por entrenador cada 3 horas) |
| `payment_approved` (**opcional**, pregunta 2) | clienta | cuando el webhook de Wompi aprueba un pago y activa la membresía | el pago dejó de estar `approved` |
| `optout_ack` (mensaje de servicio, no plantilla) | quien pidió la baja | al recibir BAJA/STOP o el botón de baja | — |
| `autoreply` (mensaje de servicio, no plantilla) | quien escribe al número de avisos | al recibir cualquier otro mensaje, máximo uno cada 24 h por persona | — |

El correo **sigue saliendo igual** (WhatsApp es un canal adicional; pregunta 11). Un fallo de WhatsApp nunca interrumpe la aprobación de un plan, un pago ni nada del flujo (misma regla que el correo).

### 1.2 Fuera de la fase 1

- Chat de dos vías (atender conversaciones desde la plataforma), bot o respuestas con IA.
- Mensajes de **marketing**: promociones, ofertas, "renueva hoy", recuperar clientas que se fueron, difusión masiva. Exigen otra categoría de plantilla (más cara), otro consentimiento y aplicarían más restricciones de la Ley 2300 de 2023.
- Recordatorio de **pago pendiente o abandonado** de Wompi: Meta lo puede tratar como marketing (retargeting) y en Colombia podría verse como gestión de cobranza (Ley 2300). Se deja fuera (pregunta 2).
- Enviar el plan, la rutina, la dieta, medidas o cualquier contenido por WhatsApp.
- Envíos manuales desde el panel ("mandar un WhatsApp a esta clienta"): no hay ninguna ruta para que una persona escriba un texto libre que salga por la API.
- Identificar personas solo por nombre de usuario de WhatsApp (BSUID, sección 4.5): se guarda si llega, pero se envía siempre al teléfono.
- Menores de 18 años: no pueden activar los avisos (pregunta 12).

---

## 2. Decisiones de arquitectura (resumen)

| Tema | Decisión | Por qué |
| --- | --- | --- |
| App nueva | `apps/whatsapp`: `models.py`, `services.py` (casos de uso: activar, confirmar, dar de baja, encolar), `client/` (interfaz + `live` + `fake`), `templates_catalog.py` (plantillas y sus parámetros), `webhook.py` (interpretar el JSON de Meta, sin HTTP), `views.py` (solo HTTP), `calendar.py` (ventana horaria y festivos, Python puro), comandos de management | Separa HTTP, negocio y acceso a datos, igual que `apps/plans`. |
| Consentimiento | Nueva finalidad `whatsapp_notices` en `apps/health/models.py` (`PURPOSE_CHOICES`), con su texto versionado. **Las pantallas y reglas de salud no la ven** (sección 4.1) | Reutiliza el registro versionado ya aprobado (Ley 1581) sin mezclarlo con los datos de salud. |
| Número | Modelo propio `WhatsAppContact` con el número normalizado E.164; `User.phone` no se toca | `User.phone` es texto libre de contacto; el número de avisos debe estar confirmado y normalizado. |
| Contenido | La cola guarda **qué** aviso y **sobre qué objeto** (`kind`, `subject_type`, `subject_id`), nunca el texto. Los parámetros (nombre, fecha) se arman **al enviar** desde los datos vigentes | Minimización y corrección: si la membresía se renovó, el aviso se cancela en vez de mandar una fecha vieja. |
| Cola | Tabla `WhatsAppMessage` + reclamo atómico (`UPDATE ... WHERE status='queued'`) + recuperación de envíos colgados | Mismo patrón probado de `PlanGenerationJob` / `jobs.py`, sin Celery ni Redis. |
| Ejecución | Servicio **cron de Railway** cada 15 min en horario + intento inmediato en hilo para avisos por acción | Con App Sleeping, un hilo dentro de la web no corre si nadie visita la página; el cron sí (pregunta 8). |
| Webhook | Vista `whatsapp_webhook` `@csrf_exempt`, firma HMAC antes de leer el JSON, procesamiento corto en la misma petición | Igual que `payment_webhook` de Wompi. |
| Cliente HTTP | `urllib` (biblioteca estándar), token en la cabecera `Authorization: Bearer`, nunca en la URL | Sin dependencias nuevas; mismo criterio que Gemini. |
| Admin de Django | **No** se registran los modelos de WhatsApp | El admin se salta la regla de asignación; la superusuaria ve el estado con `check_whatsapp`. |
| Idioma | Plantillas en `es_CO` ("Spanish (COL)", existe en la lista oficial) | Coincide con la audiencia. Configurable (`WHATSAPP_CLOUD_TEMPLATE_LANG`). |

---

## 2.1 Rutas nuevas

| Método | Ruta | Nombre | Quién | Qué hace |
| --- | --- | --- | --- | --- |
| GET | `/accounts/whatsapp/` | `whatsapp_settings` | cualquier usuaria con sesión (clienta o entrenador), **sin exigir membresía vigente** ni `HEALTH_FEATURES_ENABLED` | estado de sus avisos, número normalizado, texto vigente del consentimiento, últimos 10 avisos (tipo, fecha, estado) |
| POST | `/accounts/whatsapp/enable/` | `whatsapp_enable` | idem | otorga el consentimiento (`accept=on`, `text_version`), crea/actualiza `WhatsAppContact` en `pending` y encola `optin_confirm` |
| POST | `/accounts/whatsapp/disable/` | `whatsapp_disable` | idem | retira el consentimiento, pasa el contacto a `opted_out` y cancela su cola |
| POST | `/accounts/whatsapp/resend/` | `whatsapp_resend_confirmation` | idem | reenvía `optin_confirm` (máximo 1 cada 10 minutos y 3 por día) |
| GET, POST | `/whatsapp/webhook/` | `whatsapp_webhook` | Meta (sin sesión) | sección 6 |

- Siempre trabajan sobre `request.user`: ninguna recibe el id de otra persona. Nadie (ni la superusuaria) activa o desactiva los avisos de otra persona.
- Con `WHATSAPP_CLOUD_MODE=off` todas responden **404** (y los enlaces del menú no se muestran).
- Retirar los avisos **no** exige membresía vigente (derecho de la titular). Activarlos tampoco: el aviso "membresía vencida" sirve justamente a quien ya no la tiene.
- Las respuestas de `/accounts/whatsapp/` llevan `Cache-Control: no-store` (muestran el teléfono).

---

## 3. Plantillas de mensaje

### 3.1 Reglas de contenido (obligatorias)

1. **Cero datos de salud**: ni medidas, ni ficha, ni banderas, ni diagnósticos, ni el contenido del plan, ni el **tipo** de plan (decir "plan de alimentación" ya revela algo). Solo "hay algo" y dónde verlo, como en `apps/plans/notifications.py`.
2. **Sin intención promocional**: según la guía de Meta, una plantilla *utility* "no debe promocionar, recomendar, vender más ni hacer venta cruzada; incluir ofertas; ni intentar asegurar renovaciones". Por eso el aviso de vencimiento **no** dice "renueva" ni trae descuentos (pregunta 4). Si Meta la recategoriza como marketing, cuesta más y puede castigar a toda la cuenta.
3. Parámetros con nombre (`{{nombre}}`, `{{fecha}}`: minúsculas y guion bajo, con ejemplo obligatorio al crearlas en Meta). Solo se usan **dos** datos personales: el primer nombre y una fecha.
4. `{{nombre}}` = `first_name` limpio: sin saltos de línea ni tabulaciones, espacios colapsados, máximo 30 caracteres; si queda vacío, se usa una palabra de reemplazo (ver 3.3).
5. `{{fecha}}` en español, formada por Python con nombres de mes propios (no depende del locale del servidor): "15 de diciembre de 2026".
6. Los botones de URL apuntan a una **URL fija** del sitio (sin parámetros ni ids). El dominio definitivo se fija al crear la plantilla en Meta (pregunta 14).
7. Cada plantilla lleva el pie "Responde BAJA para no recibir más avisos." (≤ 60 caracteres) y, salvo `optin_confirm`, un botón de respuesta rápida **"No recibir avisos"** (carga `optout`).
8. Nombre de plantilla con sufijo de versión (`_v1`): cambiar el texto = crear `_v2`, aprobarla en Meta y cambiar la constante en `templates_catalog.py`.

### 3.2 Texto propuesto (categoría Utility, idioma Spanish (COL) `es_CO`)

| Código | Nombre en Meta | Cuerpo | Botones |
| --- | --- | --- | --- |
| `optin_confirm` | `profit_confirmar_numero_v1` | "ProFit Studio: alguien registró este número para recibir avisos de su cuenta (vencimiento de la membresía y planes listos para ver). ¿Eres tú y quieres recibirlos?" (sin variables: si el número está mal, un desconocido no ve ningún nombre) | Respuesta rápida "Sí, soy yo" (`confirm`) · "No soy yo" (`not_me`). Pie: "Si no pediste esto, ignora este mensaje." |
| `membership_expiring` | `profit_membresia_por_vencer_v1` | "Hola, {{nombre}}. Te recordamos que tu membresía de ProFit Studio vence el {{fecha}}. Puedes ver su estado al iniciar sesión en tu cuenta." | URL "Ver mi cuenta" · respuesta rápida "No recibir avisos" |
| `membership_expired` | `profit_membresia_vencida_v1` | "Hola, {{nombre}}. Tu membresía de ProFit Studio venció el {{fecha}}. Puedes ver el estado de tu cuenta al iniciar sesión." | URL "Ver mi cuenta" · "No recibir avisos" |
| `plan_approved` | `profit_plan_aprobado_v1` | "Hola, {{nombre}}. Tu entrenador revisó y aprobó tu plan en ProFit Studio. Puedes verlo al iniciar sesión, en la sección Mi plan." | URL "Ver mi plan" · "No recibir avisos" |
| `trainer_review` | `profit_pendientes_revision_v1` | "Hola, {{nombre}}. Tienes elementos nuevos en Planes por revisar, en el panel de ProFit Studio." (sin nombre de la clienta ni motivo: igual que el correo) | URL "Abrir el panel" · "No recibir avisos" |
| `payment_approved` (opcional) | `profit_pago_recibido_v1` | "Hola, {{nombre}}. Recibimos tu pago en ProFit Studio. Tu membresía está activa hasta el {{fecha}}." | URL "Ver mi cuenta" · "No recibir avisos" |

Mensajes de servicio (texto libre, solo dentro de la ventana de 24 h que abre la propia persona al escribir; no necesitan plantilla):

- `optout_ack`: "Listo. No te enviaremos más avisos de ProFit Studio por WhatsApp. Puedes activarlos de nuevo desde tu cuenta."
- `autoreply`: "Este número solo envía avisos automáticos de ProFit Studio y no lee respuestas. Para hablar con nosotros escríbenos a {WHATSAPP_LINK}. Para dejar de recibir avisos responde BAJA." (el enlace sale de la variable existente `WHATSAPP_LINK`).
- Respuesta a "Sí, soy yo": "Gracias. Desde ahora recibirás aquí los avisos de tu cuenta de ProFit Studio."
- Respuesta a "No soy yo": "Gracias por avisar. No volveremos a escribir a este número."

### 3.3 Detalle de `{{nombre}}` vacío

Si `first_name` queda vacío tras limpiarlo, se envía `"clienta"` (o `"equipo"` para `trainer_review`). Así cada plantilla siempre recibe el mismo número de parámetros y no aparece el error `132000`.

---

## 4. Consentimiento, alta, baja y número

### 4.1 Finalidad nueva en el consentimiento versionado

- `apps/health/models.py`: `PURPOSE_WHATSAPP = 'whatsapp_notices'` en `PURPOSE_CHOICES` (etiqueta "Avisos de mi cuenta por WhatsApp"). Cabe en `max_length=20`.
- **Cambio necesario para no romper salud** (verificado en el código): hoy `purposes_with_placeholders()` y `consent_cards()` recorren **todas** las finalidades de `PURPOSES`, y `member_consent` acepta cualquier finalidad válida. Si solo se agrega la nueva:
  - un texto de WhatsApp con marcadores `[...]` haría que `ai_texts_ready()` devuelva falso y **apagaría los planes con IA**;
  - la tarjeta de consentimientos de la ficha (F-05) mostraría WhatsApp;
  - `/health/consent/whatsapp_notices/` quedaría abierta detrás de `HEALTH_FEATURES_ENABLED` y de la membresía.
  Por eso se define `HEALTH_PURPOSES = (PURPOSE_HEALTH_DATA, PURPOSE_AI)` y lo usan `is_valid_purpose`, `consent_cards`, `purposes_with_placeholders` y `export_data`. `PURPOSES` sigue incluyendo todas (para el modelo). Pruebas de regresión en la sección 12.
- Migración de datos `health` 00XX: texto v1 de `whatsapp_notices` como **borrador con marcadores** (`[RAZÓN SOCIAL]`, `[NIT]`, `[CORREO DE DERECHOS]`). En producción, `whatsapp_enable` no se ofrece mientras el texto vigente tenga marcadores (misma idea que `ai_texts_ready`, función propia `whatsapp_text_ready()`).
- Contenido mínimo del texto **[A VALIDAR por abogado]**: responsable (razón social, NIT), finalidad (avisos de la cuenta: membresía, planes listos, pagos si se aprueba), qué datos (celular y primer nombre), que el proveedor es Meta (WhatsApp) y **los datos se transmiten y guardan fuera de Colombia**, que es voluntario, cómo darse de baja (web, botón, BAJA), canal para ejercer derechos, y la declaración "soy mayor de 18 años".
- Casilla **sin marcar**, obligatoria para activar; separada de cualquier otra autorización. Se guarda versión, fecha y usuaria (sin IP), como hoy.

### 4.2 Alta (doble confirmación)

1. La usuaria abre `/accounts/whatsapp/`, ve su número (precargado desde `User.phone` normalizado, editable ahí mismo) y el texto vigente; marca la casilla y pulsa "Activar avisos".
2. El servidor valida el número (4.4), registra el consentimiento, crea o actualiza `WhatsAppContact` en `pending` y encola `optin_confirm`.
3. Ella recibe la plantilla y pulsa **"Sí, soy yo"**: el webhook pasa el contacto a `active` (`confirmed_at`) y se le responde con el mensaje de servicio de gracias.
4. **Hasta confirmar no sale ningún otro aviso.** Si pulsa "No soy yo": contacto `wrong_number`, se cancela su cola y en la web verá "Ese número no confirmó los avisos; revisa tu número".
5. Sin respuesta en 7 días: el contacto sigue `pending` y la web ofrece "Reenviar confirmación".

Por qué la doble confirmación: evita mandar el nombre y el estado de la membresía a un desconocido por un número mal escrito (principio de seguridad y circulación restringida, Ley 1581) y es la mejor prueba de que la persona quiere los mensajes (Meta exige permiso y vigila bloqueos y reportes).

### 4.3 Baja (opt-out)

Cualquiera de estas vías produce exactamente el mismo efecto: `revoke_consent(user, 'whatsapp_notices')`, contacto `opted_out` (`opted_out_at`), todos sus mensajes `queued` pasan a `cancelled`, y se encola **un** `optout_ack` (solo si llegó por WhatsApp).

| Vía | Cómo se detecta |
| --- | --- |
| Web | POST a `whatsapp_disable` |
| Botón "No recibir avisos" | mensaje entrante tipo `button` con carga `optout`; la persona se identifica por `context.id` (el `wamid` de nuestro mensaje) → `WhatsAppMessage` → usuaria. Si no hay `context`, por teléfono o BSUID |
| Texto | mensaje entrante tipo `text` cuyo texto **completo**, normalizado (minúsculas, sin tildes, sin signos, espacios colapsados, máximo 20 caracteres), es una de: `baja`, `stop`, `parar`, `detener`, `cancelar`, `no mas`, `salir`, `desuscribir`, `no quiero mas avisos`. Coincidencia exacta, para no dar de baja a quien escribe "no puedo parar de entrenar" |

- Volver a activar: solo desde la web (tiene que ver y aceptar el texto vigente). Si escribe "alta" o "start", recibe la `autoreply` con el enlace.
- El texto del mensaje entrante se usa en memoria para clasificarlo y **se descarta**: no se guarda ni se registra en logs.

### 4.4 Verificación y normalización del número

Entrada: lo que escriba la usuaria (o `User.phone`, que hoy acepta `+`, dígitos, espacios, paréntesis y guiones, 7 a 15 dígitos). Salida: E.164 con `+`.

1. Quitar espacios, guiones, puntos y paréntesis. `00` inicial = `+`.
2. 10 dígitos que empiezan por `3` → `+57` + dígitos (celular colombiano).
3. 12 dígitos que empiezan por `573` → `+` + dígitos.
4. Empieza por `+57`: debe ser `+57 3` + 9 dígitos (los fijos `60X` no reciben WhatsApp de forma fiable).
5. Fase 1 (pregunta 6): **solo celulares colombianos**. Cualquier otro → error "Escribe tu celular colombiano, por ejemplo 300 123 4567."
6. Meta devuelve en `contacts[].wa_id` el identificador real del usuario; se guarda en `WhatsAppContact.wa_id` y es el que se usa para reconocer mensajes entrantes.
7. Un mismo número no puede estar `pending` o `active` en dos cuentas (restricción parcial única). Si ya está, error genérico "Ese número ya está registrado para avisos en otra cuenta." (no se dice cuál).
8. Si la usuaria cambia `User.phone` en su perfil, **no** cambia el número de avisos; el número de avisos solo se cambia en `/accounts/whatsapp/` y vuelve a `pending` (nueva confirmación).

### 4.5 Nombres de usuario de WhatsApp (BSUID)

Meta está introduciendo nombres de usuario: cuando una persona activa uno, su teléfono puede **no venir** en los webhooks (llega `user_id`, el BSUID, con formato `CO.<alfanumérico>`), salvo que haya habido contacto con ese número en los últimos 30 días o esté en la libreta. Consecuencias para este diseño:

- Los envíos siguen yendo **al teléfono** (`to`), que la documentación mantiene soportado y recomienda.
- Se guarda `WhatsAppContact.bsuid` cuando llegue (`user_id` / `recipient_user_id`), completo y sin modificarlo.
- Para identificar a quien responde se usa, en orden: `context.id` (respuesta a un mensaje nuestro), `wa_id`/`from`, `user_id`. Los campos de teléfono del webhook se tratan como opcionales.
- Si cambia el teléfono de la persona, el BSUID cambia (llega `previous_user_id` en un mensaje de sistema): se actualiza si coincide con el guardado.

---

## 5. Modelo de datos (app `whatsapp`, migración 0001)

Solo ids, códigos y fechas. **Ningún modelo guarda el texto de un mensaje, ni el cuerpo de un webhook, ni datos de salud.** El único dato personal es el teléfono (en `WhatsAppContact`).

### 5.1 `WhatsAppContact` (uno por usuaria)

| Campo | Tipo | Nota |
| --- | --- | --- |
| `user` | OneToOne `User`, CASCADE | |
| `phone_e164` | CharField(16) | `+573001234567` |
| `wa_id` | CharField(20), vacío | lo que devuelve Meta |
| `bsuid` | CharField(160), vacío | sección 4.5 |
| `status` | `pending` / `active` / `wrong_number` / `undeliverable` / `opted_out` | solo `active` recibe avisos (y `pending` solo `optin_confirm`) |
| `consent` | FK `health.ConsentRecord`, SET_NULL | autorización con la que se activó |
| `confirmation_sent_at`, `confirmed_at`, `opted_out_at` | datetime, null | |
| `last_inbound_at` | datetime, null | para saber si la ventana de 24 h está abierta (mensajes de servicio) |
| `last_autoreply_at` | datetime, null | máximo una `autoreply` cada 24 h |
| `undeliverable_count` | entero | fallos `131026` seguidos; al llegar a 2 → `undeliverable` |
| `updated_at` | datetime | |

Restricción: único `phone_e164` con `status in (pending, active)`.

**Para poder enviar** se exigen las tres cosas a la vez, en el momento del envío: contacto `active`, consentimiento `whatsapp_notices` vigente (no retirado y sobre el texto vigente: si se publica una versión nueva del texto, hay que volver a aceptar) y usuaria `is_active`. Si falta algo, el mensaje pasa a `cancelled` con `error_code` (`no_consent`, `contact_inactive`, `user_inactive`).

### 5.2 `WhatsAppMessage` (la cola y el registro de entrega)

| Campo | Tipo | Nota |
| --- | --- | --- |
| `ref` | UUID único | se envía a Meta como `biz_opaque_callback_data` y vuelve en los webhooks de estado (reconciliación, 7.4) |
| `recipient` | FK `User`, CASCADE | |
| `kind` | código de 1.1 | |
| `template_name` | CharField(80), vacío | vacío en mensajes de servicio |
| `subject_type`, `subject_id` | `membership` / `plan` / `payment` / `contact` / `none`, entero null | sobre qué objeto es el aviso; los parámetros se recalculan al enviar |
| `dedupe_key` | CharField(120), **único** | idempotencia (5.4) |
| `status` | `queued` / `sending` / `accepted` / `sent` / `delivered` / `read` / `failed` / `cancelled` / `expired` | |
| `attempts`, `max_attempts` | entero (por defecto 5) | |
| `next_attempt_at`, `locked_at`, `expires_at` | datetime | `expires_at`: después ya no tiene sentido enviarlo (p. ej. "por vencer" después de `end_date`) |
| `wamid` | CharField(128), único, null | id de Meta |
| `error_code` | CharField(40), vacío | `meta_131026`, `http_503`, `timeout`, `no_consent`, `outside_window`… nunca el texto del error |
| `created_at`, `accepted_at`, `sent_at`, `delivered_at`, `read_at`, `failed_at` | datetime | |
| `pricing_category`, `pricing_type` | CharField(20), vacío | `utility` / `service`…, `regular` / `free_customer_service`… (para el control de costos) |

Índices: (`status`, `next_attempt_at`), (`recipient`, `created_at`).

### 5.3 `WhatsAppInbound` (respuestas recibidas, sin contenido)

| Campo | Tipo |
| --- | --- |
| `wamid` | CharField(128), **único** (idempotencia de reintentos de Meta) |
| `contact` | FK `WhatsAppContact`, SET_NULL |
| `kind` | `confirm` / `not_me` / `optout` / `other` / `unsupported` |
| `in_reply_to` | FK `WhatsAppMessage`, SET_NULL (por `context.id`) |
| `received_at` | datetime |

Los mensajes de números que no corresponden a ninguna cuenta **no se guardan** ni se responden (solo un contador en el log).

### 5.4 `WhatsAppChannelState` (una sola fila)

`paused` (bool), `paused_reason` (código), `paused_at`, `paused_templates` (lista de nombres de plantilla pausados), `last_alert_at`. Compartido entre procesos (por eso en la base y no en caché: `LocMemCache` es por proceso).

### 5.5 Claves de idempotencia

| Aviso | `dedupe_key` |
| --- | --- |
| `optin_confirm` | `optin:{contact_id}:{phone_e164 sha256[:12]}:{n}` (`n` = número de reenvío; tope en 2.1) |
| `membership_expiring` | `mexp:{membership_id}:{end_date}` |
| `membership_expired` | `mend:{membership_id}:{end_date}` |
| `plan_approved` | `plan:{plan_id}` |
| `trainer_review` | `review:{trainer_id}:{fecha}:{bloque de 3 h}` |
| `payment_approved` | `pay:{payment_id}` |
| `optout_ack` / `autoreply` | `ack:{inbound wamid}` / `auto:{contact_id}:{fecha}` |

Encolar es `get_or_create` por `dedupe_key` (con captura de `IntegrityError` en carrera): correr el cron dos veces, un doble clic o el reintento de un webhook nunca duplican un aviso.

### 5.6 Retención

- `WhatsAppMessage`: se borra a los 180 días; `WhatsAppInbound`: a los 90 días (pregunta 16). Lo hace el mismo cron una vez al día.
- Al borrar la cuenta, todo se va por CASCADE.

---

## 6. Webhook de Meta (`/whatsapp/webhook/`)

### 6.1 Verificación (GET)

- Parámetros: `hub.mode=subscribe`, `hub.verify_token`, `hub.challenge`.
- Si `hub.mode == 'subscribe'` y `hmac.compare_digest(hub.verify_token, WHATSAPP_CLOUD_VERIFY_TOKEN)`: responde **200** con el `hub.challenge` como `text/plain`. Defensa extra: el challenge solo se devuelve si tiene como máximo 128 caracteres alfanuméricos (si no, 400), para no reflejar contenido arbitrario.
- Token incorrecto → 403. Parámetros faltantes → 400. `WHATSAPP_CLOUD_VERIFY_TOKEN` vacío → 503.

### 6.2 Eventos (POST): orden de las comprobaciones

| Paso | Comprobación | Respuesta si falla |
| --- | --- | --- |
| 1 | `WHATSAPP_CLOUD_APP_SECRET` configurado | 503 (Meta reintenta; tiene sentido cuando se configure) |
| 2 | Tamaño: `Content-Length` ≤ 3,5 MB (Meta documenta cargas de hasta 3 MB). Se lee el cuerpo crudo con ese tope; ojo: `DATA_UPLOAD_MAX_MEMORY_SIZE` (2,5 MB por defecto) lanza `RequestDataTooBig` al usar `request.body`, así que la vista debe leer el flujo con su propio tope | 413 |
| 3 | Cabecera `X-Hub-Signature-256` = `sha256=<hex>`; se calcula `HMAC-SHA256(app_secret, cuerpo_crudo_en_bytes)` y se compara con `hmac.compare_digest`. **Sobre los bytes recibidos, antes de decodificar o parsear el JSON** | 401 si falta o no coincide |
| 4 | JSON válido y objeto con `object == 'whatsapp_business_account'` | 200 + log de advertencia (un reintento no lo arreglaría) |
| 5 | Por cada `entry`: `entry.id == WHATSAPP_CLOUD_WABA_ID` y `value.metadata.phone_number_id == WHATSAPP_CLOUD_PHONE_NUMBER_ID`; si no, se ignora esa entrada | 200 |
| 6 | Procesar `statuses[]`, `messages[]` y `errors[]` (6.3) en una transacción | 500 si falla la base (Meta reintenta; todo es idempotente) |
| 7 | Responder | **200** |

- `@csrf_exempt` **solo** en esta vista; `require_http_methods(['GET', 'POST'])`; sin sesión ni login.
- Respuesta rápida: el webhook solo escribe filas (unas pocas consultas). **Nunca** llama a la API de Meta: las respuestas (`optout_ack`, gracias, `autoreply`) se encolan y salen por el hilo/cron.
- Meta agrupa hasta 1000 actualizaciones por petición y reintenta durante **7 días** si no recibe 200, así que puede repetir eventos: todo el procesamiento es idempotente.
- Con App Sleeping, la primera petición a la web dormida puede responder 502; Meta reintenta y no se pierde nada.
- Con `WHATSAPP_CLOUD_MODE=off` la ruta responde 404.
- Nunca se registra el cuerpo, las cabeceras ni la firma. Log: cantidad de estados y mensajes procesados, códigos de error y tiempo.

### 6.3 Qué se hace con cada parte del webhook

**`statuses[]`** (estado de lo que enviamos): se busca el `WhatsAppMessage` por `wamid` (o por `biz_opaque_callback_data` = `ref` si todavía no tenía `wamid`). Desconocido → se ignora.

- Solo avanza: `accepted` → `sent` → `delivered` → `read`. Un `delivered` que llega después de `read` no retrocede. `failed` se aplica si el estado actual es `accepted` o `sent`.
- Se guardan las fechas (`timestamp`), `pricing.category` y `pricing.type`, y `recipient_user_id` en `WhatsAppContact.bsuid`.
- `failed` con `errors[].code` → se aplica la tabla de 7.3 (se guarda solo `meta_<code>`).

**`messages[]`** (lo que nos escriben): si `wamid` ya existe en `WhatsAppInbound` → nada (duplicado). Si no: identificar a la persona (4.5), guardar `last_inbound_at` y clasificar:

| Tipo entrante | Acción |
| --- | --- |
| `button` / `interactive.button_reply` con carga `confirm` | contacto `active` + mensaje de gracias |
| carga `not_me` | contacto `wrong_number`, cola cancelada, mensaje de gracias |
| carga `optout` o texto de baja (4.3) | baja completa + `optout_ack` |
| otro texto, audio, imagen… | `autoreply` (máximo una cada 24 h); el contenido se descarta |
| persona desconocida | nada |

**`errors[]`** del nivel de la cuenta: se registran solo los códigos; los de cuenta o token (7.3) pausan el canal.

Suscripción en Meta: campo `messages` (estados y mensajes entrantes). Opcional: `message_template_status_update` para pausar sola una plantilla rechazada o pausada; en la fase 1 eso lo revisa `check_whatsapp`.

---

## 7. Envío y cola en Railway

### 7.1 Cómo se procesa

1. **Encolar** (`services.enqueue_notice(kind, recipient, subject)`): comprueba modo, consentimiento y contacto; si no aplica, no crea nada (no se guardan filas de quien no activó los avisos). Si aplica, crea la fila por `dedupe_key`.
2. **Avisos por acción** (`plan_approved`, `trainer_review`, `payment_approved`, `optin_confirm`, `optout_ack`, `autoreply`): en `transaction.on_commit` se lanza un hilo daemon que intenta enviarlo de inmediato (como `jobs.start_job`). Si está fuera de la ventana horaria (7.5), queda en cola con `next_attempt_at` = próxima apertura.
3. **Cron de Railway** (servicio aparte del mismo repositorio, comando `python manage.py process_whatsapp_queue`):
   1. recupera envíos colgados (7.4);
   2. si el canal está pausado, termina con código de salida 1 (así Railway muestra el fallo);
   3. encola los avisos de membresía del día (idempotente: correrlo 40 veces al día no duplica nada);
   4. envía lo vencido (`queued` y `next_attempt_at <= ahora`), máximo 50 por corrida, con 1 s entre mensajes;
   5. una vez al día, borra lo antiguo (5.6);
   6. termina en menos de 4 minutos y cierra la conexión a la base (Railway salta una corrida si la anterior sigue viva).
4. **Horario del cron**: Railway usa UTC y exige al menos 5 minutos entre corridas. Bogotá es UTC-5 sin horario de verano, así que `*/15 12-23 * * 1-6` cubre lunes a sábado de 7:00 a 18:45 en Bogotá. La regla legal la decide **el código** (7.5), no el cron: el cron solo evita corridas inútiles.
5. **Reclamo atómico**: `UPDATE ... SET status='sending', locked_at=ahora WHERE id=… AND status='queued'`; si el hilo y el cron compiten por el mismo mensaje, solo uno gana.
6. **Tope diario global** `WHATSAPP_CLOUD_DAILY_LIMIT` (por defecto 200, por debajo del límite inicial de Meta de 250 personas distintas en 24 h para portafolios nuevos). Al llegar, lo demás espera al día siguiente y se avisa en el log.

Por qué no un hilo solo dentro de la web: con App Sleeping, si nadie visita la página, ningún hilo corre y los avisos de membresía no salen. Por qué no Celery/Redis: más infraestructura a operar y pagar para muy poco volumen. Alternativa (pregunta 8): un programador externo gratuito que llame a una ruta protegida.

### 7.2 Petición a Meta

- `POST https://graph.facebook.com/{WHATSAPP_CLOUD_GRAPH_VERSION}/{WHATSAPP_CLOUD_PHONE_NUMBER_ID}/messages` (la documentación usa hoy `v27.0` en sus ejemplos).
- Cabeceras: `Authorization: Bearer <token>`, `Content-Type: application/json`. El token **nunca** en la URL (las URL terminan en logs y excepciones).
- Cuerpo de plantilla: `messaging_product: "whatsapp"`, `recipient_type: "individual"`, `to: <phone_e164>`, `type: "template"`, `template: {name, language: {code: "es_CO"}, components: [body con parámetros con nombre, botones de respuesta rápida con su carga]}`, `biz_opaque_callback_data: <ref>`. **El implementador debe reconfirmar** la forma exacta de `components` para parámetros con nombre (`parameter_name`) en la referencia vigente antes de codificar.
- Respuesta 200: `contacts[].wa_id` y `messages[].id` (`wamid`) → estado `accepted`. Meta aclara que esto **no** significa entregado: la entrega llega por webhook.
- Tiempo de espera: `WHATSAPP_CLOUD_TIMEOUT_SECONDS` (10 s).
- Interfaz: `WhatsAppClient.send_template(...)` y `send_text(...)` → `SendResult(wamid, wa_id)`; errores `WATransientError`, `WARecipientError`, `WATemplateError`, `WAAccountError`, con el código de Meta como dato. Se obtiene con `get_whatsapp_client()`; las pruebas lo reemplazan.

### 7.3 Errores de Meta: qué se hace con cada uno

Los códigos llegan igual en la respuesta HTTP del envío y en `statuses[].errors[]` del webhook; una sola tabla decide. En el mensaje solo se guarda `meta_<código>`.

| Clase | Códigos (documentación oficial) | Acción |
| --- | --- | --- |
| Transitorio | HTTP 5xx, tiempo agotado, error de conexión, `131000` (error desconocido), `131016` (servicio no disponible), `130429` (límite de velocidad de la API), `131056` (demasiados mensajes a la misma persona en poco tiempo) | reintentar con espera (7.4); respetar `Retry-After` si viene |
| Ecosistema | `131049` ("no entregado para mantener un ecosistema sano") | un solo reintento después de 24 h; si vuelve a fallar, `failed` |
| Destinatario | `131026` (no se pudo entregar: sin WhatsApp, versión vieja, no aceptó términos), `131021` (es el mismo número que envía), `131050` (la persona bloqueó mensajes de marketing) | sin reintento; `131026` dos veces seguidas → contacto `undeliverable` (la web le dice "no pudimos entregarte avisos; revisa tu número"); `131021` → `wrong_number`; `131050` → `failed` |
| Fuera de ventana | `131047` (pasaron más de 24 h desde su último mensaje) | solo afecta a mensajes de servicio: se descarta (`expired`); las plantillas no tienen esta restricción |
| Plantilla o parámetros | `132000` (número de parámetros), `132001` (plantilla inexistente o no aprobada en ese idioma), `132005` (texto traducido muy largo), `132007` (viola políticas), `132012` (formato de parámetros), `132015` (pausada por baja calidad), `132016` (deshabilitada), `100`, `131008`, `131009`, `135000` (parámetros) | sin reintento; **pausa esa plantilla** (`paused_templates`) y alerta a la superusuaria; los demás avisos siguen |
| Cuenta, token o permisos | `0`, `190` (token vencido), `3`, `10`, `200`-`299`, `131005` (permisos), `368` y `131031` (cuenta restringida por políticas), `131042` (problema con el medio de pago), `131045` y `133010` (número no registrado), `131048` (límite por calidad), `131057` (mantenimiento) | **pausa todo el canal** (`WhatsAppChannelState.paused`), la cola se conserva, alerta a la superusuaria; se reanuda con `python manage.py check_whatsapp --resume` cuando `check_whatsapp` pasa |
| Desconocido | cualquier otro | `failed` sin reintento; aparece en `check_whatsapp` |

Alertas: correo a las superusuarias (mismo `_send` que `notifications.py`), máximo una cada 24 h por motivo, con solo el código: "Los avisos por WhatsApp están pausados (código meta_190: token vencido). Revisa la guía de la sección 13." Ningún error de WhatsApp se muestra a la clienta con el texto de Meta.

### 7.4 Reintentos, recuperación y no duplicar

- Espera: 1 min, 5 min, 30 min, 2 h (con variación aleatoria de ±20 %), hasta `max_attempts` = 5; nunca más allá de `expires_at` (entonces `expired`).
- **Envío colgado** (`sending` con `locked_at` de más de 10 minutos: el proceso murió a mitad de la llamada): no se sabe si Meta lo aceptó. Se espera hasta 30 minutos más a que llegue un webhook con su `ref` (`biz_opaque_callback_data`); si llega, se reconcilia (se guarda `wamid` y estado). Si no llega ninguno, se vuelve a `queued` (Meta envía al menos un estado `sent` o `failed` por cada mensaje aceptado). Así se prefiere no duplicar sin perder avisos.
- Antes de cada envío se vuelve a comprobar todo (consentimiento, contacto, ventana horaria, que el aviso siga teniendo sentido).

### 7.5 Ventana horaria (Ley 2300 de 2023) **[A VALIDAR por abogado]**

- Propuesta conservadora para **todos** los avisos (pregunta 9): lunes a viernes de 7:00 a 19:00 y sábados de 8:00 a 15:00, hora de Bogotá; **nunca domingos ni festivos**.
- Las respuestas a lo que la persona escribe (`optout_ack`, gracias, `autoreply`) se envían en cualquier momento: no son contactos iniciados por el negocio.
- Festivos de Colombia: cálculo propio en `apps/whatsapp/calendar.py` (Python puro, sin dependencias: fechas fijas, Ley 51 de 1983 "Ley Emiliani" y las que dependen de la Pascua), con pruebas contra la lista oficial de 2026 y 2027.
- Fuera de ventana: `next_attempt_at` = próxima apertura.

---

## 8. Variables de entorno (nombres, sin valores)

| Variable | Obligatoria en `live` | Uso |
| --- | --- | --- |
| `WHATSAPP_CLOUD_MODE` | — | `off` (por defecto en producción), `simulated` (por defecto con `DEBUG`), `live`. Otro valor → `production.py` no arranca |
| `WHATSAPP_CLOUD_ACCESS_TOKEN` | sí | token de usuario del sistema (System User) de Meta Business |
| `WHATSAPP_CLOUD_PHONE_NUMBER_ID` | sí | id del número de envío (no es el teléfono) |
| `WHATSAPP_CLOUD_WABA_ID` | sí | id de la cuenta de WhatsApp Business (la documentación nueva también la llama "Messaging account"); se usa para comprobar el webhook y para `check_whatsapp` |
| `WHATSAPP_CLOUD_APP_SECRET` | sí | secreto de la app de Meta; valida la firma del webhook |
| `WHATSAPP_CLOUD_VERIFY_TOKEN` | sí | cadena aleatoria propia de 32 caracteres o más (se genera con un gestor de contraseñas); `production.py` rechaza una más corta |
| `WHATSAPP_CLOUD_GRAPH_VERSION` | sí | por ejemplo `v27.0`; debe cumplir `^v\d+\.\d+$` |
| `WHATSAPP_CLOUD_TEMPLATE_LANG` | no (`es_CO`) | idioma de las plantillas |
| `WHATSAPP_CLOUD_TIMEOUT_SECONDS` | no (10) | |
| `WHATSAPP_CLOUD_DAILY_LIMIT` | no (200) | tope global de envíos por día |
| `WHATSAPP_CLOUD_EXPIRING_DAYS` | no (3) | anticipación del aviso de vencimiento |
| `WHATSAPP_CLOUD_PAYMENT_NOTICE` | no (`false`) | enciende `payment_approved` si se aprueba |
| `WHATSAPP_CLOUD_SEND_INLINE` | no (`false`) | pruebas: envía en el mismo hilo |

- En `live`, `production.py` no arranca si falta cualquiera de las obligatorias (mismo patrón que `AI_PLANS_MODE=live`). El mensaje de error nombra la variable, nunca su valor.
- El servicio cron de Railway necesita las mismas variables que la web (en Railway: variables compartidas o de referencia), además de `SECRET_KEY`, `ALLOWED_HOSTS` y `DATABASE_URL`, porque carga `production.py`.
- `.env.example` (si se agrega) solo con los nombres.

---

## 9. Seguridad (OWASP)

| Riesgo | Medida |
| --- | --- |
| Secretos | Solo en variables de Railway. Nunca en el código, en la base, en el chat ni en capturas. Al pedir capturas del panel de Meta: tapar token, app secret y verify token. Si uno se expone: rotarlo (abajo). |
| Webhook falsificado | Firma HMAC-SHA256 obligatoria sobre el cuerpo crudo, `compare_digest`, comprobación de `WABA_ID` y `phone_number_id`. Sin firma no se toca la base. Meta ofrece mTLS para webhooks y desaconseja filtrar por IP (las cambia); la fase 1 confía en la firma. |
| CSRF | Exento solo `whatsapp_webhook`. Las rutas de `/accounts/whatsapp/` son POST con `{% csrf_token %}`. |
| Inyección | Sin SQL manual (ORM). Ningún texto entrante se guarda, se muestra ni se reenvía. Los parámetros de las plantillas se limpian (3.1). |
| Reflejo de contenido | El `hub.challenge` solo se devuelve si es alfanumérico corto y como `text/plain`. |
| Logs | Nunca: teléfono completo, token, firma, cuerpo del webhook, texto de mensajes, `error_data.details` de Meta (puede traer el teléfono). Solo: id del mensaje, `kind`, estado, intento, `meta_<código>`, latencia. Si hace falta referirse a un número: enmascarado `+57*******67`. Un logger `apps.whatsapp` en `production.py` con nivel INFO, como `apps.plans`. |
| Errores al usuario | Mensajes genéricos en español ("No pudimos activar los avisos. Inténtalo de nuevo más tarde."). Nunca trazas ni texto de Meta. `logger.exception` solo con la clase del error. |
| Quién dispara envíos | **Nadie a mano.** Solo los eventos del sistema (1.1) y el cron. No hay ruta para escribir un texto libre que salga por la API. Cada persona activa o retira solo sus propios avisos. |
| Abuso de las rutas de la web | `whatsapp_resend_confirmation` con tope en la base (1 cada 10 min, 3 por día). Activar con un número ajeno no le sirve a nadie: el dueño del número debe pulsar "Sí, soy yo". |
| Rotación del token | Recomendado: token con fecha de vencimiento (pregunta 13). Procedimiento: Meta Business > Configuración del negocio > Usuarios del sistema > generar token nuevo con los mismos permisos → pegarlo en Railway > Variables → esperar el redespliegue → `check_whatsapp` → revocar el anterior. Con `190`, el canal se pausa solo y llega la alerta. |
| Mínimos permisos | El usuario del sistema solo con `whatsapp_business_messaging`, `whatsapp_business_management` y `business_management` (lo que pide Meta) y acceso únicamente a esta app y esta cuenta de WhatsApp. |
| Datos de salud | Prueba automática: el armador de parámetros solo puede leer `first_name`, `Membership.end_date` y el estado del objeto; nada de `apps.health` ni del contenido de `apps.plans`. |

---

## 10. Cambios en el código existente (puntos de integración)

| Archivo | Cambio |
| --- | --- |
| `apps/health/models.py` y `services.py` | `PURPOSE_WHATSAPP`, `HEALTH_PURPOSES` y su uso en `is_valid_purpose`, `consent_cards`, `purposes_with_placeholders`, `export_data` (4.1). Migración de datos con el texto v1 en borrador. |
| `apps/plans/notifications.py` | Al final de `client_plan_approved` y de las cuatro funciones del entrenador: `whatsapp.services.enqueue_notice(...)` dentro de `try/except` que solo registra la clase del error (nunca interrumpe, igual que el correo). |
| `apps/payments/services.py` (solo si se aprueba `payment_approved`) | Tras activar la membresía en `apply_transaction_event`, encolar en `transaction.on_commit`. Un fallo no cambia la respuesta del webhook de Wompi. |
| `apps/memberships` | Sin cambios de modelo: el cron consulta `Membership` por `end_date`. |
| `config/settings/base.py` / `production.py` | Variables de la sección 8, validación de `live`, logger `apps.whatsapp`. `development.py`: `simulated`. |
| `config/urls.py` | `path('whatsapp/', include('apps.whatsapp.urls'))` y las rutas de `/accounts/whatsapp/` en `apps/accounts/urls.py` o en la app nueva. |
| `check_infra` | Área "WhatsApp": modo, variables presentes (sin valores), canal pausado, plantillas pausadas, mensajes fallidos de las últimas 24 h. |
| Comandos nuevos | `process_whatsapp_queue` (cron), `check_whatsapp` (solo lectura: consulta el número y el estado de las plantillas en Meta, muestra conteos; `--resume` reanuda el canal), `whatsapp_register_number` (paso único de registro del número en la Cloud API; el PIN de dos pasos se pide por consola con `getpass`, no se imprime ni se guarda). |
| `start.sh` / `railway.toml` | Sin cambios en el servicio web. El cron es otro servicio de Railway (sección 13). |

---

## 11. Pantallas (pedido a frontend-architect, cuando se apruebe)

| Id | Pantalla | Plantilla sugerida | Contexto que entregará el backend |
| --- | --- | --- | --- |
| W-01 | Avisos por WhatsApp | `accounts/whatsapp_settings.html` (extiende `base.html`; para el entrenador, `trainer/base.html`) | `status` (`off` / `pending` / `active` / `wrong_number` / `undeliverable` / `opted_out`), `phone_display` (`+57 300 123 4567`), `form`, `errors` (`phone`, `accept`, `general`), `text_version`, `body` (texto del consentimiento), `text_ready` (bool), `can_resend` (bool), `recent` (últimos 10: `kind_label`, `created_at`, `status_label`), `privacy_policy_url`, `is_minor` (bool). Casilla **sin marcar**; botón "Activar avisos" deshabilitado hasta marcarla y mientras envía; botón "Desactivar avisos" con confirmación; "Reenviar confirmación" con espera visible |
| W-02 | Enlace en el menú de la cuenta | `partials/account_menu_items.html` | "Avisos por WhatsApp", solo con `{% if WHATSAPP_NOTICES_ENABLED %}` (nueva variable global de un procesador de contexto) |
| W-03 | Aviso en el panel de la clienta | `accounts/member_dashboard.html` | `whatsapp_status` para mostrar "Confirma tu número en WhatsApp" si está `pending` o "Revisa tu número" si está `wrong_number` / `undeliverable` |

El detalle final del contexto se publicará en `docs/contracts/requests.md` cuando se construya (como se hizo con A1 y A2).

---

## 12. Pruebas previstas (sin llamar nunca a Meta)

- **Normalización del número**: tabla de casos (`300 123 4567`, `+57 300-123-4567`, `0057…`, `573001234567`, fijo `6012345678` → error, extranjero → error en la fase 1, letras → error) y la máscara de logs.
- **Consentimiento**: activar sin casilla no hace nada; texto con marcadores no se ofrece en producción; versión nueva del texto deja de enviar hasta aceptar; solo la propia usuaria activa o retira; menor de edad no puede activar. **Regresión de salud**: con la finalidad nueva, `consent_cards`, `ai_texts_ready`, `export_data` y `/health/consent/whatsapp_notices/` (404) se comportan igual que hoy.
- **Webhook GET**: token correcto (200 + challenge), incorrecto (403), sin parámetros (400), challenge raro (400), verify token vacío (503).
- **Webhook POST**: firma válida, inválida, ausente, cuerpo alterado en un byte (401); secreto vacío (503); cuerpo demasiado grande (413); JSON inválido pero firmado (200); `phone_number_id` ajeno ignorado; el mismo payload dos veces no duplica nada; estados fuera de orden (`read` antes de `delivered`); `failed` con cada clase de la tabla 7.3; `confirm`, `not_me`, botón `optout` por `context.id`, texto "BAJA", " Stop. " y "no puedo parar" (este último **no** da de baja); mensaje sin `wa_id` pero con `user_id`; persona desconocida; CSRF: el webhook está exento y otra vista POST de la app sigue exigiéndolo.
- **Cola**: `dedupe_key` (dos encolados → una fila, también en carrera); reclamo atómico (hilo y cron a la vez → un solo envío); reintentos y esperas con reloj simulado; `Retry-After`; `expires_at`; membresía renovada → `cancelled`; consentimiento retirado → `cancelled`; ventana horaria (domingo, festivo, 19:01, sábado 15:01 → espera; lunes 7:00 → sale); canal pausado por `190`; plantilla pausada por `132001` sin afectar a las demás; envío colgado y reconciliación por `ref`; tope diario.
- **Cliente `live` contra un servidor HTTP falso local** (igual que las pruebas de Gemini): URL con versión y `phone_number_id`, cabecera `Authorization`, el token no aparece en la URL ni en los logs, cuerpo exacto de cada plantilla (nombre, `es_CO`, parámetros con nombre, cargas de los botones, `biz_opaque_callback_data`), tiempo de espera, cada código de error mapeado a su clase.
- **Contenido**: cada plantilla recibe exactamente sus parámetros; ninguna lee datos de `apps.health` ni contenido de `apps.plans`; `assertLogs` sin teléfonos completos, tokens ni textos.
- **Integración**: aprobar un plan encola `plan_approved` y el correo sigue saliendo; si WhatsApp lanza una excepción, la aprobación termina bien; el webhook de Wompi responde igual con o sin `payment_approved`.
- **Configuración**: `production.py` no arranca en `live` sin cada variable, con verify token corto o con versión mal escrita; `off` = rutas 404.
- **Calendario**: festivos de Colombia 2026 y 2027 contra la lista oficial.
- `FakeWhatsAppClient` (en `apps/whatsapp/tests/helpers.py`) registra las llamadas y permite forzar cualquier código de error. En `simulated` (desarrollo), los envíos se registran en el log como "simulado" con un `wamid` falso y nunca salen a la red.
- Contraprueba: quitar la comprobación de firma o la de `dedupe_key` debe hacer fallar las pruebas correspondientes.

---

## 13. Lo que debe hacer la persona fuera del código (y costos)

### 13.1 En Meta (en este orden; nada de esto lo puede hacer el código)

1. **Portafolio de negocio** en Meta Business (business.facebook.com) a nombre de ProFit Studio. **Verificación del negocio** (Configuración del negocio > Centro de seguridad > Verificación; piden documentos como RUT o Cámara de Comercio): no es obligatoria para empezar, pero sin ella el límite es 250 personas distintas por día y 2 números; con ella sube a 2.000 y luego automáticamente.
2. **Número de envío**: propio, con indicativo, que reciba SMS o llamada y que **no** esté registrado en WhatsApp (o que se borre primero). Requiere nombre visible ("ProFit Studio"), que Meta aprueba, y un **PIN de dos pasos** que se guarda en un gestor de contraseñas. Ver pregunta 1 sobre el número actual +57 300 563 8196.
3. **App de desarrollador** (developers.facebook.com > Mis apps > Crear app, tipo negocio) con el producto WhatsApp, enlazada al portafolio. Pasarla a modo **Live** (algunos webhooks no se envían en modo desarrollo); el panel pide una URL de política de privacidad (hoy el proyecto no tiene una publicada: `PRIVACY_POLICY_URL` está vacía).
4. **Medio de pago** en el Administrador de WhatsApp (sin él aparece el error `131042`). La **moneda** se elige al crear la cuenta y luego no se cambia fácilmente: COP ya está disponible (pregunta 15).
5. **Plantillas**: Administrador de WhatsApp > Plantillas de mensajes > Crear plantilla, categoría **Utilidad**, idioma **Spanish (COL)**, con el texto exacto de 3.2 y un ejemplo por variable. Esperar a que queden **Aprobadas**.
6. **Usuario del sistema y token**: Configuración del negocio > Usuarios > Usuarios del sistema > Agregar (rol administrador o empleado) > Asignar activos (la app y la cuenta de WhatsApp) > Generar token con `business_management`, `whatsapp_business_management` y `whatsapp_business_messaging`. El token se pega **directamente** en Railway > Variables (`WHATSAPP_CLOUD_ACCESS_TOKEN`), nunca en un chat o documento.
7. **Webhook**: en la app, WhatsApp > Configuración > Webhook: URL `https://<dominio>/whatsapp/webhook/`, el mismo verify token que `WHATSAPP_CLOUD_VERIFY_TOKEN`, y suscribirse al campo `messages`.
8. **Registro del número en la Cloud API**: la documentación indica que los paneles no lo registran para la API; se hace una vez con `python manage.py whatsapp_register_number` (pide el PIN por consola).
9. **Railway**: crear un servicio nuevo desde el mismo repositorio, comando de inicio `python manage.py process_whatsapp_queue`, en Settings > Cron Schedule `*/15 12-23 * * 1-6`, con las variables compartidas.
10. Probar primero con `WHATSAPP_CLOUD_MODE=simulated`, luego `live` con el número de una persona del equipo, y correr `python manage.py check_whatsapp`.

### 13.2 Costos aproximados (confirmar en la tarifa oficial de Meta antes de decidir)

- Desde el 1 de julio de 2025 Meta cobra **por mensaje entregado** (lo enviado pero no entregado no se cobra). Las plantillas de utilidad se cobran según la tarifa de Colombia, que es un mercado propio en la tabla de Meta.
- **Cambio reciente**: desde el **1 de octubre de 2026** las plantillas de utilidad enviadas dentro de la ventana de 24 h **también se cobran** (antes eran gratis).
- Los mensajes de servicio (`optout_ack`, `autoreply`, gracias) tienen 1.000 gratis al mes por número.
- No pude leer la cifra oficial de Colombia (la tabla de Meta se carga de forma interactiva o en archivos descargables). Una fuente no oficial mencionaba unos USD 0,0008 por mensaje de utilidad en Colombia en abril de 2026, y Meta marcó una **subida** de esa tarifa en octubre de 2025: **no la tomes como dato**. Orden de magnitud orientativo (estimación mía, no promesa): con 50 clientas y unos 4 avisos al mes cada una (200 mensajes), aunque la tarifa fuera 10 veces la de esa fuente, el gasto sería de unos pocos dólares al mes.
- Dónde confirmar: tarifa oficial en https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing (enlace "rate cards", por moneda) o https://whatsappbusiness.com/es-la/products/platform-pricing/.
- Railway: el servicio cron consume unos segundos de CPU por corrida (unas 40 corridas al día de lunes a sábado); costo marginal, confirmar en el panel de uso de Railway.
- Sin costo de licencias ni de dependencias nuevas.

---

## 14. Verificación en la documentación oficial (consultada el 2026-11-16)

| Tema | Hallazgo | Fuente |
| --- | --- | --- |
| Envío | `POST /<PHONE_NUMBER_ID>/messages`, ejemplos con `v27.0`, `Authorization: Bearer`; `messaging_product`, `recipient_type`, `to` (o `recipient` con BSUID); la respuesta 200 no implica entrega | https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages |
| Ventana de 24 h | Se abre cuando la persona escribe o llama; fuera de ella solo plantillas; solo a quien dio su permiso | misma página |
| Verificación del webhook | `hub.mode=subscribe`, `hub.verify_token`, `hub.challenge`; responder 200 con el challenge | https://developers.facebook.com/docs/whatsapp/webhooks/create-webhook-endpoint |
| Firma | `X-Hub-Signature-256: sha256=<hash>`, HMAC-SHA256 del cuerpo con el app secret; responder 200 si es válida, 4xx si no; TLS válido (sin autofirmados) | misma página |
| Reintentos y lotes | Hasta 1000 actualizaciones por petición; reintentos durante 7 días; hay duplicados: deduplicar | misma página y https://developers.facebook.com/docs/whatsapp/cloud-api/guides/set-up-webhooks |
| Tamaño, mTLS, IP | Cargas de hasta 3 MB; mTLS disponible; IP cambian (se recomienda mTLS antes que listas de IP); campo `messages` | https://developers.facebook.com/docs/whatsapp/cloud-api/guides/set-up-webhooks |
| Estados | `sent`, `delivered`, `read`, `played`, `failed`; `recipient_id`, `recipient_user_id`, `biz_opaque_callback_data`, `pricing.category/type`, `errors[].code` | https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/status |
| Plantillas utility | Parámetros con nombre en minúsculas y guion bajo con ejemplo; cuerpo máx. 1024, pie máx. 60, botón máx. 25 caracteres; hasta 10 botones | https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/utility-templates/utility-templates |
| Categorías | Utility = no promocional y ligado a la cuenta del usuario; las alertas de cuenta no deben "intentar asegurar renovaciones"; ejemplo oficial de recordatorio de pago; sanciones por categoría mal usada | https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/template-categorization |
| Idiomas | Existe `es_CO` (Spanish (COL)), además de `es` | https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/supported-languages |
| Códigos de error | Tabla de la sección 7.3 | https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes |
| Permiso (opt-in) | Decir claramente que se acepta recibir mensajes y de qué negocio; cumplir la ley aplicable; dar instrucciones para darse de baja y respetarlas; Meta limita a negocios con baja calidad | https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in |
| Precios | Por mensaje desde 2025-07-01; cobro al entregar; utility dentro de la ventana se cobra desde 2026-10-01; COP disponible desde 2026-04-01; Colombia con tarifa propia | https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing |
| Límites | 250 personas distintas en 24 h para portafolios nuevos; luego 2.000, 10.000, 100.000, ilimitado; por portafolio | https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits |
| Números | Propio, con indicativo, recibe SMS o llamada; si ya está en WhatsApp hay que borrarlo antes; nombre visible aprobado; PIN de dos pasos; registro con el endpoint `register` | https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/phone-numbers |
| Tokens | Usuario del sistema para quien solo maneja su propio negocio; permisos `business_management`, `whatsapp_business_management`, `whatsapp_business_messaging`; vencimiento a elección al generarlo | https://developers.facebook.com/documentation/business-messaging/whatsapp/access-tokens |
| BSUID | Identificador por negocio; el teléfono puede no venir en webhooks si la persona usa nombre de usuario; enviar por teléfono sigue soportado y recomendado | https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids |
| Railway cron | Comando que debe terminar; mínimo 5 min entre corridas; UTC; salta la corrida si la anterior sigue viva | https://docs.railway.com/reference/cron-jobs |
| Railway App Sleeping | Se despierta con tráfico entrante; la primera petición puede dar 502 | https://docs.railway.com/reference/app-sleeping |
| Ley 2300 de 2023 | Art. 3: lunes a viernes 7:00-19:00, sábados 8:00-15:00, sin domingos ni festivos (contactos de cobranza y comerciales) | https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=213990 |

No verificado (el implementador debe confirmarlo al construir): forma exacta de `components` con parámetros con nombre y cargas de botones en la petición; tarifa vigente de Colombia; si "coexistencia" (usar el mismo número en la app y en la API) está disponible en Colombia (solo encontré fuentes de terceros).

---

## 15. Riesgos abiertos

1. **Recategorización**: si Meta considera promocional el aviso de vencimiento, lo pasa a marketing (más caro) y, si se repite, restringe todas las plantillas utility de la cuenta. Mitigación: texto neutro (3.2) y no agregar "renueva".
2. **Legal** [A VALIDAR por abogado]: texto de consentimiento, transferencia internacional a Meta, alcance de la Ley 2300 sobre avisos de cuenta, y si el número de los entrenadores (empleados) necesita otro tratamiento.
3. **Token**: si vence, los avisos se pausan hasta rotarlo (alerta automática).
4. **Calidad del número**: muchos bloqueos o reportes bajan los límites. Mitigación: doble confirmación, pocos avisos, baja fácil.
5. **Nombres de usuario (BSUID)**: cambio en curso de Meta; el diseño no depende del teléfono en los webhooks, pero hay que revisar la documentación cada cierto tiempo.
6. **Versiones de la Graph API** caducan: la versión va en variable y `check_whatsapp` avisa.
7. **Precios cambian** (el cambio del 2026-10-01 ya encareció la utilidad dentro de la ventana): revisar la tarifa cada trimestre; el tope diario limita el gasto.

---

## 16. Decisiones tomadas (2026-11-16)

### 16.1 Respuesta de la persona: "Usa las recomendadas"

Quedan aprobadas las opciones recomendadas de las preguntas 1 a 13, 15 y 16 (texto original abajo, como registro): número nuevo solo para avisos; los cuatro avisos de la fase 1 más "pago recibido" (apagado por defecto) y sin recordatorio de pago pendiente; aviso 3 días antes; texto neutro (utility); confirmación con el botón "Sí, soy yo"; solo celulares colombianos; respuesta automática una vez al día; cron de Railway; horario de la Ley 2300 para todos los avisos; entrenadores con su propia activación y agrupados cada 3 horas; correo y WhatsApp en paralelo; menores de 18 sin avisos; token con vencimiento + recordatorio; facturación en COP; retención de 180 y 90 días.

### 16.2 Pregunta 14: PENDIENTE (cómo se maneja)

Razón social, NIT, correo de derechos, revisión del abogado, dominio definitivo y política de privacidad siguen sin definir. Mientras tanto:

- El texto de consentimiento v1 de `whatsapp_notices` (migración `health` 0004) es un **borrador con marcadores** `[RAZÓN SOCIAL]`, `[NIT]`, `[CORREO DE DERECHOS]`.
- Variables nuevas, vacías por defecto: `WHATSAPP_CLOUD_SITE_URL` (dominio definitivo, https) y `WHATSAPP_CLOUD_LEGAL_REVIEWED` (`true` cuando el abogado revisó el texto). Se usa además la existente `PRIVACY_POLICY_URL` (https).
- `apps.whatsapp.conf.legal_missing()` lista lo que falta. En **`live`**, mientras falte cualquiera de los cuatro (dominio, política, revisión, texto sin marcadores): la pantalla no ofrece activar, no se encola ni se envía nada, `process_whatsapp_queue` termina con error y `check_whatsapp` / `check_infra` lo muestran. El servidor **sí arranca** (no es un error de configuración técnica).
- En **`off`** y **`simulated`** los pendientes no bloquean (en simulado se puede probar todo el flujo).

### 16.3 Texto original de las preguntas (registro)

1. **¿Qué número envía los avisos?**
   - a) Un número nuevo solo para avisos (**recomendado**): no toca el WhatsApp de atención actual (+57 300 563 8196, el del botón de la web), y las respuestas se redirigen a ese número con la respuesta automática.
   - b) El mismo número actual en "coexistencia" (app y API a la vez): las respuestas llegan a la app de siempre, pero según fuentes de terceros es menos estable, desvincula dispositivos acompañantes y hay que confirmar que esté disponible en Colombia.
   - c) Pasar el número actual por completo a la API: se perdería el uso normal de la app en ese número. No recomendado.
2. **¿Qué avisos entran en la fase 1?** Propuesta: membresía por vencer, membresía vencida, plan aprobado y pendientes para la entrenadora.
   - Pago recibido (Wompi): **recomiendo sí** (es la confirmación de una transacción, claramente utility), apagado por defecto con `WHATSAPP_CLOUD_PAYMENT_NOTICE`.
   - Recordatorio de pago pendiente o abandonado: **recomiendo no** (riesgo de marketing y de cobranza según la Ley 2300).
3. **¿Con cuánta anticipación el aviso "por vencer"?** a) 3 días (**recomendado**); b) 7 días; c) 7 días y 1 día (dos avisos: más costo y más riesgo de bloqueos).
4. **Tono del aviso de vencimiento**: a) neutro, "vence el … puedes ver su estado en tu cuenta" (**recomendado**: utility, más barato y seguro); b) con "renueva aquí" o un descuento (marketing: más caro, exige otra autorización y más restricciones legales).
5. **Confirmación del número**: a) botón "Sí, soy yo" en el primer mensaje (**recomendado**); b) sin confirmación (riesgo de escribirle a un desconocido); c) código de verificación (plantilla de autenticación, más cara y más pasos).
6. **¿Qué números se aceptan?** a) solo celulares colombianos (**recomendado** para la fase 1); b) cualquier país.
7. **¿Qué pasa si alguien responde al número de avisos?** a) respuesta automática que le indica el WhatsApp de atención, máximo una vez al día (**recomendado**); b) no responder nada; c) atender a mano (exige la opción 1b).
8. **¿Cómo se procesa la cola?** a) servicio cron de Railway cada 15 minutos en horario (**recomendado**); b) un programador externo gratuito que llame a una ruta protegida con un secreto (otra cuenta y otro secreto que cuidar); c) solo un hilo dentro de la web (no recomendado: con App Sleeping los avisos de membresía no saldrían si nadie visita la web).
9. **Horario de envío**: a) para todos los avisos, lunes a viernes 7:00-19:00, sábados 8:00-15:00, sin domingos ni festivos (**recomendado**, conservador); b) ese horario solo para clientas, y a los entrenadores a cualquier hora.
10. **¿Los entrenadores también reciben avisos por WhatsApp?** a) sí, con su propia activación y agrupados (máximo uno cada 3 horas) (**recomendado**); b) solo correo.
11. **¿WhatsApp reemplaza el correo?** a) no, los dos en paralelo (**recomendado**: el correo no depende de Meta); b) sí, solo WhatsApp para quien lo active.
12. **Menores de 18**: a) no pueden activar los avisos (**recomendado**, igual que la ficha de salud); b) con autorización del acudiente (fuera de la fase 1).
13. **Token de Meta**: a) con fecha de vencimiento, el plazo más corto que ofrezca el panel, más un recordatorio en tu calendario para renovarlo (**recomendado**: si se filtra, deja de servir solo; si vence, los avisos se pausan y llega una alerta); b) sin vencimiento, rotándolo cada 6 meses y siempre que haya sospecha.
14. **Datos legales y dominio**: ¿razón social, NIT y correo para ejercer derechos? ¿Un abogado puede revisar el texto antes de activar? ¿Cuál es el dominio definitivo para los botones de las plantillas (hoy la URL de Railway o un dominio propio)? ¿Se publicará la política de tratamiento de datos (Meta la pide para la app)?
15. **Moneda de facturación en Meta**: a) COP (**recomendado**: el cobro llega en pesos a la tarjeta colombiana; no se puede cambiar fácilmente después); b) USD.
16. **Retención**: a) registro de avisos 180 días y respuestas recibidas 90 días (**recomendado**); b) otro plazo que indique el abogado.

---

## 17. Registro de implementación (backend, 2026-11-16, rama `feature/whatsapp-avisos`)

**Qué se construyó**

- App `apps/whatsapp` (en `INSTALLED_APPS`): `models.py` (`WhatsAppContact`, `WhatsAppMessage`, `WhatsAppInbound`, `WhatsAppChannelState`; migración `whatsapp` 0001), `conf.py` (modo y pendientes legales), `phone.py` (normalización E.164, solo celulares colombianos, y máscara para logs), `calendar.py` (ventana Ley 2300 y festivos de Colombia, Python puro), `catalog.py` (plantillas, parámetros, textos de servicio, palabras de baja), `services.py` (alta, reenvío, baja, respuestas recibidas, encolado idempotente, ganchos `notify_*`, avisos de membresía), `queue.py` (reclamo atómico, envío, tabla de errores 7.3, reintentos, recuperación, purga), `alerts.py` (pausa del canal o de plantillas y correo a las superusuarias), `webhook.py` (firma, challenge, estados y mensajes), `client/` (interfaz, `live` con `urllib`, `fake`), `views.py`, `forms.py`, `urls.py`, `context_processors.py` (`WHATSAPP_NOTICES_ENABLED`).
- Comandos: `process_whatsapp_queue` (cron), `check_whatsapp` (`--resume`), `whatsapp_register_number`. `check_infra` gana el área "WhatsApp" y trata los tres secretos nuevos como secretos.
- Salud: `PURPOSE_WHATSAPP` y `HEALTH_PURPOSES` (`is_valid_purpose`, `consent_cards` y `purposes_with_placeholders` solo ven salud); migración `health` 0004 (finalidad nueva + texto v1 en borrador).
- Integraciones: `apps/plans/notifications.py` (las cinco funciones encolan también el aviso de WhatsApp), `apps/payments/services.py` (`payment_approved` en `transaction.on_commit`, apagado con `WHATSAPP_CLOUD_PAYMENT_NOTICE=false`), `accounts.dashboard` (contexto `whatsapp_status`), `config/settings` (variables, validación de `live` en `production.py`, logger `apps.whatsapp`), `config/urls.py`, `.env.example`.
- Plantillas: **esqueletos mínimos** `accounts/whatsapp_settings.html`, `trainer/whatsapp_settings.html` y `accounts/partials/whatsapp_settings_body.html`, marcados "REEMPLAZAR POR frontend-architect".
- Pruebas: 103 en `apps/whatsapp/tests/` (sin red), más 5 de `test_webhook_security.py` que agregó otra herramienta y que el código respeta.

**Diferencias con el contrato (decisiones del backend)**

1. `WhatsAppMessage.subject_date` (fecha de vencimiento a la que se refiere el aviso): así se cancela el aviso si la membresía se renovó, sin guardar contenido.
2. Dos tipos de mensaje de servicio más: `confirm_ack` y `not_me_ack` (las respuestas de 3.2 a "Sí, soy yo" / "No soy yo").
3. La **baja web (`whatsapp_disable`) funciona aunque el modo esté en `off`** (derecho de la titular); las demás rutas responden 404 en `off`.
4. Envío colgado: un solo umbral de 40 minutos (`STALE_SENDING`) en lugar de "10 + 30"; el webhook con `ref` sigue reconciliando antes.
5. Errores de cuenta llegados por webhook (`failed` asíncrono): el mensaje vuelve a la cola (sin contar intento) y el canal se pausa, igual que en el envío.
6. "Membresía vencida": se encola si venció en los últimos 3 días (para cubrir domingos y caídas del cron).
7. `export_data` de salud **no se cambió**: la exportación incluye también el consentimiento de WhatsApp (más transparencia para la titular). El contrato decía filtrarlo.
8. Orden de los botones al crear las plantillas en Meta: en `profit_confirmar_numero_v1`, 0 = "Sí, soy yo" y 1 = "No soy yo"; en las demás, 0 = respuesta rápida "No recibir avisos" y 1 = botón de URL. Si Meta devuelve el texto del botón en vez de la carga, también se reconoce.
9. `WHATSAPP_CLOUD_GRAPH_VERSION` tiene valor por defecto `v27.0` (sigue siendo obligatoria y validada en `live`).
10. Pendientes legales: ver 16.2.

**Pendiente**

- Pantallas finales W-01 a W-03 (frontend; detalle en `requests.md`).
- Confirmar con `check_whatsapp` y un número de prueba la forma de `components` con `parameter_name` (sección 7.2).
- Pregunta 14 (16.2) y los pasos de Meta y Railway de la sección 13.
