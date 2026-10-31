from __future__ import annotations

import json
import os
import queue
import logging
import concurrent.futures
from collections import OrderedDict
from itertools import groupby, islice
import time
from pathlib import Path
from . import indexer, media, store, embeddings, storage, privacy
from . import inference, frontier, query_vectors, quick_index

log = logging.getLogger(__name__)


class IndexChanged(Exception):
    """The model or requested dimension changed at an indexing checkpoint."""


def runtime_issues():
    """Fail before importing torch when the installed runtime is incompatible."""
    from importlib.metadata import PackageNotFoundError, version
    import re
    issues = []
    for package, minimum in [('transformers', (5, 18, 0)), ('sentence-transformers', (6, 1, 0))]:
        required = '.'.join(map(str, minimum))
        try:
            installed = version(package)
            parts = re.match(r'^(\d+)\.(\d+)\.(\d+)', installed)
            if not parts or tuple(map(int, parts.groups())) < minimum:
                issues.append(f'{package} {installed} → 需要 >= {required}')
        except PackageNotFoundError:
            issues.append(f'{package} 未安装 → 需要 >= {required}')
    return issues


class Embedder:
    text_batch_size = 4
    image_batch_size = 1
    def __init__(self, path, device=None):
        privacy.configure_offline()
        self.dimensions = 768
        self.path = Path(path).resolve()
        if not (self.path / 'config.json').is_file():
            raise ValueError('请选择完整的本地 EmbeddingGemma 2 模型目录。')
        config = json.loads((self.path/'config.json').read_text('utf-8'))
        if config.get('model_type') != 'embedding_gemma2':
            raise ValueError('该目录不是 EmbeddingGemma 2 模型。')
        issues = runtime_issues()
        if issues:
            raise RuntimeError('；'.join(issues)+'。请运行 setup-model.cmd 安装模型运行依赖。')
        from . import runtime_backends
        half_precision = runtime_backends.selected() == 'transformers-bf16'
        if half_precision:
            inference.ensure_host_memory(self.path, device or 'cuda', weight_multiplier=1)
        else:
            inference.ensure_host_memory(self.path, device or 'cuda')
        import transformers
        if not hasattr(transformers, 'EmbeddingGemma2Model'):
            raise RuntimeError('当前 Transformers 不支持 EmbeddingGemma 2；请安装 requirements-model.txt 中的兼容运行时。')
        import torch
        torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2)//2)))
        self.device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
        if half_precision:
            inference.ensure_host_memory(self.path, self.device, weight_multiplier=1)
        else:
            inference.ensure_host_memory(self.path, self.device)
        dtype = torch.bfloat16 if self.device == 'cuda' and torch.cuda.is_bf16_supported() else torch.float32
        if half_precision:
            dtype = torch.bfloat16
        self.model = runtime_backends.load(self.path, self.device, dtype)
        log.info('Model loaded device=%s dtype=%s', self.device, dtype)
        self.key = query_vectors.model_signature(self.path)

    def close(self):
        self.model = None
        import gc
        gc.collect()
        inference.release_cuda()

    def batch_limit(self, mode):
        maximum = self.text_batch_size if mode == 'text' else self.image_batch_size if mode == 'image' else 1
        if self.device != 'cuda':
            return min(maximum, 2)
        import torch
        try:
            free, _ = torch.cuda.mem_get_info()
            reusable = max(0, torch.cuda.memory_reserved()-torch.cuda.memory_allocated())
            # Conservative admission estimate, followed by measured OOM backoff.
            allowance = max(0, free+reusable-512*1024**2)
            per_item = (384 if mode == 'text' else 768 if mode == 'image' else 1536)*1024**2
            return max(1, min(maximum, allowance//per_item))
        except Exception:
            return 1

    def prepare_batch(self, rows):
        started = time.monotonic()
        prepared = []
        for row in rows:
            try:
                if row['text']:
                    value = row['text']
                else:
                    loc = json.loads(row['location'])
                    value = ({'audio': media.audio_samples(row['path'], loc['start'], loc['end'])}
                             if loc.get('modality') == 'audio' else
                             {'video': media.video_frames(row['path'], loc['start'], loc['end'])}
                             if 'start' in loc else {'image': media.preview_image(row['path'], loc, (1008, 1008))})
                prepared.append((row, value, None))
            except Exception as exc:
                prepared.append((row, None, inference.failure(exc)))
        log.info('Prepare batch=%d seconds=%.3f', len(rows), time.monotonic()-started)
        return prepared

    def encode_prepared(self, prepared):
        import numpy as np
        started = time.perf_counter()
        if prepared[0][0]['text']:
            values = self.model.encode_document([p[1] for p in prepared], batch_size=len(prepared),
                convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
        else:
            values = self.model.encode([p[1] for p in prepared], batch_size=len(prepared),
                convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False,
                processing_kwargs={'video': {'max_frames': 8, 'fps': None}, 'image': {'max_soft_tokens': 280}})
        values = np.asarray(values, dtype='<f4').reshape(len(prepared), -1)
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        if not np.isfinite(values).all() or (norms == 0).any():
            raise ValueError('模型返回无效向量')
        elapsed = (time.perf_counter()-started)/len(prepared)
        for row, _, _ in prepared:
            if isinstance(row, dict):
                row['_encode_seconds'] = elapsed
        return embeddings.truncate(values, self.dimensions)

    def encode(self, row=None, query=None):
        import numpy as np
        if query is not None:
            value = self.model.encode_query(query, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
        else:
            loc = json.loads(row['location'])
            if row['text']:
                value = self.model.encode_document(row['text'], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
            else:
                if loc.get('modality') == 'audio':
                    inputs = {'audio': media.audio_samples(row['path'], loc['start'], loc['end'])}
                elif 'start' in loc:
                    inputs = {'video': media.video_frames(row['path'], loc['start'], loc['end'])}
                else:
                    inputs = {'image': media.preview_image(row['path'], loc, (1008, 1008))}
                value = self.model.encode(inputs, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False,
                    processing_kwargs={'video': {'max_frames': 8, 'fps': None}, 'image': {'max_soft_tokens': 280}})
        value = np.asarray(value, dtype='<f4').reshape(-1)
        if not np.isfinite(value).all() or np.linalg.norm(value) == 0:
            raise ValueError('模型返回无效向量')
        return embeddings.truncate(value, self.dimensions)

    def encode_text_batch(self, rows):
        import numpy as np
        values = self.model.encode_document([r['text'] for r in rows], batch_size=4,
            convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
        values = np.asarray(values, dtype='<f4')
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        if not np.isfinite(values).all() or (norms == 0).any():
            raise ValueError('模型返回无效向量')
        return embeddings.truncate(values, self.dimensions)


def encoded_units(model, rows, checkpoint):
    if hasattr(model, 'prepare_batch'):
        yield from pipelined_units(model, rows, checkpoint)
        return
    # Compatibility path for models without a separate preparation stage.
    for text, group in groupby(rows, key=lambda r: bool(r['text'])):
        while batch := list(islice(group, 4 if text else 1)):
            def encode(items):
                if len(items) > 1 and hasattr(model, 'encode_text_batch'):
                    return model.encode_text_batch(items)
                return [model.encode(row) for row in items]
            yield from inference.resilient_batch(model, batch, encode, checkpoint, 'text' if text else 'image')


def pipelined_units(model, rows, checkpoint):
    """One CPU producer overlaps decoding with the single GPU consumer.

    Only the main thread advances rows/SQLite. All PDF rendering stays on one
    producer thread; no concurrent calls into PyMuPDF. At most two batches live.
    """
    def modality(row):
        loc = json.loads(row['location'])
        return ('text' if row['text'] else 'audio' if loc.get('modality') == 'audio'
                else 'video' if 'start' in loc else 'image')

    def batches():
        source = iter(rows)
        # A bounded lookahead fills batches even when file types alternate.
        # Keep per-modality order and return to the fair file queue every 32 units.
        while window := list(islice(source, 32)):
            groups = {}
            for row in window:
                groups.setdefault(modality(row), []).append(row)
            for mode, group in groups.items():
                start = 0
                while start < len(group):
                    count = model.text_batch_size if mode == 'text' else model.image_batch_size if mode == 'image' else 1
                    if hasattr(model, 'batch_limit'):
                        count = model.batch_limit(mode)
                    elif getattr(model, 'device', '') == 'cpu':
                        count = min(count, 2)
                    yield group[start:start+count]
                    start += count

    source = iter(batches())
    pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix='index-prepare')
    future = None
    try:
        batch = next(source, None)
        if batch is None:
            return
        future = pool.submit(model.prepare_batch, batch)
        while future:
            while not future.done():
                checkpoint()
                try:
                    future.result(timeout=.03)
                except concurrent.futures.TimeoutError:
                    pass
            prepared = future.result()
            checkpoint()
            batch = next(source, None)
            future = pool.submit(model.prepare_batch, batch) if batch else None
            good = [p for p in prepared if p[2] is None]
            for row, _, error in prepared:
                if error is not None:
                    if isinstance(error, inference.MemoryPressure):
                        raise error
                    yield row, None, error
            if not good:
                continue
            started = time.monotonic()
            for item, value, error in inference.resilient_batch(model, good, model.encode_prepared,
                                                               checkpoint, modality(good[0][0])):
                yield item[0], value, error
            log.info('Infer batch=%d mode=%s seconds=%.3f', len(good), modality(good[0][0]), time.monotonic()-started)
    finally:
        if future:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)


def persist_vectors(db, model_key, items):
    """Commit one inference batch atomically, revalidating source and unit identity."""
    if not items:
        return 0
    accepted, signatures = [], {}
    with storage.write(db, 16384+sum(value.nbytes*4 for _, value in items)):
        for row, value in items:
            path = row['path']
            if path not in signatures:
                try:
                    stat = Path(path).stat()
                    signatures[path] = stat.st_size, stat.st_mtime_ns
                except OSError:
                    signatures[path] = None
            live = db.execute('SELECT digest FROM units WHERE id=?', (row['id'],)).fetchone()
            if live and live[0] == row['digest'] and row['_stat'] == signatures[path]:
                accepted.append((row['digest'], model_key, len(value), value.tobytes()))
                if row.get('_encode_seconds'):
                    from .work_costs import observe
                    observe(db, 'encode:'+model_key, row['kind'], 1, row['_encode_seconds'])
        db.executemany('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?)', accepted)
    return len(accepted)


def pending_units(db, model_key, dim, preferred=(), limit=128):
    frontier.settle_work(db, model_key, dim)
    query = """SELECT u.*,f.path,f.kind,f.size,f.mtime
        FROM units u JOIN files f ON f.id=u.file_id
        WHERE f.status='ready' AND coalesce(json_extract(u.location,'$.semantic'),1)<>0 AND NOT EXISTS
          (SELECT 1 FROM vectors v WHERE v.digest=u.digest AND v.model=? AND v.dim>=?)
        AND NOT EXISTS(SELECT 1 FROM embedding_errors e WHERE e.digest=u.digest AND e.model=?)"""
    args = (model_key, dim, model_key)
    order = ' ORDER BY u.ordinal,u.file_id LIMIT ?'
    rows = []
    if preferred:
        ids = tuple(preferred[:64])
        rows = db.execute(query+' AND u.file_id IN ('+','.join('?' for _ in ids)+')'+order,
                          (*args, *ids, min(32, max(1, limit//4)))).fetchall()
    # First fragments from the persistent directory frontier get their own
    # bounded lane; old fragments retain at least half of every regular batch.
    if db.execute('SELECT 1 FROM exploration_work LIMIT 1').fetchone():
        rows += db.execute(query+''' AND u.id IN (SELECT
            (SELECT first.id FROM units first WHERE first.file_id=w.file_id ORDER BY first.ordinal LIMIT 1)
            FROM exploration_work w)'''+order, (*args, min(32, max(1, limit//4)))).fetchall()
    # Retain a general lane so foreground preference cannot starve other files.
    # SQLite otherwise drives this join from ready files and sorts every unit
    # before LIMIT. Traverse the ordinal frontier directly and stop at the batch.
    ordinal_frontier = query.replace('FROM units u JOIN files f',
                             'FROM units u INDEXED BY units_frontier CROSS JOIN files f')
    ordinary = db.execute(ordinal_frontier+order, (*args, limit)).fetchall()
    return list({r['id']: r for r in rows+ordinary}.values())[:limit]


def combine_semantics(quick, full, limit):
    """The full model owns ranking and locations once it has valid results.

    Quick search is a fallback, not a second vote. Preserve all locations of
    selected files while applying the caller's file (not fragment) budget.
    """
    selected, result = set(), []
    for row in full or quick:
        fid = row['file_id']
        if fid not in selected:
            if len(selected) >= limit:
                continue
            selected.add(fid)
        result.append(row)
    return result


def worker(db_path, commands, events):
    from . import catalog
    from .inventory import Inventory
    privacy.configure_offline()
    db = store.connect(db_path)
    model, model_error = None, ''
    inference_device = None
    memory_retry_at = 0.
    load_retry_at = 0.
    model_path = store.resolve_model(db)
    quick_path = quick_index.resolve_model(db)
    if quick_path:
        quick_index.install(db)
    signature_path, expected_model = None, ''
    dimensions = embeddings.dimension(store.setting(db, 'dimensions', 256))
    roots = catalog.effective_roots(db)
    request, paused = None, False
    query_cache, vector_cache = OrderedDict(), store.VectorCache()
    last_progress, last_report, last_revision, next_gc = 0., time.monotonic(), -1, 0.
    maintenance = storage.Maintenance()
    last_model_state, vector_blocked = None, ''
    inventory = Inventory(db_path, roots, events.put)
    quick = quick_index.QuickService(db_path, quick_path, inventory, events) if quick_path else None
    quick_search = quick_index.QuickSearch()
    quick_db = store.reader(db_path) if quick else None
    last_search_revision, last_change_event = None, 0.
    report_pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix='index-statistics')
    report_future = None
    load_pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix='model-load')
    model_future, loading_path = None, ''

    def collect_stats(generation):
        connection = store.reader(db_path)
        try:
            connection.execute('BEGIN')
            stats = store.stats(connection)
            if quick and quick.model is not None:
                eligible = sum(stats['content_states'].get(state, 0) for state in ('pending', 'ready', 'error'))
                stats['quick'] = quick_index.coverage(connection, quick.model.key, eligible=eligible, reconciled=True)
            return generation, stats
        finally:
            connection.close()

    def model_state(state, text):
        nonlocal last_model_state
        if (state, text) != last_model_state:
            events.put({'type': 'model', 'state': state,
                        'ready': state in ('ready', 'indexing', 'partial'), 'text': text})
            last_model_state = state, text

    def status(text, force=False):
        nonlocal last_progress
        if force or time.monotonic()-last_progress > .3:
            events.put({'type': 'status', 'text': text})
            last_progress = time.monotonic()

    def memory_pressure():
        nonlocal model, model_error, inference_device, vector_blocked, memory_retry_at
        if model is not None and getattr(model, 'device', '') == 'cuda':
            # Release the old model after the inference stack unwinds, then load
            # on CPU in the existing loader thread. Keep the same index/model key.
            inference_device, model, model_error = 'cpu', None, ''
            model_state('loading', '显存不足，正在切换到 CPU；文件名和正文搜索可用')
            log.warning('GPU memory pressure; switching this session to CPU')
        else:
            vector_blocked = '内存不足，内容编码暂缓；稍后自动重试'
            memory_retry_at = time.monotonic()+30
            model_state('partial', vector_blocked)

    model_state('preparing' if model_path else 'missing',
                '已找到本地模型，正在准备语义搜索…' if model_path else '未找到本地模型，文件名搜索可用')

    def checkpoint(wait=True):
        nonlocal request, paused, model, model_path, model_error, dimensions, roots, vector_blocked
        nonlocal signature_path, expected_model
        while True:
            try:
                cmd = commands.get_nowait()
            except queue.Empty:
                break
            action = cmd['action']
            if action == 'stop':
                raise SystemExit
            if action == 'search':
                request = cmd
                inventory.prioritize(cmd['id'], cmd.get('kind') or store.query_intent(cmd['query'])[0])
            elif action == 'cancel_search':
                request = None
                inventory.prioritize(cmd['id'], files=[])
            elif action == 'prioritize':
                inventory.prioritize(cmd['id'], cmd.get('kind', ''), cmd.get('files', []))
            elif action in ('roots', 'configure'):
                values = {'roots': cmd['roots']} if action == 'roots' else cmd.get('settings', {})
                normalized = {}
                try:
                    for key in ('scope', 'roots', 'exclusions', 'budget_gib', 'dimensions'):
                        if key not in values:
                            continue
                        value = values[key]
                        if key == 'dimensions':
                            value = embeddings.dimension(value)
                        if key == 'budget_gib':
                            value = float(value)
                            if not .125 <= value <= 1024:
                                raise ValueError('索引预算应在 0.125–1024 GiB 之间')
                        if key == 'scope' and value not in ('computer', 'folders'):
                            raise ValueError('无效搜索范围')
                        if key in ('roots', 'exclusions'):
                            if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
                                raise ValueError('目录配置必须是路径列表')
                            value = catalog.compact_roots(value)
                        normalized[key] = value
                except (ValueError, TypeError) as exc:
                    events.put({'type': 'warning', 'text': str(exc)})
                    continue
                scope_changed = any(k in normalized and normalized[k] != store.setting(db, k)
                                    for k in ('scope', 'roots', 'exclusions'))
                with storage.transaction(db):
                    db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',
                                   [(k, json.dumps(v)) for k, v in normalized.items()])
                new_dim = embeddings.dimension(store.setting(db, 'dimensions', 256))
                if new_dim != dimensions:
                    dimensions = new_dim
                    if model is not None:
                        model.dimensions = dimensions
                    query_cache.clear()
                    vector_cache.cache.clear()
                    model_error = ''
                    with storage.transaction(db):
                        db.execute('DELETE FROM embedding_errors')
                roots = catalog.effective_roots(db)
                if scope_changed:
                    inventory.configure(roots)
                    if quick:
                        quick.coverage = {}
                else:
                    inventory.resume_capacity()
                vector_blocked = ''
                events.put({'type': 'configured'})
            elif action == 'scan':
                with storage.transaction(db):
                    db.execute("UPDATE files SET status='pending',error='' WHERE status='error'")
                    db.execute('DELETE FROM embedding_errors')
                inventory.rescan()
                vector_blocked = ''
                if model is None:
                    model_path, model_error = store.resolve_model(db), ''
            elif action == 'pause':
                paused = inventory.paused = cmd['value']
            elif action == 'model':
                query_cache.clear()
                signature_path = None
                model_path, model, model_error = cmd['path'], None, ''
                vector_blocked = ''
                store.set_setting(db, 'model', model_path)
                inventory.configure(roots)
                if quick:
                    quick.coverage = {}
                model_state('preparing', '正在切换本地模型…')
        if request:
            req, request = request, None
            query = req['query']
            kind = req.get('kind', '') or store.query_intent(query)[0]
            limit = req.get('limit', 500)
            lexical = [] if req.get('channels') else store.keywords(db, query, kind, limit)
            names = [] if req.get('channels') else store.names(db, query, kind, limit)
            semantic, error, semantic_complete = [], model_error if model is None else '', False
            quick_rows, quick_complete = [], False
            def cancelled():
                return getattr(commands, 'latest_id', req['id']) != req['id']
            if query.strip() and not cancelled():
                if quick and quick.model is not None:
                    try:
                        q = quick.model.encode(query=query.strip())
                        quick_db.execute('BEGIN')
                        try:
                            quick_rows = quick_search.search(quick_db, q, quick.model.key, kind, limit, cancelled)
                            # Only valid candidates in this request's current
                            # scope make the quick tier available. Vectors from
                            # a previous scope cannot complete the new query.
                            quick_complete = bool(quick_rows)
                            if not quick_complete and not inventory.scanning:
                                quick_complete = not quick_index.has_eligible_files(quick_db, kind)
                        finally:
                            quick_db.rollback()
                        if quick_complete and model is not None and not cancelled():
                            events.put({'type': 'results', 'id': req['id'],
                                'rows': store.fuse(names, lexical, quick_rows, query=query),
                                'semantic_rows': quick_rows, 'channels': req.get('channels', False),
                                'semantic': True, 'error': '', 'partial': True,
                                'semantic_pending': False, 'semantic_stage': 'quick',
                                'semantic_coverage': quick.coverage})
                    except Exception:
                        log.exception('Quick semantic query failed')
                try:
                    # The encoder needs the whole request: stripping "papers",
                    # "videos", etc. discards semantic intent, not just syntax.
                    # Topic extraction is only for literal matching and filters.
                    semantic_query = query.strip()
                    if model is None and signature_path != model_path:
                        signature_path = model_path
                        expected_model = query_vectors.model_signature(model_path) if model_path else ''
                    search_model = model.key if model is not None else expected_model
                    key = (search_model, dimensions, semantic_query)
                    if key not in query_cache and search_model:
                        cached = query_vectors.get(db, search_model, semantic_query, dimensions)
                        if cached is not None:
                            query_cache[key] = cached
                    if key not in query_cache and model is not None:
                        query_cache[key] = model.encode(query=semantic_query)
                        try:
                            query_vectors.put(db, search_model, semantic_query, query_cache[key])
                        except Exception:
                            log.warning('Could not persist query vector', exc_info=True)
                    if key in query_cache:
                        query_cache.move_to_end(key)
                        if len(query_cache) > 32:
                            query_cache.popitem(last=False)
                    if key in query_cache and not cancelled():
                        db.set_progress_handler(lambda: int(cancelled()), 2000)
                        try:
                            # Score the complete existing vector index before
                            # replacing lexical results. The first physical block
                            # alone is not a meaningful relevance ranking.
                            semantic = store.semantic(db, query_cache[key], search_model, kind, limit,
                                                      cache=vector_cache, cancelled=cancelled)
                            semantic_complete, error = True, ''
                        finally:
                            db.set_progress_handler(None, 0)
                except Exception as exc:
                    if inference.is_memory_error(exc):
                        memory_pressure()
                    if not cancelled():
                        error = str(exc)
                        log.exception('Semantic search failed')
            if not cancelled():
                full_has_results = bool(semantic)
                if quick_complete:
                    semantic = combine_semantics(quick_rows, semantic, limit)
                    if not semantic_complete:
                        error = ''
                stage = ('full' if full_has_results else 'quick' if quick_complete
                         else 'full' if semantic_complete else '')
                semantic_complete = semantic_complete or quick_complete
                if semantic_complete:
                    inventory.expand(req['id'], [row['file_id'] for row in semantic[:8]])
                events.put({'type': 'results', 'id': req['id'],
                            'rows': store.fuse(names, lexical, semantic, query=query),
                            'semantic_rows': semantic, 'channels': req.get('channels', False),
                            'semantic': semantic_complete and not error, 'error': error,
                            'semantic_stage': stage, 'semantic_coverage': quick.coverage if quick else {},
                            'full_checked': True,
                            'semantic_pending': bool(model_path or quick_path) and not semantic_complete and not error})
        while paused and wait:
            status('索引已暂停')
            time.sleep(.05)
            checkpoint(wait=False)

    try:
        while True:
            checkpoint()
            inventory.embedding_expected = bool(model_path) and not model_error
            if memory_retry_at and time.monotonic() >= memory_retry_at:
                memory_retry_at, vector_blocked = 0., ''
            if (model_path and model is None and not model_error and model_future is None
                    and time.monotonic() >= load_retry_at):
                model_state('loading', '显存不足，正在加载 CPU 模型…' if inference_device == 'cpu' else
                            '正在加载语义模型，已有索引保留')
                current = None
                loading_path = model_path
                def load(path, device):
                    import gc
                    gc.collect()
                    if device == 'cpu':
                        inference.release_cuda()
                    return Embedder(path, device=device) if device else Embedder(path)
                model_future = load_pool.submit(load, loading_path, inference_device)
            if model_future is not None and model_future.done():
                try:
                    loaded = model_future.result()
                    if loading_path == model_path:
                        model = loaded
                        model.dimensions = dimensions
                        store.set_setting(db, 'active_model', model.key)
                        model_state('indexing', '已切换 CPU，正在继续建立语义索引' if inference_device == 'cpu' else
                                    '正在补齐语义索引，可搜索已完成的内容')
                    loaded = None
                except Exception as exc:
                    if loading_path == model_path:
                        if isinstance(exc, inference.HostMemoryLow):
                            load_retry_at = time.monotonic()+5
                            model_state('preparing', str(exc))
                            log.warning('Model load deferred: %s', exc)
                        elif inference.is_memory_error(exc) and inference_device != 'cpu':
                            inference_device, model_error = 'cpu', ''
                            model_state('loading', '显存不足，正在切换到 CPU…')
                        else:
                            log.exception('Model load failed')
                            model_error = str(exc)
                            model_state('error', '语义模型加载失败：'+model_error)
                finally:
                    model_future = None
            work = False
            if model is not None and not vector_blocked:
                current, current_dim = model, dimensions
                try:
                    shrunk = embeddings.shrink_batch(db, current.key, dimensions)
                    work = bool(shrunk)
                    _, preferred = inventory.priority()
                    pending = pending_units(db, model.key, dimensions, preferred)
                    def valid_rows():
                        scheduled = set()
                        for row in pending:
                            checkpoint()
                            if current is not model or current_dim != dimensions:
                                return
                            if row['digest'] in scheduled:
                                continue
                            try:
                                stat = Path(row['path']).stat()
                            except OSError:
                                continue
                            if (stat.st_size, stat.st_mtime_ns) != (row['size'], row['mtime']):
                                continue
                            item = dict(row)
                            item['_stat'] = stat.st_size, stat.st_mtime_ns
                            scheduled.add(row['digest'])
                            yield item
                    writes = []
                    def flush_vectors():
                        if current is model and current_dim == dimensions:
                            persist_vectors(db, current.key, writes)
                        writes.clear()
                    def encoding_checkpoint():
                        # Save the completed batch before a search, pause, or new
                        # inference. Never hold the SQLite writer during GPU work.
                        flush_vectors()
                        checkpoint()
                        if current is not model or current_dim != dimensions:
                            raise IndexChanged()
                    for row, value, failure in encoded_units(current, valid_rows(), encoding_checkpoint):
                        if current is not model or current_dim != dimensions:
                            break
                        work = True
                        status('语义索引 · '+Path(row['path']).name)
                        if failure is not None:
                            flush_vectors()
                            with storage.write(db, 16384):
                                db.execute('INSERT OR REPLACE INTO embedding_errors VALUES(?,?,?,?)',
                                           (row['digest'], current.key, str(failure)[:600], time.time()))
                            continue
                        writes.append((row, value))
                        if len(writes) >= 8:
                            flush_vectors()
                    flush_vectors()
                    if current is not model or current_dim != dimensions:
                        raise IndexChanged()
                except IndexChanged:
                    current = None
                    continue
                except inference.MemoryPressure:
                    flush_vectors()
                    memory_pressure()
                    current = None
                    continue
                except storage.BudgetFull as exc:
                    vector_blocked = str(exc)
                except Exception as exc:
                    if inference.is_memory_error(exc):
                        memory_pressure()
                        current = None
                        continue
                    if storage.is_busy(exc):
                        db.rollback()
                        time.sleep(.05)
                        continue
                    # SQLite's hard page limit is also a capacity pause, not a crash.
                    if 'full' in str(exc).lower():
                        vector_blocked = '索引空间已达预算'
                    else:
                        raise
                complete = (not work and not inventory.scanning and not inventory.full and not inventory.extracting and
                            not db.execute('SELECT 1 FROM extraction_jobs LIMIT 1').fetchone() and
                            not db.execute("SELECT 1 FROM files WHERE status='pending' LIMIT 1").fetchone())
                if vector_blocked or inventory.blocked:
                    model_state('partial', vector_blocked or next(iter(inventory.blocked.values())))
                elif complete:
                    failed = db.execute('SELECT count(*) FROM embedding_errors WHERE model=?', (model.key,)).fetchone()[0]
                    model_state('partial' if failed else 'ready',
                                f'语义搜索可用 · {failed} 个片段失败，可在设置中重试' if failed else '语义搜索已就绪')
                elif work:
                    model_state('indexing', '正在补齐语义索引，可搜索已完成的内容')
            now = time.monotonic()
            if now-last_change_event >= 1:
                revision = (store.search_revision(db), quick.revision if quick else 0)
                if revision != last_search_revision:
                    events.put({'type': 'index_changed', 'revision': revision})
                    last_search_revision = revision
                last_change_event = now
            if now >= next_gc:
                next_gc = now+maintenance.step(db)
            if report_future is not None and report_future.done():
                try:
                    generation, stats = report_future.result()
                    if generation == inventory.generation:
                        if quick and 'quick' in stats:
                            quick.coverage = stats['quick']
                        stats['discovery'] = inventory.discovery
                        stats['scanning'] = inventory.scanning
                        stats['content'] = inventory.content
                        stats['blocked'] = vector_blocked or next(iter(inventory.blocked.values()), '')
                        stats['watching'] = bool(inventory.watchers) and all(w.active for w in inventory.watchers)
                        events.put({'type': 'indexed', 'stats': stats})
                        store.save_coverage(db, stats)
                except Exception:
                    log.exception('Index statistics failed')
                report_future = None
            if (report_future is None and now-last_report >= 3 and inventory.pruned_generation == inventory.generation
                    and (inventory.revision != last_revision or work or vector_blocked)):
                # Counting millions of content rows must not block query inference.
                report_future = report_pool.submit(collect_stats, inventory.generation)
                last_report, last_revision = now, inventory.revision
                storage.compact(db)
            if not work:
                status(vector_blocked or inventory.content if not inventory.scanning else inventory.discovery)
                time.sleep(.05)
    except SystemExit:
        pass
    except Exception as exc:
        log.exception('Worker failed')
        events.put({'type': 'fatal', 'text': str(exc)})
    finally:
        if quick:
            quick.close()
        if quick_db is not None:
            quick_db.close()
        inventory.close()
        report_pool.shutdown(wait=False, cancel_futures=True)
        load_pool.shutdown(wait=False, cancel_futures=True)
        db.close()


def main():
    import sys
    import threading
    commands = queue.Queue()
    output = sys.stdout
    # Reserve stdout for the protocol; third-party model progress belongs in the log.
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    class Events:
        def put(self, event):
            output.write(json.dumps(event, ensure_ascii=False)+'\n')
            output.flush()
    def read():
        # A blocking Windows CRT stdin read holds a descriptor lock. Native
        # NumPy/DLL initialization can need that lock too, deadlocking startup.
        # Only call os.read after the pipe reports bytes ready; never hold it
        # across a wait for the next UI command.
        fd = sys.stdin.fileno()
        if os.name == 'nt':
            import ctypes
            import msvcrt
            peek = ctypes.WinDLL('kernel32', use_last_error=True).PeekNamedPipe
            peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                             ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
            peek.restype = ctypes.c_int
            handle = msvcrt.get_osfhandle(fd)
        buffer = b''
        while True:
            if os.name == 'nt':
                available = ctypes.c_uint32()
                if not peek(handle, None, 0, None, ctypes.byref(available), None):
                    break
                if not available.value:
                    time.sleep(.03)
                    continue
                size = min(65536, available.value)
            else:
                size = 65536
            try:
                chunk = os.read(fd, size)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                try:
                    command = json.loads(line)
                    if command.get('action') in ('search', 'cancel_search'):
                        commands.latest_id = command['id']
                    commands.put(command)
                except (ValueError, UnicodeError):
                    continue
        commands.put({'action': 'stop'})
    threading.Thread(target=read, daemon=True).start()
    worker(sys.argv[1], commands, Events())


if __name__ == '__main__':
    main()
