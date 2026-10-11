# -*- coding: utf-8 -*-
"""Menu (Program add-ons > Dragoman) and the Settings buttons.

RunScript(service.subtitles.dragoman[,action]) with action one of:
translate, enterkey, importurl, importkey, testkey, removekey, leavealone, clearcache, sendlog,
syncassistant, undotiming, fixframerate (timing of the subtitle that is showing; during playback),
oldaddon (asked by the service when the earlier version of the add-on is still switched on)

A remote button can be mapped to RunScript(service.subtitles.dragoman,syncassistant).

The API key and the languages to leave alone are saved in state.json (see
state.py), never in settings.xml, so the settings dialog cannot undo them.
"""
import json
import os
from urllib.parse import urlsplit

import xbmc
import xbmcgui
import xbmcvfs

from . import cache as cache_mod
from . import diag, gemini, keysource, kodi, languages, migrate, state, sync, translator
from .config import Config
from .log import get_log

TEST_LINES = ['Good evening. Where have you been all day?', "I'll tell you later, I promise."]


def run(argv):
    action = argv[1] if len(argv) > 1 else ''
    handlers = {'translate': translate_now, 'enterkey': enter_key, 'importurl': import_key_url,
                'importkey': import_key, 'testkey': test_key, 'removekey': remove_key,
                'leavealone': choose_leave_alone, 'clearcache': clear_cache, 'sendlog': send_log,
                'oldaddon': migrate.offer_to_disable_old, 'syncassistant': sync.run_assistant_from_menu,
                'undotiming': sync.undo_from_menu, 'fixframerate': sync.framerate_from_menu}
    try:
        handlers.get(action, main_menu)()
    except Exception:
        get_log().exception('menu action %r failed' % action)
        xbmcgui.Dialog().ok(kodi.addon_name(), kodi.text(32260))


def _status():
    running, _age = diag.service_status()
    return kodi.text(32200 if running else 32201)


def main_menu():
    try:
        migrate.import_old_setup()        # normally the service has done this already
    except Exception:
        get_log().exception('copying the old setup failed')
    migrate.offer_to_disable_old()
    cfg = Config.load()
    if not cfg.api_key and not cfg.setup_done:
        first_run()
    entries = [(32202, translate_now), (32427, sync.run_assistant_from_menu), (32428, sync.undo_from_menu),
               (32203, enter_key), (32273, import_key_url), (32204, import_key),
               (32205, test_key), (32206, choose_leave_alone), (32207, clear_cache), (32208, view_log),
               (32209, send_log), (32236, remove_key), (32210, open_settings)]
    while True:
        heading = '%s %s - %s' % (kodi.addon_name(), kodi.addon_version(), _status())
        index = xbmcgui.Dialog().select(heading, [kodi.text(sid) for sid, _fn in entries])
        if index < 0:
            return
        entries[index][1]()


def first_run():
    dialog = xbmcgui.Dialog()
    dialog.ok(kodi.addon_name(), kodi.text(32211))
    choice = dialog.select(kodi.text(32212), [kodi.text(32203), kodi.text(32273), kodi.text(32204),
                                              kodi.text(32213)])
    if choice == 0:
        enter_key()
    elif choice == 1:
        import_key_url()
    elif choice == 2:
        import_key()
    state.update(setup_done=True)
    cfg = Config.load()
    if dialog.yesno(kodi.addon_name(), kodi.text(32214, lang=kodi.text(languages.string_id(cfg.target)))):
        open_settings()   # the language lives in the settings dialog


# ---------------------------------------------------------------------- actions
def translate_now():
    running, _age = diag.service_status()
    if not running:
        xbmcgui.Dialog().ok(kodi.addon_name(), kodi.text(32201))
        return
    kodi.home_window().setProperty('dragoman.request', 'translate_now')
    xbmc.executebuiltin('NotifyAll(%s,translate_now)' % kodi.ADDON_ID)
    kodi.notify(kodi.text(32235), 2500)


def _user_agent():
    return 'Mozilla/5.0 (compatible; Kodi-Dragoman/%s)' % kodi.addon_version()


def _confirm_key(key, found=False):
    """Show the whole key so the user can check it. found=False (typed): Save / Edit,
    found=True (from a web address or a file): Save / Cancel."""
    message = kodi.text(32283 if found else 32278, key=key, n=len(key))
    if not keysource.looks_like_gemini_key(key):
        message += '[CR]' + kodi.text(32279)
    return xbmcgui.Dialog().yesno(kodi.addon_name(), message,
                                  nolabel=kodi.text(32284 if found else 32281), yeslabel=kodi.text(32280))


