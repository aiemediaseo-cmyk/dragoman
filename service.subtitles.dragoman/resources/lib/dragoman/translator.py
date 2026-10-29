# -*- coding: utf-8 -*-
"""Chunked subtitle translation with validation, retry, split and pacing.

No Kodi imports: the client, clock, waiting, logging and progress reporting
are passed in, so the whole engine runs under plain Python in tests.

Only cue TEXT is sent. Timestamps stay on the device.
"""
import difflib
import json
import re
import time

from . import gender, languages, srt
from .gemini import GeminiError

# p2: characters + speaker + context after (better he/she forms)
# p3: separate speaker pass (who says it, to whom), tagged cues, on-device gender check
PROMPT_VERSION = 'p3'
MIN_SPLIT = 5          # stop splitting failing chunks below this many cues
CONTEXT_CUES = 6       # already-translated cues sent as read-only context
CONTEXT_AFTER = 6      # following cues (original text) sent as read-only context
MAX_CHARACTERS = 30
DIALOGUE_LIMIT = 60000  # characters of dialogue sent to work out who the characters are
MAX_RATE_WAITS = 8
MAX_NET_RETRIES = 3
SPEAKER_BLOCK = 300     # cues per speaker request: whole scenes, few requests
SPEAKER_CONTEXT = 12    # cues before/after a speaker block sent as read-only context
SPEAKER_MIN_SPLIT = 40  # an answer cut off for length is split down to blocks this small
SPEAKER_THINKING = 'medium'   # telling who speaks is the hard part: let the model think
SPEAKER_TIMEOUT = 150
REPAIR_MAX = 40         # cues per gender-fix request (at most one request per chunk)
REPAIR_MIN_SIMILAR = 0.7  # a fix may only touch a few words of the translation

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

# Cues that carry "by"/"to" tags from the speaker pass: only the translation comes back.
SCHEMA_TAGGED = {
    'type': 'OBJECT',
    'properties': {
        'cues': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {'id': {'type': 'INTEGER'}, 'text': {'type': 'STRING'}},
                'required': ['id', 'text'],
                'propertyOrdering': ['id', 'text'],
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
                               'aliases': {'type': 'ARRAY', 'items': {'type': 'STRING'}},
                               'note': {'type': 'STRING'}},
                'required': ['name', 'gender'],
                'propertyOrdering': ['name', 'gender', 'aliases', 'note'],
            },
        },
    },
    'required': ['characters'],
}

