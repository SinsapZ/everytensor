# EveryTensor

**Windows 早期预览版 · v0.1.0-alpha.1**

用文件名或一句描述，查找本机的文档、代码、图片、视频和音频，并在预览中定位相关内容。

当前提供源码运行方式，适合愿意反馈问题的早期使用者。界面、检索质量和资源占用仍在迭代，暂未提供独立安装包，也未完成全新 Windows 环境的完整验收。

## 可以做什么

- 按文件名、路径和正文关键词搜索；支持中文。
- 使用轻量多语言模型搜索文件提示和文字内容，完整模型提供多模态语义检索。
- 预览 PDF、文字和媒体，保留页码、行号、工作表或媒体时间位置。
- 支持 TXT、Markdown、常见代码、PDF、DOCX、PPTX、XLSX、EPUB，以及图片、视频和音频。
- 在后台增量更新索引，支持暂停、恢复、搜索范围和排除目录。

首次索引需要时间。已经发现的文件可先按名称搜索；尚未分析的内容不保证能通过语义搜索找到。

## 运行要求

- 64 位 Windows；当前开发环境为 Windows + Python 3.11。
- Python 3.11，安装时启用 “Add python.exe to PATH”。
- 安装依赖和模型时需要网络连接。
- 如需视频／音频功能，另行安装 [FFmpeg](https://ffmpeg.org/download.html)，确认 `ffmpeg`、`ffprobe` 和 `ffplay` 均可从终端运行。
- 完整模型需要额外的下载空间、内存和推理时间；CUDA 为可选加速方式，不包含在源码包中。

## 快速开始

将源码解压到可写目录，不要直接在 ZIP 内运行。

1. 双击 `setup-quick-model.cmd`。脚本会创建项目内的 `.venv`，安装桌面依赖并下载轻量文字模型。
2. 安装完成后双击 `launch.cmd`。
3. 打开设置，确认搜索范围、排除目录和索引预算，然后等待文件发现与内容分析。

**首次启动默认搜索“这台电脑”下可访问的固定本地磁盘。** 如需缩小范围，请在设置里改为指定文件夹。默认索引预算为 8 GiB，模型文件另计。初次使用建议选择一个较小的资料目录。

只想先使用文件名／关键词检索与预览，可运行 `setup-desktop.cmd`，暂不下载语义模型。

### 完整多模态模型（可选）

关闭应用后运行 `setup-model.cmd`。脚本会创建环境、安装运行依赖、准备轻量模型、下载完整模型并校验配置，完成后重新打开 `launch.cmd`。

完整模型为 [Google EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2)。轻量模型来自 [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1)，安装时转换为本地 256 维 FP16 表示。二者的结果与覆盖量分开管理；完整模型有有效结果时接管排名。

模型未准备好或可用内存不足时，已有的名称／关键词及轻量结果仍可使用。安装失败时保留终端报错，检查网络和 Python 环境后重试。下载依赖和模型不会上传待索引的资料。

### 终端启动

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 tools\install_quick_model.py
.\.venv\Scripts\python.exe -m everytensor
```

若双击后窗口立即关闭，在终端运行最后一条命令查看错误。

## 使用与数据

- `Ctrl K`／`Ctrl L`／`Ctrl F` 聚焦搜索；上下键选择，`Enter` 打开，`Esc` 清空；`Ctrl ,` 打开设置。
- 搜索、内容提取和模型推理在本机执行，运行时模型加载使用离线模式。
- 索引保存在项目内的 `.everytensor/`，含文件路径、提取文本、向量和配置；**索引也是个人资料，不要上传到公共仓库或附在公开反馈中。**
- `models/` 保存下载的模型，`.venv/` 保存 Python 依赖。三者均不包含在源码发布包中。
- 应用读取原始资料进行索引与预览，不改写原文件。外部打开后的操作由对应程序负责。
- 日志可能包含路径和错误信息；反馈前请检查并隐去私人内容。

## 许可证

Copyright (C) 2026 EveryTensor contributors.

EveryTensor 项目源码采用 **GNU Affero General Public License v3.0 only**（SPDX：`AGPL-3.0-only`），完整条款见 [LICENSE](LICENSE)。本软件不提供任何担保，具体以许可证为准。

第三方依赖和模型保留各自的许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
