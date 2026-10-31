"""Search scope and content admission. No file contents are read here."""
from __future__ import annotations

import os
from pathlib import Path


def canonical(path):
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def within(path, root):
    try:
        return os.path.commonpath([canonical(path), canonical(root)]).casefold() == canonical(root).casefold()
    except ValueError:
        return False


def compact_roots(roots):
    result = []
    for root in sorted({canonical(p) for p in roots if p}, key=len):
        if not any(within(root, other) for other in result):
            result.append(root)
    return result


def local_drives():
    """Fixed local volumes only; removable/network roots may be added explicitly."""
    if os.name != 'nt':
        return [str(Path.home())]
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetLogicalDrives.restype = ctypes.c_uint32
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel.GetDriveTypeW.restype = ctypes.c_uint32
    mask = kernel.GetLogicalDrives()
    return [f'{chr(65+i)}:\\' for i in range(26)
            if mask & (1 << i) and kernel.GetDriveTypeW(f'{chr(65+i)}:\\') == 3]


def effective_roots(db):
    from . import store
    folders = store.setting(db, 'roots', [])
    return compact_roots((local_drives() if store.setting(db, 'scope', 'folders') == 'computer' else []) + folders)


def search_scope(db):
    """Read the requested scope, even while the inventory is still reconciling it.

    A bare database used by the index/query API has no scope until configured.
    An explicitly empty folder scope, on the other hand, contains no files.
    """
    from . import store
    configured = store.setting(db, 'scope') is not None or bool(store.setting(db, 'roots', []))
    return (tuple(effective_roots(db)) if configured else None,
            tuple(excluded_paths(db)))


def scope_clause(scope, column='path_key'):
    """Literal indexed path ranges; never interpret '%' or '_' as wildcards."""
    roots, excluded = scope
    def ranges(paths):
        clauses, values = [], []
        for path in paths:
            key = search_key(canonical(path))
            prefix = key.rstrip('\\')+'\\'
            clauses.append(f'({column}=? OR ({column}>=? AND {column}<?))')
            values.extend((key, prefix, prefix+'\U0010ffff'))
        return '('+' OR '.join(clauses)+')' if clauses else '0', values
    include, values = ('1', []) if roots is None else ranges(roots)
    omit, omit_values = ranges(excluded)
    return f'({include} AND NOT {omit})', (*values, *omit_values)


def initialize_scope(db, data):
    """New catalogs default to the computer; preserve explicit existing libraries."""
    from . import store
    if store.setting(db, 'scope') is not None:
        return
    roots = store.setting(db, 'roots', [])
    default = Path(__file__).resolve().parent.parent/'.everytensor'
    old_default = Path(data).resolve() == default and compact_roots(roots) == [str(default.parent)]
    store.set_setting(db, 'scope', 'computer' if not roots or old_default else 'folders')


# Keep names discoverable in these trees, without embedding generated/system data.
NAME_ONLY = {'.git', '.hg', '.svn', '.venv', 'venv', 'node_modules', '__pycache__',
             '.pytest_cache', '.mypy_cache', '.ruff_cache', '$recycle.bin',
             'system volume information', 'windows', 'program files', 'program files (x86)',
             'cache', 'caches', 'gpucache', 'code cache'}


def content_allowed(path, kind, stat=None):
    if kind == 'other' or any(p.casefold() in NAME_ONLY for p in Path(path).parts):
        return False
    # Offline/cloud placeholders stay name-searchable; never hydrate a drive on scan.
    attributes = getattr(stat, 'st_file_attributes', 0)
    return not (attributes & (0x1000 | 0x40000 | 0x400000))


def excluded_paths(db):
    from . import store
    database = db.execute('PRAGMA database_list').fetchone()[2]
    folder = Path(database).resolve().parent
    own_data = ([str(folder)] if folder.name == '.everytensor' else
                [database+suffix for suffix in ('', '-wal', '-shm', '-journal')] +
                [str(folder/name) for name in ('backend.log', 'ui.log',
                                                'quick-search.bin', 'quick-search.building',
                                                'quick-search.lock')])
    model = store.setting(db, 'model', '')
    quick_model = store.setting(db, 'quick_model', '')
    bundled = str(Path(__file__).resolve().parents[1]/'models')
    return compact_roots(own_data + [bundled, model, quick_model] + store.setting(db, 'exclusions', []))


def grams(text):
    """Literal Unicode substring postings; include unigrams for one-character input."""
    value = str(text).casefold().replace('/', '\\')
    return ' '.join(sorted({('u'+c.encode('utf-8').hex()) for c in value} |
                           {('b'+value[i:i+2].encode('utf-8').hex()) for i in range(len(value)-1)}))


def expression(term):
    value = term.casefold().replace('/', '\\')
    parts = [('b'+value[i:i+2].encode('utf-8').hex()) for i in range(len(value)-1)]
    if not parts and value:
        parts = ['u'+value.encode('utf-8').hex()]
    return ' AND '.join('"'+p+'"' for p in dict.fromkeys(parts))


def search_key(text):
    return str(text).casefold().replace('/', '\\')
