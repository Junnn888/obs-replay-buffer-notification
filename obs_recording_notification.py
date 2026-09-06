# -*- coding: utf-8 -*-
"""
OBS Shadowplay-style notification popup (Windows).

Shows a small dark toast in a screen corner when recording starts, when
recording stops, and - the headline feature - when the replay buffer is saved.

Design rules that the code follows everywhere:
  * Tk owns its own thread; OBS never lends us its Qt event loop.
  * The OBS thread never touches Tk state.  It only ever calls after(0, fn),
    the one cross-thread-safe tkinter entry point, and only while a
    threading.Event says the Tk mainloop is actually running.
  * No polling.  Work is pushed once per event; a deque holds notifications
    that arrive while another one is still animating.
"""

import os
import gc
import ctypes
import threading
import collections
import traceback

import tkinter as tk

import obspython as obs

__version__ = '2.0.0'

# ------------------------------------------------------------------ appearance

BG_TRANSPARENT = '#0f0f0f'          # root bg, also the -transparentcolor key
BG_CARD, BORDER = '#252525', '#404040'
FG_TEXT, FG_SUB = '#ffffff', '#a8a8a8'
COL_REC, COL_OK, COL_REPLAY, COL_OFF = '#ff3333', '#00cc00', '#0099ff', '#888888'

MARGIN = 20                         # px between the toast and the screen edges
FADE_STEP = 0.1
FADE_INTERVAL_MS = 25

# kind -> (title, indicator): indicator is a dot colour, or 'check'
KINDS = {
    'rec_start':  ('Recording Started', COL_REC),
    'rec_stop':   ('Recording Stopped', 'check'),
    'replay':     ('Replay Saved',      COL_REPLAY),
    'buffer_on':  ('Replay Buffer On',  COL_OK),
    'buffer_off': ('Replay Buffer Off', COL_OFF),
}

CORNERS = ('top-left', 'top-right', 'bottom-left', 'bottom-right')

# Settings live here.  script_update() writes them on the OBS thread; the OBS
# thread reads a snapshot and copies the values into the callable it queues, so
# the Tk thread never reads this dict and no lock is needed over there.
DEFAULTS = (('duration_ms', 3000), ('corner', 'top-right'),
            ('rec_start', True), ('rec_stop', True), ('replay', True),
            ('buffer_state', False), ('show_filename', True))
CFG = dict(DEFAULTS)
_cfg_lock = threading.Lock()


def cfg():
    with _cfg_lock:
        return dict(CFG)


def _log(msg):
    try:
        obs.script_log(obs.LOG_INFO, '[notify] ' + msg)
    except Exception:
        print('[notify] ' + msg)


# ------------------------------------------------------------- focus stealing
#
# A toast must never take the keyboard away from the game or app in front.
# Tk maps windows with a plain ShowWindow(), which activates them, so the
# toplevel is tagged at the Win32 level instead:
#   WS_EX_NOACTIVATE  - showing/clicking the window never activates it
#   WS_EX_TOOLWINDOW  - keeps it out of the task bar and the Alt-Tab list
# Everything here is best-effort: on a non-Windows Python, or if user32 ever
# refuses, we simply fall back to the old (focus-stealing) behaviour instead of
# breaking the notification.

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
_NOACTIVATE_EXSTYLE = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW


def _user32():
    windll = getattr(ctypes, 'windll', None)        # absent off Windows
    return getattr(windll, 'user32', None) if windll is not None else None


def _toplevel_hwnd(widget):
    """(hwnd, user32) for the real top-level window behind a Tk toplevel.

    Tk's winfo_id() is the client window; the window manager frame that owns
    the window styles is its parent.  Returns (0, None) when unavailable.
    """
    user32 = _user32()
    if user32 is None:
        return 0, None
    ident = widget.winfo_id()
    return (user32.GetParent(ident) or ident), user32


