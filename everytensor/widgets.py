"""Small, native canvas controls; no browser or additional UI runtime."""
from pathlib import Path
import tkinter as tk
from . import typography
from tkinter.font import Font
from collections import OrderedDict
from PIL import ImageTk
from . import appearance
from .motion import Motion, mix

BG, WHITE, INK, MUTED, RED = '#fbfbfb', '#ffffff', '#25262b', '#70717b', '#ad3935'
LINE, TINT = '#e7e7eb', '#f7eeed'


def rounded(canvas, x, y, w, h, radius=14, **options):
    if not hasattr(canvas, '_surfaces'):
        canvas._surfaces = OrderedDict()
    key = (max(1, int(w)), max(1, int(h)), options.get('fill', ''), options.get('outline', ''), radius)
    if key not in canvas._surfaces:
        canvas._surfaces[key] = ImageTk.PhotoImage(appearance.surface(*key), master=canvas)
    canvas._surfaces.move_to_end(key)
    while len(canvas._surfaces) > 8:
        canvas._surfaces.popitem(last=False)
    if options.get('item') is not None:
        item = options['item']
        canvas.itemconfigure(item, image=canvas._surfaces[key])
        canvas.coords(item, x, y)
        return item
    return canvas.create_image(x, y, image=canvas._surfaces[key], anchor='nw', tags=options.get('tags', ()))


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.window, self.timer = widget, text, None, None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def schedule(self, _=None):
        self.hide()
        self.timer = self.widget.after(600, self.show)

    def show(self):
        self.timer = None
        self.window = tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.geometry(f'+{self.widget.winfo_rootx()-20}+{self.widget.winfo_rooty()+self.widget.winfo_height()+6}')
        tk.Label(self.window, text=self.text() if callable(self.text) else self.text, padx=9, pady=6,
                 bg='#303034', fg=WHITE, font=(typography.family(), 9)).pack()

    def hide(self, _=None):
        if self.timer:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window:
            self.window.destroy()
            self.window = None


class BrandMark(tk.Label):
    def __init__(self, master):
        super().__init__(master, bg=BG, bd=0)
        self.mark_size = 0

    def resize(self, size):
        if size != self.mark_size:
            self.mark_size = size
            self.photo = ImageTk.PhotoImage(appearance.brand(size), master=self)
            self.configure(image=self.photo)


