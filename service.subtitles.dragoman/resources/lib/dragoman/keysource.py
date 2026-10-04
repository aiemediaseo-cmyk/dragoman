# -*- coding: utf-8 -*-
"""Getting the Gemini API key out of text, a file or a web address.

A web address is typically a paste the user made on a phone or computer
(Pastebin, paste.kodi.tv, a GitHub gist ...). Share pages are turned into the
site's plain-text ("raw") address first; if that does not work, the page itself
is read and the key is taken from its visible text. The key is never logged.
"""
import codecs
import http.client
import re
import socket
import ssl
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Gemini keys are AIza + 35 characters; the loose pattern catches other lengths.
EXACT_KEY_RE = re.compile(r'(?<![0-9A-Za-z_\-])AIza[0-9A-Za-z_\-]{35}(?![0-9A-Za-z_\-])')
KEY_RE = re.compile(r'AIza[0-9A-Za-z_\-]{30,}')
READ_LIMIT = 256 * 1024
TIMEOUT = 20

# (pattern for the address the user typed, plain-text address). Matched after normalize().
RAW_RULES = (
    (r'^https?://(?:www\.)?pastebin\.com/(?:raw/|dl/|embed/)?([A-Za-z0-9]{4,})/?$',
     'https://pastebin.com/raw/{0}'),
    (r'^https?://paste\.kodi\.tv/(?:raw/|documents/)?([A-Za-z0-9]{4,})(?:\.[A-Za-z0-9]+)?/?$',
     'https://paste.kodi.tv/raw/{0}'),
    (r'^https?://gist\.github\.com/([^/\s]+)/([0-9a-fA-F]{8,})/?$',
     'https://gist.githubusercontent.com/{0}/{1}/raw'),
    (r'^https?://gist\.github\.com/([0-9a-fA-F]{8,})/?$',
     'https://gist.github.com/{0}/raw'),
    (r'^https?://paste\.ee/p/([A-Za-z0-9]+)/?$', 'https://paste.ee/r/{0}'),
    (r'^https?://(?:www\.)?dpaste\.org/([A-Za-z0-9]+)/?$', 'https://dpaste.org/{0}/raw'),
    (r'^https?://(?:www\.)?dpaste\.com/([A-Za-z0-9]+)/?$', 'https://dpaste.com/{0}.txt'),
    (r'^https?://drive\.google\.com/file/d/([A-Za-z0-9_\-]+)(?:/.*)?$',
     'https://drive.google.com/uc?export=download&id={0}'),
    (r'^https?://drive\.google\.com/open\?id=([A-Za-z0-9_\-]+)',
     'https://drive.google.com/uc?export=download&id={0}'),
    (r'^https?://docs\.google\.com/document/d/([A-Za-z0-9_\-]+)(?:[/?#].*)?$',
     'https://docs.google.com/document/d/{0}/export?format=txt'),
)


class FetchError(Exception):
    def __init__(self, kind, message, code=0):
        Exception.__init__(self, message)
        self.kind = kind          # 'address' | 'http' | 'network' | 'timeout' | 'nokey'
        self.message = message
        self.code = code


def find_keys(text):
    """Distinct AIza... keys in a piece of text, in order (exact-length keys first)."""
    keys = []
    for pattern in (EXACT_KEY_RE, KEY_RE):
        for m in pattern.finditer(text or ''):
            found = m.group(0)
            options = [found]
            if len(found) > 39 and found[39] in '-_':     # "AIza...xyz-thanks": offer the 39 first
                options = [found[:39], found]
            for key in options:
                if key not in keys:
                    keys.append(key)
    return keys


def find_key(text):
    """The API key in a piece of text: an AIza... key anywhere, or the text itself
    when it is one plain token (keys of other shapes, typed or pasted alone)."""
    keys = find_keys(text)
    if keys:
        return keys[0]
    tokens = (text or '').split()
    if len(tokens) == 1 and 20 <= len(tokens[0]) <= 200:
        return tokens[0]
    return ''


def keys_in(text, is_html=False):
    """All candidate keys in fetched or read text (for HTML only what a browser shows)."""
    if is_html:
        return find_keys(visible_text(text))
    key = find_key(text)
    others = [k for k in find_keys(text) if k != key]
    return ([key] if key else []) + others


def looks_like_gemini_key(key):
    return bool(re.match(r'^AIza[0-9A-Za-z_\-]{35}$', key or ''))


def normalize(address):
    """What the user typed -> a full http(s) address, or '' if it cannot be one."""
    address = (address or '').strip().strip('"\'<>')
    if not address or re.search(r'[\s\[\]]', address):
        return ''
    if not re.match(r'^[A-Za-z][A-Za-z0-9+.\-]*://', address):
        address = 'https://' + address.lstrip('/')
    m = re.match(r'^https?://[^/\s]+\.[^/\s?#]+([/?#].*)?$', address, re.I)
    if not m or not (m.group(1) or '').strip('/?#'):
        return ''          # a site's front page cannot be the page with the key
    return address


