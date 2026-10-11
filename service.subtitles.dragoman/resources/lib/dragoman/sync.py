# -*- coding: utf-8 -*-
"""Timing fixes on the Kodi side: which subtitle is showing, writing and loading
re-timed copies, the Sync assistant, Undo, and the entries Dragoman offers in
Kodi's subtitle dialog (Subtitles > Download > Dragoman).

The service and the menu / subtitle dialog run in different Python interpreters.
They share:
  current.json   (profile) the subtitle Dragoman last handled for the playing video:
                 its unmapped text ("base"), language, subtitle hash, video key and
                 the playback session it belongs to
  timing.json    (profile) the stored fixes and the re-timed copies handed to Kodi
                 (timing.FixStore)
  window properties  dragoman.session (the playback session the service is watching;
                 a record from another session is never used), dragoman.ownload (we
                 loaded a subtitle ourselves: not a new subtitle for the service) and
                 dragoman.produced (hashes of re-timed files Kodi copies into its temp
                 folder or next to the video when the subtitle dialog loads them)
"""
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata

import xbmc
import xbmcgui

from . import kodi, langdetect, languages, srt, timing
from .config import Config
from .log import get_log

CURRENT_FILE = 'current.json'
SESSION_PROP = 'dragoman.session'
OWNLOAD_PROP = 'dragoman.ownload'
PRODUCED_PROP = 'dragoman.produced'
TEMP_OUT = 'special://temp/dragoman/'
ORIGINALS = 'originals'
MAX_ORIGINALS = 200
VERIFY_SECONDS = 5.0
LIST_BEFORE_MS = 60 * 1000       # Sync assistant: lines shown from a minute ago ...
LIST_AFTER_MS = 150 * 1000       # ... to two and a half minutes ahead
LIST_MAX = 30


# ---------------------------------------------------------------------- shared state
def _current_path():
    return os.path.join(kodi.profile_dir(), CURRENT_FILE)


def session_token():
    try:
        return kodi.home_window().getProperty(SESSION_PROP) or ''
    except Exception:
        return ''


def set_session_token(token):
    try:
        if token:
            kodi.home_window().setProperty(SESSION_PROP, token)
        else:
            kodi.home_window().clearProperty(SESSION_PROP)
    except Exception:
        pass


def publish_current(record):
    """Written by the service whenever it handles a subtitle for the playing video."""
    record = dict(record)
    record['updated'] = int(time.time())
    path = _current_path()
    try:
        fd, tmp = tempfile.mkstemp(prefix='current.', suffix='.tmp', dir=os.path.dirname(path))
    except (IOError, OSError) as exc:
        get_log().warn('could not save the current subtitle record: %s' % exc)
        return
    try:
        with io.open(fd, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False))
        os.replace(tmp, path)
    except (IOError, OSError) as exc:
        get_log().warn('could not save the current subtitle record: %s' % exc)
        try:
            os.remove(tmp)
        except OSError:
            pass


