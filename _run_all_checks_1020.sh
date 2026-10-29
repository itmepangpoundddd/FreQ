#!/usr/bin/env bash
# Run the verification set for the as-run CLI + folder-wide sealing work.
# Each suite is run in the foreground, its exit status captured BEFORE any
# output filtering, so a filtered log can never look like a pass.
set -u
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"

run() {
  name="$1"; shift
  log="_run_${name}.log"
  timeout 1500 "$@" > "$log" 2>&1
  rc=$?
  tail_line=$(tail -n 3 "$log" | tr -d '\r' | grep -E '[0-9]+/[0-9]+' | tail -n 1)
  printf '%-28s rc=%-3s %s\n' "$name" "$rc" "${tail_line:-$(tail -n 1 "$log" | tr -d '\r')}"
}

run edge_cases      python test_edge_cases.py
run gui_wiring      python test_gui_wiring.py
run ui_1016         python test_ui_1016.py
run smoke_asrun     python _smoke_asrun_1020.py
run smoke_asrun_cli python _smoke_asrun_cli_1020.py
run smoke_playlist  python _smoke_playlist_1020.py
run queue_times     python _smoke_queue_times_1019.py
run settings_persist python _smoke_settings_persist_1017.py
run master_chain    python _smoke_master_chain_1017.py
run queue_stability python _smoke_queue_stability_1018.py
run random_insert   python _smoke_random_insert_1020.py
run end_modes       python _smoke_end_modes_1021.py
run audio_editor    python _smoke_audio_editor_1027.py
run preview         python _smoke_preview_1029.py
run browse_multi    python _smoke_browse_multi_1026.py
run live_stream     python _smoke_stream_m3u8_1029.py
run queue_flags     python _smoke_queue_flags_1029.py
run studio_display  python _smoke_studio_1029.py