class Button(tk.Canvas):
    def __init__(self, master, text, command, accent=False):
        self.options = dict(text=text, fg=WHITE if accent else INK,
                            bg=RED if accent else WHITE, state='normal',
                            font=(typography.family(), 10), variant='pill', min_width=0)
        self.command, self.accent, self.hover = command, accent, False
        self.pressed = False
        self.pointer_focus = False
        super().__init__(master, height=39, bg=master.cget('bg'), highlightthickness=0,
                         takefocus=True, cursor='hand2')
        self.bind('<ButtonPress-1>', self.press)
        self.bind('<ButtonRelease-1>', self.release)
        self.bind('<Return>', self.invoke)
        self.bind('<space>', self.invoke)
        self.bind('<Enter>', lambda _: self.set_hover(True))
        self.bind('<Leave>', lambda _: self.set_hover(False))
        self.bind('<FocusIn>', self.draw)
        self.bind('<FocusOut>', self.blur)
        self.font = Font(self, font=self.options['font'])
        self.hover_motion = Motion(self, 0., lambda _: self.draw())
        self.press_motion = Motion(self, 0., lambda _: self.draw())
        self.last_width = None
        self._measure_key = None
        self._content_key = None
        self._background_item = None
        self.suggestion_icon = None
        self.draw()

    def press(self, _):
        if self.options['state'] != 'disabled':
            self.pressed = True
            self.pointer_focus = True
            self.focus_set()
            self.press_motion.to(1., 70)

    def release(self, event):
        pressed, self.pressed = self.pressed, False
        self.press_motion.to(0., 140)
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            return self.invoke()

    def invoke(self, _=None):
        if _ is not None:
            self.pointer_focus = False
            self.draw()
        if self.options['state'] != 'disabled':
            self.command()
        return 'break'

    def blur(self, _=None):
        self.pointer_focus = False
        self.draw()

    def has_focus_ring(self):
        return self.focus_get() == self and not self.pointer_focus

    def set_hover(self, value):
        self.hover = value
        self.hover_motion.to(float(value), 140)

    def configure(self, cnf=None, **kwargs):
        if 'font' in kwargs and kwargs['font'] != self.options['font']:
            spec = kwargs['font']
            self.font.configure(family=spec[0], size=spec[1], weight=spec[2] if len(spec) > 2 else 'normal')
        for key in tuple(kwargs):
            if key in self.options:
                self.options[key] = kwargs.pop(key)
        if kwargs or cnf:
            super().configure(cnf, **kwargs)
        self.draw()

    config = configure

    def draw(self, _=None):
        font = self.font
        suggestion = self.options['variant'] == 'suggestion'
        measure_key = (self.options['font'], self.options['text'])
        if measure_key != self._measure_key:
            self._text_width = font.measure(self.options['text'])
            self._measure_key = measure_key
        w = max(63, self.options['min_width'], self._text_width+(65 if suggestion else 32))
        if self.last_width != w:
            super().configure(width=w, height=48 if suggestion else 39)
            self.last_width = w
        fill = self.options['bg']
        disabled = self.options['state'] == 'disabled'
        if not disabled:
            fill = mix(fill, '#a7352f' if self.accent else '#efeff1', round(self.hover_motion.value*8)/8)
            fill = mix(fill, '#952e29' if self.accent else '#e4e4e7', round(self.press_motion.value*8)/8)
        outline = RED if self.has_focus_ring() else (fill if fill in (BG, TINT) else LINE)
        shift = self.press_motion.value
        if self.options['variant'] != 'tab':
            self._background_item = rounded(self, 1+shift, 1+shift, w-2-shift*2, (45 if suggestion else 36)-shift*2,
                    23 if suggestion else 12, fill=fill, outline=outline, item=self._background_item)
        elif self.hover_motion.value > .02 or self.has_focus_ring():
            self._background_item = rounded(self, 3, 3, w-6, 32, 10, fill=fill,
                    outline=RED if self.has_focus_ring() else '', item=self._background_item)
            self.itemconfigure(self._background_item, state='normal')
        elif self._background_item is not None:
            self.itemconfigure(self._background_item, state='hidden')
        if self._background_item is not None:
            self.tag_lower(self._background_item)
        cy = (24 if suggestion else 19)+shift
        content_key = (measure_key, w, suggestion, disabled, self.options['fg'])
        if self._content_key != content_key:
            self.delete('content')
            if suggestion:
                if self.suggestion_icon is None:
                    self.suggestion_icon = ImageTk.PhotoImage(appearance.icon('search', 16, MUTED), master=self)
                self.create_image(w/2-self._text_width/2-12, cy, image=self.suggestion_icon, tags='content')
            self._text_item = self.create_text(w/2+(10 if suggestion else 0), cy,
                             text=self.options['text'], font=font, tags='content',
                             fill='#b6b1af' if disabled else self.options['fg'])
            self._content_key = content_key
        else:
            self.coords(self._text_item, w/2+(10 if suggestion else 0), cy)


class IconButton(Button):
    def __init__(self, master, name, command, label):
        self.icon_name = name
        super().__init__(master, '', command)
        self.tooltip = Tooltip(self, label)

    def draw(self, _=None):
        self.delete('all')
        if self.last_width != 36:
            tk.Canvas.configure(self, width=36, height=36)
            self.last_width = 36
        if self.hover_motion.value > .01 or self.has_focus_ring():
            rounded(self, 1, 1, 34, 34, 11,
                    fill=mix(self.master.cget('bg'), '#e7e7ea', round(self.hover_motion.value*8)/8),
                    outline=RED if self.has_focus_ring() else '')
        if not hasattr(self, 'icon_photo'):
            self.icon_photo = ImageTk.PhotoImage(appearance.icon(self.icon_name, 19, MUTED), master=self)
        self.create_image(18, 18+self.press_motion.value, image=self.icon_photo)


