# Copias de seguridad de ProFit Studio (Railway + PostgreSQL)

Guía para sacar, guardar y **probar** copias de la base de datos. Una copia que nunca se ha restaurado no es una copia: es una esperanza. Redactada el **2026-11-20**.

> **Léelo primero.** La copia de la base contiene **todo**: fichas de salud, consentimientos, pagos, correos y teléfonos de las clientas. En Colombia los datos de salud son **datos sensibles** (Ley 1581 de 2012, art. 5). Una copia es, para efectos de seguridad, tan delicada como la base de producción. Esto **no es asesoría legal**: confirma con tu abogado los plazos de conservación y qué hacer si una copia se pierde.

## 0. Resumen en diez líneas

1. Railway puede tener copias automáticas de los volúmenes (incluida la base), pero **la documentación oficial no dice qué plan las incluye** y viven junto a la base: si se borra el volumen, se borran sus copias. No dependas solo de eso (sección 2).
2. Tu copia propia: `pg_dump` en formato *custom* (un solo archivo `.dump`, comprimido), sacada **solo desde un equipo de confianza** (sección 4).
3. **Nunca pegues la dirección ni la contraseña de la base** en un comando, archivo, chat o captura: los comandos de abajo hacen que Railway se las entregue a `pg_dump` sin que las veas.
4. Guarda la copia **cifrada**, **fuera del repositorio** y fuera de carpetas sincronizadas sin cifrar (sección 5).
5. Frecuencia sugerida: una por semana y otra antes de cada cambio grande. Conserva 4 semanales + 3 mensuales; borra el resto (sección 5).
6. Prueba restaurar cada 3 meses, **siempre en una base nueva de prueba, nunca sobre producción** (sección 6).
7. Verifica comparando conteos de registros (`python manage.py count_records`, sección 7).
8. Los archivos de las clientas (fotos, videos, comprobantes) están en Cloudflare R2: **el `.dump` no los incluye** (sección 1).
9. Tras restaurar una copia hay que **repetir las eliminaciones de cuentas** pedidas después de su fecha (sección 3).
10. Qué se verificó y qué no está en la sección 9. **Nadie ha ejecutado todavía una copia real**: haz la primera con calma.

## 1. Qué hay que respaldar

| Qué | Dónde vive | Cómo se respalda |
| --- | --- | --- |
| **Base de datos** (usuarios, fichas, consentimientos, pagos, planes, rutinas, auditoría) | Servicio `Postgres` de Railway | Esta guía: `pg_dump` + (si tu plan lo incluye) copias nativas de Railway |
| **Archivos subidos** (fotos de perfil, imágenes y videos de ejercicios, **comprobantes de pago**) | Cloudflare R2: buckets `profitstudio-media` (público) y `profitstudio-receipts` (**privado**) | Aparte. El `.dump` solo guarda las *rutas* de los archivos, no los archivos. Los comprobantes son datos financieros: trátalos igual que la base. Esta guía no cubre su copia (pendiente) |
| **Variables de entorno** (claves, `SECRET_KEY`, Gemini, Wompi…) | Railway › Variables | Guarda en un gestor de contraseñas la **lista de nombres** y los valores que no puedas volver a generar. Sin ellas no se puede reconstruir el servicio. Nunca en el repositorio ni en el chat |
| **Código** | GitHub `Jdarenas22/profit-studio` | Ya está respaldado ahí |

## 2. Lo que ofrece Railway (consultado el 2026-11-20)

Fuentes: <https://docs.railway.com/volumes/backups> y <https://docs.railway.com/reference/backups>, <https://docs.railway.com/reference/pricing/plans>, <https://docs.railway.com/reference/pricing/free-trial>, <https://docs.railway.com/guides/postgresql>.

