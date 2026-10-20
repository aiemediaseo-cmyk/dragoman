# -*- coding: utf-8 -*-
"""Kodi-side scenarios for Dragoman with stand-in Kodi modules and a fake Gemini server.

Run: python3 tests/test_service.py -v
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(os.path.dirname(HERE), 'service.subtitles.dragoman')
sys.path.insert(0, os.path.join(HERE, 'fakekodi'))
sys.path.insert(0, ADDON)
sys.path.insert(0, HERE)

from kodistate import STATE  # noqa: E402
from test_core import EN, ES_LATAM, HR, SL, SR, SR_CYR, episode_cues, make_srt  # noqa: E402
from test_core import srt as core_srt  # noqa: E402

# Fake keys are built from two parts so secret scanners do not mistake them for real ones.
G = 'AI' + 'za'
KEY = G + 'SyTESTKEY0123456789abcdefghijklmnopq'
STREAM = 'https://cdn.example.net/d/TOKEN123/The.Film.2021.1080p.WEB-DL.x264-GRP.mkv?token=TOKEN123'


def sentences(text, n=30):
    parts = [p.strip() for p in text.replace('?', '?|').replace('.', '.|').split('|') if p.strip()]
    return (parts * 10)[:n]


def write(path, data):
    if not os.path.isdir(os.path.dirname(path)):
        os.makedirs(os.path.dirname(path))
    with open(path, 'wb') as handle:
        handle.write(data)


class FakeGemini(object):
    def __init__(self):
        self.requests = []
        self.mode = 'ok'
        self.delay = 0.0
        self.lock = threading.Lock()

    def __call__(self, client, method, url, body, headers):
        body = json.loads(body.decode('utf-8')) if body else None
        with self.lock:
            self.requests.append((url, body, dict(headers)))
        if self.delay:
            waited = 0.0
            while waited < self.delay:            # like a socket: abort() cuts the wait
                if client._aborted:
                    raise OSError('connection shut down')
                time.sleep(0.05)
                waited += 0.05
        if self.mode == 'quota':
            err = {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED', 'message': 'Quota exceeded',
                             'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                                          'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]}}
            return 429, json.dumps(err).encode('utf-8')
        if self.mode == 'badkey':
            err = {'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.'}}
            return 400, json.dumps(err).encode('utf-8')
        if url.endswith('models?pageSize=1000'):
            data = {'models': [{'name': 'models/gemini-3.5-flash-lite', 'supportedGenerationMethods': ['generateContent']}]}
        else:
            user = json.loads(body['contents'][0]['parts'][0]['text'])
            if 'dialogue' in user:          # the "who are the characters" request
                answer = {'characters': [{'name': 'Jane', 'gender': 'male', 'note': 'consultant'},
                                         {'name': 'Lisbon', 'gender': 'female', 'note': 'his boss'}]}
            else:
                answer = {'cues': [{'id': c['id'], 'speaker': '?', 'text': '[sl] ' + c['text']}
                                   for c in user['cues']]}
            data = {'candidates': [{'content': {'parts': [{'text': json.dumps(answer)}]},
                                    'finishReason': 'STOP'}],
                    'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 120}}
        return 200, json.dumps(data).encode('utf-8')

    def generate_calls(self):
        """Translation requests (the character request is listed by character_calls)."""
        return [r for r in self.requests if r[0].endswith(':generateContent') and not self._is_characters(r)]

    def character_calls(self):
        return [r for r in self.requests if r[0].endswith(':generateContent') and self._is_characters(r)]

    @staticmethod
    def _is_characters(request):
        body = request[1] or {}
        try:
            return 'dialogue' in json.loads(body['contents'][0]['parts'][0]['text'])
        except (KeyError, IndexError, ValueError, TypeError):
            return False


class Base(unittest.TestCase):
    settings = {}

    def setUp(self):
        self.t0 = time.time()
        self.base = tempfile.mkdtemp(prefix='dragoman-')
        STATE.reset(self.base, ADDON)
        for sub in ('temp', 'profile/addon_data', 'home'):
            os.makedirs(os.path.join(self.base, sub))
        STATE.settings = {'rpm': '30', 'chunk_size': '100'}
        STATE.settings.update(self.settings)
        self.save_state(api_key=KEY, leave_alone=['hr', 'sr', 'bs'])
        kodi_settings = {'locale.subtitlelanguage': 'Slovenian', 'locale.language': 'resource.language.en_gb',
                         'subtitles.charset': 'DEFAULT', 'subtitles.custompath': ''}

        def get_setting(params):
            if params['setting'] in kodi_settings:
                return {'value': kodi_settings[params['setting']]}
            return {'__error__': {'code': -32602, 'message': 'Invalid params.'}}
        STATE.jsonrpc = {
            'Settings.GetSettingValue': get_setting,
            'Addons.GetAddons': {'addons': [{'addonid': 'service.subtitles.a4ksubtitles', 'enabled': True}]},
        }
        for name in list(sys.modules):
            if name.startswith('resources'):
                del sys.modules[name]
        from resources.lib.dragoman import gemini, kodi, log
        kodi._addon = None
        log._instance = None
        gemini.reset_session_cache()
        self.fake = FakeGemini()
        gemini.https_transport = self.fake
        self.temp = os.path.join(self.base, 'temp')
        self.a4k = os.path.join(self.base, 'profile', 'addon_data', 'service.subtitles.a4ksubtitles', 'temp')

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # helpers ---------------------------------------------------------------
    def state_path(self):
        return os.path.join(self.base, 'profile', 'addon_data', 'service.subtitles.dragoman', 'state.json')

    def save_state(self, **values):
        data = self.load_state()
        data.update(values)
        write(self.state_path(), json.dumps(data).encode('utf-8'))

    def load_state(self):
        try:
            with open(self.state_path()) as handle:
                return json.load(handle)
        except (IOError, OSError, ValueError):
            return {}

    def player(self):
        svc = getattr(self, '_svc', None)
        return svc.player if svc is not None else STATE.players[-1]

    def svc(self):
        return self._svc

    def join_job(self):
        job = self._svc.job
        if job is not None:
            job.join(15)
            self.assertFalse(job.is_alive(), 'job did not finish')
        return job

    def start_play(self, playing_file=STREAM):
        STATE.playing = True
        STATE.file = playing_file
        STATE.total = 6000.0
        STATE.meta = {'title': 'The Film', 'year': 2021, 'plot': 'Two friends and a secret.'}
        self.player().onAVStarted()

    def open_dialog(self):
        STATE.conds['Window.IsActive(subtitlesearch)'] = True

    def pick(self, name, text, encoding='utf-8'):
        write(os.path.join(self.temp, name), make_srt(sentences(text)).encode(encoding))
        STATE.conds['Window.IsActive(subtitlesearch)'] = False
        self.player().onAVChange()

    def run_service(self, actions):
        from resources.lib.dragoman.service import TranslatorService
        STATE.actions = actions
        self._svc = TranslatorService(clock=STATE.clock)
        self._svc.run()

    def log(self):
        path = os.path.join(self.base, 'profile', 'addon_data', 'service.subtitles.dragoman', 'translator.log')
        with io.open(path, encoding='utf-8') as handle:
            return handle.read()

    def toasts(self):
        return [c[1] for c in STATE.dialog_calls if c[0] == 'notification']


class StreamTests(Base):
    def test_manual_pick_translated_loaded_and_cached(self):
        def stop():
            STATE.playing = False
            self.player().onPlayBackStopped()

        self.run_service({
            2: self.start_play, 4: self.open_dialog,
            8: lambda: self.pick('TempSubtitle.en.srt', EN),
            9: self.join_job,
            # same English subtitle picked again later -> saved translation, no new request
            80: self.open_dialog,
            82: lambda: self.pick('TempSubtitle.en.srt', EN + ' '),   # touched: new mtime/size
            83: self.join_job,
            90: stop, 92: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        print(log)
        calls = self.fake.generate_calls()
        self.assertEqual(len(calls), 1)
        url, body, headers = calls[0]
        self.assertTrue(url.endswith('models/gemini-3.5-flash-lite:generateContent'))
        self.assertEqual(headers.get('x-goog-api-key'), KEY)
        sent = json.loads(body['contents'][0]['parts'][0]['text'])
        self.assertEqual(sent['title'], 'The Film (2021)')
        self.assertEqual(len(sent['cues']), 30)
        self.assertNotIn('-->', json.dumps(body))          # timings never leave the device
        self.assertIn('Slovenian', body['systemInstruction']['parts'][0]['text'])
        # output written with BOM, same timings, translated text, loaded twice
        self.assertEqual(len(STATE.loaded_subs), 2)
        out = STATE.loaded_subs[0]
        self.assertTrue(out.startswith(os.path.join(self.base, 'temp', 'dragoman') + os.sep), out)
        self.assertTrue(out.endswith(os.sep + 'The Film (2021) AI.sl.srt'), out)
        self.assertNotIn('+', os.path.basename(out))
        self.assertIn("Kodi switched to the translation (subtitle language 'slv')", self.log())
        with open(out, 'rb') as handle:
            data = handle.read()
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'00:00:01,000 --> 00:00:01,900\n[sl] ', data)
        toasts = self.toasts()
        self.assertIn('Translating 30 lines into Slovenian - ready in about 1 min. Keep watching.', toasts)
        self.assertIn('Slovenian subtitles loaded', toasts)
        self.assertIn('Slovenian subtitles loaded (saved translation)', toasts)
        self.assertTrue(any(p[0] == 'create' for p in STATE.progress_bg))
        self.assertIn('-> manual', log)
        self.assertNotIn(KEY, log)
        self.assertNotIn('TOKEN123', log)

    def test_own_and_left_alone_languages_are_skipped(self):
        def a4k(name, text, encoding='utf-8'):
            write(os.path.join(self.a4k, name), make_srt(sentences(text)).encode(encoding))
            self.player().onAVChange()

        self.run_service({
            2: self.start_play, 4: self.open_dialog,
            6: lambda: self.pick('TempSubtitle.sl.srt', SL, 'cp1250'),
            7: self.join_job,
            100: lambda: a4k('The.Film.2021.sr.srt', SR),
            101: self.join_job,
            140: lambda: a4k('The.Film.2021.srp.srt', SR_CYR),     # Cyrillic is not on the list
            141: self.join_job,
            150: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        print(log)
        self.assertIn('left alone: TempSubtitle.sl.srt is in sl', log)
        self.assertIn('left alone: The.Film.2021.sr.srt is in sr', log)
        self.assertIn('-> auto', log)
        self.assertIn('Subtitle is in Slovenian - left as it is', self.toasts())
        self.assertEqual(len(self.fake.generate_calls()), 1)   # only the Cyrillic one
        self.assertIn('language sr-Cyrl', log)
        self.assertEqual(len(STATE.loaded_subs), 1)

    def test_restarting_the_same_video_translates_again(self):
        def restart():
            STATE.playing = False
            self.player().onPlayBackStopped()
            os.remove(os.path.join(self.temp, 'TempSubtitle.en.srt'))
            self.start_play()

        self.fake.delay = 3.0
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN),
                          7: lambda: time.sleep(0.5),
                          8: restart,        # stop during translation, play again at once
                          9: self.open_dialog,
                          10: lambda: (setattr(self.fake, 'delay', 0.0), self.pick('TempSubtitle.en.srt', EN)),
                          11: self.join_job,
                          14: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertNotIn('same subtitle', self.log())

    def test_cast_and_characters_reach_gemini_and_are_saved(self):
        def play():
            STATE.playing = True
            STATE.file = STREAM
            STATE.total = 6000.0
            STATE.meta = {'title': 'Red Sky at Night', 'show': 'The Mentalist', 'season': 3, 'episode': 1,
                          'plot': 'A body in a lake.',
                          'actors': [('Robin Tunney', 'Teresa Lisbon', 1), ('Simon Baker', 'Patrick Jane', 0),
                                     ('Tim Kang', 'Kimball Cho', 2)]}
            self.player().onAVStarted()

        def stop():
            STATE.playing = False
            self.player().onPlayBackStopped()

        self.run_service({2: play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN), 7: self.join_job,
                          20: stop, 22: play, 24: self.open_dialog,
                          26: lambda: self.pick('TempSubtitle.en.srt', EN + ' '), 27: self.join_job,
                          30: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        self.assertIn('cast 3', log)
        self.assertIn('characters: Jane: male; Lisbon: female', log)
        first = json.loads(self.fake.character_calls()[0][1]['contents'][0]['parts'][0]['text'])
        self.assertEqual(first['cast'], ['Patrick Jane (Simon Baker)', 'Teresa Lisbon (Robin Tunney)',
                                         'Kimball Cho (Tim Kang)'])
        chunk = json.loads(self.fake.generate_calls()[0][1]['contents'][0]['parts'][0]['text'])
        self.assertEqual(chunk['characters'], ['Jane: male. consultant', 'Lisbon: female. his boss'])
        # second time: the saved translation is used, no new requests at all
        self.assertEqual(len(self.fake.character_calls()), 1)
        self.assertEqual(len(self.fake.generate_calls()), 1)
        self.assertEqual(len(STATE.loaded_subs), 2)

    def test_kodi_19_cast_text(self):
        from resources.lib.dragoman.service import TranslatorService
        from xbmc import InfoTagVideo
        tag = InfoTagVideo({'cast_text': 'Simon Baker as Patrick Jane\nRobin Tunney as Teresa Lisbon\nNarrator'})
        self.assertEqual(TranslatorService.read_cast(tag),
                         ['Patrick Jane (Simon Baker)', 'Teresa Lisbon (Robin Tunney)', 'Narrator'])
        self.assertEqual(TranslatorService.read_cast(InfoTagVideo({})), [])

    def test_stopping_playback_mid_translation_says_the_work_is_kept(self):
        def stop():
            STATE.playing = False
            self.player().onPlayBackStopped()

        self.fake.delay = 4.0
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN),
                          7: lambda: time.sleep(1.5),      # the job is waiting for Gemini now
                          8: stop,
                          9: self.join_job,
                          12: lambda: setattr(STATE, 'abort', True)})
        self.assertIn('job cancelled (playback): TempSubtitle.en.srt', self.log())
        self.assertIn('Translation stopped with playback - the finished part is saved and is used '
                      'when you pick this subtitle again', self.toasts())
        self.assertEqual(STATE.loaded_subs, [])

    def test_new_pick_cancels_running_job(self):
        self.fake.delay = 5.0
        first = {}

        def second():
            first['job'] = self._svc.job
            self.fake.delay = 0.0
            self.open_dialog()
            write(os.path.join(self.temp, 'TempSubtitle.de.srt'),
                  make_srt(sentences(u'Ich weiß nicht, was du willst. Das ist gut, danke. Wir sind hier. ' * 4)).encode('utf-8'))
            STATE.conds['Window.IsActive(subtitlesearch)'] = False
            self.player().onAVChange()

        self.run_service({
            2: self.start_play, 4: self.open_dialog,
            6: lambda: self.pick('TempSubtitle.en.srt', EN),
            7: lambda: time.sleep(0.8),     # first job is waiting for Gemini (5 s fake answer)
            8: second,
            9: lambda: (first['job'].join(10), self.join_job()),
            20: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        print(log)
        self.assertEqual(first['job'].outcome, 'cancelled')
        self.assertLess(time.time() - self.t0, 5.0)     # abort cut the slow request
        self.assertEqual(self._svc.job.outcome, 'loaded')
        self.assertIn('cancelling the running translation', log)
        self.assertEqual(len(STATE.loaded_subs), 1)

    def test_quota_used_up_keeps_original(self):
        STATE.settings['fallback_models'] = 'false'
        self.fake.mode = 'quota'
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN), 7: self.join_job,
                          10: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(STATE.loaded_subs, [])
        self.assertIn("Gemini's free daily quota is used up - kept the original subtitle", self.toasts())

    def test_quota_switches_to_next_free_model(self):
        calls = {'n': 0}
        real = self.fake.__call__

        def first_quota(client, method, url, body, headers):
            calls['n'] += 1
            self.fake.mode = 'quota' if calls['n'] == 1 else 'ok'
            return real(client, method, url, body, headers)
        from resources.lib.dragoman import gemini
        gemini.https_transport = first_quota
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN), 7: self.join_job,
                          10: lambda: setattr(STATE, 'abort', True)})
        # the first request (who are the characters) hits the daily quota: switch, then carry on there
        urls = [r[0] for r in self.fake.requests if r[0].endswith(':generateContent')]
        self.assertEqual(len(urls), 3)
        self.assertIn('gemini-3.5-flash-lite', urls[0])
        self.assertIn('gemini-3.6-flash', urls[1])
        self.assertIn('gemini-3.6-flash', self.fake.generate_calls()[0][0])
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertIn('Switched to gemini-3.6-flash (daily quota of the previous model used up)', self.toasts())

    def test_translate_now_overrides_left_alone(self):
        from resources.lib.dragoman import script
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.hr.srt', SR), 7: self.join_job,
                          10: lambda: script.run(['default.py', 'translate']),
                          11: self.join_job,
                          14: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(len(self.fake.generate_calls()), 1)
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertIn('translate now requested', self.log())

    def test_translate_now_without_notifications(self):
        import xbmc
        from resources.lib.dragoman import script
        original = xbmc.executebuiltin
        xbmc.executebuiltin = lambda command, wait=False: STATE.builtins.append(command)
        try:
            self.run_service({2: self.start_play, 4: self.open_dialog,
                              6: lambda: self.pick('TempSubtitle.hr.srt', SR), 7: self.join_job,
                              10: lambda: script.run(['default.py', 'translate']),
                              12: self.join_job,
                              14: lambda: setattr(STATE, 'abort', True)})
        finally:
            xbmc.executebuiltin = original
        self.assertEqual(len(STATE.loaded_subs), 1)

    def test_a4k_deletes_its_temp_file(self):
        write(os.path.join(self.temp, 'TempSubtitle.en.srt'), make_srt(sentences(EN, 5)).encode('utf-8'))

        def a4k_download():
            write(os.path.join(self.a4k, 'Runner.2026.eng.srt'), make_srt(sentences(EN)).encode('utf-8'))

        def kodi_copies_and_a4k_cleans():
            write(os.path.join(self.temp, 'TempSubtitle.en.srt'), make_srt(sentences(EN)).encode('utf-8'))
            shutil.rmtree(self.a4k)
            STATE.conds['Window.IsActive(subtitlesearch)'] = False
            STATE.subs = ['KIN', 'English']
            self.player().onAVChange()

        STATE.playing, STATE.file, STATE.total = True, STREAM, 6000.0   # already playing at service start
        STATE.subs = ['KIN']
        self.run_service({3: self.open_dialog, 20: a4k_download, 21: kodi_copies_and_a4k_cleans,
                          22: self.join_job, 30: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        print(log)
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertIn('using the copy read when it appeared', log)
        self.assertNotIn('Kodi added a subtitle stream but', log)   # our own load does not trip the safety net

    def test_a4k_eng_named_download_like_the_users_tv(self):
        """Exact file names from the user's Kodi 21.2 debug log."""
        STATE.subs = ['KIN']

        def a4k_downloads_and_kodi_copies():
            write(os.path.join(self.a4k, 'Runner.2026.1080p.AMZN.WEB-DL.DDP5.1.H264.eng'),
                  make_srt(sentences(EN)).encode('utf-8'))
            write(os.path.join(self.temp, 'TempSubtitle.en.eng'), make_srt(sentences(EN)).encode('utf-8'))
            STATE.conds['Window.IsActive(subtitlesearch)'] = False
            STATE.subs = ['KIN', 'TempSubtitle en (External)']
            self.player().onAVChange()

        write(os.path.join(self.temp, 'kodi.old.log'), b'x' * 100)
        self.run_service({2: self.start_play, 4: self.open_dialog, 12: a4k_downloads_and_kodi_copies,
                          13: self.join_job, 20: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        print(log)
        self.assertRegex(log, r'new subtitle: (TempSubtitle\.en\.eng|Runner\.2026\..*\.eng) \[\S+\] SRT -> manual')
        self.assertIn('language en by file name', log)
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertEqual(len(self.fake.generate_calls()), 1)
        self.assertNotIn('Kodi added a subtitle stream but', log)

    def test_safety_net_when_no_new_file_is_noticed(self):
        STATE.subs = ['KIN']

        def play_with_file_already_there():
            # the subtitle file is already in place when playback starts, so the
            # folder comparison never sees it change
            write(os.path.join(self.temp, 'Runner.2026.en.srt'), make_srt(sentences(EN)).encode('utf-8'))
            self.start_play()

        def kodi_loads_it():
            STATE.subs = ['KIN', 'English']
            self.player().onAVChange()

        self.run_service({2: play_with_file_already_there, 6: kodi_loads_it,
                          24: self.join_job, 30: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        print(log)
        self.assertIn('using the newest subtitle file Runner.2026.en.srt [temp]', log)
        self.assertEqual(len(STATE.loaded_subs), 1)

    def test_safety_net_reports_empty_folders(self):
        STATE.subs = ['KIN']
        self.run_service({2: self.start_play,
                          6: lambda: (setattr(STATE, 'subs', ['KIN', 'English']), self.player().onAVChange()),
                          24: lambda: setattr(STATE, 'abort', True)})
        self.assertIn('no new subtitle file turned up in the watched folders (fine if you switched tracks yourself): temp ', self.log())

    def test_kodi_cannot_open_temp_copy_falls_back(self):
        """The user's TV: Kodi logged 'Unable to create subtitle parser' and stayed on English."""
        STATE.reject_subs = lambda path: os.sep + 'dragoman' + os.sep in path
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN), 7: self.join_job,
                          20: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        print(log)
        self.assertEqual(len(STATE.loaded_subs), 2)
        self.assertIn(os.path.join('profile', 'addon_data', 'service.subtitles.dragoman', 'subtitles'),
                      STATE.loaded_subs[1])
        self.assertIn('Kodi did not switch to the translation (attempt 1 of 2)', log)
        self.assertIn('Slovenian subtitles loaded', self.toasts())

    def test_kodi_cannot_open_any_copy(self):
        STATE.reject_subs = lambda path: True
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN), 7: self.join_job,
                          40: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(self._svc.job.outcome, 'rejected')
        self.assertIn('Kodi could not open the translated subtitle - details are in the log', self.toasts())
        self.assertNotIn('Slovenian subtitles loaded', self.toasts())

    def test_our_output_is_never_picked_up_as_a_new_subtitle(self):
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN), 7: self.join_job,
                          30: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        self.assertNotIn('AI.sl.srt [temp]', log)
        self.assertNotIn('left as it is', ' '.join(self.toasts()))

    def test_no_key(self):
        self.save_state(api_key='')
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN), 7: self.join_job,
                          10: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(self.fake.generate_calls(), [])
        self.assertIn('Add your Gemini API key: Program add-ons > Dragoman', self.toasts())


