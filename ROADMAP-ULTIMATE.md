# FreQ Ultimate Series — Roadmap

> อัปเดตล่าสุด: 2026-10-18
> **Ultimate Series** คือสายแฟล็กชิปของ FreQ — ความสามารถระดับสถานีวิทยุอาชีพ
> แยกจากสาย **Stable Pack** (SP1–SP3) ซึ่งเน้นความเสถียรภาพของเวอร์ชันปัจจุบัน
> ดูแผนระยะใกล้ได้ที่ `ROADMAP.md`

### 🏷 รูปแบบเวอร์ชันของสาย Ultimate

ตั้งแต่ปี 2026 ทุกรุ่นใช้เลขรูปแบบ **"FreQ Ultimate yyyy (Build mmdd.รอบ)"**

- `yyyy` = ปีที่ออก (ทำหน้าที่เป็นเวอร์ชันหลัก) · `mmdd` = เดือน+วันที่ build · ตัวสุดท้าย = **รอบที่อัปเดตของวันนั้น** (1, 2, 3, …)
- ในไฟล์ build/tag ใช้รูปแบบตัวเลขที่เรียงลำดับได้: `yyyy.mmdd.รอบ` เช่น `2026.0924.1` = Ultimate 2026 (Build 0924.1)
- bump ครั้งเดียวจบ: `python release.py --set-version 2026.0924.1` — ระบบ mirror เป็น display/numeric/version.txt ให้ครบทุกไฟล์เอง

### ฐานที่มีอยู่จริง (สิ่งที่ Ultimate ต่อยอดได้ทันที)

- เอนจินเสียง C++ เต็มรูปแบบ: render/decode/capture/meter/output/stream — ผ่าน soak 134,619 ops โดยไม่รั่ว
- ไมค์สดเรียลไทม์สูงสุด 4 ตัว + per-mic gain + pause/resume (SP3)
- Streaming: Icecast / Shoutcast (FFmpeg) + WebRTC (aiortc)
- Web GUI, Mobile Remote (LiveLAN), scheduler, jingle engine, mixer 5 ช่อง + VU วัดจาก loopback จริง

### กติกาของทุกรุ่น Ultimate

1. ทุกรุ่นผ่านชุดเทสเดิมก่อนแจก (jingle stress, edge cases, soak ≥ 20k ops, ไม่มี Event Viewer crash)
2. ไม่แตะพฤติกรรมเดิมที่ทำงานอยู่ — ฟีเจอร์ใหม่ต้องอยู่ข้างของเดิม พร้อม fallback เสมอ
3. โหมดปกติต้องใช้ได้โดยไม่ต้องตั้งค่าอะไรเพิ่ม (opt-in สำหรับของหนัก)

---

## 📅 Build 2026.1018.1 — แผนรอบถัดไป (วาง 2026-10-18)

> ธีมรอบนี้: **"ปิดหนี้ให้เป็นศูนย์" (debt-zero build)** — **ไม่มีฟีเจอร์ใหม่แม้แต่รายการเดียว**
> งานทั้งรอบคือทำ 4 smoke ที่ค้างมาตั้งแต่ 1016.1 ให้เขียวครบ แล้ว **commit ของจริงก่อนออกรุ่น**
>
> กติกาใหม่ที่เพิ่มรอบนี้: **ห้าม bump เลขเวอร์ชันก่อน R0 ปิด** — ครั้งที่แล้ว bump `2026.1017.1`
> ทั้งที่ tree ยังค้าง 31 ไฟล์ ทำให้รุ่นที่ "ออกแจกแล้ว" ไม่มี commit รองรับเลย

### 📌 หลักฐานตั้งต้น (วัดจากการรันจริง 2026-10-18)

| งาน | สิ่งที่วัดได้วันนี้ | ผล |
|---|---|---|
| `_smoke_heal_0928.py` | รันจริง | โมดูล **18/18 ผ่าน** แล้วตายบรรทัด 162 — `'tkapp' object has no attribute 'sentinel_heal'` → 7 เคส GUI ยังไม่ถูกประเมิน · `grep sentinel_heal gui.py` = **0 ครั้ง** |
| `_smoke_split_0928.py` | รันจริง | โมดูล **26/26 ผ่าน** · **FAIL** "split checkbox exists and defaults OFF" · ตายบรรทัด 160 — ไม่มี `_aircheck_tracklog` · `grep aircheck_split / TrackLog gui.py` = **0** |
| `_smoke_sup_0928.py` | รันจริง | **8/16** — ผ่าน status file + hang→restart · แดง 5 เคส GUI (status label, refresh, open-log, populate, revive toast) + 3 เคส `installer.iss` (supervisor exe, entry, shortcut) |
| `_smoke_marker_0926.py` | รันจริง | **พัง** — `'CTkFrame' object has no attribute 'item'` (สคริปต์วินิจฉัยค้างรุ่น ยังไม่ถูกตัดสิน) |
| repo | `git status --porcelain` | **31 paths ค้าง** · `release.py`, `push_source_code.py`, `plugins/`, `templates/` + `_smoke_*_1017.py` 7 ไฟล์ **ยัง untracked** |
| `.gitignore` | อ่านบรรทัด 23 | `*.bat` — **ยังไม่มีข้อยกเว้น** ให้ `START FreQ (Auto-Restart).bat` |
| เลขเวอร์ชัน | `version.txt` + `installer.iss` | ตรงกันที่ **2026.1017.1** แล้ว (เลขไม่ใช่ปัญหา — การ track ไฟล์ต่างหาก) |

### 🧱 ตารางงานของรอบ 1018.1 (R0–R6)

