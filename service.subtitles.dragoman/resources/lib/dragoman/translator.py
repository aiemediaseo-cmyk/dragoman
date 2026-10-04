# -*- coding: utf-8 -*-
"""Chunked subtitle translation with validation, retry, split and pacing.

No Kodi imports: the client, clock, waiting, logging and progress reporting
are passed in, so the whole engine runs under plain Python in tests.

Only cue TEXT is sent. Timestamps stay on the device.
"""
import json
import re
import time

from . import languages, srt
from .gemini import GeminiError

PROMPT_VERSION = 'p1'
MIN_SPLIT = 5          # stop splitting failing chunks below this many cues
CONTEXT_CUES = 5       # already-translated cues sent as read-only context
MAX_RATE_WAITS = 8
MAX_NET_RETRIES = 3

SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'cues': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {'id': {'type': 'INTEGER'}, 'text': {'type': 'STRING'}},
                'required': ['id', 'text'],
            },
        },
    },
    'required': ['cues'],
}

_TARGET_NOTES = {
    'sr': 'Write Serbian in Latin script (latinica), ekavian.',
    'sr-Cyrl': 'Write Serbian in Cyrillic script, ekavian.',
    'hr': 'Write standard Croatian (ijekavian, Croatian vocabulary).',
    'bs': 'Write standard Bosnian (ijekavian, Bosnian vocabulary).',
    'pt': 'Use European Portuguese.',
    'pt-BR': 'Use Brazilian Portuguese.',
    'zh': 'Use Simplified Chinese characters.',
    'zh-Hant': 'Use Traditional Chinese characters.',
    'no': 'Write Norwegian Bokmal.',
}


def system_prompt(target):
    name = languages.english_name(target)
    native = languages.native_name(target)
    lines = [
        'You translate film and TV subtitles into %s (%s).' % (name, native),
        'You receive JSON with "cues": a list of {"id", "text"}. Answer with JSON '
        '{"cues": [{"id", "text"}]} containing every id exactly once, in the same order.',
        'Translate every cue. Never merge, split, drop, reorder or add cues.',
        'Keep the line breaks (\\n) inside a cue: use the same number of lines when it reads naturally.',
        'Keep formatting tags such as <i>, </i>, <b>, <u>, <font ...> and {\\an8} around the matching words.',
        'Keep a leading "-" dialogue dash on each line that has one.',
        'Keep names of people, places and brands. Keep music symbols.',
        'Translate words inside sound tags like [door slams] or (laughs), keep the brackets.',
        'Write natural, spoken %s as in professionally subtitled films: short, clear and idiomatic. '
        'Match the tone of the original, including informal address, slang and swearing.' % name,
        'If a cue is already in %s or is only a name or number, copy it unchanged.' % name,
        '"context_before" holds the cues just before this chunk, already translated. '
        'Use it only for continuity; do not return it.',
        '"title" and "about" describe what is being watched; use them only as background.',
    ]
    note = _TARGET_NOTES.get(target)
    if note:
        lines.append(note)
    return '\n'.join(lines)


class Cancelled(Exception):
    pass


class StopJob(Exception):
    """The job cannot continue. kind: auth | quota | region | network | rate | model | bad_request.
    A 400 that is not about request settings stops the job at once: repeating
    or splitting would only burn the daily quota."""

    def __init__(self, kind, message=''):
        Exception.__init__(self, '%s: %s' % (kind, message))
        self.kind = kind
        self.message = message


class _Invalid(Exception):
    def __init__(self, message, retry=True):
        Exception.__init__(self, message)
        self.retry = retry


class _Unchanged(Exception):
    """The answer looks untranslated. Retried once, then accepted as it is
    (close languages such as Croatian/Serbian/Bosnian legitimately look alike)."""

    def __init__(self, result):
        Exception.__init__(self, 'answer is mostly the original text')
        self.result = result


