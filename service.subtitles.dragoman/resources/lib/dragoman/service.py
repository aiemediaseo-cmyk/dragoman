# -*- coding: utf-8 -*-
"""Background service: notices new subtitles during video playback and hands
them to a TranslationJob.

Detection (same approach the Recon tool logs): watch Kodi's temp folder, the
custom subtitle folder, subtitle add-on folders and the video's own folder;
check every 2 s and on every onAVChange. A subtitle that appears within 30 s
of the subtitle download dialog counts as a manual pick; one in the video's
folder otherwise counts as local; anything else as an automatic download.

Safety net: when Kodi adds a subtitle stream but no new file was noticed
within a few seconds, the newest recently modified subtitle file in the
watched folders is used, and the log says what the folders looked like.
"""
import hashlib
import os
import shutil
import threading
import time

import xbmc

from . import envinfo, kodi, langdetect, languages, migrate, scanner, srt, util
from .cache import Cache
from .config import MODE_OFF, Config
from .job import Candidate, TranslationJob, output_temp_dir
from .log import get_log

TICK = 0.5
SCAN_EVERY = 4                 # ticks -> 2 s
SUBDLG_COND = 'Window.IsActive(subtitlesearch)'
MANUAL_WINDOW = 30.0
DUPLICATE_WINDOW = 20.0
EXPECT_SECONDS = 6.0           # after a new subtitle stream, wait this long for a file
RECENT_FILE_SECONDS = 180.0    # safety net: files modified this recently count as new
OWN_LOAD_QUIET = 10.0          # ignore stream changes we caused ourselves
BUSY_TICK_SECONDS = 3.0
HEARTBEAT_TICKS = 60           # detailed log: folder summary every 30 s
TRANSLATABLE = ('srt', 'vtt')
REQUEST_PROP = 'dragoman.request'
OLD_ADDON_CHECK_TICK = 30      # ~15 s after start, when the home screen is up


def classify_source(root_kind, since_subdlg):
    if since_subdlg is not None and since_subdlg <= MANUAL_WINDOW:
        return 'manual'
    if root_kind == 'video':
        return 'local'
    return 'auto'


def _try(fn, default=None, *args):
    try:
        return fn(*args)
    except Exception:
        return default


class Session(object):
    def __init__(self, number, started, playing_file):
        self.id = number
        self.started = started
        self.file = playing_file
        self.roots = []
        self.snapshot = {}
        self.scan_no = 0
        self.last_subdlg = None
        self.subdlg_open = False
        self.missing_ticks = 0
        self.meta = {}
        self.stream_count = 0
        self.expect_until = None
        self.expect_mark = 0
        self.seen = 0                 # subtitle files considered so far
        self.considered = set()       # (path, size, mtime) already considered
        self.own_load_until = 0.0


class _Player(xbmc.Player):
    def __init__(self, svc):
        xbmc.Player.__init__(self)
        self._svc = svc

    def _call(self, name, *args):
        try:
            getattr(self._svc, name)(*args)
        except Exception:
            self._svc.log.exception('callback %s failed' % name)

    def onAVStarted(self):
        self._call('on_av_started')

    def onAVChange(self):
        self._call('on_av_change')

    def onPlayBackStopped(self):
        self._call('on_stop', 'stopped')

    def onPlayBackEnded(self):
        self._call('on_stop', 'ended')

    def onPlayBackError(self):
        self._call('on_stop', 'error')


class _Monitor(xbmc.Monitor):
    def __init__(self, svc):
        xbmc.Monitor.__init__(self)
        self._svc = svc

    def onSettingsChanged(self):
        try:
            self._svc.on_settings_changed()
        except Exception:
            self._svc.log.exception('onSettingsChanged failed')

    def onNotification(self, sender, method, data):
        try:
            self._svc.on_notification(sender, method, data)
        except Exception:
            self._svc.log.exception('onNotification failed')


