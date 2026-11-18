"""FoodData Central: servidor fijo, clave en cabecera y errores sin URL/cuerpo."""
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = 'https://api.nal.usda.gov/fdc/v1'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class FDCError(Exception):
    """Solo códigos públicos; nunca mensajes del servidor ni claves."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class FDCClient:
    def __init__(self, api_key, timeout=15, opener=None):
        if not api_key or not str(api_key).strip():
            raise FDCError('fdc_key_missing')
        self._api_key = api_key
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise FDCError('fdc_invalid_timeout')
        self.timeout = timeout
        self._open = opener or urllib.request.build_opener(_NoRedirect()).open

    def __repr__(self):
        return 'FDCClient(key=<redacted>)'

    def _get(self, path, query=None):
        url = API_ROOT + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={
            'Accept': 'application/json', 'X-Api-Key': self._api_key,
        })
        try:
            with self._open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise FDCError(f'fdc_http_{status}') from None
        except (socket.timeout, TimeoutError, urllib.error.URLError, OSError):
            raise FDCError('fdc_connection_error') from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise FDCError('fdc_response_too_large')
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise FDCError('fdc_invalid_json') from None
        if not isinstance(result, dict):
            raise FDCError('fdc_invalid_response')
        return result

    def search(self, query):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 120:
            raise FDCError('fdc_invalid_search')
        result = self._get('/foods/search', {'query': query.strip(), 'pageSize': 10})
        foods = result.get('foods')
        if not isinstance(foods, list):
            raise FDCError('fdc_invalid_response')
        return [{
            'fdc_id': item.get('fdcId'), 'description': str(item.get('description', ''))[:300],
            'data_type': str(item.get('dataType', ''))[:60],
            'brand': str(item.get('brandOwner') or item.get('brandName') or '')[:160],
        } for item in foods if isinstance(item, dict)]

    def food(self, fdc_id):
        if type(fdc_id) is not int or not 0 < fdc_id <= 9999999999:
            raise FDCError('fdc_invalid_id')
        return self._get(f'/food/{fdc_id}')

