Eres un desarrollador de software. Trabajas en un proyecto Django existente (ProFit Studio, gestión de un gimnasio) ubicado en <RAIZ_DEL_PROYECTO> (usa `.venv/Scripts/python.exe`; estás en Windows con Git Bash).

## Situación

Ya existe el módulo del ciclo de vida de un `Plan` (los cambios de estado de un plan de ejercicio o alimentación) en `docs/actividad-patrones/anexos/experimento/exp_b/`: `plan_lifecycle.py` y `tests.py`. Léelos completos antes de empezar.

## Cambio pedido: nuevo estado «vencido» (`expired`)

- Un plan `approved` cuya fecha `valid_until` es anterior a hoy debe poder pasar a `expired`. Agrega la operación pública `expire_overdue_plans(today=None) -> int`: marca como `expired` todos los planes `approved` con `valid_until < today` (por defecto `apps.health.dates.today()`), desactiva su rutina (`Routine.is_active=False`), registra para cada uno un `PlanEvent` con la acción `'expired'` (es una cadena nueva; cabe en el campo, no cambies el modelo) y la nota `'Vigencia vencida.'`, y devuelve cuántos cambió. Es idempotente: no toca los planes que ya no están en `approved`. Registra el cambio con el `logger`.
- Un plan `expired` se puede archivar con `archive_plan` (pasa a `archived`, mismo resultado que archivar un aprobado; su rutina ya está inactiva). Un plan `expired` NO se puede aprobar, ni tomar para revisión, ni regenerar: cada operación responde como ya responde para un estado no permitido (los mismos mensajes y valores de retorno que existen hoy).
- Un plan `expired` no cuenta como aprobado vigente: al aprobar otro plan de la misma clienta y tipo, el `expired` no se reemplaza ni se modifica.
- Las operaciones y los mensajes que ya existen no cambian de comportamiento para los demás estados.
- Actualiza `tests.py`: agrega las pruebas del estado nuevo y no rompas las existentes.

## Reglas de esta tarea

- Edita únicamente los archivos de `exp_b/` (`plan_lifecycle.py` y `tests.py`). No hagas más cambios de los necesarios para este pedido: no reorganices ni renombres lo que no haga falta.
- No leas ninguna otra carpeta `exp_*`, ni nada dentro de `docs/` salvo este archivo y `exp_b/`. Puedes leer los modelos (`apps/plans/models.py`), `apps/health/dates.py`, `apps/routines/models.py` y `apps/plans/tests/helpers.py`.
- Las pruebas se corren desde la raíz del proyecto con: `PYTHONPATH=docs/actividad-patrones/anexos/experimento .venv/Scripts/python.exe manage.py test exp_b`. No uses `--parallel` ni ejecutes la suite completa del proyecto.
- No hagas commits, ni push, ni cambies de rama (git solo lectura). No instales dependencias.

## Respuesta final (breve)

1. La primera línea: el ID exacto del modelo que eres.
2. Lista de los lugares del código que tuviste que tocar (función, clase o constante) y por qué.
3. Número total de pruebas y resultado (OK o fallos).
4. Cualquier dificultad o duda de diseño (máximo 5 líneas).
