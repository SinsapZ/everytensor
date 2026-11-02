from __future__ import annotations
import argparse
import json
import multiprocessing
from pathlib import Path
from .paths import default_data


def main():
    parser = argparse.ArgumentParser(description='EveryTensor 本地文件搜索')
    parser.add_argument('--data', default=str(default_data()))
    parser.add_argument('--self-test', metavar='REPORT', help='在隔离目录验证安装包并写入 JSON 报告')
    parser.add_argument('--index', metavar='FOLDER', help='增量索引文件夹后退出，不加载模型')
    parser.add_argument('--query', help='命令行文件名及关键词检索')
    parser.add_argument('--doctor', action='store_true')
    parser.add_argument('--computer', action='store_true', help='将搜索范围设为这台电脑并启动桌面')
    parser.add_argument('--dimensions', type=int, choices=(256, 512, 768), help='设置向量维度')
    parser.add_argument('--budget-gib', type=float, help='索引空间预算，不含模型')
    parser.add_argument('--legacy-ui', action='store_true', help='打开旧版 Tk 界面')
    args = parser.parse_args()
    from .paths import frozen
    if frozen() and args.legacy_ui:
        parser.error('Windows 安装版使用 Qt 界面；旧版界面仅在源码版提供')
    if args.self_test:
        from .self_test import run
        return run(Path(args.self_test))
    data = Path(args.data).resolve()
    def desktop():
        if args.legacy_ui:
            from .ui import App
            App(data).mainloop()
        else:
            from .desktop import run
            run(data)
    # The desktop owns its connection. Avoid running schema/migration work twice
    # before its first frame (the model worker has its own connection as well).
    if not (args.doctor or args.index or args.query is not None or args.computer
            or args.dimensions or args.budget_gib is not None):
        desktop()
        return
    from . import store
    db = store.connect(data/'index.sqlite3')
    if args.computer:
        store.set_setting(db, 'scope', 'computer')
    if args.dimensions:
        store.set_setting(db, 'dimensions', args.dimensions)
    if args.budget_gib is not None:
        if not .125 <= args.budget_gib <= 1024:
            parser.error('--budget-gib 应在 0.125–1024 之间')
        store.set_setting(db, 'budget_gib', args.budget_gib)
    if args.doctor:
        import importlib.metadata as md
        import shutil
        packages = {}
        for name in ('Pillow', 'PyMuPDF', 'numpy', 'torch', 'transformers', 'sentence-transformers'):
            try:
                packages[name] = md.version(name)
            except md.PackageNotFoundError:
                packages[name] = 'missing'
        print(json.dumps({'packages': packages, 'ffmpeg': shutil.which('ffmpeg'), 'ffplay': shutil.which('ffplay'),
                          'model': store.setting(db, 'model', ''), 'index': store.stats(db)}, ensure_ascii=False, indent=2))
    elif args.index:
        from . import indexer
        root = str(Path(args.index).resolve())
        roots = store.setting(db, 'roots', [])
        if root not in roots:
            store.set_setting(db, 'roots', roots+[root])
        print(json.dumps(indexer.scan(db, root), ensure_ascii=False))
        count = indexer.index_pending(db, progress=print)
        print(json.dumps({'updated': count, **store.stats(db)}, ensure_ascii=False, indent=2))
    elif args.query is not None:
        print(json.dumps(store.fuse(store.names(db, args.query), store.keywords(db, args.query), query=args.query), ensure_ascii=False, indent=2))
    else:
        db.close()
        desktop()
        return
    db.close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
