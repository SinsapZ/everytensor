# Third-party notices

EveryTensor's own source is licensed under AGPL-3.0-only. Dependencies and model weights remain under their respective upstream licenses. This source distribution does not bundle Python packages, FFmpeg binaries, or model weights.

## Windows binary distribution (0.1.0-alpha.3)

The Windows installer includes CPython 3.11, PySide6 Essentials / Qt, Shiboken6, Pillow, PyMuPDF / MuPDF, NumPy, tokenizers and the static text model described below. Exact package versions and model hashes are recorded in `build-manifest.json`. License and copyright files from the build environment are copied to `licenses/`; the build environment may contain tools and transitive packages that are not loaded by the application. Python's license is included there as well. PyInstaller's bootloader is used under its GPL license with the redistribution exception; its license is included with the collected package notices.

`corresponding-source.zip` contains this application's exact corresponding source and Windows build scripts. The same source ZIP is provided alongside the installer. Qt/PySide shared libraries remain separate in `_internal/` and can be replaced with ABI-compatible versions; the application does not enforce signatures on them. Upstream sources are available from [CPython](https://github.com/python/cpython), [Qt for Python](https://code.qt.io/cgit/pyside/pyside-setup.git/), [Qt](https://code.qt.io/), [Pillow](https://github.com/python-pillow/Pillow), [PyMuPDF](https://github.com/pymupdf/PyMuPDF), [MuPDF](https://git.ghostscript.com/?p=mupdf.git), [NumPy](https://github.com/numpy/numpy), [tokenizers](https://github.com/huggingface/tokenizers), and [PyInstaller](https://github.com/pyinstaller/pyinstaller). Use the versions in the build manifest when obtaining upstream source.

The bundled static model is a modified 256-dimensional FP16 derivative of revision `b68f4122911bcffcd6e1f695f2d99cd6788972d8`. Its model card and checksum manifest are included with the model; its Apache 2.0 license is in `licenses/quick-model-Apache-2.0.txt`. Full multimodal model weights, PyTorch, Transformers and FFmpeg are downloaded only when the user starts optional model setup. `everytensor/model-assets.json` pins each upstream URL, version, length and SHA-256 hash. Wheels retain their `.dist-info` license files; original archives, CPython license, and FFmpeg documentation remain in the user component directory. The optional setup uses CPython 3.11.9, CPU PyTorch 2.11.0 and torchvision 0.26.0, Transformers 5.19.0, Sentence Transformers 6.1.0, and Gyan FFmpeg 8.1.2 essentials. Upstream FFmpeg binaries come directly from [Gyan builds](https://github.com/GyanD/codexffmpeg/releases/tag/8.1.2); consult their archive for build configuration and source references.

The installer uses [Inno Setup 6.7.3](https://github.com/jrsoftware/issrc/tree/is-6_7_3). The Simplified Chinese installer messages come from that release's `Files/Languages/Unofficial/ChineseSimplified.isl`; attribution is preserved in that file in the corresponding source archive.

## PDF and desktop dependencies

- **PyMuPDF / MuPDF — Artifex Software.** Available under GNU AGPL v3 or a separate commercial license. EveryTensor uses the open-source dependency. See the [upstream licensing statement](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).
- **Qt for Python / PySide6 — The Qt Company and contributors.** The installed PySide6-Essentials package declares LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only. Qt components and third-party code can have additional notices; consult the [Qt for Python licensing documentation](https://doc.qt.io/qtforpython-6/licenses.html) and the exact package being distributed.
- **FFmpeg.** Installed by optional model setup on Windows, or separately for source use. Licensing depends on the build and enabled components; see [FFmpeg legal information](https://ffmpeg.org/legal.html).

## Models downloaded separately

- [Google EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2), Google DeepMind: Apache-2.0 according to its upstream model card. The full-model installer downloads the upstream snapshot.
- [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1), Sentence Transformers: Apache-2.0 according to its upstream model card. The quick-model installer uses revision `b68f4122911bcffcd6e1f695f2d99cd6788972d8`, truncates the pretrained table to 256 dimensions, and stores it in FP16. The installation manifest records source identity and checksums, and the upstream model card is copied into the installed model directory.

## Other runtime packages

The requirements files declare Pillow, NumPy, tokenizers, and optional PyTorch, Transformers and Sentence Transformers, in addition to the packages above. They and their transitive dependencies retain their upstream copyright and license notices. The notices shipped with the exact installed versions are authoritative; this list does not relicense those packages or replace their license texts.

If redistributing a bundled application, dependencies or model weights, include the notices and source/materials required by the licenses of the versions actually bundled.