- **Copias nativas de volúmenes.** Railway guarda los datos de la base en un *volumen* y puede copiarlo automáticamente: **diaria** (cada 24 h, se conserva 6 días), **semanal** (cada 7 días, 27 días) y **mensual** (cada 30 días, 89 días), o a mano. Se configuran en el servicio, pestaña **Backups**. Son incrementales: se cobra lo que ocupa cada copia, al mismo precio que los volúmenes.
- **Cómo se restaura.** Se elige la copia por su fecha y se pulsa **Restore**; Railway deja el cambio *preparado* para que lo revises y, al pulsar **Deploy**, monta un volumen nuevo con esa copia (el volumen anterior queda sin montar, no se borra). Solo se puede restaurar **dentro del mismo proyecto y entorno**.
- **Límites que importan.** «Wiping a volume deletes all its backups»: **si se borra el volumen, se borran también sus copias.** Por eso no sirven como única protección. Las copias manuales están limitadas al 50 % del tamaño del volumen.
- **¿Qué plan las incluye? No lo sé: la documentación oficial no lo dice.** La página de copias solo remite al precio de los volúmenes, y las páginas de planes (Free, Trial, Hobby, Pro) no mencionan copias. Respuestas antiguas de la comunidad de Railway (no oficiales, de hace más de un año) decían que no estaban en Hobby y sí en Pro; pueden estar desactualizadas. **Compruébalo tú:** en el panel, servicio `Postgres` → pestaña **Backups**. Si puedes crear una copia manual, está incluido; si no aparece o lo ofrece solo en un plan superior, no lo tienes. Mientras no lo confirmes, trata `pg_dump` como tu **única** copia.
- **Plan gratuito (hoy).** $1 de uso al mes, un volumen de 0,5 GB y, si el crédito se acaba, los servicios se detienen hasta el siguiente ciclo (los volúmenes se conservan). No se menciona nada sobre copias.
- **Plan de pago.** Hobby: $5 al mes con $5 de uso incluidos, volumen de hasta 5 GB. Más capacidad y servicios siempre encendidos. Tampoco menciona copias en su página.
- **Subir de versión de PostgreSQL** (`docs/DEPLOY.md`, sección 13): Railway hace una copia automática antes y otra después. Aun así, saca la tuya antes.

## 3. Reglas de seguridad (Ley 1581 de 2012)

- **Solo desde un equipo de confianza:** el tuyo, con contraseña de inicio de sesión, con el disco cifrado, sin otros usuarios y sin compartirlo. Nunca desde un café internet, un equipo prestado o el computador de un cliente.
- **Ni credenciales ni capturas.** No pegues la URL ni la contraseña de la base en comandos, archivos, chats o capturas de pantalla. Si alguna vez se expone, hay que **rotarla** (cambiarla en Railway).
- **Cifrada y fuera del repositorio.** El repositorio ya ignora `*.dump` y la carpeta `backups/` (`.gitignore`), pero no guardes copias dentro de la carpeta del proyecto. Tampoco en OneDrive, Google Drive o Dropbox **sin cifrar** primero (sección 5).
- **Registro.** Anota en una hoja: fecha, quién la hizo, dónde está cada copia y cuándo se borra.
- **Si se pierde o roban un equipo, un disco o una copia:** es un incidente de seguridad. La Ley 1581 (art. 17) obliga al responsable a informar a la Superintendencia de Industria y Comercio cuando haya violaciones a los códigos de seguridad con riesgo para los datos. Avisa a tu abogado de inmediato, y rota las claves de Railway.
- **Las copias conservan datos de personas que ya pidieron su eliminación**, hasta que la copia se borre. Por eso la retención es corta (sección 5) y debes **anotar fuera de la base** la fecha de cada solicitud de eliminación (la tabla de auditoría de cuentas también la registra, pero una copia restaurada no tiene los registros posteriores a su fecha). Si algún día restauras una copia, **repite las eliminaciones pedidas después de la fecha de esa copia**. Pregunta a tu abogado qué plazo máximo de conservación de copias debe constar en la política de tratamiento de datos.

## 4. Copia manual con `pg_dump` (formato *custom*)

**Qué es `pg_dump`:** el programa oficial de PostgreSQL que lee la base y escribe un archivo con todo su contenido. «Formato *custom*» = un solo archivo `.dump`, comprimido, que se restaura con `pg_restore`. No bloquea a nadie: la web sigue funcionando mientras copia (documentación de PostgreSQL, consultada el 2026-11-20).

### Una sola vez

1. **Instala las herramientas de PostgreSQL** (`pg_dump`, `pg_restore`, `psql`, `createdb`): el instalador oficial de PostgreSQL para Windows deja marcar solo «Command Line Tools». Su versión mayor debe ser **igual o más nueva** que la de la base (regla oficial de `pg_dump`: no copia servidores más nuevos que él). La versión de la base de producción sale en `check_infra` (`docs/DEPLOY.md`, sección 13). Comprueba con `pg_dump --version`.
2. **Railway CLI** (ya instalada en este equipo, versión 4.61.1): `railway login`, y en la carpeta del proyecto `railway link` (proyecto `alluring-dedication`, entorno `production`).
3. **Acceso externo al Postgres.** Por defecto la base solo es accesible por la red privada de Railway. Para copiarla desde tu equipo hay que activar en el servicio `Postgres` → **Settings › Networking** el acceso externo («TCP Proxy»): crea la variable `DATABASE_PUBLIC_URL` y **Railway cobra el tráfico de salida** ($0,05 por GB; una base de estudio pequeño son megas). La base queda expuesta a internet protegida solo por contraseña y TLS: **actívalo solo mientras copias y desactívalo al terminar.** (Nombre del menú según la documentación; no se vio el panel.)

