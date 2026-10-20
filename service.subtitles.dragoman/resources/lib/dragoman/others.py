# -*- coding: utf-8 -*-
"""Other add-ons whose subtitles Dragoman leaves alone (no Kodi imports).

Some add-ons translate subtitles themselves. Subterra (script.subterra) is one: it
extracts the film's English track to special://temp/subterra_eng.srt and writes its
translation, a few lines at a time, to special://temp/subterra_active.srt. Dragoman
watches special://temp, so without this it would translate Subterra's English source
or its half-finished translation, load the result and fight Subterra for the screen.

The list of add-ons to ignore is chosen with a button in the settings and saved in
state.json; until the user changes it, the known translator add-ons below are ignored.
"""
import fnmatch
import re

# add-on id -> (name, file name patterns it writes outside its own add-on folder)
KNOWN = {
    'script.subterra': ('Subterra', ('subterra_*', 'subterra.*')),
}
DEFAULT_IGNORED = ['script.subterra']

_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]*$')


def clean_ids(values):
    """Lower-case add-on ids, without blanks or duplicates, in their order."""
    out = []
    for value in values or []:
        v = ('%s' % value).strip().lower()
        if v and _ID_RE.match(v) and v not in out:
            out.append(v)
    return out


def display_name(addon_id):
    known = KNOWN.get(addon_id)
    return known[0] if known else addon_id


def name_patterns(addon_id):
    """File names an add-on's subtitles go by outside its own add-on folder."""
    known = KNOWN.get(addon_id)
    if known:
        return known[1]
    short = addon_id.rsplit('.', 1)[-1]
    if len(short) < 5:              # too short to say anything about a file name
        return ()
    return (short + '_*', short + '.*', short + '-*')


def owner(path, name, ignored):
    """The ignored add-on a subtitle file comes from, or ''.

    It comes from an add-on when it sits in that add-on's folder (addon_data or addons)
    or carries one of the file names the add-on uses (see KNOWN)."""
    if not ignored:
        return ''
    where = ('/%s/' % (path or '').replace('\\', '/').strip('/')).lower()
    low = (name or '').lower()
    for addon_id in ignored:
        if '/addon_data/%s/' % addon_id in where or '/addons/%s/' % addon_id in where:
            return addon_id
        for pattern in name_patterns(addon_id):
            if fnmatch.fnmatchcase(low, pattern):
                return addon_id
    return ''
