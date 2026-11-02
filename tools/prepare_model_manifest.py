"""Maintainer tool: pin Windows wheels and a Hugging Face revision for setup.

Resolve packaging/windows/requirements-multimodal.in with uv pip compile to
build/pylock.multimodal.toml first (Python 3.11, Windows, --torch-backend cpu).
The app only downloads entries from the resulting checked-in JSON manifest.
"""
import hashlib
import json
from pathlib import Path
import tomllib
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = '914f7f89142e33e77833254d9c9b90c3cef7303b'


def main():
    artifacts = [dict(kind='python', name='python-3.11.9-embed-amd64.zip', bytes=11249023,
                      url='https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip',
                      sha256='009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b')]
    lock = tomllib.loads((ROOT / 'build/pylock.multimodal.toml').read_text('utf-8'))
    packages = {}
    for package in lock['packages']:
        wheels = package.get('wheels', [])
        if len(wheels) != 1:
            raise ValueError('Expected one Windows x64 wheel: ' + package['name'])
        wheel = wheels[0]
        if 'size' not in wheel:
            with urllib.request.urlopen(urllib.request.Request(wheel['url'], method='HEAD'), timeout=30) as response:
                wheel['size'] = int(response.headers['Content-Length'])
        name = urllib.parse.unquote(urllib.parse.urlparse(wheel['url']).path.rsplit('/', 1)[1])
        artifacts.append(dict(kind='wheel', name=name, bytes=wheel['size'],
                              url=wheel['url'], sha256=wheel['hashes']['sha256']))
        packages[package['name']] = package['version']
    artifacts.append(dict(kind='ffmpeg', name='ffmpeg-8.1.2-essentials_build.zip', bytes=109728040,
                          url='https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/ffmpeg-8.1.2-essentials_build.zip',
                          sha256='db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec'))
    url=f'https://huggingface.co/api/models/google/embeddinggemma-2/tree/{REVISION}?recursive=true&expand=true'
    with urllib.request.urlopen(url, timeout=30) as response:
        files = json.load(response)
    for file in files:
        if file['type'] != 'file' or file['path'] in ('.gitattributes', 'README.md'):
            continue
        url=f"https://huggingface.co/google/embeddinggemma-2/resolve/{REVISION}/{file['path']}"
        if file.get('lfs'):
            digest = file['lfs']['oid']
        else:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
            assert len(data) == file['size']
            assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == file['oid']
            digest = hashlib.sha256(data).hexdigest()
        artifacts.append(dict(kind='model', name=file['path'], path=file['path'], bytes=file['size'], url=url, sha256=digest))
    digest = hashlib.sha256(json.dumps(artifacts, sort_keys=True).encode()).hexdigest()[:16]
    manifest = dict(format_version=1, id='embeddinggemma2-cpu-' + digest,
                    model_id='google/embeddinggemma-2', revision=REVISION, packages=packages,
                    working_bytes=3 * 1024**3, artifacts=artifacts)
    (ROOT / 'everytensor/model-assets.json').write_text(json.dumps(manifest, indent=2), 'utf-8')
    print('Pinned',len(artifacts),'artifacts;',round(sum(a['bytes'] for a in artifacts)/1024**3,2),'GiB total')


if __name__ == '__main__':
    main()