class OtherAddonTests(Base):
    """Subterra (script.subterra) translates by itself: it writes the film's English track to
    special://temp/subterra_eng.srt and its translation, a few lines at a time, to
    special://temp/subterra_active.srt (names from Subterra 0.3.11). Dragoman must not touch
    either, or the two add-ons fight over the subtitle on screen (GitHub issue report)."""

    def subterra_writes(self, name, text, n=30):
        write(os.path.join(self.temp, name), make_srt(sentences(text, n)).encode('utf-8'))
        STATE.subs = list(STATE.subs) + ['subterra']
        self.player().onAVChange()

    def subterra_session(self, extra=None):
        STATE.subs = ['KIN']
        actions = {
            2: self.start_play,
            6: lambda: self.subterra_writes('subterra_eng.srt', EN),
            8: lambda: self.subterra_writes('subterra_active.srt', ES_LATAM, 6),
            30: lambda: self.subterra_writes('subterra_active.srt', ES_LATAM + ' ' + EN, 14),   # grows
            60: lambda: self.subterra_writes('subterra_active.srt', ES_LATAM, 30),
            70: self.join_job,
            90: lambda: setattr(STATE, 'abort', True)}
        actions.update(extra or {})
        self.run_service(actions)
        return self.log()

    def test_subterra_files_are_ignored_by_default(self):
        log = self.subterra_session({
            # a subtitle picked from a4k in the same playback is still translated
            72: self.open_dialog, 74: lambda: self.pick('TempSubtitle.en.srt', EN), 76: self.join_job})
        print(log)
        self.assertIn('subtitle: subterra_eng.srt [temp] comes from Subterra (script.subterra) - ignored', log)
        self.assertIn('subtitle: subterra_active.srt [temp] comes from Subterra (script.subterra) - ignored', log)
        self.assertEqual(log.count('comes from Subterra'), 2)          # once per file, not on every rewrite
        self.assertNotIn('using the newest subtitle file subterra', log)
        self.assertNotIn('language en by file name; target sl\n', log.split('TempSubtitle.en.srt')[0])
        self.assertIn('ignore_addons=script.subterra', log)
        self.assertEqual(len(self.fake.generate_calls()), 1)          # only the a4k pick
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertTrue(STATE.loaded_subs[0].endswith('AI.sl.srt'))

    def test_chooser_lists_service_add_ons_and_an_empty_choice_is_kept(self):
        from resources.lib.dragoman import script

        def get_addons(params):
            if params.get('type') == 'xbmc.service':
                return {'addons': [{'addonid': 'script.subterra', 'name': 'Subterra'},
                                   {'addonid': 'service.subtitles.dragoman', 'name': 'Dragoman'}]}
            return {'addons': [{'addonid': 'service.subtitles.a4ksubtitles', 'name': 'a4kSubtitles'},
                               {'addonid': 'service.subtitles.dragoman', 'name': 'Dragoman'}]}
        STATE.jsonrpc['Addons.GetAddons'] = get_addons
        STATE.multiselect_answer = []
        script.run(['default.py', 'ignoreaddons'])
        call = [c for c in STATE.dialog_calls if c[0] == 'multiselect'][0]
        self.assertEqual(call[2], ['a4kSubtitles  [service.subtitles.a4ksubtitles]', 'Subterra  [script.subterra]'])
        self.assertEqual(call[3], [1])                                # Subterra preselected
        self.assertEqual(self.load_state()['ignore_addons'], [])
        from resources.lib.dragoman.config import Config
        self.assertEqual(Config.load().ignore_addons, [])
        # switched off: Subterra's English source is translated like any other subtitle
        log = self.subterra_session()
        print(log)
        # what the user saw: Subterra's files picked up, re-translated on every rewrite
        self.assertNotIn('comes from Subterra', log)
        self.assertIn('new subtitle: subterra_eng.srt [temp]', log)
        self.assertIn('changed subtitle: subterra_active.srt [temp]', log)
        self.assertGreaterEqual(len(self.fake.generate_calls()), 1)
        # and back on, with an add-on that is not installed any more kept on the list
        STATE.dialog_calls = []
        self.save_state(ignore_addons=['script.subterra', 'script.gone'])
        STATE.multiselect_answer = [1, 2]
        script.run(['default.py', 'ignoreaddons'])
        call = [c for c in STATE.dialog_calls if c[0] == 'multiselect'][0]
        self.assertEqual(call[2][2], 'script.gone  [script.gone] (not installed)')
        self.assertEqual(call[3], [1, 2])
        self.assertEqual(self.load_state()['ignore_addons'], ['script.subterra', 'script.gone'])


