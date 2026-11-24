# Contrato: transferir la administración

- Estado: **IMPLEMENTADO (2026-11-24)**, rama `feature/transferir-administracion`. Pantalla T-01 pendiente de frontend-architect (hoy hay un esqueleto funcional: `docs/contracts/requests.md`, «Transferir la administración: pantalla T-01»).
- Autor: arquitecto-backend. Decisiones ya tomadas por la persona (pedido de la dueña): poder cambiar quién es la superadministradora **sin entrar al servidor** (sin consola ni `shell` en producción).
- Referencia de estilo: `docs/contracts/cuentas-eliminar.md` (ciclo de vida de las cuentas). Aquel contrato dejó fuera cambiar quién es superusuaria («sigue siendo con `create_trainer` o con el admin», sección 1.2): este lo cubre.
- Términos:
  - **Superusuaria** o **administradora**: la cuenta con `role='trainer'` e `is_superuser=True`. Hoy es una sola (la dueña). Ve todas las clientas y los pagos, gestiona entrenadores y es la única que asigna clientas.
  - **Entrenador común**: `role='trainer'`, `is_superuser=False`, `is_staff=True`. Ve solo las clientas que tiene asignadas.
  - **Transferir**: la superusuaria le entrega la administración a otro entrenador y pasa a ser un entrenador común. No se agrega una segunda administradora: se cambia de persona.

---

## 0. Resumen

1. Una pantalla nueva en el panel (`Entrenadores > Transferir la administración`), solo para la superusuaria.
2. Ella elige a un entrenador activo, escribe **su propia contraseña de nuevo** y marca una casilla de confirmación.
3. En una transacción: el entrenador elegido pasa a `is_superuser=True` e `is_staff=True`; ella pasa a `is_superuser=False` (e `is_staff=True`, como cualquier entrenador común).
4. Sus clientas **no se reasignan**: ella conserva las que tiene (sigue siendo entrenadora).
5. Queda una fila en `AccountAuditLog` con la acción `transfer_admin` (quién entrega, quién recibe).
6. Después de transferir, ella ya no ve Pagos ni Entrenadores y vuelve a su panel de entrenadora.
7. Siempre queda exactamente una superusuaria activa después del traspaso; el servicio lo comprueba dentro de la transacción.
8. `create_trainer --superuser` (que `start.sh` ejecuta en cada despliegue) deja de volver a promover a la antigua administradora cuando ya hay otra superusuaria activa (sección 9).

---

## 1. Alcance

### 1.1 Incluido

- La ruta, el formulario, el servicio y la auditoría del traspaso.
- La protección para que un despliegue no deshaga el traspaso (sección 9).

### 1.2 Fuera de alcance

- Tener **dos** administradoras a la vez: el traspaso cambia de persona, no agrega otra.
- Que un entrenador común pida o reciba la administración sin que la actual la entregue: la persona que recibe no acepta nada en la pantalla; basta con que la entregue una superusuaria.
- Desactivar o eliminar a la superusuaria (sigue prohibido: `docs/contracts/cuentas-eliminar.md`, sección 2). Para sacar del todo a la antigua administradora, primero se transfiere y después la nueva administradora la desactiva o la elimina como a cualquier entrenador.
- Cambiar `is_superuser` desde el admin de Django (sigue de solo lectura).
- Recuperar la administración si la persona que la recibe pierde el acceso a su cuenta: usa la recuperación de contraseña normal (correo). Solo si no hubiera ninguna superusuaria activa, `create_trainer --superuser` vuelve a funcionar desde el servidor (sección 9).

---

## 2. Ruta

| Nombre | Ruta | Métodos | Quién | Qué hace |
| --- | --- | --- | --- | --- |
| `trainer_admin_transfer` | `/accounts/trainer/staff/transfer-admin/` | GET, POST | solo la superusuaria (`@superuser_required`) | GET: muestra el formulario (T-01). POST: transfiere. |

Respuestas por tipo de visitante (mismas reglas del resto del panel):

| Quién llama | Respuesta |
| --- | --- |
| Anónimo | Redirige al inicio de sesión. |
| Clienta | 403 (`accounts/forbidden.html`). |
| Entrenador común (no superusuaria) | Redirige a `trainer_dashboard` con «No tienes permiso para acceder a esta sección.» (incluye a la antigua administradora justo después de transferir). |
| Superusuaria | GET 200; POST según la sección 3. |
| Cualquier otro método (PUT, PATCH, DELETE) | 405. |

Todas las respuestas llevan `Cache-Control: no-store` (`never_cache`). El POST exige CSRF (sin token → 403). El GET nunca cambia nada.

