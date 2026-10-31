"""Interruptible UI-thread tweens. One timer per property; no queued animations."""
import os
import time
import tkinter as tk


def animations_enabled():
    """Read the native accessibility preference without changing system settings.

    The override is useful for deterministic captures and remote desktops. An
    unset/unknown value follows Windows; other platforms retain short tweens.
    """
    override = os.environ.get('EVERYTENSOR_REDUCED_MOTION', '').strip().lower()
    if override in ('1', 'true', 'yes', 'on'):
        return False
    if override in ('0', 'false', 'no', 'off'):
        return True
    if os.name == 'nt':
        import ctypes
        enabled = ctypes.c_int(1)
        try:
            # SPI_GETCLIENTAREAANIMATION writes a BOOL, not ANIMATIONINFO.
            # https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-systemparametersinfow
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return bool(enabled.value)
        except (AttributeError, OSError):
            pass
    return True


class Motion:
    def __init__(self, widget, value, update):
        self.widget, self.value, self.update = widget, value, update
        self.target = self._frame_value = value
        self.timer = None
        self._revision, self._destroyed = 0, False
        widget.bind('<Destroy>', self.destroy, add='+')

    def destroy(self, event=None):
        if event is None or event.widget is self.widget:
            self._destroyed = True
            self.stop()

    def stop(self):
        self._revision += 1
        timer, self.timer = self.timer, None
        self.target = self.value
        if timer is not None:
            try:
                self.widget.after_cancel(timer)
            except tk.TclError:
                pass

    def to(self, target, milliseconds=150):
        if self._destroyed:
            return
        # Repeated pointer/configure events must not restart a running tween.
        # A caller can still explicitly reset .value before replaying a pulse.
        if self.timer is not None and target == self.target and self.value == self._frame_value:
            return
        self.stop()
        self.target = target
        if target == self.value:
            return
        if milliseconds <= 0 or not animations_enabled():
            self.value = self._frame_value = target
            self.update(target)
            return
        initial, started = self.value, time.perf_counter()
        revision = self._revision

        def tick():
            if self._destroyed or revision != self._revision:
                return
            self.timer = None
            t = min(1., (time.perf_counter()-started)*1000/max(1, milliseconds))
            ease = 1-(1-t)**3
            self.value = self._frame_value = target if t == 1 else initial+(target-initial)*ease
            self.update(self.value)
            # An update may interrupt this tween or destroy its owner. Never
            # leave the old callback queued after the new operation takes over.
            if t < 1 and not self._destroyed and revision == self._revision:
                self.timer = self.widget.after(16, tick)
        tick()


def mix(first, second, amount):
    amount = min(1., max(0., amount))
    a = tuple(int(first[i:i+2], 16) for i in (1, 3, 5))
    b = tuple(int(second[i:i+2], 16) for i in (1, 3, 5))
    return '#'+''.join(f'{round(x+(y-x)*amount):02x}' for x, y in zip(a, b))
