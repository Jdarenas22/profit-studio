# Despliegue de ProFit Studio — Railway + Cloudflare

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
- Los servicios de Railway están en modo «sleeping» (App Sleeping): tras ~10 min sin tráfico Railway detiene el servicio web **y** Postgres, y los despierta con la primera visita, que tarda entre 10 y 20 s. «Sleeping» en el panel **no** significa desconectado. Para que el despertar sea fiable, `start.sh` espera a que Postgres acepte conexiones antes de migrar (hasta `DB_WAIT_SECONDS`, 90 s por defecto). Si prefieres respuesta inmediata siempre, desactiva «Serverless» en Settings de cada servicio (los deja siempre encendidos y cuesta más).

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
| `AI_PLANS_MODE` | Planes con IA: `off` (por defecto), `simulated` (borradores de ejemplo, sin costo, no se aprueban) o `live` (Gemini). Ver sección 7b |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_BILLING_CONFIRMED` | Obligatorias en `live` (el servidor no arranca sin ellas). La clave debe ser de un proyecto con facturación |

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
3. **Gemini de pago**: en Google AI Studio crea la clave en un proyecto con **facturación habilitada** (prepago mínimo USD 5). En el nivel gratuito Google puede usar y revisar los datos: no sirve para datos de salud.
4. **Prueba la clave con datos ficticios** desde tu equipo (no toca la base ni datos de clientas): pon `GEMINI_API_KEY` y `GEMINI_MODEL` en tu `.env` local y ejecuta `python manage.py check_gemini`. Debe terminar con «Gemini responde y el formato es válido» y muestra los tokens usados. Si responde `ai_http_400`, prueba `GEMINI_SCHEMA_FIELD=none` y confirma el nombre del modelo. Después borra la clave de tu `.env`.
5. Define en Railway `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_BILLING_CONFIRMED=true` y `AI_PLANS_MODE=live`.

### Plan de alimentación (fase C)

Además de lo anterior:

1. **Catálogo de alimentos**: `start.sh` ejecuta `python manage.py load_foods` en cada despliegue (90 alimentos con cifras **aproximadas y sin validar**; es repetible, no pisa los ya validados, y si falla el arranque continúa). También puedes correrlo a mano.
2. **Un nutricionista-dietista debe revisar las cifras** (admin → Alimentos) y marcar los alimentos como validados (acción “Marcar como validados”). Fuera de modo de desarrollo, el sistema **ignora los alimentos sin validar**: sin este paso no se pueden generar planes de alimentación reales.
3. **Validación profesional (hoy SUSPENDIDA)**: con `AI_PLANS_REQUIRE_PROFESSIONAL` sin definir (o `false`) cualquier entrenador aprueba planes de alimentación y se usan los alimentos aunque no estén validados; `check_infra` lo avisa. Para reactivarla define `AI_PLANS_REQUIRE_PROFESSIONAL=true` y sigue el punto siguiente.
   **Credencial para aprobar** (solo si la validación está activa): en el admin → «Credenciales de validación», marca `can_validate_nutrition` y escribe la frase que se imprime en el plan (p. ej. «Nutricionista-dietista, RETHUS N.º …») para quien apruebe los planes de alimentación. Quién puede hacerlo es una decisión legal (Ley 1164 de 2007): consúltalo con tu abogado.
4. `check_infra` informa si el catálogo está vacío, cuántos alimentos están sin validar y si nadie tiene la credencial. `check_gemini` prueba también una comida.
5. Cupos opcionales de «otra opción»: `AI_PLANS_ALT_SLOT_WEEKLY_LIMIT` (por comida y semana, 3) y `AI_PLANS_ALT_DAILY_LIMIT` (por clienta y día, 6).

Los borradores se generan en un hilo del mismo servidor (sin Redis ni Celery); si un despliegue corta uno a la mitad, se recupera solo al consultar su estado o con `python manage.py process_plan_jobs`.

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
