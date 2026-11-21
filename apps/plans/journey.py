"""Camino guiado de la clienta hasta su plan con IA (docs/contracts/camino-guiado.md).

Solo PRESENTA lo que ya decide `services.assess`: pasos hechos, por hacer y bloqueados, el avance y
el estado de cada plan. No cambia ninguna regla, no llama a la IA, no crea trabajos y no escribe.

Privacidad: el resultado solo trae estados, textos genéricos y direcciones. Ni datos de salud ni
códigos de bandera (`R..`, `Y..`) ni los códigos `D..`. Todo se calcula sobre la usuaria recibida
(`request.user`); ninguna ruta de aquí recibe el id de otra persona.
"""
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse

from apps.health import services as health
from apps.health.models import PURPOSE_AI, PURPOSE_HEALTH_DATA
from apps.health.navigation import with_next

from . import services, wellness
from .models import KIND_EXERCISE, KIND_NUTRITION, Plan, PlanGenerationJob
from .rules import eligibility as rules

KINDS = (KIND_EXERCISE, KIND_NUTRITION)

STEP_ORDER = ('consent_health', 'profile', 'measure', 'consent_ai', 'activity')
SCOPE_ALL = 'all'
SCOPE_NUTRITION = 'nutrition'
_SCOPES = {'activity': SCOPE_NUTRITION}

# Requisito que falta -> paso. `D05` (cupo), `D06` y `D00` no son pasos: dan el estado del plan.
_STEP_FOR_CODE = {
    rules.D01_CONSENT_HEALTH: 'consent_health',
    rules.D02_PROFILE: 'profile',
    rules.D02_PROFILE_CONFIRM: 'profile',
    rules.D03_MEASURE: 'measure',
    rules.D04_INCOHERENT: 'measure',
    rules.D01_CONSENT_AI: 'consent_ai',
    rules.D07_NUTRITION_DETAILS: 'activity',
}
# Códigos de cada paso, en orden de prioridad (el primero es el «normal»)
_VARIANTS = {
    'consent_health': (rules.D01_CONSENT_HEALTH,),
    'profile': (rules.D02_PROFILE, rules.D02_PROFILE_CONFIRM),
    'measure': (rules.D03_MEASURE, rules.D04_INCOHERENT),
    'consent_ai': (rules.D01_CONSENT_AI,),
    'activity': (rules.D07_NUTRITION_DETAILS,),
}
# Cada paso depende de uno ANTERIOR de la lista (nunca de uno posterior)
_REQUIRES = {'profile': 'consent_health', 'measure': 'consent_health', 'consent_ai': 'consent_health',
             'activity': 'profile'}

# Pantalla que resuelve cada requisito: (nombre de URL, argumentos, consulta fija)
_REQUIREMENT_TARGETS = {
    rules.D01_CONSENT_HEALTH: ('member_consent', (PURPOSE_HEALTH_DATA,), ''),
    rules.D01_CONSENT_AI: ('member_consent', (PURPOSE_AI,), ''),
    rules.D02_PROFILE: ('member_health_profile', (), ''),
    rules.D02_PROFILE_CONFIRM: ('member_health_profile', (), ''),
    rules.D03_MEASURE: ('member_measurement_add', (), ''),
    rules.D04_INCOHERENT: ('member_health_profile', (), '?edit=1'),
    # Ficha básica: falta el nivel de actividad («Más detalles» de la ficha)
    rules.D07_NUTRITION_DETAILS: ('member_health_profile', (), '?edit=1'),
}
# Dónde se REVISA un paso ya hecho (sin `next`)
_REVIEW_TARGETS = {
    'consent_health': ('member_consent', (PURPOSE_HEALTH_DATA,)),
    'profile': ('member_health_profile', ()),
    'measure': ('member_measurement_list', ()),
    'consent_ai': ('member_consent', (PURPOSE_AI,)),
    'activity': ('member_health_profile', ()),
}
ACTIVITY_ANCHOR = '#mas-detalles'     # abre el asistente de la ficha directo en el paso 5

