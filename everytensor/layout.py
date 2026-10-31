"""Reference-led desktop composition: search first, content second."""
import tkinter as tk
from . import typography
from tkinter import ttk
from .widgets import (BG, WHITE, INK, MUTED, RED, LINE, SearchBox, ResultList,
                      ImageView, BrandMark, IconButton, Tooltip, rounded)
from .motion import Motion


class SplitView(tk.Frame):
    """Stable children: changing mode never reparents a native pane."""
    def __init__(self, master):
        super().__init__(master, bg=BG)
        self.children_order, self.orient, self.position = [], 'horizontal', 510
        self.handle = tk.Frame(self, bg=BG, cursor='sb_h_double_arrow')
        self.handle_line = tk.Frame(self.handle, bg=LINE)
        self.handle_line.place(relx=.5, y=12, width=1, relheight=1, height=-24)
        self.handle.bind('<Enter>', lambda _: self.handle_line.configure(bg='#c7a09c'))
        self.handle.bind('<Leave>', lambda _: self.handle_line.configure(bg=LINE))
        self.handle.bind('<B1-Motion>', self.drag)
        self.handle.bind('<ButtonRelease-1>', lambda _: master.remember_pane_ratio())
        self.handle_line.bind('<B1-Motion>', self.drag)
        self.handle_line.bind('<ButtonRelease-1>', lambda _: master.remember_pane_ratio())
        self.handle_line.configure(cursor='sb_h_double_arrow')

    def add(self, child, **_):
        if child not in self.children_order:
            self.children_order.append(child)
        self.arrange()

    def forget(self, child):
        child = self.nametowidget(str(child))
        if child in self.children_order:
            self.children_order.remove(child)
        child.place_forget()
        self.arrange()

    def panes(self):
        return tuple(map(str, self.children_order))

    def cget(self, key):
        return self.orient if key == 'orient' else super().cget(key)

    def configure(self, **options):
        self.orient = options.pop('orient', self.orient)
        if options:
            super().configure(**options)
        self.arrange()

    def sash_place(self, index, x, y):
        self.position = y if self.orient == 'vertical' else x
        self.arrange()

    def sash_coord(self, index):
        return (0, self.position) if self.orient == 'vertical' else (self.position, 0)

    def drag(self, event):
        if self.orient == 'horizontal':
            self.position = event.x_root-self.winfo_rootx()
            self.arrange()

    def arrange(self):
        if not hasattr(self, 'handle'):
            return
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        self.handle.place_forget()
        if len(self.children_order) == 1:
            height = min(h, int(w*.29)+142) if self.orient == 'vertical' else h
            self.children_order[0].place(x=0, y=0, width=w, height=height)
        elif len(self.children_order) == 2:
            first, second = self.children_order
            if self.orient == 'vertical':
                pos = max(1, min(h-70, self.position))
                first.place(x=0, y=0, width=w, height=pos)
                second.place(x=0, y=pos+16, width=w, height=max(1, h-pos-16))
            else:
                pos = max(min(285, w//2), min(w-300, self.position))
                first.place(x=0, y=0, width=max(1, pos-14), height=h)
                second.place(x=pos+14, y=0, width=max(1, w-pos-14), height=h)
                self.handle.place(x=pos-5, y=0, width=11, height=h)


class Panel(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=BG, bd=0)
        self.skin = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.skin.place(x=0, y=0, relwidth=1, relheight=1)
        self.skin.bind('<Configure>', self.draw)
        self.mode = 'document'
        self.last_size = None

    def draw(self, event):
        size = (event.width, event.height, self.mode)
        if self.last_size == size:
            return
        self.last_size = size
        self.skin.delete('all')
        height = event.height-122 if self.mode == 'image' else event.height
        rounded(self.skin, 0, 0, event.width, max(1, height), 20, fill=WHITE, outline=LINE)
        self.tk.call('lower', self.skin._w)


def build(app):
    app.option_add('*Font', (typography.family(), 11))
    style = ttk.Style(app)
    style.theme_use('clam')
    style.configure('Vertical.TScrollbar', troughcolor=BG, background='#dfdfe3',
                    bordercolor=BG, arrowcolor=BG, width=7, relief='flat')
    style.configure('Hit.TCombobox', fieldbackground=WHITE, background=WHITE,
                    bordercolor=LINE, arrowcolor=MUTED, padding=(8, 5), arrowsize=12)
    style.map('Hit.TCombobox', fieldbackground=[('readonly', WHITE)], selectbackground=[('readonly', WHITE)],
              selectforeground=[('readonly', INK)])
    app.top_bar = tk.Frame(app, bg=BG, height=204)
    app.top_bar.pack(fill='x')
    app.top_bar.pack_propagate(False)
    app.logo = BrandMark(app.top_bar)
    app.wordmark = tk.Label(app.top_bar, text='EveryTensor', bg=BG, fg=INK,
                            font=('Segoe UI', 14, 'bold'))
    app.wordmark.place(x=32, y=24)
    app.local_label = tk.Label(app.top_bar, text='本地 · 私密', bg=BG, fg=MUTED,
                              font=(typography.family(), 9))
    app.local_label.place(relx=1, x=-86, y=29, anchor='ne')
    app.hero_title = tk.Label(app.top_bar, text='找到你记得的那一点。', bg=BG, fg=INK,
                             font=(typography.family(), 27))
    app.hero_subtitle = tk.Label(app.top_bar, text='一句描述，连接散落的文件与灵感。', bg=BG, fg=MUTED,
                                font=(typography.family(), 11))
    app.home_hint = tk.Label(app.top_bar, text='试着搜一搜', bg=BG, fg=MUTED,
                            font=(typography.family(), 9))
    app.search_box = SearchBox(app.top_bar, app.query)
    app.entry = app.search_box.entry
    app.entry.bind('<Down>', app.focus_results)
    app.entry.bind('<Return>', app.open_selected)
    app.settings_button = IconButton(app.top_bar, 'settings', app.settings, '设置')
    app.settings_button.place(relx=1, x=-26, y=22, anchor='ne')
    app.status_dot = tk.Canvas(app.top_bar, width=10, height=10, bg=BG, highlightthickness=0, cursor='hand2')
    app.status_dot.bind('<Button-1>', lambda _: app.settings())
    Tooltip(app.status_dot, lambda: app.model_status)
    app.tabs = tk.Frame(app.top_bar, bg=BG)
    app.tab_buttons = {}
    for label, kind in [('全部', ''), ('文档', 'document'), ('图片', 'image'), ('视频', 'video'), ('代码', 'code')]:
        button = app.button(app.tabs, label, lambda k=kind: app.set_kind(k))
        button.configure(bg=BG, fg=RED if not kind else MUTED, variant='tab')
        button.pack(side='left', padx=(0, 12))
        app.tab_buttons[kind] = button
    kinds = tuple(app.tab_buttons)
    def step_tab(kind, step):
        target = kinds[(kinds.index(kind)+step) % len(kinds)]
        app.set_kind(target)
        app.tab_buttons[target].focus_set()
        return 'break'
    for kind, button in app.tab_buttons.items():
        button.bind('<Left>', lambda _, k=kind: step_tab(k, -1))
        button.bind('<Right>', lambda _, k=kind: step_tab(k, 1))
    app.tab_indicator = tk.Frame(app.tabs, bg=RED, height=2)
    app.tab_motion = Motion(app.tabs, -1, lambda x: app.tab_indicator.place(x=int(x), y=38, width=24, height=2))
    app.examples = tk.Frame(app.top_bar, bg=BG)
    for text in ('关于世界模型的论文', '机器人把杯子放到桌上', '那张正交投影示意图'):
        button = app.button(app.examples, text, lambda s=text: app.query.set(s))
        button.configure(variant='suggestion', font=(typography.family(), 10))
        button.pack(side='left', padx=5)
    app.home = tk.Frame(app, bg=BG)
    app.home.pack(fill='both', expand=True)
    tk.Label(app.home, text='Ctrl K  搜索     /     ↓ ↑  浏览     /     Enter  打开',
             bg=BG, fg=MUTED, font=(typography.family(), 9)).pack(side='bottom', pady=30)
    app.body = SplitView(app)
    app.body.bind('<Configure>', app.resize_panes)
    app.body.bind('<ButtonRelease-1>', app.remember_pane_ratio)
    app.results_panel = left = tk.Frame(app.body, bg=BG)
    app.result_count = tk.Label(left, bg=BG, fg=MUTED, anchor='w', font=(typography.family(), 9))
    app.tree = ResultList(left)
    app.tree.on_select, app.tree.on_view = app.select, app.request_thumbnails
    app.tree.on_interact = app.mark_selection_interaction
    def scroll_results(*args):
        app.mark_selection_interaction()
        app.tree.yview(*args)
    scroll = ttk.Scrollbar(left, orient='vertical', command=scroll_results)
    def update_scroll(first, last):
        scroll.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            scroll.pack_forget()
        elif not scroll.winfo_manager():
            scroll.pack(side='right', fill='y', before=app.tree)
    app.tree.configure(yscrollcommand=update_scroll)
    app.tree.pack(fill='both', expand=True)
    app.tree.bind('<Double-1>', app.open_selected)
    app.tree.bind('<Return>', app.open_selected)
    app.more = app.button(left, '加载更多', app.load_more)
    app.body.add(left, minsize=285, width=510)
    app.preview = Panel(app.body)
    app.body.add(app.preview, minsize=320)
    app.preview.bind('<Configure>', app.preview_resized)
    app.preview_info = tk.Frame(app.preview, bg=WHITE)
    app.title_label = tk.Label(app.preview_info, bg=WHITE, fg=INK, anchor='w', font=(typography.family(), 12))
    app.path_label = tk.Label(app.preview_info, bg=WHITE, fg=MUTED, anchor='w', font=(typography.family(), 10))
    Tooltip(app.title_label, lambda: app.selected['name'] if app.selected else '')
    Tooltip(app.path_label, lambda: app.selected['path'] if app.selected else '')
    app.preview_meta = tk.Label(app.preview_info, bg=WHITE, fg=RED, anchor='w', font=(typography.family(), 10))
    app.controls = tk.Frame(app.preview_info, bg=WHITE)
    app.open_button = app.button(app.controls, '打开', app.open_selected)
    app.open_button.pack(side='left', padx=(0, 4))
    app.reveal_button = IconButton(app.controls, 'folder', app.reveal, '在文件夹中显示')
    app.reveal_button.pack(side='left')
    app.reveal_image_button = app.button(app.controls, '在文件夹中显示', app.reveal)
    app.fit_button = IconButton(app.controls, 'fit', app.toggle_preview_fit, '切换整页 / 适合宽度')
    app.image_label = ImageView(app.preview, bg=WHITE)
    app.image_label.bind('<Configure>', app.preview_resized, add='+')
    app.text = tk.Text(app.preview, bg=WHITE, fg=INK, relief='flat', bd=0, wrap='word',
                       padx=6, pady=8, spacing1=3, spacing3=6, font=(typography.family(), 11))
    app.text.configure(state='disabled')
    app.keyframes = tk.Frame(app.preview, bg=WHITE, height=66)
    app.keyframes.pack_propagate(False)
    app.hit_list = ttk.Combobox(app.preview_info, state='readonly', style='Hit.TCombobox')
    app.hit_list.bind('<<ComboboxSelected>>', app.choose_hit)
    app.timeline = tk.Canvas(app.preview, bg=WHITE, height=38, highlightthickness=0)
    app.timeline.bind('<Button-1>', app.seek)
    app.timeline.bind('<Configure>', lambda _: app.draw_timeline())
    app.foot_bar = tk.Frame(app, bg=BG)
    app.footer = tk.Label(app.foot_bar, bg=BG, fg=RED, anchor='w', font=(typography.family(), 9))
    app.footer.pack(fill='x')
    app._preview_arrangement = None
    app.layout_shell()
