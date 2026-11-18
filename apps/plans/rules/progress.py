"""Progreso de la clienta para el entrenador (fase D, docs/contracts/plan-ia-fase-d.md, sección 6).

Módulo PURO (sin Django ni red): recibe datos simples (fechas, números, `Decimal`) y devuelve
diccionarios con la forma exacta que usan las plantillas. La lectura de la base de datos está en
`apps/plans/progress.py`.

- Constancia (adherencia): misma ventana y mismos cortes que `rules/adaptation.py` (las constantes
  se IMPORTAN, no se copian), así el nivel de entrenamiento coincide con el del panel «Qué se tuvo
  en cuenta».
- Gráficos: nada de librerías ni JavaScript. Python calcula los puntos de un `<svg>` y los entrega
  como TEXTO con punto decimal: una plantilla localizada (es-CO) los pondría con coma y romperían
  los atributos SVG.

Los cortes de constancia son los de la adaptación y siguen **[A VALIDAR]** por un profesional.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .. import texts
from . import adaptation as rules_adaptation

WEEKS = 4
WEEK_DAYS = 7
WINDOW_DAYS = rules_adaptation.WINDOW_DAYS                  # 28
MIN_DAYS_OF_HISTORY = rules_adaptation.MIN_DAYS_OF_HISTORY  # 14
ADHERENCE_LOW = rules_adaptation.ADHERENCE_LOW              # 0,5
ADHERENCE_HIGH = rules_adaptation.ADHERENCE_HIGH            # 0,8
MEASURE_DUE_DAYS = 14                                       # se invita a medir cada 14 días

KIND_EXERCISE = 'exercise'
KIND_NUTRITION = 'nutrition'
KIND_LABELS = {KIND_EXERCISE: 'Plan de ejercicio', KIND_NUTRITION: 'Plan de alimentación'}

# Geometría fija de los gráficos (atributo `viewBox` y rectángulo de dibujo)
VIEW_BOX = '0 0 640 240'
PLOT_LEFT, PLOT_RIGHT, PLOT_TOP, PLOT_BOTTOM = 56, 624, 16, 200
Y_TICKS = 3


# ─── Constancia de 4 semanas ──────────────────────────────────────────────────

def window_start(today, since):
    """Último día ANTES de la ventana: se cuentan los días `inicio < día <= hoy`. Con `since` (fecha
    del primer plan aprobado) la ventana no empieza antes de él; sin plan, son los últimos 28 días."""
    start = today - timedelta(days=WINDOW_DAYS)
    return max(start, since) if since is not None else start


def week_ranges(today):
    """4 semanas de 7 días hacia atrás desde hoy, de la más antigua (1) a la más reciente (4):
    `[(índice, primer_día, último_día)]`. Juntas cubren `(hoy - 28, hoy]`."""
    ranges = []
    for index in range(1, WEEKS + 1):
        last = today - timedelta(days=WEEK_DAYS * (WEEKS - index))
        ranges.append((index, last - timedelta(days=WEEK_DAYS - 1), last))
    return ranges


def _level(rate):
    if rate < ADHERENCE_LOW:
        return 'low'
    if rate >= ADHERENCE_HIGH:
        return 'high'
    return 'medium'


def _count(days, first, last):
    return sum(1 for day in days if first <= day <= last)


def adherence_block(*, days, today, since, has_plan, per_week, plan_note, level_texts):
    """Constancia de un tipo (entrenamiento o comidas).

    - `days`: fechas de cada marca (una fila de `RoutineDayLog` o `MealChoice` marcada = una fecha).
    - `since`: fecha local del primer plan aprobado (o `None`).
    - `per_week`: meta semanal (días por semana, o comidas por día × 7) o `None` si no se conoce.
    - `plan_note`: texto cuando no hay plan del tipo.

    Con menos de 14 días de historial (arranque en frío), sin plan o sin meta no hay porcentaje ni
    nivel: solo lo hecho y una nota que explica por qué.
    """
    start = window_start(today, since)
    in_window = [day for day in days if start < day <= today]
    window_days = (today - start).days
    done = len(in_window)
    cold = since is None or (today - since).days < MIN_DAYS_OF_HISTORY
    note = ''
    expected = rate = level = None
    if not has_plan:
        note = plan_note
    elif cold:
        note = texts.NOTE_COLD_START
    elif per_week is None:
        note = texts.NOTE_NO_PROFILE
    else:
        # Mismo cálculo que `rules.adaptation.training_adherence` (así coinciden hasta en los cortes exactos)
        weeks = window_days / WEEK_DAYS
        expected_exact = per_week * weeks
        share = min(1.0, done / expected_exact) if expected_exact > 0 else None
        if share is not None:
            expected = int(expected_exact + 0.5)
            rate = int(share * 100 + 0.5)
            level = _level(share)
    return {
        'done': done, 'expected': expected,
        'per_week': per_week if (has_plan and not cold and rate is not None) else None,
        'rate': rate, 'level': level,
        'level_text': level_texts.get(level, '') if level else '',
        'has_plan': has_plan, 'note': note,
    }, in_window


def adherence(*, today, training_days, meal_days, training_since, meals_since, exercise_plan_ever,
              days_per_week, meals_per_day, has_current_nutrition, meals_visible):
    """Todo el bloque `adherence` del contexto (sección 6.5).

    `meal_days` es `None` cuando no se pueden mostrar comidas (sin consentimiento); `days_per_week`
    es `None` sin ficha o sin consentimiento (no hay porcentaje de entrenamiento)."""
    training, training_in = adherence_block(
        days=training_days, today=today, since=training_since, has_plan=exercise_plan_ever,
        per_week=days_per_week, plan_note=texts.NOTE_NO_EXERCISE_PLAN,
        level_texts=texts.TRAINING_LEVEL_TEXT)
    meals = None
    meals_in = []
    if meals_visible:
        nutrition_plan = meals_since is not None
        meals, meals_in = adherence_block(
            days=meal_days or (), today=today, since=meals_since, has_plan=nutrition_plan,
            per_week=meals_per_day * WEEK_DAYS if (nutrition_plan and has_current_nutrition and meals_per_day)
            else None,
            plan_note=texts.NOTE_NO_NUTRITION_PLAN, level_texts=texts.MEALS_LEVEL_TEXT)
        if nutrition_plan and not has_current_nutrition and meals['note'] == texts.NOTE_NO_PROFILE:
            meals['note'] = texts.NOTE_NO_CURRENT_NUTRITION_PLAN
    start = window_start(today, training_since)
    weeks = [{
        'index': index, 'first_day': first, 'last_day': last,
        'training_done': _count(training_in, first, last),
        'meals_done': _count(meals_in, first, last) if meals_visible else None,
    } for index, first, last in week_ranges(today)]
    return {
        'start': start + timedelta(days=1), 'end': today, 'days': (today - start).days,
        'weeks': weeks, 'training': training, 'meals': meals,
    }


# ─── Medidas: línea base y cambios ────────────────────────────────────────────

@dataclass(frozen=True)
class MeasurementPoint:
    pk: int
    date: date
    weight: Decimal
    waist_cm: Decimal | None = None
    hip_cm: Decimal | None = None
    body_fat_pct: Decimal | None = None
    imc: Decimal | None = None
    needs_review: bool = False

    @property
    def order(self):
        return (self.date, self.pk)


def latest_reliable(points):
    """La medida más reciente SIN la marca de posible error (o `None`)."""
    reliable = [p for p in points if not p.needs_review]
    return max(reliable, key=lambda p: p.order) if reliable else None


def pick_baseline(candidates):
    """Entre los planes aprobados hoy con medida base, la medida MÁS ANTIGUA (empate: ejercicio).
    `candidates` = `[(kind, version, MeasurementPoint)]`; devuelve el elegido o `None`."""
    if not candidates:
        return None
    return min(candidates, key=lambda c: (c[2].date, c[2].pk, 0 if c[0] == KIND_EXERCISE else 1))


def _delta(latest, reference):
    if latest is None or reference is None:
        return None
    return latest - reference


def baseline_info(chosen, points):
    """`baseline` del contexto (sección 6.4) o `None` si ningún plan aprobado tiene medida base."""
    if chosen is None:
        return None
    kind, version, ref = chosen
    latest = latest_reliable(points)
    same = latest is None or latest.order <= ref.order
    delta = {'weight': None, 'waist_cm': None, 'hip_cm': None, 'imc': None}
    if not same:
        delta = {name: _delta(getattr(latest, name), getattr(ref, name)) for name in delta}
    return {
        'plan_kind': kind, 'plan_kind_label': KIND_LABELS[kind], 'plan_version': version,
        'date': ref.date, 'weight': ref.weight, 'waist_cm': ref.waist_cm, 'hip_cm': ref.hip_cm, 'imc': ref.imc,
        'latest_date': latest.date if latest is not None else None,
        'days_between': (latest.date - ref.date).days if (latest is not None and not same) else None,
        'same_measurement': same,
        'delta': delta,
    }


def measure_due(points, today):
    """`(¿toca medir?, texto)`: más de 14 días sin medir, o sin medidas."""
    if not points:
        return True, 'Todavía no tiene medidas registradas.'
    days_old = (today - max(points, key=lambda p: p.order).date).days
    if days_old > MEASURE_DUE_DAYS:
        return True, f'Hace {days_old} días que no registra medidas.'
    return False, ''


# ─── Gráficos con puntos para un <svg> ────────────────────────────────────────

def _quantize(value, decimals):
    step = Decimal(1).scaleb(-decimals)
    return Decimal(value).quantize(step, rounding=ROUND_HALF_UP)


def format_number(value, decimals=1):
    """Número con coma decimal (es-CO) para frases: `72,5`."""
    return str(_quantize(value, decimals)).replace('.', ',')


def _signed(value, decimals):
    text = format_number(value, decimals)
    return text if text.startswith('-') or Decimal(value) == 0 else f'+{text}'


def _fmt_date(day):
    return day.strftime('%d/%m/%Y')


def _coord(value):
    # Siempre con punto y un decimal: es un atributo SVG, no un número localizado
    return f'{value:.1f}'


def build_chart(*, key, label, unit, rows, baseline_pk=None, decimals=1):
    """Un gráfico de la sección 6.3. `rows` = `[(pk, fecha, valor o None, needs_review)]`, más antigua
    primero; las filas sin valor se saltan."""
    data = [(pk, day, Decimal(value), review) for pk, day, value, review in rows if value is not None]
    unit_text = f' {unit}' if unit else ''
    name = label if label.isupper() else label.lower()
    chart = {
        'key': key, 'label': label, 'unit': unit, 'count': len(data), 'has_chart': len(data) >= 2,
        'view_box': VIEW_BOX,
        'plot': {'left': str(PLOT_LEFT), 'right': str(PLOT_RIGHT), 'top': str(PLOT_TOP),
                 'bottom': str(PLOT_BOTTOM)},
        'points': [], 'polyline': '', 'y_ticks': [], 'x_ticks': [], 'summary': f'Sin datos de {name}.',
    }
    if not data:
        return chart
    first_day, last_day = data[0][1], data[-1][1]
    span = (last_day - first_day).days
    middle = (PLOT_LEFT + PLOT_RIGHT) / 2
    low = min(value for _pk, _day, value, _review in data)
    high = max(value for _pk, _day, value, _review in data)
    pad = Decimal(1) if low == high else (high - low) * Decimal('0.1')
    bottom_value, top_value = low - pad, high + pad
    height = PLOT_BOTTOM - PLOT_TOP

    def x_of(day):
        return middle if span == 0 else PLOT_LEFT + (day - first_day).days / span * (PLOT_RIGHT - PLOT_LEFT)

    def y_of(value):
        return PLOT_BOTTOM - float((value - bottom_value) / (top_value - bottom_value)) * height

    for pk, day, value, review in data:
        chart['points'].append({
            'x': _coord(x_of(day)), 'y': _coord(y_of(value)), 'date': day, 'value': value,
            'text': f'{format_number(value, decimals)}{unit_text} el {_fmt_date(day)}',
            'needs_review': review, 'is_baseline': pk == baseline_pk,
        })
    if chart['has_chart']:
        chart['polyline'] = ' '.join(f"{p['x']},{p['y']}" for p in chart['points'])
    middle_value = (bottom_value + top_value) / 2
    chart['y_ticks'] = [{'y': _coord(y_of(v)), 'label': format_number(v, decimals)}
                        for v in (bottom_value, middle_value, top_value)]
    chart['x_ticks'] = [{'x': _coord(x_of(first_day)), 'label': _fmt_date(first_day)}]
    if last_day != first_day:
        chart['x_ticks'].append({'x': _coord(x_of(last_day)), 'label': _fmt_date(last_day)})
    first_value, last_value = data[0][2], data[-1][2]
    if len(data) == 1:
        chart['summary'] = (f'{label}: una sola medición, {format_number(first_value, decimals)}{unit_text} '
                            f'({_fmt_date(first_day)}).')
    else:
        chart['summary'] = (f'{label}: de {format_number(first_value, decimals)}{unit_text} ({_fmt_date(first_day)}) '
                            f'a {format_number(last_value, decimals)}{unit_text} ({_fmt_date(last_day)}); '
                            f'cambió {_signed(last_value - first_value, decimals)}{unit_text}.')
    return chart
