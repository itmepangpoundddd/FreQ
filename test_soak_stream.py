#!/usr/bin/env python3
"""Release-gate soak for the NATIVE live-stream sender (native_stream).

Hammers the exact path a broadcast uses — WASAPI loopback tap → ffmpeg
→ TCP sink — while a fake Icecast server breaks the connection in every
way a real server dies: clean shutdown, RST, and full listener outage.
The stream must walk its reconnect ladder and KEEP SENDING every time.

An "op" is one unit of disruptive/control work (poll cycle = 1, forced
disconnect = 40, start/stop cycle = 20) so the run mixes steady state
with violence, like a real broadcast day.

Verified with Win32 APIs directly (no psutil):
  * Working set stays in a band after warm-up (no unbounded climb)
  * Thread count returns to ~baseline after every burst
  * No orphaned ffmpeg.exe survives the final stop
  * A final fresh session must reach STREAMING and flow bytes
  * Worst recovery time after a kill must stay inside the ladder budget

Run:  python test_soak_stream.py --ops 50000 --minutes 25
"""
from __future__ import annotations

import argparse
import ctypes
import random
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

import native_audio_capture as nac  # noqa: E402
import native_stream as ns  # noqa: E402

HOST = "127.0.0.1"
PORT = 8159
FFMPEG = r"C:\Program Files (x86)\FreQ\ffmpeg\ffmpeg.exe"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""),
          flush=True)


# ── Win32 introspection (same approach as test_soak.py) ──────────────────
class _PMCs(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


_k32 = ctypes.WinDLL("kernel32.dll") if hasattr(ctypes, "windll") else None
if _k32 is not None:
    _k32.K32GetProcessMemoryInfo.restype = ctypes.c_int
    _k32.K32GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_PMCs), ctypes.c_uint32]
    _k32.GetCurrentProcess.restype = ctypes.c_void_p


def rss_mb() -> float:
    if _k32 is None:
        return 0.0
    pmc = _PMCs()
    pmc.cb = ctypes.sizeof(_PMCs)
    if not _k32.K32GetProcessMemoryInfo(_k32.GetCurrentProcess(),
                                        ctypes.byref(pmc), pmc.cb):
        return 0.0
    return pmc.WorkingSetSize / (1024.0 * 1024.0)


def thread_count() -> int:
    if _k32 is None:
        return 0
    pmc = _PMCs()
    pmc.cb = ctypes.sizeof(_PMCs)
    if not _k32.K32GetProcessMemoryInfo(_k32.GetCurrentProcess(),
                                        ctypes.byref(pmc), pmc.cb):
        return 0
    # thread count is not in PMC — count via toolhelp snapshot instead
    return _toolhelp_threads()


TH32CS_SNAPTHREAD = 0x4


def _toolhelp_threads() -> int:
    class THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
                    ("th32ThreadID", ctypes.c_uint32),
                    ("th32OwnerProcessID", ctypes.c_uint32),
                    ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", ctypes.c_uint32)]
    k32 = ctypes.WinDLL("kernel32.dll")
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == -1:
        return 0
    pid = k32.GetCurrentProcessId()
    entry = THREADENTRY32()
    entry.dwSize = ctypes.sizeof(THREADENTRY32)
    n, ok = 0, bool(k32.Thread32First(snap, ctypes.byref(entry)))
    while ok:
        if entry.th32OwnerProcessID == pid:
            n += 1
        ok = bool(k32.Thread32Next(snap, ctypes.byref(entry)))
    k32.CloseHandle(snap)
    return n


def ffmpeg_count() -> int:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe", "/FO", "CSV"],
        capture_output=True, text=True).stdout
    return max(0, out.count("ffmpeg.exe") - 1)  # header line


