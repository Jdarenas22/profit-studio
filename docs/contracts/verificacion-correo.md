# Contrato: verificación del correo e invitaciones

- Estado: **IMPLEMENTADO (2026-11-24)**, rama `feature/verificacion-correo` (parte de `main` en `80b055a`). Pantallas V-01 a V-08 pendientes de frontend-architect (hoy hay esqueletos funcionales: `docs/contracts/requests.md`, «Verificación de correo: pantallas V-01 a V-08»).
- Autor: arquitecto-backend. Decisiones ya tomadas por la dueña: al crear una cuenta de clienta debe llegar un correo que confirme que el correo es real y que es una persona. Antes no existía ninguna verificación.
- **Migración reservada: `accounts 0007 ← accounts 0006`** (`0007_email_verification`: agrega `User.email_verified_at` y marca verificadas las cuentas existentes). Número anotado aquí; no se tocó `docs/COORDINACION.md`.
- Términos:
  - **Registro pendiente**: la persona se registró sola y todavía no confirmó su correo. La cuenta existe pero está **inactiva**.
  - **Invitación pendiente**: un entrenador creó la cuenta de la clienta y ella todavía no creó su contraseña. La cuenta está **activa** pero su contraseña es **inutilizable**.
  - **Verificada**: `email_verified_at` tiene fecha (confirmó el enlace o creó su contraseña con la invitación).

---

## 0. Resumen

1. **Registro público** (`register`): la cuenta nace con `is_active=False` y `email_verified_at` vacío; no inicia sesión. Se envía **un** correo con un enlace firmado de **24 horas**. La respuesta es siempre la pantalla «Revisa tu correo», exista o no el correo.
2. El enlace abre una pantalla con un botón «Confirmar mi correo»; el **POST** activa la cuenta (`is_active=True`, `email_verified_at=ahora`) y lleva al inicio de sesión con un mensaje. **No inicia sesión sola** (sección 11.4).
3. Recién al confirmar se avisa por correo a la superusuaria (`_notify_new_registration`).
4. **«Reenviar el correo»** (`verification_resend`): formulario por correo, respuesta genérica, límite por IP y por correo.
5. **Cuenta creada por el entrenador** (`trainer_add_client`): ya **no fija la contraseña**. La cuenta nace activa con contraseña inutilizable y la clienta recibe una **invitación** (enlace firmado de **72 horas**) para crear la suya; al hacerlo queda verificada. El entrenador la ve marcada «invitación pendiente» y puede **reenviar** la invitación.
6. **Cuentas existentes**: la migración marca a TODAS como verificadas (`email_verified_at = date_joined`): nadie queda bloqueado. Los entrenadores y superusuarias creados por `create_trainer` o «Agregar entrenador» quedan verificados al crearse.
7. **Turnstile** (Cloudflare) opcional en el registro y en «Reenviar el correo»; apagado sin claves.
8. `purge_unverified_accounts [--days 7] [--delete]` limpia las cuentas pendientes viejas y sin datos.

---

## 1. Alcance

### 1.1 Incluido

Registro público, confirmación del correo, reenviar, inicio de sesión de una cuenta pendiente, invitación del entrenador (crear y reenviar), visibilidad en el panel, Turnstile, comando de limpieza y la migración de datos.

### 1.2 Fuera de alcance (riesgos conocidos en la sección 12)

- Verificar un correo **nuevo** cuando la clienta o el entrenador lo cambian después (perfil o «Editar»): la cuenta conserva `email_verified_at`. Hoy el correo cambiado no se vuelve a verificar.
- Verificación para entrenadores (las crea quien administra).
- Verificación por WhatsApp o teléfono.
- CSP del hosting (el proyecto no define una; la sección 8.4 deja lo que habría que permitir).

---

## 2. Estados de una cuenta de clienta

