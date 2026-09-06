# -*- coding: utf-8 -*-
"""Drives obs_recording_notification.py against the obspython stub the way OBS
would: the "OBS thread" is this process's main thread.

Usage:  python driver.py <scenario>     (scenarios: a b c d e f g h obs28)
"""

import os
import sys
import time
import ctypes
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))   # the script lives one level up

import tkinter                                              # noqa: E402

# ---------------------------------------------------------- instrumentation
AFTER_COUNT = [0]
LABEL_LOG = []          # (t, widget_class, text)
TK_EXCEPTIONS = []
T0 = time.time()

_orig_after = tkinter.Misc.after


def _counting_after(self, ms, func=None, *args):
    AFTER_COUNT[0] += 1
    return _orig_after(self, ms, func, *args)


tkinter.Misc.after = _counting_after

_orig_configure = tkinter.Misc.configure


def _logging_configure(self, cnf=None, **kw):
    if 'text' in kw:
        LABEL_LOG.append((round(time.time() - T0, 3),
                          self.__class__.__name__, kw['text']))
    return _orig_configure(self, cnf, **kw)


tkinter.Misc.configure = _logging_configure
tkinter.Misc.config = _logging_configure

MAP_LOG = []            # (t, 'withdraw' | 'deiconify')
_orig_withdraw = tkinter.Wm.wm_withdraw
_orig_deiconify = tkinter.Wm.wm_deiconify


def _logged_withdraw(self):
    MAP_LOG.append((round(time.time() - T0, 3), 'withdraw'))
    return _orig_withdraw(self)


def _logged_deiconify(self):
    MAP_LOG.append((round(time.time() - T0, 3), 'deiconify'))
    return _orig_deiconify(self)


tkinter.Wm.wm_withdraw = _logged_withdraw
tkinter.Wm.withdraw = _logged_withdraw
tkinter.Wm.wm_deiconify = _logged_deiconify
tkinter.Wm.deiconify = _logged_deiconify

import obspython as obs                                     # noqa: E402
import obs_recording_notification as script                 # noqa: E402


def _record_exc(exc, val, tb):
    TK_EXCEPTIONS.append(''.join(traceback.format_exception(exc, val, tb)))
    sys.stderr.write('TK CALLBACK EXCEPTION:\n' +
                     ''.join(traceback.format_exception(exc, val, tb)))


script._report_tk_exception = _record_exc

RESULTS = []
LOAD_MS = []


def say(*parts):
    line = ' '.join(str(p) for p in parts)
    RESULTS.append(line)
    print(line)
    sys.stdout.flush()


def titles():
    return [t for (_, cls, t) in LABEL_LOG if cls == 'Label' and
            not t.startswith('C:') and '.mkv' not in t]


def sublines():
    return [t for (_, cls, t) in LABEL_LOG if cls == 'Label' and
            ('.mkv' in t or t == '')]


def load(settings=None):
    if settings is None:
        settings = obs.obs_data_create()
        script.script_defaults(settings)
    script.script_update(settings)
    t = time.perf_counter()
    script.script_load(settings)
    LOAD_MS.append((time.perf_counter() - t) * 1000.0)
    return settings


def probe(fn, timeout=3.0):
    """Run fn(app) on the Tk thread and bring the result back."""
    out = {}
    done = threading.Event()
    app = script.app_instance
    if app is None:
        return {'err': 'no app'}

    def run():
        try:
            out['v'] = fn(app)
        except Exception as e:                              # noqa: BLE001
            out['err'] = repr(e)
        finally:
            done.set()
    try:
        app.after(0, run)
    except Exception as e:                                  # noqa: BLE001
        return {'err': repr(e)}
    done.wait(timeout)
    return out


def fire_timed(event):
    """Returns how long the 'OBS thread' was blocked, in ms (perf_counter:
    time.time() only has ~15.6 ms resolution on Windows)."""
    t = time.perf_counter()
    obs.fire(event)
    return (time.perf_counter() - t) * 1000.0


REPLAY = obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_SAVED
REC_STOP = obs.OBS_FRONTEND_EVENT_RECORDING_STOPPED
REC_START = obs.OBS_FRONTEND_EVENT_RECORDING_STARTED


# --------------------------------------------------------------- scenarios

def scenario_a():
    """6 replay-saved events 4.5 s apart -> zero residual timers at idle."""
    load()
    for i in range(6):
        fire_timed(REPLAY)
        time.sleep(4.5)
    time.sleep(1.0)                       # let the last toast finish
    pend_before = probe(lambda a: len(a.master.tk.splitlist(a.master.tk.call('after', 'info'))))
    n0 = AFTER_COUNT[0]
    t0 = time.time()
    time.sleep(3.0)
    dt = time.time() - t0
    n1 = AFTER_COUNT[0]
    pend_after = probe(lambda a: len(a.master.tk.splitlist(a.master.tk.call('after', 'info'))))
    say('a: after() calls during %.2fs idle = %d (%.2f/s)' %
        (dt, n1 - n0, (n1 - n0) / dt))
    say('a: pending Tcl after timers at idle: before=%s after=%s' %
        (pend_before.get('v'), pend_after.get('v')))
    say('a: total after() calls for 6 notifications = %d' % n1)
    say('a: titles shown = %r' % (titles(),))
    script.script_unload()


