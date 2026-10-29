# -*- coding: utf-8 -*-
"""Who is male or female, and whether a translated line uses the right forms. No Kodi imports.

resolve() turns a speaker label from the speaker pass ("Lisbon", "waitress (f)", "?") into
a name and a gender, using the character list. It runs on the device, so every line of one
character gets the same gender, whatever the model would guess for each chunk.

check() looks for first-person ("I") and second-person singular ("you") words that show
gender in a translated line, and reports the ones that disagree with the speaker's or the
listener's gender. Only clear patterns are read: a missed mistake costs nothing, a false
alarm costs a request (and the fix request is told it may be a false alarm).
"""
import re

MALE, FEMALE, GROUP = 'male', 'female', 'group'

_UNKNOWN_LABELS = ('', '?', '-', '--', 'unknown', 'none', 'n/a', 'nobody', 'no one')
_GROUP_LABELS = ('group', 'all', 'everyone', 'everybody', 'several people', 'several', 'crowd',
                 'people', 'them', 'you all', 'audience', 'both', 'team', 'others', 'family')
_SUFFIX_RE = re.compile(r'\s*[\(\[]\s*(m|f|male|female|man|woman|boy|girl|he|she)\s*[\)\]]\s*$', re.I)
_MALE_TITLES = ('mr', 'mister', 'sir', 'lord', 'king', 'prince', 'father', 'dad', 'uncle', 'brother',
                'grandpa', 'grandfather', 'husband', 'boyfriend', 'son', 'monsieur', 'senor', 'herr',
                'man', 'boy', 'guy', 'waiter', 'policeman', 'gentleman', 'priest', 'nephew')
_FEMALE_TITLES = ('mrs', 'ms', 'miss', 'madam', 'madame', 'maam', 'lady', 'queen', 'princess',
                  'mother', 'mom', 'mum', 'aunt', 'sister', 'grandma', 'grandmother', 'wife',
                  'girlfriend', 'daughter', 'mademoiselle', 'senora', 'senorita', 'frau',
                  'woman', 'girl', 'waitress', 'policewoman', 'actress', 'nun', 'niece')
_TITLES = set(_MALE_TITLES + _FEMALE_TITLES + (
    'dr', 'doctor', 'agent', 'detective', 'officer', 'captain', 'capt', 'sergeant', 'sgt',
    'lieutenant', 'lt', 'chief', 'boss', 'professor', 'prof', 'judge', 'the', 'young', 'old'))


def norm_gender(value):
    value = ('%s' % (value or '')).strip().lower()
    if value in ('m', 'male', 'man', 'boy', 'he', 'masculine'):
        return MALE
    if value in ('f', 'female', 'woman', 'girl', 'she', 'feminine'):
        return FEMALE
    if value.startswith('fem') or value.startswith('wom'):
        return FEMALE
    if value.startswith('mal') or value.startswith('masc'):
        return MALE
    return None


def _tokens(text):
    return [t for t in re.split(r"[^\w]+", (text or '').lower().replace("'", ''), flags=re.U) if t]


def _key(text):
    return ' '.join(_tokens(text))


def _core(tokens):
    return [t for t in tokens if t not in _TITLES]


def _title_gender(tokens):
    if any(t in _MALE_TITLES for t in tokens):
        return MALE
    if any(t in _FEMALE_TITLES for t in tokens):
        return FEMALE
    return None


