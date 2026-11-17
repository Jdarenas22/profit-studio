"""Cliente REAL de la WhatsApp Business Cloud API (modo `live`), con `urllib` (sin dependencias).

- Token en la cabecera `Authorization: Bearer`, nunca en la URL ni en logs.
- Del cuerpo de error de Meta solo se lee el código numérico; el resto se descarta.
- `POST https://graph.facebook.com/{version}/{phone_number_id}/messages` (documentación consultada
  el 2026-11-16; los ejemplos usan v27.0). Reconfirmar la forma de `components` con parámetros con
  nombre (`parameter_name`) en la referencia vigente al poner en producción (`check_whatsapp`).
"""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from .base import SendResult, WhatsAppAPIError, WhatsAppClient

GRAPH_ROOT = 'https://graph.facebook.com'
MAX_RETRY_AFTER = 3600


def _meta_code(raw):
    try:
        body = json.loads(raw or b'{}')
        code = (body.get('error') or {}).get('code')
        return int(code) if code is not None and not isinstance(code, bool) else None
    except (ValueError, TypeError, AttributeError):
        return None


def _retry_after(headers):
    try:
        value = int(headers.get('Retry-After')) if headers else None
    except (TypeError, ValueError):
        return None
    return min(max(value, 0), MAX_RETRY_AFTER) if value is not None else None


class CloudAPIClient(WhatsAppClient):
    def __init__(self, *, token, phone_number_id, graph_version, timeout=10, opener=None):
        if not token or not phone_number_id or not graph_version:
            raise WhatsAppAPIError('not_configured', http_status=401)
        self._token = token
        self.phone_number_id = phone_number_id
        self.graph_version = graph_version
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def __repr__(self):   # nunca mostrar el token
        return f'CloudAPIClient(phone_number_id={self.phone_number_id!r}, version={self.graph_version!r})'

    def url(self, path):
        return f'{GRAPH_ROOT}/{self.graph_version}/{path.lstrip("/")}'

    def _request(self, method, path, body=None, query=None):
        url = self.url(path)
        if query:
            url = f'{url}?{urllib.parse.urlencode(query)}'
        data = json.dumps(body).encode('utf-8') if body is not None else None
        headers = {'Authorization': f'Bearer {self._token}'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, method=method, data=data, headers=headers)
        try:
            with self._open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                raw_error = exc.read()
            except Exception:  # noqa: BLE001
                raw_error = b''
            retry_after = _retry_after(exc.headers)
            exc.close()
            meta = _meta_code(raw_error)
            code = f'meta_{meta}' if meta is not None else f'http_{status}'
            raise WhatsAppAPIError(code, meta_code=meta, http_status=status, retry_after=retry_after) from None
        except (socket.timeout, TimeoutError):
            raise WhatsAppAPIError('timeout') from None
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, 'reason', None), (socket.timeout, TimeoutError)):
                raise WhatsAppAPIError('timeout') from None
            raise WhatsAppAPIError('network') from None
        except OSError:
            raise WhatsAppAPIError('network') from None
        try:
            return json.loads(raw or b'{}')
        except (ValueError, UnicodeDecodeError):
            raise WhatsAppAPIError('unreadable_response', http_status=502) from None

    # ── Envíos ──────────────────────────────────────────────────────────────

    @staticmethod
    def template_body(*, to, template_name, language, body_params, button_payloads, ref):
        components = []
        if body_params:
            components.append({'type': 'body', 'parameters': [
                {'type': 'text', 'parameter_name': name, 'text': value} for name, value in body_params]})
        for index, payload in button_payloads:
            components.append({'type': 'button', 'sub_type': 'quick_reply', 'index': str(index),
                               'parameters': [{'type': 'payload', 'payload': payload}]})
        template = {'name': template_name, 'language': {'code': language}}
        if components:
            template['components'] = components
        return {'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': to,
                'type': 'template', 'template': template, 'biz_opaque_callback_data': str(ref)}

    @staticmethod
    def text_body(*, to, text, ref):
        return {'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': to,
                'type': 'text', 'text': {'body': text, 'preview_url': False},
                'biz_opaque_callback_data': str(ref)}

    def _send(self, body):
        data = self._request('POST', f'{self.phone_number_id}/messages', body=body)
        try:
            wamid = str(data['messages'][0]['id'])
        except (KeyError, IndexError, TypeError):
            raise WhatsAppAPIError('unreadable_response', http_status=502) from None
        contacts = data.get('contacts') or [{}]
        wa_id = str((contacts[0] or {}).get('wa_id') or '') if isinstance(contacts, list) else ''
        return SendResult(wamid=wamid, wa_id=wa_id)

    def send_template(self, *, to, template_name, language, body_params, button_payloads, ref):
        return self._send(self.template_body(to=to, template_name=template_name, language=language,
                                             body_params=body_params, button_payloads=button_payloads, ref=ref))

    def send_text(self, *, to, text, ref):
        return self._send(self.text_body(to=to, text=text, ref=ref))

    # ── Consultas de solo lectura (check_whatsapp) y registro del número ────

    def phone_number_info(self):
        return self._request('GET', self.phone_number_id,
                             query={'fields': 'verified_name,quality_rating,name_status,status'})

    def templates(self, waba_id):
        data = self._request('GET', f'{waba_id}/message_templates',
                             query={'fields': 'name,status,language,category', 'limit': '100'})
        return data.get('data') or []

    def register_number(self, pin):
        return self._request('POST', f'{self.phone_number_id}/register',
                             body={'messaging_product': 'whatsapp', 'pin': pin})
