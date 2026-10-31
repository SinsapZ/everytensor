"""Explicit online installer for the local fast semantic model.

Only this setup command downloads data. The runtime is entirely local. The
published static MRL table is streamed to a 256d FP16 mmap in bounded memory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import struct
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from everytensor.quick_embeddings import MODEL_ID, FORMAT_VERSION, sha256_file

DEFAULT_REVISION = 'b68f4122911bcffcd6e1f695f2d99cd6788972d8'


def request(url, **kwargs):
    headers = {'User-Agent': 'EveryTensor-explicit-model-setup/1.0', **kwargs.pop('headers', {})}
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60, **kwargs)


def download(url, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + '.part')
    partial_source = destination.with_suffix(destination.suffix + '.part.source')
    # Never resume bytes from a different revision into the requested file.
    if not partial_source.exists() or partial_source.read_text('utf-8') != url:
        partial.unlink(missing_ok=True)
        partial_source.write_text(url, encoding='utf-8')
    for attempt in range(3):
        existing = partial.stat().st_size if partial.exists() else 0
        try:
            with request(url, headers={'Range': f'bytes={existing}-'} if existing else {}) as response:
                append = response.status == 206 and existing > 0
                if append:
                    content_range = response.headers.get('Content-Range', '')
                    if not content_range.startswith(f'bytes {existing}-'):
                        raise ValueError('Download server returned a different byte range')
                read = existing if append else 0
                content_length = response.headers.get('Content-Length')
                expected = read + int(content_length) if content_length is not None else None
                last_report = time.monotonic()
                with partial.open('ab' if append else 'wb') as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
                        read += len(block)
                        if time.monotonic() - last_report > 2:
                            print(f'{destination.name}: {read / 1024**2:.1f} MiB', flush=True)
                            last_report = time.monotonic()
                if expected is not None and read != expected:
                    raise ValueError(f'Incomplete download of {destination.name}: {read} / {expected} bytes')
            partial.replace(destination)
            partial_source.unlink(missing_ok=True)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1)


def convert(source, destination, dimensions=256):
    import numpy as np
    with source.open('rb') as stream:
        header_size = struct.unpack('<Q', stream.read(8))[0]
        if not 0 < header_size < 8 * 1024**2:
            raise ValueError('Invalid safetensors header length')
        header = json.loads(stream.read(header_size))
    tensor = header['embedding.weight']
    rows, width = tensor['shape']
    if tensor['dtype'] != 'F32' or width < dimensions:
        raise ValueError('Unexpected static model tensor format')
    offset, end = tensor['data_offsets']
    if end - offset != rows * width * 4 or source.stat().st_size < 8 + header_size + end:
        raise ValueError('Incomplete static model download')
    original = np.memmap(source, dtype='<f4', mode='r', offset=8+header_size+offset, shape=(rows, width))
    target = np.lib.format.open_memmap(destination, mode='w+', dtype='<f2', shape=(rows, dimensions))
    for start in range(0, rows, 512):
        values = original[start:start+512, :dimensions]
        if not np.isfinite(values).all():
            raise ValueError('Nonfinite pretrained embedding table')
        target[start:start+512] = values
    target.flush()
    del target, original
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', type=Path, default=ROOT / 'models' / 'quick-semantic')
    parser.add_argument('--ref', default=DEFAULT_REVISION,
                        help='Pinned Hugging Face commit (default), or an explicit revision to install')
    args = parser.parse_args()
    # Re-running setup for the installed commit works offline and does not
    # replace files that the running application may have memory-mapped.
    if re.fullmatch('[0-9a-f]{40}', args.ref):
        manifest_path = args.dest / 'manifest.json'
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text('utf-8'))
                if manifest.get('revision') == args.ref:
                    from everytensor.quick_embeddings import QuickEmbedder
                    model = QuickEmbedder(args.dest)
                    model.encode(query='快速本地语义搜索')
                    print(f'Already installed and validated at {args.dest}', flush=True)
                    return
            except (OSError, ValueError, KeyError):
                print('Existing quick model is incomplete; rebuilding it.', flush=True)
    cache = ROOT / '.everytensor' / 'quick-model-download'
    cache.mkdir(parents=True, exist_ok=True)
    with request(f'https://huggingface.co/api/models/{MODEL_ID}/revision/{args.ref}') as response:
        metadata = json.load(response)
    revision = metadata['sha']
    print(f'Downloading {MODEL_ID} @ {revision}', flush=True)
    base = f'https://huggingface.co/{MODEL_ID}/resolve/{revision}/'
    for remote, local in [('0_StaticEmbedding/model.safetensors', 'model.safetensors'),
                          ('0_StaticEmbedding/tokenizer.json', 'tokenizer.json'), ('README.md', 'MODEL_CARD.md')]:
        destination = cache / local
        revision_file = cache / (local + '.revision')
        if not destination.exists() or not revision_file.exists() or revision_file.read_text() != revision:
            download(base + remote, destination)
            revision_file.write_text(revision)
    args.dest.mkdir(parents=True, exist_ok=True)
    temporary = args.dest / 'embeddings.preparing.npy'
    rows = convert(cache / 'model.safetensors', temporary)
    temporary.replace(args.dest / 'embeddings.npy')
    shutil.copyfile(cache / 'tokenizer.json', args.dest / 'tokenizer.json')
    shutil.copyfile(cache / 'MODEL_CARD.md', args.dest / 'MODEL_CARD.md')
    manifest = dict(format_version=FORMAT_VERSION, model_id=MODEL_ID, revision=revision,
                    dimensions=256, vocab_size=rows, pooling='mean', storage_dtype='float16',
                    source_weights_sha256=sha256_file(cache / 'model.safetensors'),
                    sha256={name: sha256_file(args.dest / name) for name in ('embeddings.npy', 'tokenizer.json')})
    manifest_path = args.dest / 'manifest.preparing.json'
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    manifest_path.replace(args.dest / 'manifest.json')
    from everytensor.quick_embeddings import QuickEmbedder
    model = QuickEmbedder(args.dest)
    model.encode(query='快速本地语义搜索')
    print(f'Installed and validated {rows:,} x 256 embeddings at {args.dest}', flush=True)


if __name__ == '__main__':
    main()
