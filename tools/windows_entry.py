"""Shared frozen GUI/console entry point; dispatch workers before loading Qt."""
import multiprocessing
import sys


def main():
    multiprocessing.freeze_support()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    if sys.argv[1:3] == ['--worker', 'engine']:
        from everytensor.engine import main as worker
        sys.argv = [sys.argv[0]] + sys.argv[3:]
        return worker()
    if sys.argv[1:3] == ['--worker', 'pdf']:
        from everytensor.pdf_render import worker
        return worker()
    from everytensor.__main__ import main as application
    return application()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        from everytensor.paths import default_data
        data = default_data()
        data.mkdir(parents=True, exist_ok=True)
        report = data / 'startup-error.log'
        report.write_text(traceback.format_exc(), encoding='utf-8')
        if sys.stderr is not None:
            traceback.print_exc()
        elif '--self-test' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '启动失败，详细信息已保存到：\n' + str(report),
                                           'EveryTensor', 0x10)
        raise SystemExit(1)