def resolve(label, characters):
    """(name, gender) for a speaker/listener label. gender: MALE, FEMALE, GROUP or None.
    name is None when nobody can be named."""
    text = ('%s' % (label or '')).strip().strip('"').strip()
    low = text.lower()
    if low in _UNKNOWN_LABELS:
        return None, None
    if low in _GROUP_LABELS or re.search(r'\s(and|&)\s|,', low):
        return 'several people', GROUP
    suffix = None
    match = _SUFFIX_RE.search(text)
    if match:
        suffix = norm_gender(match.group(1))
        text = text[:match.start()].strip()
        if text.lower() in _UNKNOWN_LABELS:
            return ('someone', suffix) if suffix else (None, None)
    tokens = _tokens(text)
    if not tokens:
        return None, None
    key = ' '.join(tokens)
    core = set(_core(tokens))
    exact, partial = [], []
    for char in characters or []:
        names = [char.get('name', '')] + list(char.get('aliases') or [])
        keys = [_key(n) for n in names if _key(n)]
        if key in keys:
            exact.append(char)
            continue
        if core:
            for k in keys:
                other = set(_core(k.split()))
                if other and (core <= other or other <= core):
                    partial.append(char)
                    break
    found = exact or partial
    genders = set(c.get('gender') for c in found)
    said = suffix or _title_gender(tokens)
    if len(found) == 1 or (found and len(genders) == 1):
        gender = found[0].get('gender')
        if said and gender and said != gender:
            return text, said          # "Mrs. Jane" is not Patrick Jane
        name = found[0].get('name') if len(found) == 1 else text
        return name, gender or said
    return text, said


def describe(label, characters):
    """Text for a translation request: 'Teresa Lisbon (female)', 'several people', or None."""
    name, gender = resolve(label, characters)
    if gender == GROUP:
        return 'several people'
    if not name:
        return None
    if gender:
        return '%s (%s)' % (name[:60], gender)
    return '%s (male or female not known)' % name[:60]


# ---------------------------------------------------------------------------- checker
# Each language: which words mark "I" / "you" (aux), which small words may stand between
# them and the gendered word (fillers), and how to read a word's gender (classify).

_CYR = dict(zip(u'абвгдђежзијклљмнњопрстћуфхцчџш',
                [u'a', u'b', u'v', u'g', u'd', u'đ', u'e', u'ž', u'z', u'i', u'j', u'k', u'l', u'lj',
                 u'm', u'n', u'nj', u'o', u'p', u'r', u's', u't', u'ć', u'u', u'f', u'h', u'c', u'č',
                 u'dž', u'š']))


def _latin_sr(text):
    out = []
    for c in text or '':
        latin = _CYR.get(c.lower())
        if latin is None:
            out.append(c)
        else:
            out.append(latin[:1].upper() + latin[1:] if c != c.lower() else latin)
    return ''.join(out)


_TOKEN_RE = re.compile(u"[^\\W\\d_]+|[.,!?;:\u2026\u2013\u2014\"\u201c\u201d\u201e()\\[\\]\u00ab\u00bb\u00bf\u00a1]", re.U)


_SENTENCE_END = set(u'.!?:;\u2026"\u201c\u201d\u201e-([\u2013\u2014\u00ab\u00bb\u00bf\u00a1')
NAME_MARK = u'\u0000'   # put in front of a capitalised word in mid-sentence: probably a name


def _split(text):
    """Lower-case tokens; a word with a capital letter in mid-sentence (a name such as Nikola,
    Rachel, Luca or Petrov) is marked so it is never read as a gendered form."""
    text = re.sub(r'<[^>]*>|\{[^}]*\}', ' ', text or '')
    tokens = []
    raw = []
    caps = []
    for line in text.split('\n'):      # a new subtitle line may start a new sentence
        if raw:
            raw.append('.')
            caps.append(False)
        found = _TOKEN_RE.findall(line)
        shouting = not any(c.islower() for c in line)    # ALL CAPS: capitals say nothing
        raw.extend(found)
        caps.extend([shouting] * len(found))
    for k, tok in enumerate(raw):
        low = tok.lower()
        if (tok[:1].isalpha() and tok[:1] != low[:1] and k > 0 and raw[k - 1] not in _SENTENCE_END
                and not caps[k]):
            low = NAME_MARK + low
        tokens.append(low)
    return tokens


def _is_word(token):
    return token[:1].isalpha()          # names (NAME_MARK first) are not words here


