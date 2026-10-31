# Third-party notices

EveryTensor's own source is licensed under AGPL-3.0-only. Dependencies and model weights remain under their respective upstream licenses. This source distribution does not bundle Python packages, FFmpeg binaries, or model weights.

## PDF and desktop dependencies

- **PyMuPDF / MuPDF — Artifex Software.** Available under GNU AGPL v3 or a separate commercial license. EveryTensor uses the open-source dependency. See the [upstream licensing statement](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).
- **Qt for Python / PySide6 — The Qt Company and contributors.** The installed PySide6-Essentials package declares LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only. Qt components and third-party code can have additional notices; consult the [Qt for Python licensing documentation](https://doc.qt.io/qtforpython-6/licenses.html) and the exact package being distributed.
- **FFmpeg.** Installed separately by the user. Licensing depends on the build and enabled components; see [FFmpeg legal information](https://ffmpeg.org/legal.html).

## Models downloaded separately

- [Google EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2), Google DeepMind: Apache-2.0 according to its upstream model card. The full-model installer downloads the upstream snapshot.
- [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1), Sentence Transformers: Apache-2.0 according to its upstream model card. The quick-model installer uses revision `b68f4122911bcffcd6e1f695f2d99cd6788972d8`, truncates the pretrained table to 256 dimensions, and stores it in FP16. The installation manifest records source identity and checksums, and the upstream model card is copied into the installed model directory.

## Other runtime packages

The requirements files declare Pillow, NumPy, tokenizers, and optional PyTorch, Transformers and Sentence Transformers, in addition to the packages above. They and their transitive dependencies retain their upstream copyright and license notices. The notices shipped with the exact installed versions are authoritative; this list does not relicense those packages or replace their license texts.

If redistributing a bundled application, dependencies or model weights, include the notices and source/materials required by the licenses of the versions actually bundled.