| # | งาน | รายละเอียด | เกณฑ์ผ่าน |
|---|---|---|---|
| **R0** | **Commit tree ของ 1017.1 (ตัวบล็อกทุกอย่าง)** | commit งานที่ส่งจริงของ 1017.1 (gui / player / radio_manager / keys_tab / installer.iss / RELEASENOTES / ROADMAP + smoke ใหม่ 7 ตัว) · track `release.py`, `push_source_code.py`, `plugins/`, `templates/` · **แก้ `.gitignore`**: ใส่ `!START FreQ (Auto-Restart).bat` ต่อจากบรรทัด `*.bat` · ลำดับตายตัว: commit → ตรวจ `git status` → ค่อย push | `git status --porcelain` **ว่าง** · `git ls-files` เห็น `release.py`, `freq_supervisor.py`, `plugins/`, `templates/` ครบ · ด่าน "working tree clean" ของ `release.py publish()` ผ่าน (ตรวจโดยไม่ tag) |
| **R1** | **Sentinel self-heal เข้า Settings** (ยกจาก Q1 — ค้างรอบที่ 3) | สร้าง `self.sentinel_heal` ผูกกับ `silence_sentinel` เดิม · dropdown `sentinel_heal_mode_var` (off / dry_run / active, **default off**) · persist `settings["silence_sentinel"]["heal_mode"]` + restore ตอนเปิดไฟล์ · ต่อ callback จริงขั้นละ 1 จุด (skip → เปลี่ยนแทร็ก, restart → restart เอนจิน, alert → toast/log) · โชว์ขั้นปัจจุบัน/ผลครั้งล่าสุดจาก `stats()` | `_smoke_heal_0928.py` **25/25** · `test_silence_sentinel.py` เขียว · `test_ui_1016.py` **104/104** |
| **R2** | **Air-Check แยกแทร็กต่อสายไฟเข้า gui** (ยกจาก Q2) | `aircheck_split_var` (check box ในหน้า Air-Check, **default OFF**, ใช้ได้เฉพาะเมื่อมีเครื่องอัด) · `self._aircheck_tracklog = TrackLog()` · `_play_index` จด event เฉพาะตอนกำลังอัด · หยุดอัด → `split_recording(wav, log, progress=...)` แล้วรายงานเป็น log + toast (`N แทร็ก`) · ชื่อไฟล์ `track_001 - Artist - Title.wav` · retention เดิมเกลาต่อ · persist ลงไฟล์ตั้งค่า | `_smoke_split_0928.py` **33/33** · `test_combo_aircheck_stream.py` เขียว · ปิดสวิตช์แล้ว WAV ต้องเป็นก้อนเดียวเป๊ะ |
| **R3** | **Supervisor: หน้าจอสถานะ + แพ็กเข้า installer** (ยกจาก Q3) | gui อ่าน status file ของ `freq_supervisor.py` (state / restarts / ts) มาโชว์บน Settings + ปุ่ม "Open log" + toast "กู้คืนหลังค้าง" · `installer.iss` กำหนด entry ของ supervisor exe และให้ shortcut ตัวหลักเรียกผ่าน supervisor · `release.py verify` ตรวจ entry ใหม่ · **โหมด dev ต้องไม่ขึ้นเตือนหลอก** (ทำหลัง R0 เท่านั้น เพราะ bat ต้องเข้า git ได้ก่อน) | `_smoke_sup_0928.py` **16/16** · `_smoke_supervisor_0926.py` + `_smoke_watchdog_0926.py` ยังเขียว · `release.py verify` ผ่านใน bundle จริง |
| **R4** | **ตัดสิน `_smoke_marker_0926.py`** | ซ่อมให้รันได้ (อัปเดตตาม widget จริง) **หรือ** ลบทิ้งพร้อมจดใน RELEASENOTES ว่าถอดเพราะอะไร — ห้ามปล่อยค้างเป็นแดงที่ไม่มีเจ้าภาพเป็นรอบที่ 3 | ทางใดทางหนึ่งต้องได้ **rc=0** หรือไม่มีไฟล์เหลืออยู่ และชุดเทสกลางไม่ได้อ้างถึง |
| **R5** | **ของเล็กที่ค้างจริงจาก Q4** | (1) `Enable All` / `Disable All` ทั้ง 9 ขั้นของไมค์ chain — สลับ mode เท่านั้น **ห้ามแตะระดับเสียง** (2) Broadcast Check เพิ่ม 2 แถว: **สถานะสตรีม** (`streamer.state` + host) และ **Master chain** (`get_master_chain()`; OFF คือค่าที่ถูก ไม่ใช่ fail) ทั้งคู่ **read-only** | เทสใหม่ยืนยัน bulk toggle เขียน mode ครบ 9 ขั้นและค่าอื่นไม่ถูกแตะ · broadcast คืน **9 แถว** แบบ read-only · `test_ui_1016.py` 104/104 |
| **R6** | **ปิดเกณฑ์คุณภาพ + ออก 1018.1** | ชุดเทสกลาง + smoke ทั้งชุดรันคำสั่งเดียวจบ · soak ≥ 20,000 ops (RSS นิ่ง, เธรดกลับฐาน) · `release.py verify` ครบ entry · ออกอากาศจริง ≥ 8 ชม. เปิด split + heal(dry_run) + supervisor พร้อมกัน · แล้วค่อย `--set-version 2026.1018.1` → RELEASENOTES หัวใหม่เหนือ 1017.1 → installer → commit → push | ทุกชุดออก **0** (จับ exit code จริง) · `version.txt` = `installer.iss` = **2026.1018.1** · `_soak_1018.log` เก็บไว้ · ไม่มี Event Viewer crash ตลอด 8 ชม. |

### ลำดับทำงาน (phase)

1. **R0 ก่อนเสมอ และห้ามข้าม** — ตอนนี้ `release.py` เองยังไม่ติด git และ `publish()` จะปฏิเสธเพราะ tree ไม่สะอาด → เส้นทางออกรุ่น *ใช้ไม่ได้จริง* จนกว่า R0 จะปิด
2. **R1 / R2 ขนานกันได้** — คนละหน้าใน gui แต่ **commit แยกต่อสาย** เพื่อย้อนกลับได้ทีละเส้น
3. **R3 หลัง R0** — เพราะต้องพึ่งข้อยกเว้น `*.bat` ใน `.gitignore` ไม่งั้น supervisor ผ่านในเครื่อง dev แต่หายในเครื่องติดตั้ง
4. **R4 + R5 ปิดท้ายของโค้ด** — แตะหน้า Settings/Broadcast Check ที่ R1/R3 ใช้อยู่
5. **R6 ท้ายสุดเสมอ** — ไม่มีอะไรขึ้น installer โดยข้าม quality gate และไม่ bump เลขก่อนโค้ดสะอาด

### ⚠️ ความเสี่ยงที่จดไว้ล่วงหน้า

- **tree ค้าง 31 ไฟล์ = ความเสี่ยงอันดับหนึ่ง**: ถ้ารอบนี้ยังไม่ commit แล้วมีคนรัน `push_source_code.py` งาน 1017.1 ทั้งก้อนอาจหลุดจาก git แบบ commit `f8ce056` ที่เคยลบ 9 ไฟล์
- **heal ต้องไม่กลายเป็นผู้ควบคุมเสียง**: `dry_run` ต้องเรียก callback **0 ครั้ง** — ถ้าแดง ห้ามผ่อน assertion
- **split ปิดแล้วต้องเป๊ะ**: WAV ก้อนเดียวเหมือนก่อนทุกไบต์ ไม่ใช่ "ใกล้เคียง"
- **Broadcast Check ห้ามเปิด device**: แถวใหม่ต้องอ่านค่าที่มีอยู่แล้วเท่านั้น
- **`test_ui_1016.py` เป็นด่านกันถอย**: 104/104 ต้องคงอยู่ทุก R

---

## 📅 Build 2026.1017.1 — แผนรอบที่แล้ว (วาง 2026-10-17)

