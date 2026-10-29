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

from resources.lib.dragoman import cache, gemini, langdetect, languages, srt, timing, translator  # noqa: E402
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


ES_LATAM = (u"¿Dónde estabas anoche? Estaba en la casa. ¿Qué te pasa? Tú sabes lo que quiero decir. "
            u"No quiero ir allá otra vez, está muy lejos. Ahora vamos, ándale, que se nos hace tarde. "
            u"Gracias por todo, en serio. ¿Qué onda con ella? No sé, pero está bien. Todo bien aquí, "
            u"no te preocupes. Eso es para ti, ¿ok? Sí, claro, lo que tú digas. Estoy cansado.")


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


class RegionalVariantTests(unittest.TestCase):
    """Subtitle add-ons label Latin American Spanish 'es-419', 'Spanish (LatAm)',
    u'Español Latinoamericano'... it is still Spanish: a Spanish target leaves it alone."""

    def test_file_names(self):
        from resources.lib.dragoman import util
        for name in ('Movie.es-419.srt', 'Movie.es_419.srt', 'Movie.es_SR.srt', 'Movie.es-MX.forced.srt',
                     'Movie.Spanish (LatAm).srt', 'Movie.Spanish (Latin America).srt',
                     u'Movie.Español Latinoamericano.srt', 'Movie.latino.srt', 'Movie.spa.srt',
                     'TempSubtitle.es-419.srt'):
            self.assertEqual(util.guess_lang(name), 'es', name)
        self.assertEqual(util.guess_lang('Movie.pt-BR.srt'), 'pt')
        self.assertEqual(util.guess_lang('Movie.en_US.srt'), 'en')
        self.assertEqual(util.guess_lang('Movie.Serbian (Cyrillic).srt'), 'sr')
        # nothing new is invented
        self.assertEqual(util.guess_lang('Captain America.srt'), '')
        self.assertEqual(util.guess_lang('Movie.WEB-DL.srt'), '')
        self.assertEqual(util.guess_lang('subterra_active.srt'), '')
        self.assertEqual(langdetect.classify('Movie.Serbian (Cyrillic).srt', SR_CYR)[0], 'sr-Cyrl')

    def test_names_kodi_and_add_ons_use(self):
        n = languages.normalize
        for value in ('es-419', 'es_419', 'es_SR', 'es-MX', 'Spanish (LatAm)', 'Spanish (Latin America)',
                      u'Español Latinoamericano', u'español', 'Castellano', 'spa', 'Spanish (Mexico)'):
            self.assertEqual(n(value), 'es', value)
        self.assertEqual(n('Portuguese (Brazil)'), 'pt-BR')
        self.assertEqual(n('pt_BR'), 'pt-BR')
        self.assertEqual(n('pt_PT'), 'pt')
        self.assertEqual(n('sr_Cyrl'), 'sr-Cyrl')
        self.assertEqual(n('sr-Latn'), 'sr')
        self.assertEqual(n('zh-TW'), 'zh-Hant')
        self.assertEqual(n('en-US'), 'en')
        self.assertEqual(n('Klingon (Qo)'), '')
        self.assertEqual(n('subterra'), '')

    def test_latin_american_spanish_is_left_alone_for_a_spanish_target(self):
        self.assertEqual(langdetect.detect_text(ES_LATAM)[0], 'es')
        for name in ('Movie.es-419.srt', 'Movie.Spanish (LatAm).srt', u'Película.Español Latinoamericano.srt',
                     'subtitle.srt'):
            code = langdetect.classify(name, ES_LATAM)[0]
            self.assertEqual(code, 'es', name)
            self.assertTrue(languages.matches(code, ['es']), name)


class OtherAddonTests(unittest.TestCase):
    def test_subterra_files_and_folders(self):
        from resources.lib.dragoman import others
        ids = others.DEFAULT_IGNORED
        self.assertEqual(ids, ['script.subterra'])
        temp = '/storage/.kodi/temp/'
        self.assertEqual(others.owner(temp + 'subterra_eng.srt', 'subterra_eng.srt', ids), 'script.subterra')
        self.assertEqual(others.owner(temp + 'subterra_active.srt', 'subterra_active.srt', ids), 'script.subterra')
        self.assertEqual(others.owner(temp + 'Subterra_Active.SRT', 'Subterra_Active.SRT', ids), 'script.subterra')
        self.assertEqual(others.owner(temp + 'TempSubtitle.en.srt', 'TempSubtitle.en.srt', ids), '')
        self.assertEqual(others.owner(temp + 'subterra_eng.srt', 'subterra_eng.srt', []), '')
        win = 'C:\\Users\\x\\AppData\\Roaming\\Kodi\\userdata\\addon_data\\script.subterra\\a.srt'
        self.assertEqual(others.owner(win, 'a.srt', ids), 'script.subterra')
        # an add-on we know nothing about: its folders, and files named after it
        other = ['service.subtitles.autotranslate']
        self.assertEqual(others.owner('/k/addon_data/service.subtitles.autotranslate/x/en.srt', 'en.srt', other),
                         'service.subtitles.autotranslate')
        self.assertEqual(others.owner(temp + 'autotranslate_es.srt', 'autotranslate_es.srt', other),
                         'service.subtitles.autotranslate')
        self.assertEqual(others.owner(temp + 'TempSubtitle.en.srt', 'TempSubtitle.en.srt', ['script.abc']), '')
        self.assertEqual(others.clean_ids([' Script.Subterra ', 'script.subterra', '', 'bad id']),
                         ['script.subterra'])


