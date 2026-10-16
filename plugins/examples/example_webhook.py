"""
Example FreQ plugin — Now-Playing Webhook (observe-only).

Copy this file into the plugins/ folder (next to FreQ.exe, or next to
gui.py when running from source) and FreQ will POST a short JSON message
to the webhook URL below every time a track starts.

Quick test:
  1. Open https://webhook.site, copy your unique URL into WEBHOOK_URL.
  2. Copy this file into plugins/.
  3. Start FreQ and play a song — watch the requests appear.

Everything here uses ONLY the documented PluginContext surface
(ctx.log / ctx.notify / ctx.snapshot). Plugins cannot control playback —
by design (Ultimate rule: the audio path stays untouchable).
"""

PLUGIN_NAME = "Now Playing Webhook"
PLUGIN_VERSION = "1.0"

# Where to POST the now-playing JSON. Any request-bin service works for
# testing; point it at your station's endpoint for real use.
WEBHOOK_URL = "https://webhook.site/00000000-0000-0000-0000-000000000000"

# Keep the fire-and-forget POST short — plugin hooks must never stall
# the app (the SDK disables plugins that fail 3 times in a row, and
# warns about slow handlers).
TIMEOUT = 3.0


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
        request = Request(
            WEBHOOK_URL, data=data,
            headers={"Content-Type": "application/json"},
        )
        urlopen(request, timeout=TIMEOUT).read()
        ctx.log(f"webhook sent: {payload['title']}")
    except Exception as error:
        ctx.log(f"webhook failed (will retry next track): {error}")


def on_shutdown(ctx):
    try:
        import json
        from urllib.request import Request, urlopen

        data = json.dumps({"event": "shutdown",
                           "by": PLUGIN_NAME}).encode("utf-8")
        urlopen(Request(WEBHOOK_URL, data=data,
                        headers={"Content-Type": "application/json"}),
                timeout=TIMEOUT).read()
    except Exception:
        pass
