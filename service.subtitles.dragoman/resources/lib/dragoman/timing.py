# -*- coding: utf-8 -*-
"""Subtitle timing: frame-rate drift check, fixes from sync points, applying a fix.

No Kodi imports, so all of it runs under plain Python in tests.

A fix maps the subtitle's own times to the video's times:

    video_ms = k * subtitle_ms + b

k is 1 for a plain delay (b alone moves every line) and a frame-rate ratio such
as 25/23.976 when the subtitle was made for a version of the film that runs at
another speed (the lines drift further off as the film goes on).

Fixes are stored per video and per subtitle (the hash of the original file), so
a fix made once is applied again next time, also to translations of that
subtitle: a translation keeps the original's timestamps.
"""
import hashlib
import io
import json
import os
import re
import tempfile
import time

from . import srt

FPS = (23.976, 24.0, 25.0)
# every speed change between two of the usual film frame rates (1.0 included)
RATIOS = sorted(set(round(a / b, 6) for a in FPS for b in FPS))
SNAP = 0.005                    # a measured speed within 0.5 % of a standard ratio is that ratio
MIN_SPAN_MS = 8 * 60 * 1000     # sync points at least this far apart can measure drift
MAX_SKEW = 0.08                 # a speed further than 8 % from 1 is a mistake or a cut, not drift
DRIFT_MIN_MS = 2000             # a speed is only believed when it moves the lines this much between
                                # the two sync points (a press is easily 0.3 s early or late)
MAX_MISFIT_MS = 1500            # two sync points one speed explains to within this lie on one line;
                                # more means a cut or an extra scene between them
PRESS_NOISE_MS = 600            # two presses together can be this far off
OVERSHOOT_MS = 2000             # lines ending this much after the video's end cannot be right
MIN_OVERSHOOT_CUES = 5          # ... and it takes this many of them ("next time on" is a few lines)
MAX_GAP_MS = 60 * 1000          # a pause this long before the lines past the end ...
MIN_BLOCK_LINES = 10            # ... followed by fewer lines than this: something added at the end
MAX_LINE_MS = 15 * 1000         # a line on screen longer than this is a typo or a sign, not dialogue
MIN_OFF_LINES = 10              # "seems out of sync" (no frame rate explains it) needs this many lines
MIN_OFF_MS = 45 * 1000          # ... running on by at least this much
MIN_SHARE = 0.4                 # sure only when the lines run on by at least this share of what the
                                # frame-rate change predicts (else extra lines could explain it)
MIN_FPS_CHANGE = 0.01           # only changes of 1 % and more show at the end (not 23.976 vs 24)
MAX_POINTS = 10
MAX_FIXES = 400
MAX_PRODUCED = 200
INACTIVE = ('declined', 'cleared')   # records that say "leave the timing as it is"

_AD_RE = re.compile(
    r'(?i)subtit|opensubtitles|podnapisi|titlovi|addic7ed|www\.|https?:|\.(com|org|net)\b|'
    r'\bsync|corrected by|ripped by|translated by|subs by|advertis|support us|elderman|'
    # credits for the subtitle in other languages (Croatian/Serbian/Bosnian, Slovenian, German, ...)
    r'\bprevod|\bprijevod|\bpreve[ol]|\bprevedl|\bobrad[ae]\b|sinhroniz|sinkroniz|\btitlova|'
    r'untertitel|bersetz|napisy|t\w?umacz|traduc|traduit|sous-titr|sottotitol|\blegendas?\b|'
    r'ondertitel|vertaling|felirat|titulky|preklad|altyaz|çeviri')
_MUSIC_RE = re.compile(u'[♩♪♫♬¶#]')     # song lyrics, not dialogue
_FPS_TAG_RE = re.compile(r'(?i)(?<!\d)(23[.,]?976|23[.,]98|24(?:[.,]0+)?|25(?:[.,]0+)?)\s?fps')
_EDITION_RE = re.compile(r'(?i)(?<![a-z])(extended|uncut|unrated|theatrical|director.?s.?cut|final.?cut|'
                         r'ultimate|redux|special.?edition|imax)(?![a-z])')


# ---------------------------------------------------------------------- frame rates
def norm_fps(value):
    """The film frame rate behind a video frame rate (None if unknown).
    29.97 and 59.94 fps video of a film runs as long as 23.976; 50 fps as long as 25."""
    try:
        fps = float(('%s' % value).replace(',', '.'))
    except (TypeError, ValueError):
        return None
    if fps <= 1:
        return None
    best = None
    for base in (1.0, 2.0, 1.25, 2.5):       # 23.976/24/25, 47.95/48/50, 29.97/30, 59.94/60
        for film in FPS:
            error = abs(fps / base - film) / film
            if best is None or error < best[0]:
                best = (error, film)
    return best[1] if best[0] < 0.004 else None


