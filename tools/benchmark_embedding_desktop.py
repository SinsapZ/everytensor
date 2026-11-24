"""Replay a frozen fixture through Qt, its real JSON-lines worker, and the desktop result model.

Use one fresh process and --work directory per variant. --database is opened read-only.
This measures row-model availability; it does not measure the subsequent painted frame.
"""
import argparse
from collections import defaultdict
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback

from benchmark_embedding_search import digest, distribution, load_fixture, write_json
from embedding_search_fixture import score


ROOT = Path(__file__).resolve().parents[1]


def page_worker_imports(process):
    """Ask Windows to page reclaimable memory only in this benchmark's own Popen child."""
    import ctypes
    from ctypes import wintypes
    import psutil
    from everytensor.inference import host_available
    if process.poll() is not None:
        raise RuntimeError("The private benchmark worker has already exited")
    launcher = psutil.Process(process.pid)
    args = list(map(str, process.args))
    if (args[1:4] != ["-u", "-m", "everytensor.engine"] or len(args) != 5
            or Path(launcher.exe()).resolve() != Path(args[0]).resolve() or launcher.cmdline()[1:] != args[1:]):
        raise ValueError("The launched process is not the expected private desktop worker")
    actual_exe = Path(sys._base_executable).resolve()
    if Path(launcher.exe()).resolve() == actual_exe:
        worker = launcher
    else:
        # CPython's venv launcher on Windows forwards to the base interpreter.
        workers = [p for p in launcher.children() if Path(p.exe()).resolve() == actual_exe
                   and p.cmdline()[1:] == args[1:] and p.ppid() == launcher.pid]
        if len(workers) != 1:
            raise RuntimeError("Could not identify the unique interpreter for this worker and index")
        worker = workers[0]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.K32EmptyWorkingSet.argtypes = (wintypes.HANDLE,)
    kernel.K32EmptyWorkingSet.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x0100 | 0x0400, False, worker.pid)  # SET_QUOTA | QUERY_INFORMATION
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not worker.is_running() or process.poll() is not None:
            raise RuntimeError("The private interpreter has exited")
        result = dict(launch_pid=process.pid, pid=worker.pid, argv=worker.cmdline(), exe=worker.exe(),
                      before_rss=worker.memory_info().rss, available_before=host_available())
        ok = kernel.K32EmptyWorkingSet(handle)
        result.update(ok=bool(ok), errno=0 if ok else ctypes.get_last_error(),
                      after_rss=worker.memory_info().rss, available_after=host_available())
        return result
    finally:
        kernel.CloseHandle(handle)


def copy_fixture_index(database, directory, folder, fixture, cached_only):
    expected = {(folder / f["name"]).resolve() for f in fixture["files"]}
    if directory.is_relative_to(folder) or directory.exists():
        raise ValueError("Supply a new work directory outside the corpus")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
        settings = {k: json.loads(v) for k, v in source.execute("select key,value from settings")}
        if settings.get("scope") != "folders" or [Path(s).resolve() for s in settings["roots"]] != [folder]:
            raise ValueError("Refusing to launch a worker with a different search scope")
        paths = {Path(r[0]).resolve() for r in source.execute("select path from files where status='ready'")}
        total = source.execute("select count(*) from files").fetchone()[0]
        if paths != expected or total != len(expected):
            raise ValueError("Refusing to use a database with a different library")
        covered = source.execute("""select count(distinct u.file_id) from units u
            join vectors v on v.digest=u.digest where v.model=? and v.dim>=?""",
            (settings["active_model"], settings["dimensions"])).fetchone()[0]
        if covered != len(expected):
            raise ValueError("Fixture documents do not all have a full vector")
        directory.mkdir(parents=True)
        with closing(sqlite3.connect(directory/"index.sqlite3")) as local:
            source.backup(local)
            original = local.execute("select count(*) from query_vectors").fetchone()[0]
            if cached_only:
                if original != len(fixture["queries"]):
                    raise ValueError("Cached-only probe requires a complete verified query cache")
            else:
                local.execute("delete from query_vectors")
                local.commit()
            return {"files": total, "source_cached_queries": original,
                    "copy_cached_queries": local.execute("select count(*) from query_vectors").fetchone()[0],
                    "model": settings["model"], "quick_model": settings["quick_model"]}


