# -*- coding: utf-8 -*-
"""Tests for the Kodi-free core: SRT, language detection, Gemini client, translator, cache."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(os.path.dirname(HERE), 'service.subtitles.dragoman')
sys.path.insert(0, ADDON)

from resources.lib.dragoman import cache, gemini, langdetect, languages, srt, translator  # noqa: E402
from resources.lib.dragoman.gemini import GeminiError  # noqa: E402

EN = (u"Where were you last night?\nI was at home. What's wrong with you?\nYou know what I mean. "
      u"I don't want to go there again, okay? It's just not right. We can talk about it tomorrow. "
      u"Yes, I know. Come on, let's go. There is nothing here for us. What do you want from me? ") * 6
SL = (u"Kje si bil sinoči? Doma sem bil. Kaj je narobe s tabo? Saj veš, kaj mislim. Nočem več iti tja, "
      u"prav? To pač ni prav. Lahko se pogovoriva jutri. Ja, vem. Gremo, pridi. Tukaj ni nič za naju. "
      u"Kaj hočeš od mene? Zakaj si tako zelo jezen? Tudi jaz sem utrujen.") * 6
HR = (u"Gdje si bio sinoć? Bio sam kod kuće. Što ti je? Znaš na što mislim. Ne želim opet ići tamo, "
      u"u redu? To jednostavno nije u redu. Možemo razgovarati sutra. Da, znam. Hajde, idemo. Ovdje "
      u"nema ništa za nas. Što hoćeš od mene? Tko je to bio? Točno tako, uvijek isto.") * 6
SR = (u"Gde si bio sinoć? Bio sam kod kuće. Šta ti je? Znaš na šta mislim. Ne želim opet da idem tamo, "
      u"u redu? To jednostavno nije u redu. Možemo da razgovaramo sutra. Da, znam. Hajde, idemo. Ovde "
      u"nema ništa za nas. Šta hoćeš od mene? Ko je to bio? Tačno tako, uvek isto. Lepo vreme.") * 6
BS = (u"Gdje si bio sinoć? Bio sam kod kuće. Šta ti je? Znaš na šta mislim. Ne želim opet ići tamo, "
      u"u redu? To jednostavno nije u redu. Možemo razgovarati sutra. Da, znam. Hajde, idemo. Ovdje "
      u"nema ništa za nas. Šta hoćeš od mene? Ko je to bio? Tačno tako, uvijek isto. Lijepo vrijeme.") * 6
# TV-style English with words that are also Croatian/Serbian ("sad", "ja", "ko", "ceo", "pre", "i", "a")
EN_TV = (u"Agent Lisbon, we have a body. I'm sad to say we found him in the lake. Who found him? "
         u"A jogger. Cho, talk to the jogger. Rigsby, check the car. Jane, where are you going? "
         u"He was the CEO of a tech company. Pre-tax profits were huge. Ja, ja, very funny. "
         u"He was knocked out, K.O., before he went in the water. I know it was murder. "
         u"Where were you last night? At home. Alone? Yes. I mean, no. My sister was with me. "
         u"I loved him. I know you did. That's why you're so angry with him. ") * 8
SR_CYR = (u"Где си био синоћ? Био сам код куће. Шта ти је? Знаш на шта мислим. Ђорђе, хоћеш ли кафу? "
          u"Ћао, видимо се сутра. Љубав је лепа ствар. Њега нема овде.") * 6


def make_srt(texts, crlf=True):
    parts = []
    for i, t in enumerate(texts):
        parts.append('%d\n00:00:%02d,000 --> 00:00:%02d,900\n%s\n' % (i + 1, i % 60, i % 60, t))
    s = '\n'.join(parts)
    return s.replace('\n', '\r\n') if crlf else s


# Fake keys are built from two parts so secret scanners do not mistake them for real ones.
G = 'AI' + 'za'


class SrtTests(unittest.TestCase):
    def test_parse_sloppy(self):
        text = (u'﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i>\r\nthere\r\n\r\n\r\n'
                u'00:00:03.000 --> 00:00:04.000 X1:10 X2:20\r\n- Hi\r\n- Bye\r\n'
                u'3\r\n00:01:00,5 --> 00:01:02,000\r\n42\r\n')
        cues = srt.parse(text)
        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[0].text, '<i>Hello</i>\nthere')
        self.assertEqual(cues[1].start, 3000)
        self.assertEqual(cues[1].text, '- Hi\n- Bye')
        self.assertEqual(cues[2].start, 60500)
        self.assertEqual(cues[2].text, '42')
        out = srt.compose(cues, ['A', 'B\n\nC', ''])
        self.assertIn('1\n00:00:01,000 --> 00:00:02,500\nA\n', out)
        self.assertIn('B\nC', out)
        self.assertEqual(len(srt.parse(out)), 3)

    def test_decode_cp1250_vs_cp1252(self):
        sl_bytes = make_srt(SL.split('. ')).encode('cp1250')
        text, enc = srt.decode(sl_bytes)
        self.assertEqual(enc, 'cp1250')
        self.assertIn(u'Tudi jaz sem utrujen', text)
        self.assertIn(u'sinoči', text)
        fr = (u"C'est très bien, je suis là. Où est le café? " * 30).encode('cp1252')
        self.assertEqual(srt.decode(fr)[1], 'cp1252')
        cyr = SR_CYR.encode('cp1251')
        text, enc = srt.decode(cyr)
        self.assertEqual(enc, 'cp1251')
        self.assertIn(u'Ђорђе', text)
        self.assertEqual(srt.decode(sl_bytes, 'CP1250')[1], 'cp1250')
        self.assertEqual(srt.decode(u'x č'.encode('utf-8'))[1], 'utf-8')


class LangTests(unittest.TestCase):
    def test_detect(self):
        d = langdetect.detect_text
        self.assertEqual(d(EN)[0], 'en')
        self.assertEqual(d(SL)[0], 'sl')
        self.assertEqual(d(HR)[0], 'hr')
        self.assertEqual(d(SR)[0], 'sr')
        self.assertEqual(d(BS)[0], 'bs')
        self.assertEqual(d(SR_CYR)[0], 'sr-Cyrl')
        self.assertEqual(d(u'Привет, как дела? Это было хорошо. Мы были там. ' * 5)[0], 'ru')
        self.assertEqual(d(u'Ich weiß nicht, was du willst. Das ist gut, danke. Wir sind hier. ' * 5)[0], 'de')

    def test_classify(self):
        c = langdetect.classify
        self.assertEqual(c('TempSubtitle.sl.srt', SL)[0], 'sl')
        self.assertEqual(c('TempSubtitle.sl.srt', SL)[1], 'file name')
        self.assertEqual(c('TempSubtitle.sr.srt', SR_CYR)[0], 'sr-Cyrl')
        self.assertEqual(c('Movie.srb.cyr.srt', SR)[0], 'sr')  # srb unknown -> text says sr
        code, how = c('TempSubtitle.en.srt', SL)  # wrong name, text wins
        self.assertEqual(code, 'sl')
        self.assertIn('name said en', how)
        self.assertEqual(c('TempSubtitle.hr.srt', SR)[0], 'hr')  # same cluster: trust the name
        self.assertEqual(c('movie.srt', EN)[0], 'en')

    def test_english_with_croatian_looking_words_stays_english(self):
        # The user's TV: English a4k downloads (".eng") were taken for Croatian/Serbian and left alone.
        self.assertEqual(langdetect.detect_text(EN_TV)[0], 'en')
        self.assertEqual(langdetect.classify('The.Mentalist.S03E03.WEBRip.x264-ION10.eng', EN_TV),
                         ('en', 'file name'))
        self.assertEqual(langdetect.classify('TempSubtitle.en.srt', EN_TV)[0], 'en')
        # a subtitle really in another language than its name still gets caught
        code, how = langdetect.classify('The.Mentalist.S03E03.eng', HR)
        self.assertEqual(code, 'hr')
        self.assertIn('name said en', how)
        self.assertEqual(langdetect.classify('Film.de.srt', SL)[0], 'sl')

    def test_languages(self):
        self.assertEqual(languages.normalize('Slovenian'), 'sl')
        self.assertEqual(languages.normalize('Serbian (Cyrillic)'), 'sr-Cyrl')
        self.assertEqual(languages.normalize('original'), '')
        self.assertEqual(languages.from_kodi_ui_language('resource.language.sl_si'), 'sl')
        self.assertEqual(languages.from_kodi_ui_language('resource.language.pt_br'), 'pt-BR')
        self.assertTrue(languages.matches('sr', ['hr', 'sr', 'bs']))
        self.assertFalse(languages.matches('sr-Cyrl', ['hr', 'sr', 'bs']))
        self.assertEqual(languages.parse_code_list('hr, sr,xx,sr-Cyrl'), ['hr', 'sr', 'sr-Cyrl'])
        codes = [row[0] for row in languages.LANGUAGES]
        self.assertEqual(len(codes), len(set(codes)))
        ids = [row[3] for row in languages.LANGUAGES]
        self.assertEqual(ids, list(range(32501, 32501 + len(ids))))


class FakeClient(object):
    """Scripted Gemini stand-in. script: list of callables or exceptions consumed per call."""

    def __init__(self, model, script=None, calls=None):
        self.model = model
        self.script = script if script is not None else []
        self.calls = calls if calls is not None else []
        self.last_usage = {'_secs': 0.1}

    def generate_json(self, system, user, schema):
        payload = json.loads(user)
        self.calls.append((self.model, payload))
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            if callable(step):
                return step(payload)
        return json.dumps({'cues': [{'id': c['id'], 'text': 'SL:' + c['text']} for c in payload['cues']]})


class TranslatorTests(unittest.TestCase):
    def make(self, script=None, models=('m1', 'm2'), chunk=10, **kw):
        calls = []
        clients = {}

        def factory(model):
            clients[model] = FakeClient(model, script if model == models[0] else [], calls)
            return clients[model]
        waits = []
        now = [1000.0]

        def wait(seconds):
            waits.append(seconds)
            now[0] += seconds
            return False
        tr = translator.Translator(factory, list(models), 'sl', title='Film', plot='Plot', chunk_size=chunk,
                                   rpm=600, wait=wait, clock=lambda: now[0], **kw)
        return tr, calls, waits

    def test_happy_path_and_context(self):
        texts = ['line %d' % i for i in range(25)] + ['']
        tr, calls, _w = self.make()
        out, failed = tr.translate(texts)
        self.assertEqual(failed, 0)
        self.assertEqual(out[0], 'SL:line 0')
        self.assertEqual(out[25], '')
        self.assertEqual(len(calls), 3)
        self.assertNotIn('context_before', calls[0][1])
        self.assertEqual(calls[1][1]['context_before'][-1], 'SL:line 9')
        self.assertEqual(calls[0][1]['title'], 'Film')

    def test_bad_answer_retry_then_split(self):
        bad = lambda p: json.dumps({'cues': [{'id': 1, 'text': 'x'}]})  # noqa: E731
        script = [bad, bad]  # first chunk of 10 fails twice -> split into 5 + 5 -> ok
        tr, calls, _w = self.make(script)
        out, failed = tr.translate(['line %d' % i for i in range(10)])
        self.assertEqual(failed, 0)
        self.assertEqual(tr.splits, 1)
        self.assertEqual([len(c[1]['cues']) for c in calls], [10, 10, 5, 5])

    def test_safety_block_splits_down_and_keeps_original(self):
        blocked = GeminiError('blocked', 'finishReason PROHIBITED_CONTENT')
        script = [blocked, blocked, blocked, blocked, blocked]
        tr, calls, _w = self.make(script, chunk=10)
        out, failed = tr.translate(['line %d' % i for i in range(10)])
        # 10 -> 5+5; each 5-cue half fails once and is kept original (no retry for blocks)
        self.assertEqual(failed, 10)
        self.assertEqual(out[0], 'line 0')

    def test_rate_limit_waits_and_quota_switches_model(self):
        script = [GeminiError('rate', 'slow down', retry_after=34.0),
                  GeminiError('quota_day', 'daily')]
        switched = []
        tr, calls, waits = self.make(script, on_model_switch=switched.append)
        out, failed = tr.translate(['a %d' % i for i in range(5)])
        self.assertEqual(failed, 0)
        self.assertIn(34.0, [round(w, 1) for w in waits])
        self.assertEqual(switched, ['m2'])
        self.assertEqual(calls[-1][0], 'm2')

    def test_quota_without_fallback_stops(self):
        tr, _c, _w = self.make([GeminiError('quota_day', 'daily')], fallback=False)
        with self.assertRaises(translator.StopJob) as ctx:
            tr.translate(['a'])
        self.assertEqual(ctx.exception.kind, 'quota')

    def test_auth_stops(self):
        tr, _c, _w = self.make([GeminiError('auth', 'API key not valid')])
        with self.assertRaises(translator.StopJob) as ctx:
            tr.translate(['a'])
        self.assertEqual(ctx.exception.kind, 'auth')

    def test_untranslated_answer_rejected(self):
        same = lambda p: json.dumps({'cues': p['cues']})  # noqa: E731
        tr, calls, _w = self.make([same])
        out, failed = tr.translate(['line %d' % i for i in range(10)])
        self.assertEqual(failed, 0)
        self.assertEqual(len(calls), 2)

    def test_cached_chunks_and_cancel(self):
        tr, calls, _w = self.make()
        chunks = {}
        out, _f = tr.translate(['x %d' % i for i in range(20)], {'0': ['C%d' % i for i in range(10)]},
                               on_chunk=lambda i, c: chunks.__setitem__(i, c))
        self.assertEqual(out[0], 'C0')
        self.assertEqual(len(calls), 1)
        self.assertIn(1, chunks)
        tr2, _c, _w = self.make(is_cancelled=lambda: True)
        with self.assertRaises(translator.Cancelled):
            tr2.translate(['a'])

    def test_prompt_mentions_target_rules(self):
        self.assertIn('Slovenian', translator.system_prompt('sl'))
        self.assertIn('Cyrillic', translator.system_prompt('sr-Cyrl'))


class GeminiTests(unittest.TestCase):
    def setUp(self):
        gemini.reset_session_cache()

    def opener(self, responses, seen):
        def _transport(client, method, url, body, headers):
            seen.append((url, json.loads(body.decode('utf-8')) if body else None, dict(headers)))
            step = responses.pop(0)
            if isinstance(step, Exception):
                raise step
            code, data = step
            return code, json.dumps(data).encode('utf-8')
        return _transport

    def ok(self, text, finish='STOP'):
        return (200, {'candidates': [{'content': {'parts': [{'text': 'thinking...', 'thought': True},
                                                             {'text': text}]},
                                      'finishReason': finish}],
                      'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 5}})

    def test_request_shape_and_ladder(self):
        seen = []
        bad = (400, {'error': {'code': 400, 'status': 'INVALID_ARGUMENT', 'message': 'thinking level not supported'}})
        client = gemini.GeminiClient('AIzaKEY', 'gemini-3.5-flash-lite',
                                     transport=self.opener([bad, self.ok('{"cues": []}')], seen))
        self.assertEqual(client.generate_json('sys', 'user', {'type': 'OBJECT'}), '{"cues": []}')
        url, body, headers = seen[0]
        self.assertTrue(url.endswith('models/gemini-3.5-flash-lite:generateContent'))
        self.assertEqual(headers.get('x-goog-api-key'), 'AIzaKEY')
        self.assertEqual(body['generationConfig']['thinkingConfig'], {'thinkingLevel': 'minimal'})
        self.assertNotIn('temperature', body['generationConfig'])
        self.assertNotIn('safetySettings', body)
        self.assertEqual(seen[1][1]['generationConfig']['thinkingConfig'], {'thinkingLevel': 'low'})
        # the ladder position is remembered for the next request
        client._transport = self.opener([self.ok('{}')], seen)
        client.generate_json('s', 'u', {})
        self.assertEqual(seen[2][1]['generationConfig']['thinkingConfig'], {'thinkingLevel': 'low'})

    def test_errors(self):
        quota = {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED', 'message': 'Quota exceeded',
                           'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                                        'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]},
                                       {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '41s'}]}}
        e = gemini.parse_error(429, json.dumps(quota))
        self.assertEqual((e.kind, e.retry_after), ('quota_day', 41.0))
        quota['error']['details'][0]['violations'][0]['quotaId'] = 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'
        self.assertEqual(gemini.parse_error(429, json.dumps(quota)).kind, 'rate')
        key = {'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.',
                         'details': [{'@type': 'type.googleapis.com/google.rpc.ErrorInfo', 'reason': 'API_KEY_INVALID'}]}}
        self.assertEqual(gemini.parse_error(400, json.dumps(key)).kind, 'auth')
        self.assertEqual(gemini.parse_error(400, json.dumps(
            {'error': {'message': 'User location is not supported for the API use.'}})).kind, 'region')
        self.assertEqual(gemini.parse_error(404, '{}').kind, 'model')
        self.assertEqual(gemini.parse_error(503, 'x').kind, 'server')

    def test_blocked_and_max_tokens(self):
        seen = []
        client = gemini.GeminiClient('k', 'gemini-3.6-flash', transport=self.opener([
            (200, {'promptFeedback': {'blockReason': 'PROHIBITED_CONTENT'}}),
            self.ok('{"cues"', finish='MAX_TOKENS'),
            self.ok('{}', finish='SAFETY')], seen))
        for kind in ('blocked', 'too_long', 'blocked'):
            with self.assertRaises(GeminiError) as ctx:
                client.generate_json('s', 'u', {})
            self.assertEqual(ctx.exception.kind, kind)

    def test_auth_error_is_not_retried_with_other_variants(self):
        seen = []
        key = (400, {'error': {'code': 400, 'message': 'API key not valid.'}})
        client = gemini.GeminiClient('k', 'gemini-3.6-flash', transport=self.opener([key], seen))
        with self.assertRaises(GeminiError) as ctx:
            client.generate_json('s', 'u', {})
        self.assertEqual(ctx.exception.kind, 'auth')
        self.assertEqual(len(seen), 1)


class ReviewFixTests(unittest.TestCase):
    """Regression tests for the independent review findings."""

    def setUp(self):
        gemini.reset_session_cache()

    def test_unrelated_400_is_not_retried_and_stops_the_job(self):
        seen = []

        def transport(client, method, url, body, headers):
            seen.append(url)
            return 400, json.dumps({'error': {'code': 400, 'message': 'Developer instruction is not enabled for models/gemma-9'}}).encode('utf-8')
        client = gemini.GeminiClient('k', 'gemma-9', transport=transport)
        with self.assertRaises(GeminiError) as ctx:
            client.generate_json('s', 'u', {})
        self.assertEqual(ctx.exception.kind, 'bad_request')
        self.assertEqual(len(seen), 1)
        tr = translator.Translator(lambda m: gemini.GeminiClient('k', m, transport=transport), ['gemma-9'], 'sl',
                                   rpm=600, wait=lambda s: False)
        with self.assertRaises(translator.StopJob) as ctx:
            tr.translate(['line %d' % i for i in range(100)])
        self.assertEqual(ctx.exception.kind, 'bad_request')
        self.assertEqual(len(seen), 2)   # one more call, then stop - no storm

    def test_config_400_walks_the_ladder(self):
        seen = []
        answers = [(400, {'error': {'code': 400, 'message': 'Invalid JSON payload received. Unknown name "thinkingLevel"'}}),
                   (400, {'error': {'code': 400, 'message': 'thinking level low is not supported'}}),
                   (200, {'candidates': [{'content': {'parts': [{'text': '{}'}]}, 'finishReason': 'STOP'}]})]

        def transport(client, method, url, body, headers):
            seen.append(json.loads(body.decode('utf-8'))['generationConfig'])
            code, data = answers.pop(0)
            return code, json.dumps(data).encode('utf-8')
        client = gemini.GeminiClient('k', 'gemini-9-flash', transport=transport)
        client.generate_json('s', 'u', {'type': 'OBJECT'})
        self.assertEqual([c.get('thinkingConfig') for c in seen],
                         [{'thinkingLevel': 'minimal'}, {'thinkingLevel': 'low'}, None])

    def test_close_languages_accepted_after_one_retry(self):
        calls = []

        class Same(object):
            last_usage = {}

            def generate_json(self, system, user, schema):
                cues = json.loads(user)['cues']
                calls.append(len(cues))
                return json.dumps({'cues': cues})
        tr = translator.Translator(lambda m: Same(), ['m'], 'hr', rpm=600, wait=lambda s: False)
        out, failed = tr.translate(['Gdje si bio %d?' % i for i in range(100)])
        self.assertEqual((calls, failed, tr.splits), ([100, 100], 0, 0))
        calls[:] = []
        tr = translator.Translator(lambda m: Same(), ['m'], 'hr', rpm=600, wait=lambda s: False,
                                   check_unchanged=False)
        tr.translate(['Gdje si bio %d?' % i for i in range(100)])
        self.assertEqual(calls, [100])

    def test_dropped_connection_is_a_network_error_and_abort_cancels(self):
        import http.client

        def broken(client, method, url, body, headers):
            raise http.client.IncompleteRead(b'{"cand')
        client = gemini.GeminiClient('k', 'gemini-3.6-flash', transport=broken)
        with self.assertRaises(GeminiError) as ctx:
            client.generate_json('s', 'u', {})
        self.assertEqual(ctx.exception.kind, 'network')

        def slow(client, method, url, body, headers):
            client.abort()             # what cancel() does from another thread
            raise OSError('socket shut down')
        client = gemini.GeminiClient('k', 'gemini-3.6-flash', transport=slow)
        tr = translator.Translator(lambda m: client, ['gemini-3.6-flash'], 'sl', rpm=600, wait=lambda s: False)
        with self.assertRaises(translator.Cancelled):
            tr.translate(['a', 'b'])

    def test_numeric_last_line_kept(self):
        text = ('00:00:01,000 --> 00:00:02,000\nThe year was\n1984\n'
                '00:00:03,000 --> 00:00:04,000\n3\n'
                '00:00:05,000 --> 00:00:06,000\nEnd\n')
        cues = srt.parse(text)
        self.assertEqual([c.text for c in cues], ['The year was\n1984', '3', 'End'])
        text = '1\n00:00:01,000 --> 00:00:02,000\nHi\n2\n00:00:03,000 --> 00:00:04,000\nBye\n'
        self.assertEqual([c.text for c in srt.parse(text)], ['Hi', 'Bye'])

    def test_same_group(self):
        self.assertTrue(langdetect.same_group('bs', 'hr'))
        self.assertTrue(langdetect.same_group('pt-BR', 'pt'))
        self.assertFalse(langdetect.same_group('sl', 'hr'))
        self.assertFalse(langdetect.same_group('', 'hr'))


class A4kNamingTests(unittest.TestCase):
    """a4kSubtitles saves some downloads as '<release>.eng' and Kodi copies them to
    special://temp/TempSubtitle.en.eng (seen on the user's Kodi 21.2)."""

    def test_name_kind_and_language(self):
        from resources.lib.dragoman import util
        self.assertEqual(util.name_kind('TempSubtitle.en.eng'), 'maybe')
        self.assertEqual(util.name_kind('Runner.2026.1080p.AMZN.WEB-DL.DDP5.1.H264.eng'), 'maybe')
        self.assertEqual(util.name_kind('Movie.en.srt'), 'sub')
        self.assertEqual(util.name_kind('sub.zip'), 'archive')
        self.assertIsNone(util.name_kind('kodi.log'))
        self.assertIsNone(util.name_kind('cache.json'))
        self.assertEqual(util.guess_lang('TempSubtitle.en.eng'), 'en')
        self.assertEqual(util.guess_lang('Runner.2026.1080p.AMZN.WEB-DL.DDP5.1.H264.eng'), 'en')
        self.assertEqual(util.guess_lang('TempSubtitle.sr.srp'), 'sr')
        self.assertEqual(util.guess_lang('Movie.2020.1080p.srt'), '')
        self.assertEqual(langdetect.classify('TempSubtitle.en.eng', EN)[0], 'en')

    def test_sniff(self):
        from resources.lib.dragoman import util
        self.assertEqual(util.sniff_format(make_srt(['Hi', 'there']).encode('utf-8')), 'srt')
        self.assertEqual(util.sniff_format(b'\xef\xbb\xbf1\r\n00:00:01,000 --> 00:00:02,000\r\nHi'), 'srt')
        self.assertEqual(util.sniff_format(b'WEBVTT\n\n00:01.000 --> 00:02.000\nHi'), 'vtt')
        self.assertEqual(util.sniff_format(b'[Script Info]\nTitle: x\n'), 'ass')
        self.assertEqual(util.sniff_format(b'{"not": "a subtitle"}'), '')

    def test_vtt_parse(self):
        text = (u'WEBVTT\nKind: captions\n\nNOTE made by hand\n\nintro\n00:01.000 --> 00:03.500 align:start\n'
                u'<v Anna>Hello <c.yellow>there</c></v>\n<i>friend</i>\n\nNOTE between cues\n\n'
                u'01:02:03.000 --> 01:02:05.250\nBye <00:01:02.500>now\n')
        cues = srt.parse(text)
        self.assertEqual([c.text for c in cues], ['Hello there\n<i>friend</i>', 'Bye now'])
        self.assertEqual((cues[0].start, cues[0].end, cues[1].start), (1000, 3500, 3723000))