class FakeClient(object):
    """Scripted Gemini stand-in. script: list of callables or exceptions consumed per call."""

    def __init__(self, model, script=None, calls=None):
        self.model = model
        self.script = script if script is not None else []
        self.calls = calls if calls is not None else []
        self.last_usage = {'_secs': 0.1}

    def generate_json(self, system, user, schema, **kw):
        payload = json.loads(user)
        self.calls.append((self.model, payload))
        self.kw = kw
        self.system = system
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

    def test_characters_speaker_and_context_after(self):
        """Gender fix: who-is-who first, then every chunk carries it, the next lines and the cast."""
        characters = lambda p: json.dumps({'characters': [  # noqa: E731
            {'name': 'Patrick Jane', 'gender': 'Male', 'note': 'consultant, works for Lisbon'},
            {'name': 'Teresa Lisbon', 'gender': 'female', 'note': 'his boss; called "Boss"'},
            {'name': 'Bob', 'gender': 'n/a'}, {'name': '', 'gender': 'male'}]})
        texts = ['line %d' % i for i in range(25)]
        tr, calls, _w = self.make([characters], cast=['Patrick Jane (Simon Baker)', 'Teresa Lisbon (Robin Tunney)'])
        found = tr.find_characters(texts)
        self.assertEqual([translator.character_line(c) for c in found],
                         ['Patrick Jane: male. consultant, works for Lisbon',
                          'Teresa Lisbon: female. his boss; called "Boss"', 'Bob: gender unknown'])
        first = calls[0][1]
        self.assertIn('line 0\nline 1', first['dialogue'])
        self.assertEqual(first['cast'][0], 'Patrick Jane (Simon Baker)')
        out, failed = tr.translate(texts)
        self.assertEqual(failed, 0)
        chunk1, chunk2, chunk3 = [c[1] for c in calls[1:]]
        self.assertEqual(chunk1['characters'], [translator.character_line(c) for c in found])
        self.assertEqual(chunk1['context_after'], ['line %d' % i for i in range(10, 16)])
        self.assertEqual(chunk2['context_before'][-1], 'SL:line 9')
        self.assertNotIn('context_after', chunk3)
        self.assertEqual(chunk3['cast'][1], 'Teresa Lisbon (Robin Tunney)')
        # the answer names the speaker before the text, so the model settles who is talking first
        item = translator.SCHEMA['properties']['cues']['items']
        self.assertEqual(item['propertyOrdering'], ['id', 'speaker', 'text'])
        self.assertIn('speaker', translator.system_prompt('bs'))
        self.assertIn('gender', translator.system_prompt('bs'))

    def test_character_request_problems_do_not_stop_the_translation(self):
        tr, calls, _w = self.make([GeminiError('server', 'boom', 503)])
        self.assertEqual(tr.find_characters(['line %d' % i for i in range(20)]), [])
        out, failed = tr.translate(['line %d' % i for i in range(5)])
        self.assertEqual((out[0], failed), ('SL:line 0', 0))
        self.assertNotIn('characters', calls[-1][1])
        self.assertEqual(translator.parse_characters('not json'), [])
        self.assertEqual(translator.parse_characters('{"characters": "x"}'), [])
        # too little dialogue: no request at all
        tr, calls, _w = self.make()
        self.assertEqual(tr.find_characters(['hi', '', 'yo']), [])
        self.assertEqual(calls, [])

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


