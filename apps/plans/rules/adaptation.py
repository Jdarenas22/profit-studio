"""Perfil de adaptación de la clienta (diseño: docs/contracts/plan-ia-adaptacion.md).

Módulo PURO (sin Django ni red): recibe datos simples (fechas, ids, números) y devuelve un
`AdaptationProfile` inmutable. La lectura de la base de datos y la integración con el payload de la
IA son de pasos posteriores; aquí solo viven los cálculos y sus umbrales.

Principios:
- Es un resumen de preferencias SUAVES. Nunca amplía los catálogos permitidos ni cambia metas
  calóricas, topes ni reglas de seguridad (las aplican los otros módulos de `rules/`).
- Solo produce enumeraciones, números e ids de nuestros catálogos: nada de texto libre de la clienta.
- Con poca evidencia no concluye nada (arranque en frío y mínimos de repetición).

[A VALIDAR] Los umbrales son valores iniciales para calibrar con clientas reales; el rango de
cambio de peso esperado por meta lo debe revisar un profesional de la salud.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import catalog as rules_catalog
from . import foods as rules_foods

ADAPTATION_VERSION = 'adaptation/1'

# ─── Umbrales ─────────────────────────────────────────────────────────────────
WINDOW_DAYS = 28                    # ventana de adherencia
MIN_DAYS_OF_HISTORY = 14            # menos que esto: arranque en frío, no hay historial
ADHERENCE_LOW = 0.5                 # por debajo: "low"
ADHERENCE_HIGH = 0.8                # desde aquí: "high"
SKIPPED_DAY_MAX_RATE = 0.4          # un día de rutina con menos cumplimiento que esto se "salta"
SKIPPED_DAY_MIN_WEEKS = 3           # semanas completas de datos para hablar de días saltados
SKIPPED_DAYS_MAX = 2
LIKED_MIN_MEALS = 3                 # comidas completadas con el alimento para decir que le gusta
AVOID_MIN_DISCARDS = 2              # opciones descartadas con el alimento (y ninguna completada)
TRAINER_EDIT_MIN_PLANS = 2          # planes distintos donde el entrenador quitó lo mismo
PROGRESS_WINDOW_DAYS = 42
PROGRESS_MIN_SPAN_DAYS = 14         # entre la primera y la última medición
PROGRESS_STALLED_KG_WEEK = 0.1      # cambio semanal menor a esto = estancada
LOSS_TOO_FAST_KG_WEEK = 1.0         # perder más que esto por semana no es "buen camino"
GAIN_TOO_FAST_KG_WEEK = 0.5         # ganar más que esto por semana no es "buen camino"
REVIEW_MIN_WEEKS = 3                # semanas de datos para sugerir revisar las calorías

ADHERENCE_LEVELS = ('low', 'medium', 'high')
PROGRESS_VALUES = ('on_track', 'stalled', 'off_track', 'insufficient_data', 'not_applicable')

GOAL_WEIGHT_LOSS = 'weight_loss'
GOAL_MUSCLE_GAIN = 'muscle_gain'
# Solo estas metas se pueden juzgar con el peso; en las demás el peso no dice si va bien
TRACKED_GOALS = (GOAL_WEIGHT_LOSS, GOAL_MUSCLE_GAIN)


# ─── Entrada ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TrainingDay:
    """Un día de la rutina vigente (`id` es el de `RoutineDay`; `order` ordena los empates)."""
    id: int
    name: str
    order: int = 0


@dataclass(frozen=True)
class TrainingLog:
    """Un día de entrenamiento marcado como completado."""
    day_id: int
    on: date


@dataclass(frozen=True)
class WeightPoint:
    on: date
    kg: float


@dataclass(frozen=True)
class AdaptationInput:
    """Todo lo que el cálculo necesita, ya extraído de la base de datos.

    - `history_since`: primer día con un plan aprobado de esta clienta (None = nunca tuvo uno).
    - `completed_meals`: una entrada por comida marcada como comida, con los ids de sus alimentos.
    - `discarded_options`: una entrada por opción que pidió reemplazar, con los ids de sus alimentos.
    - `trainer_removals`: una entrada por plan anterior con lo que el entrenador quitó del borrador.
    """
    today: date
    history_since: date | None = None
    goal: str = ''
    days_per_week: int = 3
    training_days: tuple = ()
    training_logs: tuple = ()
    completed_meals: tuple = ()
    discarded_options: tuple = ()
    trainer_removals: tuple = ()
    weights: tuple = ()


@dataclass(frozen=True)
class TrainerRemoval:
    """Lo que el entrenador quitó de UN plan anterior respecto al borrador de la IA."""
    exercise_ids: frozenset = frozenset()
    food_ids: frozenset = frozenset()


# ─── Salida ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AdaptationProfile:
    ready: bool = False
    weeks_of_data: int = 0
    adherence: str | None = None
    skipped_day_names: tuple = ()
    liked_food_ids: tuple = ()
    avoid_food_ids: tuple = ()
    removed_exercise_ids: tuple = ()
    removed_food_ids: tuple = ()
    progress: str = 'insufficient_data'
    suggest_calorie_review: bool = False
    reasons: tuple = field(default=(), compare=False)   # códigos para el panel del entrenador

    @property
    def has_signal(self):
        return bool(self.adherence or self.skipped_day_names or self.liked_food_ids or self.avoid_food_ids
                    or self.removed_exercise_ids or self.removed_food_ids
                    or self.progress in ('on_track', 'stalled', 'off_track'))

    def as_payload(self):
        """El bloque `history` que viaja a la IA (sección 3.1 del diseño), o `None` si no hay nada
        que decir. Solo enumeraciones, números e ids."""
        if not self.ready or not self.has_signal:
            return None
        return {
            'weeks_of_data': self.weeks_of_data,
            'training': {'adherence': self.adherence, 'skipped_day_names': list(self.skipped_day_names)},
            'nutrition': {'liked_food_ids': list(self.liked_food_ids), 'avoid_food_ids': list(self.avoid_food_ids)},
            'trainer_edits': {'removed_exercise_ids': list(self.removed_exercise_ids),
                              'removed_food_ids': list(self.removed_food_ids)},
            'progress': self.progress,
        }

    def summary(self):
        """Resumen de CÓDIGOS para guardar en el trabajo y mostrarlo al entrenador (sin datos
        personales ni nombres)."""
        return {
            'version': ADAPTATION_VERSION, 'ready': self.ready, 'weeks_of_data': self.weeks_of_data,
            'adherence': self.adherence, 'progress': self.progress,
            'skipped_days': len(self.skipped_day_names), 'liked_foods': len(self.liked_food_ids),
            'avoided_foods': len(self.avoid_food_ids),
            'removed_exercises': len(self.removed_exercise_ids), 'removed_foods': len(self.removed_food_ids),
            'suggest_calorie_review': self.suggest_calorie_review, 'reasons': list(self.reasons),
        }


COLD_START = AdaptationProfile()


# ─── Señales ──────────────────────────────────────────────────────────────────

def _elapsed_days(data):
    return (data.today - data.history_since).days


def _window_start(data):
    return max(data.today - timedelta(days=WINDOW_DAYS), data.history_since)


def _window_weeks(data):
    """Semanas (con decimales) que cubre la ventana de adherencia."""
    return (data.today - _window_start(data)).days / 7


def training_adherence(data):
    """`'low'`, `'medium'`, `'high'` o `None` si no hay base para calcularla."""
    weeks = _window_weeks(data)
    if weeks <= 0 or data.days_per_week <= 0:
        return None
    start = _window_start(data)
    done = sum(1 for log in data.training_logs if start < log.on <= data.today)
    rate = min(1.0, done / (data.days_per_week * weeks))
    if rate < ADHERENCE_LOW:
        return 'low'
    if rate >= ADHERENCE_HIGH:
        return 'high'
    return 'medium'


def skipped_days(data):
    """Nombres de hasta 2 días de la rutina que casi nunca hace (cumplimiento menor a 40 %)."""
    full_weeks = int(_window_weeks(data))
    if full_weeks < SKIPPED_DAY_MIN_WEEKS or not data.training_days:
        return ()
    start = _window_start(data)
    counts = {day.id: 0 for day in data.training_days}
    for log in data.training_logs:
        if start < log.on <= data.today and log.day_id in counts:
            counts[log.day_id] += 1
    rows = []
    for day in data.training_days:
        rate = counts[day.id] / full_weeks
        if rate < SKIPPED_DAY_MAX_RATE:
            rows.append((rate, day.order, day.id, day.name))
    rows.sort()
    return tuple(name for _rate, _order, _id, name in rows[:SKIPPED_DAYS_MAX])


def _food_counts(groups):
    counts = {}
    for food_ids in groups:
        for food_id in set(food_ids):
            counts[food_id] = counts.get(food_id, 0) + 1
    return counts


def food_preferences(data):
    """`(liked, avoid)`: ids de alimentos. Le gusta = en ≥3 comidas completadas. Evitar = en ≥2
    opciones descartadas y en ninguna completada (así los dos grupos nunca se cruzan)."""
    eaten = _food_counts(data.completed_meals)
    discarded = _food_counts(data.discarded_options)
    liked = sorted(food_id for food_id, count in eaten.items() if count >= LIKED_MIN_MEALS)
    avoid = sorted(food_id for food_id, count in discarded.items()
                   if count >= AVOID_MIN_DISCARDS and food_id not in eaten)
    return tuple(liked), tuple(avoid)


def trainer_corrections(data):
    """`(exercise_ids, food_ids)` que el entrenador quitó en ≥2 planes distintos."""
    exercises = _food_counts(removal.exercise_ids for removal in data.trainer_removals)
    foods = _food_counts(removal.food_ids for removal in data.trainer_removals)
    return (tuple(sorted(i for i, count in exercises.items() if count >= TRAINER_EDIT_MIN_PLANS)),
            tuple(sorted(i for i, count in foods.items() if count >= TRAINER_EDIT_MIN_PLANS)))


def weekly_weight_change(data):
    """Cambio de peso en kg/semana (pendiente de la recta por mínimos cuadrados) en los últimos 42
    días, o `None` si hay menos de 2 mediciones o no cubren al menos 14 días."""
    since = data.today - timedelta(days=PROGRESS_WINDOW_DAYS)
    points = sorted((p for p in data.weights if since <= p.on <= data.today), key=lambda p: p.on)
    if len(points) < 2 or (points[-1].on - points[0].on).days < PROGRESS_MIN_SPAN_DAYS:
        return None
    xs = [(p.on - points[0].on).days / 7 for p in points]
    ys = [float(p.kg) for p in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance


def progress_status(data):
    """Compara la tendencia del peso con la meta. Solo pérdida de peso y ganancia muscular se
    pueden juzgar con el peso; en las demás metas devuelve `'not_applicable'`."""
    if data.goal not in TRACKED_GOALS:
        return 'not_applicable'
    change = weekly_weight_change(data)
    if change is None:
        return 'insufficient_data'
    if abs(change) < PROGRESS_STALLED_KG_WEEK:
        return 'stalled'
    if data.goal == GOAL_WEIGHT_LOSS:
        return 'on_track' if -LOSS_TOO_FAST_KG_WEEK < change < 0 else 'off_track'
    return 'on_track' if 0 < change <= GAIN_TOO_FAST_KG_WEEK else 'off_track'


# ─── Perfil completo ──────────────────────────────────────────────────────────

def build_profile(data):
    """Calcula el perfil. Con menos de 14 días de historial devuelve el arranque en frío (`ready`
    en falso y sin señales)."""
    if data.history_since is None or _elapsed_days(data) < MIN_DAYS_OF_HISTORY:
        return COLD_START
    adherence = training_adherence(data)
    skipped = skipped_days(data)
    liked, avoid = food_preferences(data)
    removed_exercises, removed_foods = trainer_corrections(data)
    progress = progress_status(data)
    weeks = _elapsed_days(data) // 7
    review = (progress == 'stalled' and adherence == 'high' and weeks >= REVIEW_MIN_WEEKS
              and data.goal in TRACKED_GOALS)

    reasons = []
    if adherence in ('low', 'high'):
        reasons.append(f'adherence_{adherence}')
    if skipped:
        reasons.append('skipped_days')
    if liked:
        reasons.append('liked_foods')
    if avoid:
        reasons.append('avoid_foods')
    if removed_exercises:
        reasons.append('trainer_removed_exercises')
    if removed_foods:
        reasons.append('trainer_removed_foods')
    if progress in ('on_track', 'stalled', 'off_track'):
        reasons.append(f'progress_{progress}')
    if review:
        reasons.append('calorie_review_suggested')

    return AdaptationProfile(
        ready=True, weeks_of_data=weeks, adherence=adherence, skipped_day_names=skipped,
        liked_food_ids=liked, avoid_food_ids=avoid, removed_exercise_ids=removed_exercises,
        removed_food_ids=removed_foods, progress=progress, suggest_calorie_review=review,
        reasons=tuple(reasons))


# ─── Exclusiones con resguardo ────────────────────────────────────────────────

def exclude_foods(items, avoid_ids):
    """Quita de los alimentos permitidos los que la clienta descarta, SOLO si lo que queda sigue
    siendo suficiente (mismo criterio que `D06_FEW_FOODS`). Devuelve `(items, ids_quitados)`."""
    avoid = set(avoid_ids)
    kept = tuple(item for item in items if item.id not in avoid)
    removed = tuple(sorted(item.id for item in items if item.id in avoid))
    if not removed or not rules_foods.FoodCatalog(allowed=kept).enough:
        return tuple(items), ()
    return kept, removed


def exclude_exercises(items, removed_ids):
    """Quita de los ejercicios permitidos los que el entrenador suele eliminar, SOLO si quedan
    suficientes (`MIN_ALLOWED_EXERCISES`). Devuelve `(items, ids_quitados)`."""
    drop = set(removed_ids)
    kept = tuple(item for item in items if item.id not in drop)
    removed = tuple(sorted(item.id for item in items if item.id in drop))
    if not removed or not rules_catalog.CatalogResult(allowed=kept, limits=rules_catalog.Limits()).enough:
        return tuple(items), ()
    return kept, removed
