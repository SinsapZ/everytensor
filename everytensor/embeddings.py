"""EmbeddingGemma 2's documented Matryoshka truncation, shared by all modalities."""
from __future__ import annotations

DIMENSIONS = (256, 512, 768)


def dimension(value):
    value = int(value)
    if value not in DIMENSIONS:
        raise ValueError('向量维度须为 256、512 或 768')
    return value


def truncate(values, dim):
    import numpy as np
    array = np.asarray(values, dtype='<f4')[..., :dim].copy()
    if array.shape[-1] != dim:
        raise ValueError('源向量维度不足，需重新编码')
    norms = np.linalg.norm(array, axis=-1, keepdims=True)
    if not np.isfinite(array).all() or (norms <= 0).any():
        raise ValueError('模型返回无效向量')
    return array/norms


def shrink_batch(db, model, dim, limit=128):
    """Downsize in-place, without inference or a second permanent vector index."""
    import numpy as np
    from . import storage
    rows = db.execute('SELECT digest,data FROM vectors WHERE model=? AND dim>? LIMIT ?', (model, dim, limit)).fetchall()
    if not rows:
        return 0
    with storage.write(db, max(65536, len(rows)*dim*8)):
        db.executemany('UPDATE vectors SET dim=?,data=? WHERE digest=? AND model=?',
                      [(dim, truncate(np.frombuffer(r['data'], '<f4'), dim).tobytes(), r['digest'], model) for r in rows])
    return len(rows)
