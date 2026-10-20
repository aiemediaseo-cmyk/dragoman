# -*- coding: utf-8 -*-
"""Add-on settings in one place. Read fresh with Config.load() whenever needed."""
from . import kodi, languages, others, state

MODEL_CHOICES = [
    'gemini-3.5-flash-lite',
    'gemini-3.6-flash',
    'gemini-3.5-flash',
    'gemini-3.1-flash-lite',
    'gemini-3.7-flash',
    'gemini-3.8-flash',
]   # the settings enum has one more entry at the end: "Other (type the model id)"
MODE_AUTO, MODE_ASK, MODE_OFF = 0, 1, 2
TIMING_AUTO, TIMING_ASK, TIMING_OFF = 0, 1, 2   # frame-rate drift check
# Sync assistant: taken off each press - the user's reaction and the OK dialog's closing
# animation (Kodi returns from the dialog only after it, about 0.2 s in Estuary/Confluence)
REACTION_MS = 450


def kodi_setting(setting_id):
    result, err = kodi.jsonrpc('Settings.GetSettingValue', {'setting': setting_id})
    if err or not isinstance(result, dict):
        return None
    return result.get('value')


def resolve_auto_target():
    """Kodi's preferred subtitle language, else Kodi's interface language, else English."""
    code = languages.normalize('%s' % (kodi_setting('locale.subtitlelanguage') or ''))
    if code:
        return code, 'Kodi subtitle language'
    code = languages.from_kodi_ui_language('%s' % (kodi_setting('locale.language') or ''))
    if code:
        return code, 'Kodi interface language'
    return 'en', 'fallback'


class Config(object):
    @classmethod
    def load(cls):
        kodi.addon(refresh=True)
        c = cls()
        index = kodi.setting_int('target_language', 0, 0, len(languages.LANGUAGES))
        if index == 0:
            c.target, c.target_source = resolve_auto_target()
        else:
            c.target, c.target_source = languages.LANGUAGES[index - 1][0], 'settings'
        saved = state.load()
        if isinstance(saved.get('leave_alone'), list):
            c.leave_alone = languages.parse_code_list(','.join(saved['leave_alone']))
        else:   # v0.1.0 kept it in settings.xml
            c.leave_alone = languages.parse_code_list(kodi.setting('leave_alone', ''))
        if isinstance(saved.get('ignore_addons'), list):
            c.ignore_addons = others.clean_ids(saved['ignore_addons'])
        else:   # not chosen yet: the known translator add-ons
            c.ignore_addons = list(others.DEFAULT_IGNORED)
        c.src_manual = kodi.setting_bool('src_manual', True)
        c.src_auto = kodi.setting_bool('src_auto', True)
        c.src_local = kodi.setting_bool('src_local', True)
        c.mode = kodi.setting_int('mode', MODE_AUTO, 0, 2)
        c.api_key = ('%s' % (saved.get('api_key') or kodi.setting('api_key', ''))).strip()
        c.setup_done = bool(saved.get('setup_done'))
        model_index = kodi.setting_int('model', 0, 0, len(MODEL_CHOICES))
        if model_index < len(MODEL_CHOICES):
            primary = MODEL_CHOICES[model_index]
        else:
            primary = kodi.setting('custom_model', '').strip() or MODEL_CHOICES[0]
        c.fallback = kodi.setting_bool('fallback_models', True)
        c.models = [primary] + [m for m in MODEL_CHOICES if m != primary]
        c.chunk_size = kodi.setting_int('chunk_size', 100, 20, 200)
        c.rpm = kodi.setting_int('rpm', 8, 1, 30)
        c.cache_days = kodi.setting_int('cache_days', 30, 0, 365)
        c.show_progress = kodi.setting_bool('show_progress', True)
        c.notify_skips = kodi.setting_bool('notify_skips', True)
        c.debug = kodi.setting_bool('debug_log', False)
        c.kodi_charset = '%s' % (kodi_setting('subtitles.charset') or '')
        c.timing_check = kodi.setting_int('timing_check', TIMING_ASK, 0, 2)
        c.reaction_ms = kodi.setting_int('reaction_ms', REACTION_MS, 0, 1500)
        return c

    def source_enabled(self, source):
        return {'manual': self.src_manual, 'auto': self.src_auto, 'local': self.src_local}.get(source, False)

    def skip_languages(self):
        return [self.target] + [c for c in self.leave_alone if c != self.target]

    def key_hint(self):
        if not self.api_key:
            return 'not set'
        return 'set (%s...%s, %d chars)' % (self.api_key[:4], self.api_key[-4:], len(self.api_key))

    def summary(self):
        return ('target=%s (%s) leave_alone=%s ignore_addons=%s sources=%s mode=%s key: %s | models=%s fallback=%s '
                'chunk=%d rpm=%d cache_days=%d progress=%s notify_skips=%s debug=%s charset=%s '
                'timing=%s reaction=%dms' % (
                    self.target, self.target_source, ','.join(self.leave_alone) or '-',
                    ','.join(self.ignore_addons) or '-',
                    ','.join(s for s in ('manual', 'auto', 'local') if self.source_enabled(s)) or '-',
                    ('auto', 'ask', 'off')[self.mode], self.key_hint(), self.models[0], self.fallback,
                    self.chunk_size, self.rpm, self.cache_days, self.show_progress, self.notify_skips,
                    self.debug, self.kodi_charset or '?', ('auto', 'ask', 'off')[self.timing_check],
                    self.reaction_ms))