class TranslatorService(object):
    def __init__(self, clock=None):
        self.clock = clock or time.time
        self.log = get_log()
        self.lock = threading.RLock()
        self.session = None
        self.session_counter = 0
        self.job = None
        self.recent = {}
        self.last_candidate = None
        self.tick_no = 0
        self.last_scan_secs = 0.0
        self.old_addon_checked = False
        self.cfg = Config.load()
        self.log.debug_enabled = self.cfg.debug
        self.monitor = _Monitor(self)
        self.player = _Player(self)

    # ------------------------------------------------------------------ lifecycle
    def run(self):
        self.startup()
        try:
            while not self.monitor.abortRequested():
                if self.monitor.waitForAbort(TICK):
                    break
                self.tick_no += 1
                started = time.time()
                try:
                    self.on_tick()
                except Exception:
                    self.log.exception('tick failed')
                busy = time.time() - started
                if busy > BUSY_TICK_SECONDS:
                    self.log.info('service loop was busy for %.1fs (last folder check %.1fs)' % (
                        busy, self.last_scan_secs))
        finally:
            with self.lock:
                job = self.job
                self.session = None
            if job is not None and job.is_alive():
                job.cancel()
                job.join(3)
            _try(kodi.home_window().clearProperty, None, kodi.HEARTBEAT_PROP)
            self.log.info('service stopped')

    def startup(self):
        self.log.info('=' * 60)
        self.log.info('service started (v%s)' % kodi.addon_version())
        for line in envinfo.environment_lines():
            self.log.debug('  ' + line)
        self.log.info('settings: ' + self.cfg.summary())
        profile = kodi.profile_dir()
        shutil.rmtree(output_temp_dir(), ignore_errors=True)       # translations of earlier sessions
        shutil.rmtree(os.path.join(profile, 'subtitles'), ignore_errors=True)
        if not migrate.old_addon_enabled():
            old_temp = migrate.old_temp_dir()
            if old_temp and os.path.isdir(old_temp):
                shutil.rmtree(old_temp, ignore_errors=True)   # left behind by AI Subtitle Translator
        removed = Cache(os.path.join(profile, 'cache')).purge(self.cfg.cache_days)
        if removed:
            self.log.info('removed %d old saved translations' % removed)
        if not self.cfg.api_key:
            kodi.notify(kodi.text(32306), 8000)

    def check_old_addon(self):
        """The earlier version of this add-on (other id) would translate every subtitle a second
        time. Returns True when nothing (more) needs doing this Kodi session."""
        if not migrate.old_addon_enabled():
            return True
        if migrate.declined():
            self.log.warn('%s (the earlier version of this add-on) is still switched on' % migrate.OLD_ID)
            return True
        if _try(self.player.isPlayingVideo, False) or kodi.dialog_id() != kodi.NO_DIALOG:
            return False        # ask when nothing is playing and no dialog is open
        self.log.warn('%s (the earlier version of this add-on) is still switched on - asking' % migrate.OLD_ID)
        xbmc.executebuiltin('RunScript(%s,oldaddon)' % kodi.ADDON_ID)
        return True

    def on_settings_changed(self):
        with self.lock:
            self.cfg = Config.load()
            self.log.debug_enabled = self.cfg.debug
            self.log.info('settings changed: ' + self.cfg.summary())

    # ------------------------------------------------------------------ loop
    def on_tick(self):
        if not self.old_addon_checked and self.tick_no >= OLD_ADDON_CHECK_TICK and self.tick_no % 4 == 0:
            self.old_addon_checked = _try(self.check_old_addon, True)
        home = kodi.home_window()
        _try(home.setProperty, None, kodi.HEARTBEAT_PROP, '%d' % int(time.time()))
        if _try(home.getProperty, '', REQUEST_PROP) == 'translate_now':
            self.on_request()
        with self.lock:
            playing = _try(self.player.isPlayingVideo, False)
            s = self.session
            if s is None:
                if playing:
                    self.start_session('video already playing')
                return
            if not playing:
                s.missing_ticks += 1
                if s.missing_ticks >= 6:
                    self.end_session('video no longer playing')
                return
            s.missing_ticks = 0
            self.poll_ui(s)
            if self.tick_no % SCAN_EVERY == 0:
                self.scan(s, 'poll')
            if s.expect_until is not None and self.clock() >= s.expect_until:
                s.expect_until = None
                if s.seen == s.expect_mark:
                    self.safety_net(s)
            if self.log.debug_enabled and self.tick_no % HEARTBEAT_TICKS == 0:
                self.log.debug('watching: ' + self.roots_summary(s))

    def poll_ui(self, s):
        open_now = kodi.cond(SUBDLG_COND)
        if open_now:
            s.last_subdlg = self.clock()
        if open_now != s.subdlg_open:
            self.log.debug('subtitle dialog %s' % ('opened' if open_now else 'closed'))
        s.subdlg_open = open_now

    # ------------------------------------------------------------------ player events
    def on_av_started(self):
        with self.lock:
            if not _try(self.player.isPlayingVideo, False):
                return
            playing_file = _try(self.player.getPlayingFile, '')
            if self.session is not None:
                if self.session.file == playing_file:
                    return
                self.end_session('another video started')
            self.start_session('onAVStarted')

    def on_av_change(self):
        with self.lock:
            s = self.session
            if s is None:
                return
            streams = list(_try(self.player.getAvailableSubtitleStreams, []) or [])
            current = _try(self.player.getSubtitles, '')
            grew = len(streams) > s.stream_count
            self.log.debug('onAVChange: %d subtitle streams %r, current %r' % (len(streams), streams, current))
            s.stream_count = len(streams)
            self.scan(s, 'onAVChange')
            if grew and self.clock() > s.own_load_until:
                s.expect_until = self.clock() + EXPECT_SECONDS
                s.expect_mark = s.seen

    def on_stop(self, reason):
        with self.lock:
            if self.session is not None:
                self.end_session('playback %s' % reason)

    def on_notification(self, sender, method, data):
        if sender == kodi.ADDON_ID and method.endswith('translate_now'):
            self.on_request()

    def on_request(self):
        """'Translate now' from the menu: arrives as a notification and as a window
        property (whichever comes first wins; the property is cleared either way)."""
        home = kodi.home_window()
        if _try(home.getProperty, '', REQUEST_PROP) != 'translate_now':
            return
        _try(home.clearProperty, None, REQUEST_PROP)
        self.translate_now()

    def note_own_load(self, session_id):
        """The job loaded our translation: the stream change that follows is ours."""
        with self.lock:
            s = self.session
            if s is not None and s.id == session_id:
                s.own_load_until = self.clock() + OWN_LOAD_QUIET
                s.stream_count += 1

    # ------------------------------------------------------------------ session
    def start_session(self, reason):
        self.session_counter += 1
        playing_file = _try(self.player.getPlayingFile, '')
        s = Session(self.session_counter, self.clock(), playing_file)
        self.session = s
        self.last_candidate = None
        self.recent.clear()
        s.meta = self.read_meta(playing_file)
        s.stream_count = len(_try(self.player.getAvailableSubtitleStreams, []) or [])
        s.roots = self.build_roots(playing_file)
        s.snapshot, stats = scanner.snapshot(s.roots, self.skip_paths())
        self.log.info('playback started (%s): %s | title %r | %d subtitle streams' % (
            reason, util.safe_location(playing_file), s.meta.get('title'), s.stream_count))
        self.log.info('watching: ' + self.roots_summary(s, stats))
        for root in s.roots:
            root.last_error = (stats.get(root.label) or {}).get('error')
            if root.last_error and root.last_error != 'missing':
                self.log.warn('cannot read folder %s (%s): %s' % (root.label, root.local or root.path, root.last_error))
            self.log.debug('  %s' % root.describe())
        if self.cfg.src_local:
            self.check_local(s)

    def end_session(self, reason):
        s = self.session
        self.session = None
        job = self.job
        if job is not None and job.is_alive() and s is not None and job.cand.session_id == s.id:
            job.cancel('playback')
        self.log.info('playback ended (%s)' % reason)

    def is_current(self, session_id):
        with self.lock:
            return self.session is not None and self.session.id == session_id

    def read_meta(self, playing_file):
        meta = {'title': '', 'plot': '', 'stem': ''}
        tag = _try(self.player.getVideoInfoTag)
        title = show = ''
        season = episode = -1
        year = 0
        if tag is not None:
            title = _try(tag.getTitle, '') or ''
            show = _try(tag.getTVShowTitle, '') or ''
            season = _try(tag.getSeason, -1)
            episode = _try(tag.getEpisode, -1)
            year = _try(tag.getYear, 0) or 0
            meta['plot'] = _try(tag.getPlot, '') or ''
        if show:
            label = show
            if season is not None and season >= 0 and episode is not None and episode >= 0:
                label += ' S%02dE%02d' % (season, episode)
            meta['title'] = label + (' - ' + title if title else '')
        elif title:
            meta['title'] = title + (' (%d)' % year if year else '')
        name = util.file_name(playing_file)
        stem = name.rsplit('.', 1)[0] if '.' in name else name
        if not any(c.isalpha() for c in stem) and meta['title']:
            stem = meta['title']
        meta['stem'] = stem or meta['title'] or 'subtitle'
        return meta

    def build_roots(self, playing_file):
        depth = 4
        roots = [scanner.Root('temp', 'temp', 'special://temp/', depth)]
        custom, _err = envinfo.kodi_setting('subtitles.custompath')
        if custom:
            roots.append(scanner.Root('custom', 'custom', custom, depth))
        ids = set()
        addons, _err = envinfo.subtitle_addons()
        for addon in addons:
            if addon.get('addonid'):
                ids.add(addon['addonid'])
        base = kodi.translate_path('special://profile/addon_data/')
        try:
            ids.update(n for n in os.listdir(base) if n.startswith('service.subtitles.'))
        except OSError:
            pass
        ids.discard(kodi.ADDON_ID)
        ids.discard(migrate.OLD_ID)       # the earlier version of this add-on: its output is a translation
        for addon_id in sorted(ids):
            roots.append(scanner.Root('addon:' + addon_id, 'addon',
                                      'special://profile/addon_data/%s/' % addon_id, depth))
        folder = util.video_folder(playing_file)
        if folder:
            roots.append(scanner.Root('video', 'video', folder, 2))
        unique, seen = [], set()
        for root in roots:
            key = os.path.normcase(os.path.normpath(root.local)) if root.local else root.path.rstrip('/')
            if key not in seen:
                seen.add(key)
                unique.append(root)
        return unique

    @staticmethod
    def skip_paths():
        """Our own folders (and the earlier version's): never treat translations as new subtitles."""
        return [kodi.profile_dir(), output_temp_dir(), migrate.old_temp_dir(), migrate.old_profile_dir()]

    def roots_summary(self, s, stats=None):
        parts = []
        for root in s.roots:
            st = (stats or {}).get(root.label) or root.last_stats or {}
            label = root.label.replace('addon:service.subtitles.', 'addon:')
            if st.get('error'):
                parts.append('%s (%s)' % (label, 'not there' if st['error'] == 'missing' else 'ERROR ' + st['error']))
            else:
                parts.append('%s %d files/%d subs %.1fs%s' % (
                    label, st.get('files', 0), st.get('subs', 0), st.get('secs', 0.0),
                    ' TRUNCATED' if st.get('truncated') else ''))
        return ' | '.join(parts)

    # ------------------------------------------------------------------ detection
    def scan(self, s, trigger):
        started = time.time()
        s.scan_no += 1
        new, stats = scanner.snapshot(s.roots, self.skip_paths(), s.scan_no, s.snapshot)
        self.last_scan_secs = time.time() - started
        for root in s.roots:
            st = stats.get(root.label)
            if not st:
                continue
            err = st.get('error')
            if err != root.last_error:
                if err and err != 'missing':
                    self.log.warn('cannot read folder %s: %s' % (root.label, err))
                elif not err:
                    self.log.debug('folder %s is readable now' % root.label)
                root.last_error = err
            if st.get('secs', 0) > scanner.SLOW_ROOT_SECONDS and root.every == 1:
                root.every = scanner.SLOW_ROOT_EVERY
                self.log.info('folder %s is slow (%.1fs), checking it less often' % (root.label, st['secs']))
        added, changed, _removed = scanner.diff(s.snapshot, new)
        s.snapshot = new
        for key in added + changed:
            self.consider(s, new[key], trigger, 'new' if key in added else 'changed')

    def safety_net(self, s):
        """Kodi added a subtitle stream but no new file was noticed: look again."""
        files, stats = scanner.snapshot(s.roots, self.skip_paths())
        now = time.time()
        recent = sorted((e for e in files.values()
                         if e.kind in ('sub', 'maybe') and e.mtime and now - e.mtime < RECENT_FILE_SECONDS
                         and (e.key, e.size, e.mtime) not in s.considered and e.is_subtitle),
                        key=lambda e: e.mtime, reverse=True)
        if recent:
            entry = recent[0]
            self.log.warn('Kodi added a subtitle stream but no new file was noticed; using the newest '
                          'subtitle file %s [%s], modified %.0fs ago' % (entry.name, entry.root.label, now - entry.mtime))
            s.snapshot.update(files)
            self.consider(s, entry, 'safety net', 'recent')
            return
        self.log.info('Kodi lists more subtitle tracks now, but no new subtitle file turned up in the '
                      'watched folders (fine if you switched tracks yourself): ' + self.roots_summary(s, stats))

    def consider(self, s, entry, trigger, what):
        fmt = entry.subtitle_format()
        if not fmt:
            if entry.kind == 'maybe':
                self.log.debug('%s file %s [%s] is not a subtitle' % (what, entry.name, entry.root.label))
            return
        s.seen += 1
        s.considered.add((entry.key, entry.size, entry.mtime))
        since = 0.0 if s.subdlg_open else ((self.clock() - s.last_subdlg) if s.last_subdlg is not None else None)
        source = classify_source(entry.root.kind, since)
        self.log.info('%s subtitle: %s [%s] %s -> %s (%s)' % (
            what, entry.name, entry.root.label, fmt.upper(), source, trigger))
        if fmt not in TRANSLATABLE:
            self.log.info('  %s subtitles are not supported yet (SRT and WebVTT only)' % fmt.upper())
            return
        candidate = Candidate(entry, source, s.id)
        self.last_candidate = candidate
        if not self.cfg.source_enabled(source):
            self.log.info('  not translating: %s subtitles are switched off in the settings' % source)
            return
        if self.cfg.mode == MODE_OFF:
            self.log.info('  not translating: "Do nothing" is selected in the settings')
            return
        self.start_job(candidate)

    def check_local(self, s):
        """At playback start: a subtitle next to a local video (Kodi loads it by itself)."""
        stem = util.file_name(s.file).rsplit('.', 1)[0].lower()
        if not stem:
            return
        found = [e for e in s.snapshot.values()
                 if e.root.kind == 'video' and e.name.lower().startswith(stem)
                 and e.subtitle_format() in TRANSLATABLE]
        if not found:
            return
        skip = self.cfg.skip_languages()
        detected = []
        for entry in sorted(found, key=lambda e: e.name):
            raw = scanner.read_head(entry, 512 * 1024) or b''
            text, _enc = srt.decode(raw, self.cfg.kodi_charset)
            code, how = langdetect.classify(entry.name, text)
            detected.append((entry, code))
            self.log.info('local subtitle %s: %s (%s)' % (entry.name, code or '?', how))
        if any(languages.matches(code, skip) for _e, code in detected):
            self.log.info('a local subtitle is already in a left-alone language - nothing to do')
            return
        english = [e for e, code in detected if code == 'en']
        pick = english[0] if english else detected[0][0]
        candidate = Candidate(pick, 'local', s.id)
        self.last_candidate = candidate
        if self.cfg.mode != MODE_OFF:
            self.start_job(candidate)

    # ------------------------------------------------------------------ jobs
    def start_job(self, candidate, forced=False):
        raw = scanner.read_head(candidate.entry, 8 * 1024 * 1024)
        if raw:
            candidate.raw = raw      # keep it: a4k deletes its temp files soon after
        elif candidate.raw:
            raw = candidate.raw
        md5 = hashlib.md5(raw).hexdigest() if raw else None
        now = self.clock()
        running = self.job if (self.job is not None and self.job.is_alive()) else None
        if not forced and md5:
            if (running is not None and running.md5 == md5 and not running.cancelled
                    and running.cand.session_id == candidate.session_id):
                self.log.info('  same subtitle is already being translated')
                return
            seen = self.recent.get(md5)
            if seen is not None and now - seen < DUPLICATE_WINDOW:
                self.log.info('  same subtitle was handled %.0fs ago' % (now - seen))
                return
        if running is not None:
            self.log.info('  a new subtitle arrived - cancelling the running translation')
            running.cancel()
        self.cfg = Config.load()
        self.log.debug_enabled = self.cfg.debug
        if md5:
            self.recent[md5] = now
        s = self.session
        meta = dict(s.meta) if s is not None else {}
        self.job = TranslationJob(self, candidate, self.cfg, meta, forced=forced, md5=md5)
        self.job.start()

    def job_finished(self, job):
        self.log.debug('job finished: %s -> %s' % (job.cand.name, job.outcome))

    def translate_now(self):
        with self.lock:
            if self.session is None:
                kodi.notify(kodi.text(32318))
                return
            if self.last_candidate is None:
                kodi.notify(kodi.text(32319))
                return
            self.log.info('translate now requested for %s' % self.last_candidate.name)
            kodi.notify(kodi.text(32320), 3000)
            self.start_job(self.last_candidate, forced=True)


def run():
    try:
        copied = migrate.import_old_setup()
    except Exception:
        copied = []
        get_log().exception('copying the old setup failed')
    if 'API key' in copied:
        kodi.notify(kodi.text(32286, old=migrate.OLD_NAME), 6000)
    TranslatorService().run()
