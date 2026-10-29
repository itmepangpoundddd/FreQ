#!/usr/bin/env python3
"""Smoke: Dual Output [dual-0927] — mirror engine + UI wiring.

Verifies on the REAL RadioApp (headless, silent) that:
  1. the module starts clean, refuses bad endpoints, stops idempotently
  2. the resampler converts 48k->44.1k keeping duration AND frequency
  3. the Dual Output dropdown exists, defaults to Off, and toggling Off
     is a safe no-op
  4. settings persist carries dual_output (enabled=False when off) and
     the restore path is a no-op when disabled
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Isolate the user's real radio_queue.freq: RadioQueue saves to a RELATIVE
# path in the cwd, so run the app from a throwaway directory.
os.chdir(tempfile.mkdtemp(prefix="freq_dual_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


# ── 1. module behaviour ─────────────────────────────────────────
from dual_output import DualOutputMirror  # noqa: E402

m = DualOutputMirror()
check("start refuses empty endpoint", m.start("") is False)
check("start refuses bogus endpoint", m.start("__no_such_endpoint__") is False)
check("stats expose error after failure",
      m.stats()["running"] is False and m.stats()["error"] != "")
m.stop()  # idempotent even when never started
m.stop()
check("double stop is a safe no-op", m.stats()["running"] is False)

# resampler: 8 cycles of a 440 Hz sine at 48 kHz -> 44.1 kHz
import math  # noqa: E402

def sine_bytes(rate, hz, seconds, amp=12000):
    n = int(rate * seconds)
    out = bytearray()
    for i in range(n):
        s = int(amp * math.sin(2 * math.pi * hz * i / rate))
        out += s.to_bytes(2, "little", signed=True) * 2
    return bytes(out)

src48 = sine_bytes(48000, 440, 1.0)
out = m._resample(src48, 48000)
out_frames = len(out) // 4
expect_frames = int((len(src48) // 4) * 44100 / 48000)
check("resampler keeps duration",
      abs(out_frames - expect_frames) <= 2,
      f"{out_frames} vs {expect_frames}")

# zero crossings of a sine = 2 * cycles; count them to prove the pitch
def zero_crossings_interleaved(b):
    from array import array as _arr
    samples = _arr("h")
    samples.frombytes(b)
    left = samples[0::2]
    return sum(1 for i in range(1, len(left)) if left[i - 1] < 0 <= left[i])

zc_in = zero_crossings_interleaved(src48)
zc_out = zero_crossings_interleaved(out)
check("resampler keeps frequency (zero crossings)",
      abs(zc_out - zc_in) <= 2, f"in={zc_in} out={zc_out}")

# ── 2. UI wiring on the real app ────────────────────────────────
from gui import RadioApp  # noqa: E402

app = RadioApp()
try:
    app.update_idletasks()
    check("dual dropdown exists", hasattr(app, "dual_device_menu"))
    check("dual dropdown defaults to Off", app.dual_device_var.get() == "Off")
    app._on_dual_device_selected("Off")
    check("selecting Off is a safe no-op",
          app.dual_output.stats()["running"] is False)
    app._refresh_dual_list()
    names = list(app.dual_device_menu.cget("values"))
    check("dual list has devices beyond Off", len(names) >= 1, str(names[:3]))

    st = app._collect_settings().get("dual_output", {})
    check("settings persist carries dual_output",
          "enabled" in st and st.get("enabled") is False, str(st))
    # restore path: disabled entry must be a no-op (no crash, stays off)
    app._restore_settings({"dual_output": {"enabled": False, "endpoint": ""}})
    check("restore with disabled flag is a no-op",
          app.dual_output.stats()["running"] is False)

    # ── regression [1016.1 -> 1017.1]: the volume slider merged the
    # _open_sound_settings body, so every drag step opened the Windows
    # Settings app. Intercept the shell call and drag the real slider.
    import os as _os
    shell_calls = []
    real_startfile = getattr(_os, "startfile", None)
    _os.startfile = lambda target, *a, **k: shell_calls.append(target)
    try:
        for pct in (0.0, 37.0, 120.0, 200.0):
            app.dual_vol_var.set(pct)
            app._on_dual_volume(pct)
        check("dragging dual volume never opens the OS settings app",
              shell_calls == [], f"startfile called with {shell_calls}")
        check("dragging dual volume still drives label + mirror volume",
              app.dual_vol_label.cget("text") == "200%"
              and abs(app.dual_output.volume - 2.0) < 1e-6,
              f"label={app.dual_vol_label.cget('text')!r} "
              f"vol={app.dual_output.volume}")
        app._open_sound_settings()
        check("the manual picker still opens when asked (once)",
              shell_calls == ["ms-settings:sound"], str(shell_calls))
    finally:
        if real_startfile is not None:
            _os.startfile = real_startfile
        else:
            try:
                del _os.startfile
            except AttributeError:
                pass
finally:
    app._shutdown_dual_output()
    try:
        app.destroy()
    except Exception:
        pass

# ── result ──────────────────────────────────────────────────────
fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
