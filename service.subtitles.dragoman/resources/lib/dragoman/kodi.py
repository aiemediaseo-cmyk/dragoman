# -*- coding: utf-8 -*-
"""Thin, defensive wrappers around the Kodi Python API (Kodi 19+)."""
import json
import os
import re

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'service.subtitles.dragoman'
HOME_WINDOW_ID = 10000
HEARTBEAT_PROP = 'dragoman.heartbeat'
NO_DIALOG = 9999  # WINDOW_INVALID, returned when no dialog is open

_addon = None


def addon(refresh=False):
    global _addon
    if _addon is None or refresh:
        _addon = xbmcaddon.Addon(ADDON_ID)
    return _addon


def addon_info(key):
    try:
        return addon().getAddonInfo(key)
    except Exception:
        return ''


def addon_name():
    return addon_info('name') or 'Dragoman'


def addon_version():
    return addon_info('version') or '?'


def translate_path(path):
    """xbmcvfs.translatePath (Kodi 19+) with a fallback for old builds."""
    fn = getattr(xbmcvfs, 'translatePath', None)
    if fn is None:
        fn = getattr(xbmc, 'translatePath')
    try:
        return fn(path)
    except Exception:
        return ''


def profile_dir():
    path = translate_path(addon_info('profile') or
                          'special://profile/addon_data/%s/' % ADDON_ID)
    if path and not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError:
            xbmcvfs.mkdirs(path)
    return path


def text(string_id, **kwargs):
    """Localized string from strings.po, with optional {placeholders}."""
    try:
        s = addon().getLocalizedString(string_id)
    except Exception:
        s = ''
    if not s:
        s = '#%d' % string_id
    if kwargs:
        try:
            s = s.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return s


def setting(key, default=''):
    try:
        value = addon().getSetting(key)
    except Exception:
        return default
    return value if value != '' else default


def setting_int(key, default, lo=None, hi=None):
    try:
        value = int(float(setting(key, default)))
    except (TypeError, ValueError):
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def set_setting(key, value):
    try:
        addon().setSetting(key, value)
        return True
    except Exception:
        return False


def setting_bool(key, default):
    value = setting(key, '')
    if value == '':
        return default
    return value.lower() == 'true'


def info(label):
    try:
        return xbmc.getInfoLabel(label)
    except Exception:
        return ''


def cond(expression):
    try:
        return bool(xbmc.getCondVisibility(expression))
    except Exception:
        return False


def jsonrpc(method, params=None):
    """Return (result, error). error is None on success."""
    request = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params is not None:
        request['params'] = params
    try:
        raw = xbmc.executeJSONRPC(json.dumps(request))
    except Exception as exc:
        return None, 'executeJSONRPC failed: %s' % exc
    try:
        response = json.loads(raw)
    except ValueError:
        return None, 'invalid JSON: %s' % raw[:200]
    if 'error' in response:
        err = response['error']
        if isinstance(err, dict):
            detail = err.get('data', {})
            if isinstance(detail, dict) and detail.get('message'):
                return None, '%s (%s)' % (err.get('message', 'error'), detail.get('message'))
            return None, err.get('message', 'error')
        return None, '%s' % err
    return response.get('result'), None


def subtitle_state():
    """(number of subtitle tracks, index of the current one or None) of the video player,
    or None when Kodi does not say (JSON-RPC: the video player is always player 1)."""
    result, err = jsonrpc('Player.GetProperties', {'playerid': 1, 'properties': ['subtitles', 'currentsubtitle']})
    if err or not isinstance(result, dict) or not isinstance(result.get('subtitles'), list):
        return None
    current = result.get('currentsubtitle')
    index = current.get('index') if isinstance(current, dict) else None
    return len(result['subtitles']), (index if isinstance(index, int) else None)


def build_version():
    return info('System.BuildVersion')


def kodi_major():
    m = re.match(r'(\d+)', build_version() or '')
    return int(m.group(1)) if m else 0


def notify(message, time_ms=4000):
    try:
        xbmcgui.Dialog().notification(addon_name(), message, addon_info('icon'), time_ms, False)
    except Exception:
        pass


def home_window():
    return xbmcgui.Window(HOME_WINDOW_ID)


def dialog_id():
    try:
        return xbmcgui.getCurrentWindowDialogId()
    except Exception:
        return NO_DIALOG


def window_id():
    try:
        return xbmcgui.getCurrentWindowId()
    except Exception:
        return 0


def window_xml(win_id):
    """Skin XML file of a window, e.g. DialogSubtitles.xml - identifies dialogs reliably."""
    if not win_id or win_id == NO_DIALOG:
        return ''
    return info('Window(%d).Property(xmlfile)' % win_id)


def kodi_log_file():
    """Path of kodi.log on this device, or ''."""
    for candidate in ('special://logpath/kodi.log', 'special://home/kodi.log',
                      'special://temp/kodi.log'):
        path = translate_path(candidate)
        if path and not path.startswith('special://') and os.path.isfile(path):
            return path
    return ''
