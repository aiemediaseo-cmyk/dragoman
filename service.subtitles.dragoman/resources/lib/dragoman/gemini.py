# -*- coding: utf-8 -*-
"""Minimal Gemini client (generateContent, v1beta) on http.client. No Kodi imports.

The request adapts per model: it first asks for the lowest thinking level the
model supports plus a JSON schema. Only if Google answers 400 and the message
points at one of those settings does it retry without thinking settings, then
without the schema; the variant that works is remembered for the session.
Any other 400 is returned to the caller straight away (no retries).

A request in flight can be aborted from another thread (abort()), so stopping
playback or quitting Kodi never waits for a slow answer.
"""
import http.client
import json
import re
import socket
import ssl
import time
from urllib.parse import urlsplit

API_BASE = 'https://generativelanguage.googleapis.com/v1beta/'
TIMEOUT = 75

# Lowest thinking level each model accepts (Gemini docs, Sept 2026).
# Unknown models start at 'minimal' and fall back automatically.
THINKING_FLOOR = {
    'gemini-3.8-flash': 'low',
    'gemini-3.7-flash': 'low',
    'gemini-3.6-flash': 'minimal',
    'gemini-3.5-flash': 'minimal',
    'gemini-3.5-flash-lite': 'minimal',
    'gemini-3.1-flash-lite': 'minimal',
}

_CONFIG_ERROR_RE = re.compile(
    r'(?i)thinking|schema|response_?mime|generation_?config|unknown name|invalid json payload|'
    r'json mode|not supported|unsupported')

_LADDER_CACHE = {}   # (model, thinking asked for) -> index of the request variant that works


class GeminiError(Exception):
    """kind: auth | quota_day | rate | server | network | timeout | blocked |
    too_long | bad_request | model | region | empty | cancelled"""

    def __init__(self, kind, message='', retry_after=None, status=None):
        Exception.__init__(self, '%s: %s' % (kind, message))
        self.kind = kind
        self.message = message
        self.retry_after = retry_after
        self.status = status


def _duration(value):
    m = re.match(r'^\s*([\d.]+)s\s*$', value or '')
    return float(m.group(1)) if m else None


def parse_error(code, body):
    """GeminiError from an HTTP error status and body."""
    try:
        err = json.loads(body).get('error') or {}
    except (ValueError, AttributeError):
        err = {}
    status = err.get('status', '')
    message = err.get('message', '') or ('HTTP %s' % code)
    retry_after = None
    per_day = False
    reason = ''
    for detail in err.get('details') or []:
        dtype = detail.get('@type', '')
        if dtype.endswith('RetryInfo'):
            retry_after = _duration(detail.get('retryDelay'))
        elif dtype.endswith('QuotaFailure'):
            for violation in detail.get('violations') or []:
                ident = (violation.get('quotaId', '') + ' ' + violation.get('quotaMetric', '')).lower()
                if 'perday' in ident or 'per_day' in ident or 'daily' in ident:
                    per_day = True
        elif dtype.endswith('ErrorInfo'):
            reason = detail.get('reason', '')
    low = message.lower()
    if code == 429:
        kind = 'quota_day' if per_day else 'rate'
    elif reason in ('API_KEY_INVALID', 'API_KEY_SERVICE_BLOCKED') or 'api key' in low or code == 401:
        kind = 'auth'
    elif 'location is not supported' in low or 'not available in your country' in low:
        kind = 'region'
    elif code == 403:
        kind = 'auth'
    elif code == 404:
        kind = 'model'
    elif code >= 500:
        kind = 'server'
    else:
        kind = 'bad_request'
    return GeminiError(kind, message, retry_after, status)


def https_transport(client, method, url, body, headers):
    """(status, body bytes). Registers the connection on the client so abort() can cut it."""
    parts = urlsplit(url)
    conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=client.timeout,
                                       context=ssl.create_default_context())
    client._conn = conn
    try:
        path = parts.path + ('?' + parts.query if parts.query else '')
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        client._conn = None
        conn.close()


