# Despliegue de ProFit Studio — Railway + Cloudflare

Alcance actual de la persona: aplicación única publicada desde su PC sin contratar hosting ni dominio.
El arranque vigente está en [Ejecución integrada](EJECUCION-INTEGRADA.md). Este documento conserva
la operación opcional de Railway; sus servicios no son requisitos para usar las APIs o publicar el PC.


Guía de referencia para tener la web publicada y conectada. **Nunca** pegues valores secretos en este archivo, en el repositorio ni en el chat: las claves viven solo en Railway (Variables) y en tu `.env` local (que git ignora).

## 1. Cómo encaja todo

```text
GitHub  Jdarenas22/profit-studio  (rama main)
   │  cada merge a main dispara un despliegue
   ▼
Railway  proyecto «alluring-dedication» · entorno «production»
   ├─ servicio  profit-studio   (Django + gunicorn, arranca con start.sh)
   └─ servicio  Postgres        (base de datos; Railway inyecta DATABASE_URL)
   │
   ▼
Cloudflare R2  (archivos subidos)
   ├─ bucket  profitstudio-media     → PÚBLICO (r2.dev): imágenes y videos de ejercicios, fotos de perfil
   └─ bucket  profitstudio-receipts  → PRIVADO: comprobantes de pago (solo URLs firmadas de 5 min)
```

- Dirección pública actual: `https://profit-studio-production.up.railway.app`
- Healthcheck de Railway: `GET /health/` → `{"status": "ok"}` (no toca la base de datos).
- `start.sh` hace, en este orden: `migrate` (si falla, **no arranca**) → `collectstatic` (solo si falta `staticfiles/staticfiles.json`; si falla, **no arranca**) → crea la superusuaria `yiseth` (solo si existe `TRAINER_INITIAL_PASSWORD`) → `load_initial_data` → `gunicorn`.
- Los servicios de Railway están en modo «sleeping» (App Sleeping): tras 5-10 min sin tráfico saliente (según la documentación de Railway, consultada el 2026-11-20) Railway detiene el servicio web **y** Postgres, y los despierta con la primera visita, que tarda entre 10 y 20 s. «Sleeping» en el panel **no** significa desconectado. Para que el despertar sea fiable, `start.sh` espera a que Postgres acepte conexiones antes de migrar (hasta `DB_WAIT_SECONDS`, 90 s por defecto). Si prefieres respuesta inmediata siempre, desactiva «Serverless» en Settings de cada servicio (los deja siempre encendidos y cuesta más).

## 2. Estado verificado (2026-10-08)

| Pieza | Estado |
| --- | --- |
| Repositorio GitHub ↔ Railway | Conectado; último despliegue de `main` exitoso |
| Base Postgres | Conectada (`DATABASE_URL` definida; `migrate` corre sin errores en cada arranque) |
| Cloudflare R2: bucket de media | Existe, acceso público por `r2.dev` activo |
| Cloudflare R2: bucket de comprobantes | Existe, **sin** acceso público (correcto) |
| `R2_PUBLIC_URL` | **Corregida el 2026-10-08**: apuntaba a una URL que no era la del bucket y devolvía 404 para archivos existentes |
| Correo (Gmail) | Variables definidas; la entrega real no se ha probado desde aquí |
| Wompi (pagos online) | **Faltan las 4 claves**: los pagos online no funcionan hasta definirlas |
| Superusuaria `yiseth` | Falta `TRAINER_INITIAL_PASSWORD`: si la cuenta no existe, no se crea |
| Función de salud de la clienta | Apagada (`HEALTH_FEATURES_ENABLED` sin definir = apagada), a propósito |

Esto se comprobó leyendo los **nombres** de las variables y la configuración de los buckets. Que una clave sea válida solo lo confirma `python manage.py check_infra` (sección 6).

## 3. Variables de entorno en Railway

Se editan en Railway → servicio `profit-studio` → **Variables**, o con la CLI: `railway variable set NOMBRE=valor --service profit-studio`. Cada cambio provoca un redespliegue.

### Obligatorias (sin ellas la app no arranca o no funciona)

| Variable | Qué es | De dónde sale |
| --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | Siempre `config.settings.production` | Fija |
| `SECRET_KEY` | Clave secreta de Django, larga y aleatoria | La generas tú (no la reutilices de ningún ejemplo) |
| `ALLOWED_HOSTS` | Dominios permitidos, separados por coma, **sin `*`**. Incluye `healthcheck.railway.app` | Tu dominio de Railway y, si lo agregas, tu dominio propio |
| `DATABASE_URL` | Conexión a Postgres | La inyecta Railway al enlazar el servicio Postgres |

### Cloudflare R2 (archivos)

| Variable | Qué es | De dónde sale |
| --- | --- | --- |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Credenciales S3 del token de R2 | Cloudflare → R2 → **Manage R2 API Tokens** → crear token con permiso *Object Read & Write* limitado a los dos buckets |
| `R2_ENDPOINT_URL` | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` (API S3, **no** es una URL pública) | El Account ID aparece en `wrangler whoami` |
| `R2_BUCKET_NAME` | `profitstudio-media` | Nombre del bucket público |
| `R2_PUBLIC_URL` | URL pública del bucket de media: solo `https://pub-….r2.dev`, **sin ruta ni nombre de bucket** | `wrangler r2 bucket dev-url get profitstudio-media` |
| `R2_RECEIPTS_BUCKET_NAME` | `profitstudio-receipts` | Nombre del bucket privado |

Lo que el código hace cumplir: una `R2_PUBLIC_URL` mal formada detiene el arranque, y los comprobantes solo salen por la vista protegida `payment_receipt` con URLs firmadas. Lo que depende de ti en Cloudflare: el bucket de comprobantes **no** debe tener acceso `r2.dev` ni dominio público (compruébalo con los comandos de la sección 4).

### Wompi (pagos online)