| Estado | `is_active` | `email_verified_at` | `deactivated_at` | Contraseña | ¿Inicia sesión? | ¿La ve el panel? |
| --- | --- | --- | --- | --- | --- | --- |
| **Registro pendiente** | `False` | vacío | vacío | utilizable | No (el login avisa «confirma tu correo» solo con la contraseña correcta) | **No** (`clients_for_trainer` la excluye) |
| **Invitación pendiente** | `True` | vacío | vacío | inutilizable | No | Sí, marcada «Invitación pendiente» |
| **Activa y verificada** | `True` | fecha | vacío | utilizable | Sí | Sí |
| **Desactivada** | `False` | (lo que tenía) | fecha | (la que tenía) | No | Sí, en «Desactivadas» (sin cambios) |
| **Eliminada** (lápida) | `False` | — | fecha | inutilizable | No | No (sin cambios) |

- `User.is_pending_registration` e `User.is_invitation_pending` (propiedades) y `pending_registration_q()` (filtro de base de datos equivalente) implementan las dos primeras filas. Una cuenta **desactivada** siempre lleva `deactivated_at`, así que nunca se confunde con un registro pendiente.
- Las cuentas creadas por otros medios (admin de Django, `shell`, datos de demostración) con contraseña utilizable y `email_verified_at` vacío no son ni lo uno ni lo otro: se comportan como cuentas normales.
- `email_verified_at` es de solo lectura en el admin (igual que `is_active`).

## 3. Rutas

| Nombre | Ruta | Métodos | Quién | Qué hace |
| --- | --- | --- | --- | --- |
| `register` | `/accounts/register/` | GET, POST | anónimo | Registro. POST: crea la cuenta inactiva y envía el correo (sección 4). |
| `register_check_email` | `/accounts/register/check-email/` | GET | cualquiera | V-01 «Revisa tu correo». Misma pantalla exista o no el correo. |
| `email_verify` | `/accounts/verify/<token>/` | GET | cualquiera | V-02. El enlace del correo: muestra el botón «Confirmar mi correo». **No cambia nada.** |
| `email_verify_confirm` | `/accounts/verify/confirm/` | POST | cualquiera | Activa la cuenta. Recibe `token` en el cuerpo. Redirige a `login?next=/accounts/register/success/`. |
| `verification_resend` | `/accounts/verify/resend/` | GET, POST | cualquiera | V-04 «Reenviar el correo» (confirmación o invitación pendiente). |
| `invitation_accept` | `/accounts/invitation/<token>/` | GET | cualquiera | V-05. El enlace de la invitación: formulario para crear la contraseña. |
| `invitation_set_password` | `/accounts/invitation/accept/` | POST | cualquiera | Guarda la contraseña. Recibe `token`, `password1`, `password2` en el cuerpo. Redirige a `login`. |
| `trainer_client_resend_invitation` | `/accounts/trainer/clients/<pk>/resend-invitation/` | POST | entrenador de la clienta o superusuaria | Reenvía la invitación. Otro entrenador: 404. |
| `register_success` | `/accounts/register/success/` | GET | clienta con sesión | Sin cambios: ya no es la respuesta del registro; es a donde lleva el primer inicio de sesión tras confirmar. |

Todas las pantallas del enlace llevan `Cache-Control: no-store` y `Referrer-Policy: no-referrer`. Todos los POST exigen CSRF. `verify/resend/` y `verify/confirm/` van **antes** de `verify/<token>/` en `urls.py`; los tokens nunca llevan `/`.

## 4. Registro (`POST /accounts/register/`)

Orden de las comprobaciones (la primera que falla corta):

1. Límite de envíos por IP (`register-attempt`, 20 por hora): 429 con `Retry-After`.
2. **Turnstile**, si está configurado (sección 8): error `errors.captcha`, HTTP 200.
3. Validación de `RegisterForm` (nombre, apellido, correo, teléfono, usuario, contraseña con `validate_password`, etc.). **Un correo repetido ya NO es un error del formulario.** Un usuario ocupado sí (`errors.username`).
4. Límite de cuentas por IP (`register-success`, 5 por hora): 429. **También cuenta cuando el correo ya existía** (el límite no puede delatar si existe).
5. Según el correo:
   - **Correo nuevo**: crea la cuenta (`role='member'`, `is_active=False`, `email_verified_at` vacío) y envía el correo de confirmación (si la dirección no se pasó de su cuota por hora, sección 9).
   - **Correo que ya tiene cuenta**: no crea nada. Calcula un hash de contraseña (igual de lento que crear la cuenta) para que el tiempo de respuesta no delate que existe, y envía a esa dirección:
     - si había una cuenta pendiente (registro o invitación): el enlace nuevo que le falta;
     - si no: un aviso «Ya tienes una cuenta con este correo» con los enlaces de inicio de sesión y de recuperar la contraseña (sin enlace firmado).