class Pacer(object):
    """Keeps requests at most `rpm` per minute and honours server back-off."""

    def __init__(self, rpm, wait, clock):
        self.interval = 60.0 / max(1, rpm)
        self._wait = wait        # wait(seconds) -> True if cancelled
        self._clock = clock
        self._next = 0.0

    def before_request(self):
        delay = self._next - self._clock()
        if delay > 0 and self._wait(delay):
            raise Cancelled()
        self._next = self._clock() + self.interval

    def penalize(self, seconds):
        self._next = max(self._next, self._clock() + seconds)


def _norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', text or '')).strip().lower()


class Translator(object):
    def __init__(self, make_client, models, target, title='', plot='', chunk_size=100, rpm=8,
                 wait=None, clock=time.time, log=None, progress=None, fallback=True,
                 on_model_switch=None, is_cancelled=None, check_unchanged=True):
        self.make_client = make_client
        self.models = list(models)
        self.model_index = 0
        self.client = make_client(self.models[0])
        self.target = target
        self.title = title or ''
        self.plot = (plot or '')[:500]
        self.chunk_size = max(5, int(chunk_size))
        self.fallback = fallback
        self._wait = wait or (lambda seconds: time.sleep(seconds) or False)
        self._cancelled = is_cancelled or (lambda: False)
        self.pacer = Pacer(rpm, self._wait, clock)
        self._log = log or (lambda message: None)
        self._progress = progress or (lambda done, total: None)
        self._on_model_switch = on_model_switch or (lambda model: None)
        self.system = system_prompt(target)
        self.check_unchanged = check_unchanged
        self.requests = 0
        self.splits = 0

    @property
    def model(self):
        return self.models[self.model_index]

    # ------------------------------------------------------------------ public
    def translate(self, texts, cached_chunks=None, on_chunk=None):
        """Return (translated_texts, failed_count). Failed cues keep the original text."""
        n = len(texts)
        out = [None] * n
        cached_chunks = cached_chunks or {}
        ranges = [(a, min(a + self.chunk_size, n)) for a in range(0, n, self.chunk_size)]
        done = 0
        for index, (a, b) in enumerate(ranges):
            if self._cancelled():
                raise Cancelled()
            cached = cached_chunks.get(str(index))
            if isinstance(cached, list) and len(cached) == b - a:
                out[a:b] = cached
            else:
                self._range(texts, out, a, b)
                chunk = out[a:b]
                if on_chunk is not None and all(t is not None for t in chunk):
                    on_chunk(index, chunk)
            done += b - a
            self._progress(done, n)
        failed = sum(1 for t in out if t is None)
        return [out[i] if out[i] is not None else texts[i] for i in range(n)], failed

    # ------------------------------------------------------------------ internals
    def _range(self, texts, out, a, b):
        ids = []
        for i in range(a, b):
            if texts[i].strip():
                ids.append(i)
            else:
                out[i] = texts[i]
        if ids:
            self._ids(texts, out, ids)

    def _ids(self, texts, out, ids):
        tries = 0
        while True:
            try:
                result = self._request(texts, out, ids)
            except _Unchanged as exc:
                tries += 1
                if tries < 2:
                    self._log('cues %d-%d: %s, asking once more' % (ids[0] + 1, ids[-1] + 1, exc))
                    continue
                self._log('cues %d-%d: accepted as they are (very close to the original)' % (ids[0] + 1, ids[-1] + 1))
                result = exc.result
            except _Invalid as exc:
                tries += 1
                self._log('cues %d-%d (%d): %s' % (ids[0] + 1, ids[-1] + 1, len(ids), exc))
                if exc.retry and tries < 2:
                    continue
                if len(ids) > MIN_SPLIT:
                    self.splits += 1
                    mid = len(ids) // 2
                    self._ids(texts, out, ids[:mid])
                    self._ids(texts, out, ids[mid:])
                else:
                    self._log('giving up on cues %d-%d, keeping the original text' % (ids[0] + 1, ids[-1] + 1))
                return
            for i in ids:
                out[i] = result[i]
            return

    def _next_model(self, why):
        if not self.fallback or self.model_index + 1 >= len(self.models):
            return False
        self.model_index += 1
        self.client = self.make_client(self.model)
        self._log('%s - switching to %s' % (why, self.model))
        self._on_model_switch(self.model)
        return True

    def _request(self, texts, out, ids):
        items = [{'id': k + 1, 'text': texts[i]} for k, i in enumerate(ids)]
        context = [out[j] for j in range(max(0, ids[0] - CONTEXT_CUES), ids[0]) if out[j]]
        payload = {'cues': items}
        if context:
            payload['context_before'] = context
        if self.title:
            payload['title'] = self.title
        if self.plot:
            payload['about'] = self.plot
        user = json.dumps(payload, ensure_ascii=False)
        rate_waits = 0
        net_errors = 0
        while True:
            if self._cancelled():
                raise Cancelled()
            self.pacer.before_request()
            try:
                answer = self.client.generate_json(self.system, user, SCHEMA)
                self.requests += 1
                usage = getattr(self.client, 'last_usage', {}) or {}
                self._log('ok %s: cues %d-%d (%d) in %ss, tokens in/out %s/%s' % (
                    self.model, ids[0] + 1, ids[-1] + 1, len(ids), usage.get('_secs', '?'),
                    usage.get('promptTokenCount', '?'), usage.get('candidatesTokenCount', '?')))
            except GeminiError as exc:
                kind = exc.kind
                if kind == 'rate':
                    rate_waits += 1
                    if rate_waits > MAX_RATE_WAITS:
                        raise StopJob('rate', exc.message)
                    delay = exc.retry_after or min(60, 15 * rate_waits)
                    self._log('rate limited, waiting %.0fs' % delay)
                    self.pacer.penalize(delay)
                    continue
                if kind in ('server', 'timeout', 'network'):
                    net_errors += 1
                    if net_errors > MAX_NET_RETRIES:
                        raise StopJob('network', exc.message)
                    delay = 5 * (3 ** (net_errors - 1))
                    self._log('%s (%s), retrying in %ds' % (kind, exc.message[:120], delay))
                    self.pacer.penalize(delay)
                    continue
                if kind == 'quota_day':
                    if self._next_model('daily free quota used up for %s' % self.model):
                        continue
                    raise StopJob('quota', exc.message)
                if kind == 'model':
                    if self._next_model('model %s not available' % self.model):
                        continue
                    raise StopJob('model', exc.message)
                if kind in ('auth', 'region', 'bad_request'):
                    raise StopJob(kind, exc.message)
                if kind == 'cancelled':
                    raise Cancelled()
                if kind == 'empty':
                    raise _Invalid(exc.message, retry=True)
                raise _Invalid(exc.message, retry=False)   # blocked, too_long: split, don't repeat
            return self._validate(answer, items, ids, self.check_unchanged)

    @staticmethod
    def _validate(answer, items, ids, check_unchanged=False):
        text = answer.strip()
        if text.startswith('```'):
            text = re.sub(r'^```[a-zA-Z]*\s*|\s*```$', '', text)
        try:
            data = json.loads(text)
        except ValueError:
            raise _Invalid('answer is not valid JSON')
        cues = data.get('cues') if isinstance(data, dict) else data
        if not isinstance(cues, list):
            raise _Invalid('answer has no cue list')
        mapping = {}
        for cue in cues:
            if not isinstance(cue, dict):
                raise _Invalid('cue is not an object')
            try:
                cid = int(cue.get('id'))
            except (TypeError, ValueError):
                raise _Invalid('cue without a valid id')
            value = cue.get('text')
            if not isinstance(value, str):
                raise _Invalid('cue %d has no text' % cid)
            mapping[cid] = srt.clean_text(value)
        expected = set(range(1, len(items) + 1))
        if set(mapping) != expected or len(cues) != len(items):
            raise _Invalid('ids do not match (got %d cues for %d)' % (len(cues), len(items)))
        empty = [k for k in expected if not mapping[k]]
        if empty:
            raise _Invalid('%d empty translations' % len(empty))
        result = dict((ids[k - 1], mapping[k]) for k in expected)
        if check_unchanged and len(items) >= 8:
            same = sum(1 for k in expected if _norm(mapping[k]) == _norm(items[k - 1]['text']))
            if same > 0.7 * len(items):
                raise _Unchanged(result)
        return result