def _save_key(key, from_paste=False):
    key = key.strip()
    state.update(api_key=key)
    get_log().info('API key saved (%s)' % Config.load().key_hint())
    kodi.notify(kodi.text(32221), 3000)
    if xbmcgui.Dialog().yesno(kodi.addon_name(), kodi.text(32282 if from_paste else 32224)):
        test_key()


def _clean_typed(typed):
    typed = (typed or '').strip()
    return keysource.find_key(typed) or ''.join(typed.split())


def enter_key():
    """Type the key. The keyboard shows what is typed (no hidden input), it starts
    with the saved key, and the whole key is shown again before it is saved."""
    current = Config.load().api_key
    typed = current
    while True:
        typed = xbmcgui.Dialog().input(kodi.text(32220), defaultt=typed, type=xbmcgui.INPUT_ALPHANUM, option=0)
        key = _clean_typed(typed)
        if not key:
            return
        if key == current:
            kodi.notify(kodi.text(32287), 2000)
            return
        if _confirm_key(key):
            _save_key(key)
            return
        typed = key          # Edit: back to the keyboard with what was typed


def find_key(text):
    return keysource.find_key(text)


def _choose_key(keys):
    """One key from a list of candidates (the user picks when there are several)."""
    if len(keys) == 1:
        return keys[0]
    index = xbmcgui.Dialog().select(kodi.text(32291), keys)
    return keys[index] if index >= 0 else ''


def _host(address):
    try:
        return urlsplit(keysource.normalize(address)).hostname or '?'
    except ValueError:
        return '?'


def import_key_url():
    """Fetch the key from a web address the user types, e.g. an unlisted Pastebin paste.
    Only the site's name is logged or kept: the full address leads to the key."""
    dialog = xbmcgui.Dialog()
    address = state.get('key_site') or 'pastebin.com/'
    while True:
        address = dialog.input(kodi.text(32274), defaultt=address, type=xbmcgui.INPUT_ALPHANUM, option=0)
        address = (address or '').strip()
        if not address:
            return
        host = _host(address)
        progress = xbmcgui.DialogProgress()
        progress.create(kodi.addon_name(), kodi.text(32275, host=host))
        error = None
        try:
            keys, _used = keysource.key_from_address(address, _user_agent())
        except keysource.FetchError as exc:
            error = exc
        except Exception as exc:
            get_log().exception('key import from %s failed' % host)
            error = keysource.FetchError('network', str(exc) or exc.__class__.__name__)
        finally:
            progress.close()
        if error is None:
            break
        get_log().warn('key import from %s failed: %s %s' % (host, error.kind, error.message))
        if error.kind == 'address':
            dialog.ok(kodi.addon_name(), kodi.text(32285))
        elif error.kind == 'nokey':
            dialog.ok(kodi.addon_name(), kodi.text(32277))
        else:
            dialog.ok(kodi.addon_name(), kodi.text(32276, host=host, error=error.message))
        # back to the keyboard to fix the address; Back there cancels
    state.update(key_site=host + '/')
    get_log().info('API key found at %s (%d candidate%s)' % (host, len(keys), '' if len(keys) == 1 else 's'))
    key = _choose_key(keys)
    if key and _confirm_key(key, found=True):
        _save_key(key, from_paste=True)


def import_key():
    dialog = xbmcgui.Dialog()
    path = dialog.browse(1, kodi.text(32222), 'files', '.txt|.key|.json|.cfg|.conf|.env')
    if not path:
        return
    try:
        handle = xbmcvfs.File(path)
        try:
            data = keysource.decode(bytes(handle.readBytes(65536)))
        finally:
            handle.close()
    except Exception as exc:
        dialog.ok(kodi.addon_name(), kodi.text(32229, error='%s' % exc))
        return
    keys = keysource.keys_in(data)
    if not keys:
        dialog.ok(kodi.addon_name(), kodi.text(32223))
        return
    key = _choose_key(keys)
    if key and _confirm_key(key, found=True):
        _save_key(key)