class SearchBox(tk.Canvas):
    def __init__(self, master, variable):
        super().__init__(master, height=66, bg=BG, highlightthickness=0)
        self.variable = variable
        self._surface_key = None
        self.focus_motion = Motion(self, 0., self.draw_focus)
        self.entry = tk.Entry(self, textvariable=variable, bg=WHITE, fg=INK,
                              insertbackground=RED, relief='flat', bd=0,
                              selectbackground=TINT, selectforeground=INK,
                              font=(typography.family(), 14))
        self.window = self.create_window(54, 31, window=self.entry, anchor='w')
        self.placeholder = tk.Label(self, text='描述你想找的文件、画面或内容…', bg=WHITE, fg=MUTED,
                                    font=(typography.family(), 12), cursor='xterm', anchor='w')
        self.placeholder.bind('<Button-1>', lambda _: self.entry.focus_set())
        self.placeholder_window = self.create_window(54, 31, window=self.placeholder, anchor='w')
        self.clear_button = IconButton(self, 'close', self.clear, '清空搜索 · Esc')
        tk.Canvas.configure(self.clear_button, bg=WHITE)
        self.shortcut = tk.Label(self, text='Ctrl K', bg=WHITE, fg=MUTED, font=('Segoe UI', 9))
        self.search_icon = ImageTk.PhotoImage(appearance.icon('search', 23, MUTED), master=self)
        variable.trace_add('write', self.draw)
        self.entry.bind('<FocusIn>', lambda _: self.focus_motion.to(1., 160))
        self.entry.bind('<FocusOut>', lambda _: self.focus_motion.to(0., 180))
        self.bind('<Configure>', self.draw)
        self.bind('<Button-1>', lambda _: self.entry.focus_set())

    def clear(self):
        self.variable.set('')
        self.entry.focus_set()

    def draw_focus(self, value):
        self.delete('focus-ring')
        w, h = self.winfo_width(), self.winfo_height()
        # A vector stroke keeps focus animation off the bitmap/shadow path.
        x, y, r = 3, 3, 20
        right, bottom = w-3, h-5
        self.create_line(x+r, y, right-r, y, right, y, right, y+r,
                         right, bottom-r, right, bottom, right-r, bottom,
                         x+r, bottom, x, bottom, x, bottom-r, x, y+r,
                         x, y, x+r, y, smooth=True, splinesteps=24,
                         fill=mix(LINE, '#c18782', value), width=1.4, tags='focus-ring')

    def draw(self, *_):
        self.delete('decoration')
        w, h = self.winfo_width(), self.winfo_height()
        key = (max(2, w-6), max(2, h-8), WHITE, '', 20)
        if key != self._surface_key:
            self.surface_photo = ImageTk.PhotoImage(appearance.surface(*key), master=self)
            self._surface_key = key
        self.create_image(3, 3, image=self.surface_photo, anchor='nw', tags='decoration')
        self.create_image(30, h/2-1, image=self.search_icon, tags='decoration')
        self.coords(self.window, 56, h/2-1)
        self.coords(self.placeholder_window, 54, h/2-1)
        if self.variable.get():
            self.shortcut.place_forget()
            self.clear_button.place(x=w-50, y=h/2-19)
        else:
            self.clear_button.place_forget()
            self.shortcut.place(x=w-64, y=h/2-12, width=45, height=24)
        self.itemconfigure(self.placeholder_window, state='hidden' if self.variable.get() else 'normal',
                           width=max(80, w-150))
        self.tag_lower('decoration')
        self.itemconfigure(self.window, width=max(100, w-128))
        self.draw_focus(self.focus_motion.value)


