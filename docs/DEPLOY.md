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
- `start.sh` hace, en este orden: `migrate` (si falla, **no arranca**) → `collectstatic` → crea la superusuaria `yiseth` (solo si existe `TRAINER_INITIAL_PASSWORD`) → `load_initial_data` → `gunicorn`.
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
3. Revisa el arranque: `railway logs --service profit-studio`. Deben salir `migrate OK`, `collectstatic OK` y `Listening at …`.
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

**Contra producción** ejecútalo *dentro* del servicio, donde están las dependencias de producción y la base interna de Railway (solo funciona cuando el código con `check_infra` ya esté desplegado):

```text
railway ssh --service profit-studio -- python manage.py check_infra --smtp
```

`railway ssh` necesita una llave SSH registrada en tu cuenta de Railway (`ssh-keygen -t ed25519` y luego `railway ssh keys`); sin ella el comando solo te lo indica y no ejecuta nada. Es una configuración de tu cuenta, así que hazla cuando tú lo decidas. Alternativa sin llave: el servicio `profit-studio` en el panel de Railway permite abrir un shell desde la web.

No lo corras con `railway run` desde tu equipo: `railway run` ejecuta el comando en tu computador con las variables de Railway, y tu entorno local (SQLite, sin `psycopg2` ni `boto3`) no puede conectarse a la base interna ni a R2.

## 7. Antes de encender la función de salud

La función viene **apagada** a propósito. No la actives hasta que:

1. Un abogado apruebe los textos de consentimiento (el borrador tiene marcadores `[RAZÓN SOCIAL]`, `[NIT]` y `[CORREO DE DERECHOS]`) y la superusuaria publique la versión definitiva en el admin (*Textos de consentimiento*).
2. El texto de `health_data` cubra explícitamente la ficha: condiciones, medicamentos, alergias, embarazo y antecedentes alimentarios.
3. Un profesional de la salud valide los umbrales de las banderas y las preguntas del cuestionario de aptitud (marcados `[A VALIDAR]` en `docs/contracts/plan-ia.md`).
4. Definas `PRIVACY_POLICY_URL` si ya publicaste la política de tratamiento de datos.

Luego define `HEALTH_FEATURES_ENABLED=true` en Railway.

## 8. Archivos de despliegue en el repositorio

| Archivo | Para qué sirve |
| --- | --- |
| `railway.toml` | Builder, comando de arranque (`bash start.sh`), healthcheck `/health/` y política de reinicio |
| `start.sh` | Migraciones, estáticos, cuenta inicial, datos iniciales y gunicorn |
| `Procfile` | Comando alternativo de gunicorn (Railway usa `startCommand` de `railway.toml`) |
| `requirements.txt` → `requirements/base.txt` | Dependencias de producción |
| `requirements/development.txt`, `requirements/local.txt` | Dependencias para desarrollo (con debug toolbar; `local.txt` sin PostgreSQL) |
| `.env.example` | Plantilla de variables, solo con valores de ejemplo |