# Código -> (título, descripción, texto del botón). Las descripciones de los casos especiales son
# las de `REQUIREMENT_TEXTS`: las mismas que ve en «Pedir mi plan».
_TODO_TEXTS = {
    rules.D01_CONSENT_HEALTH: (
        'Autoriza el uso de tus datos de salud',
        'Es tu permiso para guardar tus datos de salud y usarlos en tu plan. Puedes retirarlo cuando quieras.',
        'Autorizar'),
    rules.D02_PROFILE: (
        'Completa tu ficha de salud y alimentación',
        'Responde unas preguntas sobre ti, tu salud y tu alimentación para armar un plan a tu medida.',
        'Llenar mi ficha'),
    rules.D02_PROFILE_CONFIRM: (
        'Confirma tu ficha de salud y alimentación',
        rules.REQUIREMENT_TEXTS[rules.D02_PROFILE_CONFIRM],
        'Revisar y confirmar'),
    rules.D03_MEASURE: (
        'Registra tus medidas',
        rules.REQUIREMENT_TEXTS[rules.D03_MEASURE],
        'Registrar mis medidas'),
    rules.D04_INCOHERENT: (
        'Revisa tus datos',
        rules.REQUIREMENT_TEXTS[rules.D04_INCOHERENT],
        'Revisar mis datos'),
    rules.D01_CONSENT_AI: (
        'Autoriza el apoyo de la inteligencia artificial',
        'Tus datos viajan sin tu nombre ni tus datos de contacto. Puedes retirar este permiso cuando quieras.',
        'Autorizar'),
    rules.D07_NUTRITION_DETAILS: (
        'Cuéntanos tu nivel de actividad diaria',
        rules.REQUIREMENT_TEXTS[rules.D07_NUTRITION_DETAILS],
        'Completar más detalles'),
}
_DONE_TEXTS = {
    'consent_health': 'Ya autorizaste el uso de tus datos de salud.',
    'profile': 'Tu ficha está guardada y confirmada.',
    'measure': 'Tus medidas están al día.',
    'consent_ai': 'Ya autorizaste el apoyo de la inteligencia artificial.',
    'activity': 'Ya nos contaste tu nivel de actividad diaria.',
}

# Estados de cada plan
STATE_MISSING = 'missing_steps'
STATE_READY = 'ready'
STATE_GENERATING = 'generating'
STATE_IN_REVIEW = 'in_review'
STATE_APPROVED = 'approved'
STATE_BLOCKED = 'blocked'
STATE_QUOTA = 'quota'

_KIND_WORD = {KIND_EXERCISE: 'ejercicio', KIND_NUTRITION: 'alimentación'}
MESSAGE_GENERATING = 'Estamos armando el borrador de tu plan. Esto puede tardar uno o dos minutos.'
MESSAGE_IN_REVIEW = 'Tu entrenador está revisando tu plan. Te avisaremos cuando esté aprobado.'
CTA_GENERATING = 'Ver cómo va'
CTA_APPROVED = 'Ver mi plan'

# Estado `blocked`. Aquí la clienta todavía NO pidió nada: no hay trabajo ni aviso al entrenador, así que
# estos textos no dicen que «ya fue avisado» (a diferencia de `rules.BLOCKED_MESSAGE`, que solo se muestra en
# la pantalla del trabajo, cuando sí se avisó). Dos variantes por causa: la del panel manda a hablar con el
# entrenador; la de «Pedir mi plan», que trae el botón «Avisar a mi entrenador», invita a usarlo.
_BLOCKED_GENERIC = ('Por la información de salud que registraste, lo más seguro es que tu médico o '
                    'nutricionista te valore antes de armar un plan.')
MESSAGE_BLOCKED = _BLOCKED_GENERIC + ' Habla con tu entrenador.'
MESSAGE_BLOCKED_REQUEST = _BLOCKED_GENERIC + ' Usa el botón para avisar a tu entrenador.'
# Sin ejercicios / alimentos compatibles: código -> (texto del panel, texto de «Pedir mi plan»)
_BLOCKED_FEW_ITEMS = {
    rules.D06_FEW_EXERCISES: (
        'Por ahora no hay suficientes ejercicios compatibles contigo. Habla con tu entrenador para armar tu plan.',
        'Por ahora no hay suficientes ejercicios compatibles contigo. Usa el botón para avisar a tu entrenador.'),
    rules.D06_FEW_FOODS: (
        'Por ahora no hay suficientes alimentos compatibles contigo. '
        'Habla con tu entrenador para armar tu guía de alimentación.',
        'Por ahora no hay suficientes alimentos compatibles contigo. Usa el botón para avisar a tu entrenador.'),
}


# ─── Direcciones ──────────────────────────────────────────────────────────────

