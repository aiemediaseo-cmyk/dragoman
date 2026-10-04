# -*- coding: utf-8 -*-
"""Device, Kodi and settings facts for the log and diagnostics."""
import os
import sys

import xbmc

from . import kodi, util

# Subtitle-related Kodi settings. Some ids may not exist on every version;
# the recon logs the error instead, which is exactly what we want to learn.
KODI_SETTINGS = (
    'locale.subtitlelanguage',
    'locale.language',
    'subtitles.languages',
    'subtitles.storagemode',
    'subtitles.custompath',
    'subtitles.tv',
    'subtitles.movie',
    'subtitles.pauseonsearch',
    'subtitles.downloadfirst',
    'subtitles.charset',
    'videoplayer.adjustrefreshrate',
)

_PLATFORMS = (
    ('System.Platform.Android', 'android'),
    ('System.Platform.Linux.RaspberryPi', 'raspberrypi'),
    ('System.Platform.Linux', 'linux'),
    ('System.Platform.UWP', 'uwp'),
    ('System.Platform.Windows', 'windows'),
    ('System.Platform.IOS', 'ios'),
    ('System.Platform.OSX', 'macos'),
    ('System.Platform.Darwin', 'darwin'),
)


def platform_names():
    for condition, name in _PLATFORMS:     # first match wins (Android also reports Linux)
        if kodi.cond(condition):
            return [name]
    return ['unknown']


def kodi_setting(setting_id):
    """(value, error) for one Kodi setting via JSON-RPC."""
    result, err = kodi.jsonrpc('Settings.GetSettingValue', {'setting': setting_id})
    if err:
        return None, err
    if isinstance(result, dict):
        return result.get('value'), None
    return result, None


def kodi_settings_lines():
    lines = []
    for sid in KODI_SETTINGS:
        value, err = kodi_setting(sid)
        if err:
            lines.append('%s = <error: %s>' % (sid, util.clip(err, 120)))
        else:
            lines.append('%s = %r' % (sid, value))
    return lines


def subtitle_addons():
    """Installed subtitle service add-ons as a list of dicts."""
    result, err = kodi.jsonrpc('Addons.GetAddons', {
        'type': 'xbmc.subtitle.module', 'enabled': 'all',
        'properties': ['name', 'version', 'enabled']})
    if err or not isinstance(result, dict):
        return [], err
    return result.get('addons') or [], None


def subtitle_addon_lines():
    addons, err = subtitle_addons()
    if err:
        return ['<error: %s>' % err]
    if not addons:
        return ['(none installed)']
    return ['%s %s (%s)%s' % (a.get('addonid'), a.get('version', '?'), a.get('name', ''),
                              '' if a.get('enabled', True) else ' DISABLED')
            for a in addons]


def ssl_lines():
    try:
        import ssl
        paths = ssl.get_default_verify_paths()
        return ['%s' % ssl.OPENSSL_VERSION,
                'cafile=%s capath=%s' % (paths.cafile, paths.capath),
                'SSL_CERT_FILE=%s' % os.environ.get('SSL_CERT_FILE', '(not set)')]
    except Exception as exc:
        return ['ssl module problem: %s' % exc]


def environment_lines():
    lines = [
        'kodi: %s' % kodi.build_version(),
        'python: %s' % sys.version.replace('\n', ' '),
        'platform: %s | os: %s' % (', '.join(platform_names()),
                                   kodi.info('System.OSVersionInfo') or '?'),
        'skin: %s | ui language: %s' % (xbmc.getSkinDir(), kodi.info('System.Language')),
        'add-on: %s %s' % (kodi.ADDON_ID, kodi.addon_version()),
        'paths: temp=%s | profile=%s | kodi.log=%s' % (
            kodi.translate_path('special://temp/'), kodi.profile_dir(),
            kodi.kodi_log_file() or '(not found)'),
    ]
    lines.append('kodi subtitle settings:')
    lines.extend('  ' + line for line in kodi_settings_lines())
    lines.append('subtitle add-ons:')
    lines.extend('  ' + line for line in subtitle_addon_lines())
    lines.append('ssl:')
    lines.extend('  ' + line for line in ssl_lines())
    return lines