> **สถานะปิดรอบ (2026-10-18):** ออกแจกเป็นตัวเลข **2026.1017.1** แล้ว (`version.txt` = `installer.iss`)
> แต่เป็นรุ่น *บางส่วน* — ส่งจริง 7 งาน (master chain dialog, autosave persist, round-trip panel,
> queue selection, mouse wheel, playlist first-video, dual output slider) และ **ยังไม่มี commit รองรับ**
> · Q1–Q4 ยังค้างทั้งชุด → ยกไป R1–R5 ของ 1018.1

> ธีมรอบนี้: **"ปิดสัญญาที่ให้ไว้" (Wire-up completion)** — รอบ 1016.1 เอาของใหม่ขึ้น gui ได้ 2 สาย
> (Dual Output UI + Plugin Settings) แต่ยังเหลือ **3 สายที่โมดูลเสร็จแล้วแต่ gui ยังไม่ต่อสายไฟเลย**
> และมี smoke ที่รอรับงานอยู่แล้ว → รอบนี้คือรอบที่ทำให้ smoke ทั้งชุดเขียวครบ แล้วปิดเกณฑ์คุณภาพ
> **ไม่เพิ่มฟีเจอร์ใหม่ถ้าไม่จำเป็น**: งานใหม่ทุกรายการจะ "เปิดทาง" ให้ของที่สร้างไว้แล้วใช้งานได้จริง ไม่ใช่ของใหม่ลอย ๆ

### 📌 หลักฐานตั้งต้น (วัดจากการรันเทสจริง 2026-10-17 ไม่ใช่การอ่านโค้ด)

| งาน | สิ่งที่วัดได้ | ผล |
|---|---|---|
| `_smoke_dual_0927.py` | รันจริง | **12/12 ผ่าน** — สายนี้ปิดแล้ว (1016.1) |
| `test_ui_1016.py` | รันจริง | **44/44 ผ่าน** — ต้องไม่ถอยตลอดรอบ |
| `_smoke_heal_0928.py` | รันจริง (ไฟล์นี้มี 25 check site: โมดูล 18 + GUI 7) | โมดูล **18/18 ผ่าน** แล้ว**ตายที่บรรทัด 162** — `AttributeError: 'tkapp' object has no attribute 'sentinel_heal'` → เหลือ 7 เคส GUI ที่ยังไม่ถูกประเมิน |
| `_smoke_split_0928.py` | รันจริง (ไฟล์นี้มี 33 check site: โมดูล 26 + GUI/persist 7) | โมดูล **26/26 ผ่าน** แล้ว **FAIL 1 เคสที่บรรทัด 157** ("split checkbox exists and defaults OFF") + ตายที่บรรทัด 160 (`_aircheck_tracklog`) → เหลือ 6 เคสที่ยังไม่ถูกประเมิน |
| `_smoke_sup_0928.py` | รันจริง | **8/16** — status file/hang→restart ผ่าน; ที่เหลือคือ gui (status label, open-log, revive toast) + installer.iss 3 ข้อ |
| `_smoke_supervisor_0926.py` | รันจริง | **10/10 ผ่าน (rc=0)** — ตัว supervisor เองยังดี |
| `_smoke_watchdog_0926.py` | รันจริง | **5/5 ผ่าน (rc=0)** — watchdog ในโปรเซสยังดี |
| `_smoke_marker_0926.py` | รันจริง | **พัง** — `'CTkFrame' object has no attribute 'item'` (สคริปต์วินิจฉัยค้างรุ่น) |
| เลขเวอร์ชัน | `version.txt` + `installer.iss` | ตรงกันที่ **2026.1016.1** แล้ว |
| ไฟล์ใน git | `git status` / `git show f8ce056` | `release.py`, `push_source_code.py`, `plugins/`, `templates/` **ยัง untracked** และ commit `f8ce056` เคย **ลบ 9 ไฟล์เหล่านี้ออกจาก git** แล้วปล่อยให้กลับมาเป็น untracked |
| Broadcast Check | อ่าน `_broadcast_check_rows` (บรรทัด 8010) | มี 7 แถว (Queue/Mics/Output/Loopback/Mic filters/Plugins/Disk) — **ยังไม่มีแถวสตรีม และไม่มีแถว master chain** ทั้งที่ค่าเหล่านี้มีอยู่แล้วในโค้ด |
| Enable/Disable All ไมค์ | grep ทั้ง gui.py | **ไม่มี** — ยังเป็นงานค้าง P2 ตัวจริง |
| `test_gui_wiring.py` (กันถอยใหม่, ไม่ต้องมีจอ) | รันจริง | **18/18 ผ่าน (rc=0)** — baseline หนี้ที่รู้อยู่ 14 รายการ (dead import 8 + ต่อสายไฟค้าง 6 ของ Q1/Q2) · โหมด `--strict` แดง 2 จุดตามที่ออกแบบ |
| `test_edge_cases.py` (suite หลัก) | รันจริง | **398/398 ผ่าน (rc=0, 48 วิ)** — release gate ตอนนี้รันทั้ง 2 ชุด |

### 🧱 ตารางงานของรอบ 1017.1 (Q0–Q6)