SPEAKER_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'cues': {
            'type': 'ARRAY',
            'items': {
                'type': 'OBJECT',
                'properties': {'id': {'type': 'INTEGER'}, 'speaker': {'type': 'STRING'},
                               'to': {'type': 'STRING'}},
                'required': ['id', 'speaker', 'to'],
                'propertyOrdering': ['id', 'speaker', 'to'],
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


def system_prompt(target, tagged=False):
    name = languages.english_name(target)
    native = languages.native_name(target)
    lines = [
        'You translate film and TV subtitles into %s (%s).' % (name, native),
        'You receive JSON with "cues": a list of {"id", "text"}. Answer with JSON '
        '{"cues": [{"id", %s"text"}]} containing every id exactly once, in the same order.'
        % ('' if tagged else '"speaker", '),
        'Translate every cue. Never merge, split, drop, reorder or add cues.',
        'Keep the line breaks (\\n) inside a cue: use the same number of lines when it reads naturally.',
        'Keep formatting tags such as <i>, </i>, <b>, <u>, <font ...> and {\\an8} around the matching words.',
        'Keep a leading "-" dialogue dash on each line that has one.',
        'Keep names of people, places and brands. Keep music symbols.',
        'Translate words inside sound tags like [door slams] or (laughs), keep the brackets.',
        'Write natural, spoken %s as in professionally subtitled films: short, clear and idiomatic. '
        'Match the tone of the original, including informal address, slang and swearing.' % name,
        'If a cue is already in %s or is only a name or number, copy it unchanged.' % name,
    ]
    if tagged:
        lines += [
            'Who is speaking: a cue may carry "by" (who says it) and "to" (who it is said to), each with '
            'their gender. A cue with dialogue dashes lists them per dash line: "1) ... 2) ...". They were '
            'worked out beforehand from the whole scene: follow them, unless the line itself clearly shows '
            'otherwise (for example "my husband" or "I\'m pregnant" said by someone tagged male).',
            'Gender: where %s marks gender (verb forms, adjectives, participles, pronouns), every form for '
            '"I"/"me" takes the gender in "by" and every form for "you" takes the gender in "to"; "several '
            'people" means plural "you". Check each such word before you write it. If a cue has no "by"/"to" '
            'or the gender is not known, work it out from "characters" and the nearby lines; never default to '
            'one gender, and if it really cannot be known choose a wording that avoids it.' % name,
        ]
    else:
        lines += [
            'Who is speaking: subtitles rarely say it. Before writing each cue, decide who says it and to whom, '
            'and put the speaker\'s name in "speaker" ("?" if you cannot tell). Work it out from "characters", '
            'from names, nicknames and titles used in nearby lines (sir, ma\'am, Mr., Mrs., Miss, "boss", '
            'vocatives), from he/she/him/her, from speaker labels like "JANE:" and from what is being said.',
            'Gender: where %s marks gender (verb forms, adjectives, participles, pronouns), use the speaker\'s '
            'gender for "I"/"we" and the gender of the person spoken to for "you", and give every character '
            'the same gender throughout. Never default to one gender; if it really cannot be known, choose a '
            'wording that avoids it.' % name,
        ]
    lines += [
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
        'For each give "name" (full name as the subtitles or the cast list give it), "gender" '
        '("male", "female" or "unknown"), "aliases" (every other way the dialogue refers to them: '
        'first name, surname, nickname, title such as "Boss", "Doc" or "Mr. Jane") and a short "note": '
        'who they are and their relationships.' % MAX_CHARACTERS,
        'Decide gender from the cast list (the actor\'s gender), your knowledge of the title, and '
        'pronouns, names and forms of address in the dialogue. Use "unknown" only when nothing points '
        'either way.',
        'Answer with JSON {"characters": [{"name", "gender", "aliases", "note"}]}.',
    ])


def speaker_prompt(target):
    return '\n'.join([
        'You prepare a subtitle translation into %s, where verbs and adjectives change with the gender '
        'of the speaker and of the person spoken to. Your job is only to work out who says each line and '
        'to whom; you do not translate.' % languages.english_name(target),
        'You receive JSON with "cues" ({"id", "text"}, in order), "characters" (people in this film or '
        'episode with their gender and the names others use for them), "context_before" and '
        '"context_after" (the lines around these cues, for continuity only) and possibly "title", "about" '
        'and "cast".',
        'For every cue give "speaker" (who says it) and "to" (who it is said to):',
        '- a person from "characters": their name exactly as written there;',
        '- anyone else: a short description with their gender, like "waitress (f)" or "guard (m)";',
        '- "to" is "group" when several people are addressed and "-" when nobody in particular is (a '
        'narrator, a phone message, a song, a sign); "?" only when you really cannot tell;',
        '- a cue with lines starting with "-" has one speaker per dash line: give them in order separated '
        'by " | " ("Lisbon | Jane"), and "to" the same way ("Jane | Lisbon").',
        'How to tell: follow the turns - a conversation mostly alternates between two people; a name or '
        'title said to someone ("Thanks, Lisbon", "Yes, sir") shows who is spoken to, and the next reply '
        'usually comes from that person; labels such as "LISBON:" or "(Jane)" name the speaker; '
        'he/she/him/her and what is said ("my husband", "I\'m pregnant") show gender; a new place, topic '
        'or set of people starts a new scene. Think through each conversation before answering.',
        'Answer with JSON {"cues": [{"id", "speaker", "to"}]} with every id exactly once, in order.',
    ])


def repair_prompt(target):
    name = languages.english_name(target)
    return '\n'.join([
        'You fix male/female grammar in subtitle translations into %s.' % name,
        'Each cue has "original" (the source line), "translation", "by" (who says it, with gender), '
        '"to" (who it is said to, with gender) and "problem" (what an automatic checker found). In a cue '
        'with dialogue dashes, "1) ... 2) ..." in "by" and "to" go with the first and second dash line.',
        'Rewrite the translation so that every form for "I"/"me" matches the gender in "by" and every form '
        'for "you" matches the gender in "to". Change nothing else: same wording, line breaks, tags and '
        'dialogue dashes.',
        'The checker is simple and often wrong: first decide whether the flagged word really refers to the '
        'speaker ("I") or to the person spoken to ("you"). If it does not, or "by"/"to" look wrong for '
        'this line, return the translation unchanged.',
        'Answer with JSON {"cues": [{"id", "text"}]}.',
    ])


def _character(item):
    """A clean character dict {name, gender, aliases, note} or None."""
    if isinstance(item, str):   # older saved form: "Name: gender. note"
        name, _sep, rest = item.partition(':')
        word = rest.strip().split('.')[0].strip()
        item = {'name': name, 'gender': word, 'note': rest.strip()[len(word):].lstrip('. ')}
    if not isinstance(item, dict):
        return None
    name = ('%s' % (item.get('name') or '')).strip()[:60]
    if not name:
        return None
    raw = ('%s' % (item.get('gender') or '')).strip().lower()
    aliases = item.get('aliases') if isinstance(item.get('aliases'), list) else []
    aliases = [('%s' % a).strip()[:40] for a in aliases if ('%s' % a).strip()][:8]
    return {'name': name,
            'gender': gender.norm_gender(raw) if not raw.startswith('unk') else None,
            'aliases': [a for a in aliases if a.lower() != name.lower()],
            'note': ('%s' % (item.get('note') or '')).strip()[:160]}


def character_line(char):
    """How a character is shown in requests: 'Teresa Lisbon: female (also "Boss", "Lisbon"). his boss'."""
    also = ', '.join('"%s"' % a for a in char.get('aliases') or [])
    return '%s: %s%s%s' % (char['name'], char.get('gender') or 'gender unknown',
                           (' (also %s)' % also) if also else '',
                           ('. ' + char['note']) if char.get('note') else '')


def character_names(chars):
    return '; '.join('%s (%s)' % (c['name'], (c.get('gender') or '?')[:1]) for c in chars or [])


def coerce_characters(items):
    result = []
    for item in items or []:
        char = _character(item)
        if char and char['name'].lower() not in [c['name'].lower() for c in result]:
            result.append(char)
    return result[:MAX_CHARACTERS]


def parse_characters(answer):
    """Character dicts from the model's answer ([] when it is unusable)."""
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
    return coerce_characters([i for i in items[:MAX_CHARACTERS * 2] if isinstance(i, dict)])


def parse_voices(answer, ids):
    """{cue index: [[speaker, listener], ...]} from a speaker answer (one pair per dash line)."""
    text = (answer or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-zA-Z]*\s*|\s*```$', '', text)
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    cues = data.get('cues') if isinstance(data, dict) else data
    if not isinstance(cues, list):
        return {}
    result = {}
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        try:
            k = int(cue.get('id'))
        except (TypeError, ValueError):
            continue
        if not 1 <= k <= len(ids):
            continue
        speakers = [x.strip()[:60] for x in ('%s' % (cue.get('speaker') or '?')).split('|')][:4]
        listeners = [x.strip()[:60] for x in ('%s' % (cue.get('to') or '?')).split('|')][:4]
        while len(listeners) < len(speakers):
            listeners.append('?')
        result[ids[k - 1]] = [[sp or '?', li or '?'] for sp, li in zip(speakers, listeners)]
    return result


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


def _problem_text(problems):
    parts = []
    for person, expected, found, word in problems:
        parts.append('"%s" may be a %s form for %s, while %s is %s - check whether it refers to them' % (
            word, found, '"I"' if person == 1 else '"you"',
            'the speaker' if person == 1 else 'the person spoken to', expected))
    return '; '.join(parts)


def _norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', text or '')).strip().lower()


class Translator(object):
    def __init__(self, make_client, models, target, title='', plot='', chunk_size=100, rpm=8,
                 wait=None, clock=time.time, log=None, progress=None, fallback=True,
                 on_model_switch=None, is_cancelled=None, check_unchanged=True, cast=None,
                 characters=None, speakers=False, voices=None, speaker_blocks=None, source=''):
        self.make_client = make_client
        self.models = list(models)
        self.model_index = 0
        self.client = make_client(self.models[0])
        self.target = target
        self.title = title or ''
        self.plot = (plot or '')[:500]
        self.cast = [('%s' % c)[:120] for c in (cast or [])][:25]
        self.characters = coerce_characters(characters)
        # Speaker pass: who says each cue and to whom, worked out in blocks before the cues are
        # translated. voices: {cue index: [[speaker, listener], ...]} (one pair per dash line).
        # only worth its requests when "I"/"you" can be male or female in the target language
        self.speakers = bool(speakers) and gender.marks_gender(target)
        # the on-device check reads which persons a line speaks of from the English source
        self.check_forms = self.speakers and gender.supported(target) and (source or '').startswith('en')
        self.voices = {}
        for k, v in (voices or {}).items():
            try:
                self.voices[int(k)] = [[('%s' % a)[:60], ('%s' % b)[:60]] for a, b in v][:4]
            except (TypeError, ValueError):
                continue
        self.speaker_blocks = set()     # blocks answered (saved for a resumed job)
        self._speaker_failed = set()    # blocks that failed this time (asked again next time)
        self._repair_budget = 0
        self._repair_off = False
        self._chunk_repaired = False
        for start in speaker_blocks or []:
            try:
                self.speaker_blocks.add(int(start))
            except (TypeError, ValueError):
                continue
        self.chunk_size = max(5, int(chunk_size))
        self.fallback = fallback
        self._wait = wait or (lambda seconds: time.sleep(seconds) or False)
        self._cancelled = is_cancelled or (lambda: False)
        self.pacer = Pacer(rpm, self._wait, clock)
        self._log = log or (lambda message: None)
        self._progress = progress or (lambda done, total: None)
        self._on_model_switch = on_model_switch or (lambda model: None)
        self.system = system_prompt(target)
        self.system_tagged = system_prompt(target, tagged=True)
        self.check_unchanged = check_unchanged
        self.requests = 0
        self.splits = 0
        self.speaker_requests = 0
        self.gender_flagged = 0     # cues the on-device check found with wrong he/she forms
        self.gender_fixed = 0       # ... and that the fix request corrected
        self._texts = []

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
                answer = self.client.generate_json(character_prompt(self.target), user, CHARACTER_SCHEMA,
                                                   thinking=SPEAKER_THINKING if self.speakers else None,
                                                   timeout=SPEAKER_TIMEOUT if self.speakers else None)
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
        self._log('characters: %s' % (character_names(self.characters) or 'none found'))
        return self.characters

    def state(self):
        """What a resumed job needs again: characters and the speaker pass so far."""
        return {'characters': self.characters,
                'voices': dict((str(k), v) for k, v in self.voices.items()),
                'speaker_blocks': sorted(self.speaker_blocks)}

    def translate(self, texts, cached_chunks=None, on_chunk=None):
        """Return (translated_texts, failed_count). Failed cues keep the original text."""
        n = len(texts)
        out = [None] * n
        self._texts = texts
        # fix requests: at most one per chunk, and about one for every three chunks overall
        self._repair_budget = max(2, -(-n // self.chunk_size) // 3)
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
                if self.speakers:
                    self._ensure_voices(texts, a, b)
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
        self._chunk_repaired = False     # one fix request per chunk, also when the chunk is split
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
                result = self._check_gender(texts, ids, result)
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
            else:
                result = self._check_gender(texts, ids, result)
            for i in ids:
                out[i] = result[i]
            return

    # ------------------------------------------------------------------ speaker pass
    def _ensure_voices(self, texts, a, b):
        """Run the speaker pass for every block that cues a..b-1 fall in and that has not run yet."""
        start = (a // SPEAKER_BLOCK) * SPEAKER_BLOCK
        while start < b:
            if start not in self.speaker_blocks and start not in self._speaker_failed:
                if self._speaker_block(texts, start, min(start + SPEAKER_BLOCK, len(texts))):
                    self.speaker_blocks.add(start)
                else:
                    # not asked again in this job (the cues just carry no tags); a resumed job tries again
                    self._speaker_failed.add(start)
            start += SPEAKER_BLOCK

    def _speaker_block(self, texts, a, b, depth=0):
        """Ask who says cues a..b-1 and to whom. True when an answer came back."""
        ids = [i for i in range(a, b) if texts[i].strip()]
        if not ids:
            return True
        payload = {'cues': [{'id': k + 1, 'text': texts[i]} for k, i in enumerate(ids)]}
        if self.characters:
            payload['characters'] = [character_line(c) for c in self.characters]
        before = [texts[j] for j in range(max(0, a - SPEAKER_CONTEXT), a) if texts[j].strip()]
        after = [texts[j] for j in range(b, min(len(texts), b + SPEAKER_CONTEXT)) if texts[j].strip()]
        if before:
            payload['context_before'] = before
        if after:
            payload['context_after'] = after
        if self.title:
            payload['title'] = self.title
        if self.plot:
            payload['about'] = self.plot
        if self.cast:
            payload['cast'] = self.cast
        user = json.dumps(payload, ensure_ascii=False)
        tries = 0
        while True:
            if self._cancelled():
                raise Cancelled()
            self.pacer.before_request()
            try:
                answer = self.client.generate_json(speaker_prompt(self.target), user, SPEAKER_SCHEMA,
                                                   max_output_tokens=32768, thinking=SPEAKER_THINKING,
                                                   timeout=SPEAKER_TIMEOUT)
                self.requests += 1
                self.speaker_requests += 1
            except GeminiError as exc:
                kind = exc.kind
                if kind == 'cancelled':
                    raise Cancelled()
                if kind == 'quota_day' and self._next_model('daily free quota used up for %s' % self.model):
                    continue
                if kind == 'model' and self._next_model('model %s not available' % self.model):
                    continue
                tries += 1
                if kind == 'rate' and tries <= 4:
                    self.pacer.penalize(exc.retry_after or min(60, 15 * tries))
                    continue
                if kind in ('server', 'network', 'empty') and tries <= 2:
                    self.pacer.penalize(5 * tries)
                    continue
                # too long, or so slow it timed out: two halves, once (a timed-out request still
                # counts against the daily quota, so it is not simply repeated)
                if kind in ('too_long', 'timeout') and depth == 0 and b - a > SPEAKER_MIN_SPLIT:
                    mid = a + (b - a) // 2
                    first = self._speaker_block(texts, a, mid, depth + 1)
                    second = self._speaker_block(texts, mid, b, depth + 1)
                    return first or second   # half an answer is kept rather than asked for again
                # never fatal: these cues are translated without tags (as before the speaker pass)
                self._log('speakers %d-%d: skipped (%s: %s)' % (a + 1, b, kind, (exc.message or '')[:120]))
                return False
            found = parse_voices(answer, ids)
            if len(found) < 0.5 * len(ids):
                self._log('speakers %d-%d: answer covered only %d of %d lines, not used' % (
                    a + 1, b, len(found), len(ids)))
                return False
            self.voices.update(found)
            known = sum(1 for i in found if any(sp != '?' for sp, _li in found[i]))
            self._log('speakers %d-%d: %d of %d lines placed' % (a + 1, b, known, len(ids)))
            return True

    def _tags(self, i):
        """("by", "to") texts for cue i, e.g. ("Teresa Lisbon (female)", "Patrick Jane (male)")."""
        voices = self.voices.get(i) if self.speakers else None
        if not voices:
            return None, None
        if len(voices) != len(gender.segments(self._source(i))):
            return None, None   # e.g. one speaker given for a cue with two dash lines: unreliable
        bys = [gender.describe(sp, self.characters) for sp, _li in voices]
        tos = [gender.describe(li, self.characters) for _sp, li in voices]
        if len(voices) == 1:
            return bys[0], tos[0]

        def joined(items):
            if not any(items):
                return None
            return ' '.join('%d) %s' % (k + 1, d or '?') for k, d in enumerate(items))
        return joined(bys), joined(tos)

    def _source(self, i):
        return self._texts[i] if 0 <= i < len(self._texts) else ''

    def _genders(self, i):
        return [(gender.resolve(sp, self.characters)[1], gender.resolve(li, self.characters)[1])
                for sp, li in self.voices.get(i) or []]

    # ------------------------------------------------------------------ gender check
    def _check_gender(self, texts, ids, result):
        """Read the I/you forms of each translated cue on the device; send the ones that
        contradict the speaker pass back once to be fixed. Never fatal."""
        if not self.check_forms:
            return result
        flagged = []
        for i in ids:
            voices = self._genders(i)
            if not voices or len(voices) != len(gender.segments(texts[i])):
                continue
            # only the persons the English line itself speaks of ("I", "you")
            persons = [gender.persons_in(part) for part in gender.segments(texts[i])]
            problems = gender.check(self.target, result[i], voices, persons)
            if problems:
                flagged.append((i, problems, voices, persons))
        if not flagged:
            return result
        self.gender_flagged += len(flagged)
        self._log('gender check: %d line(s) to fix in cues %d-%d (%s)' % (
            len(flagged), ids[0] + 1, ids[-1] + 1,
            ', '.join('%d:%s' % (f[0] + 1, f[1][0][3]) for f in flagged[:8])))
        if self._repair_off or self._repair_budget <= 0 or self._chunk_repaired:
            self._log('gender check: no fix request left for this part')
            return result
        self._repair_budget -= 1
        self._chunk_repaired = True
        result = dict(result)
        self._repair(texts, result, flagged[:REPAIR_MAX])
        return result

    def _repair(self, texts, result, flagged):
        items = []
        for k, (i, problems, _voices, _persons) in enumerate(flagged):
            by, to = self._tags(i)
            items.append({'id': k + 1, 'original': texts[i], 'translation': result[i],
                          'by': by or '?', 'to': to or '?', 'problem': _problem_text(problems)})
        user = json.dumps({'cues': items}, ensure_ascii=False)
        if self._cancelled():
            raise Cancelled()
        self.pacer.before_request()
        try:
            answer = self.client.generate_json(repair_prompt(self.target), user, SCHEMA_TAGGED, thinking='low')
            self.requests += 1
        except GeminiError as exc:
            if exc.kind == 'cancelled':
                raise Cancelled()
            if exc.kind in ('rate', 'quota_day'):
                # the quota matters more for the translation itself: no more fix requests
                self._repair_off = True
                self.pacer.penalize(exc.retry_after or 15)
            self._log('gender fix skipped (%s: %s)' % (exc.kind, (exc.message or '')[:120]))
            return
        text = (answer or '').strip()
        if text.startswith('```'):
            text = re.sub(r'^```[a-zA-Z]*\s*|\s*```$', '', text)
        try:
            data = json.loads(text)
            cues = data.get('cues') if isinstance(data, dict) else data
        except ValueError:
            cues = None
        if not isinstance(cues, list):
            self._log('gender fix: answer unusable')
            return
        for cue in cues:
            if not isinstance(cue, dict) or not isinstance(cue.get('text'), str):
                continue
            try:
                k = int(cue.get('id'))
            except (TypeError, ValueError):
                continue
            if not 1 <= k <= len(flagged):
                continue
            i, problems, voices, persons = flagged[k - 1]
            new = srt.clean_text(cue['text'])
            old = result[i]
            if not new or new == old or len(gender.segments(new)) != len(gender.segments(old)):
                continue
            # a fix changes a few word endings: not a rewrite, not the source line, not English
            if _norm(new) == _norm(texts[i]) or difflib.SequenceMatcher(None, old, new).ratio() < REPAIR_MIN_SIMILAR:
                self._log('gender fix for cue %d not used (changed too much)' % (i + 1))
                continue
            left = gender.check(self.target, new, voices, persons)
            if len(left) < len(problems):
                result[i] = new
                if not left:
                    self.gender_fixed += 1

    def _next_model(self, why):
        if not self.fallback or self.model_index + 1 >= len(self.models):
            return False
        self.model_index += 1
        self.client = self.make_client(self.model)
        self._log('%s - switching to %s' % (why, self.model))
        self._on_model_switch(self.model)
        return True

    def _request(self, texts, out, ids):
        items = []
        for k, i in enumerate(ids):
            item = {'id': k + 1, 'text': texts[i]}
            by, to = self._tags(i)
            if by:
                item['by'] = by
            if to:
                item['to'] = to
            items.append(item)
        tagged = any('by' in item or 'to' in item for item in items)
        context = [out[j] for j in range(max(0, ids[0] - CONTEXT_CUES), ids[0]) if out[j]]
        after = [texts[j] for j in range(ids[-1] + 1, min(len(texts), ids[-1] + 1 + CONTEXT_AFTER))
                 if texts[j].strip()]
        payload = {'cues': items}
        if self.characters:
            payload['characters'] = [character_line(c) for c in self.characters]
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
                if tagged:
                    answer = self.client.generate_json(self.system_tagged, user, SCHEMA_TAGGED)
                else:
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