### Cada copia (PowerShell)

```powershell
$carpeta = "$HOME\Respaldos-ProFit"        # fuera del repositorio y de OneDrive
New-Item -ItemType Directory -Force $carpeta | Out-Null
$archivo = Join-Path $carpeta ("profit-" + (Get-Date -Format "yyyy-MM-dd-HHmm") + ".dump")

# Plan A: Railway entrega las variables PG* del servicio Postgres al comando; pg_dump las lee solo
railway run --service Postgres -- pg_dump --format=custom --file=$archivo
```

- `railway run` ejecuta **en tu equipo** el comando que sigue a `--` con las variables del servicio indicado. **No escribes ninguna clave.** `$archivo` lo expande PowerShell y es solo la ruta del archivo.
- **Atención: no uses `railway run env` ni `printenv`**: imprimen los valores secretos (lo advierte la propia ayuda de la CLI).
- Si Plan A falla con un error de «could not translate host name» o «connection refused», es que `pg_dump` recibió la dirección *interna* de Railway. Prueba el **Plan B**, que usa la dirección pública (`DATABASE_PUBLIC_URL`). Ojo con las comillas: la variable la tiene que expandir el `cmd` que lanza Railway, no tu PowerShell, por eso se escribe con `%...%` y la ruta va escrita completa (cambia `TU_USUARIO`):

```powershell
railway run --service Postgres -- cmd /c "pg_dump --format=custom --file=C:\Users\TU_USUARIO\Respaldos-ProFit\profit.dump --dbname=%DATABASE_PUBLIC_URL%"
```

- Si te pide una contraseña, **no la escribas**: cancela con Ctrl+C y revisa que estés usando `railway run`.
- **Estos comandos no se pudieron probar al redactar la guía** (sección 9): la primera vez, hazlo con calma y sigue con la comprobación de abajo.

### Comprueba que la copia sirve (sin restaurar nada)

```powershell
Get-Item $archivo | Select-Object Name, Length            # el tamaño no puede ser 0 ni de unos pocos KB
pg_restore --list $archivo | Select-Object -First 25       # debe listar tablas (TABLE DATA accounts_user, …)
```

`pg_restore --list` solo lee el índice del archivo (no toca ninguna base). Si falla con «unsupported version in file header», tu `pg_restore` es más viejo que el `pg_dump` que hizo la copia: instala el más nuevo.

### Al terminar

1. **Desactiva el acceso externo** del servicio `Postgres` (paso 3 de arriba).
2. Cifra la copia y bórrala sin cifrar (sección 5).

## 5. Dónde guardarla, cada cuánto y cuánto tiempo

**Cifrar.** Dos opciones:

- *Simple:* guarda el `.dump` **solo en un disco cifrado**. Windows 11 Home trae «Cifrado de dispositivo» en equipos compatibles (Configuración › Privacidad y seguridad); no se verificó en este equipo, y si no aparece usa la segunda opción.
- *Para llevarla a otro sitio:* un archivo `.7z` con contraseña usando 7-Zip: `7z a -t7z -mhe=on -p "$archivo.7z" $archivo`. Con `-p` y **sin valor**, 7-Zip te pide la contraseña (no queda en el historial de comandos); `-mhe=on` cifra también los nombres. Guarda la contraseña en un gestor de contraseñas, **nunca junto al archivo**. Después `7z t "$archivo.7z"` para comprobarlo y `Remove-Item $archivo` para borrar el `.dump` sin cifrar (en un disco SSD el borrado no es irrecuperable: por eso el disco debe ir cifrado).

**Dónde.** Dos copias en dos lugares distintos: (1) el equipo de confianza (disco cifrado) y (2) un disco externo cifrado guardado fuera del lugar de trabajo, o una nube **solo con el archivo ya cifrado**. Nunca por WhatsApp o correo, nunca en el repositorio, nunca en el servidor de Railway.