| # | งาน | รายละเอียด | เกณฑ์ผ่าน (เทสที่ต้องเขียว) |
|---|---|---|---|
| **Q0** | **ปิดรากฐาน repo ให้จบ** (งานยกจาก P0 ของ 1016.1) | commit `release.py` + `push_source_code.py` ให้เป็น tracked จริง · ตัดสิน `_smoke_marker_0926.py` (ซ่อมหรือลบ — ถ้าลบให้จดใน RELEASENOTES ว่าถอดเพราะเหตุใด) · ล้าง dead import 8 ตัวที่ `test_gui_wiring.py` ตั้ง baseline ไว้ (SongStatus, cached_icon, ScheduleEvent, CustomTitleBar, PlaybackModes, EditResult, FONT_SIZES, RADII) แล้วถอดออกจาก BASELINE_UNUSED_IMPORTS ให้หมด · **แก้ `.gitignore`**: ตอนนี้ `*.bat` ถูก ignore ทำให้ `START FreQ (Auto-Restart).bat` ของ supervisor **เข้า git ไม่ได้เลย** → ต้องมีบรรทัดยกเว้น (`!START FreQ (Auto-Restart).bat`) ไม่งั้นงาน Q3 พังเงียบ · **จดกติกาลำดับ**: `push_source_code.py` เคยลบไฟล์ dev ออกจาก git → ต้อง commit งานจริงให้เสร็จ **ก่อน** กด push และตรวจ `git status` ซ้ำหลัง push ทุกครั้ง | untracked เหลือ 0 (`git status --porcelain` ว่าง) · `python -m py_compile` ผ่าน · ยืนยันด้วย `git ls-files` ว่าไฟล์ที่เพิ่ง commit ติดจริง |
| **Q1** | **Sentinel self-heal ต่อสายไฟเข้า Settings** (งานยกจาก P1c) | สร้าง `self.sentinel_heal` ใน gui + ผูกกับ `silence_sentinel` ที่มีอยู่ · dropdown `sentinel_heal_mode_var` (off / dry_run / active, **default off** ตามกติกา U4) · persist `settings["silence_sentinel"]["heal_mode"]` + restore ตอนเปิดไฟล์ · ต่อ callback จริง 1 จุดต่อขั้น (skip → เปลี่ยนแทร็ก, restart → restart เอนจิน, alert → toast/log) · แสดงขั้นปัจจุบัน/ผลครั้งล่าสุดจาก `sentinel_heal.stats()` บนหน้า Settings · **dry_run ต้องไม่เรียก callback จริงแม้แต่ครั้งเดียว** | `_smoke_heal_0928.py` **25/25** (โฟกัสที่ 7 เคส GUI บรรทัด 161–185) · `test_silence_sentinel.py` ยังเขียว (sentinel ต้องเป็น observer เหมือนเดิม) · `test_ui_1016.py` 44/44 |
| **Q2** | **Air-Check แยกแทร็กต่อสายไฟเข้า gui** (งานยกจาก P1d) | เพิ่ม `aircheck_split_var` (check box ในหน้า Air-Check, **default OFF**, ใช้ได้เฉพาะเมื่อมีเครื่องอัด) · `self._aircheck_tracklog = TrackLog()` · ใน `_play_index` จด event ทุกครั้งที่แทร็ก/ไมค์เริ่ม **เฉพาะตอนกำลังอัด** · ตอนหยุดอัดเรียก `aircheck_split.split_recording(wav, log, progress=...)` แล้วรายงานผลเป็นบรรทัด log + toast (`N แทร็ก`) · ไฟล์ชื่อ `track_001 - Artist - Title.wav` · ใช้ retention เดิมเกลาต่อ · ปิดสวิตช์แล้วพฤติกรรมเดิมต้องเป๊ะ (WAV ก้อนเดียวเหมือนก่อน) · persist ลง .freq | `_smoke_split_0928.py` **33/33** (โฟกัสที่ 7 เคส GUI/persist บรรทัด 157–207) · `test_combo_aircheck_stream.py` ยังเขียว · `test_ui_1016.py` 44/44 |
| **Q3** | **Supervisor: หน้าจอสถานะ + แพ็กเข้า installer** (งานยกจาก P1b) | gui อ่าน status file ของ `freq_supervisor.py` (state / restarts / ts) มาโชว์บน Settings + ปุ่ม "Open log" + toast "กู้คืนหลังค้าง" เมื่อเปิดมาแล้วเจอว่าเพิ่งถูก revive · `installer.iss` กำหนด entry ของ supervisor exe, ให้ entry/shortcut ตัวหลักเรียกผ่าน supervisor · ให้ `release.py verify` ตรวจ entry ใหม่นี้ · **โหมด dev (ไม่ผ่าน supervisor) ต้องไม่ขึ้นคำเตือนหลอก** | `_smoke_sup_0928.py` **16/16** · `_smoke_supervisor_0926.py` + `_smoke_watchdog_0926.py` ยังเขียว · `release.py verify` ผ่านใน bundle จริง |
| **Q4** | **ของเล็กท้ายรอบที่ค้างจริง** (P2 ของ 1016.1) | (1) `Enable All` / `Disable All` ทั้ง 9 ขั้นของไมค์ chain ในไดอะล็อก Mic Filters — **ต้องไม่แตะระดับเสียง** แค่สลับ mode ของแต่ละขั้น (2) Broadcast Check เพิ่ม 2 แถว: **สถานะสตรีม** (`streamer.state` — idle/connecting/live/reconnecting/error พร้อม host) และ **Master chain** (จาก `audio_player.get_master_chain()` — รายงานว่าขั้นไหนเปิด/ระดับเท่าไร; OFF เป็นค่าที่ถูกต้อง ไม่ใช่ fail) — ทั้งคู่ต้องเป็น **read-only เหมือนแถวเดิม** (ห้ามเปิด device ห้ามย้ายคิว) | เทสใหม่เล็ก ๆ ยืนยัน bulk toggle เขียน mode ครบทั้ง 9 ขั้นและค่าที่ไม่ใช่ mode ไม่ถูกแตะ · ยืนยัน broadcast check คืน 9 แถวแบบ read-only · `test_ui_1016.py` 44/44 |
| **Q5** | **ปิดเกณฑ์คุณภาพ** | suite หลักครบชุด + `test_gui_wiring.py` (โหมด `--strict` ตอนปิดรอบ) + smoke ทั้ง 7 ตัว (`sup_0928` เป้า 16/16, `dual_0927` 12/12, `heal_0928` 25/25, `split_0928` 33/33, `supervisor_0926`, `watchdog_0926`, `marker_0926`) → รวมเป็นชุดเดียวที่รันคำสั่งเดียวจบ · soak ≥ 20,000 ops (RSS นิ่ง, เธรดกลับฐาน, start พลาด 0) · `release.py verify` ครบทุก entry point · **ออกอากาศจริง ≥ 8 ชม.** โดยเปิด split + heal(dry_run) + supervisor พร้อมกัน | ทุกชุดออก 0 (จับ exit code จริง ไม่กรองทิ้ง) · `release.py` test gate เขียวทั้ง 2 ชุด (edge_cases 398/398 + gui_wiring) · `_soak_1017.log` เก็บไว้เป็นหลักฐาน · ไม่มี Event Viewer crash ตลอด 8 ชม. |
| **Q6** | **ออกแจก 1017.1** | `python release.py --set-version 2026.1017.1` → RELEASENOTES ส่วนใหม่ (ต้องเขียนว่า **"ต่อสายไฟให้ครบ"** ไม่ใช่ปล่อยเป็นของใหม่) → `release.py` ออก installer ไป `_final/` → commit งานจริงก่อน แล้วจึง `push_source_code.py --publish` (tag + GitHub Release) | `version.txt` = `installer.iss` = **2026.1017.1** ตรงกัน · RELEASENOTES มีหัว `Build 2026.1017.1` อยู่เหนือ `1016.1` · `git status --porcelain` ว่างหลัง push |

### 🩹 แก้แล้วระหว่างรอบนี้ (ผู้ใช้รายงาน · ต้องยกเป็นหัวข้อใน RELEASENOTES ตอน Q6)

