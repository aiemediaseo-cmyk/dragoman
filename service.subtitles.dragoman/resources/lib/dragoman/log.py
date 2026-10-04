# -*- coding: utf-8 -*-
"""Add-on log: every line goes to kodi.log with an [Dragoman] prefix and to
translator.log in the add-on's profile folder (rotated at 2 MB).

Everything is passed through util.sanitize_text first, so links, tokens and
keys never reach either log.
"""
import io
import os
import threading
import time
import traceback

import xbmc

from . import kodi, util

PREFIX = '[Dragoman] '
MAX_BYTES = 2 * 1024 * 1024

LOGDEBUG = getattr(xbmc, 'LOGDEBUG', 0)
LOGINFO = getattr(xbmc, 'LOGINFO', 1)
LOGWARNING = getattr(xbmc, 'LOGWARNING', 2)
LOGERROR = getattr(xbmc, 'LOGERROR', 3)

_TAGS = {LOGWARNING: 'WARN  ', LOGERROR: 'ERROR '}


class ReconLog(object):
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    @staticmethod
    def _stamp():
        now = time.time()
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)) + \
            '.%03d' % int((now % 1) * 1000)

    def write(self, message, level=LOGINFO):
        text = util.sanitize_text('%s' % (message,))
        lines = text.splitlines() or ['']
        for line in lines:
            try:
                xbmc.log(PREFIX + line, level)
            except Exception:
                pass
        if not self.path:
            return
        stamp = self._stamp()
        tag = _TAGS.get(level, '')
        with self._lock:
            try:
                self._rotate()
                with io.open(self.path, 'a', encoding='utf-8') as handle:
                    for line in lines:
                        handle.write('%s %s%s\n' % (stamp, tag, line))
            except (IOError, OSError):
                pass

    debug_enabled = False

    def info(self, message):
        self.write(message, LOGINFO)

    def debug(self, message):
        """Only written when 'Detailed log' is on (kodi.log still gets it at debug level)."""
        if self.debug_enabled:
            self.write(message, LOGINFO)
        else:
            try:
                xbmc.log(PREFIX + util.sanitize_text('%s' % (message,)), LOGDEBUG)
            except Exception:
                pass

    def warn(self, message):
        self.write(message, LOGWARNING)

    def error(self, message):
        self.write(message, LOGERROR)

    def exception(self, message):
        self.write('%s\n%s' % (message, traceback.format_exc()), LOGERROR)

    def _rotate(self):
        try:
            if os.path.getsize(self.path) > MAX_BYTES:
                old = self.path + '.1'
                if os.path.exists(old):
                    os.remove(old)
                os.rename(self.path, old)
        except OSError:
            pass

    def clear(self):
        with self._lock:
            for path in (self.path, self.path + '.1'):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def read_tail(self, max_chars):
        """Newest max_chars characters across recon.log.1 + recon.log."""
        chunks = []
        for path in (self.path + '.1', self.path):
            try:
                with io.open(path, 'r', encoding='utf-8', errors='replace') as handle:
                    chunks.append(handle.read())
            except (IOError, OSError):
                continue
        data = ''.join(chunks)
        if len(data) > max_chars:
            data = data[-max_chars:]
            cut = data.find('\n')
            if 0 <= cut < 500:
                data = data[cut + 1:]
            data = '[... older lines cut ...]\n' + data
        return data


_instance = None


def get_log():
    global _instance
    if _instance is None:
        _instance = ReconLog(os.path.join(kodi.profile_dir(), 'translator.log'))
    return _instance
