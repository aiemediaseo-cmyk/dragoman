# -*- coding: utf-8 -*-
"""Snapshots of subtitle files in the watched folders, and diffs between them.

Local folders (everything under special://) are walked with os.scandir, which
is fast even on a Fire TV Stick. Network shares (smb://, nfs://...) go through
xbmcvfs. Only subtitle and archive files are kept in a snapshot.
"""
import os
import time

import xbmcvfs

from . import kodi, util

MAX_ENTRIES_PER_ROOT = 5000
SLOW_ROOT_SECONDS = 1.0
SLOW_ROOT_EVERY = 5


class Root(object):
    """One watched folder."""

    def __init__(self, label, kind, path, depth):
        self.label = label      # e.g. 'temp', 'custom', 'addon:service.subtitles.x', 'video'
        self.kind = kind        # 'temp' | 'custom' | 'addon' | 'video' | 'extra'
        self.path = path        # as Kodi knows it (special://, smb://, /storage/...)
        self.depth = depth      # 1 = only this folder
        self.every = 1          # scan every N scans (raised for slow network roots)
        translated = kodi.translate_path(path) if path.startswith('special://') else path
        self.local = translated if (translated and not util.has_scheme(translated)) else None
        self.last_error = None
        self.last_stats = None

    def display(self, full_path):
        """Path for the log: special:// form for local roots (hides user names)."""
        if self.local and full_path.startswith(self.local.rstrip('/\\')):
            rel = full_path[len(self.local.rstrip('/\\')):].lstrip('/\\').replace('\\', '/')
            base = self.path if self.path.endswith('/') else self.path + '/'
            return base + rel
        return full_path

    def describe(self):
        where = self.local or self.path
        return '%-28s depth=%d  %s' % (self.label, self.depth, where)


class Entry(object):
    __slots__ = ('key', 'root', 'name', 'size', 'mtime', 'local')

    def __init__(self, key, root, name, size, mtime, local):
        self.key = key
        self.root = root
        self.name = name
        self.size = size
        self.mtime = mtime
        self.local = local

    @property
    def ext(self):
        return os.path.splitext(self.name)[1].lower()

    @property
    def kind(self):
        return util.name_kind(self.name)

    def subtitle_format(self):
        """'srt', 'vtt', 'ass', 'sub', ... or '' if this is not a subtitle.
        Files without a subtitle ending (e.g. TempSubtitle.en.eng) are
        recognised by their content; the answer is cached per file version."""
        kind = self.kind
        if kind == 'sub':
            return {'.ssa': 'ass'}.get(self.ext, self.ext[1:])
        if kind != 'maybe':
            return ''
        key = (self.key, self.size, self.mtime)
        if key not in _SNIFFED:
            head = read_head(self, 8192)
            _SNIFFED[key] = util.sniff_format(head) if head else ''
            if len(_SNIFFED) > 2000:
                _SNIFFED.clear()
        return _SNIFFED[key]

    @property
    def is_subtitle(self):
        return bool(self.subtitle_format())


_SNIFFED = {}


def _wanted(name):
    return util.name_kind(name) is not None


def _norm(path):
    return os.path.normcase(os.path.normpath(path))


def _skipped(path, skip):
    n = _norm(path)
    for s in skip:
        if n == s or n.startswith(s + os.sep):
            return True
    return False


def _walk_local(root, path, level, out, stats, skip):
    if stats['entries'] > MAX_ENTRIES_PER_ROOT:
        stats['truncated'] = True
        return
    try:
        iterator = os.scandir(path)
    except OSError as exc:
        if level == 0:
            stats['error'] = 'missing' if not os.path.exists(path) else str(exc)
        return
    subdirs = []
    with iterator:
        for de in iterator:
            stats['entries'] += 1
            try:
                if de.is_dir(follow_symlinks=False):
                    stats['dirs'] += 1
                    if level + 1 < root.depth and not _skipped(de.path, skip):
                        subdirs.append(de.path)
                    continue
                stats['files'] += 1
                if _wanted(de.name):
                    st = de.stat()
                    out[de.path] = Entry(de.path, root, de.name, st.st_size, st.st_mtime, True)
                    stats['subs'] += 1
            except OSError:
                continue
    for sub in subdirs:
        _walk_local(root, sub, level + 1, out, stats, skip)


def _walk_vfs(root, path, level, out, stats, skip):
    if stats['entries'] > MAX_ENTRIES_PER_ROOT:
        stats['truncated'] = True
        return
    if not path.endswith('/'):
        path += '/'
    try:
        dirs, files = xbmcvfs.listdir(path)
    except Exception as exc:
        if level == 0:
            stats['error'] = str(exc)
        return
    if level == 0 and not dirs and not files and not xbmcvfs.exists(path):
        stats['error'] = 'missing'
        return
    stats['entries'] += len(dirs) + len(files)
    stats['dirs'] += len(dirs)
    stats['files'] += len(files)
    for name in files:
        if not _wanted(name):
            continue
        full = path + name
        try:
            st = xbmcvfs.Stat(full)
            size, mtime = st.st_size(), st.st_mtime()
        except Exception:
            size, mtime = -1, 0
        out[full] = Entry(full, root, name, size, mtime, False)
        stats['subs'] += 1
    if level + 1 < root.depth:
        for name in dirs:
            _walk_vfs(root, path + name + '/', level + 1, out, stats, skip)


def snapshot(roots, skip_paths, scan_no=0, previous=None):
    """Return ({key: Entry}, {root.label: stats}) for all roots.

    Slow roots (network shares) are only rescanned every root.every scans;
    in between, their entries from `previous` are carried over.
    """
    skip = [_norm(p) for p in skip_paths if p]
    files = {}
    all_stats = {}
    for root in roots:
        if previous is not None and root.every > 1 and scan_no % root.every:
            for key, entry in previous.items():
                if entry.root is root:
                    files[key] = entry
            continue
        stats = {'entries': 0, 'dirs': 0, 'files': 0, 'subs': 0,
                 'truncated': False, 'error': None, 'secs': 0.0}
        started = time.time()
        try:
            if root.local:
                _walk_local(root, root.local, 0, files, stats, skip)
            else:
                _walk_vfs(root, root.path, 0, files, stats, skip)
        except Exception as exc:  # never let one folder break the scan
            stats['error'] = str(exc)
        stats['secs'] = time.time() - started
        root.last_stats = stats
        all_stats[root.label] = stats
    return files, all_stats


def diff(old, new):
    added = sorted(k for k in new if k not in old)
    changed = sorted(k for k in new if k in old and
                     (new[k].size != old[k].size or new[k].mtime != old[k].mtime))
    removed = sorted(k for k in old if k not in new)
    return added, changed, removed


def read_head(entry, limit=2 * 1024 * 1024):
    """First `limit` bytes of a file, or None."""
    try:
        if entry.local:
            with open(entry.key, 'rb') as handle:
                return handle.read(limit)
        handle = xbmcvfs.File(entry.key)
        try:
            return bytes(handle.readBytes(limit))
        finally:
            handle.close()
    except Exception:
        return None


def stat_entry(entry):
    """(size, mtime) of an entry's file right now, or None."""
    try:
        if entry.local:
            st = os.stat(entry.key)
            return st.st_size, st.st_mtime
        st = xbmcvfs.Stat(entry.key)
        return st.st_size(), st.st_mtime()
    except Exception:
        return None
