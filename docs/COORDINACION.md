# Coordinación entre asistentes

Dos asistentes trabajan en este repositorio: **ChatGPT** y **Claude (Claude Code)**. No se hablan
directamente. Se coordinan con este archivo, con git y con `docs/contracts/requests.md`. La persona
dueña del proyecto hace de puente y decide cuando hay dudas.

**Regla de oro:** leer este archivo al empezar y actualizarlo al empezar y al terminar cada tarea.

**El tablero vive solo en `main`.** Cada worktree ve la copia de su rama, así que:

- Para leer la versión vigente desde cualquier rama: `git fetch origin && git show origin/main:docs/COORDINACION.md`.
- Para actualizarlo, un commit pequeño **directo a `main`** que toque **solo** este archivo
  (`git pull --rebase origin main` antes del `push`). Es la única excepción a «todo por PR», limitada
  exclusivamente a `docs/COORDINACION.md`. Todo el código y el resto de la documentación entran por
  PR con el CI aprobado. Desde un worktree que está en otra rama se hace con una carpeta
  temporal: `git worktree add ../tablero-tmp main`, editar, commit, push y `git worktree remove ../tablero-tmp`.
- No se edita este archivo dentro de las ramas de trabajo (evita conflictos al fusionar).

## 1. Carpetas (una por asistente)

| Asistente | Carpeta | Uso |
| --- | --- | --- |
| ChatGPT | `C:\Proyectos\profit-studio` | Carpeta principal. Aquí están `.env`, `.venv` y la base SQLite local |
| Claude | `C:\Proyectos\profit-studio-claude` | `git worktree` del mismo repositorio. Sin `.env`: usa los valores por defecto de `development.py` y el intérprete de `..\profit-studio\.venv` |

- Nunca se cambia de rama en la carpeta del otro asistente.
- Un `git worktree` no puede tener la misma rama abierta en dos carpetas: cada uno trabaja en sus ramas.

## 2. Ramas y PR

- Una rama por tarea, a partir de `main` actualizado (`git pull`). Nombres: `feature/...`, `fix/...`, `chore/...`, `docs/...`.
- **Nada sin commit en `main`.** Todo entra por pull request con el CI en verde (`.github/workflows/ci.yml`).
- Antes de abrir el PR: suite completa (`python -W error::DeprecationWarning manage.py test --settings=config.settings.development`) y `makemigrations --check --dry-run`.
- Sin `git push --force` a `main` ni a ramas del otro asistente.

## 2b. Migraciones y bases de datos

**Bases locales:** `config/settings/development.py` fija SQLite en `BASE_DIR / 'db.sqlite3'` e ignora
`DATABASE_URL`. Como `BASE_DIR` es la raíz de cada carpeta, **cada worktree tiene su propia base y su
propia carpeta `media/`**, y las pruebas crean bases aparte. No hay base compartida en desarrollo.

- **Reservar el número antes de crear una migración:** anotar en la sección 5 «app, número y de qué
  migración depende» (por ejemplo `plans 0006 ← plans 0005`) y la rama. Reservar ayuda, pero no basta.
- **Migraciones paralelas:** si al fusionar aparecen dos migraciones de la misma app con el mismo padre,
  renumerar no resuelve el conflicto por sí solo. Quien fusiona segundo revisa el grafo
  (`showmigrations <app>` y las `dependencies` de cada una) y elige, según lo que hace cada operación:
  apuntar la suya a la otra (si nunca se aplicó fuera de su base local) o agregar una migración de
  fusión (`makemigrations --merge`). Comprobar que las operaciones no choquen (mismo campo o tabla)
  y que `makemigrations --check` quede sin cambios, con la suite completa en verde.
- Nunca editar ni borrar una migración que ya está en `main`.
- **Producción:** nadie corre `migrate`, `shell` ni comandos con `config.settings.production` desde su
  equipo. La base de Railway solo se migra al desplegar `main` (`start.sh`).
- **Base local adelantada (al cambiar a una rama que no tiene una migración ya aplicada):** no revertir
  de forma automática. Revertir puede borrar columnas o tablas con datos. Primero mirar qué haría
  (`python manage.py migrate <app> <número anterior> --plan`) y, si borra datos que importan, respaldar
  la base local antes (copiar `db.sqlite3` fuera del repositorio). Si los datos locales no importan, es
  más simple recrear la base.
- Si alguna vez se apunta desarrollo a otra base (Postgres local, por ejemplo), usar una por carpeta y
  anotarlo en este archivo.

## 3. Reparto de áreas

| Área | Responsable | Archivos principales |
| --- | --- | --- |
| Integraciones con APIs externas: USDA FoodData Central, Gemini, WhatsApp Cloud, Wompi | ChatGPT | `apps/plans/sources/`, `apps/plans/ai/`, `apps/whatsapp/client/`, `apps/payments/` (lado Wompi) |
| Fase D de planes (planes de una clienta, progreso, clonar a borrador) | Claude | `docs/contracts/plan-ia-fase-d.md`, vistas y plantillas nuevas de la fase D |
| Despliegue (Railway, `start.sh`, `Procfile`, CI) | Claude | `start.sh`, `Procfile`, `.github/`, `docs/DEPLOY.md` |
| Revisiones de seguridad y calidad de los PR del otro asistente | Ambos | — |

Archivos **compartidos**, que exigen avisar aquí antes de tocarlos: `apps/plans/models.py`,
`apps/plans/services.py`, `apps/plans/nutrition.py`, `config/settings/*.py`, `CLAUDE.md`,
`.env.example`, `templates/trainer/base.html`, `templates/base.html`, `static/css/app.css`.