La ruta cuelga de `trainer/staff/` junto a las demás de «Gestión de entrenadores».

## 3. Campos del POST

| Campo | Obligatorio | Reglas |
| --- | --- | --- |
| `new_admin` | Sí | Id de una cuenta de la lista de elegibles: `role='trainer'`, `is_active=True`, sin `erased_at`, `is_superuser=False` y distinta de quien entrega. Texto de 1 a 18 dígitos; cualquier otro valor (vacío, texto, negativo, un id que no existe, una clienta, una superusuaria, una cuenta desactivada o eliminada, ella misma) es un error del campo, nunca un 500. |
| `password` | Sí | La contraseña **de quien entrega**, tal como la escribe (no se recorta). Máximo 128 caracteres. Si no coincide: error genérico en el campo. Nunca se vuelve a enviar a la plantilla. |
| `confirm` | Sí | Casilla marcada (`on`): «Entiendo que dejaré de ser la administradora y que solo quien la recibe puede devolvérmela». |

Con errores de formulario o de contraseña, la vista vuelve a pintar la misma pantalla (HTTP 200) con `errors` y `form` (solo `new_admin`; **nunca** `password`). Si sale bien: `messages.success` y redirige a **`trainer_dashboard`** (patrón POST → redirección → GET).

**Por qué `trainer_dashboard`:** es una página que cualquier entrenador ve. La antigua administradora acaba de perder Pagos y Entrenadores; mandarla a `trainer_staff_list` la devolvería a su panel con un aviso de «sin permiso» justo después de un éxito.

## 4. Reglas del servicio (`apps/accounts/services.py::transfer_admin(actor, target, password)`)

Las reglas viven en el servicio, no solo en la vista (defensa en profundidad), y se comprueban **con las filas ya bloqueadas** dentro de la transacción:

1. Una sola transacción (`transaction.atomic`) con `select_for_update()` sobre **las dos filas** (`actor` y `target`), en orden de id, para que dos traspasos cruzados no se bloqueen entre sí. Si dos clics llegan a la vez, el segundo encuentra que ella ya no es superusuaria y se rechaza.
2. Quien entrega: `role='trainer'`, `is_superuser=True`, `is_active=True` y sin `erased_at`. Si no → `not_allowed`.
3. Quien recibe: distinto de quien entrega (`account_self`), no superusuario (`account_protected`), `role='trainer'` (`not_allowed`), activo y sin `erased_at` (`target_unavailable`, error del campo `new_admin`).
4. Contraseña: `check_password` sobre la fila bloqueada de quien entrega. Si falla (o llega vacía) → `password_invalid`, error del campo `password`, y un aviso en el log **con el id, nunca con lo escrito**.
5. Cambios:
   - `target`: `is_superuser=True`, `is_staff=True`.
   - `actor`: `is_superuser=False`, `is_staff=True`.
6. Se comprueba que quede al menos una superusuaria activa (`last_superuser`); si no, se revierte todo.
7. Fila en `AccountAuditLog` (sección 7).

### 4.1 Por qué `is_staff=True` para quien entrega

Los entrenadores comunes ya tienen `is_staff=True`: así los crean `create_trainer` y «Agregar entrenador/a» (`trainer_create_trainer`). Con `is_superuser=False` pueden entrar a `/admin/` pero no ven ni pueden hacer nada allí (Django solo les da lo que tengan asignado, y las apps del proyecto restringen sus administraciones a la superusuaria). Dejarla con `is_staff=True` la hace **idéntica a cualquier otro entrenador**. Apagarlo no ganaría seguridad y sería una diferencia más que explicar.

## 5. Qué cambia y qué no

| Tema | Después del traspaso |
| --- | --- |
| Quien recibe | Ve todas las clientas, Pagos y Entrenadores; asigna clientas; entra al admin con todos los permisos (incluida la credencial profesional de los entrenadores, que solo la superusuaria asigna); recibe los avisos por correo y por WhatsApp que van a «la superusuaria» (registro nuevo, alertas de la cuenta de WhatsApp, etc.). |
| Quien entrega | Es un entrenador común: solo ve sus clientas asignadas; ya no ve Pagos ni Entrenadores en el menú; los avisos de superusuaria dejan de llegarle. |
| Clientas | **No se reasigna ninguna.** La antigua administradora conserva las que tiene asignadas. Las clientas sin asignar (auto-registradas) las ve ahora la nueva administradora. |
| Contraseñas, correo, usuario, foto, biografía | Sin cambios. |
| `is_active`, `deactivated_at`, `erased_at` | Sin cambios. |
| Sesiones | No se cierra ninguna (ver abajo). |
| `assigned_trainer`, planes `in_review`, avisos en cola, trabajos de IA | Sin cambios. |
| Página pública «Nosotros» y portada | **Efecto visible para el público:** «Nosotros» (`templates/public/about.html`) pinta la insignia «Entrenadora Principal» a quien tenga `is_superuser`, así que **la insignia pasa a la nueva administradora** y la antigua la pierde. Los textos fijos de la portada (nombre y foto de la dueña) son contenido y no cambian. Si la dueña quiere conservar la insignia aunque ya no administre, hay que separarla de `is_superuser` (campo propio): es una decisión aparte. |
| Admin de Django | `is_superuser` e `is_active` siguen de solo lectura. El traspaso no se hace desde allí. |