def name_fps(name):
    """Frame rate a release name states (e.g. "25fps", "PAL"), or None."""
    name = name or ''
    m = _FPS_TAG_RE.search(name)
    if m:
        return norm_fps(m.group(1))
    if re.search(r'(?i)(?<![a-z])pal(?![a-z])', name):
        return 25.0
    return None


def fps_label(fps):
    return '%g' % fps if fps else '?'


def editions(name):
    """Edition words in a release name ('extended', 'directorscut', ...)."""
    return set(re.sub(r'[^a-z]', '', m.group(1).lower()) for m in _EDITION_RE.finditer(name or ''))


# ---------------------------------------------------------------------- applying a fix
def map_ms(ms, k, b):
    return int(round(k * ms + b))


def retime(cues, k=1.0, b=0.0, texts=None):
    """(cues, texts) with every time mapped. Lines that end up entirely before the
    start of the video are dropped (texts, when given, stay paired with their cues)."""
    out_cues, out_texts = [], []
    for n, cue in enumerate(cues):
        start, end = map_ms(cue.start, k, b), map_ms(cue.end, k, b)
        if end <= 0:
            continue
        out_cues.append(srt.Cue(max(0, start), end, cue.text))
        if texts is not None:
            out_texts.append(texts[n])
    return out_cues, (out_texts if texts is not None else None)


def compose(cues, texts=None, fix=None):
    """SRT text, with the fix (k, b) applied when given."""
    if fix:
        cues, texts = retime(cues, fix[0], fix[1], texts)
    return srt.compose(cues, texts)


def describe(k, b):
    """Short English description for the log, e.g. 'k=1.042709 b=+2.40s'."""
    return 'k=%.6f b=%+.2fs' % (k, b / 1000.0)


# ---------------------------------------------------------------------- fixes from sync points
def pick_points(points):
    """The newest point and the one farthest from it in subtitle time: the newest says
    where the film is now, the distance makes a drift measurement reliable."""
    if len(points) <= 2:
        return list(points)
    newest = points[-1]
    farthest = max(points[:-1], key=lambda p: abs(p[0] - newest[0]))
    return [farthest, newest]


def fit(points, ratio=None):
    """(k, b, how) from sync points [(subtitle_ms, video_ms), ...] (oldest first).

    how: 'none' (no points), 'offset' (a plain delay - one point, two close together, or
    two far apart that agree - on top of a known frame-rate ratio if there is one),
    'drift' (two points far apart whose speed is a standard frame-rate ratio) or 'uneven'
    (two points that no one speed explains - usually a cut or an extra scene between
    them: the newest delay is used, which is right from here on).

    The line always goes through the newest point: that is where the film is now, and
    what the user just checked. A frame-rate speed is only taken when it moves the lines
    clearly more than a press can be off over the distance between the points, and when
    it explains both points (a cut between them can look like a speed)."""
    base = ratio or 1.0
    pts = pick_points([(float(p[0]), float(p[1])) for p in points])
    if not pts:
        return base, 0.0, 'none'
    s_new, a_new = pts[-1]
    if len(pts) == 1 or abs(pts[-1][0] - pts[0][0]) < MIN_SPAN_MS:
        return base, a_new - base * s_new, 'offset'
    s_old, a_old = pts[0]
    span = abs(s_new - s_old)
    measured = (a_new - a_old) / (s_new - s_old)

    def misfit(k):
        return abs((a_new - a_old) - k * (s_new - s_old))

    speeds = [r for r in RATIOS if r != 1.0 and abs(r - measured) / r <= SNAP
              and abs(r - 1.0) <= MAX_SKEW and abs(r - 1.0) * span >= DRIFT_MIN_MS]
    if speeds:
        # ratios a press error apart cannot be told apart yet: the known one, else a
        # subtitle made for 23.976 fps (the usual), else the closest
        best = min(misfit(r) for r in speeds)
        close = [r for r in speeds if misfit(r) <= best + PRESS_NOISE_MS]
        speed = ratio if ratio in close else next(
            (r for r in close if fps_pair(r, None)[0] == 23.976), min(close, key=misfit))
        if misfit(speed) <= MAX_MISFIT_MS:
            return speed, a_new - speed * s_new, 'drift'
    if misfit(1.0) <= MAX_MISFIT_MS:
        return 1.0, a_new - s_new, 'offset'
    return base, a_new - base * s_new, 'uneven'