class ImageView(tk.Canvas):
    """Scrollable native preview; a wide PDF stays readable without clipping."""
    def __init__(self, master, **kwargs):
        self.picture, self.message, self.ink = None, '', MUTED
        self.play_command = None
        self.content_height = 1
        super().__init__(master, highlightthickness=0, height=180, **kwargs)
        self.scroll_motion = Motion(self, 0., lambda value: tk.Canvas.yview_moveto(self, value))
        self.scrollbar = tk.Scrollbar(self, orient='vertical', command=self.yview,
                                     bd=0, highlightthickness=0, width=10)
        super().configure(yscrollcommand=self.scrollbar.set)
        self.bind('<Configure>', self.draw)
        self.bind('<MouseWheel>', self.wheel)

    def configure(self, cnf=None, **kwargs):
        if 'image' in kwargs:
            picture = kwargs.pop('image')
            if picture is not self.picture:
                self.scroll_motion.stop()
                self.picture = picture or None
                self.yview_moveto(0)
        if 'text' in kwargs:
            self.message = kwargs.pop('text')
        if 'fg' in kwargs:
            self.ink = kwargs.pop('fg')
        if kwargs or cnf:
            super().configure(cnf, **kwargs)
        self.draw()

    config = configure

    def draw(self, _=None):
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if self.picture:
            iw, ih = self.picture.width(), self.picture.height()
            overflow = ih > h
            self.content_height = max(h, ih+12)
            x = max(iw/2+4, (w-(12 if overflow else 0))/2)
            y = 6 if overflow else max(6, (h-ih)/2)
            self.create_image(x, y, image=self.picture, anchor='n')
            if self.play_command:
                center_y = y+ih/2
                rounded(self, x-28, center_y-28, 56, 56, 28, fill='#262628', outline='', tags='play')
                self.play_icon = ImageTk.PhotoImage(appearance.icon('play', 28, '#ffffff'), master=self)
                self.create_image(x+2, center_y, image=self.play_icon, tags='play')
                self.tag_bind('play', '<Button-1>', lambda _: self.play_command())
                # Updating this widget through our configure() redraws/deletes
                # the current item, which emits Leave/Enter forever on Windows.
                self.tag_bind('play', '<Enter>', lambda _: tk.Canvas.configure(self, cursor='hand2'))
                self.tag_bind('play', '<Leave>', lambda _: tk.Canvas.configure(self, cursor=''))
            super().configure(scrollregion=(0, 0, w, self.content_height))
            if overflow:
                self.scrollbar.place(relx=1, x=-10, y=0, width=10, relheight=1)
            else:
                self.scrollbar.place_forget()
        else:
            self.scrollbar.place_forget()
            self.content_height = max(1, h)
            super().configure(scrollregion=(0, 0, w, h))
            self.create_text(w/2, h/2, text=self.message, fill=self.ink,
                             font=(typography.family(), 10), width=max(80, w-40))

    def wheel(self, event):
        if self.content_height > self.winfo_height():
            total = self.content_height
            current = self.canvasy(0)/total
            target = self.scroll_motion.target if self.scroll_motion.timer else current
            self.scroll_motion.value = current
            self.scroll_motion.to(max(0., min(1-self.winfo_height()/total,
                                               target-event.delta/120*86/total)), 140)
        return 'break'

    def yview(self, *args):
        if args:
            self.scroll_motion.stop()
        return super().yview(*args)


