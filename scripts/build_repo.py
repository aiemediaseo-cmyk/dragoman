#!/usr/bin/env python3
"""Build the Kodi repository that lives in docs/.

    python3 scripts/build_repo.py          # build after changing an add-on
    python3 scripts/build_repo.py --check  # CI: fail if docs/ is out of date

docs/ is served two ways:
  * raw.githubusercontent.com/.../main/docs/  - what repository.dragoman reads
    (addons.xml, its checksum, the zips and their hashes, icons and fanart)
  * GitHub Pages (https://aiemediaseo-cmyk.github.io/dragoman/) - the page you
    add as a file source in Kodi to install the repository zip

For each add-on folder (addon.xml at its top) this makes
docs/<id>/<id>-<version>.zip plus <zip>.sha256, and copies the add-on's icon
and fanart where Kodi's add-on browser looks for them. Then it writes
docs/addons.xml, docs/addons.xml.sha256 and docs/index.html. Zips are built
byte-for-byte reproducibly, so an unchanged add-on gives an unchanged zip; if
the files changed but the version did not, the build stops and asks for a
version bump (Kodi only updates when the version goes up).
"""
import hashlib
import io
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
ADDONS = ['service.subtitles.dragoman', 'repository.dragoman']
REPOSITORY_ADDON = 'repository.dragoman'
INCLUDE = ('.py', '.xml', '.po', '.md', '.txt', '.png', '.jpg')
ZIP_TIME = (2020, 1, 1, 0, 0, 0)
NEWS_LIMIT = 1500          # Kodi's repository checker warns above this


class BuildError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_addon(folder):
    path = os.path.join(ROOT, folder, 'addon.xml')
    root = ET.parse(path).getroot()
    addon_id, version = root.get('id'), root.get('version')
    if addon_id != folder:
        raise BuildError('%s: id %r does not match its folder name' % (path, addon_id))
    if not version:
        raise BuildError('%s: no version' % path)
    news = root.find('.//news')
    if news is not None and len(news.text or '') > NEWS_LIMIT:
        print('warning: %s news is %d characters (Kodi suggests at most %d)'
              % (addon_id, len(news.text), NEWS_LIMIT))
    assets = [el.text.strip() for el in root.iter() if el.tag in ('icon', 'fanart', 'banner', 'clearlogo',
                                                                   'screenshot') and el.text]
    with io.open(path, encoding='utf-8') as handle:
        xml_text = handle.read()
    return addon_id, version, assets, xml_text


def addon_files(folder):
    base = os.path.join(ROOT, folder)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.') and d != '__pycache__')
        for name in sorted(filenames):
            if name.startswith('.'):
                continue
            if not name.endswith(INCLUDE):
                raise BuildError('unexpected file in the add-on: %s' % os.path.join(dirpath, name))
            full = os.path.join(dirpath, name)
            yield full, os.path.relpath(full, os.path.dirname(base)).replace(os.sep, '/')


