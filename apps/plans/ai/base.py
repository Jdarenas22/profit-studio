"""Interfaz común de los clientes de IA (contrato, sección 4.3).

`AIClient.generate_json(task, system, payload, schema, job_ref) -> AIResult`

Errores (sus mensajes NUNCA llevan la clave, el prompt, la respuesta ni el cuerpo de error del
proveedor: solo un código corto apto para guardar en `PlanGenerationJob.error_code`):
- `AITransientError`: 429, 5xx, timeout, red. Se reintenta.
- `AIPermanentError`: 400/401/403/404, respuesta ilegible. No se reintenta.
- `AIBlockedError`: el proveedor bloqueó la petición o la respuesta por seguridad.
- `AIInvalidOutput`: la respuesta no es JSON. Cuenta como falla de validación (`V01_SCHEMA`):
  se reintenta una vez con el código del error.
"""
from dataclasses import dataclass, field

FINISH_NORMAL = 'STOP'


@dataclass(frozen=True)
class AIUsage:
    tokens_in: int = 0
    tokens_out: int = 0        # incluye los tokens de "pensamiento" (se cobran como salida)


@dataclass(frozen=True)
class AIResult:
    data: object               # JSON ya parseado (sin validar: lo valida apps/plans/rules/validate.py)
    usage: AIUsage = field(default_factory=AIUsage)
    model: str = ''
    finish_reason: str = FINISH_NORMAL


class AIError(Exception):
    """Base. `code` es lo único que se guarda o se registra."""

    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class AITransientError(AIError):
    pass


class AIPermanentError(AIError):
    pass


class AIBlockedError(AIError):
    pass


class AIInvalidOutput(AIError):
    pass


class AIClient:
    model_name = ''
    is_simulated = False

    def generate_json(self, *, task, system, payload, schema, job_ref):
        raise NotImplementedError
