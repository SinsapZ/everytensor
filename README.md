# EveryTensor

作者：**[sinsap](https://github.com/SinsapZ)**

**简体中文** | [English](README.en.md) | [日本語](README.ja.md) | [Español](README.es.md)

Windows 预览版 · main 开发版

所有文件，都是向量。

你不必记住文件名，向量会帮你找到它们。

## 搜索功能

- 搜索文件名、路径和正文，支持中文关键词。
- 用轻量文字模型按含义搜索文字内容。
- 用完整模型按内容搜索图片、视频和音频。
- 预览搜索结果。结果可包含页码、行号、工作表或音视频中的时间位置。
- 在后台更新索引。可以在设置中暂停或恢复索引。

支持的文件类型包括 TXT、Markdown、代码、PDF、DOCX、PPTX、XLSX、EPUB、图片、视频和音频。

索引记录文件信息，供搜索使用。应用发现文件后，就可以按名称搜索。按内容搜索前，应用需要先读取相应内容。

## 运行要求

- 64 位 Windows。
- Python 3.11。安装 Python 时勾选 “Add python.exe to PATH”。
- 安装依赖和模型时需要网络连接。

使用音视频功能前，请安装 [FFmpeg](https://ffmpeg.org/download.html)。确认 `ffmpeg`、`ffprobe` 和 `ffplay` 命令均可在终端中运行。

## 安装与启动

此预览版从源码运行。以下步骤使用包含最新改动的 `main` 分支。[已发布的 v0.1.0-alpha.1](https://github.com/SinsapZ/everytensor/releases/tag/v0.1.0-alpha.1) 保留原有内容，不包含之后的更新。

1. 下载[最新源码 ZIP](https://github.com/SinsapZ/everytensor/archive/refs/heads/main.zip)。
2. 解压到你有写入权限的文件夹。
3. 双击 `setup-quick-model.cmd`。
4. 等待安装完成。
5. 双击 `launch.cmd`。
6. 按 `Ctrl+,` 打开设置。
7. 选择要搜索的文件夹。初次使用可先选择一个较小的资料目录。

安装脚本会创建 `.venv`、安装依赖，并下载轻量文字模型。

**首次启动默认搜索所有可访问的固定本地磁盘。** 可以在设置中缩小搜索范围或排除文件夹。默认索引预算为 8 GiB，模型文件另占空间。

### 其他安装方式

| 脚本 | 用途 |
| --- | --- |
| `setup-desktop.cmd` | 安装应用，不下载模型。可使用文件名搜索、关键词搜索和预览。 |
| `setup-model.cmd` | 安装完整模型及其依赖，同时准备轻量文字模型。 |

运行 `setup-model.cmd` 前，请先关闭 EveryTensor。安装完成后，用 `launch.cmd` 重新启动。

轻量文字模型为 [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1)。完整模型为 [Google EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2)，需要更多磁盘空间和内存。

完整模型无法运行时，仍可使用已有的文件名、关键词和轻量文字模型结果。

### 从终端启动

在项目文件夹中打开终端，依次运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 tools\install_quick_model.py
.\.venv\Scripts\python.exe -m everytensor
```

如果双击启动后窗口立即关闭，在终端运行最后一条命令可查看错误。

## 按键与本地数据

| 按键 | 操作 |
| --- | --- |
| `Ctrl+K`、`Ctrl+L` 或 `Ctrl+F` | 移到搜索框 |
| `↑`、`↓` | 选择结果 |
| 首条结果上按 `↑` | 返回搜索框 |
| 类型标签上按 `←`、`→` | 切换文件类型 |
| `Enter` | 打开结果 |
| 结果列表中按 `Shift+Enter` | 在文件夹中显示 |
| 结果列表中按 `Ctrl+C` | 复制文件路径及匹配位置 |
| 右键结果或按 `Shift+F10` | 打开结果操作菜单 |
| `Esc` | 清空搜索 |
| `Ctrl+,` | 打开设置 |

应用在本机读取文件、建立索引和运行模型。下载依赖和模型不会上传你的文件。应用不会改写原文件。

以下文件夹位于项目目录中，不包含在源码 ZIP 内：

| 文件夹 | 内容 |
| --- | --- |
| `.everytensor/` | 索引和设置，包括文件路径及提取的文字 |
| `models/` | 下载的模型 |
| `.venv/` | Python 运行环境和依赖 |

索引可能包含个人资料，请勿公开上传。分享日志前，请删除其中的私人路径和内容。

## 许可证

Copyright (C) 2026 EveryTensor contributors.

源码采用 **GNU Affero General Public License v3.0 only**（`AGPL-3.0-only`）。完整条款及免责声明见 [LICENSE](LICENSE)。

第三方依赖和模型使用各自的许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