| Variable | De dónde sale |
| --- | --- |
| `WOMPI_PUBLIC_KEY`, `WOMPI_PRIVATE_KEY` | Panel de Wompi → Desarrolladores → Llaves |
| `WOMPI_INTEGRITY_SECRET`, `WOMPI_EVENTS_SECRET` | Panel de Wompi → Desarrolladores → Secretos |

Además, en el panel de Wompi registra la **URL de eventos (webhook)**: `https://profit-studio-production.up.railway.app/payments/webhook/`. Sin las claves de Wompi la web funciona, pero «Pagar online» no.

### Correo (recuperación de contraseña)

| Variable | Qué es |
| --- | --- |
| `EMAIL_HOST_USER` | Cuenta de Gmail que envía los correos |
| `EMAIL_HOST_PASSWORD` | **Contraseña de aplicación** de Gmail (Cuenta de Google → Seguridad → Contraseñas de aplicaciones), no la contraseña normal |
| `EMAIL_HOST`, `EMAIL_PORT`, `DEFAULT_FROM_EMAIL` | Opcionales (por defecto `smtp.gmail.com`, `587` y `ProFit Studio <EMAIL_HOST_USER>`) |

Si `EMAIL_HOST_USER` no está definida, los correos **no se envían**: se imprimen en los logs de Railway.

### Opcionales

| Variable | Para qué |
| --- | --- |
| `TRAINER_INITIAL_PASSWORD` | Clave de la superusuaria `yiseth`. `start.sh` solo crea la cuenta si no existe; si ya existe, no toca su clave. Después de crearla puedes quitar la variable |
| `WHATSAPP_NUMBER`, `WHATSAPP_LINK`, `INSTAGRAM_URL` | Datos de contacto (ya tienen valores por defecto en el código) |
| `HEALTH_FEATURES_ENABLED` | `true` enciende «Mis medidas» y la ficha de salud de la clienta. Ver sección 7 antes de activarla |
| `PRIVACY_POLICY_URL` | Enlace a la política de tratamiento de datos que se muestra junto al consentimiento |
| `REDIS_URL` | Caché compartida entre los 2 workers (límite de intentos de login). Sin ella se usa memoria local de cada proceso |
| `DB_CONN_MAX_AGE` | Segundos que cada worker reutiliza su conexión a Postgres (entero ≥ 0). **Sin definir: `0` = una conexión nueva por petición** (el servicio puede dormirse con App Sleeping). Con `60` las páginas con base de datos responden ~200 ms más rápido, pero **el servicio deja de dormirse**: ponerla al pasar a un plan de pago. Un valor que no sea un entero ≥ 0 detiene el arranque. Ver sección 11 |
| `AI_PLANS_MODE` | Planes con IA: `off` (por defecto), `simulated` (borradores de ejemplo, sin costo, no se aprueban) o `live` (Gemini). Ver sección 7b |
| `AI_PLANS_WORKER` | **Apagado por defecto (`false`).** `true` = la web no lanza hilos de IA: deja el trabajo en cola y lo toma un servicio trabajador aparte (`python manage.py process_plan_jobs --loop`). Ese servicio mantiene despierto a Postgres y gasta crédito las 24 horas: **no usarlo en el plan gratuito**; es decisión del dueño. Solo `true`/`false`: otro valor detiene el arranque. Ver sección 12 |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_ACCESS_TIER` | Obligatorias en `live`; Free requiere `GEMINI_FREE_TIER_CONFIRMED=true` y facturación false. El nivel pago requiere confirmación de facturación explícita |

## 4. Cloudflare R2: comandos útiles

Con `wrangler` (ya inicia sesión con tu cuenta):

```text
wrangler r2 bucket list
wrangler r2 bucket dev-url get profitstudio-media        # URL pública → va en R2_PUBLIC_URL
wrangler r2 bucket dev-url get profitstudio-receipts     # debe decir que está DESACTIVADA
wrangler r2 bucket domain list profitstudio-receipts     # no debe haber dominios conectados
```

## 5. Primer despliegue o cambios de configuración

1. Haz merge a `main` (o cambia una variable): Railway construye y despliega solo.
2. Mira el avance: `railway deployment list --service profit-studio` (estados `BUILDING` → `DEPLOYING` → `SUCCESS`).
3. Revisa el arranque: `railway logs --service profit-studio`. Deben salir `Base de datos lista`, `migrate OK`, el bloque de `check_infra`, `staticfiles ya generado en el build` (o `collectstatic OK` si lo repitió) y `Listening at …`.
4. Comprueba `https://<tu-dominio>/health/`. Si falla justo al arrancar, espera unos segundos: el servicio estaba despertando.
5. Si algo sale mal, en Railway → Deployments puedes volver al despliegue anterior (rollback).

## 6. Verificar que todo está conectado: `check_infra`

Comando de solo lectura que **nunca imprime valores secretos**:

```text
python manage.py check_infra            # base de datos, R2, Wompi, correo (solo configuración), superusuaria
python manage.py check_infra --smtp     # además autentica contra el servidor de correo, sin enviar nada
python manage.py check_infra --json     # misma salida en JSON
```

Muestra una tabla OK / AVISO / ERROR con la acción sugerida y termina con código distinto de cero si hay algún ERROR.

**Contra producción:** `start.sh` ejecuta `check_infra` en **cada arranque** (sin `--smtp`, sin bloquear el arranque) y deja la tabla en los logs. Después de cada despliegue míralo con:

```text
railway logs --service profit-studio
```

Busca el bloque «Revisión de infraestructura — ProFit Studio» y revisa que no haya filas `[ERROR]`. Para comprobar el correo de verdad, pide una recuperación de contraseña desde la web publicada y mira si llega el mensaje (con el correo en consola, en los logs aparece impreso).

**Lo que NO funciona desde tu equipo (probado el 2026-10-09):**

