# -*- coding: utf-8 -*-
"""Moving over from the add-on's earlier name.

Up to v0.1.3 this add-on was "AI Subtitle Translator" with the id
service.subtitles.aitranslator. Kodi treats a new id as a different add-on,
so on the first start Dragoman copies what the user already set up there:
the API key, the languages to leave alone and the settings. The old add-on's
own files are only read, never changed.

If the old add-on is still switched on, both would translate the same
subtitle, so Dragoman offers to switch it off (Switch off / Keep both /
Ask later; only "Keep both" stops the question for good).
"""
import io
import json
import os
import xml.etree.ElementTree as ET

import xbmcgui

from . import kodi, state
from .log import get_log

OLD_ID = 'service.subtitles.aitranslator'
OLD_NAME = 'AI Subtitle Translator'
OLD_TEMP = 'special://temp/aisubs/'

# Settings with the same meaning (and the same value lists) in both versions.
SETTING_IDS = ('target_language', 'src_manual', 'src_auto', 'src_local', 'mode', 'model',
               'custom_model', 'fallback_models', 'chunk_size', 'rpm', 'cache_days',
               'show_progress', 'notify_skips', 'debug_log')

ANSWER_LATER, ANSWER_NO, ANSWER_YES = -1, 0, 1


def old_profile_dir():
    return kodi.translate_path('special://profile/addon_data/%s/' % OLD_ID)


def old_temp_dir():
    return kodi.translate_path(OLD_TEMP)


def read_settings_xml(path):
    """{id: value} for the values the user changed (Kodi marks untouched ones default="true").
    Reads both the Kodi 18+ format (<setting id="x">v</setting>) and the old one (value="v")."""
    values = {}
    try:
        root = ET.parse(path).getroot()
    except (IOError, OSError, ET.ParseError):
        return values
    for node in root.iter('setting'):
        key = node.get('id')
        if not key or node.get('default') == 'true':
            continue
        value = node.get('value')
        if value is None:
            value = node.text or ''
        values[key] = value
    return values


def _read_json(path):
    try:
        with io.open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def import_old_setup():
    """Copy the old add-on's key, languages and settings once. Returns what was copied.
    Settings are copied only into a fresh install, so nothing set in Dragoman is overwritten."""
    log = get_log()
    current = state.load()
    if current.get('migrated'):
        return []
    fresh = not current
    folder = old_profile_dir()
    copied = []
    updates = {'migrated': True}
    if folder and os.path.isdir(folder):
        old_state = _read_json(os.path.join(folder, state.FILE_NAME))
        old_settings = read_settings_xml(os.path.join(folder, 'settings.xml'))
        key = ('%s' % (old_state.get('api_key') or old_settings.get('api_key') or '')).strip()
        if key and not current.get('api_key'):
            updates['api_key'] = key
            copied.append('API key')
        if 'leave_alone' not in current:
            if isinstance(old_state.get('leave_alone'), list):
                updates['leave_alone'] = old_state['leave_alone']
                copied.append('languages to leave alone')
            elif old_settings.get('leave_alone'):          # v0.1.0 kept them in settings.xml
                updates['leave_alone'] = [c for c in old_settings['leave_alone'].split(',') if c]
                copied.append('languages to leave alone')
        if old_state.get('setup_done') or key:
            updates['setup_done'] = True
        if fresh:
            moved = 0
            for setting_id in SETTING_IDS:
                if setting_id in old_settings and kodi.set_setting(setting_id, old_settings[setting_id]):
                    moved += 1
            if moved:
                copied.append('%d settings' % moved)
    state.update(**updates)
    if copied:
        log.info('copied from %s: %s' % (OLD_ID, ', '.join(copied)))
    return copied


def old_addon_enabled():
    return kodi.cond('System.AddonIsEnabled(%s)' % OLD_ID)


def declined():
    return bool(state.get('old_addon_declined'))


def _ask():
    """ANSWER_YES (switch off), ANSWER_NO (keep both, don't ask again) or ANSWER_LATER."""
    dialog = xbmcgui.Dialog()
    message = kodi.text(32270, old=OLD_NAME)
    try:      # Kodi 19+: a third button, and Back is told apart from "No"
        answer = dialog.yesnocustom(kodi.addon_name(), message, kodi.text(32290),
                                    nolabel=kodi.text(32289), yeslabel=kodi.text(32288))
    except (AttributeError, TypeError):
        return ANSWER_YES if dialog.yesno(kodi.addon_name(), message) else ANSWER_LATER
    if answer == 1:
        return ANSWER_YES
    if answer == 0:
        return ANSWER_NO
    return ANSWER_LATER


def offer_to_disable_old():
    """Ask to switch the old add-on off (unless the user chose to keep both).
    Returns True when it is off afterwards."""
    if not old_addon_enabled():
        return True
    if declined():
        return False
    answer = _ask()
    if answer == ANSWER_NO:
        state.update(old_addon_declined=True)
        get_log().info('user keeps %s switched on' % OLD_ID)
        return False
    if answer == ANSWER_LATER:
        return False
    _result, error = kodi.jsonrpc('Addons.SetAddonEnabled', {'addonid': OLD_ID, 'enabled': False})
    if error:
        get_log().warn('could not switch off %s: %s' % (OLD_ID, error))
        xbmcgui.Dialog().ok(kodi.addon_name(), kodi.text(32271, old=OLD_NAME))
        return False
    get_log().info('switched off %s' % OLD_ID)
    kodi.notify(kodi.text(32272, old=OLD_NAME), 4000)
    return True
