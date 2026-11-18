# Contrato: desactivar y eliminar cuentas (clientas y entrenadores)

- Estado: **APROBADO (2026-11-17)** por la persona, con todas las recomendaciones de la sección 19: **1a** (los comprobantes se conservan en el bucket privado, visibles solo para la superusuaria y marcados como conservados por obligación contable **[A VALIDAR por contador]**), **2a**, **3a** (lápida), **4a**, **5a**, **6a**, **7a**, **8a**, **9a** y **10a**. Backend implementado el 2026-11-17 (sección 21); pantallas pendientes de frontend-architect (C-01 a C-08).
- Fecha: 2026-11-16 (propuesta) y 2026-11-17 (aprobación e implementación). Autor: arquitecto-backend. Rama: `feature/eliminar-usuarios`.
- Pedido de la persona: "que el perfil de administrador pueda eliminar usuarios y entrenadores".
- Decisiones ya tomadas por la persona (2026-11-16):
  1. Dos acciones separadas: **Desactivar** (no puede iniciar sesión, se conserva todo, se puede reactivar, sale de las listas por defecto) y **Eliminar definitivamente** (se confirma escribiendo el nombre). Los **pagos se conservan sin datos personales** para la contabilidad.
  2. Al desactivar o eliminar un entrenador, la confirmación pide **a quién pasar sus clientas** (otro entrenador activo o la superusuaria) y se reasignan en la misma transacción.
- Aviso: lo legal (Ley 1581 de 2012, Decreto 1377 de 2013, Ley 962 de 2005) es una propuesta técnica marcada **[A VALIDAR por abogado y contador]**. No es asesoría legal. Las normas se consultaron en Función Pública el 2026-11-16 (sección 20).
- Términos que se usan aquí:
  - **Superusuaria**: la dueña (`role='trainer'` + `is_superuser=True`), la "administradora" del pedido.
  - **Cuenta**: una fila de `accounts.User` (clienta o entrenador).
  - **Cascada**: cuando se borra una fila, Django borra solo todas las filas que dependen de ella (`on_delete=CASCADE`).
  - **Anonimizar**: borrar los datos que identifican a la persona y dejar el resto (montos, fechas, códigos).
  - **Lápida**: la fila de la cuenta que queda después de eliminarla, sin ningún dato personal (sección 3).

---

## 0. Resumen en diez líneas

1. Una cuenta tiene tres estados: **activa**, **desactivada** (reversible) y **eliminada** (definitivo).
2. **Desactivar** = `is_active=False` + fecha. Django ya rechaza el inicio de sesión de una cuenta inactiva y la saca en la siguiente petición; los avisos de WhatsApp y los correos ya revisan `is_active`.
3. **Eliminar definitivamente** borra todos los datos personales y de salud, conserva los pagos sin datos personales y deja una **lápida** (la fila de `User` vaciada) para que pagos, auditoría y constancias no pierdan su referencia. Hoy, en cambio, `trainer_client_delete` borra todo en cascada, **incluidos los pagos manuales**, y deja los comprobantes huérfanos en R2.
4. Solo la **superusuaria** desactiva, reactiva y elimina entrenadores, y es la única que elimina clientas. Un entrenador puede **desactivar y reactivar** a sus propias clientas (recomendado; pregunta 2), pero ya no eliminarlas.
5. Nunca se puede actuar sobre una superusuaria (tampoco sobre una misma), y el servicio comprueba además que siempre quede al menos una superusuaria activa.
6. Al desactivar o eliminar un entrenador se elige a quién pasar sus clientas; en la misma transacción se reasignan, y sus planes "en revisión" vuelven a la bandeja.
7. Todas las acciones son **POST con CSRF**; el GET solo muestra la confirmación. La eliminación exige escribir el nombre tal como aparece.
8. Un registro de auditoría nuevo (`AccountAuditLog`) guarda quién hizo qué a qué cuenta y cuándo, **sin nombres ni datos personales**.
9. Los archivos (foto de perfil en el bucket público y, según la pregunta 1, el comprobante en el bucket privado) se borran **después** de confirmar la transacción.
10. Cambios de pantallas para frontend en `docs/contracts/requests.md` ("Cuentas: contrato C-01 a C-08").

---

## 1. Alcance

### 1.1 Incluido

- Desactivar, reactivar y eliminar definitivamente **clientas** y **entrenadores** (no superusuarias).
- Reasignación de clientas al desactivar o eliminar un entrenador.
- Filtro "Activas / Desactivadas / Todas" en las listas de clientas y de entrenadores.
- Registro de auditoría y su consulta en el admin, solo para leer.
- Cierre de las protecciones que hoy permiten saltarse el flujo: botón "Eliminar" del admin de usuarios y cascada sobre `ManualPayment`.

### 1.2 Fuera de alcance (se puede pedir después)

- Que la propia clienta pida eliminar su cuenta desde la web (autoservicio). Hoy la solicitud de supresión llega por WhatsApp o correo y la ejecuta la superusuaria con este flujo.
- Desactivar o eliminar superusuarias. Cambiar quién es superusuaria (sigue siendo con `create_trainer` o con el admin).
- Exportar todos los datos de la cuenta antes de eliminarla. La ficha de salud ya tiene su propia exportación (`member_health_export`).
- Borrado automático de las lápidas, las constancias o los pagos al cumplirse los plazos de conservación (pregunta 6).

---

## 2. Quién puede hacer qué

| Acción | Superusuaria | Entrenador (no superusuario) | Clienta |
| --- | --- | --- | --- |
| Desactivar una clienta | Todas | **Solo sus clientas asignadas** (recomendado, pregunta 2) | No |
| Reactivar una clienta | Todas | Solo sus clientas asignadas | No |
| Eliminar definitivamente una clienta | Sí | **No** (hoy sí puede: se le quita) | No |
| Desactivar, reactivar o eliminar un entrenador | Sí | No | No |
| Actuar sobre una superusuaria (incluida ella misma) | **Nunca** | Nunca | Nunca |
| Actuar sobre sí misma o sí mismo | Nunca | Nunca | — |
| Ver clientas desactivadas | Todas | Sus asignadas | — |
| Ver entrenadores desactivados | Sí | No (no ve la lista de entrenadores) | — |
| Ver la auditoría (admin, solo lectura) | Sí | No | No |

Reglas que hace cumplir el **servicio**, además de las vistas:

- `target.is_superuser` → se rechaza (`account_protected`).
- `target.pk == actor.pk` → se rechaza (`account_self`).
- Después de la acción debe quedar al menos una cuenta con `role='trainer'`, `is_superuser=True`, `is_active=True` y sin `erased_at`. Con las dos reglas anteriores esto ya se cumple; se comprueba igual dentro de la transacción (`last_superuser`), por si alguien cambia `is_superuser` desde el admin.
- Una cuenta eliminada (`erased_at` no vacío) no admite ninguna acción (`account_erased`) y no aparece en ninguna lista.

Un entrenador sin permiso recibe **404** en las rutas de clientas que no son suyas (regla de `permissions.py`, para no revelar que existen). En las rutas solo de la superusuaria se usa `@superuser_required`, que lo devuelve a su panel con un mensaje.