**Cada cuánto** (estudio pequeño):

- **Una por semana**, el domingo por la noche (poca actividad).
- **Una antes de cada cambio grande:** subir la versión de PostgreSQL, una migración que borra o transforma datos, encender las funciones de salud o de IA, o eliminar cuentas en bloque.
- **Prueba de restauración cada 3 meses** y después de cualquier cambio de versión de PostgreSQL (sección 6).

**Cuánto tiempo.** Las **últimas 4 semanales y 3 mensuales** (la primera de cada mes): como máximo 7 copias. Borra el resto de forma segura. Es una sugerencia: el plazo definitivo lo fija tu abogado (sección 3). Si tu plan incluye copias nativas de Railway (sección 2), complementan esto; no lo sustituyen.

## 6. Restaurar en una base NUEVA de prueba (nunca sobre producción)

> **Regla de oro.** Antes de pulsar Enter, **lee el comando**: el servidor tiene que decir `localhost` (o el de tu base de pruebas) y **jamás una dirección de Railway**. No uses `--clean` ni `--create` apuntando a producción. Si dudas, no lo ejecutes. La base de prueba tiene **datos reales de salud**: trátala como producción y bórrala al terminar.

Camino recomendado: un PostgreSQL **en tu propio equipo**. (Un proyecto de prueba en Railway no es viable en el plan gratuito: permite un solo proyecto con hasta 3 servicios.)

1. **PostgreSQL local** de versión mayor **igual o más nueva** que la de la copia (instalador oficial o Docker). Ponle una contraseña que **no se parezca a ninguna de producción**.
2. Si la copia está en `.7z`: `7z x archivo.7z` en una carpeta fuera del repositorio.
3. Crea la base vacía:

```powershell
createdb --host=localhost --username=postgres --template=template0 profit_restore_test
```

4. Restaura (te pedirá la contraseña de la base **local**):

```powershell
pg_restore --no-owner --no-acl --exit-on-error --host=localhost --username=postgres --dbname=profit_restore_test $archivo
```

`--no-owner` y `--no-acl` ignoran los dueños y permisos originales (en tu equipo no existen esos roles); `--exit-on-error` se detiene en el primer error en vez de seguir a medias (por defecto `pg_restore` continúa y solo cuenta los errores al final). Si se detiene por un error que mencione `schema public` o una `extension`, suele ser un aviso típico de restaurar entre servidores distintos: anota el texto exacto y repite **sin** `--exit-on-error`, leyendo con atención el resumen de errores del final.

5. Verifica (sección 7).
6. **Al terminar, destruye la prueba:** `dropdb --host=localhost --username=postgres profit_restore_test` y borra el `.dump` descifrado.

## 7. Verificar que la restauración sirve

### a) Que el esquema coincide con el código

