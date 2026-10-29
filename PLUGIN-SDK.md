# FreQ Plugin SDK — Ultimate Platform (U5)

FreQ plugins are **single-file Python programs** that receive broadcast
events from FreQ. Drop a `.py` file into the `plugins/` folder and it
runs — no installer, no build step, no manifest.

**v1 plugins are observe-only, by design.** A plugin sees what is playing
and can report back (log lines, status messages, its own files / HTTP
calls / scripts), but there is **no API to control playback**. A broken
plugin can cost at most its own background thread — never the audio path.

---

## Quick start (60 seconds)

1. Open the plugins folder: **Settings → 🧩 Plugins → `📂 Open folder`**
   (or create a `plugins/` folder next to `FreQ.exe` yourself).
2. Create a file named `my_first_plugin.py`:

   ```python
   PLUGIN_NAME = "My First Plugin"

   def on_track_started(ctx, song=None):
       ctx.notify(f"Now on air: {song.get('title', '?')}")
   ```

3. Click **`🔄 Reload`** in the Settings panel (or restart FreQ).
4. Flip the switch next to your plugin if it is off.
5. Play a song — your message appears on the status line.

---

## Where plugins live

| Situation | Folder |
|---|---|
| Installed app (`.exe`) | `plugins\` **next to `FreQ.exe`** |
| Running from source | `plugins\` next to `gui.py` |
| Shipped examples | `plugins\examples\` — **not scanned**, copy files up one level to activate |

Discovery rules: only `*.py` directly inside `plugins\` is scanned
(subfolders are ignored — handy for parking examples and drafts), and
file names starting with `_` are skipped.

---

## The plugin contract

Every hook is **optional**. Define only the events you care about.

```python
PLUGIN_NAME = "Human readable name"     # shown in Settings (default: file name)
PLUGIN_VERSION = "1.0"                  # shown as "v1.0" (default: "0")

def on_start(ctx):
    """FreQ has launched and loaded plugins (after settings restore)."""

def on_track_started(ctx, song=None):
    """A track (song, jingle, mic segment, ...) started playing."""

def on_track_finished(ctx, song=None):
    """The previously playing track finished naturally or was stopped."""

def on_shutdown(ctx):
    """FreQ is closing. Do quick cleanup here."""
```

Event names are exact — anything else (`on_play`, `on_click`, ...) is
never called. Unknown/extra functions in your file are simply ignored,
which keeps older plugins compatible as new events ship.

### The `ctx` object — everything a plugin can do

| Call | What it does |
|---|---|
| `ctx.log(msg)` | Append a line to FreQ's plugin log (visible in the log file). |
| `ctx.notify(msg)` | Show a short message on the app's status line. Keep it short — it is a one-line ticker, not a messagebox. |
| `ctx.snapshot()` | Read-only dict of the current playback state (see below). |

`ctx.snapshot()` returns:

```python
{
    "playing": True,          # is a track currently playing?
    "queue_length": 12,       # items waiting in the queue
    "current_index": 3,       # position of the current item
    "volume": 0.8,            # master volume 0.0–1.0 (string value from the fader)
    "plugin_api": 1,          # plugin API version — always present
}
```

### The `song` payload

`song` is a **plain dict copy** — a read-only snapshot, never the live
queue object. Treat it as immutable:

| Key | Meaning |
|---|---|
| `title` | Track title ("Microphone ×2" for multi-mic segments) |
| `artist` | Artist / "Live voice" for mic segments |
| `source` | `"file"`, `"youtube"`, `"mic"`, `"jingle"`, ... |
| `duration` | Seconds (float) |
| `duration_str` | Preformatted `mm:ss` |
| `id` | Queue item id |

---

## The safety model (why plugins can't break your broadcast)

* **Load failure ≠ app failure.** A file with a syntax error is skipped
  and listed as "failed to load" in Settings — the app runs on.
* **Every hook is wrapped.** An exception inside your hook never escapes;
  it is logged with your plugin name.
* **3 strikes → auto-disable.** Three consecutive failing hook calls and
  the plugin is switched off automatically (the Settings row shows
  `⚠ 1/3`, `⚠ 2/3` while it burns its chances). Fix and press Reload.
* **Slow code cannot stall the app.** Hooks run on a background daemon
  thread, one event dispatched at a time. The UI and the audio engine
  never wait for your plugin. Keep handlers fast anyway — a hung webhook
  delays your *next* events, and timeouts count as failures.
* **State resets on Reload.** Module-level variables survive the session
  but a Reload/restart re-imports the file fresh. Persist anything that
  matters to your own file (see Example 2).

> ⚠️ **Observe-only is an API guarantee, not a security sandbox.** Plugin
> code runs with the same rights as FreQ itself — it can read files, use
> the network and run subprocesses. Only install plugins you wrote or
> trust.

### Managing plugins (Settings → 🧩 Plugins)

* Switch per plugin — on/off takes effect immediately, and the choice is
  saved into the `.freq` preset (survives restarts).
* `🔄 Reload` rescans the folder: **new** files are loaded, already-loaded
  plugins keep their on/off state.
* Failed files show ❌ with the file name; the status line counts
  loaded vs. failed.

---

## Example 1 — Now-Playing webhook (HTTP POST per track)

Posts a JSON payload to any endpoint every time a track starts. Great
for station websites, Discord bots, or home automation. A ready-to-copy
copy ships at `plugins/examples/example_webhook.py`.

```python
"""POST a now-playing JSON to a webhook on every track start."""
PLUGIN_NAME = "Now Playing Webhook"
PLUGIN_VERSION = "1.0"

