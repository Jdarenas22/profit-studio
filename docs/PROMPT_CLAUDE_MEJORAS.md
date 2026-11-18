# Prompt para Claude Code

Copia el bloque siguiente en Claude Code con VS Code abierto en la carpeta del repositorio que contiene `manage.py`, `.git` y `CLAUDE.md`.

```text
Trabaja en el repositorio ProFit Studio. Lee primero `CLAUDE.md`, `docs/REVISION_TECNICA_SEGURIDAD.md` y `docs/PLAN_MEJORAS_RECOMENDADAS.md`.

Objetivo: implementar en cambios pequeños las recomendaciones de código priorizadas del plan. Conserva la arquitectura actual Django 5.2; no hagas una migración general de framework o dependencias.

Reglas de trabajo:
- Antes de editar, revisa `git status` y `git diff`. Hay trabajo local en la app de WhatsApp, salud, pagos y configuración. No lo descartes, restaures, sobrescribas ni reformatees en bloque. Integra tus cambios con cuidado y señala cualquier conflicto.
- No leas ni imprimas `.env`, `.env.*` secretos, `db.sqlite3`, `media/` ni `USUARIOS-PRUEBA.local.txt`. No incluyas valores sensibles en logs, pruebas, salida ni documentación.
- No cambies secretos de Railway, Meta, Wompi, Gemini, R2, correo o base de datos. No habilites funciones live, no publiques textos legales, no despliegues, no ejecutes migraciones contra producción y no hagas commit.
- No cambies de Django o Python ni actualices paquetes en masa. No agregues excepciones amplias de CSP para silenciar errores.
- Si una recomendación depende de un cambio externo (Railway, Cloudflare, credenciales, copias de seguridad o aprobación legal), documenta el paso exacto pendiente; no simules haberlo verificado.

Implementa en este orden:
1. Revisa la protección de comprobantes en `config/settings/production.py` y `apps/payments/storage.py`. Cuando R2 esté configurado, haz que producción falle de forma clara si `R2_RECEIPTS_BUCKET_NAME` falta o es igual a `R2_BUCKET_NAME`. Conserva el modo sin R2 si ya es compatible. Añade pruebas de configuración que demuestren que los recibos requieren un bucket separado. No cambies permisos del bucket en Cloudflare.
2. Endurece la validación de `SECRET_KEY` de producción para rechazar claves que Django marcaría con `security.W009` (longitud, variedad de caracteres y valores de ejemplo). Los mensajes de error deben mostrar únicamente el nombre de la variable, nunca su valor. Añade pruebas con claves ficticias fuertes y débiles; no leas el `.env` real ni copies ninguna clave.
3. Amplía las pruebas de WhatsApp sin conexiones de red: consentimiento versionado, revocación y baja, restricción de menores, idempotencia de webhooks, rechazo de otra WABA o número, límite del cuerpo, mensajes duplicados y reintentos/estados. Usa datos sintéticos y mocks. Mantén desactivado `WHATSAPP_CLOUD_MODE=live`.
4. Añade un workflow de GitHub Actions para Python 3.11 con instalación de `requirements/development.txt`, `manage.py check --settings=config.settings.development`, `makemigrations --check --dry-run`, pruebas Django y `pip-audit` sobre requirements. No agregues secretos al workflow ni acciones de despliegue. Atiende el hecho conocido de que la prueba CSS necesita npm; haz que CI instale Tailwind de forma reproducible o deja claramente aislada esa comprobación si el diseño del repositorio exige compilar localmente. No introduzcas `package.json` sin revisar primero las instrucciones de `CLAUDE.md` sobre Railway/Nixpacks.
5. Revisa `apps/accounts/ratelimit.py` y `apps/public/management/commands/check_infra.py`. Mejora el diagnóstico para producción cuando Redis no está configurado y documenta el comportamiento distribuido. No hagas que el despliegue dependa de Redis sin verificar las decisiones y pruebas existentes.

Para cada paso:
- Primero localiza código y pruebas existentes; reutiliza sus patrones y evita duplicar controles.
- Implementa el cambio y pruebas específicas; conserva los contratos actuales.
- Ejecuta pruebas focalizadas, `manage.py check`, comprobación de migraciones y `git diff --check`.
- Si la red impide instalar o auditar algo, no informes éxito: deja constancia del comando, error y verificación pendiente.

Al terminar, resume archivos cambiados, controles añadidos, pruebas ejecutadas y resultado, riesgos pendientes que requieren acciones manuales y cualquier recomendación que no implementaste. No hagas commit ni despliegue.
```