Se apunta Django a la base restaurada **solo en esta ventana de PowerShell** (las variables desaparecen al cerrarla). En la carpeta del proyecto:

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings.production"
$env:SECRET_KEY = "inventa-aqui-una-frase-larga-de-mas-de-cincuenta-caracteres-solo-para-esta-prueba"   # NO la de producción
$env:ALLOWED_HOSTS = "localhost"
$env:DATABASE_URL = "postgres://postgres@localhost:5432/profit_restore_test"
$env:PGPASSWORD = "la-contraseña-de-la-base-LOCAL-de-prueba"     # solo para esta ventana
python manage.py migrate --check          # código 0 = no falta ninguna migración; 1 = la copia es de una versión anterior del esquema
```

### b) Que están los datos: `count_records`

`python manage.py count_records` cuenta los registros de **cada modelo** (solo `COUNT`: lee, no escribe) e imprime **únicamente nombres de modelo y números** (sin datos personales, sin host ni claves). Su salida está ordenada, así que se puede comparar con `diff`.

```powershell
python manage.py count_records > restaurada.txt          # en la base restaurada (variables de arriba)
```

Y el mismo conteo de **producción**, sacado justo antes de la copia (con el sitio tranquilo):

- **Opción A** (puede no funcionar; no se probó): `railway run --service profit-studio -- python manage.py count_records > produccion.txt`. Corre en tu equipo con las variables del servicio web. Si falla porque no alcanza la base *interna* (como pasó con `check_infra` el 2026-10-09, `docs/DEPLOY.md` sección 6), usa la opción B.
- **Opción B** (no se probó contra una base real): abre una consola SQL de producción con `railway connect Postgres` (necesita `psql` instalado) y pega esto; da el conteo exacto por tabla, también sin datos personales. Hazlo igual en la base restaurada (`psql --host=localhost --username=postgres --dbname=profit_restore_test`) y compara:

```sql
SELECT relname AS tabla,
       (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I', relname), false, true, '')))[1]::text::bigint AS filas
FROM pg_class
WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace
ORDER BY relname;
```

**Qué comparar.** Las tablas clave: `accounts.User`, `health.HealthProfile`, `health.ConsentRecord`, `memberships.Membership`, `payments.Payment`, `payments.ManualPayment`, `plans.Plan`, `routines.Routine`, `assessments.InitialAssessment`, `accounts.AccountAuditLog`. Si sacas los conteos de producción **justo antes** de la copia, deben ser **iguales**; si pasó tiempo, la restaurada puede tener *menos* (registros nuevos desde entonces), **nunca más**. Una diferencia grande, o una tabla en 0 que no debería, significa que la copia no sirve: no la borres, repítela.

Probar la web completa contra la copia restaurada (iniciar sesión con una cuenta real) es posible, pero necesita más pasos (archivos estáticos, cookies seguras) que no se documentan ni se probaron aquí.

### c) Limpieza

Cierra la ventana de PowerShell (o `Remove-Item Env:SECRET_KEY, Env:DATABASE_URL, Env:PGPASSWORD`), borra `restaurada.txt` si quieres y destruye la base de prueba (sección 6, paso 6).

## 8. Si algún día hay que restaurar de verdad (emergencia)

Esto es un **esquema, nunca ensayado**: antes de una emergencia real, ensaya la sección 6 y pide ayuda.

1. **No toques la base dañada.** Crea un servicio `Postgres` **nuevo** en Railway y restaura ahí la copia más reciente que pase la verificación de la sección 7 (como el `pg_restore` de la sección 6, pero apuntando al servicio nuevo con `railway run`, sin pegar claves).
2. Compara los conteos. Después apunta `DATABASE_URL` del servicio web al Postgres nuevo (referencia a su variable) y deja que `start.sh` aplique las migraciones.
3. Revisa los logs de `check_infra` y entra con una cuenta de prueba.
4. **Repite las eliminaciones de cuentas** pedidas después de la fecha de la copia (sección 3) y avisa a las clientas si perdieron registros recientes (medidas, planes, pagos manuales). Revisa también los pagos recientes de Wompi contra lo que quedó en la base.
5. Conserva la base vieja hasta estar seguro, y luego bórrala.
6. Si la causa fue una filtración o pérdida de claves: rota todas las variables (`SECRET_KEY`, R2, Wompi, Gemini, correo) y consulta a tu abogado.

## 9. Qué se verificó y qué no

**Verificado:**

- Las opciones de `pg_dump` y `pg_restore` (`--format=custom`, `--file`, `--no-owner`, `--no-acl`, `--exit-on-error`, `--list`, `--dbname`, la regla de versiones de `pg_dump`) contra la documentación oficial de PostgreSQL 19 (<https://www.postgresql.org/docs/current/app-pgdump.html> y <https://www.postgresql.org/docs/current/app-pgrestore.html>, consultadas el 2026-11-20).
- `railway run --service … -- <comando>` y `railway connect <servicio>` contra la ayuda de la CLI instalada (4.61.1) y la documentación de Railway (consultada el 2026-11-20).
- `count_records`: pruebas automáticas (solo lectura, sin datos personales, sin host ni claves, salida ordenada y comparable, código de salida 1 si algo no se puede contar).

**NO verificado** (este equipo no tiene PostgreSQL ni `pg_dump`, y no se accedió a Railway ni a la base de producción):

- Ninguna copia ni restauración **reales**. Los comandos de las secciones 4, 6 y 7 son los documentados, no ejecutados contra una base de verdad.
- Que `railway run` entregue a `pg_dump` el *host público* en las variables `PG*` (Plan A) y que el Plan B funcione con las comillas de PowerShell/`cmd`.
- La consulta SQL de conteos (es un fragmento estándar de PostgreSQL con `query_to_xml`).
- Qué plan de Railway incluye copias nativas, y los nombres exactos de los menús del panel.
- El cifrado de dispositivo de Windows 11 Home en este equipo.

La primera vez, hazlo con calma, revisa cada comando antes de pulsar Enter y termina con una restauración de prueba completa (sección 6) y la comparación de conteos (sección 7).