WEBHOOK_URL = "https://webhook.site/your-id-here"   # your endpoint
TIMEOUT = 3.0                                       # keep it short!


def on_track_started(ctx, song=None):
    song = song or {}
    payload = {
        "event": "track_started",
        "title": song.get("title", ""),
        "artist": song.get("artist", ""),
        "source": song.get("source", ""),
        "duration": song.get("duration", 0.0),
        "playing": bool(ctx.snapshot().get("playing")),
    }
    try:
        import json
        from urllib.request import Request, urlopen

        data = json.dumps(payload).encode("utf-8")
        request = Request(WEBHOOK_URL, data=data,
                          headers={"Content-Type": "application/json"})
        urlopen(request, timeout=TIMEOUT).read()
        ctx.log(f"webhook sent: {payload['title']}")
    except Exception as error:
        ctx.log(f"webhook failed (will retry next track): {error}")


def on_shutdown(ctx):
    try:
        import json
        from urllib.request import Request, urlopen

        data = json.dumps({"event": "shutdown"}).encode("utf-8")
        urlopen(Request(WEBHOOK_URL, data=data,
                        headers={"Content-Type": "application/json"}),
                timeout=TIMEOUT).read()
    except Exception:
        pass
```

**Test tip:** open `https://webhook.site`, paste your unique URL into
`WEBHOOK_URL`, reload, play a song — watch the request land.

---

## Example 2 — Daily on-air log (your own playlist history)

Appends one line per track to a daily file (`onair_2026-09-25.log`), so
you always have proof of what aired — useful for royalty reports or
"what did we play last Tuesday".

```python
"""Write a daily on-air log: one line per track, UTF-8, per-day files."""
import datetime as _dt
from pathlib import Path

PLUGIN_NAME = "Daily On-Air Log"
PLUGIN_VERSION = "1.0"

# Where logs go. Keep them OUTSIDE the plugins folder (subfolders of
# plugins\ are not scanned, but a sibling folder is even cleaner).
LOG_DIR = Path.home() / "FreQLogs"


def _log_file() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    return LOG_DIR / f"onair_{today}.log"


def _stamp() -> str:
    return _dt.datetime.now().strftime("%H:%M:%S")


def on_track_started(ctx, song=None):
    song = song or {}
    line = (f"{_stamp()}\tSTART\t{song.get('title', '?')}\t"
            f"{song.get('artist', '')}\t{song.get('source', '')}\t"
            f"{song.get('duration_str', '')}\n")
    try:
        with open(_log_file(), "a", encoding="utf-8") as handle:
            handle.write(line)          # flush happens on close
        ctx.log(f"logged: {song.get('title', '?')}")
    except OSError as error:
        ctx.log(f"on-air log write failed: {error}")


def on_track_finished(ctx, song=None):
    song = song or {}
    try:
        with open(_log_file(), "a", encoding="utf-8") as handle:
            handle.write(f"{_stamp()}\tEND\t{song.get('title', '?')}\n")
    except OSError:
        pass


def on_start(ctx):
    try:
        with open(_log_file(), "a", encoding="utf-8") as handle:
            handle.write(f"{_stamp()}\tSESSION\tFreQ started\n")
    except OSError:
        pass
```

