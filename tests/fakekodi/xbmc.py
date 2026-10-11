# -*- coding: utf-8 -*-
"""Stand-in for Kodi's xbmc module (only what the add-on uses)."""
import json

from kodistate import STATE

LOGDEBUG = 0
LOGINFO = 1
LOGWARNING = 2
LOGERROR = 3
LOGFATAL = 4


def log(msg, level=LOGDEBUG):
    if not isinstance(msg, str):
        raise TypeError('xbmc.log needs str')
    STATE.kodi_log.append((level, msg))


def getInfoLabel(label):
    return STATE.labels.get(label, '')


def getCondVisibility(expression):
    if expression == 'Player.Paused':
        return STATE.playing and STATE.paused
    if expression == 'VideoPlayer.SubtitlesEnabled' and expression not in STATE.conds:
        return STATE.playing and STATE.subs_visible
    return STATE.conds.get(expression, False)


def executebuiltin(command, wait=False):
    STATE.builtins.append(command)
    if command.startswith('NotifyAll(') and command.endswith(')'):
        sender, _sep, message = command[len('NotifyAll('):-1].partition(',')
        for monitor in list(STATE.monitors):
            monitor.onNotification(sender.strip(), 'Other.' + message.strip(), '')


def getSkinDir():
    return 'skin.estuary'


def _player_properties(params):
    """Player.GetProperties for the video player (subtitle tracks only)."""
    if not STATE.playing or params.get('playerid') != 1:
        return {'__error__': {'code': -32100, 'message': 'Failed to execute method.'}}
    tracks = [{'index': n, 'language': name, 'name': name} for n, name in enumerate(STATE.subs)]
    cur = STATE.cur_index
    current = tracks[cur] if cur is not None and 0 <= cur < len(tracks) else None
    return {'subtitles': tracks, 'currentsubtitle': current, 'subtitleenabled': STATE.subs_visible}


_BUILTIN_JSONRPC = {'Player.GetProperties': _player_properties}


def executeJSONRPC(request):
    req = json.loads(request)
    handler = STATE.jsonrpc.get(req['method'], _BUILTIN_JSONRPC.get(req['method']))
    if handler is None:
        return json.dumps({'id': 1, 'jsonrpc': '2.0',
                           'error': {'code': -32601, 'message': 'Method not found.'}})
    result = handler(req.get('params', {})) if callable(handler) else handler
    if isinstance(result, dict) and '__error__' in result:
        return json.dumps({'id': 1, 'jsonrpc': '2.0', 'error': result['__error__']})
    return json.dumps({'id': 1, 'jsonrpc': '2.0', 'result': result})


class Actor(object):
    def __init__(self, name='', role='', order=-1, thumbnail=''):
        self._v = (name, role, order, thumbnail)

    def getName(self):
        return self._v[0]

    def getRole(self):
        return self._v[1]

    def getOrder(self):
        return self._v[2]

    def getThumbnail(self):
        return self._v[3]


class InfoTagVideo(object):
    def __init__(self, meta):
        self._m = meta

    def getActors(self):
        # Kodi 20+; meta 'cast_text' simulates Kodi 19, which only has getCast()
        if 'cast_text' in self._m:
            raise AttributeError('getActors')
        return [Actor(*a) for a in self._m.get('actors', [])]

    def getCast(self):
        return self._m.get('cast_text', '')

    def getTitle(self):
        return self._m.get('title', '')

    def getOriginalTitle(self):
        return self._m.get('original', '')

    def getTVShowTitle(self):
        return self._m.get('show', '')

    def getSeason(self):
        return self._m.get('season', -1)

    def getEpisode(self):
        return self._m.get('episode', -1)

    def getYear(self):
        return self._m.get('year', 0)

    def getIMDBNumber(self):
        return self._m.get('imdb', '')

    def getMediaType(self):
        return self._m.get('type', '')

    def getPlot(self):
        return self._m.get('plot', '')


class Player(object):
    def __init__(self):
        STATE.players.append(self)

    def _need(self):
        if not STATE.playing:
            raise RuntimeError('Kodi is not playing any media file')

    def isPlaying(self):
        return STATE.playing

    def isPlayingVideo(self):
        return STATE.playing

    def getPlayingFile(self):
        self._need()
        return STATE.file

    def getTime(self):
        self._need()
        return STATE.time

    def getTotalTime(self):
        self._need()
        return STATE.total

    def getSubtitles(self):
        self._need()
        return STATE.cur_sub

    def getAvailableSubtitleStreams(self):
        self._need()
        return list(STATE.subs)

    def getAvailableAudioStreams(self):
        self._need()
        return ['English']

    def getVideoInfoTag(self):
        self._need()
        return InfoTagVideo(STATE.meta)

    def setSubtitles(self, path):
        """Like Kodi: take the language from the file name and switch to the new
        track - unless the test says Kodi cannot open this path (then it stays on
        the previous track, as after 'Unable to create subtitle parser')."""
        STATE.loaded_subs.append(path)
        code = path.rsplit('.', 2)[-2] if path.count('.') >= 2 else ''
        lang = {'sl': 'slv', 'bs': 'bos', 'de': 'ger', 'hr': 'hrv', 'sr': 'srp', 'en': 'eng'}.get(code, code)
        STATE.subs.append(lang)                 # Kodi lists the new track either way
        if STATE.reject_subs is not None and STATE.reject_subs(path):
            return
        STATE.cur_sub = lang
        STATE.cur_index = len(STATE.subs) - 1
        STATE.subs_visible = True               # like Kodi: adding a subtitle file shows subtitles

    def showSubtitles(self, visible):
        STATE.show_calls.append(visible)
        STATE.subs_visible = visible

    def pause(self):
        """Like Kodi's Player.pause(): toggles."""
        self._need()
        STATE.paused = not STATE.paused


class Monitor(object):
    def __init__(self):
        STATE.monitors.append(self)

    def abortRequested(self):
        return STATE.abort

    def waitForAbort(self, timeout=0):
        STATE.on_wait(timeout)
        return STATE.abort
