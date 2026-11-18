# Auditoría de ProFit Studio: seguridad, capacidad y tecnologías

Fecha: 18 de noviembre de 2026, America/Bogota.
Rama: `chore/auditoria-seguridad-capacidad`. Código base evaluado: `55120d6` de `main`.
El refuerzo de Gemini del PR #27 se revisa por separado: todavía no forma parte de esta base.

## Resultado ejecutivo

La arquitectura es adecuada para una plataforma de gimnasio y tiene controles de acceso y una suite
amplia que funcionan en los escenarios probados. Hay debilidades reproducibles que conviene corregir
antes de aumentar tráfico: consultas repetidas en la lista de clientes, falta de límite de recuperación
de contraseña y un error no controlado en el webhook Wompi. El Python local también requiere parches.

No se demostró acceso no autorizado ni ejecución remota de código en estas pruebas. Esto no certifica
que el sistema sea invulnerable. No se realizó un ataque ni una prueba de carga sobre producción.

No existe una cifra verificable de «máximo de personas» en Railway con la información disponible.
El ensayo local encontró que 25 usuarios virtuales fue el mayor nivel probado que cumplió el objetivo
de p95 <= 2 segundos en la ventana ampliada de navegación mixta. Con 50 se midieron 2,16 s y con 100,
4,43 s. No son límites de cuentas registradas ni una capacidad contratada del servidor.

## Alcance y método

- Revisión de código de cuentas, permisos, sesiones, API Ruffier, salud, pagos, IA, WhatsApp,
  archivos, frontend y configuración de despliegue.
- Suite completa de la base `main`: **1.365 pruebas aprobadas en 344,236 s**.
- Repetición final con DeprecationWarning como error y base de pruebas en memoria: **1.365 pruebas aprobadas en 434.098 s**.
- Django check y makemigrations --check --dry-run aprobados. `pip check`: sin incompatibilidades.
- pip-audit 2.10.1: 21 paquetes de una resolución nueva de producción y 23 paquetes del entorno
  instalado, sin vulnerabilidades conocidas reportadas en la fecha de consulta. No evalúa el intérprete
  Python, las versiones del servidor de base de datos ni todas las bibliotecas JavaScript.
- Bandit 1.9.4: análisis de `apps/` y `config/`, excluyendo tests y migraciones; 14 avisos revisados.
- Pruebas adversariales con Django Client y CSRF real; datos, credenciales y correos ficticios.
- Servidor temporal Waitress 3.0.2 con dos hilos, SQLite nuevo, 1.000 miembros ficticios, dos
  entrenadores y una administradora; conexión solo a `127.0.0.1`, sin llamadas a API ni correos externos.
- Descarga sin ejecución de tres recursos CDN para comprobar sus hashes SRI; consulta de versiones
  públicas en PyPI y documentación oficial.

El perfil aislado usa `DEBUG=False` y reproduce algunos valores visibles de seguridad de producción,
como cookies Secure y X-Frame-Options. **No carga config.settings.production ni verifica los valores
reales de Railway**. No usa registros de negocio ni los medios existentes. `.env` no se lee en los
scripts de auditoría; la base sintética vive fuera del repositorio. Las herramientas se instalaron en
un entorno temporal aparte, sin modificar las dependencias de la aplicación.

Quedan fuera TLS real, proxy/WAF, reglas y permisos efectivos de Cloudflare R2, secretos e historial
de exposición, restauración de respaldos, PostgreSQL/Redis desplegados, cuotas reales de proveedores,
comportamiento del navegador con todos los formularios y pagos reales. La fase D en el worktree de
Claude no forma parte del código probado.

## Hallazgos y prioridades

Las prioridades siguientes son una evaluación técnica para ordenar trabajo; no son puntuaciones CVSS.

### F-01 — Alta: lista de clientes sin paginación y consultas N+1

Evidencia: `apps/accounts/views.py`, función `trainer_client_list`, y
`templates/trainer/clients.html`, consulta `client.has_active_membership` dentro del bucle.

| Pantalla y datos | Consultas por petición | HTML sin comprimir |
| --- | ---: | ---: |
| Inicio | 1 | 32.454 bytes |
| Dashboard de miembro | 6 | 30.349 bytes |
| Entrenador con 500 clientes asignados | 504 | 800.679 bytes |
| Administradora con 1.000 clientes | 1.005 | 2.078.817 bytes |