def _pairs(pairs):
    masc, fem = set(), set()
    for item in pairs.split():
        m, _sep, f = item.partition('/')
        if m:
            masc.add(m)
        if f:
            fem.add(f)
    return masc, fem


class _Lang(object):
    def __init__(self, aux, fillers, classify, before=True, prepare=None, extra=None, max_fill=2):
        self.aux = aux                # function(tokens, i) -> 1, 2 or None
        self.fillers = set(fillers.split())
        self.classify = classify      # function(word) -> MALE, FEMALE or None
        self.before = before          # also read the word right before the aux word
        self.prepare = prepare or (lambda text: text)
        self.extra = extra            # function(tokens) -> [(person, gender, word)] found anywhere
        self.max_fill = max_fill


# "a little" only as the two words together; a lone un/um starts a noun ("Sei un genio")
_LITTLE = set([(u'un', u'poco'), (u'um', u'pouco'), (u'un', u'po'), (u'un', u'peu')])


def _scan(tokens, lang):
    found, seen = [], set()

    def add(person, index):
        word = tokens[index]
        gender = lang.classify(word)
        if gender and (person, index) not in seen:
            seen.add((person, index))
            found.append((person, gender, word))
            return True
        return False

    for i in range(len(tokens)):
        person = lang.aux(tokens, i)
        if not person:
            continue
        j, skipped = i + 1, 0
        while j < len(tokens) and skipped < lang.max_fill:
            if tokens[j] in lang.fillers:
                j += 1
            elif j + 1 < len(tokens) and (tokens[j], tokens[j + 1]) in _LITTLE:
                j += 2
            else:
                break
            skipped += 1
        if j < len(tokens) and _is_word(tokens[j]) and lang.aux(tokens, j) is None:
            add(person, j)
        if lang.before and i > 0 and _is_word(tokens[i - 1]) and lang.aux(tokens, i - 1) is None:
            add(person, i - 1)
    if lang.extra:
        found.extend(lang.extra(tokens))
    return found


_CLAUSE_END = set(u'.,!?;:\u2026\u2013\u2014"\u201c\u201d\u201e()[]')


def _clause_before(tokens, i):
    """The tokens before i in the same clause."""
    j = i
    while j > 0 and tokens[j - 1] not in _CLAUSE_END:
        j -= 1
    return tokens[j:i]


_THIRD = set(u'on ona ono oni one ony'.split())


def _set_aux(first, second, reflexive_after=()):
    first, second = set(first.split()), set(second.split())
    reflexive_after = set(reflexive_after)

    def aux(tokens, i):
        word = tokens[i]
        if word in first:
            return 1
        if word in second:
            # "Kupio sam si auto": si right after sam is the reflexive "to myself"
            if i > 0 and tokens[i - 1] in reflexive_after:
                return None
            return 2
        return None
    return aux


# --- Bosnian / Croatian / Serbian ----------------------------------------------------------
_BCS_MASC, _BCS_FEM = _pairs(
    u'umoran/umorna siguran/sigurna spreman/spremna sretan/sretna srećan/srećna gladan/gladna '
    u'žedan/žedna gotov/gotova kriv/kriva zauzet/zauzeta ljut/ljuta /sama mrtav/mrtva živ/živa '
    u'pijan/pijana zabrinut/zabrinuta uplašen/uplašena prestrašen/prestrašena zadovoljan/zadovoljna '
    u'slobodan/slobodna dužan/dužna svjestan/svjesna svestan/svesna zaljubljen/zaljubljena '
    u'oženjen/udata /udana /trudna bolestan/bolesna nervozan/nervozna tužan/tužna ponosan/ponosna '
    u'uvjeren/uvjerena ubeđen/ubeđena zbunjen/zbunjena iznenađen/iznenađena razočaran/razočarana '
    u'povrijeđen/povrijeđena povređen/povređena ozlijeđen/ozlijeđena budan/budna lud/luda '
    u'glup/glupa rođen/rođena zahvalan/zahvalna došao/došla otišao/otišla pošao/pošla '
    u'našao/našla išao/išla mogao/mogla rekao/rekla')
