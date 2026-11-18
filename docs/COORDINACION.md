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
`.env.example`, `templates/trainer/base.html`, `templates/base.html`.

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

- 2026-11-17 · Claude · Fase D (D-01 a D-04), contrato aprobado por la persona · `feature/fase-d` · en curso · `apps/plans/` (vistas, `rules/progress.py`, `services.py`, `nutrition.py`), `apps/routines/`, plantillas `trainer/client_plans.html`, `trainer/client_progress.html` y `trainer/plan_detail.html`

### Hecho

- 2026-11-17 · ChatGPT · Integración USDA FoodData Central · PR #26 · migración `plans/0005`
- 2026-11-17 · Claude · Railway sin `railway.toml` (configuración en el panel) · PR #25

### Migraciones reservadas

- (ninguna; la última en `main` será `plans 0005` al fusionar el PR #26)

### Pedidos entre asistentes

- (vacío)

### Pendientes de la persona

- Cloudflare R2: el token no tiene acceso al bucket `profitstudio-receipts` (error 403 en `check_infra`).
- Definir `FDC_API_KEY` en Railway si se van a importar alimentos de USDA desde producción.
