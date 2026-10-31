"""Interchangeable local inference implementations; no network fallback."""
import json
import os
from pathlib import Path
from typing import Protocol

NAMES = ('sentence-transformers', 'transformers', 'transformers-bf16')


class Backend(Protocol):
    def encode_query(self, values, **kwargs): ...
    def encode_document(self, values, **kwargs): ...
    def encode(self, values, **kwargs): ...


def selected():
    name = os.environ.get('EVERYTENSOR_BACKEND', NAMES[0])
    if name not in NAMES:
        raise ValueError('未知本地推理后端：'+name)
    return name


def identity():
    from importlib.metadata import version, PackageNotFoundError
    versions = []
    for package in ('torch', 'transformers', 'sentence-transformers'):
        try:
            versions.append(package+'='+version(package))
        except PackageNotFoundError:
            versions.append(package+'=missing')
    name = selected()
    revision = 'runtime-v1' if name == NAMES[0] else 'runtime-v2'
    return name+':'+revision+':'+','.join(versions)


def load(path, device, dtype):
    if selected() == NAMES[0]:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(str(path), local_files_only=True, trust_remote_code=False,
                                   device=device, model_kwargs={'dtype': dtype})
    return TransformersBackend(path, device, dtype)


class TransformersBackend:
    """Direct Transformers forward + masked mean pooling, matching saved modules.

    Separate representation identity is deliberate until the parity experiment
    establishes equivalence for each modality and precision.
    """
    def __init__(self, path, device, dtype):
        from transformers import AutoModel, AutoProcessor
        self.device = device
        self.processor = AutoProcessor.from_pretrained(str(path), local_files_only=True, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(str(path), local_files_only=True, trust_remote_code=False,
                                             dtype=dtype).to(device).eval()
        config = json.loads((Path(path)/'config_sentence_transformers.json').read_text('utf-8'))
        self.prompts = config['prompts']

    def encode_query(self, values, **kwargs):
        return self._text(values, self.prompts['query'], **kwargs)

    def encode_document(self, values, **kwargs):
        return self._text(values, self.prompts['document'], **kwargs)

    def _text(self, values, prefix, **kwargs):
        items = [values] if isinstance(values, str) else values
        result = self.encode([prefix+value for value in items], **kwargs)
        return result[0] if isinstance(values, str) else result

    def encode(self, values, **kwargs):
        import numpy as np
        import torch
        single = isinstance(values, (dict, str))
        items = [values] if single else list(values)
        if not items:
            return np.empty((0, 768), dtype='<f4')
        batch_size = int(kwargs.get('batch_size', len(items)))
        output = []
        for start in range(0, len(items), max(1, batch_size)):
            batch = items[start:start+max(1, batch_size)]
            args = {'return_tensors': 'pt', 'padding': True, 'truncation': True}
            if isinstance(batch[0], str):
                args['text'] = batch
            elif 'audio' in batch[0]:
                args.update(audio=[b['audio']['array'] for b in batch], sampling_rate=16000)
            elif 'video' in batch[0]:
                args.update(videos=[b['video'] for b in batch],
                            videos_kwargs={'max_frames': 8, 'fps': None})
            else:
                args.update(images=[b['image'] for b in batch], images_kwargs={'max_soft_tokens': 280})
            features = self.processor(**args).to(self.device)
            with torch.inference_mode():
                hidden = self.model(**features).last_hidden_state
                # The saved ST Pooling module accumulates in the model dtype.
                # Changing this to float32 would confound backend and pooling
                # comparisons for the bf16 profile.
                mask = features['attention_mask'].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden*mask).sum(dim=1)/mask.sum(dim=1).clamp(min=1e-9)
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            output.append(pooled.float().cpu().numpy())
        result = np.concatenate(output).astype('<f4', copy=False)
        return result[0] if single else result