_BCS_NOT = set(u'kao zao posao pepeo anđeo andjeo orao kotao ugao pakao čao cao ciao ceo cio '
               u'deo dio studio rodeo leo mario teo škola skola kola cela cijela dela djela tela tijela '
               u'sela pola ala hala vila sila ula bla ola kila stola budala hulja propala dola '
               u'nikola mila posla'.split())


def _bcs_classify(word):
    if word in _BCS_MASC:
        return MALE
    if word in _BCS_FEM:
        return FEMALE
    if len(word) >= 3 and word not in _BCS_NOT:
        if word.endswith('la'):
            return FEMALE
        if word[-1] == 'o' and word[-2] in 'aeiu':
            return MALE
    return None


def _bcs_aux():
    base = _set_aux(u'sam nisam jesam', u'si nisi jesi', reflexive_after=(u'sam', u'nisam'))

    def aux(tokens, i):
        person = base(tokens, i)
        if person == 1 and tokens[i] == 'sam':
            # "On je to sam rekao": here sam is "himself"; "Ti si sam kriv": "yourself"
            window = tokens[max(0, i - 3):i]
            if 'je' in window or 'su' in window or set(window) & set(('si', 'nisi', 'jesi')):
                return None
        if person == 2:
            # reflexive si: "Kupila si je auto", "Ona si to ne može priuštiti"
            if (i + 1 < len(tokens) and tokens[i + 1] == 'je') or _THIRD & set(_clause_before(tokens, i)):
                return None
        return person
    return aux


_BCS = _Lang(_bcs_aux(),
             u'li ja ti to ga mu joj im ih se me te vas nas tu već vec baš bas samo stvarno uvijek '
             u'uvek još jos ikad ikada nikad nikada onda tada zaista čak cak možda mozda ovdje ovde '
             u'tamo sad sada opet toliko tako jako vrlo malo baš',
             _bcs_classify)
_BCS_CYR = _Lang(_BCS.aux, ' '.join(_BCS.fillers), _bcs_classify,
                 prepare=_latin_sr)

# --- Slovenian -----------------------------------------------------------------------------
_SL_MASC, _SL_FEM = _pairs(
    u'utrujen/utrujena prepričan/prepričana pripravljen/pripravljena srečen/srečna lačen/lačna '
    u'žejen/žejna sam/sama kriv/kriva jezen/jezna zaljubljen/zaljubljena mrtev/mrtva živ/živa '
    u'/noseča poročen/poročena bolan/bolna zaskrbljen/zaskrbljena ponosen/ponosna '
    u'žalosten/žalostna gotov/gotova nor/nora buden/budna rojen/rojena presenečen/presenečena '
    u'razočaran/razočarana hvaležen/hvaležna šel/šla')
_SL_NOT = set(u'dol pol stol sol del cel cela kotel pekel angel val tal mal šola sola sila vila '
              u'kila hala ula pola general ideal material kanal hotel kul cool'.split())
_SL_OTHER_AUX = set(u'je si so ste smo sta bo bom boš bodo'.split())


def _sl_aux():
    base = _set_aux(u'sem nisem', u'si nisi', reflexive_after=(u'sem', u'nisem'))

    def aux(tokens, i):
        person = base(tokens, i)
        before = _clause_before(tokens, i)
        if person == 1 and tokens[i] == 'sem' and _SL_OTHER_AUX & set(before):
            return None   # "Zakaj si prišel sem?": sem = here
        if person == 2:
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ''
            if nxt in ('je', 'jo', 'ga', 'so', 'bom', u'boš', 'bo', 'bova', 'bomo', 'boste', 'bodo') \
                    or _THIRD & set(before):
                return None   # "Zapomnila si je": reflexive si
        return person
    return aux


