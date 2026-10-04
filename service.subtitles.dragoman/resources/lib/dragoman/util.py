# -*- coding: utf-8 -*-
"""Pure helpers with no Kodi imports, so they can be unit-tested anywhere.

Keep this file Python 3.6-compatible: no walrus, no f-string '=' specifier,
no dict union, no str.removeprefix.
"""
import hashlib
import re

from urllib.parse import unquote, urlsplit

SUB_EXTS = ('.srt', '.ass', '.ssa', '.vtt', '.sub', '.idx', '.smi', '.sami',
            '.sup', '.ttml', '.dfxp')
ARCHIVE_EXTS = ('.zip', '.rar', '.gz', '.7z')
TEXT_SUB_EXTS = ('.srt', '.ass', '.ssa', '.vtt', '.smi', '.sami', '.sub')

_SCHEME_RE = re.compile(r'^([A-Za-z][A-Za-z0-9+.\-]*)://')
_URL_RE = re.compile(
    r'\b(?:https?|rtmps?|rtsp|mms|davs?|smb|nfs|ftps?|sftp|plugin)://[^\s"\'<>,\]\)}]+',
    re.IGNORECASE)
_GOOGLE_KEY_RE = re.compile(r'AIza[0-9A-Za-z_\-]{20,}')
_SECRET_PARAM_RE = re.compile(
    r'(?i)\b(api[_-]?key|apikey|access[_-]?token|token|password|passwd|auth|key)=([^&\s"\']+)')
_OPENAI_KEY_RE = re.compile(r'\bsk-[A-Za-z0-9_\-]{16,}')
_HOME_RE = re.compile(r'(?i)([\\/](?:users|home|documents and settings)[\\/])(?!shared\b|public\b)[^\\/\s\'"]+')

LOCAL_SHARE_SCHEMES = ('smb', 'nfs', 'sftp', 'ftp', 'ftps', 'dav', 'davs')


def name_kind(name):
    """'sub' (known subtitle ending), 'archive', 'maybe' (worth a look inside:
    ends in a language code like .eng, or Kodi's TempSubtitle.*), or None."""
    low = name.lower()
    base, dot, ext = low.rpartition('.')
    if not dot:
        return None
    if '.' + ext in SUB_EXTS:
        return 'sub'
    if '.' + ext in ARCHIVE_EXTS:
        return 'archive'
    if low.startswith('tempsubtitle.') or ext in _LANG_LOOKUP:
        return 'maybe'
    return None


_CUE_TIMING_RE = re.compile(r'\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3}\s*-->')


def sniff_format(head):
    """Subtitle format from the first bytes of a file: 'srt', 'vtt', 'ass' or ''."""
    text = head[:8192].decode('latin-1')
    if text.startswith('\xef\xbb\xbf'):
        text = text[3:]
    stripped = text.lstrip()
    if stripped.upper().startswith('WEBVTT'):
        return 'vtt'
    if '[Script Info]' in text or re.search(r'(?m)^Dialogue:', text):
        return 'ass'
    if _CUE_TIMING_RE.search(text):
        return 'srt'
    return ''


def has_scheme(path):
    return bool(path) and bool(_SCHEME_RE.match(path)) and not _is_windows_drive(path)


def _is_windows_drive(path):
    return len(path) > 2 and path[1] == ':' and path[0].isalpha() and path[2] in '\\/'


def scheme_of(path):
    m = _SCHEME_RE.match(path or '')
    return m.group(1).lower() if m else ''


def safe_location(path):
    """Shorten a playing path or URL for logs.

    Keeps the file name (it carries the release tags) and drops hosts,
    credentials, query strings and tokens. LAN share paths keep their folders.
    """
    if not path:
        return ''
    p = path.split('|', 1)[0]
    scheme = scheme_of(p)
    if not scheme or scheme in ('special', 'file'):
        return p
    try:
        parts = urlsplit(p)
    except ValueError:
        return scheme + '://...'
    if scheme == 'plugin':
        tail = '...' if (parts.path.strip('/') or parts.query) else ''
        return 'plugin://{0}/{1}'.format(parts.netloc, tail)
    if scheme in LOCAL_SHARE_SCHEMES:
        host = parts.hostname or ''
        return '{0}://{1}{2}'.format(scheme, host, unquote(parts.path))
    name = ''
    if parts.path:
        name = unquote(parts.path.rstrip('/').rsplit('/', 1)[-1])
    return '{0}://.../{1}'.format(scheme, name) if name else scheme + '://...'


def sanitize_text(text):
    """Remove links, tokens and API keys from any text before it is logged."""
    if not text:
        return text
    text = _URL_RE.sub(lambda m: safe_location(m.group(0)), text)
    text = _SECRET_PARAM_RE.sub(lambda m: m.group(1) + '=***', text)
    text = _GOOGLE_KEY_RE.sub('AIza***', text)
    text = _OPENAI_KEY_RE.sub('sk-***', text)
    text = _HOME_RE.sub(lambda m: m.group(1) + '<user>', text)
    return text