---

## 3. Estados de una cuenta

```
            Desactivar                 Eliminar definitivamente
ACTIVA  ─────────────────▶ DESACTIVADA ─────────────────────────▶ ELIMINADA (lápida)
   ▲        Reactivar          │                                      ▲
   └───────────────────────────┘                                      │
   └──────────────────────────────────────────────────────────────────┘
                     Eliminar definitivamente (también desde ACTIVA)
```

| Estado | `is_active` | `deactivated_at` | `erased_at` | Puede iniciar sesión | Aparece en listas |
| --- | --- | --- | --- | --- | --- |
| Activa | `True` | vacío | vacío | Sí | Sí (por defecto) |
| Desactivada | `False` | fecha | vacío | No | Solo con el filtro "Desactivadas" o "Todas" |
| Eliminada | `False` | fecha | fecha | No | **Nunca**. En los pagos aparece como "Cuenta eliminada" |

**Por qué una lápida y no borrar la fila.** Diez tablas guardan "quién hizo" algo (aprobó un plan, registró un pago, abrió una ficha). Si se borra la fila, esas referencias quedan vacías y se pierde la trazabilidad que pide la Ley 1581 (seguridad y acceso restringido). Además, `ManualPayment.user` hoy está en cascada y se llevaría los pagos. La lápida no tiene nombre, correo, teléfono, foto, usuario reconocible ni contraseña utilizable, así que deja de ser un dato personal dentro del sistema. La otra opción (borrar la fila de verdad) está en la pregunta 3.

---

## 4. Rutas nuevas y cambiadas (`apps/accounts/urls.py`)

Todas devuelven 405 con métodos distintos de los indicados. Todo formulario lleva `{% csrf_token %}`. Las pantallas de confirmación responden `Cache-Control: no-store`.

| Nombre | Ruta | Métodos | Quién | Qué hace |
| --- | --- | --- | --- | --- |
| `trainer_clients` (cambia) | `/accounts/trainer/clients/?status=active\|inactive\|all` | GET | entrenador | Lista. Sin `status`, o con un valor no válido, se usa `active`. |
| `trainer_client_deactivate` (nueva) | `/accounts/trainer/clients/<pk>/deactivate/` | GET, POST | entrenador con acceso (`get_client_for_trainer`) | GET: confirmación C-03. POST: desactiva. |
| `trainer_client_reactivate` (nueva) | `/accounts/trainer/clients/<pk>/reactivate/` | POST | entrenador con acceso | Reactiva. |
| `trainer_client_delete` (cambia) | `/accounts/trainer/clients/<pk>/delete/` | GET, POST | **solo superusuaria** | GET: confirmación C-04 con el resumen. POST: elimina definitivamente. |
| `trainer_staff_list` (cambia) | `/accounts/trainer/staff/?status=active\|inactive\|all` | GET | superusuaria | Lista con filtro. |
| `trainer_staff_deactivate` (nueva) | `/accounts/trainer/staff/<pk>/deactivate/` | GET, POST | superusuaria | GET: confirmación C-06. POST: reasigna y desactiva. |
| `trainer_staff_reactivate` (nueva) | `/accounts/trainer/staff/<pk>/reactivate/` | POST | superusuaria | Reactiva (las clientas **no** vuelven solas). |
| `trainer_staff_delete` (nueva) | `/accounts/trainer/staff/<pk>/delete/` | GET, POST | superusuaria | GET: confirmación C-07. POST: reasigna y elimina definitivamente. |

`<pk>` de una superusuaria, de una cuenta eliminada, de la propia cuenta o de un rol equivocado (por ejemplo, el id de una clienta en `/staff/`) → **404**. Así nunca se pinta un botón para algo que no se puede hacer.

### 4.1 Campos de los POST

| Ruta | Campo | Reglas |
| --- | --- | --- |
| desactivar (clienta o entrenador) | `reason` | Opcional. Lista cerrada (sección 11.2). Otro valor → error de campo. |
| eliminar (clienta o entrenador) | `confirm_name` | Obligatorio. Se compara con el nombre mostrado (`get_full_name()` o, si está vacío, `username`), quitando espacios al principio y al final, juntando espacios repetidos y sin distinguir mayúsculas (`casefold`). **Las tildes sí cuentan.** Máximo 300 caracteres. |
| eliminar (clienta o entrenador) | `reason` | Obligatorio al eliminar (recomendado, para dejar constancia de por qué se suprimieron los datos). Lista cerrada. |
| eliminar clienta | `ack_payments` | Casilla obligatoria **solo si** la clienta tiene una membresía vigente o un pago Wompi `pending`: "Entiendo que tiene una membresía vigente o un pago en curso". |
| desactivar o eliminar entrenador | `reassign_to` | Obligatorio si el entrenador tiene al menos una clienta asignada. Debe ser el id de una cuenta `role='trainer'`, activa, no eliminada y distinta del entrenador. Cualquier otro valor → error de campo (nunca 500). Si no tiene clientas, se ignora. |
| reactivar | (ninguno) | — |

Con errores, la vista vuelve a pintar la misma confirmación con `errors` y `form` (HTTP 200), como el resto del panel. Si sale bien, redirige (patrón POST → redirección → GET) con un mensaje de `messages`.

---

## 5. Desactivar: efectos exactos

Todo en **una transacción** (`transaction.atomic`), con `select_for_update()` sobre la cuenta afectada (y sobre el entrenador destino, si lo hay) para que dos clics o dos personas no hagan la acción dos veces. Si la cuenta ya estaba desactivada, no cambia nada y responde con el mensaje "ya estaba desactivada" (idempotente).

### 5.1 Comunes a clientas y entrenadores

1. `is_active=False`, `deactivated_at=ahora`. No se cambia la contraseña: al reactivar, la persona entra con la misma.
2. Se cierran las sesiones abiertas (sección 9).
3. Avisos de WhatsApp en cola para esa cuenta (`status` `queued` o `sending` sin `wamid`) → `cancelled`, `error_code='user_inactive'`. La cola ya los cancela al enviar (`queue._recipient_problem`), pero así no quedan pendientes en la pantalla del entrenador. `WhatsAppContact` **se conserva**: al reactivar, los avisos vuelven solos si el consentimiento sigue vigente.
4. Nada se borra: ficha, medidas, rutinas, planes, pagos, membresía y consentimientos se quedan igual.
5. Fila nueva en `AccountAuditLog` (`action='deactivate'`).

### 5.2 Solo clientas

6. Trabajos de IA de la clienta en `queued` → `failed` con `error_code='client_inactive'`. Los que están `running` terminan, pero `jobs` no crea el plan si la clienta ya no está activa (mismo código), para que no aparezca un borrador de alguien desactivado.
7. La membresía **no se toca** (las fechas siguen corriendo). Ver la pregunta 5 si se quiere "congelar".
8. Mientras esté desactivada, su ficha en el panel es **de solo lectura** (recomendado; pregunta 4): las vistas que escriben datos de esa clienta (editar, membresía, pago manual, valoración, medida, rutina, ficha, generar plan, aprobar plan) responden con el mensaje "Esta cuenta está desactivada. Reactívala para hacer cambios" y redirigen al detalle. La regla vive en un ayudante nuevo, `permissions.ensure_client_active(client)`, que lanzan los servicios, no solo las vistas.