class OutputNameTests(unittest.TestCase):
    def test_safe_name(self):
        import sys as _s
        _s.path.insert(0, os.path.join(HERE, 'fakekodi'))
        from resources.lib.dragoman import job
        self.assertEqual(job.safe_name('Runner (2026)'), 'Runner (2026)')
        self.assertEqual(job.safe_name(u'Čuvar: Početak / Part+2'), 'Cuvar Pocetak Part 2')
        self.assertEqual(job.safe_name('Runner.2026.1080p.WEBRip.DD+5.1.HEVC.KIN'), 'Runner 2026 1080p WEBRip DD 5 1 HEVC KIN')
        self.assertEqual(job.safe_name(u'日本語'), '')


class CacheTests(unittest.TestCase):
    def test_roundtrip_and_purge(self):
        folder = tempfile.mkdtemp()
        try:
            c = cache.Cache(os.path.join(folder, 'cache'))
            k = c.key(['a', 'b'], 'sl', 'm', 100)
            self.assertNotEqual(k, c.key(['a', 'b'], 'hr', 'm', 100))
            self.assertEqual(c.load(k), {})
            c.save(k, {'complete': True, 'texts': [u'č', 'b']})
            self.assertEqual(c.load(k)['texts'][0], u'č')
            self.assertEqual(c.purge(30), 0)
            self.assertEqual(c.clear(), 1)
        finally:
            shutil.rmtree(folder)


