"""Small, local static text embeddings for the first semantic search tier.

The published Sentence Transformers model is trained for multilingual sentence
similarity. This implements its mean-pooling operation without importing torch.
Its space is separate from EmbeddingGemma; vectors must never be mixed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODEL_ID = 'sentence-transformers/static-similarity-mrl-multilingual-v1'
FORMAT_VERSION = 1


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def bundled_path():
    return Path(__file__).resolve().parents[1] / 'models' / 'quick-semantic'


def available(path=None):
    path = Path(path or bundled_path())
    return all((path / name).is_file() for name in ('manifest.json', 'embeddings.npy', 'tokenizer.json'))


def model_key(path=None):
    """Read the declared identity without loading the tokenizer or NumPy."""
    manifest = json.loads((Path(path or bundled_path()) / 'manifest.json').read_text('utf-8'))
    if manifest.get('format_version') != FORMAT_VERSION or manifest.get('model_id') != MODEL_ID:
        raise ValueError('不支持的快速语义模型')
    identity = {key: manifest[key] for key in ('model_id', 'revision', 'dimensions', 'sha256')}
    return 'quick-static-v1:' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class QuickEmbedder:
    """A read-only 256-dimensional, CPU-only learned embedding table.

    The mmap is OS-pageable and token vectors are accumulated in bounded slices.
    There is no model context truncation: every token in each supplied text is
    represented. The application should retain its normal document chunks for
    precise locators and bounded tokenizer input.
    """
    def __init__(self, path=None):
        import numpy as np
        from tokenizers import Tokenizer
        self.path = Path(path or bundled_path()).resolve()
        manifest = json.loads((self.path / 'manifest.json').read_text('utf-8'))
        if manifest.get('format_version') != FORMAT_VERSION or manifest.get('model_id') != MODEL_ID:
            raise ValueError('不支持的快速语义模型；请运行 setup-quick-model.cmd')
        for filename in ('embeddings.npy', 'tokenizer.json'):
            if sha256_file(self.path / filename) != manifest['sha256'][filename]:
                raise ValueError('快速语义模型文件校验失败：' + filename)
        self.weights = np.load(self.path / 'embeddings.npy', mmap_mode='r', allow_pickle=False)
        self.dimensions = int(manifest['dimensions'])
        if (self.weights.dtype != np.dtype('<f2') or self.weights.ndim != 2
                or self.weights.shape != (int(manifest['vocab_size']), self.dimensions)):
            raise ValueError('快速语义模型的向量表与清单不一致')
        self.tokenizer = Tokenizer.from_file(str(self.path / 'tokenizer.json'))
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        if self.tokenizer.get_vocab_size() > len(self.weights):
            raise ValueError('快速语义模型的词表大小不一致')
        self.key = model_key(self.path)
        self.device = 'cpu'

    def encode_texts(self, texts):
        import numpy as np
        if not texts:
            return np.empty((0, self.dimensions), dtype='<f4')
        encode = getattr(self.tokenizer, 'encode_batch_fast', self.tokenizer.encode_batch)
        tokens = encode(texts, add_special_tokens=False)
        output = np.zeros((len(tokens), self.dimensions), dtype='<f4')
        for row, tokenized in enumerate(tokens):
            ids = tokenized.ids
            for start in range(0, len(ids), 256):
                output[row] += self.weights[ids[start:start+256]].sum(axis=0, dtype=np.float32)
        norms = np.linalg.norm(output, axis=1, keepdims=True)
        np.divide(output, norms, out=output, where=norms > 0)
        if not np.isfinite(output).all():
            raise ValueError('快速语义模型返回无效向量')
        return output

    def encode(self, row=None, query=None):
        text = query if query is not None else row['text']
        return self.encode_texts([text])[0]