### 5.3 Solo entrenadores

9. **Reasignación** (sección 8): todas sus clientas (`assigned_clients`, también las desactivadas) pasan a `reassign_to`.
10. Planes con `status='in_review'` y `reviewed_by=entrenador` → vuelven a `draft_ai` con `reviewed_by=NULL`, y se crea un `PlanEvent` nuevo (`action='released'`, `actor`=quien desactivó, sin nota). Así aparecen en la bandeja del nuevo entrenador. Los aprobados no cambian.
11. `TrainerCredential` se conserva (se recupera al reactivar).
12. La página pública "Nosotros" (`apps/public/views.about`) deja de mostrarlo: hoy lista a **todos** los `role='trainer'`; pasa a filtrar `is_active=True`.
13. Deja de aparecer en la lista para asignar clientas (`trainer_assign_client`), que además valida en el POST que el destino esté activo.
14. Las rutinas, valoraciones y planes que creó o aprobó conservan su nombre (es historial); el panel puede mostrar "(desactivado)" junto a él.

## 6. Reactivar

1. Solo desde **desactivada** (nunca desde eliminada). `is_active=True`, `deactivated_at=NULL`.
2. Entrenador: **no** recupera sus clientas; la superusuaria las reasigna a mano si quiere (`trainer_assign_client`). Se avisa en el mensaje.
3. Clienta: vuelve a sus listas y su ficha deja de ser de solo lectura. Para recibir avisos de WhatsApp sigue haciendo falta un consentimiento vigente (no se renueva solo).
4. `AccountAuditLog` (`action='reactivate'`).

---

## 7. Eliminar definitivamente: efectos exactos

Una sola transacción con `select_for_update()`. Si algo falla, no se borra nada. Los archivos se borran con `transaction.on_commit` (sección 7.4). El paso a paso vive en un caso de uso nuevo, `apps/accounts/services.py` (`erase_account(target, actor, reason, reassign_to=None)`); la vista solo maneja HTTP.

Orden:

1. Mismas comprobaciones de permiso de la sección 2 y validación del POST.
2. Si es entrenador con clientas: **reasignación** (sección 8) y liberación de planes `in_review` (5.3, punto 10).
3. Cancelar los avisos de WhatsApp en cola (5.1, punto 3) y los trabajos de IA (5.2, punto 6).
4. Borrar, anonimizar o conservar cada tabla según la sección 7.1.
5. Vaciar la fila de `User` y convertirla en lápida (7.2).
6. Limpiar lo que no es clave foránea (7.3).
7. `AccountAuditLog` (`action='erase'`).
8. Después de confirmar la transacción: borrar los archivos (7.4) y escribir en el log solo `account erased user_id=<pk> role=<rol>`.

### 7.1 Inventario de todas las claves foráneas a `User` (30) y qué se hace con cada una

Revisadas con la introspección de Django el 2026-11-16 en esta rama: 17 en cascada (incluida la del admin) y 13 con `SET_NULL`. La columna "Hoy" muestra lo que pasa con el borrado actual (`client.delete()`).

**A. Datos de la persona eliminada (la cuenta es la dueña del dato)**

| # | Tabla.campo | Hoy | Al eliminar | Por qué |
| --- | --- | --- | --- | --- |
| 1 | `health.HealthProfile.user` (ficha de salud, todas las versiones) | se borra en cascada | **Se borra.** Se registra en `HealthAccessLog` (`action='delete'`, `actor`=quien elimina, versión mayor) | Dato sensible. Mismo efecto que `health.services.delete_profile`, pero con el actor correcto (`delete_profile` registra a la propia clienta como actor). |
| 2 | `assessments.InitialAssessment.user` (+ `DixonTest` en cascada) | cascada | **Se borra** | Datos de salud (IMC, frecuencia cardíaca). |
| 3 | `assessments.BodyMeasurement.user` | cascada | **Se borra** | Datos de salud. |
| 4 | `routines.Routine.user` (+ días y ejercicios) | cascada | **Se borra** | Datos de la persona. |
| 5 | `routines.RoutineDayLog.user` | cascada | **Se borra** | Hábitos de la persona. |
| 6 | `plans.Plan.client` (+ `PlanEvent`, `NutritionPlan`, comidas y opciones) | cascada | **Se borra** | Derivado de la ficha de salud. |
| 7 | `plans.PlanGenerationJob.client` | cascada | **Se borra** (después de cancelar los que están en cola) | Lleva banderas de salud y notas. |
| 8 | `plans.MealChoice.client` | cascada | **Se borra** | Hábitos de la persona. |
| 9 | `memberships.Membership.user` | cascada | **Se borra** | Es el estado del servicio, no un registro contable; el pago (10 y 11) conserva plan, monto y fecha. Su campo `notes` es texto libre. |
| 10 | `payments.ManualPayment.user` | **cascada: hoy se pierden los pagos** | **Se conserva**, apuntando a la lápida. `notes=''`. Comprobante: **pregunta 1**. Se conservan monto, método, fecha, plan, `trainer` y `created_at`. | Conservación contable (sección 18). El `on_delete` cambia a `PROTECT` (sección 12) para que nada vuelva a borrarlo en cascada. |
| 11 | `payments.Payment.user` (Wompi) | `SET_NULL` (se conserva, pero `raw_webhook` guarda correo, nombre y teléfono) | **Se conserva**, apuntando a la lápida. `raw_webhook` se reduce a una lista blanca: `id`, `reference`, `status`, `amount_in_cents`, `currency`, `payment_method_type`, `created_at`, `finalized_at`. Se quita todo lo demás (`customer_email`, `customer_data`, `shipping_address`, datos de `payment_method`). | Igual que 10. La referencia permite cruzarlo con el panel de Wompi, que guarda sus propios datos. |
| 12 | `health.ConsentRecord.user` | cascada | **Se conserva** apuntando a la lápida (versión, `granted_at`, `revoked_at`); los activos se marcan `revoked_at=ahora`. **[A VALIDAR por abogado]**, pregunta 6 | Decreto 1377 de 2013, art. 8: hay que conservar la prueba de la autorización. No tiene datos personales propios. |
| 13 | `health.HealthAccessLog.client` | cascada | **Se conserva** apuntando a la lápida. **[A VALIDAR]** | Es la constancia de quién abrió la ficha y de que se borró. Sin IP ni contenido. |
| 14 | `whatsapp.WhatsAppContact.user` | cascada | **Se borra** (tiene el teléfono) | Dato personal. `WhatsAppInbound.contact` ya está en `SET_NULL`. |
| 15 | `whatsapp.WhatsAppMessage.recipient` | cascada | Se cancelan los que están en cola y después **se borran todos** | Solo tienen ids y códigos, pero no sirven para nada sin la persona. |
| 16 | `plans.TrainerCredential.user` (entrenador) | cascada | **Se borra** (tiene el registro profesional). `Plan.reviewer_credential` (texto copiado al aprobar) **se conserva** en los planes de **otras** clientas | El plan aprobado debe seguir diciendo quién lo validó (evidencia profesional). |
| 17 | `admin.LogEntry.user` (acciones de la persona en el admin) | cascada | **Se conserva** apuntando a la lápida | Con la lápida no se borra. Es la trazabilidad del admin. |