class ResultList(tk.Canvas):
    """Only paint visible rows. Selection methods mirror the previous Treeview."""
    def __init__(self, master, **kwargs):
        super().__init__(master, bg=BG, highlightthickness=0, takefocus=True, **kwargs)
        self.rows, self.selected_id, self.photos = [], None, OrderedDict()
        self.on_select = None
        self.on_interact = None
        self.on_view = None
        self.hover_id = None
        self._view_range = None
        self.fit_cache = OrderedDict()
        self.file_icons = {}
        self.row_height = 72
        self.selection_item = None
        self.selection_motion = Motion(self, 1., self.draw_selection)
        self.scroll_motion = Motion(self, 0., self.scroll_to)
        self.name_font = Font(family=typography.family(), size=12)
        self.detail_font = Font(family=typography.family(), size=10)
        self.empty_text = '正在查找…'
        self.bind('<Configure>', self.draw)
        self.bind('<Button-1>', self.click)
        self.bind('<MouseWheel>', self.wheel)
        self.bind('<Motion>', self.hover)
        self.bind('<Leave>', lambda _: self.hover(None))
        self.bind('<Up>', lambda _: self.move(-1))
        self.bind('<Down>', lambda _: self.move(1))
        self.bind('<Prior>', lambda _: self.move(-5))
        self.bind('<Next>', lambda _: self.move(5))
        self.bind('<Home>', lambda _: self.move(-len(self.rows)))
        self.bind('<End>', lambda _: self.move(len(self.rows)))
        self.bind('<FocusIn>', self.draw)
        self.bind('<FocusOut>', self.draw)

    def set_rows(self, rows, reset=False):
        top = self.canvasy(0)
        index = max(0, min(len(self.rows)-1, int(top//self.row_height)))
        anchor = self.rows[index]['file_id'] if self.rows else None
        offset = top-index*self.row_height
        selected_index = next((i for i, r in enumerate(self.rows) if str(r['file_id']) == self.selected_id), -1)
        if selected_index >= 0 and top <= selected_index*self.row_height < top+self.winfo_height():
            anchor = self.rows[selected_index]['file_id']
            offset = top-selected_index*self.row_height
        self.rows = rows
        self._view_range = None
        self.scroll_motion.stop()
        self.row_height = 134 if rows and all(r['kind'] == 'image' for r in rows) else 72
        if not self.exists(self.selected_id):
            self.selected_id = None
        self.configure(scrollregion=(0, 0, 1, max(1, len(rows)*self.row_height)))
        destination = next((i*self.row_height+offset for i, r in enumerate(rows) if r['file_id'] == anchor), 0)
        super().yview_moveto(0 if reset else max(0, destination)/max(1, len(rows)*self.row_height))
        self.draw()

    def hover(self, event):
        index = int(self.canvasy(event.y)//self.row_height) if event is not None else -1
        key = str(self.rows[index]['file_id']) if 0 <= index < len(self.rows) else None
        if key != self.hover_id:
            self.hover_id = key
            self.draw()

    def visible_rows(self):
        start = max(0, int(self.canvasy(0)//self.row_height))
        return self.rows[start:start+int(self.winfo_height()/self.row_height)+3]

    def get_children(self):
        return tuple(str(r['file_id']) for r in self.rows)

    def exists(self, key):
        return key is not None and str(key) in self.get_children()

    def selection(self):
        return (self.selected_id,) if self.selected_id is not None else ()

    def selection_set(self, key):
        if self.exists(key):
            changed = self.selected_id != str(key)
            self.selected_id = str(key)
            if changed:
                self.selection_motion.value = .35
                self.draw()
                self.selection_motion.to(1., 160)
            if self.on_select:
                self.on_select()

    def focus(self, key=None):
        if key is None:
            return self.selected_id

    def click(self, event):
        self.scroll_motion.stop()
        if self.on_interact:
            self.on_interact()
        index = int(self.canvasy(event.y)//self.row_height)
        if 0 <= index < len(self.rows):
            self.focus_set()
            self.selection_set(self.rows[index]['file_id'])

    def move(self, delta):
        self.scroll_motion.stop()
        if self.on_interact:
            self.on_interact()
        ids = self.get_children()
        if ids:
            i = ids.index(self.selected_id) if self.selected_id in ids else -1
            i = max(0, min(len(ids)-1, i+delta))
            self.selection_set(ids[i])
            top, bottom = self.canvasy(0), self.canvasy(self.winfo_height())
            y = i*self.row_height
            if y < top or y+self.row_height > bottom:
                super().yview_moveto(y/max(1, len(ids)*self.row_height))
                self.draw()
        return 'break'

    def wheel(self, event):
        if self.on_interact:
            self.on_interact()
        total = max(1, len(self.rows)*self.row_height)
        self.scroll_motion.value = self.canvasy(0)/total
        destination = self.scroll_motion.target if self.scroll_motion.timer else self.scroll_motion.value
        target = max(0., min(max(0., 1-self.winfo_height()/total), destination-event.delta/120*76/total))
        self.scroll_motion.to(target, 120)
        return 'break'

    def scroll_to(self, fraction):
        super().yview_moveto(fraction)
        self.draw()

    def yview(self, *args):
        if args:
            self.scroll_motion.stop()
        value = super().yview(*args)
        if args:
            self.draw()
        return value

    def fit(self, text, width, font):
        key = (text, int(width), str(font))
        if key in self.fit_cache:
            self.fit_cache.move_to_end(key)
            return self.fit_cache[key]
        if font.measure(text) <= width:
            value = text
        else:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo+hi+1)//2
                if font.measure(text[:mid]+'…') <= width:
                    lo = mid
                else:
                    hi = mid-1
            value = text[:lo]+'…'
        self.fit_cache[key] = value
        if len(self.fit_cache) > 512:
            self.fit_cache.popitem(last=False)
        return value

    def draw(self, _=None):
        self.delete('all')
        self.selection_item = None
        w, h = max(100, self.winfo_width()), self.winfo_height()
        if not self.rows:
            cy = max(70, h/3)
            self.create_oval(w/2-15, cy-65, w/2+9, cy-41, outline='#bdbeb5', width=2)
            self.create_line(w/2+7, cy-43, w/2+17, cy-33, fill='#bdbeb5', width=2)
            self.create_text(w/2, cy, text=self.empty_text, fill=MUTED,
                             font=self.detail_font, width=max(100, w-60), justify='center')
            return
        start = max(0, int(self.canvasy(0)//self.row_height))
        stop = min(len(self.rows), start+int(h/self.row_height)+3)
        for i in range(start, stop):
            row, y = self.rows[i], i*self.row_height
            selected = str(row['file_id']) == self.selected_id
            if selected:
                self.selection_item = rounded(self, 2, y+3, w-7, self.row_height-7, 14,
                        fill=mix(BG, TINT, round(self.selection_motion.value*8)/8),
                        outline='#dab0ad' if self.focus_get() == self else '#ecd9d7')
            elif str(row['file_id']) == self.hover_id:
                rounded(self, 2, y+3, w-7, self.row_height-7, 14, fill='#f1f1f4', outline='')
            cy = y+self.row_height/2
            thumb = self.photos.get(row['path']) if self.row_height == 134 else None
            visual = self.row_height == 134
            x = 170 if visual else 67
            ext = Path(row['path']).suffix.lstrip('.').upper() or 'FILE'
            if thumb:
                self.create_image(82 if visual else 40, cy, image=thumb)
            else:
                symbol = 'pdf' if ext == 'PDF' else row['kind'] if row['kind'] in ('code', 'image', 'video') else 'document'
                if symbol not in self.file_icons:
                    self.file_icons[symbol] = ImageTk.PhotoImage(appearance.icon(symbol, 26, RED if symbol == 'pdf' else '#7e8694'), master=self)
                if not visual:
                    rounded(self, 18, cy-18, 36, 36, 10, fill=WHITE, outline='')
                self.create_image(82 if visual else 36, cy, image=self.file_icons[symbol])
            reserve = 90 if 'page' in row.get('location', {}) else 30
            self.create_text(x, cy-12, text=self.fit(row['name'], w-x-reserve, self.name_font),
                             anchor='w', fill=INK, font=self.name_font)
            path = str(Path(row['path']).parent).replace(str(Path.home()), '~', 1)
            self.create_text(x, cy+10, text=self.fit(path, w-x-30, self.detail_font),
                             anchor='w', fill=MUTED, font=self.detail_font)
            loc = row.get('location', {})
            if 'page' in loc and not visual:
                self.create_text(w-40, cy, text=f"p. {loc['page']}", anchor='e', fill=MUTED, font=self.detail_font)
            self.create_text(w-20, cy, text='›', fill=MUTED, font=(typography.family(), 14))
        if self.on_view and self._view_range != (start, stop):
            self._view_range = (start, stop)
            self.on_view()

    def draw_selection(self, value):
        if self.selection_item is None:
            return
        coords = self.coords(self.selection_item)
        rounded(self, coords[0], coords[1], max(100, self.winfo_width())-7,
                self.row_height-7, 14, fill=mix(BG, TINT, round(value*8)/8),
                outline='#dab0ad' if self.focus_get() == self else '#ecd9d7',
                item=self.selection_item)
