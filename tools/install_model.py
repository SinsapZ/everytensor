"""Explicit, resumable model download. The app itself never accesses the network."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', default=str(Path(__file__).resolve().parents[1]/'models'/'embeddinggemma-2'))
    args = parser.parse_args()
    try:
        from huggingface_hub import snapshot_download
        path = snapshot_download('google/embeddinggemma-2', local_dir=args.destination,
                                 ignore_patterns=['*.md', '*.png', '*.jpg', '*.msgpack', '*.h5', '*.ot'])
        print('模型已下载：'+str(path))
        print('请在 EveryTensor 设置中选择此目录。')
    except Exception as exc:
        print('下载未完成，可在网络可用后运行相同命令继续。\n'+str(exc))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
