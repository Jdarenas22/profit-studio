# Revisión técnica y de seguridad

**Fecha:** 16 de noviembre de 2026  
**Alcance:** estructura, backend, frontend, configuración de despliegue, dependencias declaradas y cambios locales de WhatsApp. Es una revisión estática y de pruebas del repositorio; no incluye pentest externo, revisión de infraestructura Railway/R2/Wompi ni análisis de producción.
**Actualización (16 de noviembre de 2026, rama `security/endurecimiento`):** validación gradual de `SECRET_KEY` y del bucket de comprobantes, diagnóstico de Redis, CI en GitHub Actions y auditoría de dependencias. Detalle en la columna "Estado" y en "Verificaciones ejecutadas".

Para priorizar acciones, consulta el [plan de mejoras](PLAN_MEJORAS_RECOMENDADAS.md) y el [prompt de ejecución para Claude Code](PROMPT_CLAUDE_MEJORAS.md).

## Resumen

El proyecto es un monolito Django modular con plantillas del servidor, PostgreSQL en producción, SQLite en desarrollo, Tailwind CSS, HTMX y Alpine.js. Esta arquitectura sigue siendo adecuada para el producto descrito: panel de entrenadores y clientas, pagos, rutinas y flujos de aprobación. No hay evidencia que justifique migrar ahora a React, microservicios o una API separada; eso sumaría complejidad operativa.

La base técnica se mantiene en una versión LTS vigente de Django. La revisión encontró controles útiles de autenticación, aislamiento entre entrenadores, protección de archivos, firmas de webhooks y privacidad de datos de salud. Se corrigió el arranque silencioso de producción con SQLite y se añadieron pruebas básicas para autenticar el webhook de WhatsApp.

## Estructura

- `apps/accounts`: identidad, roles, autenticación y autorización por clienta.
- `apps/memberships`, `apps/payments`: suscripciones y conciliación de pagos.
- `apps/health`, `apps/assessments`: consentimiento, mediciones y datos sensibles.
- `apps/plans`, `apps/exercises`, `apps/routines`: catálogo, reglas y planes revisados por entrenadores.
- `apps/public`: páginas públicas y comprobaciones de infraestructura.
- `apps/whatsapp`: cambios locales sin confirmar para consentimiento, cola, cliente Meta y webhooks.
- `templates/` y `static/`: frontend renderizado por Django; Tailwind se compila antes de desplegar.
- `config/settings/`: configuración común, desarrollo y producción.

La separación por app es clara y el backend conserva reglas y casos de uso en módulos Python. El frontend evita el costo de mantener un framework cliente para formularios y paneles internos; Tailwind, HTMX y Alpine son una combinación razonable mientras el producto conserve este modelo de interacción.

## Hallazgos y acciones

