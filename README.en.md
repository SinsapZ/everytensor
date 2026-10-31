# EveryTensor

By **[sinsap](https://github.com/SinsapZ)**

[简体中文](README.md) | **English** | [日本語](README.ja.md) | [Español](README.es.md)

Windows preview · v0.1.0-alpha.1

Every file is a vector.

You do not need to remember file names. Vectors will help you find them.

## Search functions

- Search file names, paths, and text. You can use Chinese keywords.
- Search text by meaning with the small text model.
- Search images, video, and audio by content with the full model.
- Preview search results. A result can include a page number, line number, sheet name, or time in a recording.
- Update the index in the background. You can pause or resume indexing in settings.

File types include TXT, Markdown, source code, PDF, DOCX, PPTX, XLSX, EPUB, images, video, and audio.

The index stores file information for search. Files become searchable by name as the app finds them. To search content, the app must first read that content.

## Requirements

- 64-bit Windows.
- Python 3.11. During Python installation, select “Add python.exe to PATH”.
- An internet connection to install packages and models.

For audio and video, install [FFmpeg](https://ffmpeg.org/download.html). Make sure that the `ffmpeg`, `ffprobe`, and `ffplay` commands work in a terminal.

## Install and start

This preview runs from source.

1. Download the [source ZIP](https://github.com/SinsapZ/everytensor/releases/download/v0.1.0-alpha.1/everytensor-0.1.0-alpha.1-source.zip).
2. Extract the ZIP to a folder where you have write permission.
3. Run `setup-quick-model.cmd`.
4. Wait for setup to finish.
5. Run `launch.cmd`.
6. Press `Ctrl+,` to open settings.
7. Select the folders to search. Start with a small folder for your first use.

The setup script creates `.venv`, installs packages, and downloads the small text model.

**At first start, the app searches all accessible fixed local drives.** You can narrow the search scope or exclude folders in settings. The default index budget is 8 GiB. Model files use additional space.

### Other setup options

| Script | Purpose |
| --- | --- |
| `setup-desktop.cmd` | Install the app without downloading a model. Use file name search, keyword search, and previews. |
| `setup-model.cmd` | Install the full model and its packages. Also prepare the small text model. |

Close EveryTensor before you run `setup-model.cmd`. After setup, start the app again with `launch.cmd`.

The small text model is [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1). The full model is [Google EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2). The full model needs more disk space and memory.

If the full model cannot run, you can still use existing results from names, keywords, and the small text model.

### Start from a terminal

Open a terminal in the project folder. Run these commands in order:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 tools\install_quick_model.py
.\.venv\Scripts\python.exe -m everytensor
```

If the app window closes at startup, run the last command in a terminal to see the error.

## Keys and local data

| Key | Action |
| --- | --- |
| `Ctrl+K`, `Ctrl+L`, or `Ctrl+F` | Move to the search box |
| `↑`, `↓` | Select a result |
| `Enter` | Open the result |
| `Esc` | Clear the search |
| `Ctrl+,` | Open settings |

The app reads files, builds the index, and runs models on your computer. Package and model downloads do not upload your files. The app does not change the original files.

These folders are in the project folder. The source ZIP does not include them.

| Folder | Contents |
| --- | --- |
| `.everytensor/` | Index and settings, including file paths and extracted text |
| `models/` | Downloaded models |
| `.venv/` | Python environment and packages |

The index can contain personal data. Do not upload it to a public location. Before you share logs, remove private paths and content.

## License

Copyright (C) 2026 EveryTensor contributors.

The source code uses **GNU Affero General Public License v3.0 only** (`AGPL-3.0-only`). See [LICENSE](LICENSE) for the terms and warranty disclaimer.

Third-party packages and models have their own licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