def scenario_b():
    """Two replay events 500 ms apart: both shown, fade-out in between."""
    load()
    fire_timed(REPLAY)
    time.sleep(0.5)
    fire_timed(REPLAY)
    states = []
    end = time.time() + 9.0
    while time.time() < end:
        p = probe(lambda a: (a.master.state(),
                             float(a.master.attributes('-alpha'))))
        if 'v' in p:
            states.append((round(time.time() - T0, 2), p['v']))
        time.sleep(0.25)
    withdrawn_between = False
    seen_first = False
    for i, (_, (st, _al)) in enumerate(states):
        if st == 'normal':
            seen_first = True
        elif seen_first and st == 'withdrawn':
            # was there a later 'normal' after this withdraw?
            if any(s[1][0] == 'normal' for s in states[i:]):
                withdrawn_between = True
    say('b: titles = %r' % (titles(),))
    say('b: "Replay Saved" shown %d time(s)' %
        sum(1 for t in titles() if t.startswith('Replay Saved')))
    say('b: withdrawn (faded out) seen by 250ms sampling = %s' %
        withdrawn_between)
    say('b: map/unmap log = %r' % (MAP_LOG,))
    say('b: fade-out between the two toasts = %s' %
        (len([m for m in MAP_LOG if m[1] == 'withdraw']) >= 2 and
         [m[1] for m in MAP_LOG[1:]] ==
         ['deiconify', 'withdraw', 'deiconify', 'withdraw']))
    script.script_unload()


def scenario_c():
    """Replay saved, then recording stopped 1 s later: both, in order."""
    load()
    fire_timed(REPLAY)
    time.sleep(1.0)
    fire_timed(REC_STOP)
    time.sleep(9.0)
    say('c: titles in order = %r' % (titles(),))
    say('c: subtitles seen = %r' % (sublines(),))
    script.script_unload()


def scenario_d():
    """After fade-out: state 'withdrawn', alpha exactly 0.0."""
    load()
    fire_timed(REPLAY)
    time.sleep(1.0)
    mid = probe(lambda a: (a.master.state(),
                           float(a.master.attributes('-alpha'))))
    say('d: mid-animation state/alpha = %r' % (mid.get('v'),))
    time.sleep(4.5)
    end = probe(lambda a: (a.master.state(),
                           float(a.master.attributes('-alpha')),
                           a.is_animating, len(a.queue)))
    say('d: post-fade state/alpha/is_animating/queue = %r' % (end.get('v'),))
    script.script_unload()


def scenario_e():
    """Events before load / after unload; unload timing; reload."""
    t = fire_timed(REPLAY)
    say('e: fire before script_load blocked %.2f ms (no exception)' % t)

    load()
    t = fire_timed(REPLAY)
    say('e: fire after load blocked %.2f ms' % t)
    time.sleep(1.0)
    n_before_unload = len(titles())

    t0 = time.time()
    script.script_unload()
    unload_ms = (time.time() - t0) * 1000.0
    alive = script._thread is not None and script._thread.is_alive()
    thread_objs = [th for th in threading.enumerate()
                   if th.name == 'obs-notify-tk']
    say('e: script_unload returned in %.1f ms; tracked thread alive=%s; '
        'live tk threads=%d; app_instance=%r; callbacks=%d' %
        (unload_ms, alive, len(thread_objs), script.app_instance,
         len(obs.CALLBACKS)))

    t1 = fire_timed(REPLAY)
    t2 = fire_timed(REC_START)
    say('e: fires after unload blocked %.2f / %.2f ms (no exception)' %
        (t1, t2))

    load()
    fire_timed(REPLAY)
    time.sleep(2.0)
    n_after_reload = len(titles())
    say('e: titles before unload=%d, after reload=%d -> reload shows a '
        'notification = %s' % (n_before_unload, n_after_reload,
                               n_after_reload > n_before_unload))
    say('e: titles = %r' % (titles(),))
    say('e: script_load blocked the OBS thread %s ms (both loads)' %
        (['%.1f' % v for v in LOAD_MS],))
    time.sleep(3.0)
    script.script_unload()