# ---------------------------------------------------------------------- drift check
def real_cues(cues):
    """Cues that look like dialogue (adverts and credits for the subtitle removed)."""
    return [c for c in cues if c.text and not _AD_RE.search(c.text)]


def dialogue(cues):
    """Dialogue lines in time order: credits and adverts for the subtitle and song
    lyrics (marked with a note sign) removed."""
    return sorted((c for c in real_cues(cues) if not _MUSIC_RE.search(c.text)), key=lambda c: c.start)


def fps_pair(r, video=None):
    """(subtitle fps, video fps) for a ratio, e.g. 0.95904 -> (23.976, 25.0)."""
    if video:
        return round(r * video, 3), video
    for a in FPS:
        for b in FPS:
            if abs(a / b - r) < 1e-5:
                return a, b
    return None, None


def drift_check(cues, duration_ms, video_fps=None, video_name='', sub_name=''):
    """Is this subtitle made for a version of the video that runs at another speed?

    Returns None (nothing to say) or a dict:
      ratio    k to apply (None when the subtitle is clearly off but no frame rate - or not
               only a frame rate - explains it: a job for the Sync assistant)
      certain  True when nothing else explains it (fixed without asking when the user chose that)
      from_fps, to_fps   frame rates for the message (None if unknown)
      overshoot_ms       how far the subtitle runs past the end of the video
      reason   for the log

    Evidence: dialogue that runs on past the end of the video cannot be right. A few
    lines after the end prove nothing ("next time on ...", a song over the credits), so
    it takes a run of dialogue with no long pause before it, about as many lines as a
    stretched subtitle would put there. If the dialogue starts later than that
    overshoot, a plain delay explains it as well. Otherwise the subtitle runs too slow
    and the frame-rate ratio that brings its last line back inside the video is the
    answer - for sure only when Kodi reports the video's frame rate, the overshoot is a
    good part of what that ratio predicts (not a few extra lines), and the release
    names do not point at another edition (an extended cut runs longer too). A frame
    rate stated in the subtitle's name ("25fps", "PAL") is a hint, never certain.
    """
    if not duration_ms or duration_ms < 5 * 60 * 1000 or not cues:
        return None
    # lines on screen for more than 15 s are typos or signs, not dialogue
    real = sorted((c for c in real_cues(cues) if 0 < c.end - c.start <= MAX_LINE_MS), key=lambda c: c.start)
    lines = [c for c in real if not _MUSIC_RE.search(c.text)]
    if len(lines) < 20:
        return None
    player_fps = norm_fps(video_fps)
    video = player_fps or name_fps(video_name)
    sub_hint = name_fps(sub_name)
    last_end = max(c.end for c in lines)
    over = [c for c in lines if c.end > duration_ms + OVERSHOOT_MS]
    if len(over) < MIN_OVERSHOOT_CUES:
        # nothing (much) past the end: only a frame rate in the subtitle's name can hint
        if sub_hint and video and abs(sub_hint - video) > 0.01:
            ratio = round(sub_hint / video, 6)
            if abs(ratio - 1.0) >= MIN_FPS_CHANGE and ratio * last_end <= duration_ms + OVERSHOOT_MS:
                return {'ratio': ratio, 'certain': False, 'from_fps': sub_hint, 'to_fps': video,
                        'overshoot_ms': 0, 'reason': 'the subtitle\'s name says %s fps, the video runs at %s fps'
                        % (fps_label(sub_hint), fps_label(video))}
        return None

    overshoot = last_end - duration_ms
    # Dialogue that runs on past the end goes on without a long pause (a song counts as going
    # on). After a long pause, a short block is something added at the end ("next time on").
    first = next(n for n, c in enumerate(real) if c.end > duration_ms + OVERSHOOT_MS)
    prev_end = max(c.end for c in real[:first]) if first else real[first].start
    after_pause = None                       # lines after the last long pause, if there is one
    for n in range(first, len(real)):
        if real[n].start - prev_end >= MAX_GAP_MS:
            after_pause = len(real) - n
        prev_end = max(prev_end, real[n].end)
    expected = len(lines) * float(overshoot) / max(1, last_end - lines[0].start)
    if (after_pause is not None and after_pause < MIN_BLOCK_LINES) or len(over) < 0.5 * expected:
        return None
    reason = '%d lines run past the end of the video (last one by %.0fs)' % (len(over), overshoot / 1000.0)

    candidates = sorted(set(round(f / video, 6) for f in FPS)) if video else list(RATIOS)
    candidates = [r for r in candidates if r < 1.0 and 1.0 - r >= MIN_FPS_CHANGE]
    credits_max = max(4 * 60 * 1000, 0.12 * duration_ms)
    fits = [r for r in candidates if duration_ms - credits_max <= r * last_end <= duration_ms + OVERSHOOT_MS]
    hinted = round(sub_hint / video, 6) if (sub_hint and video) else None
    dialogue_start = lines[min(2, len(lines) - 1)].start     # a stray credit line at 0:00 aside
    unexplained = None
    if not fits:
        unexplained = 'no frame rate explains it'
    elif dialogue_start >= overshoot and hinted not in fits:
        unexplained = 'a plain delay could explain it as well'
    elif sub_hint and video and abs(sub_hint - video) < 0.01:
        unexplained = 'the subtitle\'s name says it is made for this frame rate'
    elif editions(sub_name) - editions(video_name):
        unexplained = 'the subtitle is named for another edition (%s)' % ', '.join(sorted(editions(sub_name)))
    if unexplained:
        if after_pause is not None or len(over) < MIN_OFF_LINES or overshoot < MIN_OFF_MS:
            return None      # not enough to tell the user the subtitle is off
        return {'ratio': None, 'certain': False, 'from_fps': None, 'to_fps': video,
                'overshoot_ms': overshoot, 'reason': reason + '; ' + unexplained}

    ratio = hinted if hinted in fits else None
    if ratio is None:
        # most subtitles are made for 23.976 fps releases (WEB, Blu-ray), then 25 (PAL)
        for src in (23.976, 25.0, 24.0):
            ratio = next((r for r in fits if fps_pair(r, video)[0] == src), None)
            if ratio is not None:
                break
        if ratio is None:
            ratio = max(fits)                   # the smallest change that fits
    doubts = []
    if not player_fps:
        doubts.append('Kodi does not report the video\'s frame rate')
    if overshoot < MIN_SHARE * (1.0 - ratio) * last_end:
        doubts.append('it runs on by less than the frame rate would make it (extra lines?)')
    if [r for r in fits if abs(r - ratio) / ratio > 0.002]:
        doubts.append('more than one frame rate fits')
    if after_pause is not None:
        doubts.append('a long pause comes before the lines past the end')
    if dialogue_start >= overshoot:
        doubts.append('a plain delay could explain it as well')
    src, dst = fps_pair(ratio, video)
    return {'ratio': ratio, 'certain': not doubts, 'from_fps': src, 'to_fps': dst, 'overshoot_ms': overshoot,
            'reason': reason + ('; not sure: ' + ', '.join(doubts) if doubts else '')}


