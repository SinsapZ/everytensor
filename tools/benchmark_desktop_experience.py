"""Time real Qt desktop operations on disposable fixture files and an isolated catalog.

Example: .venv/Scripts/python tools/benchmark_desktop_experience.py --output report.json
Use --source to run the same harness against a preserved application snapshot.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time


def first_frame(source, database):
    """Run only in a fresh child interpreter, with no imports from the other snapshot."""
    sys.path.insert(0, str(source))
    from everytensor.desktop import create_desktop
    runtime = create_desktop(database, start_backend=False, restore_geometry=False, offer_setup=False)
    seen = []
    runtime.window.frameSwapped.connect(lambda: seen.append(time.perf_counter_ns()))
    runtime.window.requestUpdate()
    deadline = time.monotonic() + 15
    try:
        while not seen:
            if time.monotonic() >= deadline:
                raise RuntimeError("No Qt frame was swapped")
            runtime.app.processEvents()
            time.sleep(.001)
        print(seen[0], flush=True)
    finally:
        runtime.close()


def summarize(samples):
    return {key: dict(median=statistics.median(values),
                      p95=sorted(values)[math.ceil(len(values) * .95)-1],
                      n=len(values)) for key, values in samples.items()}


def compare(baseline, source, repeats, launches):
    order = ("before", "after", "after", "before")
    roots = dict(before=baseline, after=source)
    runs = {label: [] for label in roots}
    with tempfile.TemporaryDirectory(prefix="et-paired-bench-") as temporary:
        for index, label in enumerate(order):
            output = Path(temporary) / f"batch-{index}.json"
            subprocess.run([sys.executable, __file__, "--source", str(roots[label]),
                            "--output", str(output), "--repeats", str(repeats),
                            "--launches", str(launches)], check=True)
            runs[label].append(json.loads(output.read_text(encoding="utf-8")))
    first = runs["before"][0]
    versions = {}
    for label, batches in runs.items():
        assert batches[0]["application_sha256"] == batches[1]["application_sha256"], (
            "Source changed between paired batches", label)
        samples = {key: [value for batch in batches for value in batch["samples_ms"][key]]
                   for key in batches[0]["samples_ms"]}
        versions[label] = dict(summary_ms=summarize(samples),
                               samples_ms_by_batch=[batch["samples_ms"] for batch in batches],
                               application_sha256=batches[0]["application_sha256"])
    return dict(environment=first["environment"], fixture=first["fixture"],
                measurement=first["measurement"], batch_order=order, versions=versions)


def measure(source, repeats, launches, search_only=False):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QUICK_BACKEND"] = "software"
    sys.path.insert(0, str(source))
    import PySide6
    from PySide6.QtCore import QObject, Qt
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from everytensor import desktop, store

    app = QApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))

    samples = {}
    if launches:
        with tempfile.TemporaryDirectory(prefix="et-startup-bench-") as launch_root:
            elapsed = []
            for trial in range(launches + 1):
                cmd = [sys.executable, __file__, "--source", str(source),
                       "--frame-worker", str(Path(launch_root) / str(trial))]
                start_ns = time.perf_counter_ns()
                frame_ns = int(subprocess.check_output(cmd, text=True).strip())
                if trial:
                    elapsed.append((frame_ns - start_ns) / 1_000_000)
            samples["new_process_to_first_frame_ms"] = elapsed

    def record(name, start, trial):
        if trial > 0:
            samples.setdefault(name, []).append((time.perf_counter() - start) * 1000)

    def wait_for(check):
        deadline = time.monotonic() + 20
        while not check():
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting for the measured desktop operation")
            app.processEvents()
            time.sleep(.001)

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def publish(rows, operation, trial):
        start = time.perf_counter()
        client._publish(rows)
        record(operation, start, trial)
        assert [r["file_id"] for r in client.results.rows] == [r["file_id"] for r in rows]
        app.processEvents()

    with tempfile.TemporaryDirectory(prefix="et-desktop-bench-") as directory:
        root = Path(directory)
        files = root / "corpus"
        files.mkdir()
        runtime = desktop.create_desktop(root / "state", start_backend=False,
                                         restore_geometry=False, offer_setup=False)
        try:
            client, window = runtime.client, runtime.window
            window.setWidth(1280)
            window.setHeight(820)
            store.set_setting(client.db, "scope", "folders")
            store.set_setting(client.db, "roots", [str(files)])
            rows = []
            with client.db:
                for index in range(500):
                    family = "quartz" if index % 2 else "willow"
                    path = files / f"{family}-{index:04}.txt"
                    content = f"Field notes {index:04} — 世界模型\n" + "Observation and results.\n" * 80
                    path.write_text(content, encoding="utf-8")
                    stat = path.stat()
                    client.db.execute("""INSERT INTO files(id,path,root,name,kind,status,size,mtime)
                        VALUES(?,?,?,?,'document','ready',?,?)""",
                        (index + 1, str(path), str(files), path.name, stat.st_size, stat.st_mtime_ns))
                    rows.append(dict(file_id=index + 1, path=str(path), name=path.name, kind="document",
                                     size=stat.st_size, mtime=stat.st_mtime_ns, text=content,
                                     location={"line": 1}, sources=["内容"]))
            QTest.qWait(150)
            field = window.findChild(QObject, "searchField")
            listing = window.findChild(QObject, "resultsList")
            prefix = ""
            milestones = {}

            def matches_query():
                return (bool(prefix) and client.query == prefix and bool(client.results.rows)
                        and client.results.rows[0]["name"].startswith(prefix))

            def inserted(*_):
                if "inserted" not in milestones and matches_query():
                    milestones["inserted"] = time.perf_counter()

            def swapped():
                if "swapped" not in milestones and matches_query():
                    item = listing.property("currentItem")
                    if item and item.isVisible() and str(item.property("name")).startswith(prefix):
                        milestones["swapped"] = time.perf_counter()

            client.results.rowsInserted.connect(inserted)
            window.frameSwapped.connect(swapped)
            # A real paste goes through TextField.onTextEdited, workers, polling and QML.
            for trial in range(repeats + 1):
                prefix = ("quartz-00", "willow-00")[trial % 2]
                milestones.clear()
                field.forceActiveFocus()
                QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
                app.clipboard().setText(prefix)
                start = time.perf_counter()
                QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
                wait_for(matches_query)
                record("paste_to_first_matching_model_row_ms", start, trial)
                wait_for(lambda: "swapped" in milestones)
                if trial:
                    for key, metric in (("inserted", "paste_to_rows_inserted_ms"),
                                        ("swapped", "paste_to_frame_with_matching_delegate_ms")):
                        samples.setdefault(metric, []).append((milestones[key] - start) * 1000)
                wait_for(lambda: not client._pending.intersection(("names", "keywords")))
                assert {r["file_id"] for r in client.rows} == {
                    r["file_id"] for r in rows if r["name"].startswith(prefix)}
            window.frameSwapped.disconnect(swapped)
            client.results.rowsInserted.disconnect(inserted)

            if not search_only:
                client.setQuery("")
                client._query = "Field notes"
                client.stateChanged.emit()
                client._visible = len(rows)  # Same state reached after four "load more" operations.
                client._publish(rows)
                client.selectFile(rows[0]["file_id"], True)
                wait_for(lambda: not runtime.preview.loading and bool(runtime.preview.text))
                for trial in range(repeats + 1):
                    publish(rows, "republish_identical_500_sync_ms", trial)
                    publish(rows[100:] + rows[:100], "rotate_500_by_100_sync_ms", trial)
                    publish(rows, "restore_rotation_sync_ms", trial)
                    publish(list(reversed(rows)), "reverse_500_sync_ms", trial)
                    publish(rows, "restore_reversal_sync_ms", trial)
                    assert client.selectedId == rows[0]["file_id"]
                    listing.forceActiveFocus()
                    start = time.perf_counter()
                    for _ in range(12):
                        QTest.keyClick(window, Qt.Key.Key_Down)
                    record("dispatch_12_down_keys_ms", start, trial)
                    assert client.selectedId == rows[12]["file_id"]
                    wait_for(lambda: not runtime.preview.loading and "Field notes 0012" in runtime.preview.text)
                    record("dispatch_to_final_text_preview_12_keys_ms", start, trial)
                    client.selectFile(rows[0]["file_id"], True)
                    wait_for(lambda: not runtime.preview.loading and "Field notes 0000" in runtime.preview.text)
            assert not runtime.warnings, runtime.warnings
            dpr, font_family = window.devicePixelRatio(), runtime.desktop.fontFamily
        finally:
            runtime.close()
    hashes = {str(p.relative_to(source)): digest(p)
              for p in sorted((source / "everytensor").rglob("*"))
              if p.suffix in {".qml", ".py"}}
    return dict(environment=dict(platform=platform.platform(), python=platform.python_version(),
                                 processor=platform.processor(), qt=PySide6.__version__,
                                 renderer="Qt Quick offscreen software", window=[1280, 820],
                                 device_pixel_ratio=dpr, font_family=font_family,
                                 harness_sha256=digest(Path(__file__))),
                fixture="500 generated UTF-8 files, SQLite name index, model worker disabled",
                measurement="One discarded warmup per metric. Publish and key dispatch measure synchronous GUI work. "
                            "First matching model row is observed after processEvents returns, rowsInserted is timed directly. "
                            "Matching frame is frameSwapped with the matching current delegate instantiated; final preview is ready text. "
                            "Startup is subprocess creation to first Qt frameSwapped, fresh DB and warm OS cache. "
                            "None of these measures monitor FPS.",
                repeats=repeats, startup_repeats=launches, samples_ms=samples,
                summary_ms=summarize(samples), application_sha256=hashes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", type=Path, help="Run before/after/after/before paired batches")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--launches", type=int, default=5)
    parser.add_argument("--search-only", action="store_true", help="Only measure live paste-to-result milestones")
    parser.add_argument("--frame-worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.frame_worker:
        first_frame(args.source.resolve(), args.frame_worker)
        sys.exit(0)
    if args.repeats < 1 or args.launches < 0 or not args.output:
        parser.error("Supply --output, a positive --repeats and nonnegative --launches")
    if args.baseline:
        if args.search_only:
            parser.error("--search-only is a single-source diagnostic")
        result = compare(args.baseline.resolve(), args.source.resolve(), args.repeats, args.launches)
    else:
        result = measure(args.source.resolve(), args.repeats, args.launches, args.search_only)
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if "summary_ms" in result:
        print(json.dumps(result["summary_ms"], indent=2))
