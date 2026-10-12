"""¿Se puede generar un plan con IA para esta clienta? (requisitos previos + banderas rojas).

Módulo PURO (sin Django ni red). Referencia: docs/contracts/plan-ia.md, sección 3.1.
La IA nunca decide si se puede generar: lo decide este módulo antes de llamarla.

- Requisitos previos (`D..`): datos que la clienta puede completar (consentimientos, ficha,
  medidas, cupo de uso). Mientras falte alguno no se crea trabajo.
- Banderas rojas del alcance pedido: bloquean la generación automática y derivan (el trabajo
  queda `blocked` con los CÓDIGOS, sin texto médico, y se avisa al entrenador).
"""
from dataclasses import dataclass

from .flags import YELLOW_COMPANION

D00_NOT_AVAILABLE = 'D00_NOT_AVAILABLE'   # función apagada o textos legales sin revisar
D01_CONSENT_HEALTH = 'D01_CONSENT_HEALTH'
D01_CONSENT_AI = 'D01_CONSENT_AI'
D02_PROFILE = 'D02_PROFILE'
D02_PROFILE_CONFIRM = 'D02_PROFILE_CONFIRM'
D03_MEASURE = 'D03_MEASURE'
D04_INCOHERENT = 'D04_INCOHERENT'
D05_QUOTA = 'D05_QUOTA'
D06_FEW_EXERCISES = 'D06_FEW_EXERCISES'
D06_FEW_FOODS = 'D06_FEW_FOODS'

# Textos para la clienta (sin diagnósticos)
REQUIREMENT_TEXTS = {
    D00_NOT_AVAILABLE: 'Los planes con IA aún no están disponibles.',
    D01_CONSENT_HEALTH: 'Autoriza el tratamiento de tus datos de salud.',
    D01_CONSENT_AI: 'Autoriza el envío sin identificar de tus datos a la inteligencia artificial.',
    D02_PROFILE: 'Llena tu ficha de salud y alimentación.',
    D02_PROFILE_CONFIRM: 'Tu entrenador corrigió tu ficha: revísala y confírmala.',
    D03_MEASURE: 'Registra tus medidas (peso y estatura) de los últimos 30 días.',
    D04_INCOHERENT: 'Revisa tus datos: el peso, la estatura o la fecha de nacimiento no parecen correctos.',
    D05_QUOTA: 'Ya pediste varios planes esta semana. Podrás pedir otro en unos días.',
    D06_FEW_EXERCISES: 'Por ahora no hay suficientes ejercicios compatibles contigo. Tu entrenador armará tu plan.',
    D06_FEW_FOODS: 'Por ahora no hay suficientes alimentos compatibles contigo. Tu entrenador armará tu guía de alimentación.',
}

# Mensaje genérico ante una bandera roja (contrato 3.3): sin diagnósticos
BLOCKED_MESSAGE = ('Por la información de salud que registraste, lo más seguro es que tu médico o '
                   'nutricionista te valore antes de armar un plan. Tu entrenador ya fue avisado y te '
                   'contactará.')

# Banderas faltantes del motor (`flags.py`) -> requisito previo
_MISSING_TO_REQUIREMENT = {
    'M04_NO_PROFILE': D02_PROFILE,
    'M04_PROFILE_UNCONFIRMED': D02_PROFILE_CONFIRM,
    'M01_NO_MEASURE': D03_MEASURE,
    'M02_MEASURE_OLD': D03_MEASURE,
    'M03_NO_HEIGHT': D03_MEASURE,
    'M08_INCOHERENT': D04_INCOHERENT,
    'M09_NO_AI_CONSENT': D01_CONSENT_AI,
}
_ORDER = (D00_NOT_AVAILABLE, D01_CONSENT_HEALTH, D01_CONSENT_AI, D02_PROFILE, D02_PROFILE_CONFIRM,
          D03_MEASURE, D04_INCOHERENT, D05_QUOTA)


@dataclass(frozen=True)
class Eligibility:
    kind: str
    missing: tuple = ()          # códigos D.. en orden de pantalla
    red_codes: tuple = ()        # banderas rojas que bloquean este alcance
    yellow_codes: tuple = ()     # amarillas de este alcance (el plan se genera con advertencias)

    @property
    def blocked(self):
        return not self.missing and bool(self.red_codes)

    @property
    def can_request(self):
        return not self.missing and not self.red_codes

    @property
    def conservative(self):
        """A la IA solo le llega este booleano genérico cuando hay advertencias (sin diagnósticos)."""
        return bool(self.yellow_codes)

    @property
    def missing_texts(self):
        return [REQUIREMENT_TEXTS[code] for code in self.missing]


def evaluate(report, kind, *, available=True, health_consent=True, quota_ok=True):
    """Combina el `FlagReport` de la ficha con los requisitos de la plataforma.

    - `available`: la función está encendida y los textos legales listos (`ai_texts_ready`).
    - `health_consent`: consentimiento `health_data` vigente.
    - `quota_ok`: la clienta no superó los límites de uso.
    """
    missing = set()
    if not available:
        missing.add(D00_NOT_AVAILABLE)
    if not health_consent:
        missing.add(D01_CONSENT_HEALTH)
    for flag in report.missing:
        code = _MISSING_TO_REQUIREMENT.get(flag.code)
        if code is not None and kind in (flag.scopes or (kind,)):
            missing.add(code)
    if not quota_ok:
        missing.add(D05_QUOTA)
    red = tuple(flag.code for flag in report.red if kind in flag.scopes)
    yellow = tuple(flag.code for flag in report.yellow if kind in flag.scopes)
    # Rojo de un solo alcance que deja este en amarillo (p. ej. R08 deja el ejercicio en amarillo)
    yellow += tuple(flag.code for flag in report.red
                    if kind not in flag.scopes and YELLOW_COMPANION.get(flag.code) == kind)
    return Eligibility(
        kind=kind,
        missing=tuple(code for code in _ORDER if code in missing),
        red_codes=red,
        yellow_codes=yellow,
    )
