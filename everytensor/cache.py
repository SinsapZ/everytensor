"""Bounded, process-local caches. Durable search data remains in SQLite."""
from collections import OrderedDict
from threading import RLock


class ByteCache:
    def __init__(self, max_bytes, max_items=128):
        self.max_bytes, self.max_items = max_bytes, max_items
        self.items = OrderedDict()
        self.bytes = 0
        self.lock = RLock()

    def get(self, key):
        with self.lock:
            item = self.items.get(key)
            if item is None:
                return None
            self.items.move_to_end(key)
            return item[0]

    def put(self, key, value, size):
        with self.lock:
            old = self.items.pop(key, None)
            if old:
                self.bytes -= old[1]
            if size > self.max_bytes:
                return
            self.items[key] = value, size
            self.bytes += size
            while self.bytes > self.max_bytes or len(self.items) > self.max_items:
                _, (_, count) = self.items.popitem(last=False)
                self.bytes -= count

    def clear(self):
        with self.lock:
            self.items.clear()
            self.bytes = 0
