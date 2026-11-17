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

## Recomendaciones priorizadas

| Prioridad | Mejora | Motivo y alcance | Criterio para darla por terminada |
| --- | --- | --- | --- |
| P0 | Reemplazar la `SECRET_KEY` local y confirmar la de producción | `check --deploy` detectó que la clave que carga el `.env` local no satisface los criterios de fuerza de Django. Claude no debe leer, imprimir ni cambiar secretos. La clave de producción debe ser independiente. | Claves nuevas, aleatorias y distintas; `check --deploy` deja de mostrar W009 usando variables de entorno de prueba fuertes. |
| P0 | Exigir bucket privado separado para comprobantes cuando R2 esté activo | El código advierte si `R2_RECEIPTS_BUCKET_NAME` falta o coincide con el bucket público de media. Una URL firmada no evita que el objeto sea público si el bucket entero permite acceso público. | Producción no arranca con R2 configurado sin un bucket privado distinto; pruebas cubren ausencia, igualdad y configuración válida. Verificar además el acceso público en Cloudflare. |
| P0 | Confirmar HTTPS de extremo a extremo | Django depende del proxy de Railway para redirigir HTTP; `check --deploy` avisa W008. No cambiar el redirect sin considerar el healthcheck interno. | Comprobación desde el dominio público demuestra que HTTP redirige a HTTPS; `/health/` sigue pasando el healthcheck interno. |
| P1 | Completar pruebas del flujo de WhatsApp | Las pruebas nuevas cubren autenticación del webhook, pero no todo el tratamiento de consentimiento, mensajes ni estados. Ampliar pruebas unitarias y de vistas sin llamar a Meta. | Casos de consentimiento vigente/retirado y versionado; menores; alta, cambio de número y baja; idempotencia; payload de otra WABA/número; límite del cuerpo; reintentos y transiciones de estado. |
| P1 | Añadir CI con pruebas y auditoría de dependencias | El entorno no tiene `pip-audit`; la suite de CSS requiere npm. La integración continua debe detectar CVE y regresiones en cada PR. | GitHub Actions instala requirements, ejecuta `check`, revisión de migraciones, pruebas Django y `pip-audit`; informa fallos sin desplegar ni requerir secretos. |
| P1 | Usar Redis compartido en producción para límites y caché | La caché local por proceso no comparte contadores entre workers. Esto reduce la precisión de los límites de intentos y otras claves temporales. | `REDIS_URL` activo en producción, chequeo de infraestructura lo informa con claridad y pruebas validan el backend configurado. |
| P1 | Revisar retención, copias de seguridad y recuperación | La aplicación guarda mediciones, perfiles de salud y pagos. La revisión de código no confirma retención, cifrado ni restauración de la base y archivos en Railway/R2. | Política de retención definida y una restauración de backup ensayada; confirmar acceso privado a comprobantes y permisos mínimos de credenciales R2. |
| P2 | Preparar actualización del runtime Python | El proyecto fija Python 3.11, con fin de soporte de seguridad previsto para octubre de 2027. | Migración a Python 3.12 probada en desarrollo y despliegue de ensayo; no actualizar Django hasta una ventana compatible. |
| P2 | Planificar Django 6.2 LTS | Django 5.2.19 está vigente y tiene soporte extendido hasta abril de 2028. Django 6.2 LTS está previsto para abril de 2027; evaluar cuando sea estable. | Actualización en rama separada con revisión de notas, dependencias, migraciones y suite completa. |
| P2 | Añadir CSP en modo de reporte después de inventariar recursos | Hoy no se encontró política CSP. Hay scripts inline y recursos CDN, por lo que una política estricta aplicada sin inventario podría romper páginas o debilitarse con excepciones amplias. | CSP-Report-Only configurada en infraestructura o middleware compatible; revisar informes y probar todas las páginas antes de imponerla. |

## Secuencia sugerida

1. **Cerrar exposición de archivos y secretos:** reemplazar claves fuera del código; hacer obligatorio el bucket privado para recibos cuando aplique; confirmar configuración HTTPS desde el dominio público.
2. **Aumentar cobertura de WhatsApp:** completar pruebas de consentimiento, revocación, encolado, duplicados y validación de cuenta. Mantener `WHATSAPP_CLOUD_MODE=off` hasta validar el contrato y la configuración real.
3. **Automatizar seguridad y calidad:** CI con pruebas, migraciones y auditoría de dependencias. Resolver o aislar la descarga de Tailwind para que la comprobación de CSS sea repetible en CI.
4. **Asegurar operación:** Redis compartido, copias de seguridad y restauración probada, revisión de permisos de R2 y proceso de respuesta a incidentes.
5. **Actualizar plataforma en ventanas controladas:** Python 3.12 primero; Django 6.2 LTS cuando esté publicado y validado.

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