class GenderFormTests(unittest.TestCase):
    """On-device reading of I/you forms that show gender (0.5.0)."""

    def forms(self, target, text):
        from resources.lib.dragoman import gender
        return [(p, g) for p, g, _w in gender.forms(target, text)]

    def test_bcs(self):
        M, F = 'male', 'female'
        self.assertEqual(set(self.forms('bs', u'Bila sam umorna.')), {(1, F)})
        self.assertEqual(self.forms('bs', u'Rekao sam ti da ne ideš.'), [(1, M)])
        self.assertEqual(self.forms('bs', u'Jesi li ti bio tamo?'), [(2, M)])
        self.assertEqual(self.forms('hr', u'Nisam bila sigurna.'), [(1, F)])
        self.assertEqual(self.forms('sr', u'Osećam se kao da sam luda.'), [(1, F)])
        self.assertEqual(self.forms('sr-Cyrl', u'Била сам тамо. Јеси ли ти био?'), [(1, F), (2, M)])
        # not about the speaker
        self.assertEqual(self.forms('bs', u'On je to sam rekao.'), [])        # sam = himself
        self.assertEqual(self.forms('bs', u'Ja sam kao dijete.'), [])         # kao = like
        self.assertEqual(self.forms('bs', u'Bio je umoran.'), [])             # he
        self.assertEqual(self.forms('bs', u'Kupio sam si auto.'), [(1, M)])  # si = to myself
        self.assertEqual(self.forms('bs', u'Izgubila sam posao.'), [(1, F)])  # posao = job

    def test_other_slavic(self):
        M, F = 'male', 'female'
        self.assertEqual(set(self.forms('sl', u'Bil sem utrujen.')), {(1, M)})
        self.assertEqual(self.forms('sl', u'Sem si kupila avto.'), [(1, F)])
        self.assertEqual(set(self.forms('cs', u'Byla jsem unavená.')), {(1, F)})
        self.assertEqual(self.forms('cs', u'Jsem rád, že jsi přišel.'), [(1, M), (2, M)])
        self.assertEqual(self.forms('sk', u'Bola som tam.'), [(1, F)])
        self.assertEqual(self.forms('pl', u'Byłam zmęczona.'), [(1, F)])
        self.assertEqual(self.forms('pl', u'Siedziałem pod stołem.'), [(1, M)])
        self.assertEqual(self.forms('pl', u'Czy byłaś tam?'), [(2, F)])
        self.assertEqual(self.forms('pl', u'Wysyłam list.'), [])
        self.assertEqual(self.forms('ru', u'Я не знал.'), [(1, M)])
        self.assertEqual(self.forms('ru', u'Ты готова?'), [(2, F)])
        self.assertEqual(self.forms('uk', u'Я був там.'), [(1, M)])
        self.assertEqual(self.forms('uk', u'Я знов тут.'), [])

    def test_romance(self):
        M, F = 'male', 'female'
        self.assertEqual(self.forms('es', u'Estoy cansada.'), [(1, F)])
        self.assertEqual(self.forms('es', u'Estoy trabajando.'), [])
        self.assertEqual(self.forms('pt-BR', u'Obrigado!'), [(1, M)])
        self.assertEqual(self.forms('pt', u'Estou perto.'), [])
        self.assertEqual(self.forms('it', u'Sono stata qui.'), [(1, F)])
        self.assertEqual(self.forms('it', u'Ci sono molti.'), [])
        self.assertEqual(self.forms('fr', u'Je ne suis pas sûre.'), [(1, F)])
        self.assertEqual(self.forms('fr', u"J'étais fatigué."), [(1, M)])
        self.assertEqual(self.forms('fr', u'Tu es prêt ?'), [(2, M)])
        self.assertEqual(self.forms('fr', u'Je suis le chemin.'), [])
        self.assertEqual(self.forms('de', u'Ich bin müde.'), [])   # no check for German

    def test_no_false_alarms(self):
        """Sentences the review found misread (0.5.0)."""
        corpus = [('bs', u'Ja sam budala.'), ('bs', u'Ti si budala.'), ('bs', u'Ja sam Nikola.'),
                  ('bs', u'Ja sam dio tima.'), ('bs', u'Ti si sam kriv.'), ('hr', u'Kupila si je auto.'),
                  ('sl', u'Kdo te je pripeljal sem?'), ('sl', u'Zapomnila si je.'), ('sl', u'Oddahnil si je.'),
                  ('sl', u'Jaz sem Rachel.'), ('sl', u'Sem Isabel.'), ('sk', u'Ona si to zaslúžila.'),
                  ('sk', u'Myslel si, že to vieš.'), ('sk', u'Sadla si.'), ('it', u'Sono alla stazione.'),
                  ('it', u'Sono della polizia.'), ('it', u'Sono Luca.'), ('it', u'Sono Andrea.'),
                  ('es', u'Estás bajo arresto.'), ('es', u'Estoy contigo.'), ('pt', u'Estou dentro.'),
                  ('es', u'La casa está encantada.'), ('pt', u'Ele foi obrigado a sair.'),
                  ('ru', u'Я генерал.'), ('ru', u'Ты ангел.'), ('uk', u'Ти любов.'), ('ru', u'Я Петров.'),
                  ('pl', u'Został generałem.'), ('pl', u'Nie zdołam.'), ('pl', u'Odwołam to.'),
                  # second review
                  ('es', u'¿Dónde estás ahora?'), ('pt', u'Onde estás agora?'), ('it', u'Dove sei ora?'),
                  ('es', u'Estoy mucho mejor.'), ('it', u'Sei un genio!'), ('it', u'Sono un medico.'),
                  ('fr', u'Tu es un bébé.'), ('bs', u'Bez posla sam već godinu dana.'), ('sl', u'Si kul?'),
                  ('sl', u'Sem cool.')]
        for target, text in corpus:
            self.assertEqual(self.forms(target, text), [], (target, text))
        # and still read where they should be
        self.assertEqual(self.forms('bs', u'- Da.\nBila sam tamo.'), [(1, 'female')])
        self.assertEqual(self.forms('sk', u'Ty si bol tam?'), [(2, 'male')])
        self.assertEqual(self.forms('pt', u'Muito obrigada!'), [(1, 'female')])
        self.assertEqual(self.forms('sl', u'Ali si bil tam?'), [(2, 'male')])
        self.assertEqual(self.forms('hr', u'Znam da si bio sam.'), [(2, 'male')])     # sam = alone
        self.assertEqual(self.forms('sl', u'Zapomnila si bom, kaj si rekel.'), [(2, 'male')])
        self.assertEqual(self.forms('sr-Cyrl', u'Видео сам Данила јуче.'), [(1, 'male')])
        self.assertEqual(self.forms('it', u'Sono appena arrivata.'), [(1, 'female')])
        self.assertEqual(self.forms('es', u'Estoy un poco cansada.'), [(1, 'female')])
        for target, text in (('hr', u'\u2013 Bila sam tamo.'), ('es', u'\u2014\u00bfEst\u00e1s loca?'),
                             ('pl', u'\u2013 Jestem gotowa.'), ('ru', u'\u2013 \u042f \u0433\u043e\u0442\u043e\u0432\u0430.'),
                             ('bs', u'BILA SAM TAMO.')):
            self.assertEqual([g for _p, g in self.forms(target, text)][:1], ['female'], (target, text))
        from resources.lib.dragoman import gender
        self.assertEqual(gender.persons_in(u"I'm tired, you know."), {1, 2})
        self.assertEqual(gender.persons_in(u'Where is it?'), set())
        self.assertEqual(gender.persons_in(u'Give me that.'), {1})

    def test_check_per_dash_line(self):
        from resources.lib.dragoman import gender
        text = u'- Bila sam tamo.\n- Bio sam i ja.'
        self.assertEqual(gender.check('bs', text, [('female', 'male'), ('male', 'female')]), [])
        self.assertEqual(gender.check('bs', u'- Bila sam tamo.\n- Bila sam i ja.',
                                      [('female', 'male'), ('male', 'female')]),
                         [(1, 'male', 'female', 'bila')])
        self.assertEqual(gender.check('bs', u'Jesi li bila umorna?', [('male', 'male')]),
                         [(2, 'male', 'female', 'bila')])
        # unknown gender or a group: nothing to compare with
        self.assertEqual(gender.check('bs', u'Bila sam tamo.', [(None, None)]), [])
        self.assertEqual(gender.check('bs', u'Jesi li bila?', [('male', 'group')]), [])
        # dash lines that do not match the voices are not checked
        self.assertEqual(gender.check('bs', u'Bila sam tamo.', [('male', None), ('female', None)]), [])

    def test_resolve_names(self):
        from resources.lib.dragoman import gender
        chars = [{'name': 'Teresa Lisbon', 'gender': 'female', 'aliases': ['Boss']},
                 {'name': 'Patrick Jane', 'gender': 'male', 'aliases': []},
                 {'name': 'Grace Van Pelt', 'gender': 'female', 'aliases': []}]
        self.assertEqual(gender.resolve('Lisbon', chars), ('Teresa Lisbon', 'female'))
        self.assertEqual(gender.resolve('Boss', chars), ('Teresa Lisbon', 'female'))
        self.assertEqual(gender.resolve('Mr. Jane', chars), ('Patrick Jane', 'male'))
        self.assertEqual(gender.resolve('waitress (f)', chars), ('waitress', 'female'))
        self.assertEqual(gender.resolve('Mrs. Smith', chars), ('Mrs. Smith', 'female'))
        self.assertEqual(gender.resolve('?', chars), (None, None))
        self.assertEqual(gender.resolve('everyone', chars), ('several people', 'group'))
        self.assertEqual(gender.resolve('Cho', chars), ('Cho', None))
        self.assertEqual(gender.resolve('Lisbon and Jane', chars), ('several people', 'group'))
        self.assertEqual(gender.resolve('woman', chars), ('woman', 'female'))
        self.assertEqual(gender.resolve('Mrs. Jane', chars), ('Mrs. Jane', 'female'))
        self.assertEqual(gender.describe('Lisbon', chars), 'Teresa Lisbon (female)')
        self.assertIsNone(gender.describe('?', chars))