**B. Referencias a la persona como autora de algo de otra persona (sobre todo entrenadores)**

Con la lápida, ninguna de estas referencias queda vacía. Muestran "Cuenta eliminada" (propiedad `display_name`, sección 12).

| # | Tabla.campo | Hoy | Al eliminar |
| --- | --- | --- | --- |
| 18 | `accounts.User.assigned_trainer` (clientas del entrenador) | `SET_NULL`: quedan sin asignar y solo las ve la superusuaria | **Se reasignan** a `reassign_to` antes de eliminar (sección 8). |
| 19 | `routines.Routine.trainer` (rutinas que creó) | `SET_NULL` | Se conserva → lápida. |
| 20 | `assessments.InitialAssessment.trainer` | `SET_NULL` | Se conserva → lápida. |
| 21 | `assessments.BodyMeasurement.trainer` | `SET_NULL` | Se conserva → lápida. |
| 22 | `health.HealthAccessLog.actor` (fichas que abrió o editó) | `SET_NULL`: se pierde quién accedió | Se conserva → lápida (trazabilidad, Ley 1581). |
| 23 | `health.HealthProfile.created_by` | `SET_NULL` | Se conserva → lápida. |
| 24 | `memberships.Membership.activated_by` | `SET_NULL` | Se conserva → lápida. |
| 25 | `payments.ManualPayment.trainer` (pagos que registró) | `SET_NULL` | Se conserva → lápida. |
| 26 | `plans.Plan.approved_by` | `SET_NULL` | Se conserva → lápida (más `reviewer_credential`, punto 16). |
| 27 | `plans.Plan.reviewed_by` | `SET_NULL` | Los planes `in_review` se liberan antes (5.3, punto 10); en los demás se conserva → lápida. |
| 28 | `plans.Plan.warnings_ack_by` | `SET_NULL` | Se conserva → lápida. |
| 29 | `plans.PlanEvent.actor` | `SET_NULL` | Se conserva → lápida. |
| 30 | `plans.PlanGenerationJob.requested_by` | `SET_NULL` | Se conserva → lápida. |

Más dos tablas automáticas de Django (`groups` y `user_permissions`, muchos a muchos): se vacían.

Ejercicios (`exercises.Exercise`) y alimentos (`plans.Food`) **no** tienen clave foránea a `User`, así que no se tocan. `Food.validated_by` es texto libre con el nombre de quien validó las cifras: si era el entrenador eliminado, la superusuaria decide en el admin si lo cambia (se avisa en la confirmación C-07).

### 7.2 La lápida (campos de `User`)

| Campo | Valor |
| --- | --- |
| `username` | `cuenta-eliminada-<pk>` (libera el nombre de usuario original para que se pueda volver a usar) |
| `first_name`, `last_name`, `email`, `phone`, `bio` | `''` |
| `gender`, `training_goal` | `''` |
| `interested_plan`, `assigned_trainer` | `NULL` |
| `profile_photo` | `NULL` (el archivo se borra en 7.4) |
| `password` | `set_unusable_password()` |
| `is_active`, `is_staff`, `is_superuser` | `False` |
| `last_login` | `NULL` |
| `role`, `date_joined`, `created_at` | Se conservan (solo para estadísticas: "cuántas clientas hubo") |
| `deactivated_at` | Se conserva o se pone ahora |
| `erased_at` | ahora |

### 7.3 Lo que no es clave foránea

| Dónde | Qué se hace |
| --- | --- |
| `axes` (`AccessAttempt`, `AccessLog`, `AccessFailureLog`) | Se borran las filas con el `username` **original** (guardan IP y navegador). Se hace antes de renombrar. |
| Sesiones | Sección 9. |
| `admin.LogEntry` de **objetos** de la persona (`content_type` de `User`, `Membership`, `ManualPayment` o `Payment` con su id) | `object_repr` y `change_message` se reemplazan por `'[dato eliminado]'`. Hoy `User.__str__`, `ManualPayment.__str__` y `Membership.__str__` guardan el nombre completo ahí. |
| Caché de límites (`ratelimit`) | Nada: es por IP y caduca sola. |
| Testimonios (`public.Testimonial`, texto escrito a mano) | El código no puede saber si uno es de ella. La confirmación C-04 avisa: "Si tiene un testimonio publicado, bórralo en Admin > Testimonios". |
| Correos ya enviados, registros de Railway y copias de seguridad de la base | Fuera del alcance del código. Las copias de Railway se renuevan solas según su plan (pregunta 6). |

### 7.4 Archivos (después de confirmar la transacción)

- `profile_photo`: **siempre se borra** del almacenamiento. Con R2 está en el bucket **público** con una URL permanente: si no se borra, la foto sigue en internet.
- `ManualPayment.receipt` (bucket **privado**): **se conserva** (respuesta 1a). Dentro de la transacción se marca `receipt_retained_at=ahora` ("Comprobante conservado por obligación contable", **[A VALIDAR por contador]**). Solo la superusuaria lo puede abrir (`payment_receipt`).
- Si el borrado de un archivo falla (R2 caído), el log solo guarda `account erase file_delete_failed kind=<photo|receipt> user_id=<pk>`, sin nombres ni rutas, y el comando `python manage.py purge_orphan_files` (nuevo) lo reintenta: borra los archivos de `profiles/` y `receipts/` que ninguna fila referencia. Ese mismo comando limpia los comprobantes que hoy quedan huérfanos por el borrado en cascada actual.

---

## 8. Reasignación de clientas (entrenadores)

- La confirmación (C-06 y C-07) muestra cuántas clientas tiene asignadas (activas y desactivadas) y un selector `reassign_to` con **todos los entrenadores activos menos él**, con la superusuaria primero y marcada "(dueña)". No se preselecciona ninguno: hay que elegir.
- Si no tiene clientas, no se muestra el selector y la acción sigue igual.
- En la transacción: `User.objects.filter(assigned_trainer=entrenador).update(assigned_trainer=destino)`, después de bloquear al destino con `select_for_update()` y comprobar que sigue activo. Si el destino se desactivó mientras tanto: error de campo "Ese entrenador ya no está disponible. Elige otro." y no se hace nada.
- Un entrenador desactivado no puede tener clientas: esa es la invariante que prueban los tests.
- Los planes `in_review` del entrenador se liberan (5.3, punto 10). Los avisos de "pendientes por revisar" que salgan después ya van al nuevo entrenador, porque `trainer_recipients` usa `assigned_trainer`.
- `AccountAuditLog` guarda `reassigned_to` y `reassigned_count`.

---

## 9. Sesiones abiertas

