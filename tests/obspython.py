# -*- coding: utf-8 -*-
"""Minimal stub of OBS Studio's `obspython` module, enough to exercise
obs_recording_notification.py outside OBS.

Set STUB_NO_LAST_REPLAY=1 in the environment to emulate OBS 28 (no
obs_frontend_get_last_replay / obs_frontend_get_last_recording).
"""

import os
import sys

# --------------------------------------------------------------- constants
OBS_FRONTEND_EVENT_STREAMING_STARTED = 0
OBS_FRONTEND_EVENT_RECORDING_STARTING = 4
OBS_FRONTEND_EVENT_RECORDING_STARTED = 5
OBS_FRONTEND_EVENT_RECORDING_STOPPING = 6
OBS_FRONTEND_EVENT_RECORDING_STOPPED = 7
OBS_FRONTEND_EVENT_REPLAY_BUFFER_STARTED = 21
OBS_FRONTEND_EVENT_REPLAY_BUFFER_STOPPED = 23
OBS_FRONTEND_EVENT_REPLAY_BUFFER_SAVED = 24
OBS_FRONTEND_EVENT_FINISHED_LOADING = 14
OBS_FRONTEND_EVENT_EXIT = 12

OBS_COMBO_TYPE_LIST = 1
OBS_COMBO_FORMAT_STRING = 2

LOG_ERROR = 400
LOG_WARNING = 300
LOG_INFO = 200
LOG_DEBUG = 100

LOG_LINES = []


def script_log(level, message):
    LOG_LINES.append((level, message))
    sys.stderr.write('[obs log %d] %s\n' % (level, message))


# ------------------------------------------------------------- obs_data_t
class ObsData(object):
    def __init__(self):
        self.values = {}
        self.defaults = {}

    def get(self, key, fallback):
        if key in self.values:
            return self.values[key]
        return self.defaults.get(key, fallback)


def obs_data_create():
    return ObsData()


def obs_data_release(d):
    pass


def obs_data_set_default_int(d, k, v):
    d.defaults[k] = int(v)


def obs_data_set_default_string(d, k, v):
    d.defaults[k] = str(v)


def obs_data_set_default_bool(d, k, v):
    d.defaults[k] = bool(v)


def obs_data_set_int(d, k, v):
    d.values[k] = int(v)


def obs_data_set_string(d, k, v):
    d.values[k] = str(v)


def obs_data_set_bool(d, k, v):
    d.values[k] = bool(v)


def obs_data_get_int(d, k):
    return int(d.get(k, 0))


def obs_data_get_string(d, k):
    return str(d.get(k, ''))


def obs_data_get_bool(d, k):
    return bool(d.get(k, False))


# ----------------------------------------------------------- obs_properties
class ObsProperty(object):
    def __init__(self, kind, name, desc, extra=None):
        self.kind = kind
        self.name = name
        self.desc = desc
        self.extra = extra
        self.items = []
        self.callback = None


class ObsProperties(object):
    def __init__(self):
        self.props = []

    def add(self, p):
        self.props.append(p)
        return p


def obs_properties_create():
    return ObsProperties()


def obs_properties_add_int_slider(props, name, desc, mn, mx, step):
    return props.add(ObsProperty('int_slider', name, desc, (mn, mx, step)))


def obs_properties_add_int(props, name, desc, mn, mx, step):
    return props.add(ObsProperty('int', name, desc, (mn, mx, step)))


def obs_properties_add_bool(props, name, desc):
    return props.add(ObsProperty('bool', name, desc))


def obs_properties_add_list(props, name, desc, ctype, cformat):
    return props.add(ObsProperty('list', name, desc, (ctype, cformat)))


def obs_property_list_add_string(prop, name, value):
    prop.items.append((name, value))


def obs_properties_add_button(props, name, desc, callback):
    p = ObsProperty('button', name, desc)
    p.callback = callback
    return props.add(p)


# ------------------------------------------------------- frontend callbacks
CALLBACKS = []


def obs_frontend_add_event_callback(cb):
    CALLBACKS.append(cb)


def obs_frontend_remove_event_callback(cb):
    if cb in CALLBACKS:
        CALLBACKS.remove(cb)


def fire(event):
    """Test helper: dispatch like OBS's frontend does (from the 'OBS thread')."""
    for cb in list(CALLBACKS):
        cb(event)


if os.environ.get('STUB_NO_LAST_REPLAY') != '1':
    def obs_frontend_get_last_replay():
        return 'C:\\Videos\\Replay 2026-09-06 12-34-56.mkv'

    def obs_frontend_get_last_recording():
        return 'C:\\Videos\\2026-09-06 12-30-00.mkv'