def _sl_classify(word):
    if word in _SL_MASC:
        return MALE
    if word in _SL_FEM:
        return FEMALE
    if len(word) >= 3 and word not in _SL_NOT:
        if word.endswith('la'):
            return FEMALE
        if word.endswith('l'):
            return MALE
    return None


_SL = _Lang(_sl_aux(),
            u'pa že še tudi res ravno samo le ga ji jo mu jih jim se si te me vas nas to tega kar '
            u'prav zelo tako včeraj danes tam tukaj spet vedno nikoli kdaj',
            _sl_classify)

# --- Czech / Slovak ------------------------------------------------------------------------
_CS_MASC, _CS_FEM = _pairs(u'rád/ráda sám/sama ženatý/vdaná /těhotná šel/šla')
_SK_MASC, _SK_FEM = _pairs(u'rád/rada sám/sama ženatý/vydatá /tehotná šiel/šla')
_CS_NOT = set(u'stůl stôl cíl kostel kostol uhel uhol orel orol popel popol kotel kotol kašel '
              u'dál hotel anjel anděl sval škola skola síla sila kola díla diela čela generál '
              u'ideál materiál kanál'.split())


def _west_classify(masc, fem):
    def classify(word):
        if word in masc:
            return MALE
        if word in fem:
            return FEMALE
        if len(word) >= 3 and word not in _CS_NOT:
            if word.endswith('la'):
                return FEMALE
            if word.endswith('l'):
                return MALE
            if len(word) >= 4 and word.endswith(u'ý'):
                return MALE
            if len(word) >= 4 and word.endswith(u'á'):
                return FEMALE
        return None
    return classify


_CS = _Lang(_set_aux(u'jsem nejsem bych', u'jsi nejsi jseš bys'),
            u'to tak taky také už ještě opravdu fakt moc velmi docela hrozně strašně tady tam ti '
            u'mu jí ho se si tě vás nás jim prostě vždycky nikdy právě zrovna teď pak',
            _west_classify(_CS_MASC, _CS_FEM))
def _sk_aux():
    def aux(tokens, i):
        word = tokens[i]
        if word == 'som':
            return 1
        if word == 'si':
            # si is mostly reflexive (myslieť si, zaslúžiť si): count it only next to "ty"
            before = _clause_before(tokens, i)
            if 'ty' in before[-3:] and not (_THIRD & set(before)):
                return 2
        return None
    return aux


_SK = _Lang(_sk_aux(),
            u'to tak tiež už ešte naozaj fakt veľmi dosť tu tam ti mu jej ho sa si ťa vás nás im '
            u'proste vždy nikdy práve teraz potom',
            _west_classify(_SK_MASC, _SK_FEM))

# --- Polish: the verb itself carries person and gender (byłem/byłam, byłeś/byłaś) -----------
_PL_NOT = set(u'stołem kołem wołem orłem dołem czołem popiołem kościołem aniołem piekłem mydłem '
              u'siodłem źródłem światłem krzesłem masłem wiosłem ciałem działem szkłem skrzydłem '
              u'węzłem kozłem osłem kotłem michałem pawłem kłam złam połam załam wyłam działam '
              u'wołam wywołam przywołam zawołam generałem ideałem materiałem kanałem upałem '
              u'zdołam odwołam podołam'.split())
_PL_PREP = set(u'z ze za pod nad przed między'.split())
_PL_ENDINGS = ((u'łabym', 1, FEMALE), (u'łbym', 1, MALE), (u'łabyś', 2, FEMALE), (u'łbyś', 2, MALE),
               (u'łam', 1, FEMALE), (u'łem', 1, MALE), (u'łaś', 2, FEMALE), (u'łeś', 2, MALE))
_PL_MASC, _PL_FEM = _pairs(u'sam/sama gotowy/gotowa pewien/pewna pewny/pewna zmęczony/zmęczona '
                           u'szczęśliwy/szczęśliwa głodny/głodna zajęty/zajęta przekonany/przekonana '
                           u'winny/winna zakochany/zakochana pijany/pijana smutny/smutna chory/chora '
                           u'żonaty/zamężna wściekły/wściekła')


