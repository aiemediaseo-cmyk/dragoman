# -*- coding: utf-8 -*-
"""One translation job: read a subtitle, decide, translate in the background,
write the result to the add-on's folder and load it into the player.
Playback never pauses; the original subtitle stays on screen until the
translation is ready."""
import os
import re
import threading
import time
import unicodedata

import xbmc
import xbmcgui

from . import cache as cache_mod
from . import gemini, kodi, langdetect, languages, scanner, srt, sync, timing, translator
from .config import TIMING_ASK, TIMING_AUTO, TIMING_OFF
from .log import get_log

MAX_FILE_BYTES = 8 * 1024 * 1024
TEMP_OUT = 'special://temp/dragoman/'     # where Kodi reliably opens subtitles (like its own TempSubtitle)
VERIFY_SECONDS = 5.0
FACTS_TRIES = 7                           # video length / frame rate: up to 3 s to appear
CONFIRM_MS = 20000                        # timing question: closes by itself after 20 s


def output_temp_dir():
    return kodi.translate_path(TEMP_OUT)


def estimate_minutes(lines, chunk_size, rpm, seconds_per_request=9.0, extra_requests=0):
    """Rough time for a translation: one request per chunk, at most `rpm` a minute."""
    requests = max(1, -(-max(0, lines) // max(1, chunk_size))) + extra_requests
    seconds = requests * max(seconds_per_request, 60.0 / max(1, rpm))
    return max(1, int(seconds / 60.0 + 0.5))


def safe_name(text, limit=60):
    """ASCII-only file name part: no '+', no accents, no path characters."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'[^A-Za-z0-9 ()_-]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:limit].strip()


def srt_time(ms):
    seconds = int((ms or 0) / 1000)
    return '%d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def language_label(code):
    sid = languages.string_id(code)
    return kodi.text(sid) if sid else (code or kodi.text(32315))


class Candidate(object):
    def __init__(self, entry, source, session_id):
        self.entry = entry
        self.source = source          # manual | auto | local
        self.session_id = session_id
        self.raw = None               # bytes read when the file was noticed

    @property
    def name(self):
        return self.entry.name


class TranslationJob(threading.Thread):
    def __init__(self, service, candidate, cfg, meta, forced=False, md5=None, translate=True):
        threading.Thread.__init__(self, name='dragoman-job')
        self.daemon = True
        self.service = service
        self.cand = candidate
        self.cfg = cfg
        self.meta = meta or {}
        self.forced = forced
        self.md5 = md5
        self.translate = translate      # False: only timing (translation is off for this subtitle)
        self.fix_key = None
        self.fix_note = ''              # 'remembered' | 'framerate' when a timing fix applies
        self.drift = None
        self.baked = None               # fix already in the file (a re-timed copy Kodi kept)
        self.kind = 'original'          # what the subtitle is: 'original' or 'translation'
        self.cancel_event = threading.Event()
        self.cancel_reason = ''
        self.log = get_log()
        self.progress = None
        self.result_path = None
        self.outcome = 'running'
        self.engine = None

    # ------------------------------------------------------------------ control
    def cancel(self, reason=''):
        self.cancel_reason = self.cancel_reason or reason
        self.cancel_event.set()
        engine = self.engine
        if engine is not None:
            try:
                engine.client.abort()    # cut a request in flight
            except Exception:
                pass

    @property
    def cancelled(self):
        return self.cancel_event.is_set()

    def _wait(self, seconds):
        return self.cancel_event.wait(max(0.0, seconds))

    # ------------------------------------------------------------------ run
    def run(self):
        try:
            self._run()
        except translator.Cancelled:
            self.outcome = 'cancelled'
            self.log.info('job cancelled (%s): %s' % (self.cancel_reason or 'new subtitle', self.cand.name))
            if self.cancel_reason == 'playback' and self.engine is not None:
                # finished chunks are in the cache: picking this subtitle again continues from there
                kodi.notify(kodi.text(32324), 6000)
        except translator.StopJob as exc:
            self.outcome = 'stopped:' + exc.kind
            self.log.warn('translation stopped (%s): %s' % (exc.kind, exc.message))
            messages = {'quota': 32307, 'auth': 32308, 'region': 32309, 'network': 32310,
                        'rate': 32311, 'model': 32312, 'bad_request': 32321}
            kodi.notify(kodi.text(messages.get(exc.kind, 32313), model=self.cfg.models[0]), 6000)
        except Exception:
            self.outcome = 'error'
            self.log.exception('translation failed: %s' % self.cand.name)
            kodi.notify(kodi.text(32313), 6000)
        finally:
            self._progress_close()
            self.service.job_finished(self)

    def _run(self):
        cfg = self.cfg
        entry = self.cand.entry
        self._wait_stable()
        raw = scanner.read_head(entry, MAX_FILE_BYTES)
        if not raw and self.cand.raw:
            self.log.info('%s is gone already - using the copy read when it appeared' % entry.name)
            raw = self.cand.raw
        if not raw:
            self.outcome = 'unreadable'
            self.log.warn('could not read %s' % entry.name)
            if self.forced:
                kodi.notify(kodi.text(32317))
            return
        text, encoding = srt.decode(raw, cfg.kodi_charset)
        cues = srt.parse(text)
        if len(cues) < 3:
            self.outcome = 'unreadable'
            self.log.warn('%s: only %d cues found (encoding %s) - not an SRT file?' % (entry.name, len(cues), encoding))
            if self.forced:
                kodi.notify(kodi.text(32317))
            return
        lang, how = langdetect.classify(entry.name, text)
        target = cfg.target
        self.log.info('subtitle %s: %d cues, %s, language %s by %s; target %s' % (
            entry.name, len(cues), encoding, lang or '?', how, target))
        origin = sync.origin_of(self.md5) if self.md5 else None
        if origin is not None:
            # Kodi's subtitle dialog keeps the re-timed file it was handed (next to the video,
            # in place of the subtitle there): work with the subtitle it was made from
            info, cues = origin
            self.md5 = info.get('md5') or self.md5
            self.baked = (float(info.get('k', 1.0)), float(info.get('b', 0.0)))
            self.kind = info.get('kind') or 'original'
            lang = info.get('lang') or lang
            self.log.info('%s is a re-timed copy Dragoman made earlier (%s, %s) - using the %s it was made from' % (
                entry.name, self.kind, timing.describe(*self.baked), 'translation' if self.kind == 'translation'
                else 'subtitle'))
        video = self.meta.get('video_key') or ''
        self.fix_key = timing.fix_key(video, self.md5) if (video and self.md5) else None
        left_alone = not self.forced and languages.matches(lang, cfg.skip_languages())
        # nothing to translate and Kodi is not showing it: no questions about its timing
        quiet = (left_alone or not self.translate) and not self._kodi_shows(lang)
        fix = self._timing(cues, quiet)
        if self.cancelled:
            raise translator.Cancelled()
        if left_alone:
            self.outcome = 'left_alone'
            self.log.info('left alone: %s is in %s' % (entry.name, lang))
            if cfg.notify_skips and not fix and self.translate:
                kodi.notify(kodi.text(32305, lang=language_label(lang)), 3000)
            self._original(cues, lang, fix)
            return
        if not self.translate:
            self.outcome = 'original'
            self._original(cues, lang, fix)
            return
        if not cfg.api_key:
            self.outcome = 'no_key'
            self.log.warn('no Gemini API key set')
            kodi.notify(kodi.text(32306), 8000)
            self._original(cues, lang, fix)
            return
        if not self.forced and cfg.mode == 1:
            if not self._ask(lang, target):
                self.outcome = 'declined'
                self.log.info('user declined the translation')
                self._original(cues, lang, fix)
                return
        if self.cancelled:
            raise translator.Cancelled()
        # The original stays on screen while the translation is made: note it for the
        # Sync assistant, and show it re-timed already when there is a fix.
        self._original(cues, lang, fix)

        texts = [cue.text for cue in cues]
        store = cache_mod.Cache(os.path.join(kodi.profile_dir(), 'cache'))
        key = store.key(texts, target, cfg.models[0], cfg.chunk_size)
        saved = store.load(key)
        from_cache = bool(saved.get('complete')) and len(saved.get('texts') or []) == len(texts)
        failed = 0
        if from_cache:
            final = saved['texts']
            self.log.info('using saved translation %s' % key)
        else:
            final, failed = self._translate(texts, lang, target, store, key, saved.get('chunks') or {},
                                            saved.get('characters'))
        if self.cancelled:
            raise translator.Cancelled()
        self.kind = 'translation'
        self._publish('translation', srt.compose(cues, final), target)
        fix = self._stored_fix()            # the Sync assistant may have been used meanwhile
        result = self._deliver(timing.compose(cues, final, fix), target)
        later = self._stored_fix()
        if result == 'loaded' and not timing.same_fix(later, fix):
            self.log.info('the timing was changed while the translation was loading - loading it again')
            result = self._deliver(timing.compose(cues, final, later), target)
        if result == 'loaded':
            self.outcome = 'loaded'
            if from_cache:
                kodi.notify(kodi.text(32304, lang=language_label(target)), 4000)
            elif failed:
                kodi.notify(kodi.text(32303, lang=language_label(target), failed=failed), 6000)
            else:
                kodi.notify(kodi.text(32302, lang=language_label(target)), 4000)
        elif result == 'rejected':
            self.outcome = 'rejected'
            kodi.notify(kodi.text(32322), 8000)
        else:
            self.outcome = 'written'

    # ------------------------------------------------------------------ steps
    def _wait_stable(self):
        """Wait until the file stops changing (Kodi or an add-on may still be writing it)."""
        last = None
        for _attempt in range(12):
            now = scanner.stat_entry(self.cand.entry)
            if now is not None and now == last and now[0] > 0:
                return
            if now is None and self.cand.raw:
                return   # deleted already (a4k cleans its temp folder); the saved copy is used
            last = now
            if self._wait(0.5):
                raise translator.Cancelled()

    def _ask(self, lang, target):
        try:
            return bool(xbmcgui.Dialog().yesno(
                kodi.addon_name(), kodi.text(32314, src=language_label(lang), lang=language_label(target)),
                autoclose=15000))
        except TypeError:
            return bool(xbmcgui.Dialog().yesno(
                kodi.addon_name(), kodi.text(32314, src=language_label(lang), lang=language_label(target))))

    def _translate(self, texts, lang, target, store, key, chunks, characters=None):
        cfg = self.cfg
        self.log.info('translating %d cues %s -> %s with %s (chunk %d, %d/min)' % (
            len(texts), lang or '?', target, cfg.models[0], cfg.chunk_size, cfg.rpm))
        self._progress_open(target, len(texts))
        remaining = len(texts) - sum(len(v) for v in chunks.values())
        kodi.notify(kodi.text(32323, n=remaining, lang=language_label(target),
                              min=estimate_minutes(remaining, cfg.chunk_size, cfg.rpm,
                                                   extra_requests=0 if characters else 1)), 6000)

        def make_client(model):
            return gemini.GeminiClient(cfg.api_key, model, user_agent='Kodi-Dragoman/%s' % kodi.addon_version(),
                                       log=self.log.info)

        def on_chunk(index, chunk_texts):
            chunks[str(index)] = chunk_texts
            store.save(key, {'complete': False, 'chunks': chunks, 'target': target,
                             'characters': engine.characters})

        def on_switch(model):
            kodi.notify(kodi.text(32316, model=model), 5000)

        # Close languages (hr/sr/bs, pt/pt-BR) legitimately come back looking almost unchanged.
        close = langdetect.same_group(lang, target)
        engine = translator.Translator(
            make_client, cfg.models, target, title=self.meta.get('title', ''), plot=self.meta.get('plot', ''),
            chunk_size=cfg.chunk_size, rpm=cfg.rpm, wait=self._wait, log=self.log.debug,
            progress=lambda done, total: self._progress_update(target, done, total),
            fallback=cfg.fallback, on_model_switch=on_switch, is_cancelled=lambda: self.cancelled,
            check_unchanged=not (self.forced or close), cast=self.meta.get('cast'),
            characters=characters if isinstance(characters, list) else None)
        self.engine = engine
        if self.cancelled:
            raise translator.Cancelled()
        if engine.characters:
            self.log.info('characters (saved): %s' % '; '.join(c.split('.')[0] for c in engine.characters))
        else:
            # who is who, and male or female: lets the translation get he/she forms right
            engine.find_characters(texts)
            self.log.info('characters: %s' % ('; '.join(c.split('.')[0] for c in engine.characters)
                                              or 'none found'))
            if engine.characters:
                store.save(key, {'complete': False, 'chunks': chunks, 'target': target,
                                 'characters': engine.characters})
        final, failed = engine.translate(texts, chunks, on_chunk)
        self.log.info('translated %d cues with %d requests (%d splits), %d kept in the original' % (
            len(texts), engine.requests, engine.splits, failed))
        if failed == 0:
            store.save(key, {'complete': True, 'texts': final, 'target': target, 'model': engine.model,
                             'characters': engine.characters})
        return final, failed

    def _file_name(self, target, tag='AI'):
        base = safe_name(self.meta.get('title') or self.meta.get('stem') or '') or 'Subtitle'
        code = languages.file_code(target)
        return '%s %s.%s.srt' % (base, tag, code) if code else '%s %s.srt' % (base, tag)

    # ------------------------------------------------------------------ timing
    def _duration_ms(self):
        try:
            return int(float(xbmc.Player().getTotalTime()) * 1000)
        except Exception:
            return 0

    def _video_facts(self):
        """(length in ms, frame rate label). Right after playback starts Kodi may not know
        them yet: wait a moment for them."""
        duration, fps = 0, ''
        for attempt in range(FACTS_TRIES):
            duration = self._duration_ms()
            fps = kodi.info('Player.Process(videofps)')
            if duration > 0 and timing.norm_fps(fps):
                break
            if attempt < FACTS_TRIES - 1 and self._wait(0.5):
                raise translator.Cancelled()
        return duration, fps

    def _stored_fix(self):
        if not self.fix_key:
            return None
        return timing.FixStore(kodi.profile_dir()).mapping(self.fix_key)

    def _timing(self, cues, quiet=False):
        """The timing fix (k, b) to apply, or None: what is stored for this video and
        subtitle first (a fix, or "leave it as it is"); else the frame-rate drift check
        (fix / ask / suggest) - not when quiet (a subtitle Kodi is not showing)."""
        cfg = self.cfg
        if not self.fix_key:
            return None
        store = timing.FixStore(kodi.profile_dir())
        rec = store.get(self.fix_key)
        if rec:
            fix = store.mapping(self.fix_key)
            if fix:
                self.fix_note = 'remembered'
                self.log.info('timing fix from last time: %s (%s)' % (timing.describe(*fix), rec.get('how')))
                return fix
            self.log.info('timing: left as it is (%s before)' % {
                'declined': 'you said no to a frame-rate fix', 'cleared': 'you undid the fix'}.get(
                    rec.get('how'), 'checked with the Sync assistant'))
            declined = rec.get('declined') if rec.get('how') in timing.INACTIVE else None
            if declined:
                # still offered in the subtitle dialog, should the user change their mind
                src, dst = timing.fps_pair(float(declined))
                self.drift = {'ratio': float(declined), 'from_fps': src, 'to_fps': dst, 'certain': False}
            return None
        if cfg.timing_check == TIMING_OFF:
            return None
        if quiet:
            self.log.info('timing check skipped: Kodi is not showing this subtitle')
            return None
        duration, fps = self._duration_ms(), kodi.info('Player.Process(videofps)')
        drift = timing.drift_check(cues, duration, fps, self.meta.get('file_name', ''), self.cand.name)
        if duration <= 0 or (drift is not None and not timing.norm_fps(fps)):
            # right after playback starts Kodi may not know the length / frame rate yet
            duration, fps = self._video_facts()
            drift = timing.drift_check(cues, duration, fps, self.meta.get('file_name', ''), self.cand.name)
        if not drift:
            self.log.info('timing check: nothing to fix (video %s, %s fps)' % (
                srt_time(duration), fps or '?'))
            return None
        self.drift = drift
        self.log.info('timing check (video %s, %s fps): %s -> ratio %s%s' % (
            srt_time(duration), fps or '?', drift['reason'], drift['ratio'], ' (sure)' if drift['certain'] else ''))
        if drift['ratio'] is None:
            kodi.notify(kodi.text(32432), 8000)
            return None
        src, dst = timing.fps_label(drift['from_fps']), timing.fps_label(drift['to_fps'])
        if not drift['certain']:
            kodi.notify(kodi.text(32433, src=src), 8000)      # not sure: suggest, don't touch
            return None
        title = self.meta.get('title', '')
        if cfg.timing_check == TIMING_ASK:
            answer = self._confirm(kodi.text(32430, src=src, dst=dst))
            if answer is None:
                self.log.info('no answer to the timing question - it is asked again next time')
                return None
            if not answer:
                store.decline(self.fix_key, drift['ratio'], title)
                self.log.info('user kept the timing as it is (remembered for this video and subtitle)')
                return None
        new = store.set_ratio(self.fix_key, drift['ratio'], title)
        self.fix_note = 'framerate'
        return new['k'], new['b']

    def _publish(self, kind, base_content, lang):
        """Note what is showing, for the Sync assistant (menu and subtitle dialog)."""
        token = self.service.session_token(self.cand.session_id)
        if not token:
            return
        path = sync.write_base(base_content, self.md5, lang)
        if not path:
            return
        drift = None
        if self.drift and self.drift.get('ratio'):
            drift = dict((k, self.drift.get(k)) for k in ('ratio', 'from_fps', 'to_fps', 'certain'))
        sync.publish_current({
            'file': self.meta.get('file', ''), 'video': self.meta.get('video_key', ''),
            'md5': self.md5 or '', 'lang': lang or '', 'kind': kind, 'base': path, 'session': token,
            'title': self.meta.get('title') or self.meta.get('stem') or '', 'drift': drift})

    def _kodi_shows(self, lang):
        """Is Kodi showing a subtitle in this language? A re-timed copy only replaces what is
        on screen: loading a subtitle turns subtitles on, so it never happens when they are off."""
        if not kodi.cond('VideoPlayer.SubtitlesEnabled'):
            return False
        try:
            current = languages.normalize(xbmc.Player().getSubtitles() or '')
        except Exception:
            current = ''
        want = languages.normalize(lang or '')
        return not (current and want and languages.file_code(current) != languages.file_code(want)
                    and not langdetect.same_group(current, want))

    def _original(self, cues, lang, fix):
        """The subtitle as it came is what is showing: note it, and when a timing fix
        applies, show a re-timed copy instead (only if Kodi is showing it)."""
        self._publish(self.kind, srt.compose(cues), lang)
        if timing.same_fix(fix, self.baked):
            return                          # the file on screen is timed like that already
        if self.cancelled:
            raise translator.Cancelled()
        if not self._kodi_shows(lang):
            self.log.info('timing fix not loaded: Kodi is not showing this subtitle (subtitles off '
                          'or another track)')
            return
        content = timing.compose(cues, None, fix) if timing.is_change(fix) else srt.compose(cues)
        tag = 'AI' if self.kind == 'translation' else 'synced'
        result = self._deliver(content, lang, name=self._file_name(lang, tag=tag), show=False,
                               what='the re-timed subtitle')
        if result == 'loaded':
            if not timing.is_change(fix):
                self.log.info('the subtitle on screen had a timing fix that was undone - shown as it came')
            elif self.fix_note == 'remembered':
                kodi.notify(kodi.text(32434), 4000)
            else:
                kodi.notify(sync.fix_message(fix[0], fix[1], 'framerate'), 6000)

    def _confirm(self, message):
        """True (yes), False (no) or None (the question closed by itself unanswered: Kodi
        reports that as "no", so it is told apart by the time it stayed open)."""
        started = time.time()
        try:
            answer = bool(xbmcgui.Dialog().yesno(kodi.addon_name(), message, autoclose=CONFIRM_MS))
        except TypeError:
            answer = bool(xbmcgui.Dialog().yesno(kodi.addon_name(), message))
        if not answer and (time.time() - started) * 1000 >= CONFIRM_MS - 1000:
            return None
        return answer

    def _deliver(self, content, target, name=None, show=True, what='the translation'):
        """Write the translation (or re-timed copy) and switch Kodi to it; check that Kodi
        really did.

        First try: a fresh folder in Kodi's temp folder (the same place Kodi keeps
        the subtitles it downloads itself). Second try: a fresh folder in the
        add-on's profile folder. Returns 'loaded', 'rejected' or 'not_current'.
        """
        name = name or self._file_name(target)
        job_id = '%s-%d' % ((self.md5 or 'job')[:8], int(time.time() * 1000) % 10 ** 9)
        folders = [os.path.join(output_temp_dir(), job_id),
                   os.path.join(kodi.profile_dir(), 'subtitles', job_id)]
        data = b'\xef\xbb\xbf' + content.encode('utf-8')
        for attempt, folder in enumerate(folders, 1):
            try:
                if not os.path.isdir(folder):
                    os.makedirs(folder)
                path = os.path.join(folder, name)
                with open(path, 'wb') as handle:
                    handle.write(data)
            except (IOError, OSError) as exc:
                self.log.warn('could not write the translation to %s: %s' % (folder, exc))
                continue
            self.result_path = path
            self.log.info('wrote %s (%d bytes)' % (path, len(data)))
            result = self._load(path, target, show, what)
            if result != 'rejected':
                return result
            self.log.warn('Kodi did not switch to %s (attempt %d of %d)' % (what, attempt, len(folders)))
        return 'rejected'

    def _load(self, path, target, show=True, what='the translation'):
        if not self.service.is_current(self.cand.session_id):
            self.log.info('playback changed - subtitle saved but not loaded')
            return 'not_current'
        before = sync.subtitle_snapshot()
        try:
            self.service.note_own_load(self.cand.session_id)
            player = xbmc.Player()
            player.setSubtitles(path)
            if show:
                try:
                    player.showSubtitles(True)
                except Exception:
                    pass
        except Exception as exc:
            self.log.error('could not load the subtitle: %s' % exc)
            return 'rejected'
        # Kodi opens the file asynchronously; when it cannot read it, it logs
        # "Unable to create subtitle parser" and stays on the previous track.
        want = languages.file_code(target)
        current = ''
        waited = 0.0
        while waited < VERIFY_SECONDS:
            if self._wait(0.5):
                raise translator.Cancelled()
            waited += 0.5
            if sync.switched(before, want):
                try:
                    current = xbmc.Player().getSubtitles() or ''
                except Exception:
                    current = ''
                self.log.info('Kodi switched to %s (subtitle language %r)' % (what, current))
                return 'loaded'
        try:
            current = xbmc.Player().getSubtitles() or ''
        except Exception:
            current = ''
        self.log.warn('Kodi is still on subtitle %r after %.0fs, not on %s' % (current, waited, what))
        return 'rejected'

    # ------------------------------------------------------------------ progress
    def _progress_open(self, target, total):
        if not self.cfg.show_progress:
            kodi.notify(kodi.text(32300, lang=language_label(target)), 3000)
            return
        try:
            self.progress = xbmcgui.DialogProgressBG()
            self.progress.create(kodi.addon_name(), kodi.text(32300, lang=language_label(target)))
        except Exception:
            self.progress = None

    def _progress_update(self, target, done, total):
        if self.progress is None:
            return
        try:
            self.progress.update(int(done * 100 / max(1, total)), kodi.addon_name(),
                                 kodi.text(32301, lang=language_label(target), done=done, total=total))
        except Exception:
            pass

    def _progress_close(self):
        if self.progress is not None:
            try:
                self.progress.close()
            except Exception:
                pass
            self.progress = None