- **สไลเดอร์ Dual Output เปิดหน้า Settings ของ Windows เอง**: ตอน merge 1016.1 body ของ `_open_sound_settings` ไปรวมอยู่ใน `_on_dual_volume` → ทุกสเต็ปที่ลากสไลเดอร์เรียก `os.startfile("ms-settings:sound")` และเมธอด `_open_sound_settings` ที่บรรทัด 3927/3948 เรียกก็หายไป (AttributeError) — แยกกลับเป็นสองเมธอดแล้ว
- **เก๊กกันถอยที่คุมบั๊กนี้**: `test_ui_1016` เพิ่ม 5 เช็ค (ตรวจเฉพาะ *โค้ด* ไม่นับ docstring, จับ statement string ที่ค้างกลาง body, `_open_sound_settings` ต้องเป็นเมธอดของตัวเอง) · `_smoke_dual_0927` เพิ่ม 3 เช็คที่ดัก `os.startfile` จริงขณะลากสไลเดอร์ → **49/49** และ **15/15** · พิสูจน์ด้วยการใส่บั๊กกลับแล้วเทสแดงตรงจุด + คืนไฟล์ hash ตรงเดิม

### ลำดับทำงาน (phase)

1. **Q0 ก่อนเสมอ** — ถ้าไม่ปิดรากฐาน repo/`.gitignore` ก่อน งาน Q3 จะแพ็ก supervisor ไม่ผ่านแบบหาเหตุไม่เจอ (bat ถูก ignore เงียบ ๆ)
2. **Q1–Q3 ขนานกันได้** — ทั้งสามเป็นสายไฟคนละเส้น (heal / split / supervisor) ไม่ทับไฟล์กันในจุดเสี่ยง แต่ **ทุกสายต้องแตะ gui.py** → commit แยกต่อสายเพื่อให้ย้อนกลับได้ทีละเส้น
3. **Q4 ปิดท้ายของงานโค้ด** — ของเล็กลงหน้า Settings/Broadcast Check ที่แตะหน้าเดียวกับ Q1/Q3 (ทำหลังเพื่อไม่ให้ rebase กันเอง)
4. **Q5 ก่อน Q6 เสมอ** — ไม่มีอะไรขึ้น installer โดยข้าม quality gate

### กติกายังเหมือนเดิม

default OFF · ปิดแล้วทุกอย่างเดิมต้องเป๊ะ · เช็คถาวรลง suite หลักทุกชิ้น · ปรับค่าสดได้โดยไม่ต้องรีสตาร์ต

### ⚠️ ความเสี่ยงที่จดไว้ล่วงหน้า

- **`push_source_code.py` ลบไฟล์ dev ออกจาก git**: commit `f8ce056` ลบ `release.py`, `push_source_code.py`, `plugins/examples/*`, `templates/*` แล้วปล่อยให้กลับมา untracked → ลำดับคือ commit งานจริงให้ครบก่อน แล้ว push ทีหลัง และตรวจ `git status` ซ้ำหลัง push ทุกรอบ
- **`.gitignore` บล็อก `*.bat`**: supervisor ต้องพึ่ง bat → ถ้าไม่ยกเว้น งาน Q3 จะ "ผ่านในเครื่อง dev แต่หายในเครื่องติดตั้ง" ซึ่งเป็นความล้มเหลวชนิดที่เทสในเครื่องจับไม่ได้
- **heal ต้องไม่กลายเป็นผู้ควบคุมเสียง**: ค่า default คือ `off` และ `dry_run` ต้องเรียก callback 0 ครั้ง — ถ้าเทสข้อนี้แดง ห้ามผ่อน assertion ให้ผ่าน
- **Broadcast Check ห้ามกลายเป็นตัวเปิด device**: แถวใหม่ต้องอ่านค่าที่มีอยู่แล้วเท่านั้น

---

## 📅 Build 2026.1016.1 — แผนรอบที่แล้ว (วาง 2026-10-16)

> **สถานะปิดรอบ (2026-10-17):** P1a (Dual Output UI) และ P2-stretch (Plugin Settings) **ส่งครบ** — `_smoke_dual_0927` 12/12, `test_ui_1016` 44/44
> · P0 ทำได้ครึ่งเดียว (เลขเวอร์ชันรวมเป็น 2026.1016.1 แล้ว แต่การ track ไฟล์ยังไม่จบ) · P1b (heartbeat ใน gui) ทำแล้ว
> แต่การแพ็ก supervisor ยังไม่ทำ · P1c/P1d และ P2 **ยกไป Build 2026.1017.1 ทั้งชุด** (ดู Q1–Q4 ข้างบน)

> ธีมรอบนี้: **"รวมของที่สะสมเป็นรุ่นเดียว" (Consolidation build)** — ช่วง 0927–0930
> สะสมโมดูลใหม่ 4 ตัวมาแล้ว **แต่ยังไม่มีตัวไหนถูก import เข้า gui.py เลย** (ตรวจด้วย grep),
> git ยังไม่มี commit หลัง v2026.0926.2 (ไฟล์ untracked 17 ตัว) และเลขเวอร์ชันขัดกัน 2 จุด
> (`version.txt` = 2026.0927.1 แต่ `installer.iss` ยังขึ้น 2026.0926.2)
> Build นี้คือรอบเก็บกวาด + ต่อสายไฟให้ครบ + ออก installer รุ่นเดียวครบชุด

