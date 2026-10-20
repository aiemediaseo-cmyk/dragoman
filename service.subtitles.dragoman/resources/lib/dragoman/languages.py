# -*- coding: utf-8 -*-
"""Language table shared by settings, detection and prompts (no Kodi imports).

ORDER MATTERS: the settings enum for the target language stores an index into
LANGUAGES (index 0 in settings is "Auto"). Only ever append new languages.
"""

import re

# (code, English name as Kodi uses it, native name, string id in strings.po)
LANGUAGES = [
    ('en', 'English', 'English', 32501),
    ('sl', 'Slovenian', u'slovenščina', 32502),
    ('hr', 'Croatian', 'hrvatski', 32503),
    ('sr', 'Serbian', 'srpski (latinica)', 32504),
    ('sr-Cyrl', 'Serbian (Cyrillic)', u'српски (ћирилица)', 32505),
    ('bs', 'Bosnian', 'bosanski', 32506),
    ('mk', 'Macedonian', u'македонски', 32507),
    ('de', 'German', 'Deutsch', 32508),
    ('fr', 'French', u'français', 32509),
    ('es', 'Spanish', u'español', 32510),
    ('it', 'Italian', 'italiano', 32511),
    ('pt', 'Portuguese', u'português', 32512),
    ('pt-BR', 'Portuguese (Brazil)', u'português (Brasil)', 32513),
    ('nl', 'Dutch', 'Nederlands', 32514),
    ('pl', 'Polish', 'polski', 32515),
    ('cs', 'Czech', u'čeština', 32516),
    ('sk', 'Slovak', u'slovenčina', 32517),
    ('hu', 'Hungarian', 'magyar', 32518),
    ('ro', 'Romanian', u'română', 32519),
    ('bg', 'Bulgarian', u'български', 32520),
    ('ru', 'Russian', u'русский', 32521),
    ('uk', 'Ukrainian', u'українська', 32522),
    ('el', 'Greek', u'Ελληνικά', 32523),
    ('tr', 'Turkish', u'Türkçe', 32524),
    ('sq', 'Albanian', 'shqip', 32525),
    ('sv', 'Swedish', 'svenska', 32526),
    ('da', 'Danish', 'dansk', 32527),
    ('no', 'Norwegian', 'norsk', 32528),
    ('fi', 'Finnish', 'suomi', 32529),
    ('et', 'Estonian', 'eesti', 32530),
    ('lv', 'Latvian', u'latviešu', 32531),
    ('lt', 'Lithuanian', u'lietuvių', 32532),
    ('ar', 'Arabic', u'العربية', 32533),
    ('he', 'Hebrew', u'עברית', 32534),
    ('fa', 'Persian', u'فارسی', 32535),
    ('hi', 'Hindi', u'हिन्दी', 32536),
    ('zh', 'Chinese (Simplified)', u'简体中文', 32537),
    ('zh-Hant', 'Chinese (Traditional)', u'繁體中文', 32538),
    ('ja', 'Japanese', u'日本語', 32539),
    ('ko', 'Korean', u'한국어', 32540),
    ('vi', 'Vietnamese', u'Tiếng Việt', 32541),
    ('th', 'Thai', u'ไทย', 32542),
    ('id', 'Indonesian', 'Bahasa Indonesia', 32543),
    ('ms', 'Malay', 'Bahasa Melayu', 32544),
]

_BY_CODE = dict((row[0], row) for row in LANGUAGES)

