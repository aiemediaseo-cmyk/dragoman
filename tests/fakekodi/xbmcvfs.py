# -*- coding: utf-8 -*-
"""Stand-in for Kodi's xbmcvfs module (special:// and smb:// mapped to a temp dir)."""
import os

from kodistate import STATE


def translatePath(path):
    return STATE.translate(path)


def exists(path):
    return os.path.exists(STATE.vfs_local(path))


def mkdirs(path):
    local = STATE.vfs_local(path)
    if not os.path.isdir(local):
        os.makedirs(local)
    return True


def listdir(path):
    local = STATE.vfs_local(path)
    if not os.path.isdir(local):
        return [], []
    dirs, files = [], []
    for name in sorted(os.listdir(local)):
        (dirs if os.path.isdir(os.path.join(local, name)) else files).append(name)
    return dirs, files


class Stat(object):
    def __init__(self, path):
        self._st = os.stat(STATE.vfs_local(path))

    def st_size(self):
        return self._st.st_size

    def st_mtime(self):
        return int(self._st.st_mtime)


class File(object):
    def __init__(self, path, mode='r'):
        self._fh = open(STATE.vfs_local(path), 'wb' if mode == 'w' else 'rb')

    def readBytes(self, num=0):
        return bytearray(self._fh.read(num) if num else self._fh.read())

    def read(self, num=0):
        data = self._fh.read(num) if num else self._fh.read()
        return data.decode('utf-8', 'replace')

    def write(self, data):
        if isinstance(data, str):
            data = data.encode('utf-8')
        self._fh.write(bytes(data))
        return True

    def close(self):
        self._fh.close()