6. Guarda en la sesión el correo escrito (solo para repetírselo a la persona en V-01) y redirige a `register_check_email`.

La respuesta (código, redirección y pantalla) **es idéntica** en los dos casos. Una cuenta pendiente que se vuelve a registrar **no se modifica**: conserva la contraseña y los datos del primer registro (si no, alguien podría pisar el registro pendiente de otra persona con una contraseña suya).

Si el envío del correo falla (SMTP caído), se registra una advertencia **sin enlace ni correo** y la respuesta no cambia; la persona usa «Reenviar el correo».

## 5. Correos

Texto plano, sin datos de salud, sin contraseñas, con el enlace absoluto (`request.build_absolute_uri`, por lo que respeta `ALLOWED_HOSTS` y https detrás del proxy). Plantillas en `templates/accounts/`:

| Correo | Plantillas | Cuándo | Vigencia |
| --- | --- | --- | --- |
| Confirmación | `email_verify.txt` / `email_verify_subject.txt` | Registro, reenviar, o registro repetido con una cuenta pendiente | 24 horas |
| Invitación | `email_invite.txt` / `email_invite_subject.txt` | «Agregar cliente», «Reenviar invitación», reenviar, o cambio de correo de una invitación pendiente | 72 horas. Incluye el **usuario** de la clienta |
| Aviso de cuenta existente | `email_existing.txt` / `email_existing_subject.txt` | Registro con un correo que ya tiene cuenta (no pendiente) | — |

Contexto de las plantillas: `user`, `url`, `valid_hours`, `login_url`, `reset_url`, `site_name`. Cuerpos con `{% autoescape off %}` (texto plano). El asunto nunca lleva datos de la persona.

## 6. Enlaces firmados

- `django.core.signing.dumps({'u': <id>, 'f': <huella>}, salt='profit.accounts.email-link.<propósito>')` y `loads(..., max_age=...)`. Propósitos: `verify` y `invite` (cada uno con su sal: uno no sirve como el otro).
- **Huella** = HMAC de `id | correo | is_active | email_verified_at | hash de la contraseña`. Al usar el enlace el estado cambia y la huella ya no coincide, así que **cada enlace sirve una sola vez** sin guardar nada. Cambiar el correo de la cuenta también lo invalida. Verificar y aceptar la invitación bloquean la fila (`select_for_update`) en una transacción.
- Motivos de fallo (`reason` de V-03): `expired` (firma buena, vencido), `used` (ya se usó, o la cuenta cambió: correo, estado), `invalid` (manipulado, cortado, otro propósito o demasiado largo: más de 512 caracteres).
- El estado de la cuenta se vuelve a comprobar: un enlace `verify` solo sirve a un registro pendiente y uno `invite` solo a una invitación pendiente.
- Los enlaces que no sirven responden **HTTP 200** con la pantalla V-03 (no un 4xx) para que el registro de Django (`django.request`) no escriba la ruta con el token. El token del POST va en el **cuerpo** (`sensitive_post_parameters`), no en la URL: un rechazo CSRF no lo deja en el log.
- Nunca se escribe un token ni un enlace en un log; los logs llevan `user_id` y el tipo de correo.
- Caducidades configurables con `EMAIL_VERIFICATION_MAX_AGE_SECONDS` (86400) e `INVITATION_MAX_AGE_SECONDS` (259200) en settings.

## 7. Inicio de sesión de una cuenta pendiente

`authenticate` rechaza una cuenta inactiva (`ModelBackend.user_can_authenticate`), así que el login falla y **django-axes cuenta un intento fallido** (5 por usuario+IP, 1 hora; un registro pendiente que intenta entrar sin confirmar puede bloquearse; el mensaje explica cómo seguir).