- `railway run python manage.py check_infra`: ejecuta el comando en tu computador, y tu entorno local (SQLite, sin `psycopg2` ni `boto3`; Windows bloquea esos controladores) no puede conectarse a la base interna de Railway ni a R2.
- `railway ssh --service profit-studio -- <comando>`: aunque se registre una llave SSH, el servidor respondió con un resumen genérico de la cuenta en lugar de ejecutar el comando dentro del contenedor. No se logró ejecutar comandos en producción por esta vía.
- No se ha verificado que el panel web de Railway ofrezca un shell del servicio; no cuentes con él.

## 7. Antes de encender la función de salud

La función viene **apagada** a propósito. No la actives hasta que:

1. Un abogado apruebe los textos de consentimiento (el borrador tiene marcadores `[RAZÓN SOCIAL]`, `[NIT]` y `[CORREO DE DERECHOS]`) y la superusuaria publique la versión definitiva en el admin (*Textos de consentimiento*).
2. El texto de `health_data` cubra explícitamente la ficha: condiciones, medicamentos, alergias, embarazo y antecedentes alimentarios.
3. Un profesional de la salud valide los umbrales de las banderas y las preguntas del cuestionario de aptitud (marcados `[A VALIDAR]` en `docs/contracts/plan-ia.md`).
4. Definas `PRIVACY_POLICY_URL` si ya publicaste la política de tratamiento de datos.

Luego define `HEALTH_FEATURES_ENABLED=true` en Railway.

## 7b. Planes con IA (Google Gemini)

Requieren la función de salud encendida (sección 7). Pasos:

1. **Etiqueta los ejercicios** (panel → Ejercicios → Editar → «Etiquetas para planes automáticos»: intensidad, equipo y zonas de riesgo). Los que no tienen intensidad no se proponen a clientas con lesiones o advertencias, ni a quien entrena en casa. `check_infra` avisa cuántos faltan.
2. **Prueba sin costo** con `AI_PLANS_MODE=simulated`: el flujo completo funciona (pedido, bandeja, revisión), pero los borradores salen de un generador de ejemplo, quedan marcados «MODO DE PRUEBA» y no se pueden aprobar en producción.
3. **Gemini según el nivel**: Free admite el flujo de catálogo y selección de recetas sin exportar ficha, medidas, metas energéticas, notas ni historial; Django personaliza localmente. Configura el proyecto gratuito y confirma su estado en AI Studio. No es necesario habilitar facturación para ese flujo. El nivel pago queda como opción posterior explícita.
4. **Prueba la clave con datos ficticios** desde tu equipo (no toca la base ni datos de clientas): pon `GEMINI_API_KEY` y `GEMINI_MODEL` en tu `.env` local y ejecuta `python manage.py check_gemini`. Debe terminar con «Gemini responde y el formato es válido» y muestra los tokens usados. Si responde `ai_http_400`, prueba `GEMINI_SCHEMA_FIELD=none` y confirma el nombre del modelo. Después borra la clave de tu `.env`.
5. Si despliegas en Railway, define clave, modelo, `GEMINI_ACCESS_TIER=free`, `GEMINI_FREE_TIER_CONFIRMED=true`, `GEMINI_BILLING_CONFIRMED=false` y `AI_PLANS_MODE=live`. Para acceso gratuito desde tu PC usa la guía de ejecución integrada; no requiere Railway.

### Plan de alimentación (fase C)

Además de lo anterior:

1. **Catálogo de alimentos**: `start.sh` ejecuta `python manage.py load_foods` en cada despliegue (90 alimentos con cifras **aproximadas y sin validar**; es repetible, no pisa los ya validados, y si falla el arranque continúa). También puedes correrlo a mano.
2. **Un nutricionista-dietista debe revisar las cifras** (admin → Alimentos) y marcar los alimentos como validados (acción “Marcar como validados”). Fuera de modo de desarrollo, el sistema **ignora los alimentos sin validar**: sin este paso no se pueden generar planes de alimentación reales.
3. **Validación profesional (hoy SUSPENDIDA)**: con `AI_PLANS_REQUIRE_PROFESSIONAL` sin definir (o `false`) cualquier entrenador aprueba planes de alimentación y se usan los alimentos aunque no estén validados; `check_infra` lo avisa. Para reactivarla define `AI_PLANS_REQUIRE_PROFESSIONAL=true` y sigue el punto siguiente.
   **Credencial para aprobar** (solo si la validación está activa): en el admin → «Credenciales de validación», marca `can_validate_nutrition` y escribe la frase que se imprime en el plan (p. ej. «Nutricionista-dietista, RETHUS N.º …») para quien apruebe los planes de alimentación. Quién puede hacerlo es una decisión legal (Ley 1164 de 2007): consúltalo con tu abogado.
4. `check_infra` informa si el catálogo está vacío, cuántos alimentos están sin validar y si nadie tiene la credencial. `check_gemini` prueba también una comida.
5. Cupos opcionales de «otra opción»: `AI_PLANS_ALT_SLOT_WEEKLY_LIMIT` (por comida y semana, 3) y `AI_PLANS_ALT_DAILY_LIMIT` (por clienta y día, 6).

Los borradores se generan en un hilo del mismo servidor (sin Redis ni Celery); si un despliegue corta uno a la mitad, se recupera solo al consultar su estado o con `python manage.py process_plan_jobs`. Existe además un modo con servicio trabajador aparte (`AI_PLANS_WORKER`, apagado por defecto): sección 12.

## 8. Archivos de despliegue en el repositorio

