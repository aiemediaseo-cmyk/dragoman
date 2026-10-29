# -*- coding: utf-8 -*-
"""Saved translations, keyed by source text + target + model + prompt version.

Finished chunks are saved as they complete, so an interrupted translation
resumes where it stopped. No Kodi imports.
"""
import hashlib
import io
import json
import os
import time

from .translator import PROMPT_VERSION


class Cache(object):
    def __init__(self, folder):
        self.folder = folder
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except OSError:
                pass

    @staticmethod
    def key(texts, target, model, chunk_size, speakers=False):
        digest = hashlib.sha1()
        # with the speaker pass on, a translation made without it is not reused (and back)
        version = PROMPT_VERSION + ('s' if speakers else '')
        digest.update(('%s|%s|%s|%d|' % (version, target, model, chunk_size)).encode('utf-8'))
        for text in texts:
            digest.update(text.encode('utf-8'))
            digest.update(b'\x00')
        return digest.hexdigest()[:24]

    def _path(self, key):
        return os.path.join(self.folder, key + '.json')

    def load(self, key):
        try:
            with io.open(self._path(key), 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (IOError, OSError, ValueError):
            return {}

    def save(self, key, data):
        path = self._path(key)
        tmp = path + '.tmp'
        try:
            with io.open(tmp, 'w', encoding='utf-8') as handle:
                handle.write(json.dumps(data, ensure_ascii=False))
            os.replace(tmp, path)
        except (IOError, OSError):
            pass

    def purge(self, days):
        """Delete saved translations older than `days` (0 = keep nothing). Returns count."""
        removed = 0
        limit = time.time() - days * 86400
        try:
            names = os.listdir(self.folder)
        except OSError:
            return 0
        for name in names:
            path = os.path.join(self.folder, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < limit:
                    os.remove(path)
                    removed += 1
            except OSError:
                continue
        return removed

    def clear(self):
        return self.purge(-1)
