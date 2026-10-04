# -*- coding: utf-8 -*-
"""Which language is a subtitle in? File-name code first, text check as fallback
and as a sanity check. No Kodi imports.

The text check counts common words. It separates Slovenian from the
Croatian/Serbian/Bosnian cluster, and inside that cluster uses ekavian vs
ijekavian forms and a few vocabulary markers. Cyrillic is split by letters
that only some alphabets have (e.g. ђ ћ for Serbian).
"""
import re

from . import languages, util

_WORD_RE = re.compile(u"[^\\W\\d_]+(?:'[^\\W\\d_]+)?", re.UNICODE)
_TAG_RE = re.compile(r'<[^>]+>|\{[^}]*\}')


def _set(words):
    return frozenset(words.split())


STOPWORDS = {
    'en': _set(u"the and you to is it that what this of in me my your have be for not are do was we "
               u"he she they don't i'm it's just know can will get all so yes oh there here with "
               u"don't that's you're what's gonna want right come okay well"),
    'sl': _set(u"je in da se ne na to kaj sem si ni tudi za pa bo bi me mi jaz ti kako zakaj kje že "
               u"zelo lahko samo vse tako gremo hvala prosim dobro res vem ima imam smo ste ker kot "
               u"tukaj zdaj sedaj nekaj nič mora moram bil bila bilo kdo ga jo nisem greš veš imaš "
               u"potem torej vendar mogoče morda bom boš bomo vedno hočem sploh tega temu"),
    'shs': _set(u"je i da se ne na to što šta sam si nije za a će ću bi me mi ja ti kako zašto gdje gde "
                u"već jako može samo sve tako idemo hvala molim dobro stvarno znam ima imam smo ste "
                u"jer kao ovdje ovde sada sad nešto ništa mora moram bio bila bilo tko ko ga nisam "
                u"hoću možda treba reci hajde evo eto ovo ono taj jesi neću onda uvijek uvek kad kada"),
    'de': _set(u"der die das und ist nicht ich du sie es ein eine zu mit was wir auf ja nein mir dich "
               u"dir bin hast habe sind war wie auch noch schon hier jetzt gut danke bitte den dem"),
    'es': _set(u"el la que de y no es en lo un una por qué con para se me te está estoy pero sí bien "
               u"aquí ahora gracias vamos eso esto muy todo yo tú"),
    'fr': _set(u"le la les de et je tu il est pas que qui un une vous nous ce ça c'est mais oui non "
               u"bien merci avec pour dans sur suis êtes j'ai"),
    'it': _set(u"il lo la che di e non è un una per sono sei ma sì bene grazie questo quello cosa "
               u"perché anche ancora qui adesso ora molto tutto io"),
    'pt': _set(u"o a que de e não é um uma para com se eu você está estou mas sim bem obrigado aqui "
               u"agora isso muito tudo vamos"),
    'nl': _set(u"de het een en is niet ik je jij wat dat van op te zijn we maar ja nee hier nu goed dank"),
    'pl': _set(u"nie się to jest że co jak ale tak już czy mnie ja ty jestem tu teraz dobrze dziękuję "
               u"proszę tylko może wszystko"),
    'cs': _set(u"je to se že na ne co jak ale tak už jsem jsi mě já ty tady teď dobře děkuji prosím "
               u"jen může všechno není"),
    'sk': _set(u"je to sa že na nie čo ako ale tak už som si ma ja ty tu teraz dobre ďakujem prosím "
               u"len môže všetko"),
    'hu': _set(u"a az és hogy nem is egy van ez meg de csak mi én te már itt most jó köszönöm kérem igen"),
    'ro': _set(u"și nu e în este că de la ce o un pe cu mai dar da eu tu aici acum bine mulțumesc sunt ești"),
    'tr': _set(u"bir ve bu ne de da ben sen mi için çok var yok evet hayır tamam burada şimdi iyi değil"),
    'sv': _set(u"och att det är jag du inte en på som vad med har vi men ja nej här nu bra tack"),
    'da': _set(u"og at det er jeg du ikke en på som hvad med har vi men ja nej her nu godt tak"),
    'no': _set(u"og at det er jeg du ikke en på som hva med har vi men ja nei her nå bra takk"),
    'fi': _set(u"ja on ei se että mitä minä sinä hän me te he kun mutta niin tämä täällä nyt hyvä kiitos olen"),
    'sq': _set(u"dhe në është të një për me nuk që po unë ti ai ajo ne ju ata këtu tani mirë faleminderit"),
    'id': _set(u"yang dan di itu ini tidak aku kamu apa dengan untuk ada saya kita ke dari sudah akan bisa"),
}