- Si el usuario es un **registro pendiente** y la contraseña es **correcta**: mensaje `warning` «Tu cuenta todavía no está activa: falta confirmar tu correo…», contexto `email_pending=True` y `resend_url`. Esto no revela nada nuevo (quien tiene la contraseña correcta ya es dueña de la cuenta).
- Contraseña incorrecta, usuario inexistente, cuenta desactivada o invitación pendiente: el mensaje genérico de siempre «Usuario o contraseña incorrectos.».
- Si axes bloqueó la petición (`request.axes_locked_out`), no se comprueba nada más (el mensaje no sirve de oráculo de contraseñas durante un bloqueo).
- Tras confirmar, el login lleva a `register_success` (`?next=`), la pantalla de «¿Qué sigue?» que antes era la respuesta del registro.
- Una cuenta con invitación pendiente no tiene contraseña: no hay nada que comprobar. Si perdió el correo usa «Reenviar el correo» (V-04) o el entrenador reenvía la invitación. «¿Olvidaste tu contraseña?» no le envía nada (Django solo escribe a cuentas con contraseña utilizable).

## 8. Antirrobots: Cloudflare Turnstile (opcional)

- Variables: `TURNSTILE_SITE_KEY` (pública, va en la página) y `TURNSTILE_SECRET_KEY` (secreta, solo servidor). **Solo con las dos** está encendido; con una sola queda apagado y `check_infra` avisa. Sin claves no se exige ni se muestra nada.
- Dónde: `POST register` y `POST verification_resend`. En el registro se comprueba **antes** de validar el formulario.
- Verificación en el servidor (`apps/accounts/turnstile.py`, solo `urllib`): `POST https://challenges.cloudflare.com/turnstile/v0/siteverify` con `secret`, `response` (campo `cf-turnstile-response`, máximo 2048 caracteres), `remoteip` (la IP real, `get_client_ip`). Tiempo de espera 5 s, sin seguir redirecciones, lectura limitada a 64 KB. Fuente: documentación de Cloudflare consultada el 2026-11-24.
- **Falla cerrado**: error de red, tiempo agotado, respuesta que no es JSON o `success` distinto de `true` → mensaje genérico `errors.captcha` («No pudimos comprobar que eres una persona…»), HTTP 200, sin crear nada. El log lleva solo el tipo de fallo; nunca el token ni la clave secreta.
- Pantalla: el contexto trae `turnstile_site_key` (vacío = no pintar nada). Cuando tiene valor, la plantilla pinta `<div class="cf-turnstile" data-sitekey="{{ turnstile_site_key }}"></div>` dentro del `<form>` y carga `https://challenges.cloudflare.com/turnstile/v0/api.js` (`async defer`).
- **8.4 Script sin SRI (excepción justificada).** El resto de librerías externas del proyecto lleva versión fija y `integrity`. `api.js` de Turnstile **no puede**: Cloudflare exige cargarlo desde su URL exacta y advierte que cachearlo o servirlo desde otro sitio lo rompe cuando publican actualizaciones (documentación consultada el 2026-11-24; la página no ofrece SRI). Mitigaciones: solo se carga en las dos pantallas que lo usan y solo si hay claves; el proyecto no define CSP hoy; `PERMISSIONS_POLICY` no necesita cambios según esa documentación (no la menciona; confirmar en el navegador al encenderlo). Si algún día se agrega una CSP: `script-src` y `frame-src` deben permitir `https://challenges.cloudflare.com` (y `connect-src 'self'` solo para el modo de preautorización, que no se usa).
- Prueba de humo con claves de prueba de Cloudflare antes de usar las reales; la clave secreta solo en Railway > Variables.

## 9. Límites de frecuencia (`apps/accounts/ratelimit.hit`, ventana fija)