**Sesiones.** Django vuelve a cargar la cuenta desde la base en **cada petición**. Por eso el cambio rige de inmediato en las sesiones que ya estaban abiertas de las dos personas, y `is_superuser` no forma parte del hash de la sesión (solo la contraseña), así que nadie queda desconectada y no hace falta rotar ni invalidar nada. Quien necesite cortar el acceso de la persona anterior (por ejemplo, por seguridad) la desactiva desde `Entrenadores` después de transferir: eso sí cierra sus sesiones.

## 6. Errores y mensajes

| Situación | Dónde sale | Texto |
| --- | --- | --- |
| Éxito | `messages` (éxito) | «Traspasaste la administración a {nombre}. Sigues como entrenador/a con tus clientas, pero ya no ves Pagos ni Entrenadores.» |
| `new_admin` vacío o no es un número | error de campo | «Elige a quién entregar la administración.» |
| `new_admin` no es elegible (no existe, ya es superusuaria, clienta, desactivada, eliminada, ella misma) | error de campo | «Esa persona ya no está disponible. Elige otra.» |
| `password` vacía | error de campo | «Escribe tu contraseña para confirmar.» |
| `password` incorrecta | error de campo | «La contraseña no es correcta.» |
| `confirm` sin marcar | error de campo | «Marca la casilla para confirmar que entiendes el cambio.» |
| Demasiados intentos (sección 8) | `messages` (error), HTTP 429 con `Retry-After` | «Demasiados intentos. Espera unos minutos e inténtalo de nuevo.» |
| Quien entrega ya no es superusuaria (carrera o doble clic) | `messages` (error), redirige a `trainer_dashboard` | «No se puede hacer esto con esta cuenta.» |
| Fallo inesperado | `messages` (error), redirige a `trainer_dashboard` | «No pudimos completar la acción. No se cambió nada. Inténtalo de nuevo.» (el detalle va al logger, nunca a la pantalla) |

Los errores de campo van en `errors` con las claves `new_admin`, `password` y `confirm`.

## 7. Auditoría

`accounts.AccountAuditLog` (el modelo de `docs/contracts/cuentas-eliminar.md`, sección 11), una fila por traspaso:

| Campo | Valor |
| --- | --- |
| `actor` | Quien entrega (la antigua administradora). |
| `target` | Quien recibe (la nueva administradora). |
| `target_role` | `trainer` |
| `action` | `transfer_admin` (acción nueva, «Transferir la administración») |
| `reason` | vacío (no hay motivo en esta acción) |
| `reassigned_to` / `reassigned_count` | vacío / 0 (no se reasigna nada) |
| `created_at` | fecha y hora |

Sin nombres, correos ni texto libre. Se consulta en el admin, de solo lectura, y solo la ve la superusuaria vigente. No se agrega ninguna clave foránea nueva a `User` (la prueba `ForeignKeyInventoryTests` sigue cumpliéndose).

Los logs del servidor llevan solo ids: `admin transferred actor_id=… target_id=…`, `admin transfer rejected code=password_invalid actor_id=…` y `admin transfer rate limited actor_id=…`. Nunca la contraseña ni nombres.

## 8. Seguridad (OWASP)