def load_current():
    try:
        with io.open(_current_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (IOError, OSError, ValueError):
        return None


def current_for_playing(player=None):
    """The record for what is playing now: same playback session (not an earlier play of
    the same video), same file, base text still there."""
    player = player or xbmc.Player()
    try:
        if not player.isPlayingVideo():
            return None
        playing = player.getPlayingFile()
    except Exception:
        return None
    rec = load_current()
    token = session_token()
    if (not rec or not token or rec.get('session') != token or rec.get('file') != playing
            or not os.path.isfile(rec.get('base') or '')):
        return None
    return rec


def fixes():
    return timing.FixStore(kodi.profile_dir())


def key_of(rec):
    return timing.fix_key(rec.get('video', ''), rec.get('md5', ''))


def mark_own_load():
    try:
        kodi.home_window().setProperty(OWNLOAD_PROP, '%.3f' % time.time())
    except Exception:
        pass


def own_load_value():
    try:
        return kodi.home_window().getProperty(OWNLOAD_PROP) or ''
    except Exception:
        return ''


def mark_produced(data):
    md5 = hashlib.md5(data).hexdigest()
    try:
        win = kodi.home_window()
        known = json.loads(win.getProperty(PRODUCED_PROP) or '[]')
        if not isinstance(known, list):
            known = []
    except Exception:
        win, known = None, []
    if md5 not in known:
        known = (known + [md5])[-20:]
    if win is not None:
        try:
            win.setProperty(PRODUCED_PROP, json.dumps(known))
        except Exception:
            pass
    return md5


def is_produced(md5):
    try:
        known = json.loads(kodi.home_window().getProperty(PRODUCED_PROP) or '[]')
    except Exception:
        return False
    return isinstance(known, list) and md5 in known


# ---------------------------------------------------------------------- files
def temp_dir():
    return kodi.translate_path(TEMP_OUT)


def write_subtitle(content, name, job_id=None, folders=None):
    """Write UTF-8 (with BOM) SRT text into a fresh folder; returns the path or ''.
    First choice: Kodi's temp folder (where Kodi itself keeps downloaded subtitles);
    second: the add-on's profile folder."""
    job_id = job_id or '%d-%d' % (int(time.time() * 1000) % 10 ** 9, os.getpid())
    folders = folders or [os.path.join(temp_dir(), job_id),
                          os.path.join(kodi.profile_dir(), 'subtitles', job_id)]
    data = b'\xef\xbb\xbf' + content.encode('utf-8')
    for folder in folders:
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
            path = os.path.join(folder, name)
            with open(path, 'wb') as handle:
                handle.write(data)
            return path
        except (IOError, OSError) as exc:
            get_log().warn('could not write %s to %s: %s' % (name, folder, exc))
    return ''


def _base_name(md5, lang):
    return '%s-%s.srt' % ((md5 or 'subtitle')[:16], languages.file_code(lang or '') or 'und')


def write_base(content, md5, lang):
    """The unmapped text of the subtitle that is showing (what fixes are applied to)."""
    folder = os.path.join(temp_dir(), 'base')
    return write_subtitle(content, _base_name(md5, lang),
                          folders=[folder, os.path.join(kodi.profile_dir(), 'base')])


def keep_original(rec):
    """A lasting copy of the record's unmapped text (the temp copy goes at the next start).
    Kodi's subtitle dialog may store the re-timed file we hand it next to the video, in
    place of the subtitle there; with this copy Dragoman still knows the original timing
    later, so Undo works after a restart too. Returns the path or ''."""
    folder = os.path.join(kodi.profile_dir(), ORIGINALS)
    dest = os.path.join(folder, _base_name(rec.get('md5'), rec.get('lang')))
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
        shutil.copyfile(rec['base'], dest)
        return dest
    except (IOError, OSError, KeyError) as exc:
        get_log().warn('could not keep a copy of the original subtitle: %s' % exc)
        return ''


def prune_originals():
    """At service start: drop lasting copies no re-timed file refers to any more."""
    folder = os.path.join(kodi.profile_dir(), ORIGINALS)
    if not os.path.isdir(folder):
        return 0
    try:
        wanted = set(os.path.normcase(os.path.abspath(p)) for p in fixes().produced_bases() if p)
        names = os.listdir(folder)
    except Exception:
        return 0
    removed = 0
    for name in names:
        path = os.path.join(folder, name)
        if os.path.normcase(os.path.abspath(path)) not in wanted:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


def _is_stream(path):
    return (path or '').lower().startswith(('http://', 'https://'))


def origin_of(md5):
    """For a re-timed file Dragoman handed to Kodi earlier (Kodi may keep it next to the
    video): (info, cues of the subtitle it was made from), or None."""
    info = fixes().produced(md5)
    if not info:
        return None
    try:
        with open(info.get('base') or '', 'rb') as handle:
            raw = handle.read()
    except (IOError, OSError):
        return None
    text, _enc = srt.decode(raw, 'utf-8')
    cues = srt.parse(text)
    return (info, cues) if len(cues) >= 3 else None


def safe_title(text, limit=60):
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'[^A-Za-z0-9 ()_-]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:limit].strip() or 'Subtitle'


def output_name(rec):
    code = languages.file_code(rec.get('lang') or '')
    tag = 'AI' if rec.get('kind') == 'translation' else 'synced'
    return '%s %s.%s.srt' % (safe_title(rec.get('title')), tag, code) if code else \
        '%s %s.srt' % (safe_title(rec.get('title')), tag)


def read_base(rec):
    try:
        with open(rec['base'], 'rb') as handle:
            raw = handle.read()
    except (IOError, OSError, KeyError):
        return []
    text, _enc = srt.decode(raw, 'utf-8')
    return srt.parse(text)


# ---------------------------------------------------------------------- loading into Kodi
def subtitle_snapshot():
    """What Kodi shows before we load a subtitle: (language code, (streams, current index) or None)."""
    try:
        lang = languages.normalize(xbmc.Player().getSubtitles() or '')
    except Exception:
        lang = ''
    return lang, kodi.subtitle_state()


def switched(before, want):
    """Did Kodi switch to the subtitle we just loaded? Kodi opens the file a moment later,
    and when it cannot read it, it stays on the track it had ("Unable to create subtitle
    parser"). A new language shows the switch; for the same language (a re-timed copy)
    the current track number has to change."""
    before_lang, before_state = before
    try:
        current = xbmc.Player().getSubtitles() or ''
    except Exception:
        current = ''
    if want and want != before_lang:
        return languages.normalize(current) == want or current.lower() == want
    state = kodi.subtitle_state()
    if before_state is None or state is None:
        return True          # Kodi does not say which track it is on: nothing better to go on
    return state[1] is not None and state[1] != before_state[1]


def load_into_player(path, lang, wait=None, show=True):
    """setSubtitles + check that Kodi switched to it. Returns True when Kodi shows it."""
    log = get_log()
    before = subtitle_snapshot()
    mark_own_load()
    try:
        player = xbmc.Player()
        player.setSubtitles(path)
        if show:
            try:
                player.showSubtitles(True)
            except Exception:
                pass
    except Exception as exc:
        log.error('could not load the subtitle: %s' % exc)
        return False
    want = languages.file_code(lang or '')
    monitor = xbmc.Monitor()
    waited = 0.0
    while waited < VERIFY_SECONDS:
        if (wait or monitor.waitForAbort)(0.5):
            return False
        waited += 0.5
        if switched(before, want):
            return True
    try:
        current = xbmc.Player().getSubtitles() or ''
    except Exception:
        current = ''
    log.warn('Kodi did not switch to %s after %.0fs (still on %r)' % (os.path.basename(path), waited, current))
    return False


def retimed_file(rec, fix):
    """Write the record's subtitle with the fix applied; returns the path ('' on failure)."""
    cues = read_base(rec)
    if not cues:
        return ''
    content = timing.compose(cues, None, fix) if fix else srt.compose(cues)
    return write_subtitle(content, output_name(rec))


# ---------------------------------------------------------------------- user-facing helpers
def _fmt_shift(ms):
    seconds = abs(ms) / 1000.0
    return ('%.1f' % seconds).rstrip('0').rstrip('.')


def fix_message(k, b, how):
    """What a fix does, for the notification."""
    if how == 'drift' or (how == 'framerate') or abs(k - 1.0) > 1e-6:
        src, dst = _pair(k)
        if src and dst:
            return kodi.text(32446, src=timing.fps_label(src), dst=timing.fps_label(dst))
    if abs(b) < 50:
        return kodi.text(32447)
    return kodi.text(32444 if b > 0 else 32445, s=_fmt_shift(b))


def _pair(k):
    for a in timing.FPS:
        for c in timing.FPS:
            if a != c and abs(a / c - k) < 1e-5:
                return a, c
    return None, None


def _oneline(text, limit=70):
    text = re.sub(r'<[^>]+>|\{[^}]*\}', '', text or '')
    text = ' / '.join(l.strip() for l in text.split('\n') if l.strip())
    return text if len(text) <= limit else text[:limit - 1] + u'…'


def _rel(ms):
    seconds = int(round(ms / 1000.0))
    sign = '+' if seconds >= 0 else '-'
    seconds = abs(seconds)
    return '%s%d:%02d' % (sign, seconds // 60, seconds % 60)


def _paused():
    return kodi.cond('Player.Paused')


# ---------------------------------------------------------------------- Sync assistant
def assistant(rec=None):
    """Interactive: pick a line, press OK when it is heard. Stores the measurement and
    returns (rec, (k, b), how) - rec being the newest record for this subtitle (the
    translation may have arrived meanwhile) - or None when cancelled / not possible."""
    log = get_log()
    dialog = xbmcgui.Dialog()
    player = xbmc.Player()
    rec = rec or current_for_playing(player)
    if rec is None:
        dialog.ok(kodi.addon_name(), kodi.text(32441))
        return None
    cues = read_base(rec)
    if not cues:
        dialog.ok(kodi.addon_name(), kodi.text(32441))
        return None
    showing = ''
    try:
        showing = languages.normalize(player.getSubtitles() or '')
    except Exception:
        pass
    want = languages.normalize(rec.get('lang') or '')
    if showing and want and showing != want and not langdetect.same_group(showing, want):
        if not dialog.yesno(kodi.addon_name(), kodi.text(32442, lang=_lang_name(want), now=_lang_name(showing))):
            return None

    paused_here = False
    if not _paused():
        player.pause()               # read the list in peace (and the times below stay right)
        paused_here = True
    store = fixes()
    key = key_of(rec)
    k, b = store.mapping(key) or (1.0, 0.0)
    try:
        now_ms = player.getTime() * 1000.0
    except Exception:
        now_ms = 0.0
    shown = [(n, timing.map_ms(c.start, k, b)) for n, c in enumerate(cues)]
    window = [(n, t) for n, t in shown if now_ms - LIST_BEFORE_MS <= t <= now_ms + LIST_AFTER_MS
              and _oneline(cues[n].text)]
    if not window:     # far from any line (or the timing is way off): take the nearest lines
        window = sorted(shown, key=lambda nt: abs(nt[1] - now_ms))[:LIST_MAX]
        window.sort(key=lambda nt: nt[1])
    if len(window) > LIST_MAX:
        ahead = [w for w in window if w[1] >= now_ms]
        behind = [w for w in window if w[1] < now_ms]
        window = behind[-(LIST_MAX // 3):] + ahead[:LIST_MAX - min(len(behind), LIST_MAX // 3)]
    labels = ['%s   %s' % (_rel(t - now_ms), _oneline(cues[n].text)) for n, t in window]
    preselect = next((i for i, (n, t) in enumerate(window) if t >= now_ms + 3000), 0)
    try:
        index = dialog.select(kodi.text(32440), labels, preselect=preselect)
    except TypeError:
        index = dialog.select(kodi.text(32440), labels)
    if index < 0:
        if paused_here and _paused():
            player.pause()
        return None
    n, _shown_at = window[index]
    if _paused():
        player.pause()               # play on: the user listens for the line
    heard = dialog.ok(kodi.addon_name(), kodi.text(32443, line=_oneline(cues[n].text, 120)))
    try:
        at_ms = player.getTime() * 1000.0
    except Exception:
        at_ms = None
    if not heard or at_ms is None:
        return None
    cfg = Config.load()
    video_ms = at_ms - cfg.reaction_ms
    new = store.add_point(key, cues[n].start, video_ms, rec.get('title', ''))
    log.info('sync assistant: line %d (subtitle %.2fs) heard at %.2fs -> %s %s' % (
        n + 1, cues[n].start / 1000.0, video_ms / 1000.0, new.get('how'), timing.describe(new['k'], new['b'])))
    latest = current_for_playing(player)
    if latest is not None and key_of(latest) == key and latest.get('base') != rec.get('base'):
        log.info('sync assistant: the %s subtitle arrived meanwhile - re-timing that one' % latest.get('kind'))
        rec = latest
    return rec, (new['k'], new['b']), new.get('how')


def _lang_name(code):
    sid = languages.string_id(code)
    return kodi.text(sid) if sid else (code or '?')


def _done_message(fix, how):
    return fix_message(fix[0], fix[1], how) if fix else kodi.text(32448)


def apply_now(rec, fix, how=None, notify=True):
    """Menu route: write the re-timed copy and load it ourselves."""
    path = retimed_file(rec, fix if timing.is_change(fix) else None)
    if not path:
        kodi.notify(kodi.text(32313), 5000)
        return False
    with open(path, 'rb') as handle:
        mark_produced(handle.read())
    ok = load_into_player(path, rec.get('lang'))
    get_log().info('loaded re-timed subtitle %s (%s)' % (os.path.basename(path), 'ok' if ok else 'Kodi did not switch'))
    if notify:
        if ok:
            kodi.notify(_done_message(fix, how), 6000)
        else:
            kodi.notify(kodi.text(32322), 8000)
    return ok


def run_assistant_from_menu():
    result = assistant()
    if result is None:
        return
    rec, fix, how = result
    apply_now(rec, fix, how)
    if how == 'offset':
        xbmcgui.Dialog().notification(kodi.addon_name(), kodi.text(32449), kodi.addon_info('icon'), 8000, False)
    elif how == 'uneven':
        xbmcgui.Dialog().notification(kodi.addon_name(), kodi.text(32450), kodi.addon_info('icon'), 8000, False)


def undo_from_menu():
    rec = current_for_playing()
    if rec is None:
        xbmcgui.Dialog().ok(kodi.addon_name(), kodi.text(32441))
        return
    if not fixes().clear(key_of(rec)):
        kodi.notify(kodi.text(32451), 4000)
        return
    get_log().info('timing fix removed for %s' % rec.get('title'))
    apply_now(rec, None)


def framerate_from_menu():
    rec = current_for_playing()
    drift = (rec or {}).get('drift') or {}
    if rec is None or not drift.get('ratio'):
        xbmcgui.Dialog().ok(kodi.addon_name(), kodi.text(32452))
        return
    new = fixes().set_ratio(key_of(rec), drift['ratio'], rec.get('title', ''))
    apply_now(rec, (new['k'], new['b']), 'framerate')


# ---------------------------------------------------------------------- Kodi's subtitle dialog
def dialog_items():
    """[(id, text)] for Subtitles > Download > Dragoman, for the subtitle that is showing."""
    rec = current_for_playing()
    if rec is None:
        return rec, []
    items = [('sync', kodi.text(32453))]
    drift = rec.get('drift') or {}
    store = fixes()
    stored = store.get(key_of(rec)) or {}
    active = store.mapping(key_of(rec))
    if drift.get('ratio') and not (active and stored.get('ratio')):
        items.append(('framerate', kodi.text(32454, src=timing.fps_label(drift.get('from_fps')),
                                             dst=timing.fps_label(drift.get('to_fps')))))
    if active:
        items.append(('undo', kodi.text(32455)))
    return rec, items


def dialog_action(action_id):
    """Run a subtitle-dialog entry. Returns the path of the file Kodi should load, or ''."""
    rec = current_for_playing()
    if rec is None:
        xbmcgui.Dialog().ok(kodi.addon_name(), kodi.text(32441))
        return ''
    fix, how = None, None
    if action_id == 'sync':
        result = assistant(rec)
        if result is None:
            return ''
        rec, fix, how = result
    elif action_id == 'framerate':
        drift = rec.get('drift') or {}
        if not drift.get('ratio'):
            return ''
        new = fixes().set_ratio(key_of(rec), drift['ratio'], rec.get('title', ''))
        fix, how = (new['k'], new['b']), 'framerate'
    elif action_id == 'undo':
        fixes().clear(key_of(rec))
    else:
        return ''
    message = _done_message(fix, how)
    if not timing.is_change(fix):
        fix = None
    path = retimed_file(rec, fix)
    if path:
        with open(path, 'rb') as handle:
            md5 = mark_produced(handle.read())
        # Kodi may store the file next to a local video (in place of the subtitle there) and
        # load it again next time; for streams it only lands in Kodi's temp folder
        original = '' if _is_stream(rec.get('file')) else keep_original(rec)
        if original:
            fixes().note_produced(md5, {
                'md5': rec.get('md5', ''), 'video': rec.get('video', ''), 'lang': rec.get('lang', ''),
                'kind': rec.get('kind', ''), 'title': rec.get('title', ''), 'base': original,
                'k': fix[0] if fix else 1.0, 'b': fix[1] if fix else 0.0})
        mark_own_load()
        kodi.notify(message, 6000)
        get_log().info('subtitle dialog: %s -> %s' % (action_id, os.path.basename(path)))
    return path