| Qué | Clave | Límite | Ventana | Al pasarse |
| --- | --- | --- | --- | --- |
| Envíos del formulario de registro | IP | 20 (`REGISTER_ATTEMPTS_LIMIT`) | 1 h | 429 + `Retry-After` |
| Cuentas creadas (o intentos con correo existente) | IP | 5 (`REGISTER_SUCCESS_LIMIT`) | 1 h | 429 + `Retry-After` |
| Correos enviados a UNA dirección (confirmación, invitación o aviso, venga del registro o de «Reenviar») | correo en minúsculas | 3 (`EMAIL_VERIFICATION_EMAIL_LIMIT`) | 1 h | **En silencio**: no se envía y la respuesta no cambia |
| «Reenviar el correo» | IP | 10 (`EMAIL_VERIFICATION_RESEND_IP_LIMIT`) | 1 h | 429 + `Retry-After` |
| «Reenviar invitación» del entrenador | clienta | 3 (`INVITATION_RESEND_CLIENT_LIMIT`) | 1 h | Mensaje «Ya reenviaste esta invitación varias veces…» |
| «Reenviar invitación» del entrenador | quien reenvía | 30 (`INVITATION_RESEND_ACTOR_LIMIT`) | 1 h | Igual |

`ratelimit.hit` usa la caché de Django: con Redis el límite es global; sin él, cada proceso de gunicorn cuenta por separado. Si la caché falla **se permite** la petición (falla abierta, igual que el registro) y se registra.

## 10. Panel del entrenador

- Los **registros pendientes no son clientas**: `clients_for_trainer` los excluye, así que no salen en listas (ninguna pestaña), resúmenes ni búsquedas, y abrirlos da 404 (también para la superusuaria). `check_can_act` rechaza cualquier acción sobre ellos (`account_pending`) para que nadie los «reactive» sin verificar el correo.
- La **invitación pendiente** sí se ve y se puede trabajar (membresía, valoraciones, rutinas): marcada «Invitación pendiente» en la lista y en la ficha, con el botón «Reenviar invitación» (POST).
- `trainer_client_edit`: no se puede fijar una contraseña a una invitación pendiente (saltaría la verificación): error en `new_password`. Si se cambia el correo de una invitación pendiente, el enlace anterior muere y se envía una invitación nueva al correo nuevo.
- `trainer_add_client`: el campo `password` ya no existe (si llega, se ignora). Si el correo falla, la cuenta se crea igual y se avisa que use «Reenviar invitación».
- La superusuaria recibe el aviso de «Nuevo registro» al **confirmar** el correo (no al registrarse).

## 11. Decisiones y por qué

1. **Cuentas pendientes ocultas, no marcadas**: lo más simple y coherente. Un registro sin confirmar puede ser un robot o un error de tecleo; no debe ensuciar la lista ni dar trabajo al entrenador. La excepción es la invitación (la creó el entrenador a propósito).
2. **El enlace pide un clic (GET muestra, POST activa)**: los antivirus y las vistas previas de los buzones abren los enlaces; con un GET que activara, gastarían el enlace y activarían cuentas de robots solos. Cuesta un clic a la persona.
3. **Token en el cuerpo del POST y no en la URL**: evita que un rechazo (CSRF, error) deje el token en los registros.
4. **No inicia sesión sola tras confirmar**: protege contra el «pre-secuestro»: alguien registra el correo de otra persona con una contraseña suya; si al confirmar la dueña del correo quedara dentro de esa cuenta, el atacante (que conoce la contraseña) seguiría con acceso mientras ella carga datos. Sin sesión automática, ella llega al login, no conoce la contraseña y usa «Olvidé mi contraseña» (que sí funciona: la cuenta ya está activa y verificada).
5. **Una cuenta pendiente que se vuelve a registrar no cambia** (misma razón).
6. **La invitación incluye el usuario**: la clienta no lo eligió.

## 12. Riesgos conocidos y pendientes