- **Control de acceso:** `@superuser_required` en la vista y las mismas reglas en el servicio, con las filas bloqueadas y releídas.
- **Reautenticación:** contraseña de quien entrega, escrita de nuevo. Protege contra una sesión abierta que alguien más aprovecha.
- **Límite de intentos:** por cuenta que entrega, **5 intentos cada 15 minutos** (`TRANSFER_ADMIN_ATTEMPTS_LIMIT`, `TRANSFER_ADMIN_WINDOW_SECONDS`; ajustables en settings), con el mismo contador simple del registro público (`apps/accounts/ratelimit.py`, sobre la caché de Django: compartido con Redis; con la caché local cada proceso de gunicorn cuenta por separado y falla abierto si la caché cae). Solo cuentan los envíos que pasan el formulario (un error de tecleo o una casilla sin marcar no gasta intentos). Pasado el límite responde 429 aunque la contraseña sea correcta. **No se usa `django-axes`:** axes cuenta intentos de `authenticate()`/inicio de sesión y aquí se usa `check_password`; usar `authenticate()` sumaría fallos al bloqueo del inicio de sesión de la dueña (5 fallos = una hora sin poder entrar) y es un efecto que no queremos.
- **CSRF** en el POST (token en el formulario). El GET no cambia nada.
- **La contraseña nunca vuelve a la pantalla:** la vista no la pasa a la plantilla, y el campo es `type="password"` con `autocomplete="current-password"`.
- **Entradas validadas:** `new_admin` con patrón numérico y búsqueda en la lista de elegibles (ORM, sin SQL armado a mano); `password` con longitud máxima; `confirm` booleano.
- **Errores:** mensajes genéricos; la traza va al logger.
- **Doble clic y carreras:** el botón se deshabilita al enviar (frontend) y el servicio es seguro aunque llegue dos veces (la segunda ya no es superusuaria).
- **Nadie se queda sin administradora:** `last_superuser` dentro de la transacción.
- **Stateless:** nada se guarda en memoria del servidor salvo el contador de intentos (caché, con caducidad).

## 9. Despliegue: `start.sh` no debe deshacer el traspaso

`start.sh` (paso 5) ejecuta `python manage.py create_trainer --username yiseth … --superuser` **en cada despliegue** cuando hay una contraseña inicial en las variables de Railway. Antes de este cambio, ese comando volvía a poner `is_superuser=True` a la cuenta `yiseth` si ya existía sin serlo, y la **creaba de nuevo** como superusuaria si su usuario había quedado libre (por ejemplo, tras eliminarla). Resultado: el siguiente despliegue deshacía el traspaso y dejaba dos superusuarias.

`create_trainer --superuser` ahora **no hace nada** (avisa y termina con código 0) si ya existe **otra** superusuaria activa. Sin ninguna otra (arranque inicial, o recuperación cuando no quedó ninguna), funciona exactamente como antes. Las pruebas cubren los dos sentidos. No se toca `start.sh`.

Opcional, después de transferir: quitar `TRAINER_INITIAL_PASSWORD` (y `TRAINER_PASSWORD`) de las variables de Railway si ya no se necesitan.

## 10. Migración

`accounts/0006_transfer_admin_action` (depende de `accounts/0005_account_lifecycle`):

- `AccountAuditLog.action`: `AlterField` con la opción nueva `transfer_admin` **y `max_length` de 12 a 20**. El código `transfer_admin` mide 14 caracteres y la columna medía 12, así que no basta con cambiar las opciones (las opciones solas no tocan la base; el ancho sí). Ensanchar un `varchar` en PostgreSQL no reescribe la tabla ni pierde datos. Es reversible. Sin migración de datos.

## 11. Pantalla pedida a frontend

T-01: `templates/trainer/admin_transfer.html` (hoy esqueleto del backend). Contexto, textos y el enlace que falta desde la lista de entrenadores: `docs/contracts/requests.md`, «Transferir la administración: pantalla T-01».

## 12. Pruebas

`apps/accounts/tests/test_transfer_admin.py` (41 pruebas): permisos y métodos, CSRF, contexto de la pantalla, éxito (banderas, auditoría, redirección, clientas intactas, contraseñas intactas, sesiones abiertas), contraseña incorrecta (y que no se devuelve), objetivos no válidos (11 casos), carrera (se desactiva mientras tanto), falla a la mitad (todo o nada), límite de intentos, servicio llamado directo (actor releído, nunca cero superusuarias), idempotencia ante doble envío, ida y vuelta, y `create_trainer` en un despliegue posterior.

Contrapruebas hechas (quitar una pieza y comprobar que la prueba falla): sin `check_password`; el actor sigue como superusuaria; sin la verificación de la última superusuaria; sin auditoría; sin límite de intentos; sin `@superuser_required`; `create_trainer` que vuelve a promover; actor sin releer; `is_staff` del actor apagado.

## 13. Pendientes

- **Frontend:** reemplazar el esqueleto T-01 y agregar el enlace «Transferir la administración» en la lista de entrenadores (`trainer_list.html`), solo visible para la superusuaria.
- **A decidir por la persona (opcional):** si quiere que la superusuaria nueva reciba un aviso por correo al recibir la administración. No se implementó: el correo del traspaso no tiene texto aprobado y la nueva administradora ve el cambio al entrar.
