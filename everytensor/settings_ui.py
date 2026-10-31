"""Compact whole-computer scope, coverage and storage controls."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path
from . import catalog, store, typography
from .layout import Panel
from .widgets import BG, WHITE, INK, MUTED, RED, LINE


def show(app):
    existing = getattr(app, 'settings_window', None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        return
    window = app.settings_window = tk.Toplevel(app)
    window.title('设置 · EveryTensor')
    window.geometry('790x700')
    window.minsize(740, 700)
    window.configure(bg=BG)
    window.transient(app)
    window.bind('<Escape>', lambda _: window.destroy())
    scope = tk.StringVar(value=store.setting(app.db, 'scope', 'folders'))
    dims = tk.StringVar(value=str(store.setting(app.db, 'dimensions', 256)))
    budget = tk.StringVar(value=str(store.setting(app.db, 'budget_gib', 8)))
    folders = list(store.setting(app.db, 'roots', []))
    excluded = list(store.setting(app.db, 'exclusions', []))
    tk.Label(window, text='搜索与索引', font=(typography.family(), 19, 'bold'), bg=BG, fg=INK).pack(anchor='w', padx=30, pady=(16, 12))

    def card():
        panel = Panel(window)
        panel.pack(fill='x', padx=28, pady=(0, 10))
        body = tk.Frame(panel, bg=WHITE)
        body.pack(fill='both', expand=True, padx=18, pady=12)
        return body

    area = card()
    tk.Label(area, text='搜索范围', bg=WHITE, fg=INK, font=(typography.family(), 12, 'bold')).pack(anchor='w')
    choices = tk.Frame(area, bg=WHITE)
    choices.pack(fill='x', pady=(8, 4))
    buttons = {}
    def select(value):
        scope.set(value)
        for key, button in buttons.items():
            button.configure(fg=RED if key == value else MUTED)
        drives = '、'.join(catalog.local_drives())
        description.configure(text=f'搜索 {drives}；下方文件夹为额外范围' if value == 'computer' else '仅搜索下方文件夹')
    for key, label in [('computer', '这台电脑'), ('folders', '指定文件夹')]:
        button = app.button(choices, label, lambda v=key: select(v))
        button.pack(side='left', padx=(0, 10))
        buttons[key] = button
    description = tk.Label(area, bg=WHITE, fg=MUTED, anchor='w', font=(typography.family(), 10))
    description.pack(fill='x', pady=(0, 5))
    select(scope.get())

    lists = tk.Frame(area, bg=WHITE)
    lists.pack(fill='x')
    lists.columnconfigure((0, 1), weight=1, uniform='paths')
    def paths(column, title, values):
        pane = tk.Frame(lists, bg=WHITE)
        pane.grid(row=0, column=column, sticky='nsew', padx=(0, 12) if column == 0 else (12, 0))
        tk.Label(pane, text=title, bg=WHITE, fg=MUTED, font=(typography.family(), 10)).pack(anchor='w')
        listing = tk.Listbox(pane, height=2, relief='flat', bg='#f5f5f7', fg=INK,
                             selectbackground='#efe0e5', selectforeground=INK, exportselection=False,
                             highlightthickness=0, activestyle='none', font=(typography.family(), 10))
        listing.pack(fill='x', pady=4)
        for path in values:
            listing.insert('end', path)
        controls = tk.Frame(pane, bg=WHITE)
        controls.pack(fill='x')
        def add():
            path = filedialog.askdirectory(parent=window, title=title)
            if path:
                path = catalog.canonical(path)
                if path not in values:
                    values.append(path)
                    listing.insert('end', path)
        def remove():
            selection = listing.curselection()
            if selection:
                values.pop(selection[0])
                listing.delete(selection[0])
        app.button(controls, '添加', add).pack(side='left')
        app.button(controls, '移除', remove).pack(side='left', padx=4)
    paths(0, '文件夹', folders)
    paths(1, '排除目录', excluded)

    options = card()
    row = tk.Frame(options, bg=WHITE)
    row.pack(fill='x')
    tk.Label(row, text='向量维度', bg=WHITE, fg=INK).pack(side='left')
    ttk.Combobox(row, textvariable=dims, values=('256', '512', '768'), state='readonly',
                 width=7, style='Hit.TCombobox').pack(side='left', padx=(14, 30))
    tk.Label(row, text='索引预算', bg=WHITE, fg=INK).pack(side='left')
    ttk.Combobox(row, textvariable=budget, values=('2', '4', '8', '16', '32', '64'),
                 width=7, style='Hit.TCombobox').pack(side='left', padx=(14, 6))
    tk.Label(row, text='GiB', bg=WHITE, fg=MUTED).pack(side='left')
    note = tk.Label(options, text='256 维推荐。尽量分析全部支持的内容，达到预算后暂停扩建。',
                    bg=WHITE, fg=MUTED, font=(typography.family(), 10), anchor='w')
    note.pack(fill='x', pady=(9, 0))
    def dimensions_note(*_):
        old = store.setting(app.db, 'dimensions', 256)
        note.configure(text=('提高维度会在后台补算，期间保留已有搜索结果。' if int(dims.get()) > old else
                             '降低维度复用已有向量；释放的数据库页优先供后续索引使用。' if int(dims.get()) < old else
                             '256 维推荐。尽量分析全部支持的内容，达到预算后暂停扩建。'))
    dims.trace_add('write', dimensions_note)

    state = card()
    heading = tk.Label(state, text='正在准备索引', bg=WHITE, fg=INK, anchor='w')
    heading.pack(fill='x')
    numbers = tk.Label(state, bg=WHITE, fg=MUTED, anchor='w', font=(typography.family(), 10))
    numbers.pack(fill='x', pady=(7, 8))
    style = ttk.Style(window)
    style.configure('Index.Horizontal.TProgressbar', background=RED, troughcolor='#f0edf0', borderwidth=0, thickness=4)
    bar = tk.Canvas(state, height=4, bg='#eeeaf0', highlightthickness=0)
    fill = bar.create_rectangle(0, 0, 0, 4, fill=RED, outline='')
    bar.pack(fill='x')
    usage = tk.Label(state, bg=WHITE, fg=MUTED, anchor='w', font=(typography.family(), 10))
    usage.pack(fill='x', pady=(8, 0))
    tk.Label(state, textvariable=app.model_status_var, bg=WHITE, fg=MUTED,
             font=(typography.family(), 10), anchor='w', wraplength=660, justify='left').pack(fill='x', pady=(6, 0))
    error = tk.Label(window, text='', bg=BG, fg=RED, anchor='w', wraplength=720,
                     font=(typography.family(), 10), justify='left')
    error.pack(fill='x', padx=30)
    bottom = tk.Frame(window, bg=BG)
    bottom.pack(side='bottom', fill='x', padx=28, pady=14, before=window.winfo_children()[0])
    def pause():
        app.paused = not app.paused
        app.commands.put({'action': 'pause', 'value': app.paused})
        pause_button.configure(text='继续' if app.paused else '暂停')
    pause_button = app.button(bottom, '继续' if app.paused else '暂停', pause)
    pause_button.pack(side='left')
    app.button(bottom, '检查更新', lambda: app.commands.put({'action': 'scan'})).pack(side='left', padx=6)
    def choose_model():
        path = filedialog.askdirectory(parent=window, title='本地 EmbeddingGemma 2')
        if path:
            app.commands.put({'action': 'model', 'path': path})
            app.set_model_status('preparing', '正在切换本地模型…')
    app.button(bottom, '模型', choose_model).pack(side='left')
    def details():
        dialog = tk.Toplevel(window)
        dialog.title('索引详情')
        dialog.geometry('650x400')
        report = tk.Text(dialog, wrap='word', bg=WHITE, fg=INK, relief='flat', padx=16, pady=16)
        report.pack(fill='both', expand=True)
        stats = app.last_stats
        counts = stats.get('content_states', {})
        quick = stats.get('quick', {})
        report.insert('end', f"文件名可搜：{stats.get('files', 0):,} 个文件\n语义可搜：{stats.get('semantic_files', 0):,} 个文件（至少一个片段已完成）\n")
        if quick:
            report.insert('end', f"快速名称语义：{quick.get('covered_files', 0):,} / {quick.get('eligible_files', 0):,} 个文件\n快速正文语义：{quick.get('text_fragments', 0):,} 个片段\n")
            report.insert('end', '快速语义使用文件名、目录和已提取正文；图片画面、扫描页和视频画面由多模态模型继续分析。\n')
        report.insert('end', f"已建立语义索引的片段：{stats.get('covered_units', 0):,} / {stats.get('units', 0):,}（不含尚未提取的内容）\n")
        report.insert('end', f"仅文件名：{counts.get('metadata', 0):,} 个文件\n等待分析：{counts.get('pending', 0):,} 个文件\n文件名加速索引待补齐：{stats.get('name_pending', 0):,} 个文件\n")
        report.insert('end', '\n已收录范围：\n')
        for root in stats.get('roots', []):
            report.insert('end', f"{root['path']}  ·  {root['files']:,} 个文件  ·  {root['ready']:,} 个已提取内容\n")
        report.insert('end', '\n')
        report.insert('end', '系统、依赖和缓存目录保留文件名；受支持的个人资料持续分析。\n\n')
        report.insert('end', '预算包含文件目录与数据库维护预留，内容分析可能在标称上限之前暂停。\n\n')
        report.insert('end', (app.warning or stats.get('blocked', ''))+'\n')
        for item in stats.get('errors', []):
            report.insert('end', item['path']+'\n'+item['error']+'\n\n')
        for item in stats.get('embedding_errors', []):
            report.insert('end', item['name']+'\n'+item['error']+'\n\n')
        report.configure(state='disabled')
    app.button(bottom, '详情', details).pack(side='left', padx=6)
    def apply(close=False):
        try:
            amount = float(budget.get())
            if not .125 <= amount <= 1024:
                raise ValueError()
        except ValueError:
            error.configure(text='请输入 0.125–1024 GiB 的索引预算。')
            return
        if scope.get() == 'folders' and not folders:
            error.configure(text='请添加一个搜索文件夹。')
            return
        app.commands.put({'action': 'configure', 'settings': dict(scope=scope.get(), roots=folders,
                           exclusions=excluded, dimensions=int(dims.get()), budget_gib=amount)})
        error.configure(text='正在应用…')
        app.settings_apply_status = error
        app.settings_close_after_apply = window if close else None
    app.button(bottom, '完成', lambda: apply(close=True), True).pack(side='right')
    app.button(bottom, '应用', apply).pack(side='right', padx=8)
    # No synchronous full-database counts in the UI thread. Worker publishes snapshots.
    def refresh():
        if not window.winfo_exists():
            return
        stats = app.last_stats
        heading.configure(text='索引已暂停' if app.paused else stats.get('blocked') or app.last_status)
        counts = stats.get('content_states', {})
        pending = counts.get('pending', 0)
        searchable = stats.get('semantic_files', 0)
        eligible = sum(counts.get(key, 0) for key in ('pending', 'ready', 'error'))
        summary = f"文件名可搜 {stats.get('files', 0):,} · 语义可搜 {searchable:,} · 待分析 {pending:,}"
        quick = stats.get('quick', {})
        if quick:
            summary = f"文件名 {stats.get('files', 0):,} · 快速语义 {quick.get('covered_files', 0):,} · 内容语义 {searchable:,}"
        numbers.configure(text=('上次记录 · ' if stats.get('_cached') else '')+summary if stats else '正在读取覆盖量…')
        bar.coords(fill, 0, 0, bar.winfo_width()*min(1, searchable/eligible) if eligible else 0, 4)
        capacity = stats.get('storage', {})
        used, maximum = capacity.get('bytes', 0)/1024**3, capacity.get('limit', 8*1024**3)/1024**3
        usage.configure(text=f'{used:.2f} / {maximum:g} GiB · 不含模型文件 · '+('实时监听文件变化' if stats.get('watching') else '定期核对文件变化'))
        window._refresh_timer = window.after(500, refresh)
    def cleanup(event):
        if event.widget is window and getattr(window, '_refresh_timer', None):
            window.after_cancel(window._refresh_timer)
            window._refresh_timer = None
    window.bind('<Destroy>', cleanup, add='+')
    refresh()