- **Mecanismo principal (sirve con cualquier motor de sesiones):** en cada petición, Django vuelve a cargar la cuenta (`ModelBackend.get_user`) y la rechaza si `is_active=False`. La persona desactivada queda fuera en su **siguiente clic** y va a la pantalla de inicio de sesión. En producción las sesiones están en Redis cuando existe `REDIS_URL`, y ahí no se pueden recorrer por persona; por eso este es el mecanismo que vale.
- **Además**, si el motor es la base de datos (`django.contrib.sessions.backends.db`, el de desarrollo y el de producción sin Redis): se borran las filas de `django_session` de esa persona. Para eso se guarda una marca nueva en la sesión al iniciar sesión (no hace falta decodificar todas). Si no se puede (Redis), no pasa nada: el mecanismo principal ya la sacó.
- Al eliminar, `set_unusable_password()` cambia además el hash de la sesión, y Django invalida todas las sesiones de esa cuenta.
- Al intentar iniciar sesión, una cuenta desactivada o eliminada recibe el mismo mensaje genérico que una contraseña equivocada ("Usuario o contraseña incorrectos."), para no revelar qué cuentas existen. La recuperación de contraseña de Django ya ignora las cuentas inactivas (no envía el correo).
- El inicio de sesión de una cuenta inactiva no suma intentos fallidos de `axes` de forma distinta a un error normal (comportamiento actual).

---

## 10. WhatsApp, correos, IA y pagos

| Tema | Desactivada | Eliminada |
| --- | --- | --- |
| Avisos de WhatsApp en cola | `cancelled`, `user_inactive` (5.1) | Se cancelan y se borran (7.1, punto 15) |
| Avisos futuros | La cola y el cron ya excluyen a quien tiene `is_active=False` (`queue._recipient_problem`, `services.py` líneas 63, 303 y 349). Se agrega una prueba que lo confirma | No hay contacto: no se puede encolar nada |
| Correos a la persona | `plans/notifications.py` ya excluye a los entrenadores inactivos. Se agrega la misma regla a `client_plan_approved` (no escribir a una clienta desactivada) | Sin correo: no queda dirección |
| Correo de confirmación de la eliminación | **No** se envía en la fase 1 (pregunta 7) | — |
| Aviso a la superusuaria de un registro nuevo (`_notify_new_registration`) | Se agrega el filtro `is_active=True` (hoy escribe a todas las superusuarias, aunque estén inactivas) | — |
| Trabajos de IA | En cola → `failed` `client_inactive`; los que están corriendo no crean el plan (5.2) | Se cancelan y se borran. La verificación de los que están corriendo también revisa `erased_at` |
| Webhook de Wompi que llega **después** | Se procesa igual: si se aprueba, se activa la membresía (la clienta pagó) y la superusuaria la ve en la lista de desactivadas | Se guarda el estado del pago con `raw_webhook` ya reducido a la lista blanca (7.1, punto 11), **no** se crea membresía, `status='approved'` y en el log `Wompi webhook: pago aprobado de una cuenta eliminada <reference> — revisión manual` (para devolver el dinero). Sin nombres |
| Pagos Wompi `pending` al eliminar | — | Se avisa en C-04 y se exige `ack_payments` |

---

## 11. Auditoría

### 11.1 Modelo `accounts.AccountAuditLog` (migración nueva)

| Campo | Tipo | Notas |
| --- | --- | --- |
| `actor` | FK `User`, `PROTECT` | Quién lo hizo. Nunca se borra (las superusuarias no se eliminan y el resto queda como lápida). |
| `target` | FK `User`, `PROTECT` | A quién. Después de eliminar apunta a la lápida. |
| `target_role` | `member` / `trainer` | Copia del rol en ese momento. |
| `action` | `deactivate` / `reactivate` / `erase` | |
| `reason` | lista cerrada, opcional al desactivar y obligatoria al eliminar | 11.2 |
| `reassigned_to` | FK `User`, `PROTECT`, nulo | Solo entrenadores. |
| `reassigned_count` | entero ≥ 0 | |
| `created_at` | fecha y hora | |

Sin nombres, correos, IP ni texto libre. Solo lectura en el admin para la superusuaria: `has_add_permission`, `has_change_permission` y `has_delete_permission` devuelven `False`. Sin pantalla nueva en el panel (pregunta 8).

### 11.2 Motivos (lista cerrada, `apps/accounts/choices.py`)

| Código | Texto |
| --- | --- |
| `holder_request` | La persona pidió eliminar sus datos (derecho de supresión) |
| `left` | Ya no entrena aquí o ya no trabaja aquí |
| `duplicate` | Cuenta repetida o creada por error |
| `security` | Seguridad (cuenta comprometida o uso indebido) |
| `other` | Otro motivo |

---

## 12. Modelo de datos y migraciones

| App | Cambio |
| --- | --- |
| `accounts` | `User.deactivated_at` y `User.erased_at` (`DateTimeField`, nulos). Propiedades `is_erased` y `display_name` ("Cuenta eliminada" si se eliminó; si no, el nombre completo o el usuario). Modelo `AccountAuditLog`. Con `erased_at` lleno, la restricción `CheckConstraint` exige `is_active=False`. |
| `accounts` (admin) | `CustomUserAdmin.has_delete_permission` → `False` (el botón "Eliminar" del admin borra en cascada y se salta todo esto). Las acciones masivas de borrado se quitan. `is_active` y `is_superuser` quedan de solo lectura en el admin (pregunta 9). |
| `payments` | `ManualPayment.user`: `CASCADE` → `PROTECT`. `Payment.user`: `SET_NULL` → `PROTECT`. `ManualPayment.__str__` deja de usar el nombre (usa `display_name`). |
| `plans` | `PlanEvent.ACTION_CHOICES` + `('released', 'Liberado: el entrenador se desactivó')`. La migración solo cambia las opciones (no toca la base). |
| Datos | Sin migración de datos: las cuentas que ya existen quedan activas, sin fechas. |

`clients_for_trainer` excluye `erased_at__isnull=False`. Las desactivadas **siguen** incluidas: se necesitan para verlas y reactivarlas. Las listas filtran con `status`.

---

## 13. Mensajes (`messages`, ya redactados)

| Situación | Tipo | Texto |
| --- | --- | --- |
| Clienta desactivada | éxito | «Desactivaste la cuenta de {nombre}. Ya no puede iniciar sesión y su historial se conserva. Puedes reactivarla cuando quieras.» |
| Clienta reactivada | éxito | «La cuenta de {nombre} está activa de nuevo.» |
| Clienta eliminada | éxito | «Eliminaste definitivamente la cuenta de {nombre}. Sus pagos se conservan sin datos personales.» |
| Entrenador desactivado | éxito | «Desactivaste a {nombre}. {n} clientas pasaron a {destino}.» (sin clientas: «Desactivaste a {nombre}.») |
| Entrenador reactivado | éxito | «{nombre} está activo/a de nuevo. Sus clientas anteriores siguen con quien las tiene ahora; reasígnalas si hace falta.» |
| Entrenador eliminado | éxito | «Eliminaste definitivamente la cuenta de {nombre}. {n} clientas pasaron a {destino}.» |
| Ya estaba en ese estado | información | «Esa cuenta ya estaba desactivada.» / «Esa cuenta ya estaba activa.» |
| Intento sobre una superusuaria, una misma o la última superusuaria | error | «No se puede hacer esto con esta cuenta.» (las rutas responden 404 antes; este mensaje es para el servicio) |
| Acción de escritura sobre una clienta desactivada | error | «Esta cuenta está desactivada. Reactívala para hacer cambios.» |
| Error inesperado | error | «No pudimos completar la acción. No se cambió nada. Inténtalo de nuevo.» (el detalle va al logger, nunca a la pantalla) |

