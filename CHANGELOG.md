# Changelog

## 2.0.0 - 2026-09-06

Rewrite of [shadowxdgamer/obs_recording_notification](https://github.com/shadowxdgamer/obs_recording_notification).

- Arm in `script_load`; no OBS restart needed after adding the script.
- Remove the accumulating polling loop; one queued callback per event.
- Queue notifications that arrive during an animation; merge identical bursts with a count.
- Add `script_unload` and clean teardown on the Tk thread; fixes the crash on reload/exit.
- Guard every cross-thread call so the OBS UI thread never blocks on Tk or receives an exception.
- Never steal keyboard focus (WS_EX_NOACTIVATE, foreground restored after load).
- Use RECORDING_STARTED instead of STARTING; label stop as "Recording Stopped".
- Size the window from its content; configurable corner.
- Settings panel: duration, corner, per-event toggles, file name (OBS 29+), test button.
- Test harness with a stubbed `obspython` module.
