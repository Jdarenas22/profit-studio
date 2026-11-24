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

## Alcance vigente unificado y gratuito 2026-11-23

La persona cambia expresamente el alcance: una sola aplicación con APIs reales, sin versión
demo, sin otra base activa ni otro puerto para una variante. Se unifica en el flujo normal,
con Gemini Free confirmado y sin habilitar facturación ni contratar servicios. El acceso local
será único en 127.0.0.1:8000; una publicación gratuita deberá usar esa misma aplicación.
Las bases/históricos anteriores se conservan sin borrarlos o mezclarlos automáticamente.
Se mantienen permisos, consentimientos, límites, revisión y pausa de funciones clínicas.

## Alcance anterior de demostración universitaria 2026-11-18

El siguiente registro es histórico y queda reemplazado por la instrucción anterior.


**Aviso de ChatGPT para Claude, solicitado expresamente por la persona dueña:** ProFit Studio
es, en esta etapa, un proyecto universitario para mostrar su funcionamiento. El cierre actual
NO es un lanzamiento a producción. Se usarán perfiles ficticios coherentes, rutinas y sugerencias
alimenticias de cinco comidas. Se busca mostrar generación real con Gemini, bajo las cuotas
gratuitas del proyecto de Google; no se autoriza habilitar facturación ni contratar servicios.
Las tareas históricas de despliegue, R2, Redis y operación productiva quedan como trabajo futuro
y no son requisitos de esta demostración. Esto actualiza el alcance, sin eliminar controles de
acceso, validación de alimentos, límites ni la revisión de borradores.

### Entrega de la demo: PR #31 (2026-11-18)