`{nombre}` se toma **antes** de eliminar y se pinta escapado. Errores de campo (en `errors`):

- `confirm_name`: «El nombre no coincide. Escríbelo tal como aparece arriba.»
- `reassign_to`: «Elige a quién pasar sus clientas.» / «Ese entrenador ya no está disponible. Elige otro.»
- `reason`: «Elige un motivo.»
- `ack_payments`: «Confirma que revisaste la membresía y los pagos en curso.»

---

## 14. Seguridad (OWASP)

- Control de acceso: decoradores + `get_client_for_trainer` (404) en las vistas, y las mismas reglas repetidas en el servicio (defensa en profundidad). Lo que no se puede hacer responde 404, sin revelar si la cuenta existe.
- Solo POST con CSRF para las acciones. El GET nunca cambia nada. Sin ids ni nombres en la URL, salvo el `<pk>` que ya se usa hoy.
- Entradas validadas: `pk` como entero de la ruta, `reassign_to` como entero que existe y cumple las reglas, `reason` en una lista cerrada, `confirm_name` con longitud máxima. Solo el ORM, sin SQL armado a mano.
- Condiciones de carrera: `select_for_update()` sobre la cuenta afectada y el destino; las acciones se pueden repetir sin efecto (idempotentes).
- Logs: solo ids, rol, acción y códigos. Nunca nombres, correos, teléfonos, el texto escrito en `confirm_name` ni el contenido de la ficha.
- Errores: mensaje genérico al usuario; la traza va al logger.
- Archivos: la foto del bucket público se borra siempre; el comprobante del bucket privado nunca se expone (sigue entrándose solo por `payment_receipt`, que para una lápida solo deja pasar a la superusuaria).
- El admin deja de ser una puerta trasera (12).

---

## 15. Cambios en el código existente

| Archivo | Cambio |
| --- | --- |
| `apps/accounts/views.py` | `trainer_client_delete`: pasa a `@superuser_required` y llama a `services.erase_account` (hoy hace `client.delete()`). Nuevas: `trainer_client_deactivate`, `trainer_client_reactivate`, `trainer_staff_deactivate`, `trainer_staff_reactivate`, `trainer_staff_delete`. `trainer_client_list` y `trainer_staff_list`: filtro `status` y conteos. `trainer_assign_client`: solo entrenadores activos (en la lista y en el POST). `trainer_client_detail`: indicadores `can_deactivate`, `can_reactivate` y `can_erase`. `_notify_new_registration`: solo superusuarias activas. |
| `apps/accounts/services.py` (nuevo) | `deactivate_account`, `reactivate_account`, `erase_account`, `reassign_clients`, `close_sessions`, `erase_summary` (conteos para C-04 y C-07, sin datos). |
| `apps/accounts/permissions.py` | `clients_for_trainer` excluye las cuentas eliminadas. Ayudante `ensure_client_active`. |
| `apps/accounts/choices.py` (nuevo) | Motivos (11.2). |
| `apps/accounts/admin.py` | Sin borrar; `AccountAuditLog` de solo lectura; campos de solo lectura (12). |
| `apps/accounts/urls.py` | Rutas de la sección 4. |
| Vistas que escriben datos de una clienta (`assessments`, `memberships`, `payments`, `routines`, `health` del entrenador, `plans`) | Llaman a `ensure_client_active` (5.2, punto 8). |
| `apps/plans/services.py` y `jobs.py` | `request_plan` rechaza a una clienta inactiva o eliminada; el trabajo que está corriendo no guarda nada si la clienta ya no está activa. |
| `apps/plans/notifications.py` | `client_plan_approved` no escribe a una clienta inactiva. |
| `apps/payments/services.py` | Webhook de una cuenta eliminada (10). |
| `apps/payments/views.py` | `trainer_manual_payment_list`: la superusuaria ve además los pagos de cuentas eliminadas (`user__erased_at__isnull=False`), marcados como "Cuenta eliminada". `payment_receipt` de una lápida: solo la superusuaria. |
| `apps/public/views.py` | `about`: `is_active=True`. |
| `apps/payments/management/commands/purge_orphan_files.py` (nuevo) | Ver 7.4. Por defecto solo lista (`--dry-run`); borra con `--delete`. |
| `CLAUDE.md` | Sección nueva "Desactivar y eliminar cuentas" (al implementar). |

**Riesgo de choque con otras ramas:** `..\seguridad` y `..\ficha-basica` trabajan en paralelo y es probable que toquen `apps/accounts/views.py`, `admin.py` o las migraciones de `accounts`. Al integrar puede haber conflictos y hará falta renumerar la migración. La sesión principal decide el orden de las fusiones.

---

## 16. Pantallas (pedido a frontend-architect, cuando se apruebe)

El detalle va en `docs/contracts/requests.md`, "Cuentas: contrato C-01 a C-08". En resumen:

- C-01: lista de clientas con pestañas Activas / Desactivadas / Todas y la etiqueta "Desactivada".
- C-02: detalle de la clienta con la franja "Cuenta desactivada" y los botones Desactivar, Reactivar y Eliminar definitivamente, según `can_*`.
- C-03: confirmación para desactivar una clienta.
- C-04: confirmación para eliminar una clienta (resumen de lo que se borra y lo que se conserva, escribir el nombre, motivo y casilla).
- C-05: lista de entrenadores con filtro, etiquetas y botones.
- C-06 y C-07: confirmaciones para desactivar y eliminar un entrenador, con el selector de reasignación.
- C-08: pagos de cuentas eliminadas en la lista de pagos manuales, y "Cuenta eliminada" o "(desactivado)" donde se muestra quién hizo algo.

Hasta que lleguen las plantillas, las pruebas del backend usan plantillas de reemplazo (mismo patrón que `stub_missing_templates`).

---

## 17. Pruebas previstas (`apps/accounts/tests/test_account_lifecycle.py` y otras)

Permisos:
1. Una clienta, un anónimo o un entrenador que no es el asignado → 404 o redirección en cada ruta nueva. Un entrenador común en las rutas `/staff/...` y en `trainer_client_delete` → redirección a su panel con el mensaje de permiso.
2. Nadie puede actuar sobre una superusuaria, sobre su propia cuenta ni sobre una cuenta eliminada (404). El servicio rechaza dejar cero superusuarias activas, aunque se le llame directamente.
3. GET nunca cambia nada; POST sin CSRF → 403 (cliente de pruebas con `enforce_csrf_checks=True`).