class SpeakerPassTests(unittest.TestCase):
    """0.5.0: who says each cue and to whom, before translating; then the on-device check."""

    def make(self, script, target='bs', chunk=10, **kw):
        calls = []
        clients = []

        def factory(model):
            c = FakeClient(model, script, calls)
            clients.append(c)
            return c
        now = [1000.0]

        def wait(seconds):
            now[0] += seconds
            return False
        kw.setdefault('source', 'en')
        tr = translator.Translator(factory, ['m1', 'm2'], target, title='Film', chunk_size=chunk, rpm=600,
                                   wait=wait, clock=lambda: now[0], speakers=True, **kw)
        tr.characters = translator.coerce_characters([
            {'name': 'Teresa Lisbon', 'gender': 'female', 'aliases': ['Boss']},
            {'name': 'Patrick Jane', 'gender': 'male'}])
        return tr, calls, clients

    @staticmethod
    def voices(answer_for):
        return lambda p: json.dumps({'cues': [dict(id=c['id'], **answer_for(c)) for c in p['cues']]})

    def test_tags_reach_the_translation_request(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        texts = ['line %d' % i for i in range(5)] + ['- Me?\n- You.']
        dash = lambda p: json.dumps({'cues': [{'id': c['id'], 'speaker': 'Lisbon | Jane' if c['id'] == 6 else 'Boss',  # noqa: E731
                                               'to': 'Jane | Lisbon' if c['id'] == 6 else 'Jane'} for c in p['cues']]})
        tr, calls, clients = self.make([dash])
        out, failed = tr.translate(texts)
        self.assertEqual(failed, 0)
        self.assertEqual(len(calls), 2)                       # 1 speaker block + 1 chunk
        speaker_req, chunk = calls[0][1], calls[1][1]
        self.assertIn('Teresa Lisbon: female (also "Boss")', speaker_req['characters'][0])
        self.assertEqual(chunk['cues'][0]['by'], 'Teresa Lisbon (female)')
        self.assertEqual(chunk['cues'][0]['to'], 'Patrick Jane (male)')
        self.assertEqual(chunk['cues'][5]['by'], '1) Teresa Lisbon (female) 2) Patrick Jane (male)')
        self.assertIn('"by"', clients[0].system)               # the tagged prompt
        self.assertEqual(tr.voices[5], [['Lisbon', 'Jane'], ['Jane', 'Lisbon']])
        self.assertEqual(speakers({'cues': [{'id': 1}]}), '{"cues": [{"id": 1, "speaker": "Lisbon", "to": "Jane"}]}')

    def test_speaker_requests_ask_for_more_thinking_and_run_per_block(self):
        texts = ['line %d' % i for i in range(translator.SPEAKER_BLOCK + 20)]
        script = [self.voices(lambda c: {'speaker': 'Jane', 'to': 'Lisbon'})]
        tr, calls, clients = self.make(script, chunk=100)
        seen = []
        orig = FakeClient.generate_json

        def spy(this, system, user, schema, **kw):
            seen.append((schema is translator.SPEAKER_SCHEMA, kw.get('thinking')))
            return orig(this, system, user, schema, **kw)
        FakeClient.generate_json = spy
        try:
            out, failed = tr.translate(texts)
        finally:
            FakeClient.generate_json = orig
        self.assertEqual(failed, 0)
        kinds = [k for k, _t in seen]
        # speaker block 1, three chunks, speaker block 2 (only when chunk 4 needs it), chunk 4
        self.assertEqual(kinds, [True, False, False, False, True, False])
        self.assertEqual(seen[0][1], translator.SPEAKER_THINKING)
        self.assertEqual(sorted(tr.speaker_blocks), [0, translator.SPEAKER_BLOCK])
        self.assertEqual(tr.speaker_requests, 2)

    def test_speaker_pass_problems_never_stop_the_translation(self):
        texts = ['line %d' % i for i in range(5)]
        tr, calls, clients = self.make([GeminiError('blocked', 'finishReason PROHIBITED_CONTENT')])
        out, failed = tr.translate(texts)
        self.assertEqual((out[0], failed), ('SL:line 0', 0))
        self.assertNotIn('by', calls[-1][1]['cues'][0])
        self.assertEqual((tr.speaker_blocks, tr._speaker_failed), (set(), {0}))  # not asked again now
        self.assertEqual(tr.state()['speaker_blocks'], [])    # ... but a resumed job tries again
        # an answer cut off for length: the block is split
        texts = ['line %d' % i for i in range(100)]
        tr, calls, clients = self.make([GeminiError('too_long', 'MAX_TOKENS')], chunk=100)
        tr.translate(texts)
        self.assertEqual([len(c[1]['cues']) for c in calls], [100, 50, 50, 100])
        # split once only: halves that fail too are given up (no cascade of requests)
        tr, calls, clients = self.make([GeminiError('timeout', 't')] * 3, chunk=100)
        out, failed = tr.translate(texts)
        self.assertEqual(([len(c[1]['cues']) for c in calls], failed), ([100, 50, 50, 100], 0))
        self.assertNotIn('by', calls[-1][1]['cues'][0])
        # an answer that covers too few lines is not used
        half = lambda p: json.dumps({'cues': [{'id': 1, 'speaker': 'Jane', 'to': 'Lisbon'}]})  # noqa: E731
        tr, calls, clients = self.make([half])
        tr.translate(['a', 'b', 'c'])
        self.assertEqual(tr.voices, {})
        # garbage answer: no tags, translation goes on
        self.assertEqual(translator.parse_voices('nope', [0, 1]), {})

    def test_wrong_gender_is_sent_back_and_fixed(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        wrong = lambda p: json.dumps({'cues': [  # noqa: E731
            {'id': 1, 'text': u'Bio sam tamo.'}, {'id': 2, 'text': u'Jesi li bila umorna?'},
            {'id': 3, 'text': u'Bila sam tamo.'}]})
        fixed = lambda p: json.dumps({'cues': [  # noqa: E731
            {'id': 1, 'text': u'Bila sam tamo.'}, {'id': 2, 'text': u'Jesi li bio umoran?'}]})
        tr, calls, clients = self.make([speakers, wrong, fixed])
        out, failed = tr.translate(['I was there.', 'Are you tired?', 'I was there.'])
        self.assertEqual(out, [u'Bila sam tamo.', u'Jesi li bio umoran?', u'Bila sam tamo.'])
        repair = calls[2][1]['cues']
        self.assertEqual([c['translation'] for c in repair], [u'Bio sam tamo.', u'Jesi li bila umorna?'])
        self.assertIn('the speaker is female', repair[0]['problem'])
        self.assertIn('the person spoken to is male', repair[1]['problem'])
        self.assertEqual((tr.gender_flagged, tr.gender_fixed), (2, 2))

    def test_fix_that_makes_nothing_better_is_ignored(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        wrong = lambda p: json.dumps({'cues': [{'id': 1, 'text': u'Bio sam tamo.'}]})  # noqa: E731
        worse = lambda p: json.dumps({'cues': [{'id': 1, 'text': u'- Bio sam\n- tamo.'}]})  # noqa: E731
        tr, calls, clients = self.make([speakers, wrong, worse])
        out, failed = tr.translate(['I was there.'])
        self.assertEqual(out, [u'Bio sam tamo.'])
        # the fix request failing is not fatal either
        tr, calls, clients = self.make([speakers, wrong, GeminiError('server', 'boom', 503)])
        out, failed = tr.translate(['I was there.'])
        self.assertEqual((out, failed), ([u'Bio sam tamo.'], 0))

    def test_only_persons_the_english_line_speaks_of_are_checked(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        # "si" in this line is not "you" in the source: no flag, no fix request
        answer = lambda p: json.dumps({'cues': [{'id': 1, 'text': u'Bio je tamo.'},  # noqa: E731
                                                {'id': 2, 'text': u'Kupila si je auto.'}]})
        tr, calls, clients = self.make([speakers, answer])
        tr.translate(['He was there.', 'She bought herself a car.'])
        self.assertEqual((len(calls), tr.gender_flagged), (2, 0))
        # not checked at all for a source that is not English
        wrong = lambda p: json.dumps({'cues': [{'id': 1, 'text': u'Bio sam tamo.'}]})  # noqa: E731
        tr, calls, clients = self.make([speakers, wrong], source='de')
        tr.translate(['Ich war da.'])
        self.assertEqual((len(calls), tr.gender_flagged), (2, 0))

    def test_one_speaker_for_two_dash_lines_is_not_used(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        answer = lambda p: json.dumps({'cues': [{'id': 1, 'text': u'- Gdje si bio?\n- Bila sam kod kuće.'}]})  # noqa: E731
        tr, calls, clients = self.make([speakers, answer])
        tr.translate(['- Where were you?\n- I was at home.'])
        self.assertNotIn('by', calls[1][1]['cues'][0])
        self.assertEqual((len(calls), tr.gender_flagged), (2, 0))

    def test_fixes_that_rewrite_are_refused(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        wrong = lambda p: json.dumps({'cues': [{'id': 1, 'text': u'Bio sam umoran cijeli dan.'}]})  # noqa: E731
        for bad in (u"I was tired all day.", u'Cijeli dan sam se osjećala kao da me pregazio kamion.'):
            fix = lambda p, bad=bad: json.dumps({'cues': [{'id': 1, 'text': bad}]})  # noqa: E731
            tr, calls, clients = self.make([speakers, wrong, fix])
            out, failed = tr.translate(['I was tired all day.'])
            self.assertEqual(out, [u'Bio sam umoran cijeli dan.'])
            self.assertEqual(tr.gender_fixed, 0)

    def test_fix_requests_have_a_budget(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        wrong = lambda p: json.dumps({'cues': [{'id': c['id'], 'text': u'Bio sam tamo.'} for c in p['cues']]})  # noqa: E731
        script = [speakers] + [wrong] * 30
        tr, calls, clients = self.make(script, chunk=5)
        out, failed = tr.translate(['I was there.'] * 30)    # 6 chunks: budget 2 fix requests
        repairs = [c for c in calls if 'translation' in c[1]['cues'][0]]
        self.assertEqual(len(repairs), 2)
        self.assertEqual(tr.gender_flagged, 30)
        # a rate limit on a fix request stops further fix requests
        tr, calls, clients = self.make([speakers, wrong, GeminiError('rate', 'slow', retry_after=5)] + [wrong] * 5,
                                       chunk=5)
        tr.translate(['I was there.'] * 30)
        repairs = [c for c in calls if 'translation' in c[1]['cues'][0]]
        self.assertEqual(len(repairs), 1)

    def test_cancel_during_speaker_or_fix_request(self):
        tr, calls, clients = self.make([GeminiError('cancelled', 'aborted')])
        with self.assertRaises(translator.Cancelled):
            tr.translate(['I was there.'])
        self.assertEqual(tr.speaker_blocks, set())
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        wrong = lambda p: json.dumps({'cues': [{'id': 1, 'text': u'Bio sam tamo.'}]})  # noqa: E731
        tr, calls, clients = self.make([speakers, wrong, GeminiError('cancelled', 'aborted')])
        with self.assertRaises(translator.Cancelled):
            tr.translate(['I was there.'])

    def test_speaker_pass_only_where_gender_shows(self):
        tr, calls, clients = self.make([], target='de')
        self.assertFalse(tr.speakers)
        tr.translate(['I was there.'])
        self.assertEqual(len(calls), 1)
        self.assertNotIn('by', calls[0][1]['cues'][0])
        tr, calls, clients = self.make([], target='he')     # no check, but the tags help
        self.assertTrue(tr.speakers)
        self.assertFalse(tr.check_forms)

    def test_unchanged_answer_is_checked_too(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        same = lambda p: json.dumps({'cues': [{'id': c['id'], 'text': u'Bio sam tamo.'} for c in p['cues']]})  # noqa: E731
        texts = [u'Bio sam tamo.'] * 8
        tr, calls, clients = self.make([speakers, same, same, lambda p: json.dumps({'cues': []})])
        tr.translate(texts)
        self.assertEqual(tr.gender_flagged, 0)   # the source is not English: nothing to compare


    def test_no_check_without_rules_for_the_language(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': 'Jane'})
        tr, calls, clients = self.make([speakers], target='he')
        tr.translate(['I was there.'])
        self.assertEqual(len(calls), 2)

    def test_state_round_trip(self):
        speakers = self.voices(lambda c: {'speaker': 'Lisbon', 'to': '?'})
        tr, calls, clients = self.make([speakers])
        tr.translate(['a', 'b'])
        state = json.loads(json.dumps(tr.state()))
        tr2, calls2, _c = self.make([], voices=state['voices'], speaker_blocks=state['speaker_blocks'],
                                    characters=state['characters'])
        tr2.translate(['a', 'b'])
        self.assertEqual(len(calls2), 1)                       # no second speaker request
        self.assertEqual(calls2[0][1]['cues'][0]['by'], 'Teresa Lisbon (female)')
        self.assertNotIn('to', calls2[0][1]['cues'][0])
        # characters saved by 0.4.x (lines) still load
        old = translator.coerce_characters(['Patrick Jane: male. consultant'])
        self.assertEqual((old[0]['name'], old[0]['gender'], old[0]['note']), ('Patrick Jane', 'male', 'consultant'))


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


class ThinkingRequestTests(unittest.TestCase):
    """generate_json(thinking=, timeout=) for the speaker pass (0.5.0)."""

    def test_thinking_first_then_the_usual_ladder_and_timeout_restored(self):
        from resources.lib.dragoman import gemini
        gemini.reset_session_cache()
        sent = []

        def transport(client, method, url, body, headers):
            config = json.loads(body.decode('utf-8'))['generationConfig']
            sent.append(((config.get('thinkingConfig') or {}).get('thinkingLevel'), client.timeout))
            if len(sent) == 1:
                return 400, json.dumps({'error': {'code': 400, 'message': 'thinking level not supported'}}).encode()
            data = {'candidates': [{'content': {'parts': [{'text': '{"cues": []}'}]}, 'finishReason': 'STOP'}]}
            return 200, json.dumps(data).encode()
        client = gemini.GeminiClient('k', 'gemini-3.5-flash-lite', transport=transport)
        client.generate_json('s', '{}', {}, thinking='medium', timeout=150)
        self.assertEqual(sent, [('medium', 150), ('minimal', 150)])
        self.assertEqual(client.timeout, gemini.TIMEOUT)
        # remembered per (model, thinking): the next "medium" call starts at the variant that worked
        client.generate_json('s', '{}', {}, thinking='medium')
        self.assertEqual(sent[-1][0], 'minimal')
        # an ordinary call is not affected
        client.generate_json('s', '{}', {})
        self.assertEqual(sent[-1][0], 'minimal')
        self.assertEqual([t for t, _s in client._ladder('low')], ['low', 'minimal', None, None])

    def test_timeout_restored_after_an_error(self):
        from resources.lib.dragoman import gemini

        def transport(client, method, url, body, headers):
            raise OSError('down')
        client = gemini.GeminiClient('k', 'gemini-3.5-flash-lite', transport=transport)
        with self.assertRaises(gemini.GeminiError):
            client.generate_json('s', '{}', {}, thinking='medium', timeout=150)
        self.assertEqual(client.timeout, gemini.TIMEOUT)

    def test_estimate_counts_slow_requests(self):
        from resources.lib.dragoman import job
        self.assertEqual(job.estimate_minutes(1800, 100, 8), 3)
        self.assertEqual(job.estimate_minutes(1800, 100, 8, extra_requests=1, slow_requests=6), 6)


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



def episode_cues(minutes=42, every_s=6, scale=1.0, shift_s=0.0, first_s=3.0, end_gap_s=60.0):
    """Dialogue every few seconds from first_s until end_gap_s before the end, then timing
    distorted like a subtitle made for another version: sub = video * scale + shift."""
    cues, t = [], first_s
    while t < minutes * 60 - end_gap_s:
        start = int((t * scale + shift_s) * 1000)
        cues.append(srt.Cue(start, start + 2000, 'Line %d of the dialogue.' % len(cues)))
        t += every_s
    return cues


class TimingTests(unittest.TestCase):
    EP = 42 * 60 * 1000

    def test_frame_rates(self):
        self.assertEqual([timing.norm_fps(v) for v in ('23.976', '24.000', '25.000', '29.970', '50.000',
                                                         '59.940', '0.000', '', None)],
                         [23.976, 24.0, 25.0, 23.976, 25.0, 23.976, None, None, None])
        self.assertEqual([timing.name_fps(n) for n in ('Show.S01E01.25fps', 'Film.23.976fps.srt', 'Film.PAL.DVDRip',
                                                       'Napalm.Death', 'x.125fps')],
                         [25.0, 23.976, 25.0, None, None])
        self.assertEqual(len(timing.RATIOS), 7)

    def test_drift_from_frame_rate_is_found_and_sure(self):
        # made for the 23.976 fps release, playing on a 25 fps video: runs 4 % too slow
        cues = episode_cues(scale=25 / 23.976)
        d = timing.drift_check(cues, self.EP, '25.000', 'Show.S01E01.HDTV', 'Show.S01E01.WEB-DL.eng')
        self.assertEqual((d['ratio'], d['certain'], d['from_fps'], d['to_fps']), (0.95904, True, 23.976, 25.0))
        fixed, _t = timing.retime(cues, d['ratio'], 0)
        self.assertLess(fixed[-1].end, self.EP)
        self.assertAlmostEqual(fixed[-1].start / 1000.0, cues[-1].start / 1000.0 * 0.95904, delta=0.01)

    def test_a_plain_delay_is_not_taken_for_drift(self):
        cues = episode_cues(shift_s=150)          # everything two and a half minutes late
        d = timing.drift_check(cues, self.EP, '25.000')
        self.assertFalse(d['certain'])           # could be a delay: ask / suggest, never fix by itself
        self.assertIn('plain delay', d['reason'])
        d = timing.drift_check(cues, self.EP, '23.976')
        self.assertIsNone(d['ratio'])             # nothing runs slower than 23.976: Sync assistant
        self.assertIsNone(timing.drift_check(episode_cues(), self.EP, '25.000'))   # fine as it is

    def test_trailing_adverts_and_hints(self):
        cues = episode_cues() + [srt.Cue(self.EP + 5000, self.EP + 9000, 'Subtitles by www.example.org')] * 3
        self.assertIsNone(timing.drift_check(cues, self.EP, '25.000'))
        # the subtitle's name says PAL (25 fps), the video is 23.976 and stretching fits: a hint only
        cues = episode_cues(scale=23.976 / 25)
        d = timing.drift_check(cues, self.EP, '23.976', sub_name='Show.S01E01.PAL.HDTV.srt')
        self.assertEqual((d['ratio'], d['certain']), (1.042709, False))
        self.assertIsNone(timing.drift_check(cues[:10], self.EP, '25.000'))   # too little to judge

    def test_extra_lines_after_the_end_are_not_drift(self):
        # a correct subtitle with "next time on ..." lines 5-17 s past the end of a 25 fps video
        cues = episode_cues(end_gap_s=20) + [srt.Cue(self.EP + s * 1000, self.EP + s * 1000 + 2500, 'Next time.')
                                              for s in (5, 11, 17)]
        self.assertIsNone(timing.drift_check(cues, self.EP, '25.000'))
        # more of them, but after a long pause (the credits): still not dialogue running on
        cues = episode_cues(end_gap_s=20) + [srt.Cue(self.EP + s * 1000, self.EP + s * 1000 + 2500, 'Later.')
                                              for s in range(70, 130, 6)]
        self.assertIsNone(timing.drift_check(cues, self.EP, '25.000'))
        # a song over trimmed credits, marked as lyrics
        cues = episode_cues(end_gap_s=5) + [srt.Cue(self.EP + s * 1000, self.EP + s * 1000 + 3500,
                                                    u'♪ Somewhere over the hills ♪') for s in range(0, 40, 4)]
        self.assertIsNone(timing.drift_check(cues, self.EP, '25.000'))
        # the same lyrics unmarked on a 45-minute episode: they run on by far less than a frame
        # rate would make them - never sure
        cues = episode_cues(minutes=45, end_gap_s=2) + [
            srt.Cue(45 * 60000 + s * 1000, 45 * 60000 + s * 1000 + 3500, 'Somewhere over the hills')
            for s in range(0, 40, 4)]
        d = timing.drift_check(cues, 45 * 60000, '25.000')
        self.assertFalse(d and d['certain'], d)

    def test_odd_lines_and_endings_say_nothing(self):
        # one line with a typo in its end time (3:00 --> 53:00) in a correct subtitle
        cues = episode_cues()
        cues[30] = srt.Cue(cues[30].start, 53 * 60000, cues[30].text)
        self.assertIsNone(timing.drift_check(cues, self.EP, '25.000'))
        self.assertIsNone(timing.drift_check(cues, self.EP, '23.976'))
        # a correct subtitle with a "next time on" block right after the last scene, 23.976 fps video
        cues = episode_cues(end_gap_s=1) + [srt.Cue(self.EP + s * 1000, self.EP + s * 1000 + 2500, 'Next time.')
                                             for s in range(4, 28, 4)]
        self.assertIsNone(timing.drift_check(cues, self.EP, '23.976'))

    def test_drift_through_a_song_is_still_found(self):
        # the last minute and a half before the video's end is a song (lyrics marked): the
        # dialogue past the end follows the song without a pause, so it still counts
        cues = episode_cues(scale=25 / 23.976)
        for n, c in enumerate(cues):
            if 2430000 <= c.start <= 2525000:
                cues[n] = srt.Cue(c.start, c.end, u'\u266a la la la \u266a')
        d = timing.drift_check(cues, self.EP, '25.000')
        self.assertEqual((d['ratio'], d['certain']), (0.95904, True), d)
        # a long silent stretch right before the lines past the end (and a dozen of them):
        # found, but only suggested
        cues = [c for c in episode_cues(scale=25 / 23.976, end_gap_s=30) if not 2440000 <= c.start <= 2522000]
        d = timing.drift_check(cues, self.EP, '25.000')
        self.assertEqual((d['ratio'], d['certain']), (0.95904, False), d)
        self.assertIn('long pause', d['reason'])

    def test_unknown_frame_rate_is_never_sure(self):
        cues = episode_cues(scale=25 / 23.976)
        for fps in ('', '0.000', None):
            d = timing.drift_check(cues, self.EP, fps)
            self.assertEqual((d['ratio'], d['certain']), (0.95904, False), fps)
            self.assertIn('does not report', d['reason'])
        # a frame rate in the video's name helps choose, but is not Kodi's word for it either
        d = timing.drift_check(cues, self.EP, '', 'Show.S01E01.PAL.DVDRip.mkv')
        self.assertEqual((d['ratio'], d['certain'], d['to_fps']), (0.95904, False, 25.0))
        # 23.976 vs 24 changes too little to show at the end: not looked for
        d = timing.drift_check(episode_cues(scale=24 / 23.976, end_gap_s=1), self.EP, '24.000')
        self.assertTrue(d is None or d['ratio'] is None, d)

    def test_another_edition_or_a_delay_is_not_a_frame_rate(self):
        film = 100 * 60 * 1000
        extended = episode_cues(minutes=106, end_gap_s=180)      # 6 minutes more film, 3 of credits
        d = timing.drift_check(extended, film, '25.000', 'Film.2010.1080p.HDTV.x264.mkv',
                               'Film.2010.EXTENDED.1080p.BluRay.x264.srt')
        self.assertIsNone(d['ratio'])
        self.assertIn('another edition (extended)', d['reason'])
        # a plain delay with a credit line for the translator at 0:00
        cues = [srt.Cue(0, 4000, 'Prevod i obrada: Marko')] + episode_cues(every_s=4, shift_s=90, end_gap_s=5)
        d = timing.drift_check(cues, self.EP, '25.000')
        self.assertIsNone(d['ratio'])
        self.assertIn('plain delay', d['reason'])
        # a small delay is not worth a notice (the Sync assistant is there when it shows)
        cues = [srt.Cue(0, 4000, 'Prevod i obrada: Marko')] + episode_cues(every_s=4, shift_s=30, end_gap_s=5)
        self.assertIsNone(timing.drift_check(cues, self.EP, '25.000'))
        self.assertEqual([c.text for c in timing.real_cues([srt.Cue(0, 1, t) for t in (
            'Prevod i obrada: Marko', 'Prijevod: Ana', 'Untertitel: XY', u'\u00dcbersetzung: Z', 'Hello.')])],
            ['Hello.'])

    def test_fit_from_sync_points(self):
        k = 25 / 23.976
        self.assertEqual(timing.fit([]), (1.0, 0.0, 'none'))
        self.assertEqual(timing.fit([(600000, 602400)]), (1.0, 2400.0, 'offset'))
        k1, b1, how = timing.fit([(600000, 602400)], ratio=k)        # a frame-rate fix plus a delay
        self.assertEqual(how, 'offset')
        self.assertAlmostEqual(k1 * 600000 + b1, 602400, places=3)
        s1, s2 = 300000, 2000000
        k2, b2, how = timing.fit([(s1, k * s1 + 1500), (s2, k * s2 + 1750)])
        self.assertEqual((round(k2, 6), how), (1.042709, 'drift'))
        self.assertAlmostEqual(b2, 1750, delta=5)       # through the newest point: where the film is now
        self.assertEqual(timing.fit([(s1, s1 + 1000), (s2, s2 + 31000)])[2], 'uneven')   # a cut in between
        self.assertEqual(timing.fit([(s1, s1 + 1000), (s2, s2 + 31000)])[1], 31000)
        self.assertEqual(timing.fit([(s1, s1 + 1000), (s1 + 60000, s1 + 61800)]), (1.0, 1800.0, 'offset'))
        # newest point + the one farthest from it
        self.assertEqual(timing.pick_points([(1, 1), (5, 5), (9, 9)]), [(1, 1), (9, 9)])

    def test_fit_needs_a_real_speed_and_both_points_on_the_line(self):
        import random
        rnd = random.Random(7)
        # 12 s of extra footage between two points 40 minutes apart looks like 24/23.976 drift
        s1, s2 = 5 * 60000, 45 * 60000
        k, b, how = timing.fit([(s1, s1 + 2000), (s2, s2 + 14000)])
        self.assertEqual((k, b, how), (1.0, 14000.0, 'uneven'))
        # a plain 2.4 s delay synced twice 9 minutes apart, each press up to 0.3 s off
        hows = set()
        for _ in range(2000):
            a, c = 600000, 600000 + 9 * 60000
            k, b, how = timing.fit([(a, a + 2400 + rnd.uniform(-300, 300)), (c, c + 2400 + rnd.uniform(-300, 300))])
            hows.add((k, how))
            self.assertLess(abs(b - 2400), 301)
        self.assertEqual(hows, {(1.0, 'offset')})
        # real 23.976 -> 25 drift, synced 20 minutes apart with the same press error
        r = 23.976 / 25
        for _ in range(500):
            a, c = 300000, 300000 + 20 * 60000
            k, b, how = timing.fit([(a, r * a + 900 + rnd.uniform(-300, 300)), (c, r * c + 900 + rnd.uniform(-300, 300))])
            self.assertEqual((k, how), (0.95904, 'drift'))
        # 23.976 vs 24 is believed only when the points are far enough apart to show it
        r = 24 / 23.976
        self.assertEqual(timing.fit([(s1, r * s1), (s2, r * s2)])[::2], (1.001001, 'drift'))      # 2.4 s apart
        self.assertEqual(timing.fit([(s1, r * s1), (s1 + 20 * 60000, r * (s1 + 20 * 60000))])[::2],
                         (1.0, 'offset'))                                                            # 1.2 s

    def test_kodi_language_names(self):
        self.assertEqual([languages.kodi_name(c) for c in ('hr', 'sr-Cyrl', 'pt-BR', 'zh', 'zh-Hant', 'lv', '', 'xx')],
                         ['Croatian', 'Serbian', 'Portuguese (Brazil)', 'Chinese', 'Chinese', 'Latvian', '', ''])

    def test_retime_keeps_texts_paired_and_drops_lines_before_zero(self):
        cues = [srt.Cue(500, 1500, 'a'), srt.Cue(3000, 4000, 'b'), srt.Cue(10000, 11000, 'c')]
        out, texts = timing.retime(cues, 1.0, -3500, ['A', 'B', 'C'])
        self.assertEqual([(c.start, c.end) for c in out], [(0, 500), (6500, 7500)])
        self.assertEqual(texts, ['B', 'C'])
        self.assertIn('00:00:06,500 --> 00:00:07,500\nC', timing.compose(cues, ['A', 'B', 'C'], (1.0, -3500)))

    def test_fix_store(self):
        base = tempfile.mkdtemp()
        try:
            store = timing.FixStore(os.path.join(base, 'profile'))
            key = timing.fix_key(timing.video_key('The.Film.2021.1080p.WEB.mkv'), 'abc')
            self.assertIsNone(store.mapping(key))
            rec = store.add_point(key, 600000, 602400, 'The Film')
            self.assertEqual((rec['k'], rec['b'], rec['how']), (1.0, 2400.0, 'offset'))
            k = 25 / 23.976
            store.add_point(key, 2000000, int(2000000 * k + 2400 - 600000 * (k - 1) + 0))
            self.assertEqual(store.get(key)['how'], 'drift')
            self.assertTrue(store.delete(key))
            self.assertFalse(store.delete(key))
            rec = store.set_ratio(key, 0.95904)
            self.assertEqual((rec['k'], rec['how']), (0.95904, 'framerate'))
            rec = store.add_point(key, 600000, 600000 * 0.95904 + 1000)     # delay on top of the ratio
            self.assertEqual(rec['k'], 0.95904)
            self.assertAlmostEqual(rec['b'], 1000, delta=1)
            self.assertNotEqual(timing.video_key('The.Film.2021.1080p.WEB.mkv'),
                                timing.video_key('The.Film.2021.720p.HDTV.mkv'))
            self.assertEqual(timing.video_key('video.mkv', 'Film'), timing.video_key('stream', 'Film'))
        finally:
            shutil.rmtree(base, ignore_errors=True)

if __name__ == '__main__':
    unittest.main()