def _pl_extra(tokens):
    found = []
    for i, word in enumerate(tokens):
        if word in _PL_NOT or len(word) < 5 or not _is_word(word):
            continue
        if i > 0 and tokens[i - 1] in _PL_PREP:
            continue
        for ending, person, gender in _PL_ENDINGS:
            if word.endswith(ending):
                if ending == u'łam' and word.endswith(u'syłam'):
                    break   # wysyłam, posyłam: "I send", present tense
                found.append((person, gender, word))
                break
    return found


def _pl_classify(word):
    if word in _PL_MASC:
        return MALE
    if word in _PL_FEM:
        return FEMALE
    return None


_PL = _Lang(_set_aux(u'jestem', u'jesteś'),
            u'bardzo naprawdę trochę chyba już jeszcze tak też teraz zbyt całkiem tylko nie '
            u'zawsze nigdy wciąż tu tutaj to się ci mu jej go cię was nas',
            _pl_classify, before=False, extra=_pl_extra)

# --- Russian / Ukrainian ----------------------------------------------------------------------
_RU_MASC, _RU_FEM = _pairs(
    u'готов/готова уверен/уверена рад/рада должен/должна согласен/согласна счастлив/счастлива '
    u'свободен/свободна занят/занята прав/права жив/жива болен/больна виноват/виновата '
    u'один/одна сам/сама /беременна женат/замужем пьян/пьяна влюблён/влюблена влюблен/влюблена '
    u'знаком/знакома обязан/обязана нужен/нужна шёл/шла пошёл/пошла пришёл/пришла ушёл/ушла '
    u'нашёл/нашла мог/могла смог/смогла помог/помогла')
_RU_NOT = set(u'стол пол зал бал мел дела сила игла мила пила генерал ангел идеал материал канал '
              u'адмирал капитал финал сериал журнал скандал осёл орёл козёл дятел пепел футбол '
              u'профессионал персонал'.split())


def _ru_classify(word):
    if word in _RU_MASC:
        return MALE
    if word in _RU_FEM:
        return FEMALE
    if len(word) >= 3 and word not in _RU_NOT:
        if word.endswith(u'ла') or word.endswith(u'лась'):
            return FEMALE
        if word.endswith(u'л') or word.endswith(u'лся'):
            return MALE
    return None


_UK_MASC, _UK_FEM = _pairs(
    u'готовий/готова впевнений/впевнена радий/рада сам/сама один/одна винен/винна '
    u'/вагітна щасливий/щаслива втомлений/втомлена зайнятий/зайнята повинен/повинна '
    u'міг/могла зміг/змогла пішов/пішла прийшов/прийшла знайшов/знайшла')
_UK_NOT = set(u'знов лев дела сила любов кров бров генерал ангел'.split())


def _uk_classify(word):
    if word in _UK_MASC:
        return MALE
    if word in _UK_FEM:
        return FEMALE
    if len(word) >= 3 and word not in _UK_NOT:
        if word.endswith(u'ла') or word.endswith(u'лася'):
            return FEMALE
        if word.endswith(u'в') or word.endswith(u'вся'):
            return MALE
    return None


_RU_FILL = (u'не же ведь тоже уже ещё так просто только правда всегда никогда тебя его её ее их '
            u'вас нас ему ей им это всё все там тут здесь сегодня вчера очень давно наконец даже '
            u'действительно и')
_UK_FILL = (u'не ж же теж вже ще так просто тільки правда завжди ніколи тебе його її їх вас нас '
            u'йому їй їм це все там тут сьогодні вчора дуже давно нарешті навіть і')
_RU = _Lang(_set_aux(u'я', u'ты'), _RU_FILL, _ru_classify, before=False, max_fill=3)
_UK = _Lang(_set_aux(u'я', u'ти'), _UK_FILL, _uk_classify, before=False, max_fill=3)