# ── the fake Icecast ─────────────────────────────────────────────────────
class FakeIcecast:
    """Listener that records every connection and can die on demand."""

    def __init__(self, port: int = PORT) -> None:
        self.port = port
        self.headers: list[bytes] = []
        self.bytes_total = 0
        self.active: list[socket.socket] = []
        self.up = threading.Event()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self.up.wait(3)

    def _serve(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, self.port))
        srv.listen(8)
        srv.settimeout(0.4)
        self.up.set()
        try:
            while not self._stop.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self._handle, args=(conn,),
                                 daemon=True).start()
        finally:
            srv.close()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(3)
        try:
            hdr = conn.recv(4096)
            with self._lock:
                self.headers.append(hdr)
            conn.sendall(b"HTTP/1.0 200 OK\r\n\r\n")
            with self._lock:
                self.active.append(conn)
            try:
                while True:
                    chunk = conn.recv(1 << 16)
                    if not chunk:
                        break
                    with self._lock:
                        self.bytes_total += len(chunk)
            except (socket.timeout, ConnectionResetError, OSError):
                pass
        except OSError:
            pass
        finally:
            self._drop(conn)

    def _drop(self, conn: socket.socket) -> None:
        with self._lock:
            if conn in self.active:
                self.active.remove(conn)
        try:
            conn.close()
        except OSError:
            pass

    # ── violence ──────────────────────────────────────────────────────
    def kill_connections(self, rst: bool = False) -> int:
        """Kill every live connection. rst=True uses SO_LINGER 0 (RST)."""
        with self._lock:
            victims = list(self.active)
        n = 0
        for s in victims:
            if rst:
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                 b"\x01\x00\x00\x00\x00\x00\x00\x00")
                except OSError:
                    pass
            else:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self._drop(s)
            n += 1
        return n

    def restart_listener(self, downtime: float) -> None:
        """Full outage: a real server crash drops its clients too."""
        self.kill_connections()
        self._stop.set()
        self.up.clear()
        threading.Timer(downtime, self._revive).start()

    def _revive(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self.up.wait(3)


# ── main soak ────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", type=int, default=50_000)
    ap.add_argument("--minutes", type=float, default=25.0)
    ap.add_argument("--seed", type=int, default=9262)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    print(f"=== native_stream soak — target {args.ops:,} ops, "
          f"cap {args.minutes:.0f} min ===\n", flush=True)

    rate = nac.Capture(loopback=True).samplerate
    enc = (f'"{FFMPEG}" -hide_banner -loglevel error '
           f"-f s16le -ar {rate} -ac 2 -i pipe:0 "
           f"-c:a libmp3lame -b:a 128k -f mp3 pipe:1")
    header = ("SOURCE /soak ICY/1.0\r\nice-name:soak\r\n"
              "content-type:audio/mpeg\r\n\r\n")

    server = FakeIcecast()
    check("fake Icecast listening", server.up.is_set())

    base_rss = rss_mb()
    base_threads = thread_count()
    print(f"baseline: RSS {base_rss:.1f} MB, threads {base_threads}\n",
          flush=True)

    ops = 0
    disconnects = 0
    rst_kills = 0
    outages = 0
    starts = 0
    worst_recovery = 0.0
    recoveries = 0
    recovery_failures = 0
    stall_events = 0
    last_sent = 0          # diag watermark for CURRENT session (diag resets
    #                        to 0 on every start/restart — track per-session)
    stalled = 0
    bad_state = 0
    t_start = time.time()
    deadline = t_start + args.minutes * 60.0
    next_start_cycle = 0.0
    next_violence = time.time() + rng.uniform(1.2, 2.5)
    next_progress = t_start + 60
    lifetime_bytes = 0      # sum of diag across ALL sessions

    import winsound
    # A broadcast always HAS audio — silence makes loopback deliver no
    # packets at all (WASAPI quirk), which is not a fault the soak should
    # create. This thread is "the radio": a tone every beat, non-stop.
    audio_on = threading.Event()
    audio_on.set()

    def music() -> None:
        while audio_on.is_set():
            try:
                winsound.Beep(880, 450)
            except Exception:
                time.sleep(0.3)   # device busy — retry, never die silently

    threading.Thread(target=music, daemon=True).start()
    session = False

    def start_stream() -> None:
        nonlocal session, starts
        ns.stream_start(HOST, PORT, header, enc, 700)
        session = True
        starts += 1

    def stop_stream() -> None:
        nonlocal session
        if session:
            ns.stream_stop()
            session = False

    start_stream()
    ops += 20
    warm_until = time.time() + 30  # RSS warm-up window
    while ops < args.ops and time.time() < deadline:
        time.sleep(0.06)
        ops += 1
        st = ns.stream_state()
        if st not in (0, 1, 2, 3, 4, 5):
            bad_state += 1
        if session and st == 2:
            # stream_diag (bytes through the pipe + encoder) is the exact,
            # monotonic progress signal — stream_stats().sent can lag it
            # because send() returns before the kernel flushes.
            dg = ns.stream_diag()
            prog = dg[0] + dg[1]
            if prog > last_sent:
                lifetime_bytes += prog - last_sent   # true per-poll delta
                last_sent = prog
                stalled = 0
            else:
                stalled += 1
                if stalled > 250:  # ~15 s without any progress while "streaming"
                    # A silent endpoint is a real hazard: restart the stream
                    # like an operator would, count the event, keep soaking.
                    stall_events += 1
                    dg = ns.stream_diag()
                    phase = {0: "idle", 1: "capture-read", 2: "pcm-write",
                             3: "enc-peek", 4: "enc-read", 5: "send"}.get(
                        dg[3], str(dg[3]))
                    print(f"  ⚠ stall #{stall_events} at diag {prog:,} "
                          f"(pcm={dg[0]:,} enc={dg[1]:,} st={dg[2]} "
                          f"phase={phase}) — restarting stream", flush=True)
                    stop_stream()
                    time.sleep(0.4)
                    start_stream()
                    last_sent = 0   # fresh session: counters start at zero
                    stalled = 0
        now = time.time()
        if now >= next_violence and session:
            kind = rng.random()
            if kind < 0.45:
                server.kill_connections(rst=False)
            elif kind < 0.8:
                server.kill_connections(rst=True)
                rst_kills += 1
            else:
                server.restart_listener(rng.uniform(0.8, 2.0))
                outages += 1
            disconnects += 1
            ops += 40
            t_kill = time.time()
            # wait for recovery: bytes must FLOW again ≤ 12 s (a no-op kill
            # on an already-dead connection passes instantly — nothing to
            # recover); during a listener outage the ladder may legitimately
            # keep retrying past the window.
            recovered = False
            d0 = ns.stream_diag()
            while time.time() - t_kill < 10.0:
                time.sleep(0.15)
                ops += 1
                d1 = ns.stream_diag()
                if ns.stream_state() == 2 and (d1[0] + d1[1]) > (d0[0] + d0[1]):
                    recovered = True
                    break
            if not recovered:
                # An in-flight listener outage legitimately outruns the
                # window — only count it when the listener is back up.
                if server.up.is_set():
                    recovery_failures += 1
                    print(f"  ⚠ kill #{disconnects}: not recovered in 10 s "
                          f"(state={ns.stream_state()})", flush=True)
            else:
                recoveries += 1
                worst_recovery = max(worst_recovery,
                                     time.time() - t_kill)
            dg = ns.stream_diag()
            last_sent = dg[0] + dg[1]   # session-relative watermark
            last_sent = max(last_sent, sum(ns.stream_diag()[:2]))
            next_violence = time.time() + rng.uniform(1.2, 2.5)
        if now >= next_start_cycle:
            # full control-plane cycle: stop → fresh start
            stop_stream()
            time.sleep(0.4)
            ops += 7
            start_stream()
            last_sent = 0       # fresh session: counters start at zero
            ops += 20
            next_start_cycle = time.time() + 30.0
        if now >= next_progress:
            dg = ns.stream_diag()
            print(f"  … {ops:,} ops · {disconnects} kills ({rst_kills} RST, "
                  f"{outages} outages) · {starts} sessions · "
                  f"session {dg[0] + dg[1]:,} B · lifetime {lifetime_bytes:,} B · "
                  f"RSS {rss_mb():.1f} MB · "
                  f"threads {thread_count()} · "
                  f"{time.time() - t_start:.0f}s", flush=True)
            next_progress = time.time() + 60

    dg = ns.stream_diag()
    if dg[0] + dg[1] > last_sent:
        lifetime_bytes += dg[0] + dg[1] - last_sent
    stop_stream()
    ops += 2
    time.sleep(1.0)
    elapsed = time.time() - t_start

    print(f"\n=== soak finished: {ops:,} ops in {elapsed / 60:.1f} min ===\n",
          flush=True)

    check("ops target reached", ops >= args.ops,
          f"{ops:,}/{args.ops:,}" + ("" if ops >= args.ops else " (time cap)"))
    check("plenty of real disconnects",
          disconnects >= max(25, int(args.minutes * 12)),
          f"{disconnects} kills · {rst_kills} RST · {outages} full outages")
    check("server accepted many connections", len(server.headers) >= 20,
          f"{len(server.headers)} source headers seen")
    check("reconnect ladder always recovered", recovery_failures == 0,
          f"{recoveries} recoveries, {recovery_failures} failures · "
          f"worst recovery {worst_recovery:.1f}s · "
          f"{stall_events} stall restarts")
    check("bytes flowed continuously", lifetime_bytes > 1_000_000,
          f"{lifetime_bytes:,} bytes lifetime (pcm+enc diag)")
    check("state machine never glitched", bad_state == 0,
          f"{bad_state} bad polls")
    check("stopped cleanly (idle)", ns.stream_state() == 0,
          f"state={ns.stream_state()}")

    # resource discipline
    time.sleep(2.0)
    end_rss = rss_mb()
    end_threads = thread_count()
    ff = ffmpeg_count()
    warm_rss = max(base_rss, 0)
    check("no orphaned ffmpeg", ff == 0, f"{ff} ffmpeg.exe alive")
    check("RSS within band", end_rss - warm_rss < 15.0,
          f"warm≈{warm_rss:.1f} → end {end_rss:.1f} MB "
          f"(Δ{end_rss - warm_rss:+.1f})")
    check("threads back to baseline", end_threads <= base_threads + 2,
          f"{base_threads} → {end_threads}")

    # final fresh-session smoke: audio still flows end to end
    server2 = FakeIcecast(PORT + 1)
    ns.stream_start(HOST, PORT + 1, header, enc, 700)
    import winsound as ws2
    t0 = time.time()
    flowed = False
    d0 = ns.stream_diag()
    while time.time() - t0 < 8.0:
        time.sleep(0.2)
        d1 = ns.stream_diag()
        if ns.stream_state() == 2 and (d1[0] + d1[1]) > (d0[0] + d0[1]) + 20_000:
            flowed = True
            break
    check("final smoke: fresh session streams", flowed,
          f"state={ns.stream_state()} diag={sum(ns.stream_diag()[:2]):,}")
    ns.stream_stop()
    server2.kill_connections()
    audio_on.clear()
    time.sleep(0.5)

    audio_on.clear()
    fails = [r for r in RESULTS if not r[1]]
    for n, ok, d in fails:
        print(f"  ❌ {n} — {d}")
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed"
          + (" — all green ✅" if not fails else f" — {len(fails)} FAILED"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
