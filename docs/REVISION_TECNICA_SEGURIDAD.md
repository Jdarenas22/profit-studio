# Revisión técnica y de seguridad

**Fecha:** 16 de noviembre de 2026  
**Alcance:** estructura, backend, frontend, configuración de despliegue, dependencias declaradas y cambios locales de WhatsApp. Es una revisión estática y de pruebas del repositorio; no incluye pentest externo, revisión de infraestructura Railway/R2/Wompi ni análisis de producción.

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
| Alta | La clave de Django cargada desde el `.env` local provoca `security.W009` en `check --deploy`: la clave local no cumple las señales mínimas de fuerza de Django. | Pendiente operativo: generar una clave aleatoria local nueva y confirmar en Railway que `SECRET_KEY` es independiente, larga y aleatoria. No incluir valores secretos en el repositorio, logs o conversaciones. |
| Alta | El canal de WhatsApp está en cambios locales sin confirmar y no tenía pruebas funcionales en `apps/whatsapp/tests/`. El flujo involucra consentimiento, teléfono y solicitudes firmadas de Meta. | Añadidas pruebas de HMAC sobre el cuerpo crudo, challenge, rechazo de firmas inválidas y aceptación de un payload firmado. Antes de habilitar `live`, completar pruebas de consentimiento/revocación, idempotencia, límites y estados de entrega, y validar el contrato de Meta en entorno de pruebas. |
| Media | `check --deploy` emite `security.W008` porque Django no fuerza HTTPS. La configuración asume que Railway termina TLS y redirige HTTP. | Confirmar en Railway y en el dominio final que todo HTTP se redirige a HTTPS. No activar el redirect de Django sin excluir/validar el healthcheck interno. |
| Media | Los límites de intentos en memoria no se comparten entre procesos cuando Redis no está configurado; con dos workers, los contadores pueden divergir. | Usar Redis compartido en producción para límites y caché, y comprobar `REDIS_URL` en el despliegue. La protección de inicio de sesión `django-axes` está configurada aparte. |
| Media | No hay un escáner de vulnerabilidades de dependencias (`pip-audit`) en el entorno. Varias dependencias están fijadas a versiones antiguas frente al ciclo de actualización actual. | Ejecutar auditoría de dependencias en CI y actualizar paquetes en cambios pequeños, con changelog y pruebas. No se subieron versiones a ciegas durante esta revisión. |
| Media | La prueba de CSS necesita resolver Tailwind mediante npm y no pudo completar la verificación sin red. | Mantener `static/css/app.css` generado junto con los cambios de plantillas y ejecutar `scripts/build-css.ps1` con acceso al registro npm antes de integrar. |
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

## Verificaciones ejecutadas

- `python manage.py check --settings=config.settings.development`: correcto.
- `python manage.py check --deploy --settings=config.settings.production`: reporta W008 (HTTPS depende del proxy) y W009 (clave local débil).
- Suite Django previa a las pruebas nuevas: 1031 pruebas; 1030 terminaron correctamente y la de `test_compiled_css_is_up_to_date` no pudo descargar Tailwind (`ENOTFOUND registry.npmjs.org`). Después, las 41 pruebas de configuración de despliegue y las 5 nuevas pruebas de seguridad de WhatsApp pasaron en ejecuciones focalizadas.
- `makemigrations --check --dry-run`: no detecta migraciones pendientes.
- Auditoría de paquetes: no ejecutada; `pip-audit` no está instalado y la red está restringida.

## Preparación de Claude y VS Code

`.vscode/` recomienda la extensión oficial Claude Code y las extensiones de Python, apunta al entorno `.venv` de Windows y ofrece tareas para servidor, migraciones, comprobación, pruebas y CSS. En Claude Code se conserva `CLAUDE.md` como guía de arquitectura y `.claude/settings.json` deniega archivos locales con secretos o datos privados.
