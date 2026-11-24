"""Offline, file-level retrieval audit with the application's actual search components.

Run once per source revision in a fresh process and database. The shared generated
corpus is immutable. The default runtime and its memory guards remain active.
"""
import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time
import traceback

from embedding_search_fixture import create_fixture, score


ROOT = Path(__file__).resolve().parents[1]
DIMENSION = 256
LIMIT = 501


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture(folder, manifest):
    if not manifest.exists():
        write_json(manifest, create_fixture(folder))
    data = json.loads(manifest.read_text("utf-8"))
    frozen = {k: v for k, v in data.items() if k != "fingerprint"}
    canonical = json.dumps(frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != data["fingerprint"]:
        raise ValueError("Fixture manifest changed")
    if {p.name: digest(p) for p in folder.iterdir()} != {f["name"]: f["sha256"] for f in data["files"]}:
        raise ValueError("Fixture bytes changed")
    return data


def timed(times, name, call, *args, **kwargs):
    started = time.perf_counter()
    value = call(*args, **kwargs)
    times[name] = (time.perf_counter() - started) * 1000
    return value


def distribution(values):
    ordered = sorted(values)
    return dict(n=len(values), median_ms=statistics.median(values),
                p95_ms=ordered[math.ceil(len(ordered)*.95)-1], total_ms=sum(values))


def page_own_imports(available):
    """Let Windows page this benchmark process after a genuine load deferral."""
    import ctypes as c
    from ctypes import wintypes
    class Counters(c.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, c.c_size_t) for name in ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged",
                                            "nonpaged", "pagefile", "peak_pagefile")]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    psapi = c.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, c.POINTER(Counters), wintypes.DWORD)
    psapi.EmptyWorkingSet.argtypes = (wintypes.HANDLE,)
    handle = kernel.GetCurrentProcess()
    def sample():
        mem = Counters()
        mem.cb = c.sizeof(mem)
        if not psapi.GetProcessMemoryInfo(handle, c.byref(mem), mem.cb):
            raise c.WinError(c.get_last_error())
        return dict(working_set_bytes=mem.ws, available_host_bytes=available())
    before = sample()
    if not psapi.EmptyWorkingSet(handle):
        raise c.WinError(c.get_last_error())
    return dict(before=before, after=sample(), pid=os.getpid())


def file_names(rows):
    return list(dict.fromkeys(Path(row["path"]).name for row in rows))


def build_quick(db, quick, module):
    while True:
        discovered = module.pump(db, quick.key)
        accepted = 0
        for mode, limit in (("name", 256), ("text", 64)):
            rows = module.pending(db, quick.key, mode, (), limit)
            if rows:
                texts = [module.hint(row) if mode == "name" else row["text"] for row in rows]
                count = module.persist(db, quick.key, mode, rows, quick.encode_texts(texts))
                if count != len(rows):
                    raise ValueError("Quick index rejected a stationary fixture file")
                accepted += count
        if accepted == 0 and discovered == 0:
            return