| Prioridad | Hallazgo | Estado / acción |
| --- | --- | --- |
| Alta | `start.sh` permitía continuar sin `DATABASE_URL` y anunciaba SQLite. En Railway el disco del contenedor no es una base durable: un despliegue podía aparentar éxito y perder escrituras. | Corregido: el proceso de producción se detiene antes de migrar si falta `DATABASE_URL`. La configuración de producción todavía mantiene SQLite como fallback para comandos de build; cualquier comando manual de producción debe comprobar que usa PostgreSQL. |
| Alta | La clave de Django cargada desde el `.env` local provoca `security.W009` en `check --deploy`: la clave local no cumple las señales mínimas de fuerza de Django. | **Código resuelto, en modo gradual.** `config/security.py` aplica los mismos criterios que `security.W009` (50+ caracteres, 5+ distintos, sin `django-insecure-`) además de los valores de ejemplo, que siguen deteniendo siempre el arranque. Por defecto solo avisa: chequeo `profit.W001` en `check --deploy` y fila `SECRET_KEY` de `check_infra`. Con `SECURITY_STRICT_SECRETS=true`, `production.py` no arranca. Los mensajes nombran la variable y el motivo, nunca el valor. **Pendiente operativo:** generar una clave local nueva, confirmar en Railway que `SECRET_KEY` es fuerte y luego activar el modo estricto. |
| Alta | El canal de WhatsApp está en cambios locales sin confirmar y no tenía pruebas funcionales en `apps/whatsapp/tests/`. El flujo involucra consentimiento, teléfono y solicitudes firmadas de Meta. | **Resuelto en código.** Hay pruebas de la firma HMAC sobre el cuerpo crudo, del challenge, del rechazo de firmas inválidas y de un payload firmado, además de las de consentimiento, cola y webhook (`apps/whatsapp/tests/`). Antes de habilitar `live`, validar el contrato de Meta en su entorno de pruebas. |
| Media | `check --deploy` emite `security.W008` porque Django no fuerza HTTPS. La configuración asume que Railway termina TLS y redirige HTTP. | **Comprobado desde fuera:** Railway redirige HTTP a HTTPS con 301 y envía HSTS. `security.W008` sigue apareciendo a propósito (`SECURE_SSL_REDIRECT=False` para no romper el healthcheck interno). Repetir la comprobación cuando se conecte el dominio definitivo. |
| Media | Los límites de intentos en memoria no se comparten entre procesos cuando Redis no está configurado; con dos workers, los contadores pueden divergir. | **Diagnóstico añadido.** `check_infra` tiene un área "Caché": en producción sin `REDIS_URL` da AVISO y explica que solo el límite del registro de cuentas (`apps/accounts/ratelimit.py`) se cuenta por proceso. Las sesiones y `django-axes` usan la base de datos y no se afectan. Con Redis hace un PING de solo lectura y da ERROR si no responde, porque entonces las sesiones viven en Redis. El despliegue no depende de Redis. **Pendiente (decisión):** agregar Redis en Railway si se quieren límites compartidos (tiene costo). |
| Media | No hay un escáner de vulnerabilidades de dependencias (`pip-audit`) en el entorno. Varias dependencias están fijadas a versiones antiguas frente al ciclo de actualización actual. | **Resuelto.** `.github/workflows/ci.yml` (job "dependencias") audita `base.txt`, `development.txt` y `local.txt` en cada push y PR. Auditoría local del 16 de noviembre de 2026 con pip-audit 2.10.1 (servicios PyPI y OSV): ninguna vulnerabilidad conocida. Hay versiones antiguas sin vulnerabilidad publicada (por ejemplo `boto3` 1.34, `gunicorn` 22, `redis` 5.0): se actualizan en cambios pequeños, no en esta rama. |
| Media | La prueba de CSS necesita resolver Tailwind mediante npm y no pudo completar la verificación sin red. | **Resuelto.** El CI instala Node con `actions/setup-node` (sin `package.json`, para no cambiar el build de nixpacks) y la prueba `test_compiled_css_is_up_to_date` se ejecuta en vez de saltarse. En local sigue haciendo falta `npx` con red. |
| Alta | Railway deja de leer `railway.toml` el 2026-12-01 (Config as Code pasa a Infrastructure as Code). Hoy ese archivo define el `buildCommand` (collectstatic), `startCommand = "bash start.sh"`, el healthcheck `/health/` y la política de reinicio. Sin él, nixpacks podría arrancar con el `Procfile`, que lanza gunicorn **sin** `start.sh`: sin migraciones, sin la verificación de `DATABASE_URL` y sin `check_infra`. | **Pendiente, antes del 2026-12-01.** Migrar la configuración a Infrastructure as Code (`.railway/railway.ts`) o fijarla en el panel del servicio, y probar un despliegue. Mientras tanto, no borrar `railway.toml`. Ver "Fuentes oficiales consultadas". |
| Baja | Constructor de rutinas: `trainer_routine_builder` pasa la rutina a la plantilla, que recorre días, ejercicios de cada día y el ejercicio de cada fila sin `prefetch_related`/`select_related`. Las consultas crecen con el número de días y ejercicios (N+1). | **Pendiente.** Medir con `assertNumQueries` y precargar `days__exercises__exercise` en la vista. No afecta la seguridad; es rendimiento. |
| Baja | Python está fijado a 3.11; esa rama entra en fin de soporte de seguridad en octubre de 2027. | Planear Python 3.12 y comprobar la imagen/runtime de Railway y todas las dependencias. No cambiar el runtime sin reproducir despliegue y migraciones. |

## Controles revisados

- Los archivos `.env`, `db.sqlite3` y `USUARIOS-PRUEBA.local.txt` no están versionados y están ignorados por Git. Las reglas locales de Claude también bloquean su lectura.
- La vista de comprobantes de pago usa una ruta de acceso controlado; el servidor de media bloquea `receipts/`. R2 separa el bucket privado de comprobantes del bucket público de imágenes.
- Las vistas de entrenadores consultan clientes autorizados mediante `clients_for_trainer`, `get_client_for_trainer` y `ensure_client_access`; las vistas de salud de la clienta trabajan sobre `request.user`.
- Producción usa cookies `Secure`, HSTS, `X-Content-Type-Options` y `X-Frame-Options`. El proxy debe establecer correctamente `X-Forwarded-Proto` y bloquear HTTP en el borde.
- Los webhooks de Wompi y Meta están exentos de CSRF por ser integraciones externas; Wompi comprueba su checksum y Meta usa HMAC-SHA256 con comparación en tiempo constante. La ruta de Meta limita el tamaño del cuerpo y valida WABA y número configurados.
- Los formularios de carga tienen validadores específicos para imágenes y videos. Se debe seguir limitando tipo y tamaño en cualquier nueva carga.
- Los módulos de IA mantienen generación en borrador, reglas deterministas y revisión humana; el contrato restringe datos enviados a Gemini. Conservar desactivadas las funciones de salud/IA hasta completar los textos legales y la configuración indicada en `CLAUDE.md`.