# Names Kodi may store in locale.subtitlelanguage / subtitles.languages that
# differ from ours, plus common ISO 639-2 codes.
_ALIASES = {
    'slovene': 'sl', 'slv': 'sl', 'hrv': 'hr', 'srp': 'sr', 'scc': 'sr', 'bos': 'bs',
    'eng': 'en', 'ger': 'de', 'deu': 'de', 'fre': 'fr', 'fra': 'fr', 'spa': 'es',
    'ita': 'it', 'por': 'pt', 'pob': 'pt-BR', 'pb': 'pt-BR', 'portuguese (brazilian)': 'pt-BR',
    'brazilian': 'pt-BR', 'dut': 'nl', 'nld': 'nl', 'pol': 'pl', 'cze': 'cs', 'ces': 'cs',
    'slk': 'sk', 'hun': 'hu', 'rum': 'ro', 'ron': 'ro', 'bul': 'bg', 'rus': 'ru',
    'ukr': 'uk', 'gre': 'el', 'ell': 'el', 'tur': 'tr', 'alb': 'sq', 'sqi': 'sq',
    'swe': 'sv', 'dan': 'da', 'nor': 'no', 'nb': 'no', 'nob': 'no',
    'norwegian bokmal': 'no', u'norwegian bokmål': 'no', 'fin': 'fi', 'est': 'et',
    'lav': 'lv', 'lit': 'lt', 'ara': 'ar', 'heb': 'he', 'per': 'fa', 'fas': 'fa',
    'persian (iran)': 'fa', 'hin': 'hi', 'chi': 'zh', 'zho': 'zh', 'chinese': 'zh',
    'chinese (simple)': 'zh', 'zh-cn': 'zh', 'zh-hans': 'zh', 'zh-tw': 'zh-Hant',
    'jpn': 'ja', 'kor': 'ko', 'vie': 'vi', 'tha': 'th', 'ind': 'id', 'may': 'ms', 'msa': 'ms',
    'mac': 'mk', 'mkd': 'mk', 'serbian (latin)': 'sr', 'sr-latn': 'sr', 'sr-cyrl': 'sr-Cyrl',
    'serbian cyrillic': 'sr-Cyrl', 'pt-br': 'pt-BR',
    # Spanish under other names: Latin American Spanish counts as Spanish
    u'español': 'es', 'espanol': 'es', 'castellano': 'es', 'castilian': 'es', 'latino': 'es',
    'spanish (latin america)': 'es', 'spanish (latin american)': 'es', 'spanish (latam)': 'es',
    'spanish (spain)': 'es', 'spanish (mexico)': 'es', 'spanish (argentina)': 'es', 'es-419': 'es',
    'latin american spanish': 'es', u'español latinoamericano': 'es', u'español (latinoamérica)': 'es',
    'portuguese (portugal)': 'pt', u'português': 'pt', 'portugues': 'pt',
    u'português (brasil)': 'pt-BR', 'portuguese (brazil)': 'pt-BR',
}
_REGION_CODE_RE = re.compile(r'^([a-z]{2,3})[-_]([a-z]{2,4}|\d{3})$')
_BY_NAME = {}
for _row in LANGUAGES:
    _BY_NAME[_row[1].lower()] = _row[0]
    _BY_NAME[_row[0].lower()] = _row[0]


def _lookup(v):
    return _BY_NAME.get(v) or _ALIASES.get(v, '')


def normalize(value):
    """Code for a language code or English name, or '' if unknown.

    Regional variants count as the language itself unless we list them on their own
    (pt-BR, zh-Hant, sr-Cyrl): 'es-419', 'es_MX', 'Spanish (LatAm)', u'Español
    Latinoamericano' are all 'es'. Subtitle add-ons label their tracks like that."""
    if not value:
        return ''
    v = ' '.join(value.strip().lower().split())
    code = _lookup(v) or _lookup(v.replace('_', '-'))   # 'pt_BR' like 'pt-BR'
    if code:
        return code
    m = _REGION_CODE_RE.match(v)                       # 'es-419', 'es_mx', 'en-us'
    if m:
        return _lookup(m.group(1))
    head = v.split('(', 1)[0].strip()                  # 'spanish (latam)' -> 'spanish'
    if head and head != v:
        return _lookup(head)
    first = v.split(' ', 1)[0]                         # u'español latinoamericano'
    if first != v:
        return _lookup(first)
    return ''


def english_name(code):
    row = _BY_CODE.get(code)
    return row[1] if row else code


_KODI_NAMES = {'sr-Cyrl': 'Serbian', 'zh': 'Chinese', 'zh-Hant': 'Chinese'}


def kodi_name(code):
    """English name Kodi turns back into the right language code (its subtitle dialog
    names the file it is handed after it); '' when the language is unknown."""
    if not code or code not in _BY_CODE:
        return ''
    return _KODI_NAMES.get(code) or english_name(code)


def native_name(code):
    row = _BY_CODE.get(code)
    return row[2] if row else code


def string_id(code):
    row = _BY_CODE.get(code)
    return row[3] if row else 0


def file_code(code):
    """Code used in output file names, which Kodi reads as the stream language."""
    return {'sr-Cyrl': 'sr', 'pt-BR': 'pt', 'zh-Hant': 'zh'}.get(code, code)


def from_kodi_ui_language(addon_name):
    """'resource.language.sl_si' -> 'sl'."""
    if not addon_name:
        return ''
    tail = addon_name.rsplit('.', 1)[-1].lower()     # 'sl_si'
    lang, _sep, region = tail.partition('_')
    if lang == 'pt' and region == 'br':
        return 'pt-BR'
    if lang == 'zh':
        return 'zh-Hant' if region in ('tw', 'hk') else 'zh'
    if lang == 'sr' and 'cyr' in tail:
        return 'sr-Cyrl'
    return lang if lang in _BY_CODE else ''


SHS = ('hr', 'sr', 'bs')   # Croatian / Serbian (Latin) / Bosnian cluster


def matches(detected, codes):
    """True if a detected language is one of `codes`."""
    if not detected:
        return False
    if detected in codes:
        return True
    if detected == 'pt-BR' and 'pt' in codes:
        return True
    if detected == 'zh-Hant' and 'zh' in codes:
        return True
    return False


def parse_code_list(csv):
    return [c for c in (x.strip() for x in (csv or '').split(',')) if c in _BY_CODE]