def raw_address(address):
    """The plain-text version of a share page, or the address unchanged."""
    bare = re.split(r'[?#]', address, 1)[0]       # pastebin.com/X?foo, gist ...#file-key-txt
    for candidate in (address, bare):
        for pattern, template in RAW_RULES:
            m = re.match(pattern, candidate, re.I)
            if m:
                return template.format(*m.groups())
    if re.match(r'^https?://(?:www\.)?dropbox\.com/', address, re.I):
        if re.search(r'[?&]dl=0', address):
            return re.sub(r'([?&])dl=0', r'\1dl=1', address)
        if 'dl=1' not in address:
            return address + ('&' if '?' in address else '?') + 'dl=1'
    return address


def candidates(address):
    """Addresses to try, most likely first."""
    full = normalize(address)
    if not full:
        return []
    raw = raw_address(full)
    return [raw, full] if raw != full else [full]


class _TextOnly(HTMLParser):
    HIDDEN = ('script', 'style', 'noscript', 'template', 'head', 'title')
    BLOCK = ('p', 'div', 'br', 'li', 'tr', 'td', 'th', 'pre', 'textarea', 'section', 'article',
             'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ol', 'ul', 'table', 'body', 'code')

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden += 1
        elif tag in self.BLOCK:
            self.parts.append('\n')

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        elif tag in self.BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def visible_text(page):
    """Text a browser would show for an HTML page: no tags or attributes, no scripts or
    styles, entities decoded, inline tags (highlighting) not splitting words. So keys a
    site embeds in its own scripts or attributes are not picked up by mistake."""
    parser = _TextOnly()
    try:
        parser.feed(page)
        parser.close()
    except Exception:          # malformed markup: keep what was read so far
        pass
    return ''.join(parser.parts)


def decode(data, charset=None):
    """Bytes from a page or a file -> text (BOMs, the declared charset, then UTF-8)."""
    for bom, encoding in ((codecs.BOM_UTF8, 'utf-8'), (codecs.BOM_UTF16_LE, 'utf-16-le'),
                          (codecs.BOM_UTF16_BE, 'utf-16-be')):
        if data.startswith(bom):
            return data[len(bom):].decode(encoding, 'replace')
    if data[:200].count(b'\x00') > 20:            # UTF-16 without a BOM
        return data.decode('utf-16-le' if data[1:2] == b'\x00' else 'utf-16-be', 'replace')
    try:
        return data.decode(charset or 'utf-8', 'replace')
    except LookupError:
        return data.decode('utf-8', 'replace')


def _is_html(content_type, text):
    return 'html' in (content_type or '').lower() or text.lstrip()[:15].lower().startswith(('<!doctype', '<html'))


def http_get(url, user_agent, timeout=TIMEOUT):
    """(content type, text). Follows redirects. Raises FetchError."""
    request = Request(url, headers={'User-Agent': user_agent,
                                    'Accept': 'text/plain, text/html;q=0.9, */*;q=0.5'})
    try:
        response = urlopen(request, timeout=timeout, context=ssl.create_default_context())
        try:
            data = response.read(READ_LIMIT)
            content_type = response.headers.get('Content-Type', '')
            charset = response.headers.get_content_charset()
        finally:
            response.close()
    except HTTPError as exc:
        raise FetchError('http', 'HTTP %d' % exc.code, exc.code)
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            raise FetchError('timeout', 'no answer within %d s' % timeout)
        raise FetchError('network', '%s' % reason)
    except socket.timeout:
        raise FetchError('timeout', 'no answer within %d s' % timeout)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise FetchError('network', str(exc) or exc.__class__.__name__)
    return content_type, decode(data, charset).lstrip('\ufeff')


def key_from_address(address, user_agent, get=None):
    """Fetch the address (plain-text version first) and return (keys, address used),
    keys being every candidate found, best first. Raises FetchError when there is none."""
    get = get or http_get
    tried = candidates(address)
    if not tried:
        raise FetchError('address', 'not a web address')
    first_error = None
    for url in tried:
        try:
            content_type, text = get(url, user_agent)
        except FetchError as exc:
            first_error = first_error or exc
            if exc.kind == 'http':
                continue          # e.g. no plain-text version: try the page itself
            break                 # no connection: the next address is on the same site
        keys = keys_in(text, _is_html(content_type, text))
        if keys:
            return keys, url
        raise FetchError('nokey', 'no key in the text')
    raise first_error