- **Entrega del correo**: sin SMTP configurado (`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`) nadie puede confirmar su cuenta y el backend de consola escribe el correo **con el enlace** en el log de Railway. SMTP es requisito para abrir el registro. El plan de Gmail con contraseña de aplicación tiene un tope diario de envíos de Google (verificar el vigente antes de una campaña).
- **Tiempo de respuesta** de «Reenviar el correo»: con una cuenta pendiente se envía un correo (más lento) y sin ella no; el límite por IP lo frena pero no lo elimina. El registro sí está equilibrado (hash y envío en los dos caminos).
- **Concurrencia**: dos registros simultáneos con el mismo correo pueden crear dos cuentas pendientes (el correo no es único en la base). «Reenviar» usa la más reciente; la limpieza borra la que quede sin confirmar.
- **Correos cambiados después** no se vuelven a verificar (sección 1.2).
- **Sin CSP ni SRI para Turnstile** (sección 8.4).
- Las llamadas a `ratelimit.hit` fallan abiertas si la caché cae.
- Es una función de seguridad y de datos personales (correos): no es asesoría legal; si el aviso de tratamiento de datos del registro cambia por esto, que lo revise el abogado.

## 13. Comando `purge_unverified_accounts`

`python manage.py purge_unverified_accounts [--days 7] [--delete]`

- Candidatas: `role='member'`, inactivas, `email_verified_at` vacío, sin `deactivated_at` ni `erased_at`, no del equipo, creadas hace más de `--days` (mínimo 1; el enlace vence a las 24 horas).
- Sin `--delete` solo **cuenta**. Con `--delete` borra de verdad (sin lápida: nunca estuvieron activas) **solo** las que **ninguna fila de la base referencia** (se revisan todas las claves foráneas a `User`, incluidas las ocultas, y se vuelven a revisar con la fila bloqueada). Las que tengan cualquier relación se cuentan como «con datos» y no se tocan. También borra los intentos de inicio de sesión de django-axes con su usuario.
- La salida solo lleva cantidades (nunca usuarios ni correos). Se puede repetir sin riesgo. No se programa solo: en Railway puede ser un servicio con Cron Schedule semanal.
- `services.ERASE_POLICY` no cambia: el campo nuevo es una columna de `User`, no una clave foránea.

## 14. Migración `accounts 0007_email_verification`

1. `AddField User.email_verified_at` (fecha, nula).
2. `RunPython`: `UPDATE ... SET email_verified_at = date_joined WHERE email_verified_at IS NULL` para **todas** las cuentas (activas, desactivadas, eliminadas, entrenadoras, superusuarias). Un solo UPDATE; repetirlo no cambia nada. Al revertir, la columna se elimina sola.
3. `create_trainer` y «Agregar entrenador» crean con `email_verified_at=ahora`.

## 15. Variables de entorno

| Variable | Obligatoria | Qué hace |
| --- | --- | --- |
| `TURNSTILE_SITE_KEY` | No | Clave pública del widget. Con la secreta enciende Turnstile. |
| `TURNSTILE_SECRET_KEY` | No | Clave secreta (solo servidor; `check_infra` la tapa en su salida). |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | **Sí en producción** | Los correos de esta función salen por el mismo SMTP que la recuperación de contraseña. |

Ajustes opcionales en settings (sin variable de entorno): `EMAIL_VERIFICATION_MAX_AGE_SECONDS`, `INVITATION_MAX_AGE_SECONDS`, `EMAIL_VERIFICATION_EMAIL_LIMIT`, `EMAIL_VERIFICATION_EMAIL_WINDOW_SECONDS`, `EMAIL_VERIFICATION_RESEND_IP_LIMIT`, `EMAIL_VERIFICATION_RESEND_IP_WINDOW_SECONDS`, `INVITATION_RESEND_CLIENT_LIMIT`, `INVITATION_RESEND_ACTOR_LIMIT`, `INVITATION_RESEND_WINDOW_SECONDS`.

## 16. Pruebas

`apps/accounts/tests/test_email_verification.py` (registro, enlaces válidos/usados/vencidos/manipulados, enumeración, reenviar y sus límites, login pendiente con y sin axes, panel, invitación, reenvío del entrenador y 404 de otro entrenador, Turnstile apagado/encendido/falla de red, migración de datos, comando de limpieza, logs sin tokens). Se probaron contrapruebas quitando cada protección clave (huella del enlace, exclusión del panel, Turnstile cerrado, límite de axes, rama de correo existente, aviso temprano, permiso del entrenador) y la prueba correspondiente falló. Ajustadas las pruebas existentes que asumían cuenta activa al registrarse o contraseña fijada por el entrenador.