# Slovenian vs Croatian/Serbian/Bosnian: words only one side uses
SL_ONLY = _set(u"kaj sem ni tudi pa bo jaz zakaj kje že zelo lahko gremo prosim vem ker tukaj zdaj sedaj "
               u"nekaj nič bil greš veš imaš potem torej vendar mogoče morda bom boš bomo vedno hočem sploh")
SHS_ONLY = _set(u"što šta sam nije će ću ćeš ja zašto gdje gde već jako može idemo molim stvarno znam jer "
                u"kao ovdje ovde sada sad nešto ništa bio tko ko hoću možda treba reci hajde evo eto ovo "
                u"ono taj neću onda uvijek uvek kad kada")
EKAVIAN = _set(u"gde lepo lepa deca vreme pre posle hteo htela dete reč reči mleko belo ceo cela devojka "
               u"uvek ovde mesto pesma mesec videti sećaš seti dve levo telo vetar sreća")
IJEKAVIAN = _set(u"gdje lijepo lijepa djeca vrijeme prije poslije htio htjela dijete riječ riječi mlijeko "
                 u"bijelo cijeli cijela djevojka uvijek ovdje mjesto pjesma mjesec vidjeti sjećaš sjeti "
                 u"dvije lijevo tijelo vjetar")
HR_MARKERS = _set(u"što tko točno tisuća tisuću tjedan kruh vlak kava obitelj glazba zrakoplov kazalište "
                  u"nogomet jučer općenito uopće sretan sretna povijest točka")
SRBS_MARKERS = _set(u"šta ko tačno hiljada hiljadu sedmica nedelja hleb hljeb voz kafa kahva porodica "
                    u"muzika avion pozorište fudbal uopšte srećan srećna istorija historija juče")


def script_of(text):
    letters = [c for c in text[:20000] if c.isalpha()]
    if not letters:
        return ''
    cyr = sum(1 for c in letters if u'Ѐ' <= c <= u'ӿ')
    return 'cyrillic' if cyr > 0.3 * len(letters) else 'other'


def _cyrillic(text):
    t = text[:200000]
    count = lambda chars: sum(t.count(c) for c in chars)  # noqa: E731
    if count(u'ѓќѕЃЌЅ') > 0:
        return 'mk'
    if count(u'ђћЂЋ') > 0 or count(u'јљњџЈЉЊЏ') > 3:
        return 'sr-Cyrl'
    if count(u'іїєґІЇЄҐ') > 3:
        return 'uk'
    if count(u'ыэЫЭ') > 0:
        return 'ru'
    if count(u'ъЪ') > 3:
        return 'bg'
    return 'ru'


def _other_scripts(letters):
    total = float(len(letters))
    ranges = (
        ('el', u'Ͱ', u'Ͽ'), ('he', u'֐', u'׿'), ('ar', u'؀', u'ۿ'),
        ('hi', u'ऀ', u'ॿ'), ('th', u'฀', u'๿'), ('ko', u'가', u'힯'),
        ('ja', u'぀', u'ヿ'), ('zh', u'一', u'鿿'),
    )
    counts = {}
    for code, lo, hi in ranges:
        counts[code] = sum(1 for c in letters if lo <= c <= hi)
    if counts['ja'] > 0.1 * total:
        return 'ja'
    for code in ('el', 'he', 'ar', 'hi', 'th', 'ko', 'zh'):
        if counts[code] > 0.3 * total:
            if code == 'ar' and any(c in letters for c in u'پچژگ'):
                return 'fa'
            return code
    return ''