Desactivar y reactivar:
4. Después de desactivar: la siguiente petición con la sesión vieja termina en el inicio de sesión, tanto con sesiones en la base como en caché. Iniciar sesión da el mensaje genérico. La recuperación de contraseña no envía correo.
5. Los avisos de WhatsApp en cola quedan `cancelled` `user_inactive`; no se encolan avisos nuevos. Los trabajos de IA en cola pasan a `failed` `client_inactive`; un trabajo que termina después no crea el plan.
6. Las vistas que escriben datos de una clienta desactivada se rechazan; las de lectura funcionan.
7. Entrenador: todas sus clientas (incluidas las desactivadas) pasan al destino; los planes `in_review` vuelven a `draft_ai` con un `PlanEvent` `released`; desaparece de "Nosotros" y de la lista para asignar. Un destino inválido (él mismo, inactivo, una clienta, un texto, vacío con clientas) → error de campo, y **nada** cambió (comprobar que la transacción se revirtió).
8. Reactivar recupera el inicio de sesión con la misma contraseña; un entrenador reactivado no recupera sus clientas.
9. Doble POST: el segundo no crea otra fila de auditoría ni cambia nada.

Eliminar:
10. Por cada una de las 30 claves foráneas: la fila queda borrada, conservada o apuntando a la lápida, según la tabla 7.1. Una prueba recorre `User._meta.related_objects` y **falla si aparece una clave foránea nueva** que no esté clasificada en el servicio. Así, quien agregue una tabla nueva tiene que decidir qué pasa con ella.
11. La lápida no tiene nombre, correo, teléfono, foto ni contraseña utilizable; el `username` original queda libre y se puede registrar de nuevo.
12. `ManualPayment` sigue existiendo (monto, fecha, método, plan) y sin `notes`; `Payment.raw_webhook` solo tiene las claves de la lista blanca. La superusuaria lo ve en la lista de pagos como "Cuenta eliminada"; un entrenador común no.
13. `confirm_name` equivocado, vacío o con otras tildes → no se borra nada. Con mayúsculas o espacios distintos → sí se borra.
14. `ack_payments` se exige solo cuando hay membresía vigente o un pago Wompi `pending`.
15. Las filas de `axes` del usuario original y los `LogEntry` del admin quedan sin el nombre.
16. La foto se borra del almacenamiento (almacenamiento en memoria en la prueba) solo si la transacción se confirma; si falla a la mitad (se fuerza una excepción), no se borra nada, ni filas ni archivos.
17. Un webhook de Wompi aprobado para una cuenta eliminada no crea membresía, guarda el pago y no vuelve a guardar los datos personales.
18. `AccountAuditLog`: una fila por acción, con actor, objetivo, motivo y conteo, sin texto libre. En el admin no se puede agregar, editar ni borrar. El botón "Eliminar" del admin de usuarios no existe.
19. Que no se rompa nada: la suite completa (`python manage.py test --settings=config.settings.development`) y `manage.py check`.
20. Contrapruebas: quitar la reasignación, el `PROTECT` o la verificación de la última superusuaria y comprobar que la prueba correspondiente falla.

---

## 18. Ley 1581 y conservación de los pagos **[A VALIDAR por abogado y contador]**

- **Derecho de supresión** (Ley 1581 de 2012, art. 8, literal e): la titular puede pedir que se borren sus datos. El responsable debe responder los reclamos en **15 días hábiles** (art. 15). "Eliminar definitivamente" es la herramienta para cumplirlo, y el motivo `holder_request` deja la constancia de que se atendió.
- **Excepción por deber legal** (Decreto 1377 de 2013, art. 9 y art. 11): la supresión no procede, o los datos se conservan, cuando hay una obligación legal o contractual de guardarlos. El Decreto 1377 hoy está compilado en el Decreto 1074 de 2015 (verificar el número de artículo vigente).
- **Conservación contable** (Ley 962 de 2005, art. 28): los libros y papeles del comerciante se conservan **10 años** desde la fecha del último asiento, documento o comprobante, en papel o en medio electrónico. La Superintendencia de Sociedades sostiene que este artículo reemplazó al art. 60 del Código de Comercio; el Consejo Técnico de la Contaduría Pública recomienda leer los dos juntos. En cualquier caso, el plazo es de 10 años. **Que ProFit Studio sea comerciante obligada a llevar contabilidad, y qué documentos son sus soportes contables, lo debe confirmar el contador.**
- **Prueba de la autorización** (Decreto 1377, art. 8): por eso se propone conservar `ConsentRecord`, sin datos personales y apuntando a la lápida.
- **Qué queda después de eliminar y por qué:** pagos (monto, fecha, método, plan, referencia: deber contable), consentimientos (prueba de la autorización), registro de accesos a la ficha y auditoría (seguridad y responsabilidad demostrada). Nada de eso identifica a la persona dentro del sistema. La referencia de Wompi sí permite identificarla **en el panel de Wompi**, que es otro responsable con sus propias obligaciones.
- **Datos fuera de la base:** copias de seguridad de Railway, correos ya enviados y registros de la plataforma. Conviene decirlo en la política de tratamiento ("los datos eliminados pueden permanecer hasta N días en copias de seguridad").

---

## 19. Preguntas abiertas (cada una con opciones y recomendación)

1. **Comprobantes de pago (fotos o PDF de transferencias) al eliminar una clienta.** La foto suele mostrar su nombre y su cuenta bancaria, así que contradice "pagos sin datos personales". Pero puede ser el soporte contable que la ley obliga a guardar 10 años.
   - a) Conservar el archivo en el bucket privado, visible solo para la superusuaria, y anotar que se guarda por obligación contable (**recomendado**, si el contador confirma que estos comprobantes son soporte contable).
   - b) Borrar el archivo y conservar solo monto, fecha y método (recomendado si el contador ya guarda copia en otro lado, por ejemplo con la factura).
   - c) Preguntar en cada eliminación con una casilla.
2. **Entrenadores comunes y sus clientas.** Hoy cualquier entrenador puede **borrar** en cascada a sus clientas.
   - a) Pueden **desactivar y reactivar** a sus clientas; eliminar definitivamente solo la superusuaria (**recomendado**: es irreversible, borra datos de salud y tiene efectos legales).
   - b) Solo pueden pedirle a la superusuaria (no hacen nada ellos).
   - c) Mantienen la eliminación definitiva (no recomendado).
3. **Cómo eliminar de verdad.**
   - a) Lápida: la fila de `User` queda vacía, sin datos personales (**recomendado**: no se pierde quién aprobó planes, registró pagos o abrió fichas, y los pagos no quedan sueltos).
   - b) Borrar la fila: hay que pasar 11 claves foráneas a `SET_NULL` o `PROTECT` con una migración, y se pierde la trazabilidad de los entrenadores eliminados.
4. **Ficha de una clienta desactivada en el panel.**
   - a) Solo lectura hasta que se reactive (**recomendado**: evita trabajar sobre alguien que no puede ver nada).
   - b) Se puede seguir editando.
5. **Membresía al desactivar a una clienta.**
   - a) No tocarla: las fechas siguen corriendo (**recomendado**: es lo más simple y no cambia nada de lo que ya pagó).
   - b) "Congelarla": al reactivar, se suman los días que estuvo desactivada.
6. **Cuánto tiempo se guardan la lápida, los consentimientos, el registro de accesos y la auditoría.**
   - a) Mientras se guarden los pagos (10 años) y sin borrado automático en esta fase; el abogado fija el plazo (**recomendado**).
   - b) Borrar consentimientos y registro de accesos junto con la cuenta (como hoy). Se pierde la prueba de la autorización.
