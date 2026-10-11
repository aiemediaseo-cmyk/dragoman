# -*- coding: utf-8 -*-
# Dragoman - background service entry point (xbmc.service).
import sys

if __name__ == '__main__':
    if sys.argv and sys.argv[0].startswith('plugin://'):
        # a call from Kodi's subtitle dialog that reached this file (subtitle.py handles
        # those; addon.xml lists it first so Kodi picks it): never start a second service
        from resources.lib.dragoman import submodule
        submodule.run(sys.argv)
    else:
        from resources.lib.dragoman import service
        service.run()
