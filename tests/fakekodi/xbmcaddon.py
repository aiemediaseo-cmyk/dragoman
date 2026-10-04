# -*- coding: utf-8 -*-
"""Stand-in for Kodi's xbmcaddon module. Reads real strings.po from the add-on."""
import io
import os
import re

from kodistate import STATE

_STRINGS = {}


def _load_strings():
    if _STRINGS:
        return
    po = os.path.join(STATE.addon_dir, 'resources', 'language', 'resource.language.en_gb', 'strings.po')
    with io.open(po, encoding='utf-8') as handle:
        content = handle.read()
    for m in re.finditer(r'msgctxt "#(\d+)"\s*\nmsgid "((?:[^"\\]|\\.)*)"', content):
        _STRINGS[int(m.group(1))] = m.group(2).replace('\\"', '"')


class Addon(object):
    def __init__(self, addon_id=None):
        self._id = addon_id or 'service.subtitles.dragoman'

    def getSetting(self, key):
        return STATE.settings.get(key, '')

    def setSetting(self, key, value):
        if not isinstance(value, str):
            raise TypeError('setSetting needs str')
        STATE.settings[key] = value

    def getAddonInfo(self, key):
        return {
            'id': self._id,
            'name': 'Dragoman',
            'version': '0.1.0',
            'profile': 'special://profile/addon_data/%s/' % self._id,
            'path': STATE.addon_dir,
            'icon': os.path.join(STATE.addon_dir, 'resources', 'icon.png'),
        }.get(key, '')

    def getLocalizedString(self, string_id):
        _load_strings()
        if string_id not in _STRINGS:
            raise AssertionError('string id %d missing from strings.po' % string_id)
        return _STRINGS[string_id]

    def openSettings(self):
        STATE.dialog_calls.append(('openSettings',))
