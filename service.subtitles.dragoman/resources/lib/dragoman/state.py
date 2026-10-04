# -*- coding: utf-8 -*-
"""Values managed by the add-on's own buttons: the API key, the languages to
leave alone and the first-run flag.

They live in state.json in the add-on's profile folder, NOT in settings.xml.
Kodi's settings dialog keeps its own copy of settings.xml and writes it back
when it closes, which silently undid anything a script had saved in the
meantime (seen on Kodi 21: an imported key vanished seconds later).
"""
import io
import json
import os
import tempfile

from . import kodi

FILE_NAME = 'state.json'


def _path():
    return os.path.join(kodi.profile_dir(), FILE_NAME)


def load():
    try:
        with io.open(_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def get(key, default=None):
    return load().get(key, default)


def update(**values):
    """Merge values into state.json (atomic replace, safe across the service and menu)."""
    data = load()
    data.update(values)
    path = _path()
    # a temp file of its own per write: the service and the menu may write at the same moment
    fd, tmp = tempfile.mkstemp(prefix='state.', suffix='.tmp', dir=os.path.dirname(path))
    try:
        with io.open(fd, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(data, ensure_ascii=False))
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return data
