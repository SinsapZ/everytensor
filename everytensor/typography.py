from functools import lru_cache
from tkinter.font import families


@lru_cache(maxsize=1)
def family():
    installed = set(families())
    return next((name for name in ('Noto Sans SC', 'Microsoft YaHei UI', 'Segoe UI') if name in installed), 'TkDefaultFont')
