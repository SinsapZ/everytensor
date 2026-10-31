from __future__ import annotations

import concurrent.futures
import os
import queue
import subprocess
import sys
import time
import json
import threading
import logging
from logging.handlers import RotatingFileHandler
import tkinter as tk
from . import typography
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from . import media, store
from .cache import ByteCache
from .widgets import SearchBox, ResultList, Button, ImageView, BG, WHITE, INK, MUTED, RED, LINE, TINT

TYPES = [('全部', ''), ('文档', 'document'), ('图片', 'image'), ('视频', 'video'), ('音频', 'audio'), ('代码', 'code')]


class Backend:
    """One child with ordinary stdin/stdout; no named pipes or listening sockets."""
    def __init__(self, db_path, events):
        log_path = Path(db_path).parent/'backend.log'
        if log_path.exists() and log_path.stat().st_size > 4*1024*1024:
            log_path.replace(log_path.with_suffix('.previous.log'))
        self.log = open(log_path, 'a', encoding='utf-8')
        self.closing = False
        executable = Path(sys.executable)
        if executable.stem.lower() == 'pythonw':
            executable = executable.with_name('python.exe')
        self.process = subprocess.Popen([str(executable), '-u', '-m', 'everytensor.engine', str(db_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding='utf-8', bufsize=1, creationflags=media.FLAGS,
            cwd=Path(__file__).resolve().parent.parent)
        def read():
            for line in self.process.stdout:
                try:
                    events.put(json.loads(line))
                except ValueError:
                    pass
            if not self.closing:
                events.put({'type': 'fatal', 'text': '后台进程已退出，请重新打开应用；详情见 backend.log'})
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def put(self, command):
        if self.process.poll() is None:
            try:
                self.process.stdin.write(json.dumps(command, ensure_ascii=False)+'\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def close(self):
        self.closing = True
        self.put({'action': 'stop'})
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                # The venv executable is a launcher. Terminate its child too if
                # model inference or native teardown does not finish in time.
                subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                               capture_output=True, creationflags=media.FLAGS, timeout=5)
            else:
                self.process.terminate()
            self.process.wait(timeout=3)
        self.reader.join(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()
        self.log.close()


class App(tk.Tk):
    def __init__(self, data):
        super().__init__()
        self.title('EveryTensor')
        self.geometry('1180x790')
        self.minsize(920, 620)
        self.configure(bg=BG)
        self.data, self.db_path = data, data/'index.sqlite3'
        self.db = store.connect(self.db_path)
        self.logger = logging.getLogger(f'everytensor.ui.{id(self)}')
        self.log_handler = RotatingFileHandler(data/'ui.log', maxBytes=2*1024*1024, backupCount=1, encoding='utf-8')
        self.log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self.logger.addHandler(self.log_handler)
        self.logger.setLevel(logging.INFO)
        self.query = tk.StringVar()
        self.kind = ''
        self.generation, self.preview_token = 0, 0
        self.last_semantic_generation = -1
        self.rows, self.visible, self.limit = [], 100, 500
        self.selected, self.photo, self.timer = None, None, None
        self.selection_origin = None
        self.user_selection_generation = -1
        self.keyframe_photos = []
        self.futures = queue.Queue()
        self.search_pool = concurrent.futures.ThreadPoolExecutor(1)
        self.keyword_pool = concurrent.futures.ThreadPoolExecutor(1)
        self.preview_pool = concurrent.futures.ThreadPoolExecutor(1)
        self.frame_pool = concurrent.futures.ThreadPoolExecutor(2)
        self.thumbnail_pool = concurrent.futures.ThreadPoolExecutor(1)
        self.search_jobs = {}
        self.search_cancel = threading.Event()
        self.channels = {'names': [], 'keywords': [], 'semantic': []}
        self.channel_more = dict.fromkeys(self.channels, False)
        self.pending_channels = set()
        self.semantic_inflight = False
        self.result_generation = -1
        self.semantic_stage, self.semantic_coverage = '', {}
        self.refreshing_results = False
        self.index_dirty = False
        self.last_index_refresh = 0.
        self.scope_pending = True
        self.preview_cancel = threading.Event()
        self.preview_key = None
        self.fit_width = True
        self.asset_cache = ByteCache(48*1024*1024, max_items=128)
        self.preview_resize_timer = None
        self.thumbnail_versions = {}
        self.thumbnail_failures = {}
        self.thumbnail_jobs = {}
        self.thumbnail_pending = set()
        self.preview_future = None
        model_path = store.resolve_model(self.db, Path(__file__).resolve().parent.parent/'models/embeddinggemma-2')
        from . import quick_index
        quick_path = quick_index.resolve_model(self.db, Path(__file__).resolve().parent.parent/'models/quick-semantic')
        self.model_state = 'preparing' if model_path or quick_path else 'missing'
        self.model_status = '正在准备语义搜索，已有索引保留' if model_path or quick_path else '未找到本地模型，文件名搜索可用'
        self.model_status_var = tk.StringVar(value=self.model_status)
        self.last_stats = store.load_coverage(self.db)
        self.last_status = '已恢复上次进度' if self.last_stats else '正在读取索引进度'
        self.warning, self.paused = '', False
        self.search_error = ''
        self.closing = False
        self.compact = False
        self.pane_ratio = .48
        self.events = queue.Queue()
        self._build()
        self.bind('<Configure>', self.resize_layout)
        self.refresh_footer()
        from . import catalog
        catalog.initialize_scope(self.db, data)
        self.commands = Backend(self.db_path, self.events)
        self.query.trace_add('write', self.changed)
        self.bind('<Control-l>', self.focus_search)
        self.bind('<Control-k>', self.focus_search)
        self.bind('<Control-f>', self.focus_search)
        # Entry class bindings consume Ctrl+K/F before the toplevel bindtag.
        for shortcut in ('<Control-k>', '<Control-f>', '<Control-l>'):
            self.entry.bind(shortcut, self.focus_search)
        self.bind('<Escape>', self.escape)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_timer = self.after(30, self.poll)
        self.entry.focus_set()

    def button(self, parent, text, command, accent=False):
        return Button(parent, text, command, accent)

    def focus_search(self, _=None):
        self.entry.focus_set()
        self.entry.selection_range(0, 'end')
        return 'break'

    def escape(self, _=None):
        if self.query.get():
            self.query.set('')
        elif self.kind:
            self.set_kind('')
        self.entry.focus_set()
        return 'break'

    def _build(self):
        from .layout import build
        build(self)

    def layout_shell(self):
        width, height = max(920, self.winfo_width()), max(620, self.winfo_height())
        home = not self.query.get().strip() and not self.kind
        video = self.kind == 'video'
        self.compact = height < 700
        key = (width, height, home, self.kind)
        if getattr(self, '_shell_key', None) == key:
            return
        self._shell_key = key
        for button in self.examples.winfo_children():
            button.configure(min_width=0)
        hero_y = int(height*.14)
        self.top_bar.configure(height=hero_y+382 if home else 150)
        if home:
            self.logo.resize(86)
            self.logo.place(relx=.5, y=hero_y, anchor='n')
            self.hero_title.place(relx=.5, y=hero_y+104, anchor='n')
            self.hero_subtitle.place(relx=.5, y=hero_y+157, anchor='n')
            self.search_box.place(relx=.5, x=0, y=hero_y+208, width=min(780, width-144), height=74, anchor='n')
            self.home_hint.place(relx=.5, y=hero_y+305, anchor='n')
            self.tabs.place_forget()
            self.examples.place(relx=.5, y=hero_y+335, anchor='n')
            self.body.pack_forget()
            self.home.pack(fill='both', expand=True)
        else:
            for widget in (self.logo, self.hero_title, self.hero_subtitle, self.home_hint):
                widget.place_forget()
            self.search_box.place(relx=0, x=212, y=16, width=width-420, height=60, anchor='nw')
            self.examples.place_forget()
            self.tabs.place(relx=0, x=31, y=94, anchor='nw')
            self.home.pack_forget()
            self.body.pack(fill='both', expand=True, padx=32, pady=(0, 24))
        if getattr(self, '_pane_kind', None) != self.kind:
            self.pane_ratio = .43 if self.kind == 'image' else .46
            self._pane_kind = self.kind
        orient = 'vertical' if video else 'horizontal'
        if str(self.body.cget('orient')) != orient:
            for pane in self.body.panes():
                self.body.forget(pane)
            self.body.configure(orient=orient)
            if video:
                self.body.add(self.preview, minsize=300, stretch='always')
                self.body.add(self.results_panel, minsize=80, height=90)
            else:
                self.body.add(self.results_panel, minsize=285, width=int(width*self.pane_ratio))
                self.body.add(self.preview, minsize=320)
        self._arrange_preview()
        self.after_idle(self.move_tab_indicator)

    def move_tab_indicator(self):
        if self.closing:
            return
        button = self.tab_buttons[self.kind]
        destination = button.winfo_x()+(button.winfo_width()-24)/2
        self.tab_motion.to(destination, 180)

    def resize_layout(self, event):
        if event.widget is self:
            self.layout_shell()


    def resize_panes(self, event):
        self.body.arrange()
        if len(self.body.panes()) == 2:
            if str(self.body.cget('orient')) == 'vertical':
                self.body.sash_place(0, 0, max(300, event.height-100))
            else:
                self.body.sash_place(0, int(event.width*self.pane_ratio), 0)


    def remember_pane_ratio(self, _=None):
        if len(self.body.panes()) == 2 and str(self.body.cget('orient')) == 'horizontal' and self.body.winfo_width() > 1:
            self.pane_ratio = self.body.sash_coord(0)[0]/self.body.winfo_width()


    def set_kind(self, kind):
        if self.kind == kind:
            return
        self.kind = kind
        for key, button in self.tab_buttons.items():
            button.configure(bg=BG, fg=RED if key == kind else MUTED, font=(typography.family(), 11))
        self.changed()
        self.move_tab_indicator()


    def changed(self, *_):
        if _:
            # A newly typed explicit request replaces a conflicting old tab.
            requested = store.query_intent(self.query.get())[0]
            if not requested and Path(self.query.get().strip()).suffix:
                guessed = media.kind(self.query.get().strip())
                requested = guessed if guessed != 'other' else ''
            if requested and requested != self.kind:
                self.kind = requested
                for key, button in self.tab_buttons.items():
                    button.configure(fg=RED if key == self.kind else MUTED)
        self.generation += 1
        self.preview_cancel.set()
        self.preview_token += 1
        self.preview_key = None
        if self.preview_future:
            self.preview_future.cancel()
        self.channels = {'names': [], 'keywords': [], 'semantic': []}
        self.channel_more = dict.fromkeys(self.channels, False)
        self.pending_channels.clear()
        self.semantic_inflight = False
        self.semantic_stage, self.semantic_coverage = '', {}
        self.refreshing_results = self.index_dirty = False
        self.user_selection_generation = -1
        self.search_cancel.set()
        self.search_cancel = threading.Event()
        for future in self.search_jobs.values():
            future.cancel()
        self.commands.put({'action': 'cancel_search', 'id': self.generation})
        self.search_error = ''
        self.tree.empty_text = '正在查找…'
        self.visible, self.limit = 100, 500
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        # A different request/scope must never keep the previous result list
        # visible while asynchronous readers catch up.
        self.update_results([])
        if not self.query.get().strip() and not self.kind:
            self.preview_cancel.set()
            self.preview_token += 1
            self.preview_key = None
            self.selected = None
            self.rows = []
            self.result_count.configure(text='')
            self.layout_shell()
            return
        self.layout_shell()
        self.issue_names()
        if self.query.get().strip():
            self.timer = self.after(220, self.issue_semantic)

    def issue_names(self):
        gen, query, kind, limit = self.generation, self.query.get(), self.kind, self.limit+1
        db_path = self.db_path
        cancel = self.search_cancel
        self.pending_channels = {'names', 'keywords'}
        if query.strip():
            self.pending_channels.add('semantic')
        for channel, pool, search in (('names', self.search_pool, store.names),
                                      ('keywords', self.keyword_pool, store.keywords)):
            old = self.search_jobs.get(channel)
            if old:
                old.cancel()
            def task(search=search, channel=channel):
                if cancel.is_set():
                    return []
                db = store.reader(db_path)
                db.set_progress_handler(lambda: int(cancel.is_set()), 1000)
                try:
                    if channel == 'names':
                        for rows, complete in store.name_batches(db, query, kind, limit, cancel.is_set):
                            if cancel.is_set():
                                return []
                            if complete:
                                return rows
                            partial = concurrent.futures.Future()
                            partial.set_result(rows)
                            self.futures.put(('names_partial', gen, partial))
                        return []
                    return search(db, query, kind, limit)
                finally:
                    db.close()
            future = pool.submit(task)
            self.search_jobs[channel] = future
            future.add_done_callback(lambda f, c=channel, out=self.futures: out.put((c, gen, f)))

    def issue_semantic(self):
        if self.timer:
            self.after_cancel(self.timer)
        self.timer = None
        if not self.query.get().strip():
            return
        self.pending_channels.add('semantic')
        if self.semantic_inflight:
            # Model-ready events may arrive during the same request. Refine
            # after its reply instead of queuing indistinguishable responses.
            self.index_dirty = True
            return
        self.semantic_inflight = True
        self.commands.put({'action': 'search', 'id': self.generation, 'query': self.query.get(),
                           'kind': self.kind, 'limit': self.limit+1, 'channels': True})

    def set_channel(self, channel, rows, complete=True):
        """A sentinel file distinguishes a full page from actual exhaustion.

        Video time peaks share one slot; keep all peaks of included files.
        Every payload replaces this generation's channel, never a saved list.
        """
        accepted, seen, more = [], set(), False
        for row in rows:
            fid = row['file_id']
            if fid not in seen:
                if len(seen) >= self.limit:
                    more = True
                    continue
                seen.add(fid)
            accepted.append(row)
        self.channels[channel] = accepted
        if complete:
            self.channel_more[channel] = more

    def waiting_for_refresh(self):
        waiting = self.pending_channels.intersection(('names', 'keywords'))
        return bool(waiting or self.semantic_inflight)

    def refresh_results(self):
        """Refresh one request from current data while retaining its UI anchor."""
        interacted = self.user_selection_generation == self.generation
        self.generation += 1
        self.result_generation = self.generation
        if interacted:
            self.user_selection_generation = self.generation
        self.last_semantic_generation = -1
        self.search_cancel.set()
        self.search_cancel = threading.Event()
        self.semantic_inflight = False
        self.commands.put({'action': 'cancel_search', 'id': self.generation})
        self.channels = {'names': [], 'keywords': [], 'semantic': []}
        self.channel_more = dict.fromkeys(self.channels, False)
        self.semantic_stage, self.semantic_coverage = '', {}
        self.refreshing_results = True
        self.search_error = ''
        self.index_dirty = False
        self.last_index_refresh = time.monotonic()
        self.issue_names()
        self.issue_semantic()
        self.refresh_result_label()

    def refresh_if_dirty(self):
        # Coalesce changes, but do not starve an in-flight semantic request by
        # cancelling it on every vector commit. User selection is not a lock.
        if (self.index_dirty and (self.query.get().strip() or self.kind) and
                self.timer is None and not self.waiting_for_refresh() and
                time.monotonic()-self.last_index_refresh >= 1.):
            self.refresh_results()

    def content_incomplete(self):
        stats = self.last_stats
        states = stats.get('content_states', {})
        return bool(stats.get('scanning') or stats.get('blocked') or
                    states.get('pending', 0) or states.get('error', 0) or
                    stats.get('covered_units', 0) < stats.get('units', 0))

    def refresh_result_label(self):
        parts = [f'{len(self.rows):,} 个结果'] if self.rows else []
        if self.refreshing_results:
            parts.append('正在更新结果')
        elif self.rows and 'semantic' in self.pending_channels and not self.channels['semantic']:
            parts.append('文字匹配 · 语义准备中' if self.model_state in ('loading', 'preparing')
                         else '文字匹配 · 正在检索语义')
        elif self.semantic_stage == 'quick':
            parts.append('快速语义（文字）')
        if self.rows and (any(self.channel_more.values()) or len(self.rows) > self.visible):
            count = str(len(self.rows))+('+' if any(self.channel_more.values()) else '')
            parts.append(f'显示 {min(self.visible, len(self.rows))} / {count} 项')
        coverage = self.semantic_coverage
        if coverage and coverage.get('covered_files', 0) < coverage.get('eligible_files', 0):
            parts.append(f"名称语义 {coverage.get('covered_files', 0):,} / {coverage['eligible_files']:,} 个文件")
        elif self.rows and self.content_incomplete():
            parts.append('内容索引尚未完成')
        if parts:
            self.result_count.configure(text=' · '.join(parts))
            self.result_count.pack(fill='x', padx=14, pady=(0, 12), before=self.tree)
        else:
            self.result_count.pack_forget()

    def merge_channels(self):
        if self.refreshing_results and self.waiting_for_refresh():
            self.refresh_result_label()
            return
        refreshed = self.refreshing_results
        self.refreshing_results = False
        rows = store.fuse(self.channels['names'], self.channels['keywords'],
                         self.channels['semantic'], query=self.query.get())
        self.tree.empty_text = (('正在准备语义搜索…' if self.model_state in ('loading', 'preparing') and
                                'semantic' in self.pending_channels else '正在查找…') if self.pending_channels else
                                self.search_error or self.no_results_text())
        if not refreshed and not rows and self.pending_channels and self.result_generation == self.generation and self.rows:
            return
        self.update_results(rows)

    def no_results_text(self):
        unfinished = (self.scope_pending or self.content_incomplete() or
                      self.model_state in ('preparing', 'loading', 'indexing'))
        return '索引未完成，暂未找到' if unfinished else '没有匹配的文件'

    def update_results(self, rows):
        interacted = self.user_selection_generation == self.generation
        selected = self.selected['file_id'] if self.selected and interacted else None
        self.rows = rows
        # A still-matching selection may move onto a later visible page after
        # reranking. Expand the page rather than displaying a stale extra row.
        if selected is not None:
            position = next((i for i, row in enumerate(rows) if row['file_id'] == selected), -1)
            if position >= self.visible:
                self.visible = (position//100+1)*100
        self.refresh_result_label()
        if self.kind == 'video':
            listed = str(self.results_panel) in tuple(map(str, self.body.panes()))
            if len(rows) <= 1 and listed:
                self.body.forget(self.results_panel)
            elif len(rows) > 1 and not listed:
                self.body.add(self.results_panel, minsize=80, height=90)
        self.tree.set_rows(rows[:self.visible], reset=not interacted or self.result_generation != self.generation)
        self.result_generation = self.generation
        self.request_thumbnails()
        if selected is not None and self.tree.exists(str(selected)):
            self.tree.selection_set(str(selected))
        elif rows:
            self.tree.selection_set(str(rows[0]['file_id']))
        else:
            self.selected = None
            self.selection_origin = None
            self.preview_key = None
            self._preview_arrangement = None
            self.preview_cancel.set()
            self.preview_token += 1
            self.title_label.configure(text='')
            self.path_label.configure(text='')
            self.image_label.configure(image='', text='')
            self.text.configure(state='normal')
            self.text.delete('1.0', 'end')
            self.text.configure(state='disabled')
            self.timeline.place_forget()
            self.hit_list.place_forget()
            self.keyframes.place_forget()
            self.preview_meta.configure(text='')
            self.fit_button.pack_forget()
            self.open_button.configure(state='disabled')
        if len(rows) > self.visible or any(self.channel_more.values()):
            self.more.pack(pady=8)
        else:
            self.more.pack_forget()
        self.refresh_footer()

    @staticmethod
    def file_version(path):
        stat = Path(path).stat()
        return stat.st_size, stat.st_mtime_ns

    def request_thumbnails(self):
        if self.closing:
            return
        wanted = {r['path'] for r in self.tree.visible_rows() if r['kind'] == 'image'}
        for path, job in list(self.thumbnail_jobs.items()):
            if path not in wanted:
                job.cancel()
        for path in wanted:
            try:
                version = self.file_version(path)
            except OSError:
                continue
            if self.thumbnail_versions.get(path) == version and path in self.tree.photos:
                self.tree.photos.move_to_end(path)
                continue
            if self.thumbnail_failures.get(path) == version:
                continue
            if path in self.thumbnail_pending:
                continue
            self.thumbnail_pending.add(path)
            future = self.thumbnail_pool.submit(media.preview_image, path, {}, (144, 108))
            self.thumbnail_jobs[path] = future
            future.add_done_callback(lambda f, token=(path, version), out=self.futures: out.put(('thumbnail', token, f)))

    def refresh_footer(self):
        self.footer.configure(text=self.search_error or self.model_status)
        error = bool(self.search_error) or self.model_state == 'error'
        if error:
            self.foot_bar.pack(side='bottom', fill='x', padx=28, pady=(0, 8))
        else:
            self.foot_bar.pack_forget()
        self.status_dot.delete('all')
        if self.model_state in ('indexing', 'loading', 'preparing', 'missing', 'error'):
            self.status_dot.place(relx=1, x=-68, y=36, anchor='ne')
            self.status_dot.create_oval(3, 3, 8, 8, fill=RED if error else '#b9a17d', outline='')
        else:
            self.status_dot.place_forget()


    def set_model_status(self, state, text):
        self.model_state, self.model_status = state, text
        self.model_status_var.set(text)
        self.refresh_footer()

    def load_more(self):
        self.mark_selection_interaction()
        self.visible += 100
        if self.visible > self.limit and any(self.channel_more.values()):
            self.limit += 500
            self.refresh_results()
        self.update_results(self.rows)

    def mark_selection_interaction(self):
        self.user_selection_generation = self.generation

    def focus_results(self, _=None):
        self.mark_selection_interaction()
        children = self.tree.get_children()
        if children:
            self.tree.focus_set()
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
        return 'break'

    def select(self, _=None):
        selected = self.tree.selection()
        if not selected:
            return
        row = next((r for r in self.rows if str(r['file_id']) == selected[0]), None)
        if not row:
            return
        origin = (row['file_id'], row.get('unit_id'), row['path'], row.get('size'), row.get('mtime'),
                  json.dumps(row['location'], sort_keys=True), row.get('text'))
        if self.selected and self.selection_origin == origin:
            self.selected.update({key: value for key, value in row.items() if key != 'location'})
        else:
            self.selected = dict(row, location=dict(row['location']))
        self.selection_origin = origin
        self.show_preview()

    def preview_resized(self, _=None):
        self._arrange_preview()
        if self.preview_resize_timer:
            self.after_cancel(self.preview_resize_timer)
        self.preview_resize_timer = self.after(160, self.refresh_preview_size)

    def _arrange_preview(self):
        if not hasattr(self, 'preview_info'):
            return
        w, h = self.preview.winfo_width(), self.preview.winfo_height()
        row = self.selected or {}
        mode = row.get('kind', 'document')
        visual = mode in ('image', 'video') or str(row.get('path', '')).lower().endswith('.pdf')
        hit_count = len(row.get('hits', []))
        key = (w, h, mode, visual, hit_count)
        if w < 80 or h < 80 or self._preview_arrangement == key:
            return
        self._preview_arrangement = key
        self.preview.mode = mode
        from types import SimpleNamespace
        self.preview.draw(SimpleNamespace(width=w, height=h))
        info_bg = BG if mode == 'image' else WHITE
        for widget in (self.preview_info, self.title_label, self.path_label, self.preview_meta, self.controls):
            widget.configure(bg=info_bg)
        for button in (self.open_button, self.reveal_button, self.reveal_image_button, self.fit_button):
            tk.Canvas.configure(button, bg=info_bg)
        self.open_button.accent = mode == 'image'
        self.open_button.configure(bg=RED if mode == 'image' else WHITE, fg=WHITE if mode == 'image' else INK,
                                   min_width=155 if mode == 'image' else 0)
        self.title_label.configure(font=(typography.family(), 15 if mode == 'video' else 14 if mode == 'image' else 12))
        self.open_button.pack_forget()
        self.reveal_button.pack_forget()
        self.reveal_image_button.pack_forget()
        if mode == 'image':
            self.open_button.pack(side='top', fill='x')
            self.reveal_image_button.configure(min_width=155)
            self.reveal_image_button.pack(side='top', fill='x', pady=(6, 0))
        else:
            self.open_button.pack(side='left', padx=(0, 4))
            self.reveal_button.pack(side='left')
        for widget in (self.preview_info, self.title_label, self.path_label, self.preview_meta,
                       self.controls, self.image_label, self.text, self.keyframes, self.timeline, self.hit_list):
            widget.place_forget()
        view = self.image_label if visual else self.text
        if mode == 'video':
            wide = w > 700
            iw = int(w*.51)-24 if wide else w-32
            ih = h-126 if wide else max(90, h-205)
            view.place(x=16, y=16, width=iw, height=ih)
            if wide:
                self.preview_info.place(x=int(w*.53), y=28, width=int(w*.47)-24, height=220)
            else:
                self.preview_info.place(x=16, y=ih+24, width=w-32, height=75)
            self.title_label.place(x=0, y=0, relwidth=1, height=26)
            self.path_label.place(x=0, y=102 if wide else 29, relwidth=1, height=22)
            self.preview_meta.place(x=0, y=48 if wide else 52, width=230, height=26)
            self.controls.place(x=0, y=149) if wide else self.controls.place(relx=1, y=42, anchor='ne')
            if hit_count > 1:
                self.hit_list.place(x=0, y=195, width=min(230, int(w*.38)))
            self.keyframes.place(x=16, y=h-107, width=w-32, height=74)
            self.timeline.place(x=16, y=h-32, width=w-32, height=28)
        else:
            bottom = mode == 'image'
            self.preview_info.place(x=2 if bottom else 16, y=h-104 if bottom else 14, width=w-20 if bottom else w-32, height=92 if bottom else 66)
            self.title_label.place(x=0, y=0, relwidth=1, width=-175 if bottom else -145, height=27)
            self.path_label.place(x=0, y=31, relwidth=1, width=-175 if bottom else 0, height=22)
            self.controls.place(relx=1, x=0, y=0, anchor='ne')
            view.place(x=16, y=16 if bottom else 86, width=w-32, height=max(60, h-152 if bottom else h-102))
            if bottom:
                self.preview_meta.place(x=0, y=62, width=w-180, height=24)


    def refresh_preview_size(self):
        self.preview_resize_timer = None
        if self.selected and not self.closing:
            self.show_preview()

    def toggle_preview_fit(self):
        self.fit_width = not self.fit_width
        self.show_preview()

    def apply_preview(self, value):
        from PIL import ImageTk
        from .widgets import Tooltip
        from . import appearance
        image, thumbs = value
        if self.selected and self.selected['kind'] == 'image':
            size = image.info.get('original_size', image.size)
            self.preview_meta.configure(text=f"{Path(self.selected['path']).suffix[1:].upper()}  ·  {size[0]} × {size[1]}", fg=MUTED)
        if self.selected and self.selected['kind'] in ('image', 'video'):
            image = appearance.rounded_bitmap(image, 10)
        self.photo = ImageTk.PhotoImage(image, master=self)
        self.image_label.configure(image=self.photo, text='')
        if thumbs:
            for child in self.keyframes.winfo_children():
                child.destroy()
            self.keyframe_photos = []
            for second, thumb in thumbs:
                loc = self.selected['location'] if self.selected else {}
                step = loc.get('duration', 1)/max(1, len(thumbs))
                active = second < loc.get('end', 0) and second+step > loc.get('start', 0)
                photo = ImageTk.PhotoImage(appearance.rounded_bitmap(thumb, 5), master=self)
                self.keyframe_photos.append(photo)
                button = tk.Button(self.keyframes, image=photo,
                    relief='flat', bd=0, padx=0, pady=0, bg=WHITE, fg=MUTED,
                    highlightthickness=2, highlightbackground=RED if active else WHITE,
                    activebackground=TINT, cursor='hand2', font=(typography.family(), 9),
                    command=lambda t=second: self.seek_second(t))
                button.pack(side='left', expand=True)
                button.second = second
                Tooltip(button, media.timestamp(second))

    def show_preview(self):
        row = self.selected
        if not row:
            return
        loc = dict(row['location'])
        if row['kind'] == 'video' and 'duration' not in loc:
            unit = self.db.execute('SELECT location FROM units WHERE file_id=? ORDER BY ordinal LIMIT 1', (row['file_id'],)).fetchone()
            if unit:
                loc = json.loads(unit[0])
                row['hits'] = []
        row['location'] = loc
        self.image_label.play_command = self.open_selected if row['kind'] == 'video' else None
        try:
            version = self.file_version(row['path'])
        except OSError:
            version = None
        self._arrange_preview()
        bounds = (max(240, self.preview.winfo_width()-44), max(170, self.preview.winfo_height()-210))
        if self.image_label.winfo_ismapped() and self.image_label.winfo_width() > 100:
            bounds = (max(80, self.image_label.winfo_width()-16), max(32, self.image_label.winfo_height()-14))
        is_pdf = Path(row['path']).suffix.lower() == '.pdf'
        if is_pdf:
            self.fit_button.pack(side='left')
            if self.fit_width:
                bounds = (max(220, bounds[0]-16), 4000)
        else:
            self.fit_button.pack_forget()
        if row['kind'] == 'video' and not self.image_label.winfo_ismapped():
            bounds = (bounds[0], max(130, bounds[1]-120))
        render_key = (row['path'], version, json.dumps(loc, sort_keys=True), bounds)
        key = (render_key, row.get('text') if not is_pdf else '',
               json.dumps(row.get('hits', []), sort_keys=True), row.get('error'))
        self.preview_meta.configure(text=self.preview_caption(row), fg=RED if row['kind'] == 'video' else MUTED)
        if key == self.preview_key:
            return
        self.preview_key = key
        self.preview_cancel.set()
        self.preview_cancel = cancel = threading.Event()
        if self.preview_future:
            self.preview_future.cancel()
        self.preview_token += 1
        token = self.preview_token
        self.open_button.configure(state='normal')
        self.title_label.configure(text=row['name'])
        self.path_label.configure(text=str(Path(row['path']).parent).replace(str(Path.home()), '~', 1))
        self.image_label.configure(image='', text='')
        self.photo = None
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        location = ('第 '+str(loc['page'])+' 页') if 'page' in loc else ''
        if 'sheet' in loc:
            location = f"{loc['sheet']} · 第 {loc['row']} 行"
        if 'chapter' in loc:
            location = f"第 {loc['chapter']} 章"
        if row['kind'] == 'code' and 'line' in loc:
            location = f"第 {loc['line']} 行起"
        if row['kind'] == 'video':
            location = media.timestamp(loc.get('start', 0))+' / '+media.timestamp(loc.get('duration', 0))
        self.text.insert('end', location+'\n\n'+(row.get('text') or row.get('error') or ''))
        self.text.configure(state='disabled')
        for child in self.keyframes.winfo_children():
            child.destroy()
        self.keyframe_photos = []
        self.open_button.configure(text='播放' if row['kind'] == 'video' else '打开')
        if row['kind'] == 'video':
            if row.get('hits'):
                self.hit_list.configure(values=[media.timestamp(h['start'])+' – '+media.timestamp(h['end']) for h in row['hits']])
                active = next((i for i, h in enumerate(row['hits']) if h['start'] <= loc.get('start', 0) < h['end']), 0)
                self.hit_list.current(active)
            self.draw_timeline()
        if row['kind'] in ('image', 'video') or Path(row['path']).suffix.lower() == '.pdf':
            self.image_label.configure(text='…', fg=MUTED)
            path, location, typ = row['path'], dict(loc), row['kind']
            thumb_width = max(60, (self.preview.winfo_width()-50)//8)
            cached = self.asset_cache.get(render_key)
            if cached is not None:
                self.apply_preview(cached)
                return
            cache, output, frame_pool = self.asset_cache, self.futures, self.frame_pool
            def make_preview():
                def check():
                    if cancel.is_set():
                        raise concurrent.futures.CancelledError()
                check()
                image = media.preview_image(path, location, bounds)
                thumbs = []
                if typ == 'video':
                    check()
                    # The selected frame is visible while the filmstrip decodes.
                    partial = concurrent.futures.Future()
                    partial.set_result((image, []))
                    output.put(('preview', token, partial))
                    start = 0.
                    end = max(.01, location.get('duration', location.get('end', 8)))
                    def decode(second):
                        check()
                        frame_key = ('frame', path, version, second, thumb_width)
                        thumb = cache.get(frame_key)
                        if thumb is None:
                            from PIL import ImageOps
                            thumb = ImageOps.fit(media.frame(path, second, thumb_width), (thumb_width, 70))
                            cache.put(frame_key, thumb, thumb.width*thumb.height*3)
                        return second, thumb
                    jobs = [frame_pool.submit(decode, start+(end-start)*i/8) for i in range(8)]
                    try:
                        for job in jobs:
                            check()
                            thumbs.append(job.result())
                    finally:
                        for job in jobs:
                            job.cancel()
                check()
                value = image, thumbs
                if App.file_version(path) == version:
                    size = image.width*image.height*3 + sum(t.width*t.height*3 for _, t in thumbs)
                    cache.put(render_key, value, size)
                return value
            future = self.preview_pool.submit(make_preview)
            self.preview_future = future
            future.add_done_callback(lambda f, out=self.futures: out.put(('preview', token, f)))

    @staticmethod
    def preview_caption(row):
        loc = row['location']
        if 'page' in loc:
            position = f"第 {loc['page']} 页"
        elif 'sheet' in loc:
            position = f"{loc['sheet']} · 第 {loc['row']} 行"
        elif 'chapter' in loc:
            position = f"第 {loc['chapter']} 章"
        elif 'duration' in loc:
            position = media.timestamp(loc.get('start', 0))+' / '+media.timestamp(loc['duration'])
        elif 'line' in loc:
            position = f"第 {loc['line']} 行起"
        else:
            position = ''
        return position

    def draw_timeline(self):
        self.timeline.delete('all')
        if not self.selected or self.selected['kind'] != 'video':
            return
        loc = self.selected['location']
        duration = loc.get('duration', 1)
        width = max(self.timeline.winfo_width()-16, 100)
        self.timeline.create_rectangle(8, 8, width+8, 11, fill='#e6e3df', outline='')
        for hit in self.selected.get('hits', []):
            self.timeline.create_rectangle(8+width*hit['start']/duration, 8, 8+width*hit['end']/duration, 11, fill='#dda09a', outline='')
        x = 8+width*loc.get('start', 0)/duration
        self.timeline.create_oval(x-4, 5, x+4, 13, fill=RED, outline='')

    def seek(self, event):
        self.mark_selection_interaction()
        if not self.selected:
            return
        loc = self.selected['location']
        duration = loc.get('duration', 1)
        loc['start'] = max(0, min(duration-.05, (event.x-8)/max(100, self.timeline.winfo_width()-16)*duration))
        loc['end'] = min(duration, loc['start']+8)
        self.show_preview()

    def choose_hit(self, _=None):
        self.mark_selection_interaction()
        i = self.hit_list.current()
        if self.selected and i >= 0:
            self.selected['location'] = dict(self.selected['hits'][i])
            self.show_preview()
            self.hit_list.current(i)

    def seek_second(self, second):
        self.mark_selection_interaction()
        if self.selected:
            self.selected['location']['start'] = second
            self.selected['location']['end'] = min(self.selected['location'].get('duration', second+8), second+8)
            self.show_preview()

    def open_selected(self, _=None):
        if not self.selected:
            self.focus_results()
            self.select()
        if not self.selected:
            return 'break'
        try:
            from .locations import open_hit
            note = open_hit(self.selected)
            if note:
                self.preview_meta.configure(text=note)
        except Exception as exc:
            messagebox.showerror('无法打开', str(exc), parent=self)
        return 'break'

    def reveal(self):
        if not self.selected:
            return
        path = self.selected['path']
        if os.name == 'nt':
            subprocess.Popen(['explorer', '/select,', path])
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(Path(path).parent)])

    def settings(self):
        from .settings_ui import show
        show(self)

    def poll(self):
        if self.closing:
            return
        if self.poll_timer:
            self.after_cancel(self.poll_timer)
            self.poll_timer = None
        redraw = False
        deadline = time.monotonic()+.008
        try:
            while time.monotonic() < deadline:
                typ, token, future = self.futures.get_nowait()
                if typ == 'thumbnail':
                    path, version = token
                    self.thumbnail_pending.discard(path)
                    self.thumbnail_jobs.pop(path, None)
                if future.cancelled():
                    continue
                if typ in ('names', 'names_partial', 'keywords') and token != self.generation:
                    continue
                if typ == 'preview' and token != self.preview_token:
                    continue
                try:
                    value = future.result()
                    if typ in ('names', 'names_partial', 'keywords') and token == self.generation:
                        self.tree.empty_text = ('正在查找…' if self.query.get().strip() else '暂无文件')
                        channel = 'names' if typ == 'names_partial' else typ
                        if typ != 'names_partial':
                            self.pending_channels.discard(channel)
                        self.set_channel(channel, value, complete=typ != 'names_partial')
                        if channel == 'names':
                            self.commands.put({'action': 'prioritize', 'id': token, 'kind': self.kind,
                                               'files': [r['file_id'] for r in value[:64]]})
                        if self.last_semantic_generation == token:
                            self.tree.empty_text = self.search_error or self.no_results_text()
                        self.merge_channels()
                    elif typ == 'thumbnail':
                        from PIL import ImageTk
                        if self.file_version(path) != version:
                            redraw = True
                            continue
                        self.thumbnail_failures.pop(path, None)
                        from .appearance import rounded_bitmap
                        self.tree.photos[path] = ImageTk.PhotoImage(rounded_bitmap(value, 7), master=self)
                        self.thumbnail_versions[path] = version
                        self.tree.photos.move_to_end(path)
                        while len(self.tree.photos) > 96:
                            expired, _ = self.tree.photos.popitem(last=False)
                            self.thumbnail_versions.pop(expired, None)
                        redraw = True
                    elif typ == 'preview' and token == self.preview_token:
                        self.apply_preview(value)
                except concurrent.futures.CancelledError:
                    pass
                except Exception as exc:
                    self.logger.exception('%s failed token=%s', typ, token)
                    self.warning = str(exc)
                    if typ == 'thumbnail':
                        self.thumbnail_failures[path] = version
                        if len(self.thumbnail_failures) > 256:
                            self.thumbnail_failures.pop(next(iter(self.thumbnail_failures)))
                    elif typ == 'preview' and token == self.preview_token:
                        self.preview_key = None
                        self.image_label.configure(image='', text='预览失败，可尝试打开原文件\n'+str(exc)[:120])
                    elif typ in ('names', 'names_partial', 'keywords') and token == self.generation:
                        self.pending_channels.discard('names' if typ == 'names_partial' else typ)
                        self.search_error = '搜索失败，详情见 ui.log'
                        self.refresh_footer()
        except queue.Empty:
            pass
        if redraw:
            self.tree.draw()
        deadline = time.monotonic()+.008
        try:
            while time.monotonic() < deadline:
                event = self.events.get_nowait()
                typ = event['type']
                if typ == 'results' and event['id'] == self.generation:
                    if (self.semantic_stage == 'full' and event.get('semantic_stage') == 'quick'
                            and not event.get('full_checked')):
                        continue
                    pending = event.get('semantic_pending', False)
                    if not event.get('partial'):
                        self.semantic_inflight = False
                    if not event.get('partial') and not pending:
                        self.last_semantic_generation = self.generation
                        self.pending_channels.discard('semantic')
                    self.search_error = ('语义查询失败：'+event['error'][:120]) if event.get('error') else ''
                    self.tree.empty_text = self.search_error or self.no_results_text()
                    if self.search_error:
                        self.logger.error(self.search_error)
                    if event.get('channels'):
                        if not pending:
                            self.set_channel('semantic', event['semantic_rows'], complete=not event.get('partial'))
                            self.semantic_stage = event.get('semantic_stage', 'full')
                            self.semantic_coverage = event.get('semantic_coverage', {})
                        self.merge_channels()
                    else:
                        self.update_results(event['rows'])
                elif typ == 'model':
                    self.set_model_status(event.get('state', 'ready' if event['ready'] else 'error'), event['text'])
                    if event.get('state') in ('indexing', 'ready') and self.query.get().strip():
                        self.issue_semantic()
                elif typ == 'quick_model':
                    if event.get('state') == 'ready' or event.get('ready'):
                        if self.model_state == 'missing':
                            self.set_model_status('partial', '快速语义已就绪；完整语义模型未配置')
                        if self.query.get().strip():
                            self.issue_semantic()
                    elif event.get('state') == 'error' or event.get('ready') is False:
                        self.warning = event.get('error') or event.get('text') or '快速语义模型加载失败'
                        self.logger.warning(self.warning)
                elif typ == 'indexed':
                    self.last_stats = event['stats']
                    self.scope_pending = False
                    self.index_dirty = True
                    self.refresh_result_label()
                elif typ == 'index_changed':
                    self.index_dirty = True
                elif typ == 'configured':
                    label = getattr(self, 'settings_apply_status', None)
                    if label is not None and label.winfo_exists():
                        label.configure(text='已应用')
                    pending_window = getattr(self, 'settings_close_after_apply', None)
                    if pending_window is not None and pending_window.winfo_exists():
                        pending_window.destroy()
                    self.settings_close_after_apply = None
                    self.last_stats = {}
                    self.scope_pending = True
                    if self.query.get().strip() or self.kind:
                        self.changed()
                elif typ == 'status':
                    self.last_status = event['text']
                    self.refresh_footer()
                elif typ in ('fatal', 'warning'):
                    self.logger.error('%s: %s', typ, event['text'])
                    self.warning = event['text']
                    if typ == 'fatal':
                        self.set_model_status('error', event['text'])
                    else:
                        self.footer.configure(text='索引提示：'+event['text'][:100])
        except queue.Empty:
            pass
        self.refresh_if_dirty()
        self.poll_timer = self.after(16 if not self.futures.empty() or not self.events.empty() else 30, self.poll)

    def report_callback_exception(self, exc_type, exc_value, exc_traceback):
        self.logger.error('UI callback failed', exc_info=(exc_type, exc_value, exc_traceback))
        self.warning = str(exc_value)
        self.footer.configure(text='界面操作失败，详情已记录到 ui.log：'+str(exc_value)[:80])
        self.foot_bar.pack(side='bottom', fill='x', padx=28, pady=(0, 8))

    def close(self):
        self.closing = True
        self.search_cancel.set()
        self.preview_cancel.set()
        for timer in (self.timer, self.poll_timer, self.preview_resize_timer):
            if timer:
                self.after_cancel(timer)
        self.commands.close()
        self.search_pool.shutdown(wait=False, cancel_futures=True)
        self.keyword_pool.shutdown(wait=False, cancel_futures=True)
        self.preview_pool.shutdown(wait=False, cancel_futures=True)
        self.frame_pool.shutdown(wait=False, cancel_futures=True)
        self.thumbnail_pool.shutdown(wait=False, cancel_futures=True)
        self.logger.removeHandler(self.log_handler)
        self.log_handler.close()
        self.db.close()
        self.destroy()