Why open/close per line? Your hook may run minutes after the previous
one — reopening the file each time means the log survives crashes and
never holds a handle while idle. Events arrive one at a time on the
dispatcher thread, so appends cannot interleave.

---

## Example 3 — Trigger an external script (subprocess bridge)

Runs an external script on every track start and hands it the track
title on the command line. This is the bridge when your real logic
lives in another language — a `.bat`, PowerShell, Node.js, C# — anything
executable. FreQ still only ever loads the Python file.

```python
"""Run an external script per track, passing the now-playing title.

Windows example below calls a .bat; swap the command for PowerShell,
Node, Python 2, whatever you like. The script runs with the same
rights as FreQ — only point this at scripts you trust.
"""
import subprocess
from pathlib import Path

PLUGIN_NAME = "Track Script Bridge"
PLUGIN_VERSION = "1.0"

SCRIPT = r"C:\Radio\scripts\now_playing.bat"   # your script
TIMEOUT = 5.0                                  # seconds, then we give up


def on_track_started(ctx, song=None):
    song = song or {}
    title = song.get("title", "")
    try:
        completed = subprocess.run(
            [SCRIPT, title],                 # argv: script + title
            capture_output=True, text=True,
            timeout=TIMEOUT, check=False,
        )
        if completed.returncode == 0:
            ctx.log(f"script ok: {completed.stdout.strip()[:80]}")
        else:
            ctx.log(f"script exit {completed.returncode}: "
                    f"{completed.stderr.strip()[:120]}")
    except subprocess.TimeoutExpired:
        ctx.log(f"script timed out after {TIMEOUT:g}s")
    except OSError as error:
        ctx.log(f"script could not start: {error}")
```

Notes for the bridge pattern:

* **Always set `timeout`.** A script that never exits would burn your
  plugin's 3-strike budget and stall subsequent events.
* Prefer **`subprocess.run(..., capture_output=True)`** (waits, bounded)
  over bare `Popen` fire-and-forget — orphaned processes pile up across
  a long broadcast day.
* Pass data via **argv** (shown) or environment variables; avoid shell
  string interpolation with track titles — titles can contain quotes
  and `&`, which is why the list form (`[SCRIPT, title]`) is used: no
  shell is involved.
* The example calls a `.bat` directly. For `.ps1` use
  `["powershell", "-NoProfile", "-File", SCRIPT, title]`; for Node use
  `["node", SCRIPT, title]`.

---

## Testing & iteration workflow

1. Run FreQ **from source** while developing (`python gui.py`) — restart
   is seconds, logs are in your console.
2. Use `ctx.log` liberally; check the log file (or console when running
   from source) to see exactly what your hook received.
3. `🔄 Reload` after every edit — no need to restart. Remember module
   state resets on each reload.
4. Burned your 3 strikes? The plugin is auto-disabled: fix the bug, press
   Reload, flip the switch back on.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Plugin not listed | Wrong folder; missing `.py` extension; name starts with `_`; file inside a subfolder |
| ❌ failed to load | Syntax error / bad import at module level — fix and Reload |
| Switch keeps turning off | 3 consecutive hook failures auto-disable it; check the log for your plugin's name |
| Events seem delayed | A previous hook is slow (webhook timeout, hanging script); shorten timeouts |
| `ctx.notify` shows nothing | Notifications overwrite each other on one status line — use `ctx.log` for anything more than a transient ping |

## Versioning & compatibility

`PLUGIN_API_VERSION = 1`. Within v1: existing hooks and `ctx` methods
keep their signature; new hooks may be added (your plugin is never
required to implement them); `song`/`snapshot` dicts may gain new keys —
read keys with `.get()`, never unpack, and your plugin stays forward
compatible.
