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

PROMPT_VERSION = 'p2'   # p2: characters + speaker + context after (better he/she forms)
MIN_SPLIT = 5          # stop splitting failing chunks below this many cues
CONTEXT_CUES = 6       # already-translated cues sent as read-only context
CONTEXT_AFTER = 6      # following cues (original text) sent as read-only context
MAX_CHARACTERS = 30
DIALOGUE_LIMIT = 60000  # characters of dialogue sent to work out who the characters are
MAX_RATE_WAITS = 8
MAX_NET_RETRIES = 3

# "speaker" comes before "text": the model names who is talking before it writes the
# translation, so it settles the speaker's gender instead of guessing it.
SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'cues': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {'id': {'type': 'INTEGER'}, 'speaker': {'type': 'STRING'},
                               'text': {'type': 'STRING'}},
                'required': ['id', 'speaker', 'text'],
                'propertyOrdering': ['id', 'speaker', 'text'],
            },
        },
    },
    'required': ['cues'],
}

CHARACTER_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'characters': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {'name': {'type': 'STRING'}, 'gender': {'type': 'STRING'},
                               'note': {'type': 'STRING'}},
                'required': ['name', 'gender'],
                'propertyOrdering': ['name', 'gender', 'note'],
            },
        },
    },
    'required': ['characters'],
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
        '{"cues": [{"id", "speaker", "text"}]} containing every id exactly once, in the same order.',
        'Translate every cue. Never merge, split, drop, reorder or add cues.',
        'Keep the line breaks (\\n) inside a cue: use the same number of lines when it reads naturally.',
        'Keep formatting tags such as <i>, </i>, <b>, <u>, <font ...> and {\\an8} around the matching words.',
        'Keep a leading "-" dialogue dash on each line that has one.',
        'Keep names of people, places and brands. Keep music symbols.',
        'Translate words inside sound tags like [door slams] or (laughs), keep the brackets.',
        'Write natural, spoken %s as in professionally subtitled films: short, clear and idiomatic. '
        'Match the tone of the original, including informal address, slang and swearing.' % name,
        'If a cue is already in %s or is only a name or number, copy it unchanged.' % name,
        'Who is speaking: subtitles rarely say it. Before writing each cue, decide who says it and to whom, '
        'and put the speaker\'s name in "speaker" ("?" if you cannot tell). Work it out from "characters", '
        'from names, nicknames and titles used in nearby lines (sir, ma\'am, Mr., Mrs., Miss, "boss", '
        'vocatives), from he/she/him/her, from speaker labels like "JANE:" and from what is being said.',
        'Gender: where %s marks gender (verb forms, adjectives, participles, pronouns), use the speaker\'s '
        'gender for "I"/"we" and the gender of the person spoken to for "you", and give every character '
        'the same gender throughout. Never default to one gender; if it really cannot be known, choose a '
        'wording that avoids it.' % name,
        'Choose the informal or formal "you" that fits the two people (family, friends, colleagues, '
        'strangers, superiors) and keep it the same between them.',
        '"characters" lists the people in this film or episode with their gender; follow it.',
        '"context_before" holds the cues just before this chunk, already translated, and "context_after" '
        'the cues just after it in the original language. Use both only for continuity and to tell who is '
        'speaking; never translate or return them.',
        '"title", "about" and "cast" describe what is being watched; use them as background.',
    ]
    note = _TARGET_NOTES.get(target)
    if note:
        lines.append(note)
    return '\n'.join(lines)


def character_prompt(target):
    name = languages.english_name(target)
    return '\n'.join([
        'You prepare a subtitle translation into %s. You receive JSON with "dialogue" (the subtitle '
        'lines of one film or episode, in order) and possibly "title", "about" and "cast" '
        '(actor and character names).' % name,
        'List the characters who speak or are spoken to, most important first, at most %d. '
        'For each give "name" as it is written in the subtitles (or the cast list), "gender" '
        '("male", "female" or "unknown") and a short "note": who they are, their relationships, and '
        'nicknames or titles others use for them (for example: \'his boss; called "Boss" and "Lisbon"\').'
        % MAX_CHARACTERS,
        'Decide gender from the cast list, your knowledge of the title, and pronouns, names and forms '
        'of address in the dialogue. Use "unknown" only when nothing points either way.',
        'Answer with JSON {"characters": [{"name", "gender", "note"}]}.',
    ])