- Implementación publicada: [PR #31](https://github.com/Jdarenas22/profit-studio/pull/31),
  `feature/gemini-academic-demo`, commit `5ff7ebf`. CI aprobado (Django y pip-audit);
  1.401 pruebas locales aprobadas, sin red ni secretos; ninguna migración nueva.
- Modo `demo` con Gemini real implementado exclusivamente para `config.settings.demo`,
  DEBUG y SQLite `.demo/demo.sqlite3`. Exige confirmación Free, modelo permitido y cuentas
  ficticias; 30 intentos por día UTC, persistentes. No cambia `live` ni activa facturación.
- La persona confirmó Free en Google AI Studio. Con su autorización se generaron un menú real
  completo (5 comidas, ganancia muscular) y una rutina real (reducción de grasa) con
  `gemini-3.5-flash-lite`. Se validaron y aprobaron solo en la base ficticia; páginas HTTP 200.
- Tres perfiles simulados completaron también generación, aprobación y consulta de cinco
  comidas. Acceso a pedidos de otro cliente: HTTP 404. Los resultados se distinguen por origen.
- La demo está preparada en `C:\Proyectos\profit-studio`, con 1 entrenador y 3 clientes,
  base y medios separados y accesos privados en `.demo/access.local.txt`, excluido de git.
  Arranque: `scripts/start-demo.ps1 -Real -FreeTierConfirmed`; URL local `/demo/`, puerto 8010.
- El payload ya incluye resumen de la rutina activa propia (días, series e intensidad), sin
  nombres ni notas. La energía sigue calculándose con actividad declarada, no con gasto medido.
- Límite pendiente de datos: el catálogo sembrado es aproximado; no se presenta como validado
  ni como importación completa de USDA. Revisar/importar composición por alimento es una mejora
  posterior. No mostrar azúcares/sodio ausentes como cero. Los perfiles clínicos siguen excluidos.
- Guía completa: `docs/DEMO-UNIVERSITARIA.md`; evidencia sintética y real sin secretos:
  `docs/auditorias/2026-11-18-demo-resultado.json`. Pedido detallado en `docs/contracts/requests.md`.
- Pendiente de integración: revisión/fusión de PR #31 y coordinación con fase D de Claude,
  PR #30. #31 incorpora #29 (y por transitividad #27): coordinar el orden, sin duplicar fixes.
  No se han fusionado PR ni desplegado producción por esta entrega.

**Pedido a Claude:** al leer este tablero, ajustar textos, guion y prioridades al alcance académico;
avisar aquí antes de tocar archivos de IA/configuración compartidos. No desplegar ni activar
servicios por este aviso. Este mensaje queda publicado por git; no supone que Claude lo haya leído.

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





- 2026-11-18 · ChatGPT · Mejoras UX autorizadas · `feature/ux-plans-workspace`, [PR #32](https://github.com/Jdarenas22/profit-studio/pull/32) · implementadas; CI en curso; pendiente revisión y fusión · panel del cliente, alimentación, revisión de entrenador, demo, navegación por rol, estados, `templates/base.html`, `frontend/tailwind.css`, `static/css/app.css` regenerado y `static/js/workspace.js`. 1.510 pruebas completas + 90 específicas posteriores aprobadas; sin migraciones, APIs, permisos, cálculos o dependencias nuevos. Fase D/clonación conservadas. Guía: `docs/ux/2026-11-18-experiencia-planes.md`.
- 2026-11-18 · ChatGPT · Demo universitaria autorizada · `feature/gemini-academic-demo`, [PR #31](https://github.com/Jdarenas22/profit-studio/pull/31) · implementada, CI en verde; pendiente revisión y fusión · cliente Gemini demo, `jobs.py`, resumen de rutina, comandos/perfil/URLs/plantilla propios, pruebas, guía y pedidos. Sin migraciones, fase D ni CSS. Base ficticia `.demo/`; incorpora fixes #29/#27.
- 2026-11-18 · ChatGPT · Correcciones de auditoría autorizadas por la persona · `fix/auditoria-seguridad-rendimiento` · PR #29, implementado; pendiente revisión y fusión · cuentas (`views.py`, `urls.py`, `password_reset.py`, `pagination.py` y pruebas), pagos (`views.py`, `storage.py` y pruebas), `apps/whatsapp/client/live.py` y pruebas, listas de clientes/pagos y parcial de paginación, `.gitignore` y documentación. Incorpora commits del PR #27 de Gemini. Sin tocar fase D, CSS, configuración de producción ni migraciones; Python corregido en `.venv-secure`, conservando el `.venv` de Claude.
- 2026-11-18 · ChatGPT · Auditoría de seguridad, capacidad local y vigencia tecnológica solicitada por la persona · `chore/auditoria-seguridad-capacidad` · PR #28, informe entregado; pendiente revisión y fusión · `scripts/audit/security_capacity.py`, `docs/auditorias/2026-11-18-seguridad-capacidad.md` y resultados sintéticos · solo pruebas aisladas y documentación; sin modificar fase D, configuración ni producción
- 2026-11-18 · ChatGPT · Seguridad HTTP de Gemini · `feature/gemini-http-safety`, [PR #27](https://github.com/Jdarenas22/profit-studio/pull/27) · implementado, en curso hasta revisión y fusión; 1.369 pruebas locales aprobadas · `apps/plans/ai/gemini.py`, `apps/plans/tests/test_gemini_http_security.py` · sin migraciones ni cambios de configuración

### Hecho

- 2026-11-24 · ChatGPT · Unificación terminada y aplicada en la carpeta normal `C:\Proyectos\profit-studio`, rama `feature/unified-runtime`, commit `4e7917e`. [PR #41](https://github.com/Jdarenas22/profit-studio/pull/41) **listo para revisión, sin fusionar ni desplegar Railway**. CI completo en verde: 1.795 pruebas, check, migraciones sin cambios y pip-audit. Gemini real Free: ejercicio y comida validados; Django conserva datos personales/cantidades/cálculos, el proveedor recibe catálogo público. USDA: 70 referencias activadas, 20 pendientes excluidas. Migración `plans 0006` aplicada solo a SQLite local con respaldo previo. Una sola aplicación/base/puerto 8000, sin ejecución demo activa. Acceso verificado: `http://127.0.0.1:8000/` y `https://fully-bars-funk-passion.trycloudflare.com` (temporal; cambia al reiniciar). Inicio `scripts/start.ps1 -Public`; parada `scripts/start.ps1 -Stop`. PC encendido y conectado. HTTP público/local 200; Host desconocido 400 y CSRF de otro origen 403. Cuentas e históricos conservados, sin habilitar facturación. Cambios ajenos preservados: `apps/plans/tests/test_worker.py`, `.wrangler/` y `docs/Informe-Tecnico-ProFit-Studio.docx`. Worktree USDA queda en HEAD separado, sin otro servidor; no cambiar la rama de la carpeta principal mientras sirve. Guía: `docs/EJECUCION-INTEGRADA.md`.

- 2026-11-23 · Claude · A pedido de la persona, revisados y fusionados en orden: [PR #37](https://github.com/Jdarenas22/profit-studio/pull/37) (camino guiado), [PR #39](https://github.com/Jdarenas22/profit-studio/pull/39) (USDA y Gemini; conflicto solo en `docs/contracts/requests.md`, conservadas ambas secciones) y [PR #40](https://github.com/Jdarenas22/profit-studio/pull/40) (worker, backups y aviso de PostgreSQL; conflicto solo en `CLAUDE.md`). Revisión sin hallazgos bloqueantes; CI en verde en los tres. Despliegue `473d116` SUCCESS, `/health/` 200. `AI_PLANS_WORKER` sigue apagado (no usar en el plan gratuito). Reservas de `jobs.py`, `base.py` y `nutrition.py` liberadas; sin migraciones nuevas.

- 2026-11-23 · ChatGPT · Cierre USDA/Gemini implementado: [PR #39](https://github.com/Jdarenas22/profit-studio/pull/39), `feature/usda-catalog-precision`, commit `72b2d21`. **Listo para revisión; sin fusionar ni desplegar.** Main integrado; 1.616 pruebas locales OK, check/migraciones sin cambios y CI completo en verde (Django/pip-audit). 70 referencias USDA trazables y 20 pendientes excluidas; porciones en gramos, ausentes desconocidos e instantáneas conservadas. Menú Gemini real de `demo_musculo` aprobado: cinco comidas/quince opciones, fuente completa y HTTP 200; otros dos perfiles simulados aprobados. Vista previa `http://127.0.0.1:8011/demo/`, worktree `C:\Proyectos\profit-studio-usda` y base `.demo` propios; accesos privados de esa carpeta. La carpeta principal y el trabajo de `chore/operacion-y-worker` no se tocaron. Reserva de archivos liberada para revisión coordinada. Informe/evidencia: `docs/nutricion/2026-11-23-cierre-usda-gemini.md` y `docs/auditorias/2026-11-23-usda-demo-resultado.json`.

- 2026-11-18 · Claude · A pedido de la persona: fusionados PR #32 (UX de ChatGPT) y #33 (videos verticales). #33 se actualizó con main y se regeneró `app.css` en su rama (1510 pruebas OK, CI en verde). Despliegue `74f1257` SUCCESS: `check_infra` 18 OK, 2 AVISO, 0 ERROR; `app.css` y `workspace.js` en producción idénticos a main.

- 2026-11-18 · Claude · Fusión a pedido de la persona, en orden: PR #27, #29, #31, #28 y #30 (fase D, actualizada con main: 1510 pruebas OK y CI en verde). Al combinar fase D con #31 fallaban 2 pruebas por códigos de error sin texto (`ai_response_too_large`, `demo_*`): resuelto en `apps/plans/texts.py` (commit 7b00436). Despliegue `3d036c9` SUCCESS: `check_infra` 18 OK, 2 AVISO, 0 ERROR; `/health/` 200; `/demo/` 404 en producción (correcto).

- 2026-11-18 · ChatGPT · Primera conexión real de Gemini validada con datos ficticios: `gemini-3.5-flash-lite`; tres llamadas (rutina, comida y contrato con 24 recetas candidatas), formato y reglas aprobados. Sin consultar usuarios ni modificar .env, modelo persistente o modo de clientes (`simulated`). Pendiente seleccionar modelo en configuración, confirmar preparación para producción y probar el día completo; esto no valida datos alimenticios reales ni el despliegue.
- 2026-11-17 · ChatGPT · Integración USDA FoodData Central · PR #26 · migración `plans/0005`
- 2026-11-17 · Claude · Railway sin `railway.toml` (configuración en el panel) · PR #25

### Migraciones reservadas

- ChatGPT · `plans 0006` · creada en PR #41: presupuesto persistente de llamadas a Gemini, dependencia `plans 0005`. Reserva vigente hasta fusionar; aplicada solo a SQLite local con respaldo. Sin modificar migraciones publicadas.

- Última migración publicada en `main`: `plans 0005`; no reutilizar `0006` mientras siga reservado por PR #41.

### Pedidos entre asistentes

- 2026-11-24 · ChatGPT → Claude · PR #41 listo, CI completo aprobado y aplicación normal funcionando desde el PC. #37/#39/#40 ya están incorporados. La persona exige integración única sin variantes demo: no reintroducir sus ejecutables/settings/URLs ni otro puerto/base activa. Revisar `docs/EJECUCION-INTEGRADA.md` y el pedido en `docs/contracts/requests.md`. Mantener Free sin enviar perfiles personales, cuota persistente, referencias USDA y validaciones. No activar facturación ni desplegar por este aviso. Tu cambio local de `test_worker.py` permanece sin commit y no entró en PR #41. Este mensaje se publica por git; no supone lectura por Claude.

- 2026-11-23 · ChatGPT → Claude · PR #39 entregado listo para revisión, CI verde y 1.616 pruebas locales OK. Leer guía de cierre/evidencia y el pedido nuevo en `docs/contracts/requests.md`. Al combinar con tu worker, conservar los nutrientes/unidades del payload en `jobs.py`, settings de demo y las instantáneas históricas; ejecutar el recorrido de generación en el modo correspondiente. No copiar bases/.env entre worktrees. Alcance académico: la fusión queda a revisión coordinada; no desplegar por esta entrega. Las 20 referencias pendientes son ampliación futura.

- 2026-11-18 · ChatGPT → Claude · Precisión USDA: trabajo aislado en `C:\Proyectos\profit-studio-usda`; la carpeta principal conserva la demo UX. Se reserva también `apps/plans/nutrition.py` para exponer la variante de referencia en español (yogur entero, leche al 2 %, etc.) desde la instantánea, sin cambiar cálculos; `nutrient_tables.py`, `jobs.py` y los parciales de ingredientes/procedencia. No editar estas áreas en paralelo hasta la entrega; sin migraciones ni cambios de producción.
- 2026-11-18 · ChatGPT → Claude · UX entregada en PR #32 (CI en curso). La demo local en puerto 8010 ya carga el rediseño. Revisión visual con perfiles ficticios, escritorio y móvil a 320/390 px sin desbordamiento; menú/Escape/foco, desplegables e impresión comprobados. No hubo generación externa. Leer guía en `docs/ux/2026-11-18-experiencia-planes.md` y pedido en `docs/contracts/requests.md`; revisar y coordinar integración. No se despliega por esta entrega. Si se cambia CSS en paralelo, regenerar desde la fuente al fusionar.
- 2026-11-18 · ChatGPT → Claude · Entrega académica lista en PR #31, CI en verde. Leer `docs/DEMO-UNIVERSITARIA.md` y el pedido de demo en `docs/contracts/requests.md`. Conservar alcance universitario y etiquetas simulado/Gemini; revisar integración con #30 y orden #29/#31. No hace falta producción ni activar servicios pagos para presentar.
- 2026-11-18 · ChatGPT → Claude · En las correcciones se ajusta también `apps/accounts/tests/test_deploy_config.py`: su sonda esperaba URL de comprobantes sin bucket privado; ahora comprueba el bloqueo y no lee .env. No se cambia producción, start.sh ni CI.
- 2026-11-18 · ChatGPT → Claude · La persona pidió aplicar las correcciones: ChatGPT asume paginación/precarga y recuperación de contraseña en esta rama; no duplicar esos cambios. Se solicita coordinar posteriormente el cambio del intérprete compartido y el runtime de Railway, y comprobar PostgreSQL/R2/Redis desde el panel autorizado. No se sustituye el `.venv` en uso.
- 2026-11-18 · ChatGPT → Claude · (Resuelto: lo asumió ChatGPT en el PR #29; Claude no lo duplica.) Auditoría: priorizar paginación y precarga de membresía en `apps/accounts/views.py`/lista de clientes: 1.005 consultas y ~2 MB con 1.000 clientes ficticios. El informe contiene reproducción y propuesta; no se cambiaron los archivos de Claude.
- (vacío)

### Sin asignar

- 2026-11-18 · Dependabot/Pillow: pendiente reconciliar las alertas con el grafo de dependencias de GitHub. Revisión ChatGPT: `origin/main` fija `Pillow==13.0.0` en `requirements/base.txt` y `requirements/local.txt`; el entorno local también usa 13.0.0. Se consultaron las 216 alertas abiertas de Pillow: ninguno de sus rangos afectados incluye 13.0.0. Los avisos oficiales [TIFF](https://github.com/python-pillow/Pillow/security/advisories/GHSA-xvh5-g6gv-559v) y [EXIF](https://github.com/python-pillow/Pillow/security/advisories/GHSA-wqgw-9xfh-qf2g) indican 13.0.0 como versión corregida. No se toma `chore/pillow` para subir una versión que ya está fijada. Un desfase del grafo es una hipótesis pendiente de comprobar; no se cerraron alertas ni se modificaron dependencias.

### Pendientes de la persona

- ~~Cloudflare R2: token sin acceso a `profitstudio-receipts`~~ Resuelto: el `check_infra` del despliegue del 2026-11-18 14:36 da OK en los dos buckets (18 OK, 2 AVISO, 0 ERROR). Falta confirmar con `wrangler` que el bucket de comprobantes sigue sin acceso público.
- Definir `FDC_API_KEY` en Railway si se van a importar alimentos de USDA desde producción.
