# Plan de mejoras recomendadas

**Proyecto:** ProFit Studio  
**Fecha:** 16 de noviembre de 2026  
**Base:** auditoría técnica y de seguridad en [`REVISION_TECNICA_SEGURIDAD.md`](REVISION_TECNICA_SEGURIDAD.md)

Este plan separa las mejoras ya realizadas durante la auditoría de las que conviene programar. La prioridad considera el impacto sobre datos personales, disponibilidad y despliegue.

## Cambios ya aplicados

- Producción detiene el arranque si falta `DATABASE_URL`; ya no inicia silenciosamente con SQLite.
- Se agregaron pruebas para validar la firma HMAC de Meta sobre el cuerpo HTTP crudo, el token de verificación, el challenge y el rechazo de firmas inválidas.
- VS Code tiene tareas de Django y CSS, extensiones recomendadas e intérprete del entorno virtual local.
- Claude Code tiene reglas compartidas que bloquean lectura de `.env`, SQLite local, medios y archivo de usuarios de prueba.
- `CLAUDE.md` indica abrir la carpeta real del repositorio y especifica los comandos de desarrollo.
- Django 5.2 LTS está en producción desde el PR #20.
- HTTPS comprobado desde fuera: Railway redirige HTTP a HTTPS con 301 y envía HSTS.
- Pruebas de WhatsApp (firma, challenge, consentimiento, cola y webhook) sin red.
- Rama `security/endurecimiento`:
  - `SECRET_KEY` validada con los criterios de `security.W009`. Por defecto solo avisa (`profit.W001`, `check_infra`); con `SECURITY_STRICT_SECRETS=true` no arranca.
  - El bucket privado de comprobantes avisa por defecto (`profit.W002`, `RuntimeWarning`, `check_infra`) y es obligatorio con R2 en modo estricto.
  - Diagnóstico de Redis en `check_infra`.
  - CI en GitHub Actions con `check`, migraciones, la suite completa (incluida la prueba de Tailwind) y `pip-audit`.

## Recomendaciones priorizadas