class SpanishTargetTests(Base):
    settings = {}

    def setUp(self):
        Base.setUp(self)
        from resources.lib.dragoman import languages
        codes = [row[0] for row in languages.LANGUAGES]
        STATE.settings['target_language'] = str(codes.index('es') + 1)

    def test_latin_american_spanish_is_not_translated_again(self):
        names = ['Movie.es-419.srt', 'Movie.Spanish (LatAm).srt', u'Movie.Español Latinoamericano.srt']

        def a4k(name):
            write(os.path.join(self.a4k, name), make_srt(sentences(ES_LATAM)).encode('utf-8'))
            self.player().onAVChange()

        actions = {2: self.start_play, 200: lambda: setattr(STATE, 'abort', True)}
        for i, name in enumerate(names):
            actions[10 + 60 * i] = (lambda n: lambda: a4k(n))(name)
            actions[12 + 60 * i] = self.join_job
        self.run_service(actions)
        log = self.log()
        print(log)
        for name in names:
            self.assertIn('left alone: %s is in es' % name, log)
        self.assertEqual(len(self.fake.generate_calls()), 0)
        self.assertEqual(len(STATE.loaded_subs), 0)


class AskAndOffTests(Base):
    settings = {'mode': '1'}

    def test_ask_declined(self):
        STATE.yesno_answer = False
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN), 7: self.join_job,
                          10: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(self.fake.generate_calls(), [])
        asks = [c[1] for c in STATE.dialog_calls if c[0] == 'yesno']
        self.assertEqual(asks, ['Translate this English subtitle into Slovenian?'])