def file_name(path):
    """Last path component of a path or URL (query and Kodi '|' options removed)."""
    if not path:
        return ''
    p = path.split('|', 1)[0]
    if has_scheme(p):
        try:
            p = urlsplit(p).path
        except ValueError:
            pass
        p = unquote(p)
    p = p.rstrip('/\\')
    idx = max(p.rfind('/'), p.rfind('\\'))
    return p[idx + 1:] if idx >= 0 else p


def video_folder(path):
    """Folder of a local or LAN-share video, or None for streams and plugins."""
    if not path:
        return None
    p = path.split('|', 1)[0]
    if has_scheme(p):
        scheme = scheme_of(p)
        if scheme not in LOCAL_SHARE_SCHEMES and scheme != 'file':
            return None
        p = p.split('?', 1)[0]
        idx = p.rfind('/')
        return p[:idx + 1] if idx > len(scheme) + 3 else None
    idx = max(p.rfind('/'), p.rfind('\\'))
    return p[:idx + 1] if idx > 0 else None


# ----------------------------------------------------------------------------
# Language guess from a subtitle file name
# ----------------------------------------------------------------------------
_LANG_TABLE = {
    'en': ('en', 'eng', 'english'),
    'sl': ('sl', 'slv', 'slovenian', 'slovene', 'slovenscina'),
    'hr': ('hr', 'hrv', 'scr', 'croatian', 'hrvatski'),
    'sr': ('sr', 'srp', 'scc', 'serbian', 'srpski'),
    'bs': ('bs', 'bos', 'bosnian', 'bosanski'),
    'mk': ('mk', 'mac', 'mkd', 'macedonian'),
    'de': ('de', 'ger', 'deu', 'german', 'deutsch'),
    'es': ('es', 'spa', 'spanish', 'espanol'),
    'fr': ('fr', 'fre', 'fra', 'french', 'francais'),
    'it': ('it', 'ita', 'italian', 'italiano'),
    'pt': ('pt', 'por', 'portuguese', 'pb', 'pob'),
    'nl': ('nl', 'dut', 'nld', 'dutch'),
    'pl': ('pl', 'pol', 'polish'),
    'cs': ('cs', 'cze', 'ces', 'czech'),
    'sk': ('sk', 'slo', 'slk', 'slovak'),
    'hu': ('hu', 'hun', 'hungarian'),
    'ro': ('ro', 'rum', 'ron', 'romanian'),
    'bg': ('bg', 'bul', 'bulgarian'),
    'ru': ('ru', 'rus', 'russian'),
    'uk': ('uk', 'ukr', 'ukrainian'),
    'el': ('el', 'gre', 'ell', 'greek'),
    'tr': ('tr', 'tur', 'turkish'),
    'ar': ('ar', 'ara', 'arabic'),
    'he': ('he', 'heb', 'hebrew'),
    'sv': ('sv', 'swe', 'swedish'),
    'no': ('no', 'nb', 'nor', 'nob', 'norwegian'),
    'da': ('da', 'dan', 'danish'),
    'fi': ('fi', 'fin', 'finnish'),
    'sq': ('sq', 'alb', 'sqi', 'albanian'),
    'zh': ('zh', 'chi', 'zho', 'chinese'),
    'ja': ('ja', 'jpn', 'japanese'),
    'ko': ('ko', 'kor', 'korean'),
    'id': ('id', 'ind', 'indonesian'),
    'vi': ('vi', 'vie', 'vietnamese'),
    'fa': ('fa', 'per', 'fas', 'persian'),
    'hi': ('hin', 'hindi'),
}
_LANG_LOOKUP = {}
for _code, _aliases in _LANG_TABLE.items():
    for _alias in _aliases:
        _LANG_LOOKUP[_alias] = _code
_NAME_FLAGS = ('forced', 'sdh', 'cc', 'hi', 'default', 'full', 'signs', 'songs',
               'sub', 'subs', 'subtitle', 'subtitles')


def guess_lang(name):
    """ISO 639-1 code from the trailing tokens of a file name, or ''.

    Works with 'Movie.en.srt' and with names whose ending IS the language
    ('TempSubtitle.en.eng', 'Movie.1080p.eng' - a4kSubtitles saves some
    downloads like that)."""
    base, dot, ext = name.rpartition('.')
    stem = base if (dot and ('.' + ext.lower()) in SUB_EXTS + ARCHIVE_EXTS) else name
    tokens = [t for t in re.split(r'[.\s_\-\[\]()]+', stem.lower()) if t]
    for tok in reversed(tokens[-4:]):
        if tok in _NAME_FLAGS:
            continue
        return _LANG_LOOKUP.get(tok, '')
    return ''