# ---------------------------------------------------------------------- identities
def video_key(file_name, title=''):
    """Stable id for 'this video' across sessions: the release file name when it says
    something (stream links change every time, their file names do not), else the title."""
    stem = (file_name or '').rsplit('.', 1)[0] if '.' in (file_name or '') else (file_name or '')
    tokens = re.findall(r'[A-Za-z0-9]+', stem)
    basis = stem.lower() if len(tokens) >= 3 else (title or stem or '').lower()
    return hashlib.md5(basis.encode('utf-8')).hexdigest()[:16]


def fix_key(video, subtitle_md5):
    return '%s|%s' % (video, subtitle_md5 or '')


# ---------------------------------------------------------------------- fix store
def is_change(fix):
    """True when a fix (k, b) moves the lines at all (by 50 ms or more somewhere early on)."""
    return bool(fix) and (abs(fix[0] - 1.0) > 1e-9 or abs(fix[1]) >= 50)


def same_fix(a, b):
    """Two fixes (or None for 'as it is') that put every line at the same place."""
    if not is_change(a) or not is_change(b):
        return not is_change(a) and not is_change(b)
    return abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1.0


class FixStore(object):
    """timing.json in the add-on's profile:
      fixes     {key: {k, b, how, ratio, points, declined, title, updated}}
                how: offset / drift / uneven (Sync assistant), framerate (drift check),
                declined (the user said no to a frame-rate fix) or cleared (Undo) - the last
                two mean "leave the timing as it is" and stop the drift check asking again
      produced  {md5 of a re-timed file Dragoman handed to Kodi: {md5 (of the subtitle it was
                made from), lang, kind, k, b, base (lasting copy of the unmapped text), ...}}
                Kodi may keep such a file next to the video, in place of the subtitle there.
    Written by the service and by the menu / subtitle dialog, so every write re-reads the
    file and replaces it atomically."""

    def __init__(self, folder):
        self.path = os.path.join(folder, 'timing.json')

    def _read(self):
        try:
            with io.open(self.path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (IOError, OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        out = {}
        for part in ('fixes', 'produced'):
            value = data.get(part)
            out[part] = value if isinstance(value, dict) else {}
        return out

    def _write(self, data):
        for part, limit in (('fixes', MAX_FIXES), ('produced', MAX_PRODUCED)):
            items = data.get(part) or {}
            if len(items) > limit:
                keep = sorted(items.items(), key=lambda kv: kv[1].get('updated', 0), reverse=True)[:limit]
                data[part] = dict(keep)
        folder = os.path.dirname(self.path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        fd, tmp = tempfile.mkstemp(prefix='timing.', suffix='.tmp', dir=folder)
        try:
            with io.open(fd, 'w', encoding='utf-8') as handle:
                handle.write(json.dumps(data, ensure_ascii=False))
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def _load(self):
        return self._read()['fixes']

    def _update(self, key, change):
        """Read, let change(rec) edit this key's record (a dict, empty if new), write; returns it."""
        data = self._read()
        rec = data['fixes'].get(key)
        rec = dict(rec) if isinstance(rec, dict) else {}
        change(rec)
        rec['updated'] = int(time.time())
        data['fixes'][key] = rec
        self._write(data)
        return rec

    def get(self, key):
        rec = self._load().get(key)
        return rec if isinstance(rec, dict) else None

    def mapping(self, key):
        """(k, b) of the stored fix, or None when there is none or it changes nothing."""
        rec = self.get(key)
        if not rec or rec.get('how') in INACTIVE:
            return None
        try:
            fix = float(rec.get('k', 1.0)), float(rec.get('b', 0.0))
        except (TypeError, ValueError):
            return None
        return fix if is_change(fix) else None

    @staticmethod
    def _points(rec):
        return [p for p in rec.get('points', []) if isinstance(p, list) and len(p) >= 2]

    def add_point(self, key, subtitle_ms, video_ms, title=''):
        """Record one Sync assistant measurement; returns the new record."""
        def change(rec):
            if rec.get('how') in INACTIVE:
                rec.pop('ratio', None)
            points = self._points(rec)
            points.append([int(subtitle_ms), int(video_ms), int(time.time())])
            points = points[-MAX_POINTS:]
            k, b, how = fit([(p[0], p[1]) for p in points], rec.get('ratio'))
            rec.update({'k': k, 'b': b, 'how': how, 'points': points})
            if title:
                rec['title'] = title
        return self._update(key, change)

    def set_ratio(self, key, ratio, title=''):
        """A frame-rate fix (from the drift check); sync points made before are kept."""
        def change(rec):
            points = [] if rec.get('how') in INACTIVE else self._points(rec)
            rec['ratio'] = ratio
            if points:
                k, b, how = fit([(p[0], p[1]) for p in points], ratio)
            else:
                k, b, how = ratio, 0.0, 'framerate'
            rec.update({'k': k, 'b': b, 'how': how, 'points': points})
            if title:
                rec['title'] = title
        return self._update(key, change)

    def decline(self, key, ratio, title=''):
        """The user said no to a frame-rate fix: remembered, so nobody asks again."""
        def change(rec):
            rec.clear()
            rec.update({'k': 1.0, 'b': 0.0, 'how': 'declined', 'declined': ratio, 'points': []})
            if title:
                rec['title'] = title
        return self._update(key, change)

    def clear(self, key):
        """Undo: the subtitle is shown as it is, now and next time. False when there was no fix."""
        if self.mapping(key) is None:
            return False

        def change(rec):
            ratio = rec.get('ratio') or rec.get('declined')
            title = rec.get('title')
            rec.clear()
            rec.update({'k': 1.0, 'b': 0.0, 'how': 'cleared', 'points': []})
            if ratio:
                rec['declined'] = ratio
            if title:
                rec['title'] = title
        self._update(key, change)
        return True

    def delete(self, key):
        data = self._read()
        if key in data['fixes']:
            del data['fixes'][key]
            self._write(data)
            return True
        return False

    # ------------------------------------------------------------------ re-timed copies
    def note_produced(self, md5, info):
        data = self._read()
        info = dict(info)
        info['updated'] = int(time.time())
        data['produced'][md5] = info
        self._write(data)

    def produced(self, md5):
        info = self._read()['produced'].get(md5 or '')
        return info if isinstance(info, dict) else None

    def produced_bases(self):
        return set(i.get('base') for i in self._read()['produced'].values() if isinstance(i, dict))