def _words(text, max_words):
    sample = _TAG_RE.sub(' ', text[:300000])
    return sample, _WORD_RE.findall(sample.lower())[:max_words]


def detect_text(text, max_words=5000):
    """(code, confidence 0..1) from subtitle text, or ('', 0.0)."""
    if not text:
        return '', 0.0
    sample, words = _words(text, max_words)
    letters = [c for c in sample if c.isalpha()]
    if len(letters) < 40:
        return '', 0.0
    if script_of(sample) == 'cyrillic':
        return _cyrillic(sample), 0.9
    other = _other_scripts(letters)
    if other:
        return other, 0.9
    if len(words) < 20:
        return '', 0.0
    scores = []
    for code, stop in STOPWORDS.items():
        scores.append((sum(1 for w in words if w in stop), code))
    scores.sort(reverse=True)
    best_hits, best = scores[0]
    second_hits = scores[1][0]
    conf = best_hits / float(len(words))
    if best_hits < 8 or conf < 0.05:
        return '', conf
    if best in ('sl', 'shs'):
        # Slovenian vs Croatian/Serbian/Bosnian: decided by words only one side uses.
        # Only when one of the two is the best match - a stray "sad" or "ja" in an
        # English subtitle must not turn English into Croatian (seen on the user's TV).
        sl = sum(1 for w in words if w in SL_ONLY)
        shs = sum(1 for w in words if w in SHS_ONLY)
        if sl or shs:
            best = 'sl' if sl > shs else 'shs'
    elif best_hits < 1.2 * second_hits:
        conf *= 0.5   # close call between two languages
    if best == 'shs':
        best = _shs_variant(words)
    return best, conf


def share(text, code, max_words=5000):
    """Fraction of the text's words that are common words of `code` (None if unknown)."""
    key = 'shs' if code in languages.SHS else code
    stop = STOPWORDS.get(key)
    if stop is None or not text:
        return None
    _sample, words = _words(text, max_words)
    if not words:
        return None
    return sum(1 for w in words if w in stop) / float(len(words))


def _shs_variant(words):
    ek = sum(1 for w in words if w in EKAVIAN)
    ij = sum(1 for w in words if w in IJEKAVIAN)
    hr = sum(1 for w in words if w in HR_MARKERS)
    srbs = sum(1 for w in words if w in SRBS_MARKERS)
    if ek > ij:
        return 'sr'
    if hr > srbs:
        return 'hr'
    if srbs > hr:
        return 'bs'
    return 'hr'


def _cluster(code):
    if code in languages.SHS:
        return 'shs'
    return {'pt-BR': 'pt', 'zh-Hant': 'zh', 'sr-Cyrl': 'sr-Cyrl'}.get(code, code)


def same_group(a, b):
    """True for identical or very close languages (hr/sr/bs, pt/pt-BR, zh/zh-Hant)."""
    return bool(a) and bool(b) and _cluster(a) == _cluster(b)


_CYR_NAME_TOKENS = ('cyr', 'cyrl', 'cyrillic', 'cirilica', u'ћирилица', 'cir')


def classify(name, text):
    """(code, how) for a subtitle file. how explains the decision for the log."""
    raw = util.guess_lang(name)
    fcode = languages.normalize(raw) if raw else ''
    tokens = [t for t in re.split(r'[.\s_\-\[\]()]+', name.lower()) if t]
    if fcode == 'sr' and (any(t in _CYR_NAME_TOKENS for t in tokens) or script_of(text) == 'cyrillic'):
        fcode = 'sr-Cyrl'
    tcode, conf = detect_text(text)
    if fcode and tcode and conf >= 0.15 and _cluster(tcode) != _cluster(fcode):
        named = share(text, fcode)
        # The name wins unless the text clearly does not fit it: a subtitle really in
        # the named language still scores well for that language's common words.
        if named is None or named < 0.5 * conf:
            return tcode, 'text (%.0f%% common words; name said %s, %s)' % (
                conf * 100, fcode, '?' if named is None else '%.0f%%' % (named * 100))
    if fcode:
        return fcode, 'file name'
    if tcode:
        return tcode, 'text (%.0f%% common words)' % (conf * 100)
    return '', 'unknown'
