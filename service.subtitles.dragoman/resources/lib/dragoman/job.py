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
from . import gemini, kodi, langdetect, languages, scanner, srt, translator
from .log import get_log

MAX_FILE_BYTES = 8 * 1024 * 1024
TEMP_OUT = 'special://temp/dragoman/'     # where Kodi reliably opens subtitles (like its own TempSubtitle)
VERIFY_SECONDS = 5.0


def output_temp_dir():
    return kodi.translate_path(TEMP_OUT)


def estimate_minutes(lines, chunk_size, rpm, seconds_per_request=9.0):
    """Rough time for a translation: one request per chunk, at most `rpm` a minute."""
    requests = max(1, -(-max(0, lines) // max(1, chunk_size)))
    seconds = requests * max(seconds_per_request, 60.0 / max(1, rpm))
    return max(1, int(seconds / 60.0 + 0.5))


def safe_name(text, limit=60):
    """ASCII-only file name part: no '+', no accents, no path characters."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'[^A-Za-z0-9 ()_-]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:limit].strip()


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
    def __init__(self, service, candidate, cfg, meta, forced=False, md5=None):
        threading.Thread.__init__(self, name='dragoman-job')
        self.daemon = True
        self.service = service
        self.cand = candidate
        self.cfg = cfg
        self.meta = meta or {}
        self.forced = forced
        self.md5 = md5
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
        if not self.forced and languages.matches(lang, cfg.skip_languages()):
            self.outcome = 'left_alone'
            self.log.info('left alone: %s is in %s' % (entry.name, lang))
            if cfg.notify_skips:
                kodi.notify(kodi.text(32305, lang=language_label(lang)), 3000)
            return
        if not cfg.api_key:
            self.outcome = 'no_key'
            self.log.warn('no Gemini API key set')
            kodi.notify(kodi.text(32306), 8000)
            return
        if not self.forced and cfg.mode == 1:
            if not self._ask(lang, target):
                self.outcome = 'declined'
                self.log.info('user declined the translation')
                return
        if self.cancelled:
            raise translator.Cancelled()

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
            final, failed = self._translate(texts, lang, target, store, key, saved.get('chunks') or {})
        if self.cancelled:
            raise translator.Cancelled()
        content = srt.compose(cues, final)
        result = self._deliver(content, target)
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

    def _translate(self, texts, lang, target, store, key, chunks):
        cfg = self.cfg
        self.log.info('translating %d cues %s -> %s with %s (chunk %d, %d/min)' % (
            len(texts), lang or '?', target, cfg.models[0], cfg.chunk_size, cfg.rpm))
        self._progress_open(target, len(texts))
        remaining = len(texts) - sum(len(v) for v in chunks.values())
        kodi.notify(kodi.text(32323, n=remaining, lang=language_label(target),
                              min=estimate_minutes(remaining, cfg.chunk_size, cfg.rpm)), 6000)

        def make_client(model):
            return gemini.GeminiClient(cfg.api_key, model, user_agent='Kodi-Dragoman/%s' % kodi.addon_version(),
                                       log=self.log.info)

        def on_chunk(index, chunk_texts):
            chunks[str(index)] = chunk_texts
            store.save(key, {'complete': False, 'chunks': chunks, 'target': target})

        def on_switch(model):
            kodi.notify(kodi.text(32316, model=model), 5000)

        # Close languages (hr/sr/bs, pt/pt-BR) legitimately come back looking almost unchanged.
        close = langdetect.same_group(lang, target)
        engine = translator.Translator(
            make_client, cfg.models, target, title=self.meta.get('title', ''), plot=self.meta.get('plot', ''),
            chunk_size=cfg.chunk_size, rpm=cfg.rpm, wait=self._wait, log=self.log.debug,
            progress=lambda done, total: self._progress_update(target, done, total),
            fallback=cfg.fallback, on_model_switch=on_switch, is_cancelled=lambda: self.cancelled,
            check_unchanged=not (self.forced or close))
        self.engine = engine
        if self.cancelled:
            raise translator.Cancelled()
        final, failed = engine.translate(texts, chunks, on_chunk)
        self.log.info('translated %d cues with %d requests (%d splits), %d kept in the original' % (
            len(texts), engine.requests, engine.splits, failed))
        if failed == 0:
            store.save(key, {'complete': True, 'texts': final, 'target': target, 'model': engine.model})
        return final, failed

    def _file_name(self, target):
        base = safe_name(self.meta.get('title') or self.meta.get('stem') or '') or 'Subtitle'
        return '%s AI.%s.srt' % (base, languages.file_code(target))

    def _deliver(self, content, target):
        """Write the translation and switch Kodi to it; check that Kodi really did.

        First try: a fresh folder in Kodi's temp folder (the same place Kodi keeps
        the subtitles it downloads itself). Second try: a fresh folder in the
        add-on's profile folder. Returns 'loaded', 'rejected' or 'not_current'.
        """
        name = self._file_name(target)
        job_id = '%s-%d' % ((self.md5 or 'job')[:8], int(time.time()))
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
            result = self._load(path, target)
            if result != 'rejected':
                return result
            self.log.warn('Kodi did not switch to the translation (attempt %d of %d)' % (attempt, len(folders)))
        return 'rejected'

    def _load(self, path, target):
        if not self.service.is_current(self.cand.session_id):
            self.log.info('playback changed - translation saved but not loaded')
            return 'not_current'
        try:
            self.service.note_own_load(self.cand.session_id)
            player = xbmc.Player()
            player.setSubtitles(path)
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
            try:
                current = xbmc.Player().getSubtitles() or ''
            except Exception:
                current = ''
            if languages.normalize(current) == want or current.lower() == want:
                self.log.info('Kodi switched to the translation (subtitle language %r)' % current)
                return 'loaded'
        self.log.warn('Kodi is still on subtitle %r after %.0fs, not on the translation' % (current, waited))
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
