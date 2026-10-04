# -*- coding: utf-8 -*-
"""SRT reading and writing (no Kodi imports).

Reading is forgiving: BOMs, CRLF, missing or extra blank lines, missing cue
numbers, ',' or '.' before milliseconds, position suffixes after timings.
Timestamps are kept as numbers and never leave the device.
"""
import codecs
import re

from . import langdetect

_TIMING_RE = re.compile(
    r'^\s*(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})[,.:](\d{1,3})\s*-{1,2}>\s*'
    r'(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})[,.:](\d{1,3})')
# WebVTT-only markup: voice/class/language spans and karaoke timestamps
_VTT_TAG_RE = re.compile(r'</?(?:v|c|lang|ruby|rt)(?:[.\s][^>]*)?>|<\d{1,2}:[\d:.]+>')
_CE_LANGS = ('sl', 'hr', 'sr', 'bs', 'cs', 'sk', 'pl', 'hu', 'ro', 'sq')


class Cue(object):
    __slots__ = ('start', 'end', 'text')

    def __init__(self, start, end, text):
        self.start = start      # milliseconds
        self.end = end
        self.text = text        # lines joined with '\n'

    def __repr__(self):
        return 'Cue(%d, %d, %r)' % (self.start, self.end, self.text)


def _ms(h, m, s, frac):
    return ((int(h or 0) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, '0')[:3])


def _lookup(charset):
    if not charset or charset.strip().upper() in ('DEFAULT', 'AUTO', 'SYSTEM'):
        return None
    try:
        return codecs.lookup(charset.strip()).name
    except LookupError:
        return None


def decode(raw, charset_hint=None):
    """(text, encoding) for subtitle bytes.

    Order: BOM, strict UTF-8, Kodi's subtitle charset setting (if set), then a
    guess between cp1251 (Cyrillic), cp1250 (Central European, e.g. č ć đ) and
    cp1252 (Western), finally latin-1.
    """
    if raw.startswith(codecs.BOM_UTF8):
        return raw[3:].decode('utf-8', 'replace'), 'utf-8-sig'
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode('utf-16', 'replace'), 'utf-16'
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        pass
    hinted = _lookup(charset_hint)
    if hinted:
        try:
            return raw.decode(hinted), hinted
        except UnicodeDecodeError:
            pass
    guess = _guess_8bit(raw)
    try:
        return raw.decode(guess), guess
    except UnicodeDecodeError:
        return raw.decode('latin-1'), 'latin-1'


def _guess_8bit(raw):
    sample = raw[:200000]
    high = sum(1 for b in bytearray(sample) if b >= 0xC0)
    ascii_letters = sum(1 for b in bytearray(sample) if 65 <= b <= 90 or 97 <= b <= 122)
    if high > ascii_letters * 0.6:
        return 'cp1251'
    # č/ć/đ (cp1250) and è/æ/ð (cp1252) share bytes, so decide by language:
    # the common-word check works on the plain ASCII words.
    code, _conf = langdetect.detect_text(sample.decode('cp1252', 'replace'))
    return 'cp1250' if code in _CE_LANGS else 'cp1252'


def parse(text):
    """List of Cue from SRT or WebVTT text. Lines outside cues are ignored."""
    text = text.lstrip(u'\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    vtt = text.lstrip().upper().startswith('WEBVTT')
    lines = text.split('\n')
    cues = []
    current = None
    body = []
    state = {'numbered': False, 'last': ''}

    def flush(before_next_cue=True):
        if current is not None and vtt:
            # A WebVTT cue ends at the first blank line; what follows (NOTE
            # blocks, the next cue's id) is not text.
            if '' in [l.strip() for l in body]:
                del body[[l.strip() for l in body].index(''):]
            body[:] = [_VTT_TAG_RE.sub('', l) for l in body]
        if current is not None:
            while body and not body[-1].strip():
                body.pop()
            # In numbered files, a trailing bare number right before a timing
            # line is the next cue's index, not text - if it follows a blank
            # line or is exactly the index the next cue should have.
            if before_next_cue and state['numbered'] and body and body[-1].strip().isdigit():
                after_blank = len(body) > 1 and not body[-2].strip()
                if after_blank or int(body[-1].strip()) == len(cues) + 2:
                    body.pop()
            cleaned = [l.rstrip() for l in body if l.strip()]
            cues.append(Cue(current[0], current[1], '\n'.join(cleaned)))

    for line in lines:
        m = _TIMING_RE.match(line)
        if m:
            if current is None and state['last'].isdigit():
                state['numbered'] = True     # the first cue has a number line
            flush()
            g = m.groups()
            current = (_ms(g[0], g[1], g[2], g[3]), _ms(g[4], g[5], g[6], g[7]))
            body = []
        elif current is not None:
            body.append(line)
        elif line.strip():
            state['last'] = line.strip()
    flush(before_next_cue=False)
    return cues


def _stamp(ms):
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return '%02d:%02d:%02d,%03d' % (h, m, s, ms)


def compose(cues, texts=None):
    """SRT text. texts (optional) replaces each cue's text, same order."""
    out = []
    for n, cue in enumerate(cues):
        body = cue.text if texts is None else texts[n]
        body = clean_text(body) or cue.text or ' '
        out.append('%d\n%s --> %s\n%s\n' % (n + 1, _stamp(cue.start), _stamp(cue.end), body))
    return '\n'.join(out)


def clean_text(text):
    """Cue text safe for SRT: no blank lines inside, no stray whitespace."""
    if text is None:
        return ''
    text = ('%s' % text).replace('\r\n', '\n').replace('\r', '\n')
    return '\n'.join(l.strip() for l in text.split('\n') if l.strip())