| Archivo | Para qué sirve |
| --- | --- |
| `start.sh` | Espera a la base, migraciones, estáticos, cuenta inicial, datos iniciales y gunicorn |
| `Procfile` | `web: bash start.sh`. Respaldo: si el servicio no tiene comando de arranque, Nixpacks y Railpack usan esta línea, así que el arranque igual pasa por `start.sh` (antes llamaba a gunicorn directo y se saltaba `migrate`) |
| `requirements.txt` → `requirements/base.txt` | Dependencias de producción |
| `requirements/development.txt`, `requirements/local.txt` | Dependencias para desarrollo (con debug toolbar; `local.txt` sin PostgreSQL) |
| `.env.example` | Plantilla de variables, solo con valores de ejemplo |

## 9. Configuración del servicio en el panel de Railway

Railway retiró «Config as Code» (`railway.toml` / `railway.json`): esos archivos dejan de leerse el **2026-12-01**. Su reemplazo, «Infrastructure as Code», es un archivo `.railway/railway.ts` que exige instalar el paquete npm `railway` (o `.railway/railway.py`, en beta, con `pip install railway-sdk`) y aplicarlo con la CLI. No se adoptó: agregaría Node/npm al repositorio (y Nixpacks cambiaría el build al ver un `package.json`), y un `railway config apply` del proyecto entero puede borrar recursos que no estén en el archivo, como la base Postgres. Fuentes (consultadas el 2026-11-17): <https://docs.railway.com/reference/config-as-code>, <https://docs.railway.com/infrastructure-as-code>.

**Corte hecho el 2026-11-17:** los valores se copiaron al panel del servicio de producción y `railway.toml` se borró del repositorio. No se debe volver a crear `railway.toml` ni `railway.json`: mientras Railway los lea mandan sobre el panel (`test_deploy_config.py` lo impide).

Producción: proyecto `alluring-dedication`, entorno `production`, servicio `profit-studio` (dominio `profit-studio-production.up.railway.app`). Valores en **Settings**:

- **Build › Builder**: Nixpacks. Ver la nota de abajo antes de cambiarlo.
- **Build › Custom Build Command**: `python manage.py collectstatic --noinput || echo 'AVISO: collectstatic fallo en el build; start.sh lo repetira'`
- **Deploy › Custom Start Command**: `bash start.sh`
- **Deploy › Healthcheck Path**: `/health/`
- **Deploy › Healthcheck Timeout**: `300` segundos
- **Deploy › Restart Policy**: `On Failure`, con máximo `10` reintentos
- **Deploy › Serverless / App Sleeping**: encendido (el plan gratuito lo exige)
- **Settings › Config File**: vacío
- **Variables**: `DJANGO_SETTINGS_MODULE=config.settings.production` (las variables del servicio también se ven durante el build)

Redes de seguridad si algo queda vacío en el panel:

- Sin comando de arranque: Nixpacks y Railpack usan la línea `web` del `Procfile`, que es `bash start.sh` (`apps/accounts/tests/test_deploy_config.py` lo comprueba).
- Sin comando de build (o con un `collectstatic` del build que falló o quedó a medias): `start.sh` ejecuta `collectstatic` al arrancar si no existe `staticfiles/staticfiles.json`. Si vuelve a fallar, `start.sh` **aborta** el arranque (ver sección 10).
- Sin `DJANGO_SETTINGS_MODULE`: `start.sh` usa `config.settings.production`, pero el `collectstatic` del build usaría los settings de desarrollo. Por eso la variable debe existir en el servicio.
- Sin healthcheck: el despliegue sale en vivo sin comprobar que `/health/` responde. No rompe nada, pero se pierde la protección: hay que llenarlo.

Nota sobre el builder: Nixpacks está en modo mantenimiento y Railway recomienda Railpack. Railpack ignora `nixpacks.toml`, lee `.python-version` (3.11) y, sin comando de arranque, también usa el `Procfile`. Cambiar de builder es un cambio aparte, con su propio despliegue de prueba.

## 10. Estáticos con huella, service worker y cabeceras (2026-11-20)

**Estáticos con huella.** Producción usa `whitenoise.storage.CompressedManifestStaticFilesStorage` (`config/settings/production.py`). `collectstatic` guarda cada archivo con una huella de su contenido en el nombre (`css/app.css` → `css/app.5820300842cd.css`) y anota la correspondencia en `staticfiles/staticfiles.json`. `{% static %}` entrega el nombre con huella, y WhiteNoise lo sirve con `Cache-Control: max-age=315360000, public, immutable`: el navegador lo guarda y no lo vuelve a pedir; si el archivo cambia, cambia su URL. Los nombres sin huella siguen existiendo (se sirven con caché de 60 s), por eso las rutas escritas a mano (`/static/offline.html`) siguen funcionando.

Qué cambia al operar:

- `collectstatic` **tiene que terminar bien**: su post-proceso falla si un CSS tiene `url(...)` a un archivo inexistente. En el build de Railway el comando lleva `|| echo ...` (el build no se cae), así que `start.sh` revisa que exista `staticfiles/staticfiles.json`; si no existe, lo repite, y si falla otra vez **no arranca**. Sin manifest, todas las páginas darían error 500 mientras `/health/` sigue en 200 (no usa estáticos) y Railway daría el despliegue por bueno; al abortar, el healthcheck falla y Railway conserva el despliegue anterior.
- Una plantilla que pida con `{% static 'x' %}` un archivo que no existe lanza `ValueError: Missing staticfiles manifest entry` y esa página responde 500 (con el almacenamiento anterior solo fallaba esa imagen). `apps/public/tests/test_static_references.py` recorre todas las plantillas, comprueba que cada `{% static %}` y cada `/static/...` escrito a mano exista, y corre un `collectstatic` real con el almacenamiento de producción: si falla, la suite falla antes de desplegar.
- No se usa `WHITENOISE_MANIFEST_STRICT=False`: taparía el error en vez de avisarlo.
- Para volver al comportamiento anterior (sin huella) basta cambiar la línea `STORAGES['staticfiles']` de `production.py` a `whitenoise.storage.CompressedStaticFilesStorage` y volver a poner en `start.sh` el arranque tolerante.

