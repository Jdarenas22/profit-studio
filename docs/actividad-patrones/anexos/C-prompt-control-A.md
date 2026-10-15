Eres un desarrollador de software. Trabajas en un proyecto Django existente (ProFit Studio, gestión de un gimnasio) ubicado en <RAIZ_DEL_PROYECTO> (usa `.venv/Scripts/python.exe`; estás en Windows con Git Bash).

## Qué construir

Los planes de ejercicio o alimentación de una clienta (`apps/plans/models.py`: `Plan`, `PlanEvent`) los genera una IA como borrador y los revisa un entrenador. Implementa el módulo del CICLO DE VIDA DEL PLAN (los cambios de estado de un `Plan`) en `docs/actividad-patrones/anexos/experimento/exp_a/plan_lifecycle.py`, con sus pruebas en `docs/actividad-patrones/anexos/experimento/exp_a/tests.py` (crea también `exp_a/__init__.py`).

Estados de `Plan.status`: `draft_ai` (borrador de la IA), `in_review` (en revisión), `approved` (aprobado: la clienta lo ve), `superseded` (reemplazado por uno aprobado más nuevo) y `archived` (archivado).

Operaciones públicas (firmas fijas; el resto del sistema las usará así):

- `take_for_review(plan, actor) -> bool`: `draft_ai` -> `in_review`. Guarda `reviewed_by=actor` y registra un `PlanEvent` con acción `PlanEvent.ACTION_TAKEN`. Devuelve `True` si cambió el estado; si el plan no estaba en `draft_ai` no hace nada y devuelve `False`.
- `archive_plan(plan, actor, reason='') -> Plan`: desde `draft_ai`, `in_review` o `approved` pasa a `archived`. Guarda `archived_at`, desactiva su rutina (`Routine.is_active=False`) si tiene y registra un evento `ACTION_ARCHIVED` con el motivo (se recorta a 300 caracteres; si viene vacío, `'Archivado por el entrenador.'`). Desde cualquier otro estado lanza `PlanStateError('Este plan ya no se puede archivar.')`.
- `approve_plan(plan, actor, *, note_for_client='', warnings_ack=False, stale_ack=False) -> Plan`: `draft_ai` o `in_review` -> `approved`. Reglas previas: si el plan no está pendiente, `PlanStateError('Solo se puede aprobar un borrador o un plan en revisión.')`; si `plan.is_simulated` y no `settings.DEBUG`, `PlanStateError('Este plan se generó en MODO DE PRUEBA: no se puede aprobar. Genera uno real.')`; si `plan.flags` no está vacío y no `warnings_ack`, `PlanStateError('Marca "Revisé las advertencias" para aprobar.')`. (`stale_ack` se acepta pero este módulo no lo usa.) Efectos, todos dentro de una transacción con `select_for_update` y volviendo a verificar el estado dentro de ella (si cambió: `PlanStateError('Este plan cambió mientras lo revisabas. Recarga la página.')`): el plan aprobado anterior de la misma clienta y del mismo tipo pasa a `superseded` (se desactiva su rutina y se registra un evento `ACTION_SUPERSEDED` con la nota `f'Reemplazado por la versión {plan.version}.'`); el plan queda `approved` con `approved_by=actor`, `approved_at=ahora`, `reviewed_by` (el que ya tenía o el actor), `note_for_client` (recortada a 500), `valid_until = hoy + 28 días` (usa `apps.health.dates.today()`), y si tenía `flags` guarda `warnings_ack_by` y `warnings_ack_at`; su rutina se activa (`is_active=True`); se registra un evento `ACTION_APPROVED`; al confirmarse la transacción se avisa a la clienta con `apps.plans.notifications.client_plan_approved(plan)` (usa `transaction.on_commit`).
- `regenerate_plan(plan, actor, notes, request_token) -> (job, created)`: solo desde `draft_ai` o `in_review` (si no, `PlanStateError('Solo se regenera un borrador o un plan en revisión.')`). Pide un trabajo de generación con `apps.plans.services.request_plan(plan.client, plan.kind, actor, request_token, trainer_notes=notes, parent_plan=plan)` (úsala como caja negra: ya existe) y, si `created` es verdadero, registra un evento `ACTION_REGENERATE` con las notas (recortadas a 500).

Todas las operaciones verifican primero que el `actor` sea entrenador con acceso a la clienta del plan (`actor.is_trainer` y que la clienta esté en `apps.accounts.permissions.clients_for_trainer(actor)`); si no, `PlanNotAllowed('No tienes acceso a este plan.')`.

Quedan fuera de este módulo (las comprueba quien llama): la credencial de nutrición, que la rutina tenga ejercicios y la ficha desactualizada.

Reutiliza lo que ya existe: `Plan` y `PlanEvent` (`apps/plans/models.py`), `Routine` (`apps/routines/models.py`) y las excepciones `PlanNotAllowed` y `PlanStateError` de `apps/plans/services.py`.

## Pautas de diseño

- Clean Architecture: separa responsabilidades (controladores HTTP, servicios de negocio, acceso a datos).
- Mensajes de error genéricos y un `logger` para registrar los cambios de estado.
- Código mantenible y con pruebas.

## Reglas de esta tarea

- No modifiques ningún archivo existente del proyecto. Solo crea los 3 archivos de `exp_a/` indicados.
- No leas las funciones de ciclo de vida que ya existen en `apps/plans/services.py` (`take_for_review`, `_archive`, `archive_plan`, `approve_plan`, `regenerate_plan`). Puedes ver ese archivo únicamente con `grep -n "^class "` para conocer las excepciones y, si lo necesitas, las constantes del principio del archivo. Tampoco leas `apps/plans/nutrition.py`, `apps/plans/jobs.py` ni nada dentro de `docs/` (salvo este archivo). Sí puedes leer los modelos, `apps/accounts/permissions.py` y los helpers de pruebas de `apps/plans/tests/helpers.py` (por ejemplo `PlanScenarioTestCase`).
- Las pruebas se corren desde la raíz del proyecto con: `PYTHONPATH=docs/actividad-patrones/anexos/experimento .venv/Scripts/python.exe manage.py test exp_a`. No uses `--parallel`. No ejecutes la suite completa del proyecto.
- No hagas commits, ni push, ni cambies de rama (git solo lectura). No instales dependencias.

## Respuesta final (breve)

1. La primera línea: el ID exacto del modelo que eres.
2. Archivos creados, número de líneas de `plan_lifecycle.py`, número de pruebas y resultado (OK o fallos).
3. Lista exacta de los archivos del proyecto que leíste.
4. Decisiones de diseño que tomaste (máximo 8 líneas).
