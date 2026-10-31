"""Qt models for the native client. File identity survives live reranking."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from PySide6.QtCore import QAbstractListModel, QModelIndex, Property, Qt, Signal, Slot

from . import media

_HOME = Path.home()


def location_label(row):
    from .locations import label
    return label(row.get('location') or {})


class ResultModel(QAbstractListModel):
    countChanged = Signal()
    _names = ('fileId', 'name', 'parentPath', 'fullPath', 'kind', 'suffix',
              'locationLabel', 'thumbnailSource')
    _roles = {int(Qt.ItemDataRole.UserRole)+i+1: name.encode() for i, name in enumerate(_names)}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self._thumbnails = {}
        self._views = {}

    def roleNames(self):
        return self._roles

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    @Property(int, notify=countChanged)
    def count(self):
        return len(self.rows)

    @staticmethod
    def presentation(row):
        path = Path(row.get('path', ''))
        parent_path = path.parent
        parent = str(parent_path)
        try:
            relative = parent_path.relative_to(_HOME)
            parent = '~/'+relative.as_posix() if relative.parts else '~'
        except (ValueError, OSError):
            pass
        thumbnail = ''
        if row.get('kind') in ('image', 'video') or path.suffix.lower() == '.pdf':
            identity = json.dumps([str(path), row.get('size'), row.get('mtime')], ensure_ascii=False).encode('utf-8')
            thumbnail = 'image://thumb/'+base64.urlsafe_b64encode(identity).decode('ascii').rstrip('=')
        return dict(fileId=row['file_id'], name=row.get('name', path.name), parentPath=parent,
                    fullPath=str(path), kind=row.get('kind', 'other'), suffix=path.suffix.lstrip('.').upper(),
                    locationLabel=location_label(row), thumbnailSource=thumbnail)

    def data(self, index, role=int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == int(Qt.ItemDataRole.DisplayRole):
            return row.get('name', '')
        name = self._roles.get(role)
        if name is None:
            return None
        key = name.decode()
        if key == 'thumbnailSource' and row['file_id'] in self._thumbnails:
            return self._thumbnails[row['file_id']]
        return self._views[row['file_id']].get(key)

    @Slot(int, result='QVariantMap')
    def get(self, index):
        return dict(self._views[self.rows[index]['file_id']]) if 0 <= index < len(self.rows) else {}

    @Slot(int, result=int)
    def indexOf(self, file_id):
        return next((i for i, row in enumerate(self.rows) if row['file_id'] == file_id), -1)

    def set_thumbnail(self, file_id, source):
        self._thumbnails[file_id] = source
        index = self.indexOf(file_id)
        if index >= 0:
            role = next(key for key, value in self._roles.items() if value == b'thumbnailSource')
            self.dataChanged.emit(self.index(index), self.index(index), [role])

    def replace(self, rows):
        """Batch adjacent edits while preserving Qt's persistent file indexes.

        Emitting one insertion per file makes QML relayout a view hundreds of
        times when switching categories. Contiguous additions/removals are one
        change; identities already present still move without a model reset.
        """
        previous_count = len(self.rows)
        wanted = {row['file_id'] for row in rows}
        runs, start = [], None
        for index, row in enumerate(self.rows):
            if row['file_id'] not in wanted:
                if start is None:
                    start = index
            elif start is not None:
                runs.append((start, index-1))
                start = None
        if start is not None:
            runs.append((start, len(self.rows)-1))
        for first, last in reversed(runs):
            self.beginRemoveRows(QModelIndex(), first, last)
            for old in self.rows[first:last+1]:
                self._thumbnails.pop(old['file_id'], None)
                self._views.pop(old['file_id'], None)
            del self.rows[first:last+1]
            self.endRemoveRows()

        present = {row['file_id'] for row in self.rows}
        target, changed = 0, []
        while target < len(rows):
            row = rows[target]
            if row['file_id'] not in present:
                end = target+1
                while end < len(rows) and rows[end]['file_id'] not in present:
                    end += 1
                added = [dict(item) for item in rows[target:end]]
                for item in added:
                    self._views[item['file_id']] = self.presentation(item)
                self.beginInsertRows(QModelIndex(), target, end-1)
                self.rows[target:target] = added
                present.update(item['file_id'] for item in added)
                self.endInsertRows()
                target = end
                continue
            if self.rows[target]['file_id'] != row['file_id']:
                current = next(i for i in range(target+1, len(self.rows))
                               if self.rows[i]['file_id'] == row['file_id'])
                self.beginMoveRows(QModelIndex(), current, current, QModelIndex(), target)
                self.rows.insert(target, self.rows.pop(current))
                self.endMoveRows()
            if self.rows[target] != row:
                old = self.rows[target]
                if (old.get('path'), old.get('size'), old.get('mtime')) != (row.get('path'), row.get('size'), row.get('mtime')):
                    self._thumbnails.pop(row['file_id'], None)
                self.rows[target] = dict(row)
                self._views[row['file_id']] = self.presentation(row)
                changed.append(target)
            target += 1
        if changed:
            first = last = changed[0]
            for index in changed[1:]:
                if index == last+1:
                    last = index
                else:
                    self.dataChanged.emit(self.index(first), self.index(last), list(self._roles))
                    first = last = index
            self.dataChanged.emit(self.index(first), self.index(last), list(self._roles))
        if previous_count != len(self.rows):
            self.countChanged.emit()