| # | งาน | รายละเอียด | สถานะปัจจุบัน (ก่อนแผน) |
|---|---|---|---|
| P0 | **เก็บกวาด repo และเวอร์ชัน** | commit ไฟล์ untracked ที่จำเป็น (`release.py`, `push_source_code.py`, `freq_supervisor.py`, `dual_output.py`, `sentinel_heal.py`, `aircheck_split.py`, `templates/`, `plugins/`) · สคริปต์วินิจฉัย `_*.py` ตัดสินเก็บไว้เทียบหรือลบทิ้ง · ปิดกั้น build artifact ด้วย .gitignore · เขียน RELEASENOTES รวมงานที่ค้างตั้งแต่รุ่น 0926.2 → 0927.x ให้ทันเวลา · ยึดเลขเวอร์ชันเดียวด้วย `release.py --set-version 2026.1016.1` (แก้ทั้ง version.txt/installer.iss ทีเดียว) | untracked 17 ตัว, notes ค้างตั้งแต่ 0926.1, เลขเวอร์ชันขัดกัน |
| P1a | **ต่อสายไฟสายสอง: Dual Output** | `dual_output.py` (mirror mix ทั้งรายการไปลำโพงที่สอง) ยังไม่ถูก import ใน gui.py เลย — เพิ่มหน้า Settings (เลือก device ที่สอง + สวิตช์ mirror), persist ลง .freq, default OFF, เพิ่มเช็คลง Broadcast Check | โมดูล + smoke `_smoke_dual_0927` ผ่านแล้ว / **gui 0%** |
| P1b | **ต่อสายไฟสายสาม: Supervisor** | จุดเสี่ยงที่สุด: `gui.py` **ไม่มี heartbeat ตามสัญญา** — supervisor รอให้ gui เขียนไฟล์ heartbeat (`UiFreezeWatchdog` / `FREQ_SUPERVISED`) ทุกวินาที แต่ grep ใน gui.py ไม่เจอทั้งสองชื่อ → supervisor จะตัดสินว่าลูกค้างแล้วรีสตาร์ตซ้ำโดยไม่มีข้อมูลจริง งานรอบนี้: เพิ่ม UiFreezeWatchdog + heartbeat thread ใน gui.py, พก `freq_supervisor.py` + bat/Start-menu ผ่าน installer (release.py verify), smoke sup ขึ้น suite หลัก | supervisor + smoke 2 ตัวผ่าน / **สัญญา heartbeat ใน gui ยังไม่มี / installer ยังไม่แพ็ก** |
| P1c | **Sentinel self-heal ใช้งานได้จริง** | `sentinel_heal.py` ladder (off / dry_run / active) ทำงานเดี่ยวได้แต่ไม่มีทางเปิดให้ผู้ใช้ — เพิ่มหน้า Settings (เลือกโหมด + ผลสถานะ), default OFF ตามกติกา U4, callback ต่อเข้า player/engine จริง 1 จุดต่อขั้น, structural test ของ sentinel เดิมต้องยังเขียว | โมดูล + smoke `_smoke_heal_0928` ครบ 4 เคสผ่าน / **UI + การต่อ audio callbacks ยังไม่เริ่ม** |
| P1d | **Air-Check แยกแทร็กอัตโนมัติ** | ปิดงานค้างข้อ 3 ของแผน 0926.2: GUI จด TrackLog ทุกครั้งที่แทร็กเริ่ม (kind/title/artist/offset), ตอนหยุดการอัดเรียก `aircheck_split.split_recording` ตัด WAV เป็นไฟล์ต่อแทร็กแบบ stream copy, ชื่อไฟล์ `track_001 - Artist - Title.wav`, เกลาไฟล์เก่าตาม retention เดิม | โมดูล + smoke `_smoke_split_0928` ผ่าน / **GUI ยังไม่จด TrackLog** |
| P2 | **ของเล็กท้ายรอบ** (ยกจากแผน 0926.2 ข้อ 5) | Enable All / Disable All ทั้งไมค์ chain · Broadcast Check เพิ่มตรวจสถานะสตรีม + master chain | ยังไม่เริ่มจากรอบหลัง |
| P2-stretch | Plugin Settings หน้าจอ | จัดการปลั๊กอินรายตัวจากหน้า Settings (สถานะ + ปุ่ม on/off ตาม .freq) — ถ้าเวลาไม่พอเลื่อนไปรอบถัดไปเหมือนที่เคยทำมาก่อน | U5 ขั้นถัดไปจาก 0924.1 |
| P3 | **ปิดเกณฑ์คุณภาพ** | suite หลักครบ: `test_jingle_stress` + `test_edge_cases` + `test_silence_sentinel` + `test_plugin_system` + `test_soak_stream` (12/12) + smoke sup/dual/heal/split ทั้งชุด · soak ≥ 20,000 ops (RSS นิ่ง, เธรดกลับฐาน) · `release.py verify` ครบทุก entry point ใน bundle จริง · ออกอากาศจริง ≥ 8 ชม. (เกณฑ์ Ultimate) | ยังไม่เริ่ม |
| P4 | **ออกแจก** | `python release.py --set-version 2026.1016.1` → RELEASENOTES ส่วนใหม่ (รวมยอดสะสม 0927–1016) → `python release.py` ออก installer ไปที่ `_final/` · `push_source_code.py` กด source ก่อน, แล้ว `--publish` (GitHub Release + tag) | pipeline พร้อมแล้วแต่ยัง untracked |

### ลำดับทำงาน (phase)

1. **P0 เก็บกวาดก่อนเสมอ** — ปิดรากฐาน repo/เลขเวอร์ชันให้นิ่งก่อนเริ่มงานใหม่ ไม่เช่นนั้นงานใหม่จะถูกออกแจกโดยโค้ดยังไม่ผ่าน commit
2. **P1 สี่งานทำขนานกันได้** (แต่ทุกสายต้องผ่านเกณฑ์ P3 ก่อนขึ้น installer)
3. **P3 เช็คเสร็จแล้วจึงปิด P4** — ไม่มีอะไรขึ้น installer โดยข้าม quality gate

### กติกายังเหมือนเดิม

default OFF · ปิดแล้วทุกอย่างเดิมต้องเป๊ะ · เช็คถาวรลง suite หลักทุกชิ้น · ปรับค่าสดได้โดยไม่ต้องรีสตาร์ต

---

## 📅 Build 0926.2 — แผนรอบถัดไป (วาง 2026-09-26)

> ธีมรอบนี้: **"ทั้งสถานีผ่าน chain อาชีพ"** — 0926.1 ให้ chain 9 ขั้นแก่ไมค์ไปแล้ว
> รอบนี้ต่อยอดโครง param-store + two-pane dialog ไปที่เสียงออกอากาศทั้งระบบ
> และปลดล็อกการส่งสตรีม mix จริงออกไปหาผู้ฟัง

