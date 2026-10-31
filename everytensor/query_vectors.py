"""Small local query-vector cache, independent of model residency and file results."""
import hashlib
import json
from pathlib import Path
import time

from . import embeddings, storage


def model_signature(path):
    path = Path(path).resolve()
    signature = [(str(p.relative_to(path)), p.stat().st_size, p.stat().st_mtime_ns)
                 for p in sorted(path.rglob('*')) if p.is_file() and '.cache' not in p.relative_to(path).parts]
    from .runtime_backends import identity
    return hashlib.sha256(json.dumps(signature).encode()).hexdigest()+':v2:768:video8:'+identity()


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS query_vectors(
        model TEXT NOT NULL,topic TEXT NOT NULL,dim INTEGER NOT NULL,data BLOB NOT NULL,
        used REAL NOT NULL,PRIMARY KEY(model,topic))''')
    db.commit()


def topic_key(topic):
    return hashlib.sha256(topic.encode('utf-8')).hexdigest()


def get(db, model, topic, dim):
    import numpy as np
    row = db.execute('SELECT dim,data FROM query_vectors WHERE model=? AND topic=?',
                     (model, topic_key(topic))).fetchone()
    if not row or row[0] < dim or len(row[1]) != row[0]*4:
        return None
    try:
        vector = embeddings.truncate(np.frombuffer(row[1], dtype='<f4'), dim)
    except ValueError:
        return None
    # Cache hits must not block semantic search behind another writer.
    try:
        with storage.transaction(db, background=True):
            db.execute('UPDATE query_vectors SET used=? WHERE model=? AND topic=?',
                       (time.time(), model, topic_key(topic)))
    except Exception:
        pass
    return vector


def put(db, model, topic, vector):
    import numpy as np
    array = np.asarray(vector, dtype='<f4').reshape(-1)
    if not 1 <= len(array) <= 768:
        raise ValueError('Invalid query vector dimension')
    array = embeddings.truncate(array, len(array))
    with storage.write(db, 16384):
        db.execute('''INSERT INTO query_vectors VALUES(?,?,?,?,?) ON CONFLICT(model,topic)
            DO UPDATE SET dim=CASE WHEN excluded.dim>=dim THEN excluded.dim ELSE dim END,
                          data=CASE WHEN excluded.dim>=dim THEN excluded.data ELSE data END,
                          used=excluded.used''',
                   (model, topic_key(topic), len(array), array.tobytes(), time.time()))
        db.execute('''DELETE FROM query_vectors WHERE rowid IN
            (SELECT rowid FROM query_vectors ORDER BY used DESC,rowid DESC LIMIT -1 OFFSET 256)''')