def _apply_no_activate(widget, verbose=False):
    """Set WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW on widget's toplevel.

    Idempotent and cheap (two user32 calls, and the setter only runs when a bit
    is actually missing), so it is safe to re-assert before every show: Tk
    re-creates the native window whenever overrideredirect or the
    -transparentcolor/-alpha/-topmost attributes are (re)applied, which drops
    any style we set earlier.  Returns the styled hwnd, or 0.
    """
    try:
        hwnd, user32 = _toplevel_hwnd(widget)
        if not hwnd:
            if verbose:
                _log('no user32: popup will take focus when shown')
            return 0
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if (style & _NOACTIVATE_EXSTYLE) != _NOACTIVATE_EXSTYLE:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  style | _NOACTIVATE_EXSTYLE)
            if verbose:
                _log('no-activate style applied to hwnd 0x%X' % (hwnd,))
        return hwnd
    except Exception as e:                      # non-Windows / odd ctypes
        if verbose:
            _log('could not set no-activate style (%r); popup may take focus'
                 % (e,))
        return 0


def _foreground_window():
    user32 = _user32()
    if user32 is None:
        return 0
    try:
        return int(user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _restore_foreground(prev, hwnd, verbose=False):
    """Hand the foreground back after the toast window is built.

    Tk activates its native window the instant it creates it - which happens
    before any style of ours can exist, and happens even though the toast is
    withdrawn and never mapped.  So the one remaining focus theft is at script
    load; undo it.  We only ever take the foreground back from our own popup
    (never from a window the user switched to meanwhile), and a process that
    owns the foreground is allowed to give it away, so this always succeeds.
    """
    user32 = _user32()
    if user32 is None or not prev:
        return
    try:
        now = int(user32.GetForegroundWindow() or 0)
        if now == prev:
            return
        if now and now != hwnd and user32.IsWindow(now):
            return                              # not ours: leave the user be
        user32.SetForegroundWindow(prev)
        if verbose:
            _log('foreground handed back to hwnd 0x%X after startup' % (prev,))
    except Exception as e:
        if verbose:
            _log('could not restore the foreground window (%r)' % (e,))


# --------------------------------------------------------------------- window

class _Item(object):
    """One queued notification."""
    __slots__ = ('kind', 'subtitle', 'hold_ms', 'corner', 'count')

    def __init__(self, kind, subtitle, hold_ms, corner):
        self.kind, self.subtitle = kind, subtitle or ''
        self.hold_ms, self.corner = hold_ms, corner
        self.count = 1


class Application(tk.Frame):
    def __init__(self, master):
        tk.Frame.__init__(self, master, bg=BG_TRANSPARENT)
        self.pack(fill=tk.BOTH, expand=True)
        master.title('OBS Recording Notification')
        master.configure(bg=BG_TRANSPARENT)
        master.overrideredirect(1)                      # borderless
        master.attributes('-topmost', True)
        master.attributes('-alpha', 0.0)
        master.attributes('-transparentcolor', BG_TRANSPARENT)
        master.withdraw()          # never mapped at startup: no focus stealing

        # The native window only exists once Tk has created it, and Tk may
        # re-create it while the wm attributes above are applied - so the
        # no-activate styling goes last, and is re-asserted before every show.
        master.update_idletasks()
        self.hwnd = _apply_no_activate(master, verbose=True)

        # widgets are instance attributes, not module globals
        card = tk.Frame(self, bg=BG_CARD, bd=0, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.pack(padx=5, pady=5, fill=tk.BOTH, expand=True)
        card.grid_columnconfigure(1, weight=1)

        self.canvas = tk.Canvas(card, height=30, width=30, bg=BG_CARD,
                                highlightthickness=0)
        self.canvas.grid(row=0, column=0, rowspan=2, padx=(10, 5), pady=5)
        self.label = tk.Label(card, text='Recording Started', bg=BG_CARD,
                              fg=FG_TEXT, anchor='w',
                              font=('Segoe UI', 11, 'bold'))
        self.label.grid(row=0, column=1, sticky='w', padx=(0, 15), pady=(5, 0))
        self.sublabel = tk.Label(card, text='', bg=BG_CARD, fg=FG_SUB,
                                 anchor='w', font=('Segoe UI', 8))
        self.sublabel.grid(row=1, column=1, sticky='w', padx=(0, 15),
                           pady=(0, 5))
        self.sublabel.grid_remove()

        self.queue = collections.deque()
        self.is_animating = False
        self.alpha = 0.0
        self.hold_ms = 3000
        self._draw(COL_REC)

    # -- queue -------------------------------------------------------------

    def enqueue(self, kind, subtitle, hold_ms, corner):
        """Always runs on the Tk thread (reached only through after(0, ...))."""
        if kind not in KINDS:
            return
        # Coalesce a burst of identical events into one toast with a count.
        # The toast currently on screen is never merged into.
        if self.queue and self.queue[-1].kind == kind:
            tail = self.queue[-1]
            tail.count += 1
            tail.subtitle = subtitle or tail.subtitle
            tail.hold_ms, tail.corner = hold_ms, corner
            return
        if len(self.queue) > 20:                # pathological burst
            self.queue.popleft()
        self.queue.append(_Item(kind, subtitle, hold_ms, corner))
        self._pump()

    def _pump(self):
        """Show the next queued notification, if we are not busy."""
        if self.is_animating or not self.queue:
            return
        item = self.queue.popleft()
        self.is_animating = True
        started = False
        try:
            title, indicator = KINDS[item.kind]
            if item.count > 1:
                title = '%s x%d' % (title, item.count)
            self.label.config(text=title)
            if item.subtitle:
                self.sublabel.config(text=item.subtitle)
                self.sublabel.grid()
            else:
                self.sublabel.grid_remove()
            self._draw(indicator)
            self.hold_ms = max(200, int(item.hold_ms))
            self._place(item.corner)
            self.alpha = 0.0
            self.master.attributes('-alpha', 0.0)
            # Re-assert right before the window is mapped: this is the only
            # moment that can steal the keyboard, and the style may have been
            # dropped by a Tk-internal window re-create.
            self.hwnd = _apply_no_activate(self.master)
            self.master.deiconify()
            self.master.attributes('-topmost', True)
            self._fade_in()
            started = True
        finally:
            # A single Tcl error must never wedge notifications forever.
            if not started:
                self.is_animating = False
                self.after(0, self._pump)

    def _finish(self):
        self.is_animating = False
        if self.queue:
            self.after(0, self._pump)       # show what arrived meanwhile

    # -- drawing / geometry -------------------------------------------------

    def _draw(self, indicator):
        c = self.canvas
        c.delete('all')
        c.create_oval(25, 25, 5, 5, outline='#000000', fill='#000000')
        c.create_oval(24, 24, 6, 6, outline=BORDER, fill=BG_CARD)
        if indicator == 'check':
            c.create_line(10, 15, 15, 20, fill=COL_OK, width=3)
            c.create_line(15, 20, 22, 10, fill=COL_OK, width=3)
        else:
            c.create_oval(22, 22, 8, 8, fill=indicator, outline=indicator)

    def _place(self, corner):
        """Size from the requested geometry (DPI/text safe), then corner it."""
        m = self.master
        m.update_idletasks()
        w, h = max(m.winfo_reqwidth(), 180), m.winfo_reqheight()
        sw, sh = m.winfo_screenwidth(), m.winfo_screenheight()
        x = (sw - w - MARGIN) if corner.endswith('right') else MARGIN
        y = MARGIN if corner.startswith('top') else (sh - h - MARGIN)
        m.geometry('%dx%d+%d+%d' % (w, h, max(0, x), max(0, y)))

    # -- animation ----------------------------------------------------------

    def _fade_in(self):
        try:
            self.alpha = min(1.0, self.alpha + FADE_STEP)
            if self.alpha >= 0.999:
                self.alpha = 1.0
            self.master.attributes('-alpha', self.alpha)
            if self.alpha < 1.0:
                self.after(FADE_INTERVAL_MS, self._fade_in)
            else:
                self.after(self.hold_ms, self._fade_out)
        except tk.TclError:
            self._finish()

    def _fade_out(self):
        try:
            self.alpha = max(0.0, self.alpha - FADE_STEP)
            if self.alpha > 0.0009:
                self.master.attributes('-alpha', self.alpha)
                self.after(FADE_INTERVAL_MS, self._fade_out)
            else:
                # Land on a real 0.0 and unmap: a 0.1-alpha topmost window is
                # invisible but still eats clicks.
                self.alpha = 0.0
                self.master.attributes('-alpha', 0.0)
                self.master.withdraw()
                self._finish()
        except tk.TclError:
            self._finish()


# --------------------------------------------------------------- Tk lifecycle

app_instance = None                 # written on the Tk thread, read anywhere
tk_ready = threading.Event()        # set only while mainloop() is running
_thread = None
_thread_lock = threading.Lock()
_callback_registered = False


def _report_tk_exception(exc, val, tb):
    traceback.print_exception(exc, val, tb)


def _tk_main():
    global app_instance
    root = None
    prev_fg = _foreground_window()      # before Tk can take it away
    try:
        root = tk.Tk()
        root.report_callback_exception = _report_tk_exception
        app_instance = Application(root)
        _restore_foreground(prev_fg, app_instance.hwnd, verbose=True)
        tk_ready.set()
        root.mainloop()
    except Exception as e:                          # never take OBS down
        _log('Tk thread error: %r' % (e,))
    finally:
        tk_ready.clear()
        app_instance = None
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
            # tkinter keeps a module-level reference to the first root it
            # creates.  Anything that outlives this thread makes Tcl finalise
            # the interpreter on the wrong thread later, which panics with
            # "Tcl_AsyncDelete: async handler deleted by the wrong thread" and
            # aborts the process.  Drop every reference here, on our thread.
            try:
                if getattr(tk, '_default_root', None) is root:
                    tk._default_root = None
                root.children.clear()               # break widget<->root cycles
            except Exception:
                pass
            root = None
            gc.collect()


def _post(fn):
    """Queue fn onto the Tk thread.  Never raises; returns success."""
    app = app_instance              # bind once - app_instance can go None
    if app is None or not tk_ready.is_set():
        return False
    try:
        app.after(0, fn)
        return True
    except (RuntimeError, tk.TclError):
        return False


def _start_tk_thread():
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return                                  # never start() twice
        _thread = threading.Thread(target=_tk_main, name='obs-notify-tk')
        _thread.daemon = True
        _thread.start()
    tk_ready.wait(3.0)          # bounded; events fired later work regardless


def _request_quit():
    """Ask the Tk thread to destroy its root.  Deliberately its own frame so no
    reference to the app survives into the join below (see _tk_main)."""
    app = app_instance
    if app is None:
        return
    try:
        app.after(0, app.master.destroy)
    except (RuntimeError, tk.TclError):
        pass


def _stop_tk_thread(timeout=1.5):
    global _thread
    with _thread_lock:
        thread, _thread = _thread, None
    _request_quit()
    if thread is not None and thread.is_alive():
        thread.join(timeout)
    tk_ready.clear()


# ------------------------------------------------------------ OBS event glue

def _notify(kind, subtitle=''):
    conf = cfg()
    app = app_instance
    if app is None:
        return
    _post(lambda: app.enqueue(kind, subtitle, conf['duration_ms'],
                              conf['corner']))


def _last_file(getter_name):
    """Saved file name via OBS 29+ API; silently empty on OBS 28."""
    if not cfg()['show_filename']:
        return ''
    getter = getattr(obs, getter_name, None)
    if getter is None:
        return ''
    try:
        path = getter()
    except Exception:
        return ''
    return os.path.basename(str(path)) if path else ''


def frontend_event_handler(event):
    conf = cfg()
    if event == obs.OBS_FRONTEND_EVENT_EXIT:
        # Tear the Tk thread down so OBS can exit cleanly.  The callback itself
        # is removed in script_unload(), which OBS always calls afterwards;
        # unregistering from inside a frontend dispatch is not worth the risk.
        _stop_tk_thread()
    elif event == obs.OBS_FRONTEND_EVENT_RECORDING_STARTED and conf['rec_start']:
        _notify('rec_start')
    elif event == obs.OBS_FRONTEND_EVENT_RECORDING_STOPPED and conf['rec_stop']:
        # STOPPED, not STOPPING: the file may still be remuxing, hence the
        # wording "Recording Stopped" rather than "Recording Saved".
        _notify('rec_stop', _last_file('obs_frontend_get_last_recording'))
    elif event == obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_SAVED and conf['replay']:
        _notify('replay', _last_file('obs_frontend_get_last_replay'))
    elif conf['buffer_state']:
        if event == obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STARTED:
            _notify('buffer_on')
        elif event == obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STOPPED:
            _notify('buffer_off')


# ----------------------------------------------------------- OBS script API

def script_description():
    return ('<b>OBS Shadowplay-style Notification</b> v' + __version__ + '<br><br>'
            'Shows a popup when recording starts, when recording stops and '
            'when the replay buffer is saved.<br><br>'
            'Windows only.  Requires Python 3.6-3.12 <b>with tkinter</b> (a '
            'standard python.org installer - the embeddable zip has no '
            'tkinter), selected under Tools &gt; Scripts &gt; Python '
            'Settings.<br><br>'
            'No OBS restart needed: the popup arms itself when the script '
            'loads.')


def script_defaults(settings):
    for key, val in DEFAULTS:
        if isinstance(val, bool):
            obs.obs_data_set_default_bool(settings, key, val)
        elif isinstance(val, int):
            obs.obs_data_set_default_int(settings, key, val)
        else:
            obs.obs_data_set_default_string(settings, key, val)


def _on_test(props, prop, *args):
    """'Test notification' button: verify the install without recording."""
    _notify('replay', _last_file('obs_frontend_get_last_replay') or
            'test-clip.mkv')
    return False


def script_properties():
    props = obs.obs_properties_create()
    obs.obs_properties_add_int_slider(props, 'duration_ms',
                                      'Display duration (ms)', 500, 10000, 250)
    lst = obs.obs_properties_add_list(props, 'corner', 'Screen corner',
                                      obs.OBS_COMBO_TYPE_LIST,
                                      obs.OBS_COMBO_FORMAT_STRING)
    for c in CORNERS:
        obs.obs_property_list_add_string(lst, c, c)
    obs.obs_properties_add_bool(props, 'rec_start', 'Notify: recording started')
    obs.obs_properties_add_bool(props, 'rec_stop', 'Notify: recording stopped')
    obs.obs_properties_add_bool(props, 'replay', 'Notify: replay saved')
    obs.obs_properties_add_bool(props, 'buffer_state',
                                'Notify: replay buffer on/off')
    obs.obs_properties_add_bool(props, 'show_filename',
                                'Show saved file name (OBS 29+)')
    obs.obs_properties_add_button(props, 'test_button', 'Test notification',
                                  _on_test)
    return props


def script_update(settings):
    values = {}
    for key, val in DEFAULTS:
        if isinstance(val, bool):                   # bool before int!
            values[key] = bool(obs.obs_data_get_bool(settings, key))
        elif isinstance(val, int):
            values[key] = int(obs.obs_data_get_int(settings, key))
        else:
            values[key] = obs.obs_data_get_string(settings, key) or val
    values['duration_ms'] = max(200, values['duration_ms'])
    if values['corner'] not in CORNERS:
        values['corner'] = 'top-right'
    with _cfg_lock:
        CFG.update(values)


def script_load(settings):
    """Arm here - not on FINISHED_LOADING, which never fires for a script added
    to an already-running OBS."""
    global _callback_registered
    if not _callback_registered:
        obs.obs_frontend_add_event_callback(frontend_event_handler)
        _callback_registered = True
    _start_tk_thread()


def script_unload():
    global _callback_registered
    if _callback_registered:
        try:
            obs.obs_frontend_remove_event_callback(frontend_event_handler)
        except Exception:
            pass
        _callback_registered = False
    _stop_tk_thread()