| # | งาน | รายละเอียด | ต่อยอดจาก | เป้า U |
|---|---|---|---|---|
| 1 | **Master Broadcast Chain** (native C++) | EQ → Compressor → Limiter บน **master mix** ก่อนออกลำโพง/สตรีม — dialog two-pane + Immediate mode + Gentle/Strong ใช้โครงของไมค์ทั้งหมด, per-stage sliders, default OFF = เสียงดิบทุกไบต์ | param store + 2-pane dialog (0926.1) | U1 |
| └ สถานะ | **เสร็จแล้ว (0926.2)**: ในเอนจิน render (`master_process` — ครบทั้ง 3 เส้นทาง output รวม voice-over-paused-music), params เป็น lock-free atomics, C++ self-test พิสูจน์ DSP ไม่ต้องมี device (identity/EQ/comp/limiter), dialog OBS-style + Immediate mode อยู่หน้า Settings, persist ลง .freq, re-push หลัง engine swap, verify ใน release.py — แผน 0926.2 **ครบทั้ง 3 phase แล้ว** | |
| 2 | **สตรีม mix จริง** (Icecast/SHOUTcast) | streaming.py ตอนนี้เดินไฟล์อย่างเดียว — ต่อ mix tap ของเอนจิน → `FFmpegStreamer.write_audio` ให้ผู้ฟังได้ยินไมค์สด + เพลง + chain ครบทั้งชุด, ICY metadata จากคิวอัตโนมัติ, reconnect ladder เดิม | streaming.py + loopback/mix tap + self-heal (0926.1) | U1→U2 |
| └ สถานะ | **ฐาน native เสร็จแล้ว (0926.2 เริ่มก่อนกำหนด)**: `native_stream.cpp` — เธรด C++ เดี่ยวทำครบทั้งเส้น tap loopback → ffmpeg (stdin/stdout pipes) → TCP sink (ส่ง SOURCE header + TCP_NODELAY + SO_SNDTIMEO) → reconnect ladder ในตัว (delay จากผู้เรียก, ปิด sink จริงแล้วกลับมา streaming ได้ใน probe); `native_aircheck.cpp` — อัด WAV ใน C++ ล้วน (header patch ตอน stop = ไฟล์ไม่พังถ้าตายกลางทาง) พร้อม peak meter; **verify ใน release.py ทั้ง 2 จุด + แพ็กเข้า installer แล้ว**, probe จริงผ่าน 14/14 (WAV ตรงเป๊ะทุกเฟรม / reconnect จริงเมื่อ sink หลุด); **soak kill-storm 50,000 ops ผ่าน**: 668 forced disconnects (clean/RST/144 full outages), 47 sessions → **668/668 recoveries ศูนย์พลาด worst 2.1 s**, เสียงวิ่ง 118 MB, RSS นิ่ง +1.0 MB, ไม่มี ffmpeg ค้าง — soak จับ pipe deadlock + นโยบายเธรดค้าง แล้วแก้จบในรอบเดียว (stall monitor + thread abandon policy), เฮอร์เนสอยู่ที่ `test_soak_stream.py` (12/12) — ขั้นถัดไป: ผูก UI + metadata จากคิว | |
| 3 | **Aircheck อัตโนมัติเมื่อ On Air** | เริ่มอัดเองเมื่อเริ่มรายการ/เปิดสตรีม, แยกแทร็กตาม queue events, เก็บ N วันแล้วเกลา (clean_old), MP3 export ตอนหยุด | aircheck.py (AirCheckRecorder) — **เอนจินอัด native ใหม่ (`native_aircheck.cpp`) พร้อมแล้ว** | U1 |
| └ สถานะ | **เสร็จแล้ว (0926.2)**: สวิตช์ "Auto-record when ON AIR" (default OFF) — เปิดแล้วเอนจิน native เริ่มอัด mix เต็ม (loopback) ทันทีที่ Live mix stream ต่อ หรือไมค์สดเปิด, ปิดไฟล์+MP3 export+เกลาไฟล์เก่าเมื่อจบรายการ; การอัดแบบ manual/ตารางเวลามีสิทธิ์เหนือ auto เสมอ (auto ไม่เคยแตะเครื่องอัดของคนอื่น); เทส e2e ผ่าน 12/12 (เริ่มเอง/หยุดเอง/MP3/prune/ไม่เริ่มซ้ำ/persist) — เหลือเฉพาะ "แยกแทร็กตาม queue events" เป็นงานต่อยอดในรอบหน้า | |
| 4 | **Silence sentinel ขั้นกู้เอง** (opt-in) | เงียบ > N วินาที → เดินเพลงถัดไป / รีสตาร์ตเอนจิน / แจ้งเตือน — **default ปิด + มี dry-run mode ก่อนเสมอ** ตามที่หัวข้อ U4 กำหนด | silence_sentinel.py (observer เสร็จแล้ว 0924.1) | U4 |
| 5 | ของเล็กท้ายรอบ | Enable All / Disable All ทั้ง mic chain · Broadcast Check เพิ่มตรวจสถานะสตรีม + master chain | — | — |

### ลำดับทำงาน (phase)

1. **P1 — Master chain**: C++ → bridge → dialog → เทส (โครงเดิมของไมค์ทั้งหมด, param store ใหม่ต่อ master)
2. **P2 — Live mix สตรีม**: tap ในเอนจิน → FFmpegStreamer → เทส reconnect/metadata กับเซิร์ฟเวอร์จริง (หรือ local Icecast จำลอง)
3. **P3 — Aircheck auto + sentinel opt-in**: ปิดท้าย, เทส structural กันแตะเสียงโดยไม่ได้ตั้งค่า
4. **ยืนยันปล่อย**: soak 100k + heal-storm + suite ครบ + ออกอากาศจริง ≥ 8 ชม. (เกณฑ์ Ultimate)

### กติกาเดิมยังยึด

default OFF = เสียงดิบเหมือนเดิมทุกไบต์ · ปิดฟีเจอร์แล้วทุกอย่างเดิมเป๊ะ ·
เช็คถาวรลง suite หลักทุกชิ้น · ปรับค่าสดได้โดยไม่ต้องรีสตาร์ต

---

## 🎛 U1 — Ultimate Studio (ห้องอากาศจริง)

> เป้าหมาย: คนจับไมค์อาชีพใช้แล้วไม่อยากกลับ

| ความสามารถ | รายละเอียด | ต่อยอดจาก |
|---|---|---|
| Voice tracking | อัดเสียงพูดทับช่วงเปลี่ยนเพลงล่วงหน้า (segue) พร้อม waveform ให้ตัดจังหวะเอง | mic mix-in + waveform widget |
| Cue / Monitor split | เสียงหูฟัง (cue) แยกจากเสียงออกอากาศ — เปิดเพลงถัดไปในหูตัวเองได้โดยผู้ฟังไม่ได้ยิน | `native_audio_output` (output device ที่สอง) |
| Sound pads | ปุ่มเสียง 16 ช่อง (เอฟเฟกต์/เพลงประกอบ/โฆษณา) เล่นซ้อนรายการได้ลื่น ผสมผ่านเอนจินเดียวกัน | mic mix-in ระบบเดิม (มากกว่า 4 ช่อง: ยก kMaxMics) |
| Broadcast chain | EQ → Compressor → Limiter ทางออกอากาศ (สลับเปิด/ปิดได้) ให้เสียงดังพอดีไม่แตก | `render_set_eq` + `process_s16le_stereo` (มี limiter แล้ว) |
| Aircheck | บันทึกรายการตัวเองย้อนหลังอัตโนมัติ พร้อมตัดเพลง/ไมค์ออกเป็นแทร็ก | loopback capture + `waveform_peaks` |

## 🌐 U2 — Ultimate Network (หลายสถานี หลายจุดออกอากาศ)

| ความสามารถ | รายละเอียด | ต่อยอดจาก |
|---|---|---|
| Multi-station | หลายสถานีในแอปเดียว (คิว/ตาราง/ไมค์/สตรีมแยกกัน) สลับหน้าจอเดียวคุมได้ | engine instance ต่อสถานี (โครง render engine ต่อไมค์มีอยู่แล้ว) |
| Simulcast | ป้อนหลายเซิร์ฟเวอร์พร้อมกัน (Icecast + Shoutcast + YouTube พร้อมกัน) จากเสียงชุดเดียว | `streaming.py` + FFmpeg |
| Remote DJ | ดีเจอีกเครื่องคุมคิว/ไมค์สถานีเดียวกันผ่าน LAN แบบเรียลไทม์ (คนเดียวออกอากาศจากบ้าน อีกคนที่สถานี) | LiveLAN + session protocol ของไมค์ |
| Stream failover | เซิร์ฟเวอร์หลักล่ม สลับไปเซิร์ฟเวอร์สำรองเองในไม่กี่วินาที ผู้ฟังแทบไมรู้สึก | recovery ladder ของ player |

