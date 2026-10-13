"""Cliente REAL de Google Gemini (modo `live`), por REST con la biblioteca estándar.

Sin SDK de Google ni dependencias nuevas (`urllib`): control total del tiempo de espera y de lo
que se registra. Endpoint `generateContent` de la API de Gemini (v1beta).

Seguridad (contrato, sección 4):
- La clave va en la cabecera `x-goog-api-key`, nunca en la URL (las URL terminan en registros y
  excepciones) ni en logs.
- Los errores solo llevan un código corto (`ai_http_429`, `ai_timeout`...): nunca el cuerpo de
  error de Google, el prompt ni la respuesta.
- Solo `live` con `GEMINI_BILLING_CONFIRMED=true` (lo exige production.py): el nivel gratuito
  permite que Google use y revise los datos.

El campo del esquema de salida es configurable (`GEMINI_SCHEMA_FIELD`) porque Google lo ha ido
cambiando de nombre: `responseJsonSchema` (por defecto) o `none` (el formato solo va en la
instrucción). Antes de usar datos reales: `python manage.py check_gemini`.
"""
import json
import socket
import urllib.error
import urllib.request

from .base import (
    FINISH_NORMAL, AIBlockedError, AIClient, AIInvalidOutput, AIPermanentError, AIResult,
    AITransientError, AIUsage,
)

API_ROOT = 'https://generativelanguage.googleapis.com/v1beta'
SCHEMA_FIELDS = ('responseJsonSchema', 'none')
BLOCKED_FINISH_REASONS = {'SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'IMAGE_SAFETY'}
MAX_OUTPUT_TOKENS = 8192
TEMPERATURE = 0.4


def _int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


class GeminiClient(AIClient):
    is_simulated = False

    def __init__(self, api_key, model, timeout=60, schema_field='responseJsonSchema', opener=None):
        if not api_key or not model:
            raise AIPermanentError('ai_not_configured')
        if schema_field not in SCHEMA_FIELDS:
            raise AIPermanentError('ai_bad_schema_field')
        self._api_key = api_key
        self.model_name = model
        self.timeout = timeout
        self.schema_field = schema_field
        self._open = opener or urllib.request.urlopen

    def __repr__(self):   # nunca mostrar la clave
        return f'GeminiClient(model={self.model_name!r})'

    def build_body(self, system, payload, schema):
        generation = {
            'responseMimeType': 'application/json',
            'temperature': TEMPERATURE,
            'maxOutputTokens': MAX_OUTPUT_TOKENS,
        }
        if self.schema_field != 'none' and schema is not None:
            generation[self.schema_field] = schema
        return {
            'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'role': 'user', 'parts': [{'text': json.dumps(payload, ensure_ascii=False)}]}],
            'generationConfig': generation,
        }

    def generate_json(self, *, task, system, payload, schema, job_ref):
        url = f'{API_ROOT}/models/{self.model_name}:generateContent'
        request = urllib.request.Request(
            url, method='POST',
            data=json.dumps(self.build_body(system, payload, schema)).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'x-goog-api-key': self._api_key},
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            retry_after = None
            try:
                retry_after = int(exc.headers.get('Retry-After')) if exc.headers else None
            except (TypeError, ValueError):
                retry_after = None
            exc.close()   # se descarta el cuerpo: puede repetir datos de la petición
            if status == 429 or status >= 500:
                raise AITransientError(f'ai_http_{status}', retry_after=retry_after) from None
            raise AIPermanentError(f'ai_http_{status}') from None
        except (socket.timeout, TimeoutError):
            raise AITransientError('ai_timeout') from None
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, 'reason', None), (socket.timeout, TimeoutError)):
                raise AITransientError('ai_timeout') from None
            raise AITransientError('ai_network') from None
        except OSError:
            raise AITransientError('ai_network') from None
        return self.parse_response(raw)

    def parse_response(self, raw):
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise AIPermanentError('ai_unreadable_response') from None
        if not isinstance(body, dict):
            raise AIPermanentError('ai_unreadable_response')
        meta = body.get('usageMetadata') or {}
        usage = AIUsage(
            tokens_in=_int(meta.get('promptTokenCount')),
            tokens_out=_int(meta.get('candidatesTokenCount')) + _int(meta.get('thoughtsTokenCount')),
        )
        if (body.get('promptFeedback') or {}).get('blockReason'):
            raise AIBlockedError('ai_blocked_prompt')
        candidates = body.get('candidates') or []
        if not candidates or not isinstance(candidates[0], dict):
            raise AIPermanentError('ai_no_candidates')
        candidate = candidates[0]
        finish = str(candidate.get('finishReason') or FINISH_NORMAL)
        if finish in BLOCKED_FINISH_REASONS:
            raise AIBlockedError('ai_blocked_safety')
        parts = ((candidate.get('content') or {}).get('parts')) or []
        # Las partes de "pensamiento" (thought=true) no son la respuesta
        text = ''.join(part.get('text', '') for part in parts
                       if isinstance(part, dict) and not part.get('thought'))
        if finish != FINISH_NORMAL:
            # MAX_TOKENS u otro: no se intenta leer un JSON cortado
            return AIResult(data=None, usage=usage, model=body.get('modelVersion') or self.model_name,
                            finish_reason=finish)
        try:
            data = json.loads(text)
        except ValueError:
            raise AIInvalidOutput('ai_invalid_json') from None
        return AIResult(data=data, usage=usage, model=body.get('modelVersion') or self.model_name,
                        finish_reason=finish)