def scenario_f():
    """How long does the OBS thread block per event?"""
    load()
    times = []
    for i in range(10):
        times.append(fire_timed(REPLAY if i % 2 == 0 else REC_START))
        time.sleep(0.3)
    ts = sorted(times)
    say('f: OBS-thread block per event: first=%.3f ms min=%.3f ms '
        'median=%.3f ms max=%.3f ms (n=10)' %
        (times[0], ts[0], ts[len(ts) // 2], ts[-1]))
    script.script_unload()

    # burst coalescing: 5 identical events inside one animation
    n = len(titles())
    load()
    for _ in range(5):
        fire_timed(REPLAY)
        time.sleep(0.1)
    time.sleep(8.5)
    say('f: burst of 5 replay events -> titles %r' % (titles()[n:],))
    script.script_unload()


def scenario_obs28():
    """OBS 28: no obs_frontend_get_last_replay -> graceful degradation."""
    say('obs28: has get_last_replay = %s' %
        hasattr(obs, 'obs_frontend_get_last_replay'))
    load()
    fire_timed(REPLAY)
    time.sleep(1.0)
    p = probe(lambda a: (a.label.cget('text'), a.sublabel.cget('text'),
                         bool(a.sublabel.winfo_ismapped())))
    say('obs28: label/sublabel/sub-mapped = %r' % (p.get('v'),))
    time.sleep(3.5)
    script.script_unload()


def scenario_g():
    """script_properties/defaults/update, the Test button, geometry, corner,
    duration, and the double-script_load guard."""
    settings = obs.obs_data_create()
    script.script_defaults(settings)
    props = script.script_properties()
    say('g: properties = %r' %
        ([(p.kind, p.name) for p in props.props],))
    obs.obs_data_set_int(settings, 'duration_ms', 800)
    obs.obs_data_set_string(settings, 'corner', 'bottom-left')
    obs.obs_data_set_bool(settings, 'rec_start', False)
    script.script_update(settings)
    script.script_load(settings)
    t = script._thread
    script.script_load(settings)                    # "loaded twice" guard
    say('g: second script_load reused the thread = %s; live tk threads = %d' %
        (t is script._thread,
         len([x for x in threading.enumerate() if x.name == 'obs-notify-tk'])))

    # disabled event must produce nothing
    n = len(titles())
    fire_timed(REC_START)
    time.sleep(0.6)
    say('g: recording-start disabled -> new titles = %r' % (titles()[n:],))

    # the Test notification button
    btn = [p for p in props.props if p.kind == 'button'][0]
    btn.callback(props, btn)
    time.sleep(0.4)
    geo = probe(lambda a: (a.master.geometry(), a.master.winfo_screenwidth(),
                           a.master.winfo_screenheight(),
                           a.label.cget('text'), a.sublabel.cget('text')))
    say('g: test button -> geometry/screen/label/sub = %r' % (geo.get('v'),))
    t0 = time.time()
    while time.time() - t0 < 4.0:
        st = probe(lambda a: a.master.state())
        if st.get('v') == 'withdrawn':
            break
        time.sleep(0.05)
    say('g: 800 ms duration -> on-screen for %.2f s (fade ~0.5 s + hold 0.8 s)'
        % (time.time() - t0))

    # replay-buffer on/off notices, off by default, behind the setting
    n = len(titles())
    obs.obs_data_set_bool(settings, 'buffer_state', False)
    script.script_update(settings)
    fire_timed(obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STARTED)
    time.sleep(0.5)
    say('g: buffer on/off disabled -> new titles = %r' % (titles()[n:],))
    obs.obs_data_set_bool(settings, 'buffer_state', True)
    script.script_update(settings)
    fire_timed(obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STARTED)
    time.sleep(1.6)
    fire_timed(obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STOPPED)
    time.sleep(2.0)
    say('g: buffer on/off enabled -> new titles = %r' % (titles()[n:],))
    script.script_unload()


# ------------------------------------------------------- scenario h: focus
#
# The popup must never take the keyboard away from whatever the user is doing.
# We do not create a window of our own: we simply remember whatever is in the
# foreground when the scenario starts (the terminal, or the user's app) and
# assert that it is still in the foreground through every notification.

FAILURES = []

_U32 = getattr(getattr(ctypes, 'windll', None), 'user32', None)
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint),
                ('flags', ctypes.c_uint),
                ('hwndActive', ctypes.c_void_p),
                ('hwndFocus', ctypes.c_void_p),
                ('hwndCapture', ctypes.c_void_p),
                ('hwndMenuOwner', ctypes.c_void_p),
                ('hwndMoveSize', ctypes.c_void_p),
                ('hwndCaret', ctypes.c_void_p),
                ('rcCaret', ctypes.c_long * 4)]


def _fg():
    return int(_U32.GetForegroundWindow() or 0)


def _title(hwnd):
    if not hwnd:
        return '<none>'
    buf = ctypes.create_unicode_buffer(256)
    _U32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _active_focus():
    """GetActiveWindow()-equivalent for the *foreground* thread (our own
    thread's GetActiveWindow() would say nothing about the popup, which lives
    on the Tk thread)."""
    gti = _GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(gti)
    if not _U32.GetGUIThreadInfo(0, ctypes.byref(gti)):
        return 0, 0
    return int(gti.hwndActive or 0), int(gti.hwndFocus or 0)


def _popup_info(a):
    hwnd = script._toplevel_hwnd(a.master)[0]
    if not hwnd:
        return 0, 0, False, a.master.state()
    ex = int(_U32.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
    return hwnd, ex, bool(_U32.IsWindowVisible(hwnd)), a.master.state()


def _focus_case(tag, trigger, samples=(0.2, 1.0, 4.5)):
    before = _fg()
    say('h[%s]: foreground before = %s %r' % (tag, before, _title(before)))
    trigger()
    t0 = time.time()
    seen_visible = False
    for dt in samples:
        while time.time() - t0 < dt:
            time.sleep(0.02)
        now = _fg()
        act, foc = _active_focus()
        info = probe(_popup_info).get('v') or (0, 0, False, '?')
        hwnd, ex, vis, state = info
        if vis:
            seen_visible = True
        ok_fg = (now == before)
        ok_hwnd = hwnd not in (now, act, foc) or hwnd == 0
        say('h[%s]: +%.1fs foreground = %s %r (%s) | popup hwnd=%s state=%s '
            'visible=%s exstyle=0x%08X NOACTIVATE=%s TOOLWINDOW=%s | '
            'active=%s focus=%s' %
            (tag, dt, now, _title(now), 'unchanged' if ok_fg else 'CHANGED',
             hwnd, state, vis, ex, bool(ex & WS_EX_NOACTIVATE),
             bool(ex & WS_EX_TOOLWINDOW), act, foc))
        if not ok_fg:
            FAILURES.append('%s: foreground changed at +%.1fs (%r -> %r)' %
                            (tag, dt, _title(before), _title(now)))
        if not ok_hwnd:
            FAILURES.append('%s: popup hwnd %s was foreground/active/focus at '
                            '+%.1fs' % (tag, hwnd, dt))
        if vis and not (ex & WS_EX_NOACTIVATE):
            FAILURES.append('%s: visible popup lacks WS_EX_NOACTIVATE at '
                            '+%.1fs (exstyle=0x%08X)' % (tag, dt, ex))
    if not seen_visible:
        FAILURES.append('%s: popup was never visible - nothing was tested'
                        % (tag,))


def scenario_h():
    """Focus stealing: the foreground window must survive every notification."""
    if _U32 is None:
        say('h: not Windows - skipped')
        return
    at_start = _fg()
    say('h: foreground before script_load = %s %r' %
        (at_start, _title(at_start)))
    load()
    time.sleep(0.4)
    after_load = _fg()
    say('h: foreground after script_load = %s %r (%s)' %
        (after_load, _title(after_load),
         'unchanged' if after_load == at_start else 'CHANGED'))
    if after_load != at_start:
        FAILURES.append('script_load stole the foreground (%r -> %r)' %
                        (_title(at_start), _title(after_load)))
    p = probe(_popup_info).get('v')
    say('h: popup hwnd/exstyle/visible/state after load = %r' % (p,))
    if p and not (p[1] & WS_EX_NOACTIVATE):
        FAILURES.append('load: no-activate style missing right after startup')

    _focus_case('replay', lambda: fire_timed(REPLAY))
    time.sleep(1.0)

    props = script.script_properties()
    btn = [x for x in props.props if x.kind == 'button'][0]
    _focus_case('test-button', lambda: btn.callback(props, btn))
    time.sleep(1.0)

    def burst():
        for _ in range(3):
            fire_timed(REPLAY)
            time.sleep(0.1)

    _focus_case('burst-of-3', burst)

    after = _fg()
    say('h: foreground at end = %s %r' % (after, _title(after)))
    say('h: titles shown = %r' % (titles(),))
    say('h: FOCUS FAILURES = %d %r' % (len(FAILURES), FAILURES))
    script.script_unload()


SCENARIOS = {'a': scenario_a, 'b': scenario_b, 'c': scenario_c,
             'd': scenario_d, 'e': scenario_e, 'f': scenario_f,
             'g': scenario_g, 'h': scenario_h, 'obs28': scenario_obs28}

if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'b'
    SCENARIOS[name]()
    say('%s: TK CALLBACK EXCEPTIONS = %d' % (name, len(TK_EXCEPTIONS)))
    if TK_EXCEPTIONS:
        for e in TK_EXCEPTIONS:
            print(e)
        sys.exit(2)
    if FAILURES:
        for f in FAILURES:
            print('FOCUS FAILURE: ' + f)
        sys.exit(4)             # not 3: that is the Tcl_AsyncDelete abort
    print('SCENARIO %s OK' % name)
