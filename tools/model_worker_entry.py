"""Entry point for the optional isolated interpreter (no GUI dependencies)."""
from pathlib import Path
import json
import os
import sys


def main():
    resources = Path(sys.argv.pop(1)).resolve()
    source = Path(__file__).with_name('application.zip')
    sys.path.insert(0, str(source if source.is_file() else resources))
    os.environ['EVERYTENSOR_RESOURCE_ROOT'] = str(resources)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding='utf-8')
    if sys.argv[1] == '--validate':
        import numpy as np
        from PIL import Image
        from everytensor.engine import Embedder
        from everytensor import media
        for tool in ('ffmpeg', 'ffprobe', 'ffplay'):
            media.run([media.executable(tool), '-version'], timeout=20)
        model = Embedder(sys.argv[2], device='cpu')
        query = model.encode(query='a red square')
        image = Image.new('RGB', (56, 56), 'red')
        for value in ({'image': image}, {'audio': {'array': np.zeros(16000, dtype=np.float32), 'sampling_rate': 16000}},
                      {'video': [image, image]}):
            vector = model.model.encode(value, convert_to_numpy=True, normalize_embeddings=True,
                                        show_progress_bar=False,
                                        processing_kwargs={'video': {'max_frames': 8, 'fps': None}, 'image': {'max_soft_tokens': 280}})
            assert np.asarray(vector).size == 768 and np.isfinite(vector).all()
        assert query.size == 768 and np.isfinite(query).all()
        model.close()
        print(json.dumps({'ok': True, 'modalities': ['text', 'image', 'audio', 'video']}), flush=True)
    else:
        from everytensor.engine import main as worker
        worker()


if __name__ == '__main__':
    main()