def measure(args, report):
    from everytensor import engine, indexer, inference, media, query_vectors, quick_index, runtime_backends, store
    from everytensor.quick_embeddings import QuickEmbedder

    def stage(label):
        report["stage"] = label
        write_json(args.output, report)
        print(label, flush=True)

    stage("fixture")
    corpus = args.data / "corpus"
    fixture = load_fixture(corpus, args.data / "fixture.json")
    report["fixture"] = dict(fingerprint=fixture["fingerprint"], directory=str(corpus),
                             query_count=len(fixture["queries"]), file_count=len(fixture["files"]))
    times = report["times_ms"] = {}
    model_dir = ROOT / "models" / "embeddinggemma-2"
    db = store.connect(args.database)
    model = None
    try:
        for key, value in (("scope", "folders"), ("roots", [str(corpus)]),
                           ("model", str(model_dir)), ("dimensions", DIMENSION)):
            store.set_setting(db, key, value)
        query_vectors.install(db)
        quick_index.install(db)
        quick_dir = quick_index.resolve_model(db, ROOT / "models" / "quick-semantic")
        if not quick_dir:
            raise ValueError("Local Quick model is required")
        report["runtime"] = dict(backend=runtime_backends.selected(), host_available_before_load=inference.host_available(),
                                 weight_bytes=sum(p.stat().st_size for p in model_dir.glob("*.safetensors")))
        stage("full_load")
        started = time.perf_counter()
        retries = report["host_memory_deferrals"] = []
        host_available = inference.host_available
        checks = report["host_memory_checks"] = []
        def observed_available():
            value = host_available()
            checks.append(dict(elapsed_s=time.perf_counter()-started, available_bytes=value))
            return value
        inference.host_available = observed_available
        while model is None:
            try:
                model = engine.Embedder(model_dir)
            except inference.HostMemoryLow:
                retries.append(dict(elapsed_s=time.perf_counter()-started,
                                    available_bytes=host_available()))
                write_json(args.output, report)
                if retries[-1]["elapsed_s"] >= args.memory_wait:
                    raise
            if model is None:
                gc.collect()
                if (args.page_own_imports and "own_process_paging" not in report
                        and retries[-1]["elapsed_s"] >= 10 and "torch" in sys.modules and os.name == "nt"):
                    report["own_process_paging"] = page_own_imports(host_available)
                    write_json(args.output, report)
                    continue
                time.sleep(5)
        inference.host_available = host_available
        import numpy as np
        import torch
        if model.device != "cuda":
            raise ValueError(f"Expected default CUDA runtime on this host, received {model.device}")
        torch.cuda.synchronize()
        times["full_load"] = (time.perf_counter() - started) * 1000
        model.dimensions = DIMENSION
        report["runtime"].update(device=model.device, name=torch.cuda.get_device_name(), torch=torch.__version__,
                                 model_key=model.key, dimensions=model.dimensions)
        store.set_setting(db, "active_model", model.key)
        stage("scan_and_extract")
        timed(times, "scan", indexer.scan, db, str(corpus))
        timed(times, "extraction", indexer.index_pending, db)
        statuses = dict(db.execute("SELECT status,count(*) FROM files GROUP BY status"))
        if statuses != {"ready": len(fixture["files"])}:
            raise ValueError(f"Index coverage {statuses}: " + str(list(map(tuple, db.execute(
                "SELECT name,status,error FROM files WHERE status <> 'ready'")))))
        report["index"] = dict(files_by_status=statuses, units=db.execute("SELECT count(*) FROM units").fetchone()[0])
        stage("quick_index")
        quick = timed(times, "quick_load", QuickEmbedder, quick_dir)
        timed(times, "quick_index", build_quick, db, quick, quick_index)
        report["index"]["quick_vectors"] = dict(db.execute("SELECT kind,count(*) FROM quick_vectors GROUP BY kind"))
        report["runtime"]["quick_key"] = quick.key
        stage("full_document_index")
        batches = report["document_batches"] = []

        def instrument(method, prepared):
            def run(rows):
                row = rows[0][0] if prepared else rows[0]
                entry = dict(phase=method.__name__, kind=row["kind"], count=len(rows))
                value = timed(entry, "ms", method, rows)
                batches.append(entry)
                return value
            return run

        model.prepare_batch = instrument(model.prepare_batch, False)
        model.encode_prepared = instrument(model.encode_prepared, True)
        document_started = time.perf_counter()
        encoded_ms, persisted_ms = [], []
        while pending := engine.pending_units(db, model.key, DIMENSION, limit=128):
            items, seen = [], set()
            for raw in pending:
                row = dict(raw)
                if row["digest"] in seen:
                    continue
                seen.add(row["digest"])
                stat = Path(row["path"]).stat()
                row["_stat"] = stat.st_size, stat.st_mtime_ns
                items.append(row)
            before = time.perf_counter()
            values = []
            for row, vector, error in engine.encoded_units(model, items, lambda: None):
                if error is not None or vector is None:
                    raise ValueError(f"Encoding {Path(row['path']).name}: {error}")
                if vector.shape != (DIMENSION,) or not np.isfinite(vector).all():
                    raise ValueError("Invalid document vector")
                values.append((row, vector))
            encoded_ms.append((time.perf_counter()-before)*1000)
            before = time.perf_counter()
            if not values or engine.persist_vectors(db, model.key, values) != len(items):
                raise ValueError("Incomplete full index")
            persisted_ms.append((time.perf_counter()-before)*1000)
        times["document_index"] = (time.perf_counter()-document_started)*1000
        times["document_encode_with_preparation"] = sum(encoded_ms)
        times["document_vector_persistence"] = sum(persisted_ms)
        report["index"]["full_vectors"] = db.execute("SELECT count(*) FROM vectors").fetchone()[0]
        if not report["index"]["full_vectors"]:
            raise ValueError("Empty full index")

        quick_search = quick_index.QuickSearch()
        full_cache = store.VectorCache()
        ranks = {mode: {} for mode in ("lexical", "quick", "full", "fused")}
        records = report["query_timings_ms"] = []
        stage("heldout_queries")
        for case in fixture["queries"]:
            qid, text = case["id"], case["text"]
            kind = case["kind"] or store.query_intent(text)[0]
            if not kind and Path(text.strip()).suffix:
                guessed = media.kind(text.strip())
                kind = guessed if guessed != "other" else ""
            record = dict(id=qid, kind=kind)
            qry = text.strip()
            names = timed(record, "names", store.names, db, text, kind, LIMIT)
            keywords = timed(record, "lexical_fts", store.keywords, db, text, kind, LIMIT)
            quick_vector = timed(record, "quick_encode", quick.encode, query=qry)
            quick_rows = timed(record, "quick_retrieve", quick_search.search, db, quick_vector, quick.key, kind, LIMIT)
            full_vector = timed(record, "full_encode", model.encode, query=qry)
            if full_vector.shape != (DIMENSION,) or not np.isfinite(full_vector).all():
                raise ValueError(f"Invalid query vector for {qid}")
            timed(record, "full_vector_put", query_vectors.put, db, model.key, qry, full_vector)
            full_rows = timed(record, "full_retrieve", store.semantic, db, full_vector, model.key, kind, LIMIT,
                              cache=full_cache)
            semantic_rows = timed(record, "combine", engine.combine_semantics, quick_rows, full_rows, LIMIT)
            fused = timed(record, "fusion", store.fuse, names, keywords, semantic_rows, query=text)
            for mode, rows in (("lexical", store.fuse(names, keywords, query=text)), ("quick", quick_rows),
                               ("full", full_rows), ("fused", fused)):
                ranks[mode][qid] = file_names(rows)
            records.append(record)
        report["scores"] = {mode: score(fixture["queries"], ranking) for mode, ranking in ranks.items()}
        report["rankings"] = ranks
        report["query_summary"] = {label: distribution([row[label] for row in records])
                                   for label in records[0] if label not in ("id", "kind")}
        report["index"]["persisted_queries"] = db.execute("SELECT count(*) FROM query_vectors").fetchone()[0]
        cached = report["cached_retrieval_ms"] = []
        stage("persisted_vector_retrieval")
        for repetition in range(args.repeats):
            for record, case in zip(records, fixture["queries"]):
                item = dict(id=case["id"], repetition=repetition+1)
                vector = timed(item, "full_vector_get", query_vectors.get, db, model.key, case["text"].strip(), DIMENSION)
                if vector is None:
                    raise ValueError(f"Missing cached vector {case['id']}")
                full_rows = timed(item, "full_retrieve", store.semantic, db, vector, model.key, record["kind"],
                                  LIMIT, cache=full_cache)
                if file_names(full_rows) != ranks["full"][case["id"]]:
                    raise ValueError(f"Persisted query ranking differs for {case['id']}")
                cached.append(item)
        report["cached_summary"] = {label: distribution([row[label] for row in cached])
                                    for label in ("full_vector_get", "full_retrieve")}
        report["state"] = "complete"
        stage("complete")
    finally:
        if model is not None:
            model.close()
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=ROOT / "build" / "embedding-search-eval-20261124")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--memory-wait", type=float, default=180,
                        help="Seconds of bounded retries after the application's normal host-memory deferral")
    parser.add_argument("--page-own-imports", action="store_true",
                        help="On Windows, once after a genuine deferral, page only this benchmark process")
    args = parser.parse_args()
    for field in ("source", "data", "database", "output"):
        setattr(args, field, getattr(args, field).resolve())
    if args.database.exists() or args.output.exists() or args.repeats < 1:
        parser.error("Choose new database/output paths and one or more repetitions")
    os.environ["EVERYTENSOR_RESOURCE_ROOT"] = str(ROOT)
    sys.path.insert(0, str(args.source))
    report = dict(state="started", source=str(args.source), database=str(args.database), python=platform.python_version(),
                  harness_sha256=digest(Path(__file__)), fixture_generator_sha256=digest(Path(__file__).with_name(
                      "embedding_search_fixture.py")), source_sha256={
                      p.name: digest(p) for p in sorted((args.source/"everytensor").glob("*.py"))},
                  method="Serial direct production component calls. File-level ranking. Nearest-rank P95. "
                         "Document preparation overlaps inference. GPU encoders return host arrays. "
                         "Model load and initial corpus indexing each have one observation per run.")
    try:
        measure(args, report)
    except BaseException:
        report.update(state="failed", error=traceback.format_exc())
        raise
    finally:
        write_json(args.output, report)


if __name__ == "__main__":
    main()
