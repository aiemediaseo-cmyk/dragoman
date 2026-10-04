# -*- coding: utf-8 -*-
"""Shared state behind the stand-in Kodi modules. Tests drive it directly."""
import os
import re


class _State(object):
    def reset(self, base, addon_dir):
        self.base = base
        self.addon_dir = addon_dir
        self.now = 1790000000.0
        self.ticks = 0
        self.abort = False
        self.actions = {}          # tick -> callable
        self.kodi_log = []
        self.labels = {}
        self.conds = {}
        self.jsonrpc = {}
        self.settings = {}
        self.window_props = {}
        self.dialog = 9999
        self.window = 12005
        self.playing = False
        self.file = ''
        self.time = 0.0
        self.total = 0.0
        self.cur_sub = ''
        self.subs = []
        self.meta = {}
        self.players = []
        self.monitors = []
        self.dialog_calls = []
        self.select_answers = []
        self.browse_answer = ''
        self.input_answer = ''
        self.input_answers = []    # used first, one per input() call
        self.inputs = []           # what each input() was called with
        self.yesno_labels = []
        self.yesnocustom_answers = []   # 1 yes, 0 no, 2 custom, -1 cancelled (default)
        self.yesno_answer = True
        self.yesno_answers = []    # used first, one per yesno() call
        self.multiselect_answer = None
        self.loaded_subs = []
        self.reject_subs = None
        self.builtins = []
        self.progress_bg = []

    # --------------------------------------------------------------- paths
    def translate(self, path):
        mapping = (
            ('special://temp/', 'temp/'),
            ('special://logpath/', 'temp/'),
            ('special://profile/', 'profile/'),
            ('special://masterprofile/', 'profile/'),
            ('special://home/', 'home/'),
        )
        for prefix, sub in mapping:
            if path.startswith(prefix):
                return os.path.join(self.base, sub, path[len(prefix):])
            if path + '/' == prefix:
                return os.path.join(self.base, sub)
        return path

    def vfs_local(self, path):
        """Map smb://user:pass@host/share/x to <base>/smb/host/share/x."""
        m = re.match(r'^(smb|nfs)://(?:[^@/]*@)?([^/]+)/(.*)$', path)
        if m:
            return os.path.join(self.base, m.group(1), m.group(2), m.group(3))
        if path.startswith('special://'):
            return self.translate(path)
        return path

    # --------------------------------------------------------------- clock
    def clock(self):
        return self.now

    def on_wait(self, timeout):
        self.ticks += 1
        self.now += timeout
        if self.playing:
            self.time += timeout
        action = self.actions.get(self.ticks)
        if action is not None:
            action()


STATE = _State()