def _character_line(item):
    name = ('%s' % item.get('name', '')).strip()
    gender = ('%s' % item.get('gender', '')).strip().lower()
    note = ('%s' % item.get('note', '')).strip()
    if not name:
        return ''
    if gender.startswith('f'):
        gender = 'female'
    elif gender.startswith('m'):
        gender = 'male'
    else:
        gender = 'gender unknown'
    return '%s: %s%s' % (name[:60], gender, ('. ' + note[:160]) if note else '')


def parse_characters(answer):
    """Character lines from the model's answer ([] when it is unusable)."""
    text = (answer or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-zA-Z]*\s*|\s*```$', '', text)
    try:
        data = json.loads(text)
    except ValueError:
        return []
    items = data.get('characters') if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    lines = []
    for item in items[:MAX_CHARACTERS]:
        if isinstance(item, dict):
            line = _character_line(item)
            if line and line not in lines:
                lines.append(line)
    return lines


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
                 on_model_switch=None, is_cancelled=None, check_unchanged=True, cast=None,
                 characters=None):
        self.make_client = make_client
        self.models = list(models)
        self.model_index = 0
        self.client = make_client(self.models[0])
        self.target = target
        self.title = title or ''
        self.plot = (plot or '')[:500]
        self.cast = [('%s' % c)[:120] for c in (cast or [])][:25]
        self.characters = list(characters or [])
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
    def find_characters(self, texts):
        """One request over the whole dialogue: who are the characters and what gender are they.
        Sets and returns self.characters. Never fatal: on any problem the translation simply
        goes on without the list (auth/quota problems then surface in the translation itself)."""
        lines, size = [], 0
        for t in texts:
            t = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>|\{[^}]*\}', '', t or '')).strip()
            if not t:
                continue
            size += len(t) + 1
            if size > DIALOGUE_LIMIT:
                break
            lines.append(t)
        if len(lines) < 5:
            return self.characters
        payload = {'dialogue': '\n'.join(lines)}
        if self.title:
            payload['title'] = self.title
        if self.plot:
            payload['about'] = self.plot
        if self.cast:
            payload['cast'] = self.cast
        user = json.dumps(payload, ensure_ascii=False)
        answer = None
        for _attempt in range(len(self.models) + 1):
            if self._cancelled():
                raise Cancelled()
            self.pacer.before_request()
            try:
                answer = self.client.generate_json(character_prompt(self.target), user, CHARACTER_SCHEMA)
                self.requests += 1
                break
            except GeminiError as exc:
                if exc.kind == 'cancelled':
                    raise Cancelled()
                # same model switch as the translation itself would make
                if exc.kind == 'quota_day' and self._next_model('daily free quota used up for %s' % self.model):
                    continue
                if exc.kind == 'model' and self._next_model('model %s not available' % self.model):
                    continue
                if exc.kind == 'rate':
                    self.pacer.penalize(exc.retry_after or 15)
                self._log('characters: skipped (%s: %s)' % (exc.kind, (exc.message or '')[:120]))
                return self.characters
        if answer is None:
            return self.characters
        self.characters = parse_characters(answer)
        self._log('characters: %s' % ('; '.join(c.split('.')[0] for c in self.characters) or 'none found'))
        return self.characters

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
        after = [texts[j] for j in range(ids[-1] + 1, min(len(texts), ids[-1] + 1 + CONTEXT_AFTER))
                 if texts[j].strip()]
        payload = {'cues': items}
        if self.characters:
            payload['characters'] = self.characters
        if context:
            payload['context_before'] = context
        if after:
            payload['context_after'] = after
        if self.title:
            payload['title'] = self.title
        if self.plot:
            payload['about'] = self.plot
        if self.cast:
            payload['cast'] = self.cast
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