7. **Correo a la persona al eliminar su cuenta.**
   - a) No enviar; la superusuaria responde por el mismo canal por donde llegó la solicitud (**recomendado**: no hay que guardar el correo después de la eliminación y no hay plantilla legal aprobada).
   - b) Casilla "Enviar confirmación por correo" en C-04, con un texto fijo y sin datos.
8. **Dónde se consulta la auditoría.**
   - a) En el admin, solo lectura (**recomendado**: no hace falta pantalla nueva).
   - b) Una pantalla en el panel ("Historial de cuentas").
9. **`is_active` e `is_superuser` en el admin de usuarios.** Hoy se pueden cambiar con una casilla, sin reasignar clientas ni auditoría.
   - a) Dejarlos de solo lectura en el admin; todo pasa por el panel (**recomendado**).
   - b) Dejarlos editables.
10. **Motivo obligatorio al eliminar.**
    - a) Obligatorio al eliminar y opcional al desactivar (**recomendado**: deja constancia de las solicitudes de supresión).
    - b) Siempre opcional.

---

## 20. Verificación de las fuentes (consultadas el 2026-11-16)

- Ley 962 de 2005, art. 28, "Racionalización de la conservación de libros y papeles de comercio": 10 años desde el último asiento, documento o comprobante; papel o medio electrónico. Función Pública: https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=17004
- Decreto 1377 de 2013, art. 8 (prueba de la autorización), art. 9 (la supresión no procede con deber legal o contractual) y art. 11 (conservar cuando lo exige una obligación legal o contractual; documentar la conservación y la supresión). Función Pública: https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=53646
- Relación entre el art. 28 de la Ley 962 y el art. 60 del Código de Comercio: Superintendencia de Sociedades, oficio 220-003200 (https://accounter.co/normatividad/oficios/comercial-oficio-220-003200-conservacion-de-libros-papeles-del-comerciante-de-sociedades-en-liquidacion) y concepto 377 del CTCP de 2023 (https://accounter.co/normatividad/la-conservacion-de-10-anos-de-los-soportes-contables-puede-ser-en-fisico-y-o-electronico-una-vez-cumplido-este-tiempo-se-pueden-elimina-concepto-377-ctcp-de-2023). Son fuentes secundarias: el abogado o el contador deben confirmarlas.
- Comportamiento de Django 5.2.19 (versión del proyecto): `ModelBackend.get_user` y `user_can_authenticate` rechazan las cuentas con `is_active=False`; `PasswordResetForm.get_users` solo devuelve cuentas activas con contraseña utilizable. Revisarlo en el código instalado al implementar.
- Claves foráneas: introspección con `apps.get_models()` en esta rama (2026-11-16): 17 en cascada y 13 con `SET_NULL` (sección 7.1).

---

## 21. Implementación (backend, 2026-11-17)

Respuestas aplicadas: 1a, 2a, 3a, 4a, 5a, 6a, 7a, 8a, 9a y 10a.

**Migraciones** (todas reversibles, sin migración de datos):

- `accounts/0005_account_lifecycle`: `User.deactivated_at`, `User.erased_at`, restricción `accounts_user_erased_is_inactive` (una cuenta con `erased_at` no puede tener `is_active=True`) y modelo `AccountAuditLog`.
- `payments/0005_payments_protect_user`: `ManualPayment.user` y `Payment.user` pasan a `PROTECT`; campo nuevo `ManualPayment.receipt_retained_at` («Comprobante conservado por obligación contable desde», respuesta 1a).
- `plans/0004_planevent_released`: opción `released` de `PlanEvent.action` (solo cambia las opciones).

**Archivos:** `apps/accounts/services.py` (casos de uso y `ERASE_POLICY`), `choices.py`, `forms.py` (confirmaciones), `permissions.py` (`ensure_client_active`, `reject_if_inactive`), `views.py`, `urls.py`, `admin.py`; `apps/payments/management/commands/purge_orphan_files.py`; esqueletos `templates/trainer/client_deactivate.html`, `trainer_deactivate.html` y `trainer_confirm_delete.html`; pruebas en `apps/accounts/tests/test_account_lifecycle.py`.

**Decisiones de implementación que precisan el contrato:**

1. **Sesiones (sección 9):** no se agregó una marca nueva ni una tabla. Con el motor de base de datos se leen **solo las sesiones vigentes** y se borran las de la persona; con Redis no se hace nada y vale el mecanismo principal (Django rechaza la cuenta inactiva en la siguiente petición). Probado con los dos motores.
2. **Solo lectura (respuesta 4a):** se bloquean también los GET de las pantallas que son solo formulario (editar clienta, valoración nueva, medida nueva o editada, membresía, pago manual, corregir ficha, editar opción de comida). El constructor de rutinas se puede abrir; sus acciones HTMX responden el fragmento de error (`X-Form-Error: 1`). **Archivar** un plan de una clienta desactivada sí se permite (limpia la bandeja; no escribe datos de ella). Tomar, aprobar, regenerar, generar y editar o revisar opciones de comida, no.
3. **Asignar entrenador** (`trainer_assign_client`) a una clienta desactivada sí se permite (sirve para reasignarlas); solo se ofrecen y aceptan entrenadores activos.
4. **Resumen del panel** (`trainer_dashboard`): cuenta solo clientas activas; las lápidas nunca cuentan como «sin asignar».
5. **Trabajos de IA:** uno en cola de una cuenta inactiva termina `failed` `client_inactive` **sin llamar a la IA y sin avisar** al entrenador. Uno que estaba corriendo no guarda el plan (se revisa la cuenta al bloquear el trabajo antes de guardar).
6. **Webhook de Wompi** de una cuenta eliminada: devuelve `erased_account` (nuevo resultado interno), guarda `approved` con el cuerpo reducido y no crea membresía.
7. **Admin:** además de `is_active` e `is_superuser`, `deactivated_at` y `erased_at` también son de solo lectura. `AccountAuditLog` solo lo ve una superusuaria activa (un entrenador con `is_staff` no).
8. **Métodos:** la comprobación del método va primero: un GET a una ruta solo POST responde 405 incluso sin sesión (no revela nada).
9. **C-04:** el contexto incluye además `summary.receipts` (comprobantes que se conservan).
10. **`purge_orphan_files`** vive en `apps/payments` e imprime solo conteos (el nombre de una foto puede llevar el nombre de la persona).

**Contrapruebas hechas (sección 17, punto 20):** sin la reasignación falla `test_deactivate_reassigns_all_clients_and_releases_plans`; sin la verificación de la última superusuaria falla `test_last_active_superuser_is_always_kept`; con `CASCADE` en `ManualPayment.user` falla `test_payments_are_protected_from_cascades`.

**Sigue [A VALIDAR]:** que los comprobantes sean soporte contable que se deba guardar 10 años (contador); el plazo de conservación de la lápida, los consentimientos, el registro de accesos y la auditoría (abogado, respuesta 6a); y el aviso en la política de tratamiento sobre las copias de seguridad.