def test_key():
    dialog = xbmcgui.Dialog()
    cfg = Config.load()
    if not cfg.api_key:
        dialog.ok(kodi.addon_name(), kodi.text(32225))
        return
    progress = xbmcgui.DialogProgress()
    progress.create(kodi.addon_name(), kodi.text(32226))
    model = cfg.models[0]
    client = gemini.GeminiClient(cfg.api_key, model, timeout=60,
                                 user_agent='Kodi-Dragoman/%s' % kodi.addon_version())
    items = [{'id': i + 1, 'text': t} for i, t in enumerate(TEST_LINES)]
    result, error, available = None, None, None
    try:
        answer = client.generate_json(translator.system_prompt(cfg.target),
                                      json.dumps({'cues': items}, ensure_ascii=False), translator.SCHEMA)
        result = translator.Translator._validate(answer, items, [0, 1])
    except gemini.GeminiError as exc:
        error = exc
        if exc.kind == 'model':
            try:
                available = client.list_models()
            except gemini.GeminiError:
                available = []
    except Exception as exc:
        error = exc
    finally:
        progress.close()           # before any message, so it is not left behind it
    if isinstance(error, gemini.GeminiError):
        if error.kind == 'model':
            free = [m for m in cfg.models if m in (available or [])]
            dialog.ok(kodi.addon_name(), kodi.text(32228, model=model, models=', '.join(free) or '-'))
            return
        return _key_error(dialog, error)
    if error is not None:
        dialog.ok(kodi.addon_name(), kodi.text(32229, error='%s' % error))
        return
    get_log().info('key test ok with %s: %r' % (model, result.get(0)))
    dialog.ok(kodi.addon_name(), kodi.text(32227, model=model, src=TEST_LINES[0], dst=result.get(0, '')))


def _key_error(dialog, exc):
    get_log().warn('key test failed: %s' % exc)
    messages = {'auth': 32308, 'region': 32309, 'network': 32310, 'timeout': 32310, 'rate': 32311,
                'quota_day': 32307}
    if exc.kind in messages:
        text = kodi.text(messages[exc.kind])
    else:
        text = kodi.text(32229, error=exc.message[:200])
    dialog.ok(kodi.addon_name(), text)


def choose_leave_alone():
    cfg = Config.load()
    codes = [row[0] for row in languages.LANGUAGES]
    names = [kodi.text(row[3]) for row in languages.LANGUAGES]
    preselect = [i for i, code in enumerate(codes) if code in cfg.leave_alone]
    try:
        chosen = xbmcgui.Dialog().multiselect(kodi.text(32230), names, preselect=preselect)
    except TypeError:
        chosen = xbmcgui.Dialog().multiselect(kodi.text(32230), names)
    if chosen is None:
        return
    state.update(leave_alone=[codes[i] for i in chosen])
    get_log().info('languages to leave alone: %s' % (','.join(codes[i] for i in chosen) or '-'))
    kodi.notify(kodi.text(32232), 2000)


def clear_cache():
    if not xbmcgui.Dialog().yesno(kodi.addon_name(), kodi.text(32233)):
        return
    profile = kodi.profile_dir()
    count = cache_mod.Cache(os.path.join(profile, 'cache')).clear()
    count += cache_mod.Cache(os.path.join(profile, 'subtitles')).clear()
    get_log().info('cleared %d saved files' % count)
    kodi.notify(kodi.text(32234, n=count), 2500)


def _view(text):
    dialog = xbmcgui.Dialog()
    try:
        dialog.textviewer(kodi.addon_name(), text, True)
    except TypeError:
        dialog.textviewer(kodi.addon_name(), text)


def view_log():
    _view(get_log().read_tail(100000) or kodi.text(32251))


def send_log():
    dialog = xbmcgui.Dialog()
    index = dialog.select(kodi.text(32240), [kodi.text(32241), kodi.text(32242), kodi.text(32243)])
    if index == 0:
        if not dialog.yesno(kodi.addon_name(), kodi.text(32244)):
            return
        progress = xbmcgui.DialogProgress()
        progress.create(kodi.addon_name(), kodi.text(32245))
        try:
            url = diag.upload(diag.build_report(diag.UPLOAD_LIMIT))
        except diag.UploadError as exc:
            progress.close()
            dialog.ok(kodi.addon_name(), kodi.text(32247, error='%s' % exc))
            return
        progress.close()
        get_log().info('log uploaded (paste key %s)' % url.rsplit('/', 1)[-1])
        dialog.ok(kodi.addon_name(), kodi.text(32246, url=url))
    elif index == 1:
        folder = dialog.browse(3, kodi.text(32248), 'files')
        if not folder:
            return
        try:
            path = diag.save(diag.build_report(), folder)
        except Exception as exc:
            dialog.ok(kodi.addon_name(), kodi.text(32250, error='%s' % exc))
            return
        dialog.ok(kodi.addon_name(), kodi.text(32249, path=path))
    elif index == 2:
        _view(diag.build_report(diag.VIEW_LIMIT))


def remove_key():
    if not Config.load().api_key:
        xbmcgui.Dialog().ok(kodi.addon_name(), kodi.text(32225))
        return
    if xbmcgui.Dialog().yesno(kodi.addon_name(), kodi.text(32237)):
        state.update(api_key='')
        get_log().info('API key removed')
        kodi.notify(kodi.text(32232), 2000)


def open_settings():
    # A fresh Addon object, so the dialog starts from what is saved on disk now.
    kodi.addon(refresh=True).openSettings()
