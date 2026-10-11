# -*- coding: utf-8 -*-
"""Stand-in for Kodi's xbmcplugin module."""
from kodistate import STATE


def addDirectoryItem(handle, url, listitem, isFolder=False, totalItems=0):
    STATE.plugin_items.append((handle, url, listitem))
    return True


def endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=True):
    STATE.plugin_ended.append((handle, succeeded))