# ----------------------------------------------------------------------------
# Subtitle file inspection
# ----------------------------------------------------------------------------
def decode_text(raw):
    """Decode subtitle bytes: utf-8-sig, utf-16 (BOM), utf-8, cp1252, latin-1."""
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw[3:].decode('utf-8', 'replace'), 'utf-8-sig'
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16', 'replace'), 'utf-16'
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode('cp1252'), '8-bit (read as cp1252)'
    except UnicodeDecodeError:
        return raw.decode('latin-1'), '8-bit (read as latin-1)'


_TIME_RE = r'(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})'
_CUE_RE = re.compile(_TIME_RE + r'\s*-->\s*' + _TIME_RE)
_ASS_RE = re.compile(r'^Dialogue:\s*[^,]*,(\d+):(\d{2}):(\d{2})[.](\d{1,3}),(\d+):(\d{2}):(\d{2})[.](\d{1,3}),',
                     re.MULTILINE)
_TAG_RE = re.compile(r'<[^>]+>|\{[^}]*\}')


def _secs(h, m, s, frac):
    frac = (frac or '0')
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(frac.ljust(3, '0')[:3]) / 1000.0


def inspect_subtitle(name, raw):
    """Facts about a subtitle file for the log. raw may be None (not read)."""
    facts = {'lang': guess_lang(name)}
    if raw is None:
        return facts
    facts['md5'] = hashlib.md5(raw).hexdigest()[:8]
    ext = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
    if ext not in TEXT_SUB_EXTS:
        return facts
    text, enc = decode_text(raw)
    facts['enc'] = enc
    facts['crlf'] = '\r\n' in text[:4000]
    cues = 0
    first = None
    last_end = 0.0
    if ext in ('.ass', '.ssa'):
        for m in _ASS_RE.finditer(text):
            g = m.groups()
            start = _secs(g[0], g[1], g[2], g[3])
            end = _secs(g[4], g[5], g[6], g[7])
            cues += 1
            first = start if first is None else min(first, start)
            last_end = max(last_end, end)
        preview = ''
        for line in text.splitlines():
            if line.startswith('Dialogue:'):
                parts = line.split(',', 9)
                if len(parts) == 10:
                    preview = _TAG_RE.sub('', parts[9]).replace('\\N', ' / ').strip()
                    if preview:
                        break
    else:
        for m in _CUE_RE.finditer(text):
            g = m.groups()
            start = _secs(g[0], g[1], g[2], g[3])
            end = _secs(g[4], g[5], g[6], g[7])
            cues += 1
            if first is None:
                first = start
            last_end = max(last_end, end)
        preview = _first_text_line(text)
    facts['cues'] = cues
    facts['first'] = first
    facts['last_end'] = last_end if cues else None
    facts['preview'] = preview[:90]
    return facts


def _first_text_line(text):
    for line in text.splitlines():
        s = line.strip()
        if not s or s.isdigit() or '-->' in s or s.upper().startswith('WEBVTT'):
            continue
        if s.startswith('NOTE') or s.startswith('STYLE'):
            continue
        s = _TAG_RE.sub('', s).strip()
        if s:
            return s
    return ''


# ----------------------------------------------------------------------------
# Formatting
# ----------------------------------------------------------------------------
def fmt_ts(seconds):
    if seconds is None:
        return '?'
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return '?'
    sign = '-' if seconds < 0 else ''
    ms_total = int(round(abs(seconds) * 1000))
    h, rem = divmod(ms_total, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return '{0}{1}:{2:02d}:{3:02d}.{4:03d}'.format(sign, h, m, s, ms)


def fmt_dur(seconds):
    """Like fmt_ts but without milliseconds."""
    if seconds is None:
        return '?'
    seconds = int(round(float(seconds)))
    h, rem = divmod(abs(seconds), 3600)
    m, s = divmod(rem, 60)
    return '{0}{1}:{2:02d}:{3:02d}'.format('-' if seconds < 0 else '', h, m, s)


def fmt_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return '?'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('{0:.0f} {1}' if unit == 'B' else '{0:.1f} {1}').format(n, unit)
        n /= 1024.0
    return '?'


def fmt_ago(seconds):
    if seconds is None:
        return 'never'
    return '{0:.1f}s ago'.format(seconds)


def clip(text, limit):
    text = '' if text is None else '{0}'.format(text)
    text = text.replace('\r', ' ').replace('\n', ' ')
    return text if len(text) <= limit else text[:limit - 3] + '...'


def guess_source(root_kind, since_subdlg, manual_window):
    """Recon's guess at where a new subtitle came from (validated by the logs)."""
    if root_kind == 'video':
        return 'local'
    if since_subdlg is not None and since_subdlg <= manual_window:
        return 'manual'
    return 'auto'
