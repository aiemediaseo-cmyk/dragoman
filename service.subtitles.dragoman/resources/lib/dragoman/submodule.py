# -*- coding: utf-8 -*-
"""Dragoman in Kodi's subtitle dialog (Subtitles > Download > Dragoman).

Kodi calls this as a subtitle service: action=search lists what Dragoman can do
for the subtitle that is showing (Sync assistant, frame-rate fix, undo); when one
is picked, action=download runs it and hands Kodi the re-timed file, which Kodi
then loads and the dialog closes. The video keeps playing during the assistant.
"""
from urllib.parse import parse_qsl, quote_plus

import xbmcgui
import xbmcplugin

from . import kodi, languages, sync
from .log import get_log


def _params(argv):
    query = argv[2] if len(argv) > 2 else ''
    return dict(parse_qsl(query.lstrip('?')))


def _language_label(code):
    # Kodi names the file it is handed after this label (ConvertToISO6391): use a name it knows
    return languages.kodi_name(code) or 'Unknown'


def run(argv):
    log = get_log()
    try:
        handle = int(argv[1])
    except (IndexError, ValueError):
        handle = -1
    params = _params(argv)
    action = params.get('action', '')
    try:
        if action in ('search', 'manualsearch'):
            rec, items = sync.dialog_items()
            lang = (rec or {}).get('lang', '')
            for item_id, text in items:
                li = xbmcgui.ListItem(label=_language_label(lang), label2=text)
                li.setArt({'icon': '5', 'thumb': languages.file_code(lang)})
                li.setProperty('sync', 'true')
                url = 'plugin://%s/?action=download&id=%s' % (kodi.ADDON_ID, quote_plus(item_id))
                xbmcplugin.addDirectoryItem(handle=handle, url=url, listitem=li, isFolder=False)
            log.info('subtitle dialog: %d entries offered' % len(items))
        elif action == 'download':
            path = sync.dialog_action(params.get('id', ''))
            if path:
                li = xbmcgui.ListItem(label=path)
                xbmcplugin.addDirectoryItem(handle=handle, url=path, listitem=li, isFolder=False)
    except Exception:
        log.exception('subtitle dialog action %r failed' % action)
    if handle >= 0:
        xbmcplugin.endOfDirectory(handle)
