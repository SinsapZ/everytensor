"""Explicit optional-component installation, separate from offline search.

Every executable/model download is versioned, sized and SHA-256 pinned in the
shipped manifest. Only a successfully validated runtime receives a ready marker.
"""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile


class Cancelled(Exception):
    pass


def manifest():
    from .paths import resource_root
    return json.loads((resource_root() / 'everytensor' / 'model-assets.json').read_text('utf-8'))


def component_root(data):
    return Path(data).resolve() / 'multimodal'


def installed_configuration(data):
    root = component_root(data)
    try:
        saved = json.loads((root / 'ready.json').read_text('utf-8'))
        if saved['id'] != manifest()['id']:
            return None
        python = root / 'runtime' / 'python.exe'
        model = Path(saved['model'])
        required = [python, model / 'model.safetensors', model / 'config.json',
                    root / 'ffmpeg/bin/ffmpeg.exe', root / 'ffmpeg/bin/ffprobe.exe']
        if not all(path.is_file() for path in required):
            return None
        return dict(python=python, model=model, root=root)
    except (OSError, ValueError, KeyError):
        return None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
    temporary.replace(path)


def checkpoint(cancel):
    if cancel.is_set():
        raise Cancelled()


def checksum(path, cancel=None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(4 * 1024**2):
            if cancel is not None:
                checkpoint(cancel)
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def installation_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'install.lock').open('a+b') as stream:
        try:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('另一个 EveryTensor 窗口正在安装模型，请等待其完成。') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def download(item, destination, cancel, progress, *, opener=None, status=None, transferred=None):
    """Resume only the pinned bytes; publish a final file after a full hash check."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = int(item['bytes'])
    status = status or (lambda stage: None)
    transferred = transferred or (lambda count: None)
    if destination.is_file() and destination.stat().st_size == expected:
        status('checking')
        if checksum(destination, cancel) == item['sha256']:
            progress(expected)
            return destination
    partial = destination.with_name(destination.name + '.part')
    opener = opener or urllib.request.build_opener()
    for attempt in range(3):
        checkpoint(cancel)
        start = partial.stat().st_size if partial.exists() else 0
        if start > expected:
            partial.unlink()
            start = 0
        if start == expected:
            status('checking')
            if checksum(partial, cancel) == item['sha256']:
                partial.replace(destination)
                progress(expected)
                return destination
            partial.unlink()
            start = 0
        headers = {'User-Agent': 'EveryTensor-model-setup/1', 'Accept-Encoding': 'identity'}
        if start:
            headers['Range'] = f'bytes={start}-'
        try:
            status('connecting')
            with opener.open(urllib.request.Request(item['url'], headers=headers), timeout=12) as response:
                append = response.status == 206 and start > 0
                if response.status == 206:
                    expected_range = f'bytes {start}-{expected - 1}/{expected}'
                    if response.headers.get('Content-Range') != expected_range:
                        raise ValueError('下载服务器返回了错误的文件范围，请重试。')
                elif response.status != 200:
                    raise ValueError('下载服务器未返回有效文件。')
                written = start if append else 0
                length = response.headers.get('Content-Length')
                if length is not None and int(length) != expected - written:
                    raise ValueError('下载文件大小与固定版本不一致。')
                with partial.open('ab' if append else 'wb') as output:
                    progress(written)
                    status('downloading')
                    # Read available bytes instead of waiting to fill a large
                    # buffer, so pause/close stays responsive on slow links.
                    while block := response.read1(256 * 1024):
                        checkpoint(cancel)
                        if written + len(block) > expected:
                            raise ValueError('下载内容超过了预期大小。')
                        output.write(block)
                        written += len(block)
                        transferred(len(block))
                        progress(written)
                if written != expected:
                    raise OSError('网络连接中断，已保存下载进度。')
            checkpoint(cancel)
            status('checking')
            if checksum(partial, cancel) != item['sha256']:
                partial.unlink()
                raise ValueError('文件校验失败，未启用模型；请重试下载。')
            partial.replace(destination)
            return destination
        except (OSError, urllib.error.URLError):
            checkpoint(cancel)
            if attempt == 2:
                raise
            status('retrying')
            if cancel.wait(1 + attempt * 2):
                raise Cancelled()
    raise RuntimeError('下载未完成')


def extract(archive, destination, cancel, *, wheel=False, strip_top=False):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            checkpoint(cancel)
            path = PurePosixPath(info.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts or any(':' in part for part in path.parts):
                raise ValueError('安装包包含不安全的文件路径')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('安装包不应包含符号链接')
            parts = path.parts[1:] if strip_top else path.parts
            if not parts:
                continue
            if wheel and parts[0].endswith('.data'):
                # No entry-point executables or installer scripts are needed.
                if len(parts) < 3 or parts[1] not in ('purelib', 'platlib'):
                    continue
                parts = parts[2:]
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError('安装路径超出组件目录')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(info) as stream, target.open('wb') as output:
                    while block := stream.read(1024**2):
                        checkpoint(cancel)
                        output.write(block)


def worker_entry():
    from .paths import resource_root, frozen
    if frozen():
        return resource_root() / 'model-worker' / 'model_worker_entry.py'
    return resource_root() / 'tools' / 'model_worker_entry.py'


def runtime_environment(root):
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['EVERYTENSOR_COMPONENTS'] = str(root)
    env['HF_HUB_OFFLINE'] = env['TRANSFORMERS_OFFLINE'] = '1'
    env['HF_HUB_DISABLE_TELEMETRY'] = env['DO_NOT_TRACK'] = '1'
    return env


class Installer:
    def __init__(self, data, progress, cancel=None, *, assets=None, opener=None):
        self.data = Path(data).resolve()
        self.root = component_root(data)
        self.assets = assets or manifest()
        self.progress = progress
        self.cancel = cancel or threading.Event()
        self.opener = opener

    def fetch(self, jobs, total):
        """Overlap independent files, keeping extraction and publication serialized."""
        received = [0] * len(jobs)
        stages = {}
        finished = set()
        network = 0
        lock = threading.Lock()
        for index, (item, target) in enumerate(jobs):
            partial = target.with_name(target.name + '.part')
            candidate = target if target.is_file() else partial
            if candidate.is_file():
                received[index] = min(item['bytes'], candidate.stat().st_size)

        def emit():
            active = set(stages.values())
            stage = next((s for s in ('downloading', 'retrying', 'connecting', 'checking') if s in active), 'connecting')
            detail = {'downloading': '正在下载模型和运行组件', 'retrying': '网络连接中断，正在自动重试（保留已下载部分）',
                      'connecting': '正在连接下载服务器', 'checking': '正在校验已下载的文件'}[stage]
            self.progress(dict(phase='downloading', detail=detail, received=sum(received), total=total,
                               network_received=network, transfer_state=stage,
                               completed_files=len(finished), file_count=len(jobs), active_files=len(stages)))

        def work(index):
            item, target = jobs[index]
            def report(value):
                with lock:
                    received[index] = value
                    emit()
            def status(stage):
                with lock:
                    stages[index] = stage
                    emit()
            def transferred(count):
                nonlocal network
                with lock:
                    network += count
            download(item, target, self.cancel, report, opener=self.opener,
                     status=status, transferred=transferred)
            with lock:
                received[index] = item['bytes']
                finished.add(index)
                stages.pop(index, None)
                emit()

        emit()
        # Start long transfers early so small dependencies can finish alongside.
        order = sorted(range(len(jobs)), key=lambda i: jobs[i][0]['bytes'], reverse=True)
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix='model-download') as pool:
            futures = [pool.submit(work, index) for index in order]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                self.cancel.set()
                for future in futures:
                    future.cancel()
                raise

    def validate(self, python, model):
        from .paths import resource_root
        log = self.root / 'validation.log'
        with log.open('w', encoding='utf-8') as output:
            process = subprocess.Popen([str(python), '-u', str(worker_entry()), str(resource_root()),
                                        '--validate', str(model)], stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       env=runtime_environment(self.root),
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                deadline = time.monotonic() + 300
                while process.poll() is None:
                    checkpoint(self.cancel)
                    if time.monotonic() > deadline:
                        raise RuntimeError('模型验证超时，请关闭占用大量内存的应用后重试。')
                    self.cancel.wait(.2)
                if process.returncode:
                    raise RuntimeError('组件验证失败，轻量搜索仍可用。详情：' + str(log))
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)

    def run(self, local_model=None):
        with installation_lock(self.root):
            if installed_configuration(self.data):
                return installed_configuration(self.data)
            checkpoint(self.cancel)
            artifacts = self.assets['artifacts']
            total = sum(a['bytes'] for a in artifacts if not local_model or a['kind'] != 'model')
            # Runtime extraction, model files and reusable download archives are
            # outside the index budget. Reserve working room before any download.
            required = total + self.assets.get('working_bytes', 3 * 1024**3)
            present = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
            if shutil.disk_usage(self.root).free < max(0, required - present):
                raise RuntimeError(f'模型安装需要约 {required / 1024**3:.1f} GiB 可用空间，请先释放空间。')
            model = Path(local_model).resolve() if local_model else self.root / 'model'
            archives = []
            jobs = []
            # Reject an incompatible local folder before downloading components.
            ordered = sorted(artifacts, key=lambda a: a['kind'] != 'model') if local_model else artifacts
            for item in ordered:
                checkpoint(self.cancel)
                if item['kind'] == 'model' and local_model:
                    target = model / item['path']
                    self.progress(dict(phase='verifying', detail='正在校验已有模型：' + item['path'], received=0, total=total))
                    if (not target.is_file() or target.stat().st_size != item['bytes']
                            or checksum(target, self.cancel) != item['sha256']):
                        raise ValueError('已有模型文件缺失或版本不匹配：' + item['path'])
                    continue
                target = model / item['path'] if item['kind'] == 'model' else self.root / 'downloads' / item['name']
                jobs.append((item, target))
                if item['kind'] != 'model':
                    archives.append((item, target))
            self.fetch(jobs, total)
            self.progress(dict(phase='installing', detail='正在准备本地运行组件', received=total, total=total))
            runtime = self.root / 'runtime'
            # No ready marker exists, so no app process may use these files yet.
            for item, archive in archives:
                if item['kind'] == 'python':
                    extract(archive, runtime, self.cancel)
                elif item['kind'] == 'wheel':
                    extract(archive, runtime / 'Lib' / 'site-packages', self.cancel, wheel=True)
                elif item['kind'] == 'ffmpeg':
                    extract(archive, self.root / 'ffmpeg', self.cancel, strip_top=True)
            (runtime / 'python311._pth').write_text('python311.zip\n.\nLib/site-packages\nimport site\n', 'utf-8')
            self.progress(dict(phase='verifying', detail='正在验证文字、图片、音视频推理；首次验证可能需要几分钟', received=total, total=total))
            self.validate(runtime / 'python.exe', model)
            checkpoint(self.cancel)
            atomic_json(self.root / 'ready.json', dict(id=self.assets['id'], model=str(model), installed_at=time.time()))
            return dict(python=runtime / 'python.exe', model=model, root=self.root)