`static/css/app.css` es minificado: si dos ramas lo regeneran, al fusionar no se resuelve a mano. Quien fusiona segundo toma el de `main` y lo vuelve a generar (`scripts/build-css.sh`) sobre su rama.

Si un asistente necesita un cambio en el área del otro, lo pide en `docs/contracts/requests.md`
(o en la sección 5 de este archivo) y no lo hace él mismo.

## 4. Reglas que valen para los dos

- No leer, imprimir ni copiar `.env`, la base SQLite, los medios ni `USUARIOS-PRUEBA.local.txt`.
- No rotar claves ni cambiar Railway, Cloudflare, Meta, Wompi o Google sin permiso explícito de la persona.
- No encender en vivo la IA, WhatsApp ni las pantallas de salud.
- Las pruebas nunca usan la red (clientes simulados).
- Archivos con finales de línea LF (ver `.gitattributes`); `start.sh` y `Procfile` siempre en LF.
- Todo texto para usuarios va en español, con tildes.

## 5. Tablero

Formato: fecha · asistente · tarea · rama o PR · estado · archivos que toca.

### En curso

- 2026-11-18 · ChatGPT · Correcciones de auditoría autorizadas por la persona · `fix/auditoria-seguridad-rendimiento` · en curso · cuentas (`views.py`, `urls.py`, `password_reset.py`, `pagination.py` y pruebas), pagos (`views.py`, `storage.py` y pruebas), `apps/whatsapp/client/live.py` y pruebas, listas de clientes/pagos y parcial de paginación, `.gitignore` y documentación. Incorpora commits del PR #27 de Gemini. Sin tocar fase D, CSS, configuración de producción ni migraciones; Python corregido en `.venv-secure`, conservando el `.venv` de Claude.
- 2026-11-18 · ChatGPT · Auditoría de seguridad, capacidad local y vigencia tecnológica solicitada por la persona · `chore/auditoria-seguridad-capacidad` · PR #28, informe entregado; pendiente revisión y fusión · `scripts/audit/security_capacity.py`, `docs/auditorias/2026-11-18-seguridad-capacidad.md` y resultados sintéticos · solo pruebas aisladas y documentación; sin modificar fase D, configuración ni producción
- 2026-11-18 · ChatGPT · Seguridad HTTP de Gemini · `feature/gemini-http-safety`, [PR #27](https://github.com/Jdarenas22/profit-studio/pull/27) · implementado, en curso hasta revisión y fusión; 1.369 pruebas locales aprobadas · `apps/plans/ai/gemini.py`, `apps/plans/tests/test_gemini_http_security.py` · sin migraciones ni cambios de configuración
- 2026-11-18 · Claude · Fase D (D-01 a D-04): backend listo (commit fff3005, 1474 pruebas OK, sin migraciones); pantallas en curso; regenerará `app.css` · `feature/fase-d` · en curso · `apps/plans/` (vistas, `rules/progress.py`, `services.py`, `nutrition.py`), `apps/routines/`, plantillas `trainer/client_plans.html`, `trainer/client_progress.html` y `trainer/plan_detail.html`

### Hecho

- 2026-11-17 · ChatGPT · Integración USDA FoodData Central · PR #26 · migración `plans/0005`
- 2026-11-17 · Claude · Railway sin `railway.toml` (configuración en el panel) · PR #25

### Migraciones reservadas

- (ninguna; la última en `main` será `plans 0005` al fusionar el PR #26)

### Pedidos entre asistentes

- 2026-11-18 · ChatGPT → Claude · En las correcciones se ajusta también `apps/accounts/tests/test_deploy_config.py`: su sonda esperaba URL de comprobantes sin bucket privado; ahora comprueba el bloqueo y no lee .env. No se cambia producción, start.sh ni CI.
- 2026-11-18 · ChatGPT → Claude · La persona pidió aplicar las correcciones: ChatGPT asume paginación/precarga y recuperación de contraseña en esta rama; no duplicar esos cambios. Se solicita coordinar posteriormente el cambio del intérprete compartido y el runtime de Railway, y comprobar PostgreSQL/R2/Redis desde el panel autorizado. No se sustituye el `.venv` en uso.
- 2026-11-18 · ChatGPT → Claude · Auditoría: priorizar paginación y precarga de membresía en `apps/accounts/views.py`/lista de clientes: 1.005 consultas y ~2 MB con 1.000 clientes ficticios. El informe contiene reproducción y propuesta; no se cambiaron los archivos de Claude.
- (vacío)

### Sin asignar

- 2026-11-18 · Dependabot/Pillow: pendiente reconciliar las alertas con el grafo de dependencias de GitHub. Revisión ChatGPT: `origin/main` fija `Pillow==13.0.0` en `requirements/base.txt` y `requirements/local.txt`; el entorno local también usa 13.0.0. Se consultaron las 216 alertas abiertas de Pillow: ninguno de sus rangos afectados incluye 13.0.0. Los avisos oficiales [TIFF](https://github.com/python-pillow/Pillow/security/advisories/GHSA-xvh5-g6gv-559v) y [EXIF](https://github.com/python-pillow/Pillow/security/advisories/GHSA-wqgw-9xfh-qf2g) indican 13.0.0 como versión corregida. No se toma `chore/pillow` para subir una versión que ya está fijada. Un desfase del grafo es una hipótesis pendiente de comprobar; no se cerraron alertas ni se modificaron dependencias.

### Pendientes de la persona

- Cloudflare R2: el token no tiene acceso al bucket `profitstudio-receipts` (error 403 en `check_infra`).
- Definir `FDC_API_KEY` en Railway si se van a importar alimentos de USDA desde producción.