class KeySourceTests(unittest.TestCase):
    KEY = G + 'SyZYXWVUTSRQPONMLKJIHGFEDCBA9876543'

    def test_addresses(self):
        from resources.lib.dragoman import keysource as k
        self.assertEqual(k.candidates('pastebin.com/AbCd1234'),
                         ['https://pastebin.com/raw/AbCd1234', 'https://pastebin.com/AbCd1234'])
        self.assertEqual(k.candidates(' https://pastebin.com/raw/AbCd1234 '), ['https://pastebin.com/raw/AbCd1234'])
        self.assertEqual(k.candidates('https://paste.kodi.tv/ilokuqeqan.kodi')[0], 'https://paste.kodi.tv/raw/ilokuqeqan')
        self.assertEqual(k.candidates('dineout.si/kodi/key.txt'), ['https://dineout.si/kodi/key.txt'])
        self.assertEqual(k.candidates('https://www.dropbox.com/s/abc/key.txt?dl=0')[0],
                         'https://www.dropbox.com/s/abc/key.txt?dl=1')
        self.assertEqual(k.candidates('https://drive.google.com/file/d/1AbC_d-E/view?usp=sharing')[0],
                         'https://drive.google.com/uc?export=download&id=1AbC_d-E')
        for bad in ('', 'pastebin.com', 'pastebin.com/', 'https://', 'foo bar', 'localhost/key'):
            self.assertEqual(k.candidates(bad), [], bad)

    def test_key_shapes(self):
        from resources.lib.dragoman import keysource as k
        self.assertTrue(k.looks_like_gemini_key(self.KEY))
        self.assertFalse(k.looks_like_gemini_key(self.KEY + 'x'))
        self.assertFalse(k.looks_like_gemini_key('sk-abc'))
        self.assertEqual(k.find_key('\ufeffkey = "%s";' % self.KEY), self.KEY)
        html = '<head><script>a="' + G + 'SyOTHERxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"</script></head><p>x</p>'
        self.assertNotIn(G + 'SyOTHER', k.visible_text(html))
        # a '>' inside an attribute, a key split by highlighting tags, entities
        html = ('<div data-x="a>b" data-key="' + G + 'SyOTHERxxxxxxxxxxxxxxxxxxxxxxxxxxxxx">'
                '<span class="hl">%s</span><span>%s</span></div>&#65;&amp;' % (self.KEY[:10], self.KEY[10:]))
        self.assertEqual(k.keys_in(html, True), [self.KEY])
        self.assertTrue(k.visible_text(html).endswith('A&'))
        self.assertEqual(k.find_keys('%s-thanks' % self.KEY), [self.KEY, self.KEY + '-thanks'])
        self.assertEqual(k.decode(self.KEY.encode('utf-16-le')), self.KEY)
        self.assertEqual(k.candidates('https://[a.b/x'), [])
        self.assertEqual(k.candidates('pastebin.com/AbCd1234?x=1')[0], 'https://pastebin.com/raw/AbCd1234')

    def test_broken_connection_is_a_fetch_error(self):
        import http.client
        from resources.lib.dragoman import keysource as k
        original = k.urlopen

        def broken(*args, **kwargs):
            raise http.client.IncompleteRead(b'')
        k.urlopen = broken
        try:
            with self.assertRaises(k.FetchError) as caught:
                k.http_get('https://pastebin.com/raw/AbCd1234', 'test')
            self.assertEqual(caught.exception.kind, 'network')
        finally:
            k.urlopen = original

    def test_real_http_fetch(self):
        """http_get against a local server: charset, redirects, 404, and the read limit."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from resources.lib.dragoman import keysource as k
        key = self.KEY

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/raw/AbCd1234':
                    body = ('my gemini key: %s \u0161\r\n' % key).encode('cp1250')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain; charset=windows-1250')
                elif self.path == '/moved':
                    self.send_response(302)
                    self.send_header('Location', '/raw/AbCd1234')
                    body = b''
                elif self.path == '/bom':
                    body = ('\ufeff%s' % key).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain')
                elif self.path == '/big':
                    body = b'x' * (k.READ_LIMIT * 2)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain')
                else:
                    body = b'not here'
                    self.send_response(404)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        saved_env = dict(os.environ)
        for name in ('http_proxy', 'HTTP_PROXY', 'https_proxy', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY'):
            os.environ.pop(name, None)
        os.environ['no_proxy'] = '*'
        try:
            base = 'http://127.0.0.1:%d' % server.server_address[1]
            ctype, text = k.http_get(base + '/moved', 'test')
            self.assertIn('charset=windows-1250', ctype)
            self.assertTrue(text.startswith('my gemini key'))
            self.assertIn('\u0161', text)
            self.assertEqual(k.key_from_address(base + '/moved', 'test'), ([key], base + '/moved'))
            self.assertEqual(k.http_get(base + '/bom', 'test')[1], key)
            with self.assertRaises(k.FetchError) as caught:
                k.http_get(base + '/missing', 'test')
            self.assertEqual((caught.exception.kind, caught.exception.code), ('http', 404))
            self.assertEqual(len(k.http_get(base + '/big', 'test')[1]), k.READ_LIMIT)
            with self.assertRaises(k.FetchError) as caught:
                k.key_from_address(base + '/big', 'test')
            self.assertEqual(caught.exception.kind, 'nokey')
        finally:
            os.environ.clear()
            os.environ.update(saved_env)
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
