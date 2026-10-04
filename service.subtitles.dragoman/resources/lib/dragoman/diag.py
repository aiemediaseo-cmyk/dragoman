# -*- coding: utf-8 -*-
"""Log report for support: upload to paste.kodi.tv, save to a folder, or show.
The API key, video links and tokens are removed before anything leaves the device."""
import io
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import xbmcvfs

from . import envinfo, kodi, util
from .config import Config
from .log import get_log

PASTE_URL = 'https://paste.kodi.tv/'
UPLOAD_LIMIT = 900000
VIEW_LIMIT = 150000
SAVE_LIMIT = 5 * 1024 * 1024

_KODILOG_MATCH = re.compile(r'(?i)subtit|a4k|\.srt\b|dragoman|opensubtitles')
_KODILOG_HEAD_MATCH = re.compile(r'(?i)starting kodi|running on|host cpu|using (release|debug)|platform:|product:')


class UploadError(Exception):
    pass


def service_status():
    try:
        beat = int(kodi.home_window().getProperty(kodi.HEARTBEAT_PROP) or 0)
    except (TypeError, ValueError):
        beat = 0
    if not beat:
        return False, None
    age = time.time() - beat
    return age < 10, age


def _kodi_log_excerpt(max_lines=400):
    path = kodi.kodi_log_file()
    if not path:
        return ['(kodi.log not found)']
    try:
        with io.open(path, 'rb') as handle:
            head = handle.read(64 * 1024).decode('utf-8', 'replace')
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 4 * 1024 * 1024))
            tail = handle.read().decode('utf-8', 'replace')
    except (IOError, OSError) as exc:
        return ['(could not read kodi.log: %s)' % exc]
    lines = [l for l in head.splitlines()[:400] if _KODILOG_HEAD_MATCH.search(l)][:25]
    matching = [l for l in tail.splitlines() if _KODILOG_MATCH.search(l) and '[Dragoman]' not in l]
    return lines + ['-- subtitle-related lines --'] + matching[-max_lines:]


def build_report(max_chars=SAVE_LIMIT):
    running, age = service_status()
    cfg = Config.load()
    parts = ['Dragoman log report', 'generated: %s' % time.strftime('%Y-%m-%d %H:%M:%S'),
             'service running: %s%s' % ('yes' if running else 'NO',
                                        (' (heartbeat %.0fs ago)' % age) if age is not None else ''),
             'settings: %s' % cfg.summary(), '', '== environment ==']
    parts.extend(envinfo.environment_lines())
    parts += ['', '== kodi.log excerpt ==']
    parts.extend(_kodi_log_excerpt())
    parts += ['', '== translator.log ==']
    header = util.sanitize_text('\n'.join(parts))
    if cfg.api_key:
        header = header.replace(cfg.api_key, '***')
    body = get_log().read_tail(max(20000, max_chars - len(header) - 100)) or '(empty)'
    body = util.sanitize_text(body)
    if cfg.api_key:
        body = body.replace(cfg.api_key, '***')
    return header + '\n' + body + '\n== end ==\n'


def upload(report):
    request = Request(PASTE_URL + 'documents', data=report.encode('utf-8'),
                      headers={'User-Agent': 'Kodi-Dragoman/%s' % kodi.addon_version(),
                               'Content-Type': 'text/plain; charset=utf-8'})
    try:
        body = urlopen(request, timeout=30).read().decode('utf-8', 'replace')
    except HTTPError as exc:
        raise UploadError('HTTP %s %s' % (exc.code, exc.reason))
    except URLError as exc:
        raise UploadError('%s' % (exc.reason,))
    except Exception as exc:
        raise UploadError('%s' % exc)
    try:
        key = json.loads(body).get('key')
    except (ValueError, AttributeError):
        key = None
    if not key:
        raise UploadError('unexpected answer: %s' % util.clip(body, 120))
    return PASTE_URL + key


def save(report, folder):
    name = 'dragoman-log-%s.txt' % time.strftime('%Y%m%d-%H%M%S')
    sep = '\\' if ('\\' in folder and '/' not in folder) else '/'
    target = (folder if folder.endswith(('/', '\\')) else folder + sep) + name
    data = report.encode('utf-8')
    local = kodi.translate_path(target) if target.startswith('special://') else target
    if not util.has_scheme(local):
        with open(local, 'wb') as handle:
            handle.write(data)
        return target
    handle = xbmcvfs.File(target, 'w')
    try:
        try:
            ok = handle.write(bytearray(data))
        except TypeError:
            ok = handle.write(report)
    finally:
        handle.close()
    if ok is False:
        raise IOError('could not write %s' % target)
    return target