| Prioridad | Mejora | Motivo y alcance | Criterio para darla por terminada |
| --- | --- | --- | --- |
| P0 | Reemplazar la `SECRET_KEY` local y confirmar la de producción | **Código listo (modo gradual); falta la parte operativa.** Claude no debe leer, imprimir ni cambiar secretos, y la clave de producción debe ser independiente. | Generar una clave local nueva. En Railway, confirmar que `check_infra` muestra `SECRET_KEY` en OK. Después, activar `SECURITY_STRICT_SECRETS=true`. |
| P0 | Exigir bucket privado separado para comprobantes cuando R2 esté activo | **Código listo (modo gradual).** Por defecto se avisa. Con `SECURITY_STRICT_SECRETS=true`, producción no arranca con R2 si `R2_RECEIPTS_BUCKET_NAME` falta o es igual a `R2_BUCKET_NAME`. Hay pruebas de ausencia, igualdad, configuración válida y sin R2. | Confirmar en Cloudflare que el bucket de comprobantes existe, es distinto y tiene el acceso público apagado. Definir la variable en Railway y activar el modo estricto. |
| ~~P0~~ | ~~Migrar la configuración de Railway antes del 2026-12-01~~ **Hecho el 2026-11-17:** valores en el panel y `railway.toml` retirado (`docs/DEPLOY.md`, sección 9). | Railway deja de leer `railway.toml` ese día. Allí están el `collectstatic` del build, `bash start.sh`, el healthcheck `/health/` y la política de reinicio. Sin ellos, el `Procfile` arrancaría gunicorn sin migraciones ni las verificaciones de `start.sh`. | La configuración vive en Infrastructure as Code (`.railway/railway.ts`) o en el panel, y un despliegue de prueba muestra en los logs `>>> migrate OK` y el healthcheck en verde. No borrar `railway.toml` hasta migrar. |
| ~~P0~~ | ~~Confirmar HTTPS de extremo a extremo~~ | **Hecho:** Railway redirige con 301 y envía HSTS. | Repetir al conectar el dominio definitivo. |
| ~~P1~~ | ~~Completar pruebas del flujo de WhatsApp~~ | **Hecho** (pruebas sin red en `apps/whatsapp/tests/`, incluida la firma). | Antes de `live`, validar con el entorno de pruebas de Meta. |
| ~~P1~~ | ~~Añadir CI con pruebas y auditoría de dependencias~~ | **Hecho:** `.github/workflows/ci.yml`, sin secretos ni despliegue. | Ver el primer resultado en GitHub > Actions tras el push. Opcional: exigirlo antes de fusionar (Settings > Branches). |
| P1 | Usar Redis compartido en producción para límites y caché | **Diagnóstico hecho**: `check_infra` avisa en producción sin `REDIS_URL` y explica qué se cuenta por proceso. Solo afecta el límite del registro de cuentas; axes y las sesiones usan la base. | Decidir si se agrega Redis en Railway (tiene costo). Si se agrega, `check_infra` muestra "Redis responde". |
| ~~P2~~ | ~~Quitar el N+1 del constructor de rutinas~~ **Hecho el 2026-11-20:** precarga de días y ejercicios; 7 consultas fijas (antes 49 con 6 días × 5 ejercicios). | `apps/routines/tests/test_builder_queries.py`. | — |
| P1 | Revisar retención, copias de seguridad y recuperación | La aplicación guarda mediciones, perfiles de salud y pagos. La revisión de código no confirma retención, cifrado ni restauración de la base y archivos en Railway/R2. | Política de retención definida y una restauración de backup ensayada; confirmar acceso privado a comprobantes y permisos mínimos de credenciales R2. |
| P2 | Preparar actualización del runtime Python | El proyecto fija Python 3.11, con fin de soporte de seguridad previsto para octubre de 2027. | Migración a Python 3.12 probada en desarrollo y despliegue de ensayo; no actualizar Django hasta una ventana compatible. |
| P2 | Planificar Django 6.2 LTS | Django 5.2.19 está vigente y tiene soporte extendido hasta abril de 2028. Django 6.2 LTS está previsto para abril de 2027; evaluar cuando sea estable. | Actualización en rama separada con revisión de notas, dependencias, migraciones y suite completa. |
| P2 | Añadir CSP en modo de reporte después de inventariar recursos | Hoy no se encontró política CSP. Hay scripts inline y recursos CDN, por lo que una política estricta aplicada sin inventario podría romper páginas o debilitarse con excepciones amplias. | CSP-Report-Only configurada en infraestructura o middleware compatible; revisar informes y probar todas las páginas antes de imponerla. |

## Secuencia sugerida

1. ~~**Antes del 2026-12-01:** migrar la configuración de Railway (`railway.toml`).~~ Hecho el 2026-11-17.
2. **Cerrar exposición de archivos y secretos:** confirmar la `SECRET_KEY` y el bucket privado de comprobantes en Railway y Cloudflare. Después, activar `SECURITY_STRICT_SECRETS=true`.
3. **WhatsApp:** mantener `WHATSAPP_CLOUD_MODE=off` hasta validar el contrato y la configuración real.
4. **Calidad:** revisar el CI en cada PR y el N+1 del constructor de rutinas.
5. **Asegurar operación:** Redis compartido, copias de seguridad y restauración probada, revisión de permisos de R2 y proceso de respuesta a incidentes.
6. **Actualizar plataforma en ventanas controladas:** Python 3.12 primero; Django 6.2 LTS cuando esté publicado y validado.

## No automatizar desde Claude

- No pedirle que lea o copie `.env`, `db.sqlite3`, medios o archivos con usuarios de prueba.
- No rotar ni modificar claves de Railway, Wompi, Gemini, Meta, correo, R2 o base de datos; hacerlo desde el gestor correspondiente.
- No habilitar en vivo WhatsApp, IA o pantallas de salud, ni publicar consentimiento o política legal.
- No ejecutar despliegues, migraciones sobre producción, cambios en Cloudflare/Railway ni commits sin autorización explícita.
- No actualizar masivamente dependencias ni cambiar de Django/Python en el mismo cambio que arregla controles de seguridad.

## Fuentes de vigencia tecnológica

- [Versiones y soporte de Django](https://www.djangoproject.com/download/)
- [Compatibilidad Python/Django](https://docs.djangoproject.com/en/dev/faq/install/)
- [Estado de soporte de Python](https://devguide.python.org/versions/)