# --- Romance: "I am" + adjective/participle ----------------------------------------------
_ROMANCE_NOT = set(u'yo eso esto aquello como todo claro cerca hasta para contra fuera afuera '
                   u'arriba abajo encima nada una otra esta esa misma persona idiota policía víctima '
                   u'poeta pirata atleta problema fantasma perto fora longe aqui ali isso isto tudo '
                   u'uma outra pessoa io solo proprio molto troppo poco tanto quasi davvero una la '
                   u'qua questa quella persona contigo conmigo dentro adentro bajo medio debajo numa '
                   u'nessa nesta della alla nella dalla sulla dello allo nello sopra sotto contro '
                   u'senza fuori lontano vicino verso ahora agora ora adesso appena mucho mucha mismo '
                   u'misma mesmo mesma luego justo toda todas tutto tutta tutti tutte'.split())


def _romance_classify(word):
    if len(word) < 3 or word in _ROMANCE_NOT:
        return None
    if re.search(u'(ando|endo|iendo|yendo|indo)$', word) or word.endswith('ista'):
        return None
    if word.endswith('o'):
        return MALE
    if word.endswith('a'):
        return FEMALE
    return None


_ES_FILL = u'ahora luego mucho muy tan bastante demasiado algo super re realmente totalmente completamente también ya aún todavía así'
_PT_FILL = u'agora logo muito tão bastante demasiado meio super realmente totalmente completamente também já ainda assim'
_IT_FILL = (u'ora adesso appena molto così tanto troppo proprio davvero veramente completamente anche già '
            u'ancora solo sempre mai più')


def _it_aux():
    base = _set_aux(u'sono', u'sei')

    def aux(tokens, i):
        if tokens[i] == 'sono' and i > 0 and tokens[i - 1] in ('ci', 'non'):
            if tokens[i - 1] == 'ci' or (i > 1 and tokens[i - 2] == 'ci'):
                return None    # "ci sono": there are
        return base(tokens, i)
    return aux


def _it_classify(word):
    if word in ('stato', 'stata'):
        return MALE if word == 'stato' else FEMALE
    return _romance_classify(word)


_IT = _Lang(_it_aux(), _IT_FILL, _it_classify, before=False)


def _standalone(words):
    """Thanks-words that speak for the speaker (obrigado/obrigada), only at the start of a
    sentence ("Muito obrigada!") - not inside one ("Ele foi obrigado a sair")."""
    words = dict(words)
    leading = set(u'muito muitíssimo mucho muy tan'.split())

    def extra(tokens):
        found = []
        for k, t in enumerate(tokens):
            if t not in words:
                continue
            j = k - 1
            while j >= 0 and tokens[j] in leading:
                j -= 1
            if j < 0 or not _is_word(tokens[j]):
                found.append((1, words[t], t))
        return found
    return extra


_ES = _Lang(_set_aux(u'estoy estuve', u'estás estuviste'), _ES_FILL, _romance_classify, before=False,
            extra=_standalone({u'encantado': MALE, u'encantada': FEMALE}))
_PT = _Lang(_set_aux(u'estou estive fiquei', u'estás estiveste ficaste'), _PT_FILL, _romance_classify,
            before=False, extra=_standalone({u'obrigado': MALE, u'obrigada': FEMALE}))

# French: "je suis" + adjective/participle (-é masc, -ée fem), "tu es" for "you"
_FR_MASC, _FR_FEM = _pairs(
    u'prêt/prête content/contente heureux/heureuse sûr/sûre seul/seule mort/morte fou/folle '
    u'/enceinte certain/certaine amoureux/amoureuse nerveux/nerveuse jaloux/jalouse perdu/perdue '
    u'venu/venue parti/partie sorti/sortie fier/fière désolé/désolée prisonnier/prisonnière '
    u'allé/allée né/née mort/morte gentil/gentille')