## Tecnología y vigencia

Al 16 de noviembre de 2026, Django 6.1.3 es la versión estable más reciente y Django 5.2.19 es la versión instalada. Django 5.2 es LTS y recibe soporte extendido hasta abril de 2028; por estabilidad, mantener 5.2.19 es razonable. No se recomienda saltar a 6.1 de forma urgente: requiere Python 3.12 o superior y tiene una ventana de soporte más corta. Conviene pasar primero a Python 3.12 y evaluar Django 6.2 LTS cuando salga en abril de 2027.

Fuentes oficiales consultadas:

- [Versiones y soporte de Django](https://www.djangoproject.com/download/)
- [Compatibilidad Python/Django](https://docs.djangoproject.com/en/dev/faq/install/)
- [Ciclo de vida de Python](https://devguide.python.org/versions/)
- [Claude Code en VS Code](https://code.claude.com/docs/en/ide-integrations)
- [Configuración de Claude Code](https://code.claude.com/docs/en/settings)
- [Railway: Infrastructure as Code (fin de `railway.toml` el 2026-12-01)](https://docs.railway.com/infrastructure-as-code)
- [Railway: Config as Code](https://docs.railway.com/reference/config-as-code)
- [Django: comprobación `security.W009`](https://docs.djangoproject.com/en/5.2/ref/checks/#security)
- [pip-audit](https://pypi.org/project/pip-audit/)
- Versiones de las acciones de GitHub (`checkout`, `setup-python`, `setup-node` v7), consultadas el 16 de noviembre de 2026 en sus páginas de releases.

## Verificaciones ejecutadas

- `python manage.py check --settings=config.settings.development`: correcto.
- `python manage.py check --deploy --settings=config.settings.production`: reporta W008 (HTTPS depende del proxy) y W009 (clave local débil).
- Suite Django previa a las pruebas nuevas: 1031 pruebas; 1030 terminaron correctamente y la de `test_compiled_css_is_up_to_date` no pudo descargar Tailwind (`ENOTFOUND registry.npmjs.org`). Después, las 41 pruebas de configuración de despliegue y las 5 nuevas pruebas de seguridad de WhatsApp pasaron en ejecuciones focalizadas.
- `makemigrations --check --dry-run`: no detecta migraciones pendientes.
- Auditoría de paquetes: no ejecutada; `pip-audit` no está instalado y la red está restringida.

### Rama `security/endurecimiento` (16 de noviembre de 2026)

- Suite completa con `python -W error::DeprecationWarning manage.py test`: 1175 pruebas OK: las 1135 previas y 40 nuevas sobre `SECRET_KEY`, el modo estricto, el bucket de comprobantes, `check --deploy` y Redis. La prueba de Tailwind se ejecutó con `npx`. Contraprueba: con el modo estricto desactivado en `production.py`, las pruebas del modo estricto fallan.
- `check --settings=config.settings.development`: sin problemas. `makemigrations --check --dry-run`: sin cambios.
- `check --deploy --settings=config.settings.production` con variables ficticias, sin leer el `.env`: con una clave fuerte solo aparecen `security.W008` (esperado, ver arriba) y, con R2 sin bucket propio, `profit.W002`. Con una clave débil aparecen `security.W009` y `profit.W001`, sin mostrar el valor.
- `pip-audit` 2.10.1, en un entorno temporal fuera del proyecto, sobre `requirements/base.txt`, `development.txt` y `local.txt` y sobre el entorno instalado (servicios PyPI y OSV): "No known vulnerabilities found".

## Preparación de Claude y VS Code

`.vscode/` recomienda la extensión oficial Claude Code y las extensiones de Python, apunta al entorno `.venv` de Windows y ofrece tareas para servidor, migraciones, comprobación, pruebas y CSS. En Claude Code se conserva `CLAUDE.md` como guía de arquitectura y `.claude/settings.json` deniega archivos locales con secretos o datos privados.