class GeminiClient(object):
    def __init__(self, api_key, model, timeout=TIMEOUT, user_agent='Kodi-Dragoman', transport=None, log=None):
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = timeout
        self.user_agent = user_agent
        self._transport = transport or https_transport
        self._log = log
        self._conn = None
        self._aborted = False
        self.last_usage = {}

    def abort(self):
        """Cut a request in flight (safe to call from another thread)."""
        self._aborted = True
        conn = self._conn
        sock = getattr(conn, 'sock', None) if conn is not None else None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # ------------------------------------------------------------------ HTTP
    def _request(self, url, payload=None):
        if self._aborted:
            raise GeminiError('cancelled', 'aborted')
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        headers = {'x-goog-api-key': self.api_key, 'User-Agent': self.user_agent}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        try:
            status, raw = self._transport(self, 'POST' if data is not None else 'GET', url, data, headers)
        except GeminiError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            if self._aborted:
                raise GeminiError('cancelled', 'aborted')
            raise GeminiError('timeout', '%s' % exc)
        except (http.client.HTTPException, OSError) as exc:
            if self._aborted:
                raise GeminiError('cancelled', 'aborted')
            raise GeminiError('network', '%s: %s' % (type(exc).__name__, exc))
        if status >= 400:
            raise parse_error(status, raw.decode('utf-8', 'replace'))
        try:
            return json.loads(raw.decode('utf-8'))
        except ValueError as exc:
            raise GeminiError('empty', 'invalid JSON from server: %s' % exc)

    # ------------------------------------------------------------------ API
    def list_models(self):
        """Model ids (without 'models/') that support generateContent."""
        result = []
        data = self._request(API_BASE + 'models?pageSize=1000')
        for model in data.get('models') or []:
            if 'generateContent' in (model.get('supportedGenerationMethods') or []):
                result.append(model.get('name', '').replace('models/', ''))
        return result

    def _ladder(self, thinking=None):
        floor = THINKING_FLOOR.get(self.model, 'minimal')
        steps = [(thinking, True)] if thinking and thinking != floor else []
        steps.append((floor, True))
        if floor == 'minimal' and thinking != 'low':
            steps.append(('low', True))
        steps += [(None, True), (None, False)]
        return steps

    def generate_json(self, system_text, user_text, schema, max_output_tokens=16384, thinking=None,
                      timeout=None):
        """Return the model's JSON text. Raises GeminiError.
        thinking: ask for this thinking level first (the model's lowest one otherwise);
        timeout: seconds for this request instead of the client's default."""
        steps = self._ladder(thinking)
        cache_key = (self.model, thinking or '')
        start = min(_LADDER_CACHE.get(cache_key, 0), len(steps) - 1)
        for index in range(start, len(steps)):
            thinking, use_schema = steps[index]
            config = {'responseMimeType': 'application/json', 'maxOutputTokens': max_output_tokens}
            if use_schema:
                config['responseSchema'] = schema
            if thinking:
                config['thinkingConfig'] = {'thinkingLevel': thinking}
            if not self.model.startswith('gemini-3'):
                config['temperature'] = 0.2   # Gemini 3 wants its default temperature
            payload = {
                'systemInstruction': {'parts': [{'text': system_text}]},
                'contents': [{'role': 'user', 'parts': [{'text': user_text}]}],
                'generationConfig': config,
            }
            started = time.time()
            saved_timeout = self.timeout
            if timeout:
                self.timeout = timeout
            try:
                data = self._request(API_BASE + 'models/%s:generateContent' % self.model, payload)
            except GeminiError as exc:
                if (exc.kind == 'bad_request' and index + 1 < len(steps)
                        and _CONFIG_ERROR_RE.search(exc.message or '')):
                    if self._log:
                        self._log('model %s rejected request variant %d (%s); trying a simpler one'
                                  % (self.model, index, exc.message[:160]))
                    continue
                raise
            finally:
                self.timeout = saved_timeout
            _LADDER_CACHE[cache_key] = index
            self.last_usage = data.get('usageMetadata') or {}
            self.last_usage['_secs'] = round(time.time() - started, 1)
            return self._extract(data)
        raise GeminiError('bad_request', 'no request variant accepted')

    @staticmethod
    def _extract(data):
        feedback = data.get('promptFeedback') or {}
        if feedback.get('blockReason'):
            raise GeminiError('blocked', 'prompt blocked: %s' % feedback['blockReason'])
        candidates = data.get('candidates') or []
        if not candidates:
            raise GeminiError('empty', 'no candidates')
        candidate = candidates[0]
        finish = candidate.get('finishReason', '') or ''
        parts = (candidate.get('content') or {}).get('parts') or []
        text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
        if finish == 'MAX_TOKENS':
            raise GeminiError('too_long', 'answer cut off (MAX_TOKENS)')
        if finish not in ('STOP', '', 'FINISH_REASON_UNSPECIFIED'):
            raise GeminiError('blocked', 'finishReason %s' % finish)
        if not text.strip():
            raise GeminiError('empty', 'empty answer')
        return text


def reset_session_cache():
    _LADDER_CACHE.clear()
