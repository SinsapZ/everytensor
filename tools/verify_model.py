"""Verify downloaded model files and optionally configure the application."""
from __future__ import annotations
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(path):
    config = json.loads((path/'config.json').read_text('utf-8'))
    if config.get('model_type') != 'embedding_gemma2':
        raise ValueError('Not an EmbeddingGemma 2 model')
    for name in ['tokenizer.json', 'tokenizer_config.json', 'processor_config.json',
                 'preprocessor_config.json', 'config_sentence_transformers.json',
                 'modules.json', '1_Pooling/config.json', '2_Normalize/config.json']:
        # Normalize modules need not save a config file.
        if name == '2_Normalize/config.json' and not (path/name).exists():
            continue
        if not (path/name).is_file():
            raise ValueError('Missing model file: '+name)
        json.loads((path/name).read_text('utf-8'))
    weights = path/'model.safetensors'
    hasher = hashlib.sha256()
    with weights.open('rb') as file:
        prefix = file.read(8)
        if len(prefix) != 8:
            raise ValueError('Truncated safetensors header')
        length = struct.unpack('<Q', prefix)[0]
        if not 0 < length < 100_000_000:
            raise ValueError('Invalid safetensors header size')
        header_bytes = file.read(length)
        header = json.loads(header_bytes)
        hasher.update(prefix)
        hasher.update(header_bytes)
        for block in iter(lambda: file.read(4*1024*1024), b''):
            hasher.update(block)
    tensors = [value for key, value in header.items() if key != '__metadata__']
    data_size = weights.stat().st_size-8-length
    cursor = 0
    for tensor in sorted(tensors, key=lambda t: t['data_offsets']):
        start, end = tensor['data_offsets']
        if start != cursor or not start <= end <= data_size:
            raise ValueError('Truncated or invalid tensor payload')
        cursor = end
    if cursor != data_size:
        raise ValueError('Unexpected bytes after tensor payload')
    checksum = hasher.hexdigest()
    metadata = path/'.cache/huggingface/download/model.safetensors.metadata'
    expected = None
    if metadata.exists():
        lines = metadata.read_text('utf-8').splitlines()
        if len(lines) > 1 and len(lines[1]) == 64:
            expected = lines[1]
            if checksum != expected:
                raise ValueError('Weight checksum differs from Hugging Face download metadata')
    return {'model_path': str(path), 'weight_bytes': weights.stat().st_size,
            'tensor_count': len(tensors), 'sha256': checksum,
            'matches_download_metadata': checksum == expected if expected else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--path', type=Path, default=ROOT/'models/embeddinggemma-2')
    parser.add_argument('--configure', action='store_true')
    args = parser.parse_args()
    path = args.path.resolve()
    report = {}
    result = 0
    try:
        print('Checking model files and SHA-256...', flush=True)
        report.update(verify(path))
        from everytensor import store
        from everytensor.engine import runtime_issues
        report['runtime_issues'] = runtime_issues()
        if report['runtime_issues']:
            raise RuntimeError('; '.join(report['runtime_issues']))
        if args.configure:
            db = store.connect(ROOT/'.everytensor/index.sqlite3')
            store.set_setting(db, 'model', str(path))
            db.close()
            report['configured'] = True
    except Exception as exc:
        report['error'] = str(exc)
        result = 1
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(result)


if __name__ == '__main__':
    main()