def build_zip(folder):
    """Reproducible zip of the add-on folder, with the folder itself at the top (Kodi's layout)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        seen_dirs = set()
        for full, arcname in addon_files(folder):
            parts = arcname.split('/')[:-1]
            for i in range(1, len(parts) + 1):
                directory = '/'.join(parts[:i]) + '/'
                if directory not in seen_dirs:
                    seen_dirs.add(directory)
                    info = zipfile.ZipInfo(directory, ZIP_TIME)
                    info.external_attr = (0o40755 << 16) | 0x10
                    archive.writestr(info, b'')
            info = zipfile.ZipInfo(arcname, ZIP_TIME)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(full, 'rb') as handle:
                archive.writestr(info, handle.read())
    return buffer.getvalue()


def planned_files():
    """{path under docs/: bytes} for everything the current sources produce."""
    files = {}
    entries = []
    for folder in ADDONS:
        addon_id, version, assets, xml_text = read_addon(folder)
        data = build_zip(folder)
        zip_name = '%s/%s-%s.zip' % (addon_id, addon_id, version)
        files[zip_name] = data
        files[zip_name + '.sha256'] = sha256(data).encode('ascii')
        for asset in assets:
            source = os.path.join(ROOT, folder, asset)
            if not os.path.isfile(source):
                raise BuildError('%s: asset %s is missing' % (addon_id, asset))
            with open(source, 'rb') as handle:
                files['%s/%s' % (addon_id, asset)] = handle.read()
        if addon_id == REPOSITORY_ADDON:
            files['%s-%s.zip' % (addon_id, version)] = data     # the one people install by hand
            repo_zip = '%s-%s.zip' % (addon_id, version)
        body = xml_text.split('?>', 1)[1].strip() if xml_text.lstrip().startswith('<?xml') else xml_text.strip()
        entries.append(body)
    index = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n'
             + '\n'.join(entries) + '\n</addons>\n').encode('utf-8')
    ET.fromstring(index)       # must stay valid XML
    files['addons.xml'] = index
    files['addons.xml.sha256'] = sha256(index).encode('ascii')
    files['index.html'] = index_page(repo_zip).encode('utf-8')
    files['.nojekyll'] = b''
    return files


def index_page(repo_zip):
    # Kodi lists a link only when its text is exactly its href (filesystem/HTTPDirectory.cpp),
    # so the zip link below is the only entry Kodi shows; the rest is for people.
    return '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dragoman Kodi repository</title>
<style>
body {{ font-family: system-ui, sans-serif; background: #141a45; color: #fbf5e9; max-width: 40rem;
       margin: 0 auto; padding: 2rem 1rem; line-height: 1.5; }}
a {{ color: #ffd36b; }}
code {{ background: rgba(255,255,255,.08); padding: .1rem .3rem; border-radius: .25rem; }}
img {{ border-radius: 1rem; }}
</style>
</head>
<body>
<img src="repository.dragoman/icon.png" width="96" height="96" alt="">
<h1>Dragoman Kodi repository</h1>
<p>Translates subtitles into your language while the video keeps playing.</p>
<p>In Kodi: File manager &gt; Add source &gt; this page's address. Then Add-ons &gt; Install from zip file
&gt; that source &gt; the zip below.</p>
<p><a href="{zip}">{zip}</a>
</p>
<p>Source code and instructions: github.com/aiemediaseo-cmyk/dragoman</p>
</body>
</html>
'''.format(zip=repo_zip)


def check(files):
    problems = []
    for name, data in sorted(files.items()):
        path = os.path.join(DOCS, name)
        if not os.path.isfile(path):
            problems.append('missing: docs/%s' % name)
            continue
        with open(path, 'rb') as handle:
            if handle.read() != data:
                problems.append('out of date: docs/%s' % name)
    return problems


def write(files):
    for name, data in sorted(files.items()):
        path = os.path.join(DOCS, name)
        if name.endswith('.zip') and os.path.isfile(path):
            with open(path, 'rb') as handle:
                if handle.read() != data:
                    raise BuildError('docs/%s already exists with other contents - '
                                     'raise the version in addon.xml' % name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as handle:
            handle.write(data)
    # the repository zip offered on the page: only the current one
    for name in os.listdir(DOCS):
        if name.startswith(REPOSITORY_ADDON + '-') and name.endswith('.zip') and name not in files:
            os.remove(os.path.join(DOCS, name))


def main(argv):
    try:
        files = planned_files()
        if '--check' in argv:
            problems = check(files)
            for line in problems:
                print(line)
            if problems:
                print('docs/ is out of date: run python3 scripts/build_repo.py and commit the result')
                return 1
            print('docs/ is up to date')
            return 0
        write(files)
    except BuildError as exc:
        print('error: %s' % exc)
        return 1
    for folder in ADDONS:
        addon_id, version, _assets, _xml = read_addon(folder)
        print('%s %s' % (addon_id, version))
    print('docs/ updated - commit and push to publish')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