def _fr_classify(word):
    if word in _FR_MASC and word not in _FR_FEM:
        return MALE
    if word in _FR_FEM and word not in _FR_MASC:
        return FEMALE
    if len(word) >= 3:
        if word.endswith(u'ée'):
            return FEMALE
        if word.endswith(u'é'):
            return MALE
    return None


def _fr_aux(tokens, i):
    word = tokens[i]
    window = tokens[max(0, i - 3):i]
    if word in (u'suis', u'serai'):
        return 1 if ('je' in window or 'j' in window) else None
    if word in (u'étais', u'serais'):
        if 'tu' in window or 't' in window:
            return 2
        return 1 if ('je' in window or 'j' in window) else None
    if word in (u'es', u'seras'):
        return 2 if ('tu' in window or 't' in window) else None
    return None


_FR = _Lang(_fr_aux, u'pas ne n plus très si vraiment trop déjà encore toujours juste bien assez tellement',
            _fr_classify, before=False)

LANGS = {
    'bs': _BCS, 'hr': _BCS, 'sr': _BCS, 'sr-Cyrl': _BCS_CYR, 'sl': _SL, 'cs': _CS, 'sk': _SK,
    'pl': _PL, 'ru': _RU, 'uk': _UK, 'es': _ES, 'pt': _PT, 'pt-BR': _PT, 'it': _IT, 'fr': _FR,
}


# Languages where "I" or "you" can need a male or female form, even without an on-device check.
GENDERED = set(LANGS) | set(['he', 'ar', 'hi', 'ro', 'bg', 'mk', 'lt', 'lv', 'el', 'sq'])


def supported(target):
    """True when the translation can be checked on the device."""
    return target in LANGS


def marks_gender(target):
    """True when the speaker pass is worth its requests for this target language."""
    return target in GENDERED


# English source lines: does this line talk about "I" and/or "you"?
_EN_I_LOW = re.compile(r"\b(?:me|my|myself|mine)\b", re.I)
_EN_YOU = re.compile(r"\b(?:you|your|yours|yourself|ya|y'all)\b", re.I)


def persons_in(source):
    """Which persons an English source line speaks of: a set with 1 ("I") and/or 2 ("you")."""
    found = set()
    if re.search(r"\bI\b", source or '') or _EN_I_LOW.search(source or ''):
        found.add(1)
    if _EN_YOU.search(source or ''):
        found.add(2)
    return found


def forms(target, text):
    """[(person, gender, word)]: gendered "I" (1) and "you" (2) words found in text."""
    lang = LANGS.get(target)
    if lang is None:
        return []
    return _scan(_split(lang.prepare(text)), lang)


def segments(text):
    """The lines of a cue grouped by speaker: a new group starts at each leading dialogue dash."""
    groups = []
    for line in (text or '').split('\n'):
        bare = re.sub(r'^(\s|<[^>]*>|\{[^}]*\})*', '', line)
        if bare.startswith('-') or bare.startswith(u'–') or bare.startswith(u'—') or not groups:
            groups.append(line)
        else:
            groups[-1] += '\n' + line
    return groups


def check(target, text, voices, persons=None):
    """Problems in one translated cue: [(person, expected, found, word)].
    voices: [(speaker_gender, listener_gender)], one per dialogue-dash line of the cue.
    persons: optional [set of 1/2], per voice: which persons the source line speaks of
    (only those are checked). A cue with dash lines that do not match the voices one to
    one - including one voice for two dash lines - is not checked."""
    if target not in LANGS or not voices:
        return []
    parts = segments(text)
    if len(parts) != len(voices):
        return []
    if persons is not None and len(persons) != len(voices):
        return []
    problems = []
    for k, (part, (speaker, listener)) in enumerate(zip(parts, voices)):
        for person, found, word in forms(target, part):
            if persons is not None and person not in persons[k]:
                continue
            expected = speaker if person == 1 else listener
            if expected in (MALE, FEMALE) and found != expected:
                problems.append((person, expected, found, word))
    return problems
