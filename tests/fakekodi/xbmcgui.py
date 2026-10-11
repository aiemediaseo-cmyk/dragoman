# -*- coding: utf-8 -*-
"""Stand-in for Kodi's xbmcgui module."""
from kodistate import STATE

INPUT_ALPHANUM = 0
ALPHANUM_HIDE_INPUT = 2
NOTIFICATION_INFO = 'info'


def getCurrentWindowDialogId():
    return STATE.dialog


def getCurrentWindowId():
    return STATE.window


class Window(object):
    def __init__(self, window_id=-1):
        self._props = STATE.window_props.setdefault(window_id, {})

    def setProperty(self, key, value):
        if not isinstance(value, str):
            raise TypeError('property value must be str')
        self._props[key.lower()] = value

    def getProperty(self, key):
        return self._props.get(key.lower(), '')

    def clearProperty(self, key):
        self._props.pop(key.lower(), None)


class Dialog(object):
    def notification(self, heading, message, icon=NOTIFICATION_INFO, time=5000, sound=True):
        STATE.dialog_calls.append(('notification', message))

    def ok(self, heading, message):
        STATE.dialog_calls.append(('ok', message))
        return True

    def yesno(self, heading, message, nolabel='', yeslabel='', autoclose=0, *args, **kwargs):
        STATE.dialog_calls.append(('yesno', message))
        STATE.yesno_labels.append((nolabel, yeslabel))
        if STATE.yesno_answers:
            return STATE.yesno_answers.pop(0)
        return STATE.yesno_answer

    def yesnocustom(self, heading, message, customlabel, nolabel='', yeslabel='', autoclose=0, defaultbutton=0):
        STATE.dialog_calls.append(('yesnocustom', message))
        STATE.yesno_labels.append((nolabel, yeslabel, customlabel))
        return STATE.yesnocustom_answers.pop(0) if STATE.yesnocustom_answers else -1

    def select(self, heading, items, *args, **kwargs):
        STATE.dialog_calls.append(('select', heading, list(items)))
        return STATE.select_answers.pop(0) if STATE.select_answers else -1

    def multiselect(self, heading, options, autoclose=0, preselect=None, useDetails=False):
        STATE.dialog_calls.append(('multiselect', heading, list(options), preselect))
        return STATE.multiselect_answer

    def browse(self, kind, heading, shares, *args, **kwargs):
        STATE.dialog_calls.append(('browse', kind, heading))
        return STATE.browse_answer

    def input(self, heading, defaultt='', type=INPUT_ALPHANUM, option=0, autoclose=0):
        STATE.dialog_calls.append(('input', heading))
        STATE.inputs.append({'heading': heading, 'defaultt': defaultt, 'type': type, 'option': option})
        if STATE.input_answers:
            return STATE.input_answers.pop(0)
        return STATE.input_answer

    def textviewer(self, heading, text, usemono=False):
        STATE.dialog_calls.append(('textviewer', text))


class DialogProgress(object):
    def create(self, heading, message=''):
        STATE.dialog_calls.append(('progress', message))

    def update(self, percent, message=''):
        pass

    def iscanceled(self):
        return False

    def close(self):
        pass


class DialogProgressBG(object):
    def create(self, heading, message=''):
        STATE.progress_bg.append(('create', message))

    def update(self, percent=0, heading=None, message=None):
        STATE.progress_bg.append(('update', percent, message))

    def close(self):
        STATE.progress_bg.append(('close',))

    def isFinished(self):
        return False


class ListItem(object):
    def __init__(self, label='', label2='', path='', offscreen=False):
        self._label, self._label2, self._path = label, label2, path
        self.art = {}
        self.props = {}

    def getLabel(self):
        return self._label

    def getLabel2(self):
        return self._label2

    def setArt(self, art):
        self.art.update(art)

    def setProperty(self, key, value):
        self.props[key] = value

    def getProperty(self, key):
        return self.props.get(key, '')