def requirement_url(code, next_url):
    """Dirección que resuelve el requisito `code` (`D..`), con `?next=` de regreso a `next_url`.
    `None` si ese requisito no se resuelve en una pantalla (cupo, catálogo)."""
    target = _REQUIREMENT_TARGETS.get(code)
    if target is None:
        return None
    name, args, query = target
    return with_next(reverse(name, args=args) + query, next_url)


def _step_url(code, back_url):
    url = requirement_url(code, back_url)
    return url + ACTIVITY_ANCHOR if code == rules.D07_NUTRITION_DETAILS else url


def _review_url(step):
    name, args = _REVIEW_TARGETS[step]
    return reverse(name, args=args)


def _internal_back(back_url):
    if back_url and back_url.startswith('/') and not back_url.startswith('//'):
        return back_url
    return reverse('dashboard')


# ─── Quién tiene camino ───────────────────────────────────────────────────────

def _is_active_member(user):
    """Clienta autenticada, con la cuenta activa y membresía vigente."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_trainer or not user.is_active or user.is_erased:
        return False
    try:
        return bool(user.membership.is_valid)
    except ObjectDoesNotExist:
        return False


# ─── Estado de los planes ─────────────────────────────────────────────────────

def _decided_states(user, profile):
    """Estado de cada plan cuando ya lo decide un plan aprobado, un trabajo o un borrador
    (`{tipo: (estado, id del trabajo)}`). Dos consultas, sin importar cuántos planes haya."""
    approved, pending = set(), set()
    rows = Plan.objects.filter(
        client=user, kind__in=KINDS, status__in=(Plan.STATUS_APPROVED, *Plan.PENDING_STATUSES),
    ).values_list('kind', 'status', 'nutrition__pk')
    for kind, status, nutrition_id in rows:
        if status != Plan.STATUS_APPROVED:
            pending.add(kind)
        elif kind == KIND_EXERCISE or (nutrition_id is not None and not wellness.outside_scope(profile)):
            # Alimentación: igual que `nutrition.member_plan` (con su plan armado y dentro del alcance)
            approved.add(kind)
    running = dict(PlanGenerationJob.objects.filter(
        client=user, kind__in=KINDS, status__in=PlanGenerationJob.ACTIVE_STATUSES).values_list('kind', 'pk'))
    decided = {}
    for kind in KINDS:
        if kind in approved:
            decided[kind] = (STATE_APPROVED, None)
        elif kind in running:
            decided[kind] = (STATE_GENERATING, running[kind])
        elif kind in pending:
            decided[kind] = (STATE_IN_REVIEW, None)
    return decided


def blocked_message(assessment, on_request_screen=False):
    """Texto genérico (sin diagnósticos y sin afirmar que el entrenador ya fue avisado) de un plan bloqueado.
    Lo usan el camino guiado (`on_request_screen=False`) y «Pedir mi plan» (`True`, invita a usar el botón).
    `Assessment.blocked_message` / `BLOCKED_MESSAGE` quedan para la pantalla del trabajo, donde sí se avisó."""
    red = assessment.eligibility.red_codes
    if wellness.OUT_OF_SCOPE in red:
        return assessment.blocked_message          # «Los menús de bienestar no cubren esta solicitud…»: igual
    variant = 1 if on_request_screen else 0
    for code, texts in _BLOCKED_FEW_ITEMS.items():
        if red == (code,):
            return texts[variant]
    return MESSAGE_BLOCKED_REQUEST if on_request_screen else MESSAGE_BLOCKED


def _plan_entry(kind, decided, assessment, steps):
    word = _KIND_WORD[kind]
    if decided is not None:
        state, job_pk = decided
        if state == STATE_APPROVED:
            name = 'member_plans' if kind == KIND_EXERCISE else 'member_nutrition_plan'
            return {'state': state, 'url': reverse(name), 'cta': CTA_APPROVED,
                    'message': f'Tu plan de {word} está aprobado y listo para usar.'}
        if state == STATE_GENERATING:
            return {'state': state, 'url': reverse('member_plan_request_status', args=[job_pk]),
                    'cta': CTA_GENERATING, 'message': MESSAGE_GENERATING}
        return {'state': state, 'url': None, 'cta': None, 'message': MESSAGE_IN_REVIEW}

    eligibility = assessment.eligibility
    own = {_STEP_FOR_CODE[code] for code in eligibility.missing if code in _STEP_FOR_CODE}
    if own or (eligibility.missing and rules.D05_QUOTA not in eligibility.missing):
        # Faltan pasos (o un requisito que no es paso ni cupo: por defensa, tampoco se deja pedir)
        first = next((step for step in steps if step['code'] in own and step['status'] == 'todo'), None)
        if kind == KIND_NUTRITION and own == {'activity'}:
            message = rules.REQUIREMENT_TEXTS[rules.D07_NUTRITION_DETAILS]
        else:
            message = f'Te faltan pasos para pedir tu plan de {word}.'
        return {'state': STATE_MISSING, 'url': first['url'] if first else None,
                'cta': first['cta'] if first else None, 'message': message}
    if eligibility.missing:                       # solo el cupo
        return {'state': STATE_QUOTA, 'url': None, 'cta': None,
                'message': rules.REQUIREMENT_TEXTS[rules.D05_QUOTA]}
    if eligibility.blocked:
        return {'state': STATE_BLOCKED, 'url': None, 'cta': None, 'message': blocked_message(assessment)}
    return {'state': STATE_READY, 'url': reverse('member_plan_request') + f'?kind={kind}',
            'cta': f'Pedir mi plan de {word}',
            'message': f'Ya puedes pedir tu plan de {word}. Tu entrenador lo revisa antes de que lo veas.'}


# ─── Pasos ────────────────────────────────────────────────────────────────────

def _build_steps(missing_codes, back_url):
    pending = {}                                  # paso -> código que falta (el de mayor prioridad)
    for step in STEP_ORDER:
        code = next((code for code in _VARIANTS[step] if code in missing_codes), None)
        if code is not None:
            pending[step] = code
    steps, done = [], set()
    for step in STEP_ORDER:
        requires = _REQUIRES.get(step)
        code = pending.get(step)
        # Sin ficha no hay nivel de actividad que dar: `activity` solo se da por hecho con `profile` hecho
        satisfied = code is None and (step != 'activity' or 'profile' in done)
        if satisfied:
            status = 'done'
        elif requires is not None and requires not in done:
            status = 'locked'
        else:
            status = 'todo'
        text_code = code or _VARIANTS[step][0]
        title, description, cta = _TODO_TEXTS[text_code]
        entry = {'code': step, 'title': title, 'description': description, 'status': status,
                 'url': None, 'cta': None, 'scope': _SCOPES.get(step, SCOPE_ALL)}
        if status == 'done':
            done.add(step)
            entry['description'] = _DONE_TEXTS[step]
            entry['url'] = _review_url(step)
        elif status == 'todo':
            entry['url'] = _step_url(text_code, back_url)
            entry['cta'] = cta
        steps.append(entry)
    return steps


# ─── Resultado ────────────────────────────────────────────────────────────────

def client_journey(user, back_url=None, base=None):
    """El camino de `user` hasta su plan (forma en docs/contracts/camino-guiado.md), o `None` si no
    aplica (función apagada, no es clienta, sin membresía vigente, cuenta inactiva o menor de edad).

    `back_url`: ruta interna a la que vuelve la clienta al terminar un paso (por defecto, el panel).
    `base`: `services.assess_base(...)` ya calculado por quien llama (evita repetir consultas)."""
    if not _is_active_member(user):
        return None
    available = base.available if base is not None else services.feature_available()
    if not available:
        return None
    profile = base.profile if base is not None else health.current_profile(user)
    if health.is_minor(user, profile=profile):
        return None
    if base is None:
        base = services.assess_base(user, user, profile=profile, available=True)
    back_url = _internal_back(back_url)

    decided = _decided_states(user, profile)
    # Una evaluación por tipo. Si el estado ya lo decide un plan, un trabajo o un borrador no hace falta
    # filtrar el catálogo (ahí solo se detecta lo que impide pedir): se evita esa consulta y el cálculo.
    assessments = {kind: services.assess(user, kind, user, base=base, deep=kind not in decided)
                   for kind in KINDS}
    missing = {code for assessment in assessments.values() for code in assessment.eligibility.missing}

    steps = _build_steps(missing, back_url)
    plans = {kind: _plan_entry(kind, decided.get(kind), assessments[kind], steps) for kind in KINDS}
    return {
        'steps': steps,
        'done_count': sum(1 for step in steps if step['status'] == 'done'),
        'total': len(steps),
        'next_step': next((step for step in steps if step['status'] == 'todo'), None),
        'plans': plans,
        'all_ready': all(step['status'] == 'done' for step in steps if step['scope'] == SCOPE_ALL),
        'all_done': all(step['status'] == 'done' for step in steps),
    }