def wait_until(predicate, seconds, status):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(5)
    deadline = time.monotonic() + seconds
    def check():
        if predicate() or time.monotonic() >= deadline:
            loop.quit()
    if not predicate():
        timer.timeout.connect(check)
        timer.start()
        loop.exec()
        timer.stop()
    if not predicate():
        raise TimeoutError(status())


def measure(args, report):
    from PySide6.QtWidgets import QApplication
    from everytensor import client, paths
    if Path(client.__file__).resolve().parents[1] != args.source or paths.resource_root() != args.source:
        raise ValueError("Controller or child worker resolves to the wrong source")
    corpus = args.data/"corpus"
    manifest = args.data/"fixture.json"
    if not manifest.is_file():
        raise ValueError("The fixture must already be frozen")
    fixture = load_fixture(corpus, manifest)
    report["fixture_fingerprint"] = fixture["fingerprint"]
    report["copy"] = copy_fixture_index(args.database, args.work, corpus, fixture, args.cached_only)
    app = QApplication.instance() or QApplication([])
    start = time.perf_counter()
    controller = client.Controller(args.work, start_backend=True)
    backend = controller.commands
    inbox, sent = defaultdict(list), defaultdict(list)
    states = report["model_events"] = []
    enqueue, send = controller.events.put, backend.put
    def observe_event(event):
        stamp = time.perf_counter()
        if event.get("type") == "results":
            inbox[event["id"]].append((stamp, event))
        elif event.get("type") in ("model", "fatal"):
            states.append(dict(event, seconds=stamp-start))
        return enqueue(event)
    def observe_send(command):
        if command.get("action") == "search":
            sent[command["id"]].append(time.perf_counter())
        return send(command)
    controller.events.put = observe_event
    backend.put = observe_send
    current = {}
    def note_ready():
        if (current and "ready" not in current and controller.generation >= current["generation"]
                and controller.query == current["text"] and controller.semanticStage == "full"
                and not controller.pending and not controller.refreshing):
            current["ready"] = time.perf_counter()
            current["ready_generation"] = controller.generation
            current["files"] = [row["name"] for row in controller.results.rows]
    controller.stateChanged.connect(note_ready)
    report["worker"] = {"argv": list(map(str, backend.process.args)), "cwd": str(paths.resource_root()),
                        "pid": backend.process.pid}
    def checkpoint(stage):
        report["stage"] = stage
        write_json(args.output, report)
        print(stage, flush=True)
    def status():
        return f"{controller.modelState} {controller.modelStatus} {controller.searchError}; worker={backend.process.poll()}"
    try:
        checkpoint("worker_start")
        if not args.cached_only:
            def loaded():
                if (args.page_worker_imports and os.name == "nt" and "worker_paging" not in report
                        and time.perf_counter()-start >= 12 and "可用内存不足" in controller.modelStatus):
                    report["worker_paging"] = page_worker_imports(backend.process)
                    write_json(args.output, report)
                return controller.modelState in ("ready", "error", "missing")
            wait_until(loaded, args.start_timeout, status)
            log = (args.work/"backend.log").read_text("utf-8", errors="replace")
            report["cuda_log"] = [line for line in log.splitlines() if "Model loaded device=" in line]
            if controller.modelState != "ready" or not any("device=cuda" in line for line in report["cuda_log"]):
                raise RuntimeError("Expected the complete CUDA model: " + status())
            report["model_ready_ms"] = (time.perf_counter()-start)*1000
        passes = ("persisted",) if args.cached_only else ("uncached", "repeated")
        for mode in passes:
            pass_rows = report["passes"][mode] = []
            rankings = {}
            checkpoint(mode)
            for query in fixture["queries"]:
                qid = query["id"]
                generation = controller.generation + 1
                current.clear()
                current["generation"] = generation
                current["text"] = query["text"]
                begin = time.perf_counter()
                controller.setQuery(query["text"])
                def complete():
                    return "ready" in current or bool(controller.searchError) or backend.process.poll() is not None
                wait_until(complete, args.query_timeout, lambda: f"{qid} " + status())
                if "ready" not in current or controller.searchError:
                    raise RuntimeError(f"{qid} did not finish: {status()}")
                generations = range(generation, current["ready_generation"]+1)
                packets = sorted([(stamp,event) for g in generations for stamp,event in inbox.pop(g, [])
                                  if stamp <= current["ready"]], key=lambda pair: pair[0])
                full = [(stamp,event) for stamp,event in packets
                        if event.get("id") == current["ready_generation"]
                        and event.get("full_checked") and event.get("semantic_stage") == "full"]
                sends = sorted(stamp for g in generations for stamp in sent.pop(g, []) if stamp <= current["ready"])
                if not full or not sends or full[0][1].get("error"):
                    raise RuntimeError(f"{qid} has no matching complete full reply")
                received, response = full[0]
                files = current["files"]
                rankings[qid] = files
                # Production json.dumps parameters, excluding OS-dependent line endings.
                sizes = [len(json.dumps(event, ensure_ascii=False).encode("utf-8")) for _,event in packets]
                record = dict(id=qid, query=query["text"], kind=controller.kind,
                              set_query_to_full_rows_ms=(current["ready"]-begin)*1000,
                              send_to_full_received_ms=(received-sends[0])*1000,
                              initial_dispatch_ms=(sends[0]-begin)*1000, search_commands=len(sends),
                              response_packets=len(packets), total_result_json_bytes=sum(sizes),
                              full_result_json_bytes=len(json.dumps(response, ensure_ascii=False).encode("utf-8")),
                              full_checked=True, full_semantic_rows=len(response.get("semantic_rows", [])),
                              ranking=files)
                pass_rows.append(record)
                current.clear()
            report["scores"][mode] = score(fixture["queries"], rankings)
            report["summaries"][mode] = {}
            for key in ("set_query_to_full_rows_ms", "send_to_full_received_ms", "initial_dispatch_ms"):
                report["summaries"][mode][key] = distribution([r[key] for r in pass_rows])
            for key in ("response_packets", "total_result_json_bytes", "full_result_json_bytes"):
                report["summaries"][mode][key] = sum(r[key] for r in pass_rows)
            def cached_count():
                return controller.db.execute("select count(*) from query_vectors").fetchone()[0]
            wait_until(lambda: cached_count() == len(fixture["queries"]), 5,
                       lambda: f"Expected {len(fixture['queries'])} persisted queries, saw {cached_count()}")
            report["summaries"][mode]["persisted_queries"] = cached_count()
            checkpoint(mode + "_complete")
        report["state"] = "complete"
        checkpoint("complete")
    finally:
        controller.close()
        for pool in controller._pools.values():
            pool.shutdown(wait=True)
        backend.process.wait(timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "database", "data", "work", "output"):
        parser.add_argument("--"+name, type=lambda val: Path(val).resolve(), required=True)
    parser.add_argument("--cached-only", action="store_true",
                        help="Replay persisted vectors without waiting for CUDA readiness; reported separately")
    parser.add_argument("--start-timeout", type=int, default=240)
    parser.add_argument("--query-timeout", type=int, default=30)
    parser.add_argument("--page-worker-imports", action="store_true",
                        help="On a host RAM deferral, ask Windows once to trim only the child that this run launched")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_relative_to(args.data/"corpus"):
        parser.error("Supply a new report path outside the fixture")
    os.environ.update(QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                      EVERYTENSOR_RESOURCE_ROOT=str(args.source))
    sys.path.insert(0, str(args.source))
    report = {"state": "started", "source": str(args.source), "database_read_only": str(args.database),
              "working_copy": str(args.work), "harness_sha256": digest(Path(__file__)),
              "input_method": "One complete query string per setQuery call, in fixture order; no simulated keystrokes.",
              "source_hashes": {name: digest(args.source/"everytensor"/name)
                                for name in ("engine.py", "client.py", "store.py", "backend.py")},
              "cached_only": args.cached_only, "page_worker_imports": args.page_worker_imports,
              "passes": {}, "summaries": {}, "scores": {}}
    try:
        measure(args, report)
    except Exception:
        report.update(state="failed", error=traceback.format_exc())
        write_json(args.output, report)
        raise


if __name__ == "__main__":
    main()