**Service worker en `/sw.js`.** El archivo fuente es `static/sw.js`, pero se entrega desde la raíz (`apps/public/views.py::service_worker`) con `Service-Worker-Allowed: /`, `Cache-Control: no-cache` y `X-Content-Type-Options: nosniff`. Servido desde `/static/sw.js` el navegador rechaza registrarlo con `scope: '/'` y la PWA no funciona sin avisar. La vista no usa la base de datos ni la sesión.

**Permissions-Policy.** `config/middleware.py` añade la cabecera a todas las respuestas con la lista de `PERMISSIONS_POLICY` (`config/settings/base.py`): cámara, micrófono, ubicación, USB, serie, Bluetooth, HID, MIDI, captura de pantalla, magnetómetro y realidad extendida quedan negados; `payment` solo para el sitio y `https://checkout.wompi.co`. Si algún día la web usa una de esas funciones (por ejemplo, la cámara para escanear), hay que quitarla de la lista; una prueba revisa que no se niegue nada de lo que piden los `allow="..."` de las plantillas.

## 11. Conexiones a la base de datos y App Sleeping (2026-11-20)

**Decisión: por defecto `DB_CONN_MAX_AGE=0` (sin conexiones persistentes).** Reutilizar la conexión a Postgres acelera cada página que usa la base, pero Railway cuenta las conexiones abiertas como actividad y eso impide que el servicio se duerma (ver «App Sleeping» más abajo). El servicio `profit-studio` está en plan gratuito con App Sleeping encendido (verificado: `sleepApplication=true`, plan «free»; ver sección 9), así que se prefiere que duerma. La mejora queda lista y se activa con **`DB_CONN_MAX_AGE=60`** al pasar a un plan de pago, o si se prefiere un servicio siempre despierto y más rápido.

**Qué era el problema.** Django, sin configuración, abre una conexión nueva a PostgreSQL en **cada petición** y la cierra al terminar (`CONN_MAX_AGE=0`): conexión de red + negociación TLS + autenticación. Medido en producción el 2026-11-21 (mediana de 7 peticiones, tiempo de servidor): las páginas que no usan la base respondían en 225-340 ms (`/health/`, `/sw.js`, `/accounts/login/`) y las que sí la usan en 490-585 ms (`/`, `/about/`, `/exercises/`, `/contact/`); en local la portada tarda 1,4 ms. Esa diferencia de ~200-250 ms es, en gran parte, abrir la conexión.

**Qué se hizo.** `config/settings/production.py` fija en `DATABASES['default']`:

- `CONN_MAX_AGE` = `DB_CONN_MAX_AGE` (por defecto `0`: una conexión nueva por petición, como siempre). Con un valor mayor que 0 (por ejemplo `60`), cada worker de gunicorn reutiliza su conexión mientras tenga menos de esos segundos de vida. Django revisa la edad al empezar y al terminar cada petición, así que una conexión vieja se cambia cuando llega la siguiente petición, y la última conexión queda abierta hasta la siguiente visita.
- `CONN_HEALTH_CHECKS=True` (siempre): antes de la primera consulta de cada petición que reutiliza la conexión, Django hace un `SELECT 1`; si Postgres se reinició o se durmió, descarta la conexión muerta y abre otra en vez de responder 500. Cuesta un viaje de ida y vuelta a la base por petición reutilizada; con `DB_CONN_MAX_AGE=0` no hace nada, porque no hay conexión que reutilizar.
- `DB_CONN_MAX_AGE` es la única perilla: si `DATABASE_URL` trae `?conn_max_age=...` o `?conn_health_checks=...`, se sobrescriben. Lo demás que entrega la URL (motor, host, `OPTIONS`) no se toca. Una variable vacía cuenta como no definida; un valor que no sea un entero ≥ 0 (`abc`, `-1`, `1.5`, `60s`) lanza `ImproperlyConfigured` con el nombre de la variable y el servidor no arranca: Railway no da por bueno el despliegue (healthcheck) y conserva el anterior.
- `python manage.py check_infra` muestra la línea «Base de datos · Conexiones persistentes»: OK con `0` (con la nota de que reutilizar conexiones impide App Sleeping), OK con los segundos y la comprobación si se reutilizan (con la nota de que el servicio deja de dormirse), AVISO si se reutiliza sin comprobar. No muestra host ni claves.

**Activarlas (plan de pago o servicio siempre despierto):** en Railway > Variables del servicio `profit-studio`, `DB_CONN_MAX_AGE=60` (redespliega solo). **Desactivarlas de nuevo:** quitar la variable o poner `DB_CONN_MAX_AGE=0`.

**Revisado y sin cambios necesarios** (nada depende de una conexión nueva por petición):

- Hilos de planes con IA (`apps/plans/jobs.py::_thread_main`) y de WhatsApp (`apps/whatsapp/queue.py::_thread_main`): las conexiones de Django son propias de cada hilo, llaman `close_old_connections()` al empezar y `connection.close()` al terminar; ni la edad ni la comprobación las afectan (esas señales solo corren en el hilo de la petición). El comando `process_whatsapp_queue` también cierra al final.
- `start.sh`: el script que espera a la base y los comandos en segundo plano (`check_infra`, `load_initial_data`, `load_foods`) son procesos aparte; la conexión se cierra con el proceso. El `connect_timeout` de 5 s se aplica solo dentro del script de espera (`connection.settings_dict` de ese proceso) y no depende de `CONN_MAX_AGE`. Gunicorn no usa `--preload`, así que ningún worker hereda una conexión abierta antes de bifurcarse.
- `select_for_update()` y `transaction.atomic()` (pagos, planes, cuentas, WhatsApp): el candado vive hasta que termina la transacción, no la conexión. Si una petición terminara con una transacción a medias, Django descarta esa conexión al final (`close_old_connections` cierra la que no volvió a autocommit). No hay `SET` de sesión, bloqueos de sesión (`advisory`) ni tablas temporales en el código.
- Si algún día se pone PgBouncer en modo transacción delante de Postgres, hay que volver a decidir: ahí se usa `DB_CONN_MAX_AGE=0` (ya es el valor por defecto) o `DISABLE_SERVER_SIDE_CURSORS=True`.