## 👥 U3 — Ultimate Audience (เชื่อมผู้ฟัง)

| ความสามารถ | รายละเอียด | ต่อยอดจาก |
|---|---|---|
| Listen link | ปุ่มสร้างลิงก์ให้ผู้ฟังเปิดฟังจากมือถือผ่าน WebRTC — **ไม่ต้องมีเซิร์ฟเวอร์ระหว่างกลาง** | aiortc ที่มีอยู่ |
| Song requests | ผู้ฟังขอเพลงผ่าน QR/ลิงก์ (ยืนยันโดยดีเจก่อนเข้าคิว) | Web GUI (Flask) + คิว |
| Now-playing API + widget | ป้าย "กำลังออกอากาศ" สำหรับเว็บสถานี + API ให้ระบบอื่นดึง | streaming metadata เดิม |
| Go-live alerts | แจ้งผู้ติดตามเมื่อเปิดไมค์สด/เริ่มรายการ (webhook หลายปลายทาง) | emergency mic state |

## 🤖 U4 — Ultimate Automation (ออกอากาศได้แม้ไม่มีคน)

| ความสามารถ | รายละเอียด | ต่อยอดจาก |
|---|---|---|
| Auto-DJ เต็มรูปแบบ | จัดคิวเองจากกฎ (ห้ามซ้ำศิลปินใน X ชั่วโมง, สัดส่วน jingle/โฆษณา, เพลงไทย/เทศหมุนเวียน) | scheduler.py |
| TTS jingles | พิมพ์ข้อความ → เสียงพูดประกาศเข้าคิว (ไทย/อังกฤษ) ใช้เป็นป้ายเวลา/โฆษณาอัตโนมัติ | mic segment pipeline |
| Silence sentinel | เฝ้าระดับเสียงออกอากาศ เงียบเกิน N วินาที = กู้เอง (เปลี่ยนแทร็ก/รีสตาร์ตเอนจิน/แจ้งเตือน) | loopback VU + recovery ladder |
| └ สถานะ | **dry-run observer เสร็จแล้ว** (Build 0924.1): `silence_sentinel.py` เตือนอย่างเดียว ไม่มีทางแตะเสียง (ปิดกั้นด้วยเทส structural), เปิดด้วย `FREQ_SILENCE_SENTINEL=1` — การกู้เอง (เปลี่ยนแทร็ก/รีสตาร์ตเอนจิน) เป็น build ถัดไป ต้อง default ปิด + มี dry-run mode ของตัวเองก่อน | |
| Watchdog + service | รันเป็น background service สำหรับเครื่องออกอากาศจริง ค้าง = รีสตาร์ตเอง คืนคิวจาก .freq ล่าสุด | `.freq` snapshot + process supervisor |
| รายงานประจำวัน | สรุปอัตโนมัติ: เล่นอะไร กี่ครั้ง ช่วงไหนเงียบ ไมค์ใช้กี่นาที | สถิติ play + log handler |

## 🧩 U5 — Ultimate Platform (แพลตฟอร์มและชุมชน)

| ความสามารถ | รายละเอียด | ต่อยอดจาก |
|---|---|---|
| Native encoders | เข้ารหัส MP3/AAC/Opus ใน C++ — ตัด FFmpeg ออกจากสายออกอากาศ ลด latency/CPU | เอนจิน C++ เดิม (แพตเทิร์นเดียวกับ decoder) |
| Cross-platform | เส้นทาง build Linux/macOS — WASAPI abstraction, miniaudio รองรับอยู่แล้ว | โครง engine ที่แยกชั้นแล้ว |
| Plugin SDK | ผู้ใช้เขียนเอฟเฟกต์/แหล่งเสียงของตัวเองเป็น Python ต่อเข้า chain ได้ | audio pipeline + `compile_to_c.py` |
| └ สถานะ | **v1 (observe-only) เสร็จแล้ว** (Build 0924.1): `plugin_system.py` — ปลั๊กอินไฟล์เดียวใน `plugins/` รับ event (on_start / on_track_started / on_track_finished / on_shutdown), มี `ctx.log/notify/snapshot` เท่านั้น (ไม่มีทางสั่งเล่นเสียง — บังคับด้วยเทส structural), hook รัน background thread, พลาด 3 ครั้งติด = ปิดอัตโนมัติ, เปิด/ปิดรายตัวบันทึกใน .freq — ขั้นถัดไป: หน้า Settings จัดการปลั๊กอิน + hooks ด้านเสียงแบบ sandbox | |
| Skins & themes | ธีม/สกิน UI ทั้งหน้าจอ แจกเป็นไฟล์เดียว | theme.py + COLORS |

---

## ลำดับเวลาโดยประมาณ (ยืดหยุ่นตามคุณภาพ)

```
Stable Pack สายเสถียรภาพ (2.7.x) ── ทำต่อเนื่องเป็นจังหวะเล็ก
U1 Studio      ── เริ่มได้ทันที (ทุกชิ้นต่อยอดของที่มี)
U2 Network     ── หลัง U1 (ต้องมี sound pads/chain นิ่งก่อน)
U3 Audience    ── ขนานกับ U2 ได้ (คนละด้านกับ U2)
U4 Automation  ── หลัง U1 (ใช้ VU/loopback หนัก)
U5 Platform    ── ระยะยาว (native encoder คืองานหนักสุด)
```

## เกณฑ์ตัดสิน "เสร็จ" ของทุกฟีเจอร์ Ultimate

- ใช้งานจริงต่อเนื่อง ≥ 8 ชั่วโมงบนเครื่องออกอากาศจริง ไม่ค้าง ไม่รั่ว (RSS peak Δ < 5 MB)
- ปิดฟีเจอร์แล้วทุกอย่างเดิมต้องเหมือนเดิมเป๊ะ (opt-in เสมอ)
- มีเคสเทสใน suite หลัก (ไม่ใช่เทสแยกที่ไม่มีใครรัน)

## Backlog (ยังไม่ผูกรุ่น)

- อัดสตรีมผู้ฟัง/สนทนาเข้าคิวแบบ caller (โทรเข้ารายการผ่าน WebRTC)
- ตารางออกอากาศปฏิทินลากวาง (drag-drop calendar)
- Speech-to-text ทำซับ/สคริปต์รายการอัตโนมัติ
- ตลาดเอฟเฟกต์/jingle pack แจกในชุมชน