La propiedad consulta la membresía una vez por cliente. Un experimento **solo en memoria**, sin
editar la vista, agregó `select_related('membership')`: las consultas bajaron de 504 a **4**, y de
1.005 a **5**, respectivamente. El HTML conservó su tamaño: la precarga no sustituye a la paginación.

Impacto: amplificación del trabajo de base de datos y HTML al crecer el catálogo. La pantalla exige
autenticación y rol; no se ha demostrado un ataque público sin autenticar a través de ella.

Recomendación para Claude: precargar membresía y entrenador; paginar, por ejemplo, 25 o 50 clientes;
mantener los filtros de autorización y estado. Agregar una prueba que compruebe que las consultas no
crezcan por cada fila y repetir la carga. Revisar también `trainer_manual_payment_list`, que recorre
todos los pagos para sumar y renderizar, y usar agregación/paginación donde corresponda.
La [guía oficial de rendimiento de Django](https://docs.djangoproject.com/en/5.2/topics/performance/)
recomienda medir y optimizar las operaciones de base de datos.

### F-02 — Alta, entorno local: Python 3.11.15 tiene parches de seguridad posteriores

El intérprete usado por `.venv` informa **3.11.15**. Python 3.11 sigue recibiendo soporte de seguridad
hasta octubre de 2027, pero eso no hace actuales todos sus parches. La publicación oficial
[Python 3.11.18](https://www.python.org/downloads/release/python-31118/) describe correcciones de
seguridad posteriores, incluida validación de certificados y Expat.

Recomendación: actualizar el intérprete local a un parche vigente y planificar Python 3.13/3.14 con
pruebas de compatibilidad de Django y paquetes. Comprobar por separado el parche que usa Railway:
la declaración `.python-version` solo dice `3.11`. **No se confirmó que producción use 3.11.15.**

### F-03 — Media: JSON no firmado muy anidado provoca HTTP 500 en Wompi

Con `WOMPI_EVENTS_SECRET` ficticio configurado, un POST de aproximadamente 4 KB a
`/payments/webhook/` con `[` repetido 2.000 veces, `0` y los cierres correspondientes produce **500**.
Un cuerpo `{}` sin firma sí produce 401. No se procesó ningún pago ni se aceptó una firma falsa.

Causa: `apps/payments/views.py`, `payment_webhook`, hace `json.loads` antes de validar la firma;
captura JSONDecodeError y UnicodeDecodeError, pero no RecursionError.

Impacto observado: error de servidor ante entrada no autenticada; el impacto agregado sobre
disponibilidad o registros bajo ataque no se midió. No demuestra ejecución de código ni robo de pagos.

Recomendación para el área API: manejar RecursionError y responder 400; poner un límite explícito y
acotado al cuerpo del webhook; mantener firma, idempotencia y validación de importe/moneda. Agregar
una prueba de regresión sin datos reales. No se modificó el webhook en esta auditoría.

### F-04 — Media: recuperación de contraseña sin límite de frecuencia en la aplicación

Con un usuario ficticio existente y CSRF válido, tres POST consecutivos a
`/accounts/password-reset/` devolvieron 302 y generaron **tres correos en memoria**.
La ruta usa PasswordResetView directamente; no se observó un limitador en esa vista.

Esto no permite cambiar la contraseña sin el token. Sí muestra posibilidad de abuso del envío de
correo. No se comprobó si un WAF externo añade límites. El registro de cuentas y el login tienen
protecciones distintas que no cubren automáticamente esta ruta.

Recomendación para Claude: limitar por IP y por cuenta normalizada sin revelar si existe; mantener
respuesta genérica y agregar una espera/reintento controlado. Comprobar límites compartidos entre
procesos. [OWASP recomienda protección contra envíos excesivos en recuperación de contraseña](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).

### F-05 — Media preventiva: clientes externos y aislamiento del procesamiento

- En la base `main`, Gemini sigue usando urlopen sin bloqueo explícito de redirecciones y sin límite
  de lectura; el **PR #27** ya propone la corrección, con CI aprobado en la comprobación anterior.
- `apps/whatsapp/client/live.py` presenta el mismo patrón de lectura ilimitada, tanto de respuesta
  normal como de error, y usa el comportamiento de redirección predeterminado.
- IA y WhatsApp usan hilos daemon dentro del proceso web. Hay recuperación de trabajos y límites
  de negocio, pero el trabajo sigue compartiendo recursos y ciclo de vida con el servidor web.

No se demostró una filtración real de credenciales ni se contactó a Google o Meta. El riesgo de
redirección depende de una respuesta del proveedor o de la cadena de transporte; se recomienda
bloquearla por defensa en profundidad. Completar el tratamiento de respuestas inválidas y acotar
tiempo/tamaño. Si sube el volumen, separar una cola de trabajo con concurrencia y reintentos
controlados; evaluar Celery/RQ con Redis antes de añadir más procesos web.

### F-06 — Refuerzo recomendado: CSP, MFA, Redis y frontera de confianza

No se observó cabecera Content-Security-Policy en el perfil probado ni configuración CSP en el
código revisado. La ausencia de CSP **no demuestra un XSS**, y la prueba de HTML almacenado quedó
escapada correctamente. Empezar con CSP Report-Only y revisar scripts inline; no aplicar de golpe
una política que rompa el frontend. La [documentación de Alpine](https://alpinejs.dev/advanced/csp)
ofrece una variante compatible con políticas sin unsafe-eval.

No se identificó MFA propio para administración en el código revisado. Evaluar un segundo factor
para dueña/entrenadores, considerando si ya existe control externo de acceso.

La configuración permite LocMem por proceso y un limitador de registro que falla abierto si la
caché falla. Comprobar Redis y límites en el borde antes de escalar. Validar que el proxy reescriba
X-Real-IP: el código confía en esa cabecera en producción; no se verificó esa garantía de Railway.

### F-07 — Prioridad alta de verificación: separación del bucket de comprobantes

`apps/payments/storage.py` usa el bucket general si RECEIPTS_BUCKET_NAME está vacío. En producción,
la configuración solo advierte por defecto cuando falta el bucket privado o coincide con el de media;
SECURITY_STRICT_SECRETS=true convierte esa condición en error de arranque.

Si el bucket general es público y se usa ese fallback, la vista protegida de Django no impide por sí
misma el acceso público directo al objeto. Esto es un **riesgo condicional de configuración**; no se
consultaron buckets ni comprobantes reales y no se confirmó una filtración. El tablero además registra
un problema de permisos del token para el bucket privado que requiere verificación de infraestructura.

Confirmar bucket separado, sin dominios públicos, y permisos mínimos del token; después evaluar
activar el modo estricto. Las [reglas de buckets públicos de Cloudflare R2](https://developers.cloudflare.com/r2/buckets/public-buckets/)
explican las vías de exposición por dominio público. No cambiar estos valores sin comprobar el
despliegue y coordinarlo con Claude.

La validación local de PDF comprueba tamaño, extensión y cabecera, no un análisis antimalware
completo. Los archivos deben seguir tratándose como contenido no confiable; evaluar descarga como
adjunto o aislamiento del visor según el flujo, sin considerar una cabecera PDF como garantía de inocuidad.

### Avisos de Bandit: no convertir automáticamente 14 avisos en 14 vulnerabilidades

El aviso alto B613 corresponde a caracteres bidireccionales dentro de `_CONTROL_RE` en
`apps/plans/rules/validate.py`: el patrón los incluye para rechazarlos. No se encontró una alteración
maliciosa del flujo mediante ese aviso. Puede escribirse con escapes Unicode para facilitar revisión.
El medio B104 corresponde a hosts de desarrollo. Los avisos de «contraseña» señalan textos de error
o detección de marcadores; `random` se usa para recetas/simulación/reintentos, no para secretos.
Los except/pass o continue requieren contexto, no implican por sí mismos una brecha explotable.

## Controles que respondieron correctamente

Las 14 pruebas adversariales adicionales dieron **13 resultados esperados y un fallo reproducido**
(F-03). El ensayo de tres correos es una observación adicional de abuso, no está contado como
protección aprobada. Se verificó:

- Anónimo redirigido al login al abrir el panel del entrenador; un miembro recibe 403.
- Entrenador recibe 404 al pedir el cliente o la valoración de otro entrenador.
- API Ruffier sin sesión responde 401; acceso ajeno responde 404.
- Logout exige POST y un POST sin CSRF recibe 403.
- Login no redirige a un dominio externo entregado en `next`.
- Nombre con HTML de prueba queda escapado en la ficha.
- Host no autorizado produce 400 en el perfil de auditoría.
- Wompi y WhatsApp rechazan con 401 cuerpos sin firma cuando se configuran secretos ficticios.
- En el perfil aislado, cookie de login con Secure, HttpOnly y SameSite=Lax; cabeceras de
  protección contra framing, detección de tipo y HSTS presentes. Son valores reproducidos del código,
  no una comprobación de la respuesta pública de Railway.
- Integridad SRI correcta para HTMX 1.9.10, Alpine 3.17.5 y Font Awesome 6.5.0 descargados.
- `.env`, `db.sqlite3` y `media/` no aparecen como archivos rastreados en la comprobación realizada.

La [documentación de seguridad de Django](https://docs.djangoproject.com/en/5.2/topics/security/)
explica las protecciones y sus límites; los controles de framework no sustituyen la revisión de lógica.

## Capacidad: resultados medidos

Entorno: Windows, Python 3.11.15, 12 CPU lógicas, SQLite con datos ficticios y Waitress con **dos hilos**.
El cliente de carga y el servidor usan el mismo equipo. No hubo tráfico a Railway ni pruebas contra
su PostgreSQL. La base sintética no incluye rutinas, planes ni historial de pagos/medidas;
los dashboards reales con ese historial pueden requerir más trabajo. Los procesos sync de Gunicorn no son equivalentes a los hilos de Waitress.

Mezcla: 40 % inicio público, 50 % dashboard autenticado, 10 % lista del entrenador con 500 clientes.
Los usuarios virtuales repiten acciones sin pausas; esto genera más actividad por usuario que una
persona leyendo una página. No incluye descargas de CSS/JS/imágenes, escritura concurrente,
login masivo, generación de IA, pagos reales ni latencia de red pública.

### Ventanas ampliadas: 20 s de emisión por nivel más drenaje de peticiones pendientes

| Usuarios virtuales | Peticiones | Duración total | Peticiones/s | p50 | p95 | Errores HTTP |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 25 | 552 | 21,628 s | 25,52 | 975 ms | 1.767 ms | 0 |
| 50 | 582 | 21,921 s | 26,55 | 1.827 ms | 2.159 ms | 0 |
| 100 | 567 | 24,115 s | 23,51 | 4.035 ms | 4.427 ms | 0 |

**Umbral usado para comparar:** p95 <= 2.000 ms y menos de 1 % de errores. De estos niveles, solo
25 cumplió ambos. Aumentar usuarios no aumentó proporcionalmente el caudal: se acumuló espera.
No es una prueba de estabilidad prolongada ni garantiza ese rendimiento fuera del ensayo.

### Prueba corta de pantallas específicas

| Pantalla | Concurrencia | Peticiones/s | p95 | Lectura correcta |
| --- | ---: | ---: | ---: | --- |
| `/health/` sin base de datos | 50 | 1.326,20 | 37 ms | No representa capacidad del negocio |
| Lista del entrenador | 1 | 3,44 | 326 ms | El trabajo de base limita incluso con poco tráfico |
| Lista del entrenador | 5 | 3,49 | 1.719 ms | Último nivel probado bajo 2 s en este perfil |
| Lista del entrenador | 10 | 3,35 | 3.371 ms | Se detuvo la subida por superar 3 s de p95 |

La prueba corta del healthcheck duró menos de un segundo: no debe anunciarse como un máximo
sostenido de 1.326 personas ni de peticiones de negocio. Las métricas completas están en la evidencia.

### Qué hace falta para conocer la capacidad de Railway

1. Confirmar CPU/RAM, cantidad de réplicas, versión/recursos de PostgreSQL, conexiones y Redis,
   sin copiar secretos ni datos de clientas. Confirmar que las funciones de IA usan trabajadores aparte.
2. Crear staging comparable con datos sintéticos y corregir F-01 antes de usar más recursos.
3. Probar navegación, escrituras, autenticación y colas de forma separada; incluir una prueba de
   15–30 minutos y ráfagas, con un generador externo al servidor.
4. Medir p95/p99, errores, CPU/RAM, conexiones, bloqueos de base y espera de la cola. Ajustar
   trabajadores con mediciones y margen, sin elegirlos solo por el número de CPU.
5. Definir el objetivo de servicio y repetir después de cada cambio. Reservar margen de capacidad
   para picos y tareas de proveedores; no extrapolar usuarios desde el número de cuentas.

`start.sh` declara dos workers, sin otro tipo especificado. El modo sync de
[Gunicorn atiende una petición a la vez por worker](https://gunicorn.org/design/): eso no significa
dos personas en total; el resto de peticiones puede esperar en la cola/proxy.

## Tecnologías: vigencia y decisiones recomendadas

La elección de Django + PostgreSQL + HTML/HTMX/Alpine es adecuada para este producto. No hay
evidencia de que reemplazar el frontend por React o dividir en microservicios resuelva los cuellos
de botella observados. Esta es una valoración de adecuación técnica, no un estudio de contratación.

| Tecnología observada | Evaluación al 18-11-2026 | Acción |
| --- | --- | --- |
| Django 5.2.19 LTS | Parche actual de su rama, soporte extendido hasta abril de 2028 | Mantener; 6.1.3 es la rama más nueva, no una obligación inmediata |
| Python local 3.11.15 | Rama aún soportada, parche atrasado | Actualizar parche; planear 3.13/3.14 con pruebas |
| PostgreSQL en arquitectura de producción | Apropiado; versión desplegada no comprobada | Verificar versión y último parche; no quedarse en 14 |
| Tailwind 3.4.19 | Rama anterior a v4 | Migración planificada, con revisión visual y de compatibilidad de navegador |
| HTMX 1.9.10 | Anterior al 2.0.11 documentado actualmente | Migrar con pruebas de sondeo, CSRF y formularios |
| Alpine 3.17.5 | Enfoque válido para interacción ligera; SRI correcto | Evaluar variante CSP al endurecer políticas |
| Gunicorn 22.0.0 | Rezagado frente a 27.0.1 publicado en PyPI | Revisar cambios y actualizar en PR de Claude, con prueba de arranque |
| Pillow 13.0.0 / WhiteNoise 6.12.0 / django-storages 1.14.6 | Coinciden con últimas versiones consultadas en PyPI | Mantener vigilancia y revisar alertas concretas |
| psycopg2-binary 2.9.9 | PyPI publica 2.9.13 | Actualizar parche probado; Psycopg 3 puede evaluarse por separado |
| boto3 1.34.131 / django-environ 0.11.2 / django-htmx 1.23.2 | PyPI publica 1.44.5 / 0.14.0 / 1.29.0 | Actualización gradual con pruebas de R2 y configuración |
| redis cliente 5.0.7 / django-redis 6.0.0 / django-axes 8.3.1 | PyPI publica 8.1.0 / 7.0.0 / 9.0.0 | Revisar compatibilidad antes de saltos mayores; no confundir cliente Redis con servidor Redis |

Fuentes: [soporte oficial de Django](https://www.djangoproject.com/download/),
[ciclo de Python](https://devguide.python.org/versions/),
[migración oficial de Tailwind](https://tailwindcss.com/docs/upgrade-guide),
[documentación actual de HTMX](https://htmx.org/docs/) y API JSON oficial de PyPI por paquete,
por ejemplo [Gunicorn](https://pypi.org/pypi/gunicorn/json). La evidencia guarda cada versión consultada.

Importante: PostgreSQL 14 terminó su soporte el 12 de noviembre de 2026, según el
[aviso oficial de la última publicación](https://www.postgresql.org/about/news/postgresql-187-1712-1616-1520-1425-and-19-rc-2-released-3442/).
Que Django acepte PostgreSQL 14 como mínimo no lo convierte en una versión adecuada para un
despliegue nuevo. **No se afirmó que el proyecto esté ejecutando PostgreSQL 14.**

## Plan de trabajo coordinado y criterios de aceptación

| Orden | Trabajo | Responsable propuesto | Evidencia para cerrar |
| --- | --- | --- | --- |
| 1 | Actualizar parche Python; verificar runtime, PostgreSQL y bucket privado de Railway/R2 | Claude / persona con acceso al panel | Versiones vigentes y CI correcto |
| 2 | Corregir N+1 y paginar clientes/pagos | Claude | Consultas no crecen por fila, autorización intacta, carga repetida |
| 3 | Robustecer parser y límite Wompi | ChatGPT, rama API independiente | JSON anidado devuelve 400, firmas e idempotencia conservadas |
| 4 | Limitar recuperación de contraseña | Claude | Solicitudes repetidas no generan correos ilimitados ni revelan cuentas |
| 5 | Revisar y fusionar PR #27; robustecer WhatsApp | Ambas según área | CI y pruebas de transporte aprobados |
| 6 | Revisar Redis, colas, CSP y MFA | Coordinado | Pruebas de fallos, concurrencia y navegador |
| 7 | Carga prolongada en staging comparable | Coordinado | p95/p99, errores y recursos dentro del objetivo acordado |

Esta auditoría no aplica las correcciones a las áreas de Claude ni cambia Railway. Cada corrección
debe reservarse en el tablero y entrar por PR. No se crean migraciones para estos resultados.

## Reproducción y entrega

Archivos: `scripts/audit/security_capacity.py`, `scripts/audit/run_without_secrets.py` y
`docs/auditorias/2026-11-18-evidencia.json`. La evidencia no incluye claves, cookies de sesión,
correos reales ni bases de datos.

Desde la raíz del repositorio, usando una carpeta de resultados **fuera** del proyecto:

```powershell
.\.venv\Scripts\python.exe -W error::DeprecationWarning scripts/audit/run_without_secrets.py test --settings=config.settings.development --noinput
.\.venv\Scripts\python.exe scripts/audit/run_without_secrets.py check --settings=config.settings.development
.\.venv\Scripts\python.exe scripts/audit/run_without_secrets.py makemigrations --check --dry-run --settings=config.settings.development
.\.venv\Scripts\python.exe scripts/audit/security_capacity.py --project . --output-dir "$env:TEMP\profit-audit"
```

Para carga, instalar Waitress en un entorno temporal separado y pasar su site-packages (sin
instalarlo en producción):

```powershell
.\.venv\Scripts\python.exe scripts/audit/security_capacity.py --project . --output-dir "$env:TEMP\profit-audit" --tool-site C:\ruta\entorno-auditoria\Lib\site-packages --load --workloads mixed --levels 25,50,100 --duration-seconds 20
```

El script solo permite loopback, hasta 100 usuarios virtuales y ventanas de hasta 30 s; deja de
aumentar la carga cuando p95 supera 3 s, hay más de 1 % de errores o una fase supera 30 s al
terminar. Hay hasta 15 s de espera por petición; la parada no es un corte instantáneo. Los resultados
de carga son observaciones, no una prueba CI con umbrales exigidos. El fallo Wompi queda registrado
en JSON; el script no lo oculta ni lo convierte en una aprobación de seguridad.

### Prompt para Claude

```text
Trabaja en tu worktree de ProFit Studio y en una rama propia desde main actualizado.
Lee primero origin/main:docs/COORDINACION.md y este informe de auditoría con su evidencia.
No leas .env, bases reales ni medios, y no cambies la carpeta o rama de ChatGPT.

Prioriza F-01: la lista de la administradora genera 1.005 consultas con 1.000 clientes.
Precarga membership y assigned_trainer y pagina sin debilitar clients_for_trainer ni
los filtros de estado. El experimento de precarga aislada ya baja de 1.005 a 5 consultas;
la paginación sigue siendo necesaria para reducir los 2 MB de HTML. Revisa la lista de
pagos manuales y su suma en Python. Agrega pruebas que eviten crecimiento de consultas
por fila y que conserven los permisos.

Reserva otra tarea para limitar la recuperación de contraseña por IP y cuenta sin
revelar si el correo existe. Documenta Redis o el control compartido que se necesite.
Comprueba el parche del runtime y la versión de PostgreSQL desde los medios autorizados;
no conectes ni migres la base de producción desde el equipo.

El parser de Wompi y el transporte de Gemini/WhatsApp corresponden a ChatGPT: coordina
por el tablero. No fusiones ramas del otro asistente sin revisión. Ejecuta la suite,
makemigrations --check y CI antes de fusionar; repite la carga con los mismos perfiles.
```