**Cuántas conexiones puede haber a la vez** (la cuenta supone `DB_CONN_MAX_AGE` > 0, el peor caso; con `0` el máximo simultáneo es el mismo, solo que sin las 2 conexiones que quedan abiertas en reposo). Postgres de Railway, plantilla estándar: `max_connections` = 100 (el valor por defecto de Postgres; según hilos del foro de Railway de hace 1-2 años, **no verificado en tu instancia**: confírmalo con `SHOW max_connections;`). Cuenta por instancia del servicio web:

| Quién | Conexiones | Nota |
| --- | --- | --- |
| Workers de gunicorn (2, sync, un hilo cada uno) | 2 | Uso simultáneo máximo igual que con `0`; la diferencia es que, con conexiones persistentes, quedan 2 abiertas aunque no haya tráfico |
| Hilos de planes con IA | 1 por trabajo en marcha | Hasta ~4 min cada uno; el código no limita la concurrencia (tope diario: `AI_PLANS_DAILY_JOB_LIMIT`=100). Cifra de trabajo: 5 |
| Hilos de avisos de WhatsApp | 1 por aviso, segundos | Cifra de trabajo: 2 |
| `check_infra` y `load_initial_data` → `load_foods` (segundo plano de `start.sh`) | 2 | Solo el primer minuto tras arrancar |
| Servicio cron `process_whatsapp_queue` (servicio aparte) | 1 | Hasta 4 min cada 15 |
| Servicio trabajador de IA (opcional, sección 12) | 1 | Una por vuelta del bucle (con `DB_CONN_MAX_AGE=0` se abre y se cierra en cada vuelta). Con él los hilos de IA salen del servicio web (J = 0 allí). Durante un redeploy del trabajador hay 2 a la vez |

- **Estable:** 2 + 5 + 2 = 9 por instancia (lo normal son 2-4), más 1 del cron.
- **Durante un despliegue** (Railway mantiene el anterior hasta que el nuevo pasa el healthcheck): 9 (anterior) + 9 (nuevo) + 2 (arranque) + 1 (`migrate`) + 1 (cron) = **22 en el peor caso**, ~22 % de 100.
- Fórmula: en estado estable `réplicas × (2 + J + W) + 1`; en un despliegue `2 × réplicas × (2 + J + W) + 2 + 1 + 1` (instancia anterior y nueva a la vez), con `J` trabajos de IA y `W` avisos simultáneos. Con 100 como tope hay margen para varias réplicas; si algún día se escala, repite la cuenta.
- Esta cuenta es por diseño: no se midió el número real de conexiones. Para verlo en Postgres: `SELECT count(*) FROM pg_stat_activity;`.

