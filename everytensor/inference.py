"""Bounded inference retries. Never carry accelerator tracebacks across retries."""
from __future__ import annotations

import gc
import logging
import sys
import os
from pathlib import Path

log = logging.getLogger(__name__)


class MemoryPressure(RuntimeError):
    pass


class HostMemoryLow(MemoryPressure):
    pass


def host_available():
    if os.name == 'nt':
        import ctypes as c
        class Status(c.Structure):
            _fields_ = [('length', c.c_ulong), ('load', c.c_ulong)] + [
                (name, c.c_ulonglong) for name in ('total', 'available', 'page_total',
                'page_available', 'virtual_total', 'virtual_available', 'extended')]
        status = Status()
        status.length = c.sizeof(status)
        if c.windll.kernel32.GlobalMemoryStatusEx(c.byref(status)):
            return status.available
    elif sys.platform.startswith('linux'):
        try:
            for line in Path('/proc/meminfo').read_text().splitlines():
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1])*1024
        except (OSError, ValueError):
            pass
    return None


def ensure_host_memory(path, device, *, weight_multiplier=None):
    # CPU uses float32, while the bundled checkpoint is predominantly bf16.
    # Reserve room for imports, activations and the desktop as well as weights.
    weights = sum(p.stat().st_size for p in Path(path).glob('*.safetensors'))
    multiplier = weight_multiplier if weight_multiplier is not None else (2 if device == 'cpu' else 1)
    required = weights*multiplier + 1024**3
    available = host_available()
    if available is not None and available < required:
        raise HostMemoryLow('可用内存不足，模型加载暂缓；内存释放后自动继续')


def is_memory_error(exc):
    return isinstance(exc, (MemoryError, MemoryPressure)) or 'out of memory' in str(exc).lower()


def failure(exc):
    # Tracebacks/context can retain the failed forward pass and all its tensors.
    # Return a fresh exception, then leave the original except scope before retry.
    return (MemoryPressure if is_memory_error(exc) else RuntimeError)(str(exc))


def attempt(call):
    try:
        return call(), None
    except Exception as exc:
        log.warning('Inference attempt failed: %s', str(exc).splitlines()[0])
        return None, failure(exc)


def release_cuda():
    gc.collect()
    torch = sys.modules.get('torch')
    if torch is None:
        return True
    try:
        if torch.cuda.is_initialized():
            torch.cuda.empty_cache()
        return True
    except Exception as exc:
        # Allocation errors can surface asynchronously, even from empty_cache.
        log.warning('CUDA cleanup failed: %s', str(exc).splitlines()[0])
        return False


def resilient_batch(model, items, encode, checkpoint, mode='text'):
    """Halve failing batches; one OOM item requests a device fallback, not a file error."""
    cap = max(1, getattr(model, 'text_batch_size' if mode == 'text' else 'image_batch_size', len(items)))
    if len(items) > cap:
        for start in range(0, len(items), cap):
            yield from resilient_batch(model, items[start:start+cap], encode, checkpoint, mode)
        return
    checkpoint()
    values, error = attempt(lambda: encode(items))
    if error is None and len(values) != len(items):
        error = RuntimeError('批量编码数量不匹配')
    if error is None:
        yield from ((item, value, None) for item, value in zip(items, values))
        return
    if isinstance(error, MemoryPressure):
        if getattr(model, 'device', '') == 'cuda':
            field = 'text_batch_size' if mode == 'text' else 'image_batch_size'
            setattr(model, field, min(getattr(model, field, 1), max(1, len(items)//2)))
            if not release_cuda():
                raise error
        else:
            gc.collect()
        if len(items) == 1:
            raise error
    if len(items) > 1:
        middle = len(items)//2
        yield from resilient_batch(model, items[:middle], encode, checkpoint, mode)
        yield from resilient_batch(model, items[middle:], encode, checkpoint, mode)
    else:
        yield items[0], None, error
