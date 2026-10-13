"""Cliente de IA de los planes (contrato, sección 4).

Modos (`AI_PLANS_MODE`):
- `off` (por defecto en producción): la función no se ofrece.
- `simulated` (por defecto con DEBUG): `FakeAIClient`, sin red ni costo; los planes quedan
  marcados "MODO DE PRUEBA" y no se pueden aprobar fuera de DEBUG.
- `live`: `GeminiClient`. production.py no arranca en `live` sin `GEMINI_API_KEY`,
  `GEMINI_MODEL` y `GEMINI_BILLING_CONFIRMED=true`.

Las pruebas reemplazan el cliente con `mock.patch('apps.plans.ai.get_ai_client', ...)` o con
`override_settings(AI_PLANS_MODE='simulated')`.
"""
from django.conf import settings

from .base import AIBlockedError, AIClient, AIError, AIInvalidOutput, AIPermanentError, AIResult, AITransientError

MODE_OFF = 'off'
MODE_SIMULATED = 'simulated'
MODE_LIVE = 'live'
MODES = (MODE_OFF, MODE_SIMULATED, MODE_LIVE)


def ai_mode():
    """Modo vigente. Vacío = `simulated` con DEBUG y `off` sin él. Un valor desconocido = `off`."""
    mode = (getattr(settings, 'AI_PLANS_MODE', '') or '').strip().lower()
    if not mode:
        return MODE_SIMULATED if settings.DEBUG else MODE_OFF
    return mode if mode in MODES else MODE_OFF


def ai_enabled():
    return ai_mode() != MODE_OFF


def live_is_configured():
    return bool(getattr(settings, 'GEMINI_API_KEY', '') and getattr(settings, 'GEMINI_MODEL', '')
                and getattr(settings, 'GEMINI_BILLING_CONFIRMED', False))


def get_ai_client():
    """Cliente según el modo. Con `off` (o `live` mal configurado) lanza `AIPermanentError`."""
    mode = ai_mode()
    if mode == MODE_SIMULATED:
        from .fake import FakeAIClient
        return FakeAIClient()
    if mode == MODE_LIVE and live_is_configured():
        from .gemini import GeminiClient
        return GeminiClient(
            api_key=settings.GEMINI_API_KEY, model=settings.GEMINI_MODEL,
            timeout=settings.GEMINI_TIMEOUT_SECONDS, schema_field=settings.GEMINI_SCHEMA_FIELD,
        )
    raise AIPermanentError('ai_not_configured')


__all__ = [
    'AIBlockedError', 'AIClient', 'AIError', 'AIInvalidOutput', 'AIPermanentError', 'AIResult',
    'AITransientError', 'MODES', 'MODE_LIVE', 'MODE_OFF', 'MODE_SIMULATED', 'ai_enabled', 'ai_mode',
    'get_ai_client', 'live_is_configured',
]