**App Sleeping: por qué el valor por defecto es 0 y cuándo poner 60.** La documentación de Railway (consultada el 2026-11-20, <https://docs.railway.com/reference/app-sleeping>) dice que las conexiones abiertas a la base de datos cuentan como actividad y evitan que el servicio se duerma: «Idle database connections, such as pooled ones, count as outbound traffic and prevent sleep». Con `DB_CONN_MAX_AGE` > 0 cada worker se queda con su última conexión abierta hasta la siguiente visita (Django solo revisa la edad al empezar y terminar una petición), así que **el servicio web deja de dormirse**: más consumo facturado, a cambio de páginas ~200 ms más rápidas y de evitar los 10-20 s de arranque en frío de la primera visita. Que Railway lo cuente así viene de su documentación; no se probó en este servicio.

- **Plan gratuito con App Sleeping (hoy): dejar `0`.** Es lo que da el valor por defecto; la web conserva el comportamiento anterior y el servicio sigue durmiéndose. Cada página que usa la base paga ~200 ms por abrir la conexión.
- **Plan de pago, o servicio siempre despierto: poner `DB_CONN_MAX_AGE=60`.** Ahí no hay sueño que proteger y la mejora es gratis. Después de activarla, `check_infra` muestra la nota de que el servicio deja de dormirse.
- Si se activa y se quiere comprobar el efecto: no visites la web durante unos 15 minutos y mira en el panel si el servicio `profit-studio` sigue «Active» en vez de «Sleeping».

**Qué esperar al activarla.** Abrir una conexión nueva a Postgres necesita ~7 viajes de ida y vuelta (red, TLS, autenticación); si cuesta ~200-250 ms, cada viaje tarda unos 30 ms. Con la conexión reutilizada, la portada solo paga dos viajes (la comprobación y la consulta): debería bajar de ~585 ms hacia ~400-450 ms. Es una estimación hecha con las cifras medidas y no una medición posterior al cambio: vuelve a medir tras desplegar. Además, 30 ms por viaje es mucho más de lo normal dentro de la misma región de Railway (1-2 ms por la red privada), y cada consulta de cada página lo paga: conviene comprobar en el panel que `DATABASE_URL` use la dirección interna (`*.railway.internal`, no la pública `*.proxy.rlwy.net`) y que web y Postgres estén en la misma región. Es una hipótesis: no se verificó (no se leen las variables de Railway).

## 12. Modo trabajador para los planes con IA (2026-11-20) — APAGADO por defecto

**Resumen.** Hoy cada plan con IA se genera en un hilo del mismo servicio web (`apps/plans/jobs.py`). El **modo trabajador** saca ese trabajo de la web: con `AI_PLANS_WORKER=true` la web solo anota el pedido en la base de datos (estado `queued`) y otro servicio de Railway, que corre `python manage.py process_plan_jobs --loop`, lo toma y lo genera. **Está apagado por defecto y no cambia nada hasta que el dueño lo decida**: con `AI_PLANS_WORKER` sin definir (o `false`) todo sigue exactamente igual, con hilos en la web. Además, producción tiene hoy `AI_PLANS_MODE=off`, así que no se genera ningún plan con IA.

### Decisión del dueño: lo que cuesta (léelo antes de activarlo)

Fuentes de Railway, consultadas el **2026-11-20**: <https://docs.railway.com/reference/app-sleeping>, <https://docs.railway.com/pricing>, <https://docs.railway.com/reference/pricing/free-trial>, <https://docs.railway.com/deployments/restart-policy>.

- **El trabajador nunca se duerme y mantiene despierto a Postgres.** App Sleeping duerme un servicio cuando deja de enviar tráfico: se considera inactivo a los 5 minutos y se duerme entre los 5 y 10 minutos. Cuenta *cualquier* paquete saliente, incluidas las conexiones a la base de datos, y el tráfico por la red privada entre servicios del mismo proyecto también impide el sueño. El trabajador pregunta a la base cada 5 segundos (`--interval`), así que él mismo no se duerme, y su tráfico hacia Postgres mantiene despierto también a ese servicio (la documentación no habla de bases de datos en concreto: esto es una deducción de sus reglas, no se probó en este proyecto). La web sí seguiría durmiéndose.
- **Cuesta crédito las 24 horas.** Railway cobra por minuto el uso de memoria ($10 por GB al mes) y de CPU ($20 por vCPU al mes). *Estimación, no medida:* un trabajador de Python en reposo ocupa del orden de 0,2 GB, es decir, alrededor de $2 al mes solo de memoria, más lo que gaste Postgres por no dormirse.
- **El plan gratuito no alcanza.** Incluye $1 de uso al mes y, cuando se acaba, «your services stop until the next billing cycle»: se detendría también la web. Además, en los planes Free y Trial la política de reinicio `Always` no está disponible y `On Failure` tiene un máximo de 10 reinicios: el trabajador quedaría apagado tras 10 fallos seguidos. El plan Hobby ($5 al mes, con $5 de uso incluidos) y los superiores funcionan siempre encendidos: ahí el trabajador sí tiene sentido.
- **Si se activa y no existe el servicio trabajador, los planes se quedan «en cola» para siempre.** Por eso se crea y se comprueba el trabajador *antes* de encender la variable en la web, y `check_infra` avisa cuando hay trabajos en cola de más de 5 minutos.
- **Alternativa más barata (no probada):** un servicio *cron* de Railway con `python manage.py process_plan_jobs` (sin `--loop`), como el de WhatsApp. Railway exige al menos 5 minutos entre ejecuciones, y ese modo solo toma los trabajos que lleven más de 1 minuto en cola: un plan tardaría entre 1 y 6 minutos en empezar, pero solo se paga mientras corre y el servicio sí puede dormirse.

### Cómo funciona

- `start_job` es el único punto por donde la web lanza hilos. Con `AI_PLANS_WORKER=true` no lanza ninguno: el trabajo queda `queued`. La recuperación de huérfanos que corre en la web (al consultar un estado o crear un trabajo) sigue devolviendo a la cola los trabajos colgados, pero **ya no los relanza en un hilo de la web**. `AI_PLANS_RUN_INLINE` (pruebas) tiene prioridad sobre los dos modos.
- El comando `process_plan_jobs --loop [--interval N] [--max-jobs M]` repite: cierra la conexión vieja a la base → cada 60 segundos recupera huérfanos → toma el trabajo `queued` **más antiguo** (por fecha de creación) y lo corre → si no había nada, duerme `--interval` segundos (5 por defecto, mínimo 1). `--max-jobs` lo detiene después de M trabajos (para pruebas). Sin `--loop` el comando se comporta como siempre.
- **Un trabajo nunca corre dos veces.** El reclamo (`queued` → `running`) es un `UPDATE` atómico en la base: si dos trabajadores compiten (por ejemplo, el viejo y el nuevo durante el traslape de un redeploy), solo uno gana y el otro sigue con el siguiente.
- **Parada limpia.** Railway envía SIGTERM al redesplegar o detener el servicio y, pasado el tiempo de drenaje, SIGKILL. Con la primera señal (SIGTERM o SIGINT) el trabajador **termina el trabajo en curso y sale** sin tomar otro; con una segunda señal se corta (el trabajo queda `running` y se recupera solo cuando pasan 3 minutos sin latido). Un trabajo puede tardar hasta 4 minutos; el tiempo de drenaje de Railway se controla con la variable `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` (la documentación no da valor por defecto ni máximo). Si Railway corta antes, no se pierde nada: el trabajo se recupera y se vuelve a correr.
- **Errores.** Si la base no responde (Postgres reiniciándose o despertando), el bucle no muere: registra solo la clase del error, descarta la conexión y reintenta con espera creciente (10 s, 20 s, 40 s, hasta 60 s).
- **Logs.** Solo ids, estados, conteos y códigos (`plan worker started…`, `plan worker ran job_id=…`, `plan worker stopped processed=… reason=SIGTERM`). Nunca prompts, respuestas, datos de la ficha ni mensajes de error.

### Cómo crear el servicio trabajador en Railway (cuando el dueño decida activarlo)

Los nombres exactos de los menús de Railway pueden variar y **no se probaron desde aquí** (no hay acceso al panel): guíate por lo que veas.

1. **Plan de pago** (Hobby o superior), por lo dicho arriba.
2. En el mismo proyecto, crea un servicio nuevo desde el **mismo repositorio** de GitHub (`Jdarenas22/profit-studio`). Un nombre claro: `plan-worker`.
3. **Settings › Deploy › Custom Start Command:** `python manage.py process_plan_jobs --loop`. **No** uses `bash start.sh`: ese script migra, copia estáticos y arranca gunicorn, y dos servicios migrando a la vez es un riesgo.
4. **Variables:** las mismas que la web (`DJANGO_SETTINGS_MODULE=config.settings.production`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DATABASE_URL` como referencia al Postgres, `AI_PLANS_MODE`, `GEMINI_*`, `HEALTH_FEATURES_ENABLED`, `EMAIL_*`, `AI_PLANS_WORKER=true`…). Sin `SECRET_KEY` o `ALLOWED_HOSTS` el servicio no arranca (los pide `production.py`). Pégalas solo en el panel, nunca en el chat ni en el repositorio.
5. **Settings › Networking:** no generes un dominio público (el trabajador no atiende peticiones web). **Healthcheck Path:** vacío (un healthcheck HTTP haría fallar el despliegue).
6. **Serverless / App Sleeping: apagado** en este servicio. Si se durmiera, nadie lo despertaría: no recibe tráfico.
7. **Restart Policy:** `Always` (planes de pago). El trabajador solo sale por una señal de parada o con `--max-jobs`; con `On Failure` un fallo repetido lo dejaría apagado tras 10 intentos.
8. Opcional: `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=240` en el servicio trabajador, para darle al trabajo en curso hasta 4 minutos de gracia al redesplegar.
9. **Comprueba el trabajador antes de tocar la web:** en sus logs debe aparecer `plan worker started interval=5 max_jobs=- db=postgresql`. Si dice `db=sqlite`, al servicio le falta `DJANGO_SETTINGS_MODULE=config.settings.production` (usaría la base local vacía del contenedor y no vería ningún trabajo). Recién entonces pon `AI_PLANS_WORKER=true` en el servicio `profit-studio` (redespliega solo). Después, `check_infra` (en los logs de arranque de la web) debe mostrar «IA · Ejecución de trabajos · Trabajador aparte» y «Trabajos en cola · OK». Haz un pedido de prueba con `AI_PLANS_MODE=simulated` y confirma que pasa de «En cola» a «Listo».
10. **Volver atrás:** quita `AI_PLANS_WORKER` de la web (o ponla en `false`) y detén o borra el servicio trabajador. Lo que haya quedado `queued` lo recoge la recuperación de la web al consultar el estado, o `python manage.py process_plan_jobs`.

### Qué se verificó y qué no

Verificado con pruebas automáticas (sin red ni Gemini): que con `false` se lanza el hilo y con `true` no; que `AI_PLANS_RUN_INLINE` manda sobre ambos; que el bucle procesa por orden de creación, respeta `--max-jobs`, recupera huérfanos y no repite un trabajo cuando dos bucles compiten; que SIGTERM y SIGINT (señales reales entregadas con `signal.raise_signal`) terminan el trabajo en curso y salen sin tomar otro; y que los logs no llevan datos. **No verificado:** el servicio real en Railway (no se desplegó nada ni se consultaron sus variables), la entrega de SIGTERM por Railway en Linux, ni el consumo real de memoria y crédito.

## 13. Versión de PostgreSQL y su fin de soporte (2026-11-20)

`python manage.py check_infra` muestra la línea «Base de datos · Versión de PostgreSQL» (solo con PostgreSQL; con SQLite no agrega nada): la versión `mayor.menor` y si la mayor sigue recibiendo correcciones. No muestra el host ni las claves. `start.sh` lo ejecuta en cada arranque, así que la versión de producción queda en los logs de Railway (no se consultó desde aquí: **se desconoce la versión que usa hoy la base de producción**).

- **Fuente oficial:** <https://www.postgresql.org/support/versioning/>, consultada el **2026-11-20**. PostgreSQL da soporte 5 años a cada versión mayor; después publica una última versión menor («Final Release») y la versión queda sin soporte.
- **Tabla en el código** (`POSTGRES_FINAL_RELEASE` en `apps/public/management/commands/check_infra.py`, con la fecha de consulta en `POSTGRES_SUPPORT_CONSULTED`): 13 → 2025-11-13, **14 → 2026-11-12**, 15 → 2027-11-11, 16 → 2028-11-09, 17 → 2029-11-08, 18 → 2030-11-14, 19 → 2031-11-19.
- **Cómo se decide el estado.** El chequeo compara esas fechas con la fecha de **hoy**, así que una versión pasa de OK a AVISO sola cuando llega su fecha, sin tocar el código: AVISO si el soporte ya terminó (o si la versión es anterior a la 13), OK con una nota «termina pronto» si faltan 180 días o menos, y OK en el resto. Una versión mayor más nueva que la tabla da OK con la nota de agregarla. Si la tabla tiene más de un año, el chequeo lo anota para que se revise. Al actualizar la tabla se copian las fechas de la fuente y se cambia `POSTGRES_SUPPORT_CONSULTED`.
- **Qué significa un AVISO.** Con PostgreSQL 14 (sin soporte desde el 2026-11-12) la web sigue funcionando —Django 5.2 exige 14 o superior—, pero la base ya no recibe correcciones de seguridad: conviene planificar el salto a una versión con soporte. Con 13 o anterior, Django 5.2 no arranca.
- **Cómo subir de versión mayor en Railway.** Según su documentación (<https://docs.railway.com/databases/postgresql-major-upgrade>, consultada el 2026-11-20; **no se ejecutó**): solo funciona con la imagen oficial de Railway y una etiqueta de versión fija (no `:latest`); admite partir de las versiones 14 a 17; hace una verificación previa de solo lectura, una copia automática antes y otra después, y ofrece «Revert and discard changes» por un tiempo limitado (lo escrito después de la actualización se pierde); la base se detiene mientras corre `pg_upgrade`, «típicamente unos minutos». Cambiar a mano la etiqueta de la imagen **no** migra los datos. La documentación no dice qué plan lo exige. **Antes de hacerlo, saca una copia manual** (`docs/BACKUPS.md`) y elige una hora de poca actividad.
