#!/usr/bin/env bash
# Full regression sweep with the freshly rebuilt native modules.
# Each suite runs in the foreground; rc is captured BEFORE any filtering so a
# pretty log can never be mistaken for a pass. Soak suites are excluded (they
# run for 20+ minutes by design) — run them separately.
set -u
export PYTHONIOENCODING=utf-8
cd "$(dirname "$0")"
LOG=_sweep_1029.log
: > "$LOG"
run() {
  name="$1"; shift
  start=$(date +%s)
  out=$(timeout 600 python "$@" 2>&1); rc=$?
  secs=$(( $(date +%s) - start ))
  printf '%-22s rc=%-3s %4ss  %s\n' "$name" "$rc" "$secs" \
    "$(printf '%s\n' "$out" | tr -d '\r' | grep -E '[0-9]+/[0-9]+ (checks )?passed|checks passed|Summary: [0-9]+/[0-9]+' | tail -1)" | tee -a "$LOG"
  printf '%s\n' "$out" > "_sweep_${name}.log"
  return $rc
}
fails=0
for suite in \
  test_edge_cases.py test_gui_wiring.py test_ui_1016.py \
  _smoke_asrun_1020.py _smoke_asrun_cli_1020.py _smoke_playlist_1020.py \
  _smoke_queue_times_1019.py _smoke_settings_persist_1017.py \
  _smoke_master_chain_1017.py _smoke_queue_stability_1018.py \
  _smoke_random_insert_1020.py _smoke_end_modes_1021.py \
  _smoke_audio_editor_1027.py _smoke_browse_multi_1026.py \
  _smoke_stream_m3u8_1029.py _smoke_dual_0927.py _smoke_heal_0928.py \
  _smoke_select_1017.py _smoke_marker_0926.py _smoke_split_0928.py \
  _smoke_queue_nav_1018.py _smoke_wheel_1017.py _smoke_freq_roundtrip_1017.py \
  _smoke_playlist_first_1017.py _smoke_yt_mix_1017.py \
  _smoke_supervisor_0926.py _smoke_watchdog_0926.py _smoke_sup_0928.py \
  test_plugin_system.py test_silence_sentinel.py test_combo_aircheck_stream.py \
  test_jingle_stress.py test_multi_mic.py test_pause_storm.py ; do
  rg="${suite%.py}"
  run "$rg" "$suite" || fails=$((fails+1))
done
printf 'FAILED SUITES: %s\n' "$fails" | tee -a "$LOG"