class LocalShareTests(Base):
    settings = {'target_language': '8'}   # index 8 -> LANGUAGES[7] = German

    def test_local_english_next_to_video(self):
        share = os.path.join(self.base, 'smb', 'nas', 'Movies')
        write(os.path.join(share, 'Film.2020.mkv'), b'x')
        write(os.path.join(share, 'Film.2020.en.srt'), make_srt(sentences(EN)).encode('utf-8'))
        self.run_service({2: lambda: self.start_play('smb://nas/Movies/Film.2020.mkv'), 3: self.join_job,
                          6: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        print(log)
        self.assertIn('local subtitle Film.2020.en.srt: en', log)
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertTrue(STATE.loaded_subs[0].endswith('The Film (2021) AI.de.srt'))
        body = self.fake.generate_calls()[0][1]
        self.assertIn('German', body['systemInstruction']['parts'][0]['text'])


class ScriptTests(Base):
    def test_first_run_import_and_test_key(self):
        from resources.lib.dragoman import script
        self.save_state(api_key='')
        keyfile = os.path.join(self.base, 'usb', 'gemini.txt')
        write(keyfile, ('my key:\n%s\n' % KEY).encode('utf-8'))
        STATE.browse_answer = keyfile
        # first run: ok -> choose "Import from a file" -> shown, "save?" yes -> "test now?" yes -> works
        # -> "change language?" no
        STATE.select_answers = [2, -1]
        answers = [True, True, False]
        STATE_yes = lambda *a, **k: answers.pop(0) if answers else False  # noqa: E731
        import xbmcgui
        original_yesno = xbmcgui.Dialog.yesno
        xbmcgui.Dialog.yesno = lambda self, heading, message, *a, **k: STATE_yes()
        try:
            script.run(['default.py'])
        finally:
            xbmcgui.Dialog.yesno = original_yesno
        self.assertEqual(self.load_state()['api_key'], KEY)
        self.assertTrue(self.load_state()['setup_done'])
        # Kodi's settings dialog later writes back its stale copy: the key must survive
        STATE.settings = {'rpm': '8', 'mode': '1'}
        from resources.lib.dragoman.config import Config
        self.assertEqual(Config.load().api_key, KEY)
        oks = [c[1] for c in STATE.dialog_calls if c[0] == 'ok']
        self.assertTrue(any(o.startswith('The key works.') and '[sl] Good evening' in o for o in oks), oks)

    def test_remove_key(self):
        from resources.lib.dragoman import script
        script.run(['default.py', 'removekey'])
        self.assertEqual(self.load_state()['api_key'], '')

    def test_bad_key_message(self):
        from resources.lib.dragoman import script
        self.fake.mode = 'badkey'
        script.run(['default.py', 'testkey'])
        oks = [c[1] for c in STATE.dialog_calls if c[0] == 'ok']
        self.assertEqual(oks[-1], 'Gemini rejected the API key - check it in the settings')

    def test_leave_alone_chooser_and_clear_cache_and_log(self):
        from resources.lib.dragoman import languages, script
        codes = [row[0] for row in languages.LANGUAGES]
        STATE.multiselect_answer = [codes.index('hr'), codes.index('sr'), codes.index('bs')]
        script.run(['default.py', 'leavealone'])
        self.assertEqual(self.load_state()['leave_alone'], ['hr', 'sr', 'bs'])
        STATE.settings = {}          # stale settings dialog save
        from resources.lib.dragoman.config import Config
        self.assertEqual(Config.load().leave_alone, ['hr', 'sr', 'bs'])
        pre = [c for c in STATE.dialog_calls if c[0] == 'multiselect'][0][3]
        self.assertEqual(pre, [codes.index('hr'), codes.index('sr'), codes.index('bs')])
        script.run(['default.py', 'clearcache'])
        out = os.path.join(self.base, 'usb')
        os.makedirs(out)
        STATE.select_answers = [1]
        STATE.browse_answer = out + '/'
        script.run(['default.py', 'sendlog'])
        saved = os.listdir(out)
        self.assertEqual(len(saved), 1)
        with io.open(os.path.join(out, saved[0]), encoding='utf-8') as handle:
            report = handle.read()
        self.assertIn('leave_alone=hr,sr,bs', report)
        self.assertNotIn(KEY, report)
        self.assertIn('key: set (AIza...nopq', report)

    def test_find_key(self):
        from resources.lib.dragoman import script
        self.assertEqual(script.find_key('GEMINI_API_KEY=%s\n' % KEY), KEY)
        self.assertEqual(script.find_key('  sk-abcdefghijklmnopqrstuvwx  '), 'sk-abcdefghijklmnopqrstuvwx')
        self.assertEqual(script.find_key('hello world'), '')


NEW_KEY = G + 'SyZYXWVUTSRQPONMLKJIHGFEDCBA9876543'
assert len(NEW_KEY) == 39


class KeyEntryTests(Base):
    def yes_messages(self):
        return [c[1] for c in STATE.dialog_calls if c[0] == 'yesno']

    def test_typed_key_is_visible_shown_again_and_editable(self):
        from resources.lib.dragoman import script
        self.save_state(api_key='')
        typo = NEW_KEY[:-1] + 'X'
        STATE.input_answers = ['  %s ' % typo, NEW_KEY]
        STATE.yesno_answers = [False, True, False]      # Edit, then Save, then "test now?" no
        script.run(['default.py', 'enterkey'])
        self.assertEqual(self.load_state()['api_key'], NEW_KEY)
        import xbmcgui
        first, second = STATE.inputs
        for call in STATE.inputs:     # plain keyboard: what is typed stays visible
            self.assertEqual(call['type'], xbmcgui.INPUT_ALPHANUM)
            self.assertFalse(call['option'] & xbmcgui.ALPHANUM_HIDE_INPUT)
        self.assertEqual(first['defaultt'], '')
        self.assertEqual(second['defaultt'], typo)       # Edit goes back with what was typed
        confirm = self.yes_messages()[:2]
        self.assertIn('[B]%s[/B]' % typo, confirm[0])
        self.assertIn('39 characters', confirm[0])
        self.assertIn('[B]%s[/B]' % NEW_KEY, confirm[1])
        self.assertEqual(STATE.yesno_labels[0], ('Edit', 'Save'))
        self.assertNotIn(NEW_KEY, self.log())
        self.assertIn('API key saved', self.toasts())

    def test_saved_key_is_prefilled_and_odd_keys_get_a_warning(self):
        from resources.lib.dragoman import script
        STATE.input_answers = [KEY]
        script.run(['default.py', 'enterkey'])
        self.assertEqual(STATE.inputs[0]['defaultt'], KEY)
        self.assertIn('Key not changed', self.toasts())
        STATE.input_answers = ['abc def ghi jkl mno pqr stu']
        STATE.yesno_answers = [True, False]
        script.run(['default.py', 'enterkey'])
        self.assertEqual(self.load_state()['api_key'], 'abcdefghijklmnopqrstu')
        self.assertIn('does not look like a Gemini key', self.yes_messages()[0])

    def fake_web(self, pages):
        from resources.lib.dragoman import keysource
        fetched = []

        def get(url, user_agent, timeout=20):
            fetched.append(url)
            page = pages.get(url)
            if page is None:
                raise keysource.FetchError('http', 'HTTP 404', 404)
            if isinstance(page, Exception):
                raise page
            return page
        keysource.http_get = get
        return fetched

    def test_import_from_pastebin_address(self):
        from resources.lib.dragoman import script
        self.save_state(api_key='')
        fetched = self.fake_web({'https://pastebin.com/raw/AbCd1234':
                                 ('text/plain; charset=utf-8', 'GEMINI_API_KEY=%s\r\n' % NEW_KEY)})
        STATE.input_answers = ['pastebin.com/AbCd1234']
        STATE.yesno_answers = [True, False]         # save it? yes; test now? no
        script.run(['default.py', 'importurl'])
        self.assertEqual(fetched, ['https://pastebin.com/raw/AbCd1234'])
        self.assertEqual(self.load_state()['api_key'], NEW_KEY)
        self.assertEqual(STATE.inputs[0]['defaultt'], 'pastebin.com/')
        self.assertFalse(STATE.inputs[0]['option'])
        found, after = self.yes_messages()
        self.assertIn('[B]%s[/B]' % NEW_KEY, found)
        self.assertIn('delete the paste', after)
        self.assertEqual(STATE.yesno_labels[0], ('Cancel', 'Save'))
        log = self.log()
        self.assertNotIn('AbCd1234', log)        # the address leads to the key: only the site is logged
        self.assertNotIn(NEW_KEY, log)
        self.assertIn('API key found at pastebin.com', log)
        # next time the same site is offered, but not the old paste (it leads to the key)
        STATE.input_answers = ['']
        script.run(['default.py', 'importurl'])
        self.assertEqual(STATE.inputs[-1]['defaultt'], 'pastebin.com/')
        self.assertNotIn('AbCd1234', json.dumps(self.load_state()))

    def test_several_keys_let_the_user_choose(self):
        from resources.lib.dragoman import script
        other = G + 'SyOTHERKEYxxxxxxxxxxxxxxxxxxxxxxxxx'
        keyfile = os.path.join(self.base, 'usb', 'keys.txt')
        write(keyfile, ('old: %s\nnew: %s-work\n' % (other, NEW_KEY)).encode('utf-16'))
        STATE.browse_answer = keyfile
        STATE.select_answers = [1]
        STATE.yesno_answers = [True, False]
        script.run(['default.py', 'importkey'])
        choose = [c for c in STATE.dialog_calls if c[0] == 'select'][0]
        self.assertEqual(choose[2], [other, NEW_KEY, NEW_KEY + '-work'])
        self.assertEqual(self.load_state()['api_key'], NEW_KEY)

    def test_import_falls_back_to_the_page_and_ignores_site_scripts(self):
        from resources.lib.dragoman import script
        page = ('<html><head><script>var k="' + G + 'SyGOOGLEOWNKEYxxxxxxxxxxxxxxxxxxxxxx";</script></head>'
                '<body><script>x("' + G + 'SyANOTHERSITEKEYyyyyyyyyyyyyyyyyyyyyy")</script>'
                '<textarea class="textarea">%s</textarea></body></html>' % NEW_KEY)
        fetched = self.fake_web({'https://paste.kodi.tv/ilokuqeqan.kodi': ('text/html', page)})
        STATE.input_answers = ['https://paste.kodi.tv/ilokuqeqan.kodi']
        STATE.yesno_answers = [True, False]
        script.run(['default.py', 'importurl'])
        self.assertEqual(fetched, ['https://paste.kodi.tv/raw/ilokuqeqan', 'https://paste.kodi.tv/ilokuqeqan.kodi'])
        self.assertEqual(self.load_state()['api_key'], NEW_KEY)

    def test_import_errors_go_back_to_the_address(self):
        from resources.lib.dragoman import keysource, script
        fetched = self.fake_web({'https://pastebin.com/raw/Wrong123': keysource.FetchError('network', 'no route'),
                                 'https://pastebin.com/raw/NoKey123': ('text/plain', 'just some notes here')})
        STATE.input_answers = ['pastebin.com/', 'pastebin.com/Wrong123', 'pastebin.com/NoKey123', '']
        script.run(['default.py', 'importurl'])
        self.assertEqual(self.load_state()['api_key'], KEY)          # unchanged
        oks = [c[1] for c in STATE.dialog_calls if c[0] == 'ok']
        self.assertTrue(oks[0].startswith('Type the whole address'), oks)
        self.assertIn('Could not read the page on pastebin.com', oks[1])
        self.assertIn('no route', oks[1])
        self.assertTrue(oks[2].startswith('No API key found'), oks)
        self.assertEqual([i['defaultt'] for i in STATE.inputs][1:],
                         ['pastebin.com/', 'pastebin.com/Wrong123', 'pastebin.com/NoKey123'])
        self.assertEqual(len(fetched), 2)
        self.assertNotIn('Wrong123', self.log())


OLD_ID = 'service.subtitles.aitranslator'


class MigrationTests(Base):
    def old_profile(self, state=None, settings=None):
        folder = os.path.join(self.base, 'profile', 'addon_data', OLD_ID)
        if state is not None:
            write(os.path.join(folder, 'state.json'), json.dumps(state).encode('utf-8'))
        if settings is not None:
            write(os.path.join(folder, 'settings.xml'), settings.encode('utf-8'))
        return folder

    def test_key_languages_and_settings_are_copied_once(self):
        from resources.lib.dragoman import migrate
        os.remove(self.state_path())
        STATE.settings = {}
        self.old_profile({'api_key': NEW_KEY, 'leave_alone': ['hr', 'sr', 'bs'], 'setup_done': True},
                         '<settings version="2">\n'
                         '    <setting id="target_language">27</setting>\n'
                         '    <setting id="mode" default="true">0</setting>\n'
                         '    <setting id="rpm">12</setting>\n'
                         '    <setting id="api_key" default="true"></setting>\n'
                         '</settings>\n')
        copied = migrate.import_old_setup()
        self.assertEqual(copied, ['API key', 'languages to leave alone', '2 settings'])
        saved = self.load_state()
        self.assertEqual(saved['api_key'], NEW_KEY)
        self.assertEqual(saved['leave_alone'], ['hr', 'sr', 'bs'])
        self.assertTrue(saved['setup_done'] and saved['migrated'])
        self.assertEqual(STATE.settings, {'target_language': '27', 'rpm': '12'})
        STATE.settings = {}
        self.assertEqual(migrate.import_old_setup(), [])       # only once
        self.assertEqual(STATE.settings, {})
        self.assertNotIn(NEW_KEY, self.log())

    def test_existing_key_is_kept_and_v010_settings_are_read(self):
        from resources.lib.dragoman import migrate
        self.old_profile(settings='<settings>\n<setting id="api_key" value="%s" />\n'
                                  '<setting id="leave_alone" value="de,fr" />\n</settings>\n' % NEW_KEY)
        migrate.import_old_setup()
        saved = self.load_state()
        self.assertEqual(saved['api_key'], KEY)
        self.assertEqual(saved['leave_alone'], ['hr', 'sr', 'bs'])

    def test_service_start_copies_and_offers_to_switch_old_addon_off(self):
        from resources.lib.dragoman import script, service
        os.remove(self.state_path())
        self.old_profile({'api_key': NEW_KEY, 'leave_alone': ['hr']})
        STATE.conds['System.AddonIsEnabled(%s)' % OLD_ID] = True
        calls = []

        def set_enabled(params):
            calls.append(params)
            STATE.conds['System.AddonIsEnabled(%s)' % OLD_ID] = False
            return 'OK'
        STATE.jsonrpc['Addons.SetAddonEnabled'] = set_enabled
        STATE.dialog = 10100                       # a dialog is open at first: wait
        STATE.actions = {36: lambda: setattr(STATE, 'dialog', 9999), 60: lambda: setattr(STATE, 'abort', True)}
        service.run()
        ask = 'RunScript(service.subtitles.dragoman,oldaddon)'
        self.assertEqual(STATE.builtins.count(ask), 1)       # once, after the dialog closed
        self.assertIn('Your API key and settings were copied from AI Subtitle Translator', self.toasts())
        self.assertEqual(self.load_state()['api_key'], NEW_KEY)
        # Kodi runs the script: "Switch it off"
        STATE.yesnocustom_answers = [1]
        script.run(['default.py', 'oldaddon'])
        self.assertEqual(calls, [{'addonid': OLD_ID, 'enabled': False}])
        self.assertIn('AI Subtitle Translator switched off - you can uninstall it', self.toasts())
        asks = [c[1] for c in STATE.dialog_calls if c[0] == 'yesnocustom']
        self.assertEqual(len(asks), 1)
        self.assertIn('Dragoman replaces AI Subtitle Translator', asks[0])
        self.assertEqual(STATE.yesno_labels[-1], ('Keep both', 'Switch it off', 'Ask later'))

    def test_old_addons_translations_are_not_picked_up(self):
        STATE.conds['System.AddonIsEnabled(%s)' % OLD_ID] = True
        STATE.playing = True                 # playing: no question during the film
        old_out = os.path.join(self.temp, 'aisubs', 'f00d', 'The Film AI.sl.srt')
        old_saved = os.path.join(self.base, 'profile', 'addon_data', OLD_ID, 'subtitles', 'x', 'The Film AI.sl.srt')
        self.run_service({2: self.start_play,
                          6: lambda: write(old_out, make_srt(sentences(EN)).encode('utf-8')),
                          7: lambda: write(old_saved, make_srt(sentences(EN)).encode('utf-8')),
                          14: lambda: self.player().onAVChange(),
                          50: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(self.fake.generate_calls(), [])
        self.assertNotIn('The Film AI.sl.srt', self.log())
        self.assertNotIn('RunScript(service.subtitles.dragoman,oldaddon)', STATE.builtins)

    def test_later_and_back_ask_again_keep_both_does_not(self):
        from resources.lib.dragoman import migrate
        STATE.conds['System.AddonIsEnabled(%s)' % OLD_ID] = True
        STATE.yesnocustom_answers = [-1, 2, 0]      # Back, Ask later, Keep both
        for _ in range(4):
            self.assertFalse(migrate.offer_to_disable_old())
        self.assertEqual(len([c for c in STATE.dialog_calls if c[0] == 'yesnocustom']), 3)
        self.assertTrue(self.load_state()['old_addon_declined'])
        self.assertEqual(STATE.jsonrpc.get('Addons.SetAddonEnabled'), None)



EPISODE = 'https://cdn.example.net/d/TOKEN9/Show.S01E01.720p.HDTV.x264-GRP.mkv'
HR_LINES = [l.strip() + '.' for l in HR.split('.') if l.strip()]


def croatian_episode(**kw):
    cues = episode_cues(**kw)
    texts = [HR_LINES[n % len(HR_LINES)] for n in range(len(cues))]
    return core_srt.compose(cues, texts)


class TimingScenarios(Base):
    def play_episode(self, fps='25.000', total=2520.0):
        STATE.playing = True
        STATE.file = EPISODE
        STATE.total = total
        STATE.labels['Player.Process(videofps)'] = fps
        STATE.meta = {'title': 'Pilot', 'show': 'Show', 'season': 1, 'episode': 1, 'plot': 'A start.'}
        self.player().onAVStarted()

    def pick_text(self, name, content):
        write(os.path.join(self.temp, name), content.encode('utf-8'))
        STATE.conds['Window.IsActive(subtitlesearch)'] = False
        self.player().onAVChange()

    def store(self):
        from resources.lib.dragoman import kodi, timing
        return timing.FixStore(kodi.profile_dir())

    def loaded_text(self, index=-1):
        with io.open(STATE.loaded_subs[index], encoding='utf-8-sig') as handle:
            return handle.read()

    def again(self):
        """Ready for another service run (the one before ended with Kodi "shutting down")."""
        STATE.ticks = 0
        STATE.abort = False
        STATE.actions = {}
        STATE.loaded_subs = []
        STATE.dialog_calls = []

    def offered(self, handle='7'):
        from resources.lib.dragoman import submodule
        STATE.plugin_items = []
        submodule.run(['plugin://service.subtitles.dragoman/', handle, '?action=search&languages=English'])
        return [(li.getLabel(), li.getLabel2(), url.split('id=')[1]) for _h, url, li in STATE.plugin_items]

    def download(self, item_id, handle='8'):
        from resources.lib.dragoman import submodule
        STATE.plugin_items = []
        submodule.run(['plugin://service.subtitles.dragoman/', handle, '?action=download&id=' + item_id])
        paths = [url for _h, url, _li in STATE.plugin_items]
        return paths[0] if paths else ''

    def hear_at(self, seconds, also=None):
        """Stand-in for the "Press OK the moment you hear" dialog: the user presses at `seconds`."""
        import xbmcgui
        original = xbmcgui.Dialog.ok

        def heard(dialog, heading, message):
            STATE.dialog_calls.append(('ok', message))
            self.assertFalse(STATE.paused)            # the video plays while the user listens
            if also is not None:
                also()
            STATE.time = seconds
            return True
        xbmcgui.Dialog.ok = heard
        return original

    def pick_english(self):
        """The user picks the English subtitle in Kodi's dialog: Kodi adds it and shows it."""
        STATE.subs.append('eng')
        STATE.cur_sub, STATE.cur_index, STATE.subs_visible = 'eng', len(STATE.subs) - 1, True
        self.pick('TempSubtitle.en.srt', EN)

    def episode_run(self, content, extra=None):
        actions = {2: self.play_episode, 4: self.open_dialog,
                   6: lambda: self.pick_text('TempSubtitle.hr.srt', content), 7: self.join_job,
                   10: lambda: setattr(STATE, 'abort', True)}
        actions.update(extra or {})
        self.run_service(actions)

    # ------------------------------------------------------------------ frame-rate drift
    def test_frame_rate_drift_fixed_automatically(self):
        STATE.settings['timing_check'] = '0'
        self.episode_run(croatian_episode(scale=25 / 23.976))     # made for the 23.976 fps release
        log = self.log()
        self.assertIn('left alone: TempSubtitle.hr.srt is in hr', log)
        self.assertIn('ratio 0.95904 (sure)', log)
        self.assertEqual([c for c in STATE.dialog_calls if c[0] == 'yesno'], [])
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertTrue(STATE.loaded_subs[0].endswith('Show S01E01 - Pilot synced.hr.srt'), STATE.loaded_subs)
        self.assertIn('Timing fixed: subtitle made for 23.976 fps, the video runs at 25 fps', self.toasts())
        self.assertNotIn('Subtitle is in Croatian - left as it is', self.toasts())
        self.assertEqual(STATE.show_calls, [])                  # subtitles were on: nothing forced
        fixed = core_srt.parse(self.loaded_text())
        self.assertLess(fixed[-1].end, 2520 * 1000)
        self.assertEqual(fixed[0].start, 3000)                # the start hardly moves ...
        self.assertAlmostEqual(fixed[100].start, 603000, delta=2)   # ... and line 101 is back at 10:03
        from resources.lib.dragoman import sync
        rec = sync.load_current()
        self.assertEqual((rec['kind'], rec['lang']), ('original', 'hr'))
        self.assertEqual(self.fake.generate_calls(), [])

    def test_frame_rate_drift_asks_first_by_default(self):
        STATE.yesno_answers = [True]
        self.episode_run(croatian_episode(scale=25 / 23.976))
        asks = [c[1] for c in STATE.dialog_calls if c[0] == 'yesno']
        self.assertEqual(len(asks), 1)
        self.assertTrue(asks[0].startswith('This subtitle seems timed for a 23.976 fps version of the video '
                                           '(this one runs at 25 fps)'), asks)
        self.assertTrue(STATE.loaded_subs[0].endswith('synced.hr.srt'))
        self.assertIn('Timing fixed: subtitle made for 23.976 fps, the video runs at 25 fps', self.toasts())

    def test_no_to_the_frame_rate_fix_is_remembered(self):
        STATE.yesno_answers = [False]
        content = croatian_episode(scale=25 / 23.976)
        self.episode_run(content)
        self.assertEqual(STATE.loaded_subs, [])
        self.assertIn('user kept the timing as it is (remembered for this video and subtitle)', self.log())
        (rec,) = self.store()._load().values()
        self.assertEqual((rec['how'], rec['declined']), ('declined', 0.95904))
        # next time: not asked again, nothing loaded - but still on offer in the subtitle dialog
        self.again()
        self.episode_run(content)
        self.assertEqual([c for c in STATE.dialog_calls if c[0] == 'yesno'], [])
        self.assertEqual(STATE.loaded_subs, [])
        self.assertIn('timing: left as it is (you said no to a frame-rate fix before)', self.log())
        self.assertEqual([o[2] for o in self.offered()], ['sync', 'framerate'])
        path = self.download('framerate')
        self.assertTrue(path.endswith('synced.hr.srt'))
        self.assertEqual(self.store().mapping(list(self.store()._load())[0]), (0.95904, 0.0))

    def test_an_unanswered_timing_question_is_asked_again(self):
        from resources.lib.dragoman import job
        job.CONFIRM_MS = 1000             # in this test the question closes by itself at once
        STATE.yesno_answers = [False]     # ... which Kodi reports as "no"
        content = croatian_episode(scale=25 / 23.976)
        self.episode_run(content)
        self.assertIn('no answer to the timing question - it is asked again next time', self.log())
        self.assertEqual(self.store()._load(), {})
        self.again()
        job.CONFIRM_MS = 20000
        STATE.yesno_answers = [True]
        self.episode_run(content)
        self.assertEqual(len([c for c in STATE.dialog_calls if c[0] == 'yesno']), 1)
        self.assertTrue(STATE.loaded_subs[0].endswith('synced.hr.srt'))

    def test_a_delay_is_left_to_the_sync_assistant(self):
        self.episode_run(croatian_episode(shift_s=150))       # 2.5 minutes late: delay or drift?
        self.assertEqual(STATE.loaded_subs, [])
        self.assertIn('a plain delay could explain it as well', self.log())
        self.assertIn('Subtitle seems out of sync - fix it with the Sync assistant: '
                      'Subtitles > Download > Dragoman', self.toasts())
        self.assertEqual(self.offered(), [('Croatian', 'Sync assistant - press OK when you hear a line', 'sync')])

    def test_unknown_frame_rate_is_only_suggested_and_offered_in_the_subtitle_dialog(self):
        self.run_service({2: lambda: self.play_episode(fps=''), 4: self.open_dialog,
                          6: lambda: self.pick_text('TempSubtitle.hr.srt', croatian_episode(scale=25 / 23.976)),
                          7: self.join_job, 10: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(STATE.loaded_subs, [])
        self.assertIn("not sure: Kodi does not report the video's frame rate", self.log())
        self.assertIn('This subtitle may be timed for a 23.976 fps version - to fix it: '
                      'Subtitles > Download > Dragoman', self.toasts())
        offered = self.offered()
        self.assertEqual([o[0] for o in offered], ['Croatian', 'Croatian'])
        self.assertEqual(offered[1][1:], ('Fix frame rate: made for 23.976 fps, video runs at 25 fps', 'framerate'))
        path = self.download('framerate')
        self.assertTrue(path.endswith('Show S01E01 - Pilot synced.hr.srt'))
        self.assertEqual(self.store().get(list(self.store()._load())[0])['ratio'], 0.95904)
        self.assertIn('Timing fixed: subtitle made for 23.976 fps, the video runs at 25 fps', self.toasts())

    # ------------------------------------------------------------------ Sync assistant
    def test_sync_assistant_menu_then_remembered_then_subtitle_dialog(self):
        from resources.lib.dragoman import script, sync
        import xbmcgui
        # 1) English picked and translated (fake Gemini answers "[sl] ...")
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: self.pick_english, 7: self.join_job,
                          10: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(sync.load_current()['kind'], 'translation')
        first_translation = len(self.fake.generate_calls())

        # 2) Sync assistant from the menu: the user picks line 11 (shown at 0:10) and hears it
        #    2.4 s later; with the default 0.45 s press delay the press comes at 12.85 s.
        original_ok = self.hear_at(12.85)
        try:
            STATE.abort = False
            STATE.actions = {}
            STATE.time = 2.0
            STATE.select_answers = [10]
            script.run(['default.py', 'syncassistant'])
        finally:
            xbmcgui.Dialog.ok = original_ok
        lists = [c for c in STATE.dialog_calls if c[0] == 'select']
        self.assertEqual(lists[-1][1], 'Sync assistant: pick a line you have not heard yet')
        self.assertTrue(lists[-1][2][10].startswith('+0:08   [sl]'), lists[-1][2][10])
        self.assertFalse(STATE.paused)
        key, rec = list(self.store()._load().items())[0]
        self.assertEqual((rec['k'], round(rec['b']), rec['how']), (1.0, 2400, 'offset'))
        self.assertTrue(STATE.loaded_subs[-1].endswith('The Film (2021) AI.sl.srt'))
        self.assertIn('00:00:12,400 --> 00:00:13,300\n[sl]', self.loaded_text())
        self.assertIn('Subtitles now come 2.4 s later', self.toasts())
        self.assertTrue(STATE.window_props.get(10000, {}).get('dragoman.ownload'))

        # 3) Next time: the same English subtitle -> the fix is applied again, translation from cache
        self.again()
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: self.pick_english, 7: self.join_job,
                          10: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(len(self.fake.generate_calls()), first_translation)
        self.assertIn('timing fix from last time: k=1.000000 b=+2.40s (offset)', self.log())
        self.assertTrue(STATE.loaded_subs[0].endswith('The Film (2021) synced.en.srt'))
        self.assertTrue(STATE.loaded_subs[-1].endswith('The Film (2021) AI.sl.srt'))
        self.assertIn('00:00:12,400 --> 00:00:13,300\n[sl]', self.loaded_text())
        self.assertIn('Timing fix from last time applied', self.toasts())

        # 4) Kodi's subtitle dialog: Sync assistant + Undo are offered for the Slovenian translation
        self.assertEqual(self.offered(), [('Slovenian', 'Sync assistant - press OK when you hear a line', 'sync'),
                                          ('Slovenian', 'Undo the timing fix', 'undo')])
        path = self.download('undo')
        self.assertEqual(self.store().get(key)['how'], 'cleared')
        self.assertIsNone(self.store().mapping(key))
        with io.open(path, encoding='utf-8-sig') as handle:
            self.assertIn('00:00:10,000 --> 00:00:10,900\n[sl]', handle.read())
        self.assertIn('Timing fix removed', self.toasts())

        # 5) Kodi copies the file it was handed into its temp folder: not a new subtitle for us
        with open(path, 'rb') as handle:
            handed_over = handle.read()      # Kodi copies it at once (before any restart)
        self.again()
        calls = len(self.fake.generate_calls())
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: write(os.path.join(self.temp, 'TempSubtitle.sl.srt'), handed_over),
                          7: lambda: self.player().onAVChange(),
                          12: lambda: setattr(STATE, 'abort', True)})
        self.assertIn('this is the re-timed subtitle Dragoman just made - nothing to do', self.log())
        self.assertEqual(len(self.fake.generate_calls()), calls)

        # 6) The undo is remembered: next time the English subtitle is shown as it is
        self.again()
        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: self.pick_english, 7: self.join_job,
                          10: lambda: setattr(STATE, 'abort', True)})
        self.assertIn('timing: left as it is (you undid the fix before)', self.log())
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertIn('00:00:10,000 --> 00:00:10,900\n[sl]', self.loaded_text())

    def test_translation_that_arrives_during_the_sync_assistant_is_the_one_re_timed(self):
        from resources.lib.dragoman import script
        import xbmcgui
        self.fake.delay = 1.0
        box = {}

        def assistant():
            STATE.time = 2.0
            STATE.select_answers = [10]
            box['ok'] = self.hear_at(12.85, also=lambda: self._svc.job.join(15))   # the translation lands
            try:
                script.run(['default.py', 'syncassistant'])
            finally:
                xbmcgui.Dialog.ok = box['ok']

        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN),
                          7: lambda: time.sleep(1.2),          # the English original is noted by now
                          8: assistant,
                          30: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        self.assertIn('sync assistant: the translation subtitle arrived meanwhile - re-timing that one', log)
        self.assertTrue(STATE.loaded_subs[-1].endswith('The Film (2021) AI.sl.srt'), STATE.loaded_subs)
        self.assertIn('00:00:12,400 --> 00:00:13,300\n[sl]', self.loaded_text())
        self.assertIn('Subtitles now come 2.4 s later', self.toasts())

    def test_sync_assistant_without_a_subtitle_explains(self):
        from resources.lib.dragoman import script
        STATE.playing = True
        STATE.file = STREAM
        script.run(['default.py', 'syncassistant'])
        oks = [c[1] for c in STATE.dialog_calls if c[0] == 'ok']
        self.assertTrue(oks[-1].startswith('The Sync assistant fixes the subtitle Dragoman handled'), oks)

    def test_an_earlier_play_of_the_same_video_is_not_used(self):
        from resources.lib.dragoman import script

        def stop():
            STATE.playing = False
            self.player().onPlayBackStopped()

        self.run_service({2: self.start_play, 4: self.open_dialog, 6: lambda: self.pick('TempSubtitle.en.srt', EN),
                          7: self.join_job, 10: stop, 12: self.start_play,
                          14: lambda: setattr(STATE, 'abort', True)})
        self.assertEqual(self.offered(), [])
        script.run(['default.py', 'syncassistant'])
        oks = [c[1] for c in STATE.dialog_calls if c[0] == 'ok']
        self.assertTrue(oks[-1].startswith('The Sync assistant fixes the subtitle Dragoman handled'), oks)

    # ------------------------------------------------------------------ subtitles Dragoman does not translate
    def local_film(self, fix=True):
        """Film.2020.mkv on a share with its English subtitle next to it (and a stored fix for it)."""
        import hashlib
        from resources.lib.dragoman import kodi, timing
        share = os.path.join(self.base, 'smb', 'nas', 'Movies')
        write(os.path.join(share, 'Film.2020.mkv'), b'x')
        data = make_srt(sentences(EN)).encode('utf-8')
        write(os.path.join(share, 'Film.2020.en.srt'), data)
        if fix:
            key = timing.fix_key(timing.video_key('Film.2020.mkv', 'The Film (2021)'), hashlib.md5(data).hexdigest())
            timing.FixStore(kodi.profile_dir()).add_point(key, 10000, 12400)
        return share

    def play_local(self, extra=None, until=8):
        actions = {2: lambda: self.start_play('smb://nas/Movies/Film.2020.mkv'), 3: self.join_job,
                   until: lambda: setattr(STATE, 'abort', True)}
        actions.update(extra or {})
        self.run_service(actions)

    def test_do_nothing_user_keeps_what_kodi_shows(self):
        STATE.settings['mode'] = '2'                # "Do nothing"
        self.local_film()
        STATE.subs_visible = False                  # subtitles are off
        self.play_local()
        self.assertEqual(STATE.loaded_subs, [])
        self.assertIn('timing fix not loaded: Kodi is not showing this subtitle', self.log())
        self.assertEqual(self.fake.generate_calls(), [])
        # subtitles on, Kodi showing the English one: the re-timed copy replaces it, nothing forced
        self.again()
        STATE.subs_visible, STATE.cur_sub, STATE.subs, STATE.cur_index = True, 'eng', ['eng'], 0
        self.play_local()
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertTrue(STATE.loaded_subs[0].endswith('The Film (2021) synced.en.srt'))
        self.assertIn('00:00:12,400 --> 00:00:13,300', self.loaded_text())
        self.assertEqual(STATE.show_calls, [])
        self.assertIn('Kodi switched to the re-timed subtitle', self.log())

    def test_local_left_alone_subtitle_is_quiet_and_its_fix_applied(self):
        import hashlib
        from resources.lib.dragoman import kodi, timing
        share = os.path.join(self.base, 'smb', 'nas', 'Movies')
        write(os.path.join(share, 'Film.2020.mkv'), b'x')
        data = make_srt(sentences(SR)).encode('utf-8')
        write(os.path.join(share, 'Film.2020.srt'), data)        # no language in the name: the text says Serbian
        STATE.subs_visible, STATE.cur_sub, STATE.subs, STATE.cur_index = True, 'hrv', ['hrv'], 0   # Kodi says Croatian
        self.play_local()
        self.assertIn('a local subtitle is already in a left-alone language - not translating', self.log())
        self.assertEqual(self.toasts(), [])          # played often: no "left as it is" notice every time
        self.assertEqual(STATE.loaded_subs, [])
        # with a fix stored for it, the re-timed copy replaces it (Kodi's "Croatian" is the same subtitle)
        key = timing.fix_key(timing.video_key('Film.2020.mkv', 'The Film (2021)'), hashlib.md5(data).hexdigest())
        timing.FixStore(kodi.profile_dir()).add_point(key, 10000, 12400)
        self.again()
        STATE.subs_visible, STATE.cur_sub, STATE.subs, STATE.cur_index = True, 'hrv', ['hrv'], 0
        self.play_local()
        self.assertEqual(len(STATE.loaded_subs), 1)
        self.assertTrue(STATE.loaded_subs[0].endswith('The Film (2021) synced.sr.srt'), STATE.loaded_subs)
        self.assertIn('Timing fix from last time applied', self.toasts())
        self.assertEqual(self.fake.generate_calls(), [])

    def test_kodi_that_cannot_open_the_re_timed_copy_is_noticed(self):
        STATE.settings['mode'] = '2'
        self.local_film()
        STATE.subs_visible, STATE.cur_sub, STATE.subs, STATE.cur_index = True, 'eng', ['eng'], 0
        STATE.reject_subs = lambda path: (os.sep + 'temp' + os.sep) in path    # same language: the track number tells
        self.play_local()
        log = self.log()
        self.assertIn('Kodi did not switch to the re-timed subtitle (attempt 1 of 2)', log)
        self.assertEqual(len(STATE.loaded_subs), 2)
        self.assertEqual(STATE.cur_index, 2)

    def test_subtitle_with_translation_off_does_not_stop_a_translation(self):
        STATE.settings['src_auto'] = 'false'
        self.fake.delay = 1.0

        def a4k_auto_download():
            write(os.path.join(self.a4k, 'Other.Release.eng.srt'),
                  make_srt(list(reversed(sentences(EN)))).encode('utf-8'))      # another English subtitle
            self.player().onAVChange()

        self.run_service({2: self.start_play, 4: self.open_dialog,
                          6: lambda: self.pick('TempSubtitle.en.srt', EN),
                          70: a4k_auto_download, 71: self.join_job,
                          74: lambda: setattr(STATE, 'abort', True)})
        log = self.log()
        self.assertIn('Other.Release.eng.srt [addon:service.subtitles.a4ksubtitles] SRT -> auto', log)
        self.assertIn('a translation is in progress - leaving this subtitle alone', log)
        self.assertEqual(self._svc.job.outcome, 'loaded')
        self.assertTrue(STATE.loaded_subs[-1].endswith('The Film (2021) AI.sl.srt'))

    def test_re_timed_copy_kept_next_to_the_video_is_recognised(self):
        """Kodi's subtitle dialog stores the file it is handed next to a local video, in place
        of Film.2020.en.srt: next time Dragoman still knows the original timing."""
        STATE.settings['mode'] = '2'
        share = self.local_film(fix=False)
        STATE.subs_visible, STATE.cur_sub, STATE.subs, STATE.cur_index = True, 'eng', ['eng'], 0
        import xbmcgui
        box = {}

        def subtitle_dialog_sync():
            STATE.time, STATE.select_answers = 2.0, [10]
            box['ok'] = self.hear_at(12.85)
            try:
                box['path'] = self.download('sync', handle='9')
            finally:
                xbmcgui.Dialog.ok = box['ok']
            shutil.copyfile(box['path'], os.path.join(share, 'Film.2020.en.srt'))     # Kodi: "next to the video"

        self.play_local({5: subtitle_dialog_sync}, until=12)
        self.assertTrue(box['path'])
        self.assertIn('this is the re-timed subtitle Dragoman just made - nothing to do', self.log())

        # Kodi restarts (window properties are gone) and the film is played again
        self.again()
        STATE.window_props = {}
        STATE.subs_visible, STATE.cur_sub, STATE.subs, STATE.cur_index = True, 'eng', ['eng'], 0
        self.play_local()
        log = self.log()
        self.assertIn('Film.2020.en.srt is a re-timed copy Dragoman made earlier (original, k=1.000000 b=+2.40s)', log)
        self.assertIn('timing fix from last time: k=1.000000 b=+2.40s (offset)', log)
        self.assertEqual(STATE.loaded_subs, [])        # what Kodi shows is timed like that already
        self.assertEqual([o[2] for o in self.offered()], ['sync', 'undo'])
        path = self.download('undo')
        with io.open(path, encoding='utf-8-sig') as handle:
            self.assertIn('00:00:10,000 --> 00:00:10,900', handle.read())     # the original timing again

    # ------------------------------------------------------------------ add-on structure
    def test_addon_xml_lists_the_subtitle_module_first(self):
        # Kodi runs plugin:// calls (its subtitle dialog) with the library of the FIRST
        # extension point; with the service first, the dialog would start a second service.
        import xml.etree.ElementTree as ET
        root = ET.parse(os.path.join(ADDON, 'addon.xml')).getroot()
        points = [(e.get('point'), e.get('library')) for e in root.findall('extension')
                  if e.get('point') != 'xbmc.addon.metadata']
        self.assertEqual(points[0], ('xbmc.subtitle.module', 'subtitle.py'))
        self.assertIn(('xbmc.service', 'service.py'), points)
        self.assertIn(('xbmc.python.script', 'default.py'), points)

    def test_a_plugin_call_that_reaches_service_py_does_not_start_a_service(self):
        path = os.path.join(ADDON, 'service.py')
        with io.open(path, encoding='utf-8') as handle:
            code = compile(handle.read(), path, 'exec')
        argv = sys.argv
        sys.argv = ['plugin://service.subtitles.dragoman/', '5', '?action=search&languages=English']
        STATE.abort = True            # should a service start anyway, it stops at once
        try:
            exec(code, {'__name__': '__main__', '__file__': path})     # like Kodi: argv[0] stays the plugin URL
        finally:
            sys.argv = argv
        self.assertEqual(STATE.plugin_ended, [(5, True)])
        self.assertNotIn('service started', self.log())


if __name__ == '__main__':
    unittest.main()
