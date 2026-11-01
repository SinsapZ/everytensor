# EveryTensor

作者：**[sinsap](https://github.com/SinsapZ)**

[简体中文](README.md) | [English](README.en.md) | **日本語** | [Español](README.es.md)

Windows プレビュー版 · main 開発版

すべてのファイルは、ベクトルです。

ファイル名を覚える必要はありません。ベクトルがファイルを探すのを手伝います。

## 検索機能

- ファイル名、パス、本文を検索できます。中国語のキーワードにも対応しています。
- 軽量テキストモデルで、文章の意味から検索できます。
- 完全版モデルで、画像、動画、音声の内容を検索できます。
- 検索結果をプレビューできます。結果には、ページ番号、行番号、シート名、音声や動画の時刻が含まれる場合があります。
- バックグラウンドでインデックスを更新します。設定から一時停止や再開ができます。

対象のファイル形式は TXT、Markdown、ソースコード、PDF、DOCX、PPTX、XLSX、EPUB、画像、動画、音声です。

インデックスは、検索に使うファイル情報を保存します。アプリが見つけたファイルは、名前で検索できるようになります。内容を検索するには、先にアプリがその内容を読み取る必要があります。

## 動作環境

- 64 ビット版 Windows。
- Python 3.11。Python のインストール時に「Add python.exe to PATH」を選択してください。
- パッケージとモデルのインストールには、インターネット接続が必要です。

音声や動画を使う場合は、[FFmpeg](https://ffmpeg.org/download.html) をインストールしてください。ターミナルで `ffmpeg`、`ffprobe`、`ffplay` コマンドを実行できることを確認してください。

## インストールと起動

このプレビュー版はソースコードから実行します。以下の手順では、最新の変更を含む `main` ブランチを使用します。公開済みの [v0.1.0-alpha.1](https://github.com/SinsapZ/everytensor/releases/tag/v0.1.0-alpha.1) は公開時の内容を維持しており、その後の更新は含まれません。

1. [最新のソース ZIP](https://github.com/SinsapZ/everytensor/archive/refs/heads/main.zip) をダウンロードしてください。
2. 書き込み権限のあるフォルダーに展開してください。
3. `setup-quick-model.cmd` を実行してください。
4. インストールが終わるまで待ってください。
5. `launch.cmd` を実行してください。
6. `Ctrl+,` で設定を開いてください。
7. 検索するフォルダーを選択してください。初回は小さなフォルダーから始めてください。

インストールスクリプトは `.venv` を作成し、パッケージをインストールします。軽量テキストモデルもダウンロードします。

**初回起動時は、アクセス可能なすべてのローカル固定ドライブが検索対象です。** 設定で検索範囲を絞ったり、フォルダーを除外したりできます。インデックスの容量上限は初期設定で 8 GiB です。モデルファイルには別の容量が必要です。

### その他のインストール方法

| スクリプト | 用途 |
| --- | --- |
| `setup-desktop.cmd` | モデルをダウンロードせずにアプリをインストールします。ファイル名検索、キーワード検索、プレビューを使えます。 |
| `setup-model.cmd` | 完全版モデルと必要なパッケージをインストールします。軽量テキストモデルも準備します。 |

`setup-model.cmd` を実行する前に、EveryTensor を終了してください。インストール後、`launch.cmd` でアプリを起動してください。

軽量テキストモデルは [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1) です。完全版モデルは [Google EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2) です。完全版モデルは、より多くのディスク容量とメモリを必要とします。

完全版モデルが動作しない場合も、ファイル名、キーワード、軽量テキストモデルによる既存の検索結果を使えます。

### ターミナルから起動

プロジェクトフォルダーでターミナルを開いてください。次のコマンドを順に実行してください。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 tools\install_quick_model.py
.\.venv\Scripts\python.exe -m everytensor
```

起動時にウィンドウがすぐ閉じる場合は、最後のコマンドをターミナルで実行するとエラーを確認できます。

## キー操作とローカルデータ

| キー | 操作 |
| --- | --- |
| `Ctrl+K`、`Ctrl+L`、`Ctrl+F` | 検索欄に移動 |
| `↑`、`↓` | 結果を選択 |
| 先頭の結果で `↑` | 検索欄に戻る |
| 種類タブで `←`、`→` | ファイルの種類を切り替え |
| `Enter` | 結果を開く |
| 結果一覧で `Shift+Enter` | フォルダーに表示 |
| 結果一覧で `Ctrl+C` | ファイルパスと一致した位置をコピー |
| 結果を右クリック、または `Shift+F10` | 結果の操作メニューを開く |
| `Esc` | 検索を消去 |
| `Ctrl+,` | 設定を開く |

ファイルの読み取り、インデックスの作成、モデルの実行は、このコンピューター上で行います。パッケージやモデルのダウンロード時に、あなたのファイルをアップロードすることはありません。アプリは元のファイルを変更しません。

次のフォルダーはプロジェクトフォルダー内にあります。ソース ZIP には含まれません。

| フォルダー | 内容 |
| --- | --- |
| `.everytensor/` | インデックスと設定。ファイルパスや抽出したテキストを含みます。 |
| `models/` | ダウンロードしたモデル |
| `.venv/` | Python の実行環境とパッケージ |

インデックスには個人情報が含まれる場合があります。公開の場所にはアップロードしないでください。ログを共有する前に、私的なパスや内容を削除してください。

## ライセンス

Copyright (C) 2026 EveryTensor contributors.

ソースコードのライセンスは **GNU Affero General Public License v3.0 only**（`AGPL-3.0-only`）です。条項と免責事項は [LICENSE](LICENSE) を参照してください。

第三者のパッケージとモデルには、それぞれのライセンスが適用されます。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。
