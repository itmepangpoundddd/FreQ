# FreQ Ultimate Series — Roadmap

> อัปเดตล่าสุด: 2026-10-19
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

## 📅 Build 2026.1019.2 — As-run log + ไฟล์เพลย์ลิสต์ (New / Import)

> งานเล็กที่ปิดช่อง “ทำไมต้องเปิดไฟล์หนึ่งทิ้งอีกไฟล์”: หัวโปรแกรมมีปุ่มสร้างเพลย์ลิสต์ใหม่
> และปุ่มนำเข้าเพลงจาก .freq อื่นเข้ากับคิวที่เปิดอยู่

| # | งาน | สถานะ | หลักฐาน |
|---|---|---|---|
| **F1** | ปุ่ม **🆕 New** (Ctrl+N) บนหัว ข้าง Open/Save/Save As — ล้าง **คิว** เท่านั้น (Library/ประวัติ/presets อยู่ครบ) + คืนเป้าหมายการบันทึกกลับไปไฟล์ default + ไม่เขียนไฟล์เอง | ✅ ส่งแล้ว | `_smoke_playlist_1020.py` **32/32** (กดปุ่มจริง) · `test_edge_cases.py` ด่าน `[playlist]` 25 ข้อ · [_ui_toolbar_1020.png](_ui_toolbar_1020.png) |
| **F2** | ปุ่ม **📥 Import** (Ctrl+I) — เลือก .freq ได้หลายไฟล์ แล้ว **ต่อท้าย** คิวเดิม (ไอเทมที่ออกอากาศอยู่ไม่เสียที่) · ข้ามเพลงซ้ำตามตัวตน (path/URL/mic) ทั้งกับคิวและในชุดที่นำเข้าเอง · ไฟล์อ่านไม่ได้ = บอกชื่อ ไม่เงียบ | ✅ ส่งแล้ว | เคสในสโมกเดียวกัน: นำเข้า 4 เพลง → นำเข้าซ้ำได้ 0 · ไฟล์เสีย/ว่าง = เตือนและคิวเท่าเดิม |
| **F3** | ข้อจำกัดที่จดไว้ | ⚠️ | นำเข้าแต่ *รายการเพลง* ไม่ได้ก้อปไฟล์เสียง · ไม่รวม presets/library ของไฟล์ต้นทาง (ต้องตัดสินก่อน) · ไม่ตรวจว่าไฟล์บนดิสก์ยังอยู่ (จะขึ้น `missing` ตอนขึ้นอากาศ เหมือนของเดิม) |

---

### 🧾 ส่วนที่ 2 — ผังรายการ + ตรวจย้อนหลัง (วางจาก DESIGN-plan-1020.md)

> แผน 5 รอบของระบบผัง (P0–P4) อยู่ใน [DESIGN-plan-1020.md](DESIGN-plan-1020.md)
> · กติกาเดิม: ปิดทุกอย่าง = พฤติกรรมเดิมเป๊ะ · ค่าที่ไม่รู้ = `?` ไม่ประดิษฐ์

| # | งาน | สถานะ | หลักฐาน |
|---|---|---|---|
| **P0** | **As-run log** — append-only JSONL + hash chain + manifest + retention + แท็บดูย้อนหลัง + export CSV | ✅ **ส่งแล้ว (2026.1019.2)** | `_smoke_asrun_1020.py` **101/101** · `test_edge_cases.py` **536/536** (เดิม 473 + ด่าน `[asrun]` ใหม่) · `test_gui_wiring.py` 18/18 · `test_ui_1016.py` 112/112 · `_smoke_queue_times_1019.py` 47/47 · `_smoke_settings_persist_1017.py` 34/34 · `_smoke_master_chain_1017.py` 36/36 · `_smoke_queue_stability_1018.py` 16/16 · `release.py --sync-version` → `version.txt` = `installer.iss` = **2026.1019.2** |
| **P0.2** | **Seal อัตโนมัติ + สั่งตรวจทั้งโฟลเดอร์จากบรรทัดคำสั่ง** — manifest v2 (`tail_hash` = หลักฐาน sealed prefix) · `--verify-all`/`--seal-all`/`--seal`/`--list`/`--export`/`--prune` + exit codes (0/1/3/2) · Seal เองเมื่อขึ้นวันใหม่ + poller ปิดช่องว่างวันที่ค้าง · แท็บเตือนวันที่ยังไม่ Seal | ✅ **ส่งแล้ว (วันเดียวกับ P0)** | `_smoke_asrun_cli_1020.py` **59/59** (ยิง CLI จริงผ่าน subprocess: แก้บรรทัด/ลบแถว/ตัดท้าย/**เขียนใหม่ทั้งไฟล์แล้วต่อโซ่ใหม่**/**เขียนใหม่+เพิ่มแถวจนโซ่เดิมพ้นปลาย sealed**/ลบไฟล์แต่ manifest อยู่/วันไม่เคย Seal → จับได้ครบ · `--seal-all` ปฏิเสธและ manifest เดิมไม่เปลี่ยนแม้ไบต์เดียว · ไม่มีการเรียกไหนจบด้วย traceback) · `_smoke_asrun_1020.py` 101/101 · `test_edge_cases.py` 536/536 · อื่น ๆ เท่าเดิมทุกชุด |
| **P1** | ผัง `.freqplan` + validator + Grid/Wheel editor (ยังไม่แตะการเล่น) | ⏳ รอ | ต้องได้: validator จับเคสตัวอย่าง 8 ข้อครบ · round-trip ไม่เพี้ยน |
| **P2** | `schedule_compiler.py` — ผัง → คิวที่มีเวลา + reason + dry-run | ⏳ รอ | ต้องได้: revision เดียวกันให้ผลเดิมเป๊ะ 2 ครั้ง · dry-run ไม่แตะเสียง/ไม่เขียน as-run |
| **P3** | Verify + รายงาน — planned vs actual, โควตา, CSV/PDF, "กดแล้วได้ยิน" | ⏳ รอ | ต้องได้: กดไอเทม→เสียงตรงช่วง ±1 วิ · รายงานโควตาตรงกับ as-run ±0.1% |
| **P4** | กฎหมุนเวียนขั้นสูง, LAN, rundown พิมพ์ได้, แก้คิวด้วยมือแล้วบันทึก override | ⏳ รอ | ต้องไม่ทำลาย P0–P3 |

### 🧾 P0 — สิ่งที่ส่งจริง (2026.1019.2)

- **`asrun.py`** (โมดูลใหม่ ไม่พึ่ง Tk): `logs/asrun/YYYY-MM-DD.jsonl` เขียนต่อท้ายเท่านั้น + `hash`/`prev_hash` ต่อโซ่ SHA-1 + manifest รายวัน (เขียนแบบ atomic) + `verify()` + `prune()` (พื้น 30 วันตามกฎหมาย) + `export_csv()` (UTF-8 BOM)
  - **1 ไอเทม = 2 บรรทัด** (start ตอนขึ้นอากาศ, end ตอนหยุด) — ไฟดับกลางเพลงยังเหลือร่องรอย start (ไอเทมค้าง = ปกติ ไม่ใช่ความเสียหาย) · `end` เขียนลงไฟล์ของ **วันที่เริ่ม** เสมอ (ไอเทมข้ามคืนไม่แยกโซ่)
  - `ended_why` เป็นคำศัพท์ปิด 9 ค่า: `natural` / `skipped` / `stopped` / `interrupted` / `anthem-cut` / `jingle-cut` / `missing` / `error` / `superseded`
  - **ห้ามพังการออกอากาศ**: ทุกการเขียนห่อ `except OSError` → เก็บ `last_error` ให้แท็บโชว์ ⚠️ (ไม่กลืน ไม่โยน)
- **hook 9 จุดใน `gui.py`** (บางที่สุด ไม่รื้อเส้นทางเดิม): คิวเพลง/YouTube (`_play_index`) · ไมค์ · jingle อัตโนมัติ · jingle กดเอง (ปิดเพลงเดิมด้วย `jingle-cut`) · เบรกโฆษณา · ชาติ + ตอนกลับเข้าเพลง (`anthem-cut`) · สตรีมออกอากาศ 2 ทาง (FFmpeg + native live mix) · รีเลย์ · ปิดโปรแกรม (ปิดไอเทมที่ค้าง + Seal วันนั้น)
  - `_next_track`/`_prev_track` = `skipped` · ไฟล์หาย = `missing` · ดาวน์โหลดพลาด = `error` · `begin` ซ้ำจะปิดตัวเก่าเป็น `superseded` (โซ่ไม่ขาด)
  - ไอเทมแนบ **ไฟล์ air-check + offset** เมื่อกำลังอัด จึงกดฟังหลักฐานได้ในรอบถัดไป
- **แท็บ As-run (ON AIR)** — ตารางของวัน (เวลา · ชนิด · ชื่อ — ศิลปิน · ความยาวจริง · เหตุผลที่จบ) + ปุ่ม ◀/▶/วันนี้ + สรุปโซ่ + 🔒 Seal / ✅ Verify / 📤 CSV / 🗑 Prune + ช่อง retention (ปฏิเสธต่ำกว่า 30 วัน) · **default OFF** และจำค่า (enabled/folder/keep_days) ลง `.freq`
- **P0 ไม่แตะ `scheduler.py`** (ยังไม่ต่อสายเหมือนเดิม) และ **ไม่แตะ `rq.history`** — งานไทม์ไลน์/คิวเดิมไม่ถูกแก้

### 🔒 P0.2 — Seal อัตโนมัติ + CLI (ส่งพร้อม P0)

- **Seal วันเอง 2 ทาง**: (1) เขียนไอเทมแรกของวันใหม่ → Seal วันก่อนหน้า (2) `_poll_asrun` เรียก `seal_all` วันละครั้ง → สถานีที่เปิดค้างไว้หรือเพิ่งเปิดหลังเครื่องดับ ได้ manifest ครบเองโดยไม่มีคนกด
- **หน้าต่างคำสั่ง** (รันจากโฟลเดอร์ที่ถือ `logs/asrun`): `--verify-all` (ค่าเริ่มต้น) · `--seal-all` · `--seal DATE` · `--list` · `--export DATE [--out F]` · `--prune [--keep-days N] [--force]` · `--json` · `--quiet` · `--today DATE` · `--allow-unsealed`
- **exit code ที่ cron ใช้ตัดสินได้**: `0` สะอาด · `1` หลักฐานเสีย/หาย (โซ่ขาด, ไฟล์ถูกตัดทอน, เขียนใหม่หลัง Seal, ลบไฟล์แต่ manifest ยังอยู่, วันก่อนหน้าที่ยังไม่ Seal) · `3` ต้องดูแล (วันถูกต่อท้ายหลัง Seal = ควรรัน `--seal-all`) · `2` คำสั่งผิด — **แยก "ต้องดูแล" ออกจาก "เสียหาย"** เพราะสองอย่างนี้ต้องทำคนละอย่าง
- **manifest v2 + หลักฐาน sealed prefix**: เก็บ `tail_hash` (hash ของบรรทัดสุดท้ายที่ Seal) ⇒ วันไหน "โตต่อ" จะ Seal ซ้ำได้ **ก็ต่อเมื่อ hash ที่บรรทัดที่ Seal ไว้ยังตรงเป๊ะ** — ไฟล์ที่เขียนใหม่ทั้งชุดแล้วต่อโซ่ใหม่ (แม้จะเพิ่มแถวจนยาวกว่าเดิม ซึ่งกฎ "นับบรรทัด" ปล่อยผ่าน) ถูกจับว่าถูกแก้
- **Seal ไม่ใช่ทางล้างร่องรอย**: ถ้าโซ่ขาด / ไฟล์สั้นลง / prefix เปลี่ยน ⇒ `write_manifest` **ปฏิเสธ** และเก็บ manifest เดิมไว้เป็นหลักฐาน (ต้อง `--force` เท่านั้นจึงจะทับ — และการ Seal ซ้ำตามปกติจะเก็บ `previous_file_sha256` + `reseal_count` เป็นร่องรอยไว้)
- **แท็บ As-run** เพิ่มปุ่ม **🔒 ทั้งหมด** และบรรทัดเตือน `⚠️ ยังไม่ Seal N วัน (…)` เมื่อมีวันที่พิสูจน์อะไรไม่ได้

### ⚠️ เบี่ยงเบน/ยังไม่ปิดของ P0 (จดไว้ ไม่เงียบ)

- เกณฑ์ใน DESIGN ข้อ 9 เขียนว่า "จำนวนบรรทัด = จำนวนไอเทม" แต่การเขียน 2 จังหวะ (start/end) เป็นข้อกำหนดในหัวข้อ 4 ของเอกสารเดียวกัน ⇒ เกณฑ์ที่ทดสอบจริงคือ **items = 20 และ lines = 40** (นับไอเทม ไม่ใช่บรรทัด)
- เกณฑ์ "เวลา start/stop ต่างจาก `_queue_plan` ≤ 0.5 วิ" **ยังทดสอบไม่ได้** เพราะยังไม่มีผัง (P2) — สิ่งที่ทดสอบแทน: `actual_duration` ตรงกับเวลาจริงที่เทสวัดเอง (sleep 1.0 วิ → 1.003 วิ) และทุกไอเทมมี start/end ครบคู่
- โซ่ hash **ไม่ใช่การกันปลอม 100%** — ผู้ที่ตั้งใจสร้างไฟล์ใหม่ทั้งชุดทำได้ แต่จะไม่ตรงกับ SHA-256 ของ manifest ที่ Seal ไว้ (นี่คือคำรับประกันที่ซื่อสัตย์)
- `_smoke_split_0928.py` / `_smoke_heal_0928.py` / `_smoke_sup_0928.py` / `_smoke_marker_0926.py` **ยังแดงอยู่ก่อนรอบนี้** (R1–R3 ของ 1019.1 ยังไม่ทำ) — ไม่ใช่ผลจากงาน P0

---

## 📅 Build 2026.1019.1 — แผนรอบถัดไป (วาง 2026-10-19)

> ธีมรอบนี้: **"แอปพร้อมเมื่อไหร่ ก็เปิดเมื่อนั้น" (ready = open) — และปิดหนี้ที่ค้างมา 3 รอบ**
> เพิ่มเข้า 2026-10-19 (นอกแผน): **rundown ของคิวบอกเวลาของ Ads / Jingle / Anthem /
> Download / Convert ครบแล้ว** (ดูหัวข้อ 🩹 ท้ายส่วนนี้) — ก่อนหน้านี้พับเวลาสามอย่างแรก
> เข้าไปแบบมองไม่เห็น และ download/convert ไม่โผล่เลย
>
> สองงานใหญ่คือ (1) ตอนนี้โปรแกรม **สร้างเสร็จก่อนสแปลชจะขึ้น** สแปลชจึงเป็นการหน่วงเปล่า
> **1.15–1.20 วิ ทุกครั้ง** บวกกับ `__init__` อีก **5.45 วิ** ที่ยังสร้างทั้ง 9 แท็บรวดเดียว
> (2) หนี้ R1–R5 ของ 1018.1 **ยังไม่ถูกแตะเลยสักข้อ** — grep วันนี้ยังได้ 0 ตรง `sentinel_heal`,
> `aircheck_split`, `Enable All` และ Broadcast Check ยังมี 7 แถวเท่าเดิม
>
> **ไม่มีฟีเจอร์ใหม่**: ทุกรายการคือของที่สัญญาไว้แล้ว หรือเป็นเวลาที่เสียเปล่าโดยไม่ได้อะไรกลับมา

### 📌 หลักฐานตั้งต้น (วัดจากการรันจริง 2026-10-19)

| งาน | สิ่งที่วัดได้วันนี้ | ผล |
|---|---|---|
| **เส้นทางเปิดโปรแกรม** | รันจริง 3 รอบ (`import gui` → `RadioApp()` → `FreQSplash()` → reveal) | import **1.68 วิ** · `RadioApp()` **5.42 / 5.45 / 5.45 วิ** · **สแปลชหลังแอปพร้อมแล้ว 1.15 / 1.17 / 1.20 วิ** → รวม **8.70 วิ** ทุกรอบ |
| **สแปลชคือดีเลย์เปล่า** | อ่าน `gui.py:10921–10934` (คู่กับคอมเมนต์ `10829–10834`) + วัดจริง | `RadioApp()` สร้างเสร็จ **ก่อน** `FreQSplash()` ถูกสร้าง → แอนิเมชัน ~0.6 วิ (0.05 × รอบ 30 ms) + `after(300)` + fade รวมเป็น **1.15–1.20 วิ** ที่ไม่ได้ครอบงานอะไรเลย (1018.1 ลด `SPLASH_STEP` จาก 0.014 → 0.05 แล้ว แต่ที่เหลือก็ยังเป็นเวลาที่ไม่ได้งาน) |
| **`__init__` 5.45 วิ อยู่ตรงไหน** | `cProfile` บน `RadioApp()` จริง | โปรไฟล์รวม 6.37 วิ โดย **`_build_sidebar` 3.70 วิ** เป็นก้อนใหญ่สุด · ในนั้น `_build_settings_tab` **1.59 วิ** · การเรียก `ctk_option_menu` **7 ครั้ง = 1.38 วิ** · มี `threading.wait` 1.40 วิ |
| **ทั้ง 9 แท็บถูกสร้างรวดเดียว** | `gui.py:2343–2352` | `_build_add_tab()` … `_build_keys_tab()` ถูกเรียกทุกตัวตอนเปิด · `_on_sidebar_tab_switch` แค่ `pack_forget`/`pack` — ไม่ได้สร้างอะไรตอนกด |
| `_smoke_heal_0928.py` | รันจริง | โมดูลผ่านหมดแล้วตาย **บรรทัด 162** — `'tkapp' object has no attribute 'sentinel_heal'` · `grep sentinel_heal gui.py` = **0** |
| `_smoke_split_0928.py` | รันจริง | โมดูลผ่าน แล้ว **FAIL** "split checkbox exists and defaults OFF" + ตาย **บรรทัด 160** (`_aircheck_tracklog`) · `grep aircheck_split gui.py` = **0** |
| `_smoke_sup_0928.py` | รันจริง | **8/16** — แดง 5 เคส gui (status label, refresh, open-log, populate, revive toast) + 3 เคส `installer.iss` |
| `_smoke_marker_0926.py` | รันจริง | ตาย **บรรทัด 90** — `'QueueRowHandle' object has no attribute 'item'` (คิวกลายเป็น canvas ตั้งแต่ 1018.1) · มี 9 check site |
| ชุดเทสกลาง | รันจริง | `test_edge_cases.py` **449/449** ก่อนรอบนี้ + **17 ด่านใหม่** ของคอลัมน์เวลา (รันแยก `[qrow]` = **50/50 ผ่าน**) · `test_ui_1016.py` **112/112** · `test_gui_wiring.py` **18/18** · `_smoke_queue_stability_1018.py` **16/16** — rc=0 ทุกตัว · `gui_wiring` ยังแบก baseline หนี้ **13 รายการ** (dead import 7 + annotation-only 1 + ต่อสายไฟค้าง 5 ของ Q1/Q2) |
| repo | `git status --porcelain` | **ยังค้าง 4 path**: `plugins/`, `push_source_code.py`, `release.py`, `templates/` → `release.py publish()` จะปฏิเสธ (`release.py:572`) เพราะ tree ไม่สะอาด |
| `.gitignore` | บรรทัด 23 | ยังเป็น `*.bat` เฉย ๆ — **ไม่มีข้อยกเว้น** ให้ `START FreQ (Auto-Restart).bat` |
| Broadcast Check | `_broadcast_check_rows` (บรรทัด 8958) | **7 แถว** (Queue / Microphones / Output engine / Loopback tap / Mic filters / Plugins / Disk space) — ไม่มีแถวสตรีม และไม่มีแถว master chain |
| Enable/Disable All | grep ทั้ง gui.py | **ไม่มี** · ขั้นของไมค์ที่ต้องสลับมี **9 ขั้น** (`suppression, compressor, limiter, deesser, miceq, echo, gainst, upcomp, expand`) |
| เลขเวอร์ชัน | `version.txt` + `installer.iss` | **2026.1018.1** ตรงกัน — **ยังไม่ bump** (กติกา 1018.1 ยังใช้: ห้าม bump ก่อนโค้ดสะอาดและเทสเขียว) |

### 🩹 ส่งแล้วระหว่างรอบนี้ (ผู้ใช้สั่ง · ต้องยกเป็นหัวข้อใน RELEASENOTES ตอน R7)

- **เวลาเปิด–หยุดของทุกเพลงในการ์ดคิว** (สั่ง 2026-10-19): การ์ดแต่ละใบมีคอลัมน์ใหม่ทางขวา ติดกับความยาวเพลง — `HH:MM–HH:MM` ของช่วงที่เพลงนั้นจะออกอากาศ โดยยึดจาก **เพลงที่เล่นอยู่ตอนนี้** (เริ่ม = ตอนนี้ − ตำแหน่งที่เล่นไปแล้ว) เพลงที่อยู่เหนือขึ้นไปจึงเป็นเวลาที่ผ่านมาแล้ว และเพลงถัด ๆ ไปคือเวลาจริงที่จะได้ออกอากาศ
  - คำนวณ **ครั้งเดียวต่อการสร้างคิว** (`_queue_schedule`) แล้วประทับใหม่เมื่อนาทีเปลี่ยน (เทียบ `int(time.time() // 60)` ในตัว poller เดิม — เป็น `int` เปรียบเทียบ 1 ครั้งต่อ 40 ms) ไม่ได้คำนวณต่อแถว
  - ความยาวเพลงที่พัง (None / NaN / ติดลบ / ข้อความ) ยังได้เวลาครบ แค่ยาว 0 วินาที (ผ่าน `_safe_seconds` เดิม)
  - การ์ดแคบจะ **ตัดคอลัมน์เวลาออก** แล้วคืนที่ให้ชื่อเพลง ไม่บีบทั้งสองคอลัมน์
  - ด่านถาวรใหม่ `_smoke_queue_times_1019.py` (ดึงข้อความจาก canvas จริง ไม่ได้อ่านจากโค้ด) · `test_edge_cases.py` เพิ่มเช็คใน `[qrow]` · `_smoke_queue_stability_1018.py` ปรับขอบเขตเป็น 7 ไอเท็มต่อการ์ด (จาก 6) พร้อมเหตุผลในไฟล์ — ทั้งสามตัว **ต้องอยู่ในชุด gate ของ R7**
- **jingle / โฆษณา / เพลงชาติ ก็เป็นเวลาในไทม์ไลน์** (สั่ง 2026-10-19 ต่อจากข้อบน): ทั้งสามอย่างไม่ได้อยู่ในคิว แต่กินเวลาออกอากาศ จึงถูกพับเข้าไปในการคำนวณ
  - `_queue_insert_plan` จำลองตัวนับจริงของ `_on_track_finished`: **ad นับก่อน** และถ้า ad ยิง ตัวนับจิงเกิลจะไม่ขยับรอบนั้น (ตรงกับของจริงที่ `return` ก่อนถึงบล็อกจิงเกิล) · เลือกไฟล์ ad แบบ round-robin ตาม `current_ad_index` จริง
  - ความยาวของ jingle/ad อ่านจาก **ไฟล์จริงด้วย mutagen** (`_probe_seconds`, cache ตาม mtime — ไม่เล่น ไม่เปิด device) ไฟล์ที่อ่านไม่ได้ = 0 วิ ไม่เดา
  - `_anthem_folds` พับเวลาชาติ (`pause_before + fade_in + ความยาว + fade_out`) ให้ทุกแถวที่เริ่มหลังเวลานั้น และเดินตามเวลาจริงข้ามวันได้ · ชาติที่ยังไม่รู้ความยาว = พับ 0 วิ
  - **เวลา download / convert**: `_download_stall_seconds` ประมาณเวลาที่เหลือจากอัตราจริง (ต้องมี % ≥ 1 และผ่านไป ≥ 2 วิ ไม่งั้นคืน 0) แล้วเลื่อนทุกแถวที่อยู่ **หลัง** แถวที่เล่นอยู่ · ตอน **convert** ไม่มีการรายงาน % เลย → **ไม่ประดิษฐ์เวลาจบ** แค่บอกว่า `(queue waits)`
  - แถบ Now Playing: `📥 Downloading... 42% · ready ~09:04` (เดิมบอกแค่ %)
  - บรรทัดใหม่ใต้หัวคิว `queue_next_label` — แสดงเฉพาะเมื่อมีอะไรจะรายงาน: `⏭ next: jingle 09:00 · ad 09:03 · anthem 08:52` (คิวเพลงล้วนไม่ขึ้นบรรทัดนี้)
  - หลักฐาน: `_smoke_queue_times_1019.py` **35/35** (จิงเกิล/โฆษณา/ชาติ/download ครบ) · `test_edge_cases.py` `[qrow]` รวมเป็น **50/50** — ทั้งคู่ต้องอยู่ในชุด gate ของ R7
- **คอลัมน์เวลาเป็นวินาที `HH:MM:SS–HH:MM:SS`** (สั่ง 2026-10-19 ต่อจากข้อบน): `_clock_time` เปลี่ยนเป็น `%H:%M:%S` (พัง → `--:--:--`) และขยายที่จองไว้เป็น `QUEUE_TIME_W = 108` px
  - เหตุผล: เพลงละ 4 นาทีทำให้แถวติดกันได้ `09:03–09:07` ซ้ำกันสองแถว อ่านแล้วเหมือนบั๊ก · นาฬิกาฝาผนังที่ผู้จัดใช้เทียบเป็นวินาที
  - คอลัมน์เดิมยังถูก **ตัดออกเมื่อการ์ดแคบ** (เกณฑ์คือความกว้าง canvas < ~290 px) ไม่บีบชื่อเพลง
  - หลักฐาน: `_smoke_queue_times_1019.py` **35/35** (อัป regex เป็น `HH:MM:SS–HH:MM:SS` แล้ว) — ตัวเดิมต้องอยู่ในชุด gate ของ R7 ต่อ
- **สวิตช์ 🎲 แทรกเพลงแบบสุ่มตำแหน่งในคิว** (สั่ง 2026-10-19): อยู่ในแท็บ Add ใต้ปุ่ม Add Playlist — `Random positions in the queue`
  - เปิดแล้ว **ทุกทางที่เพิ่มเพลง** (YouTube video / เพลย์ลิสต์ / ไฟล์ / ลากวาง / ➕ ใน Library / ฟอร์ม manual) ไม่ต่อท้าย แต่หย่อนลงช่องสุ่ม — ทุกทางรวมที่ `_add_to_queue` จุดเดียว คืนตำแหน่งที่ลงจริง
  - กติกาสำคัญ: สุ่มเฉพาะ `[เพลงที่เล่นอยู่ + 1, ท้ายคิว]` (`RadioQueue.insert_song_at_random`) — เพลงใหม่จึงไม่ไปค้างหลังแถวที่เล่นจบไปแล้ว (runtime เดินคิวไปข้างหน้าเท่านั้น) เพลงที่เล่นอยู่/ที่ผ่านมาแล้วไม่ถูกเลื่อน · ไม่มีอะไรเล่น = สุ่มได้ทั้งคิว
  - worker โหลดเพลย์ลิสต์อ่าน **ธง bool ธรรมดา** (`_queue_random_insert`) ไม่แตะ Tk จากเธรดอื่น · ปิดสวิตช์ = กลับไปต่อท้ายเหมือนเดิมเป๊ะ (default OFF)
  - หลักฐาน: `_smoke_random_insert_1020.py` **31/31** (กด CTkSwitch ตัวจริง · 300 เพลงสุ่มโดย index 42 คงอยู่ · ไม่มีเพลงใหม่ลง ≤ 42 · ตำแหน่งไม่เรียงขึ้น · worker เพลย์ลิสต์ทำตาม) + `_smoke_select_1017.py` 32/32 + `_smoke_queue_stability_1018.py` 16/16 — ต้องอยู่ในชุด gate ของ R7
- **rundown บอกเวลาของ Ads / Jingle / Anthem / Download / Convert จริง ๆ** (ผู้ใช้ถาม 2026-10-19 ว่า "ทำไมมันไม่บอกเวลา Ads, Jingle, Anthem, Download, Convert"): ของเดิม **พับเวลาสามอย่างแรกเข้าไปในคอลัมน์เพลงแบบมองไม่เห็น** (หยุดของแถวก่อนหน้าไม่รวมเวลาเบรก จึงมีช่องว่างที่อธิบายไม่ได้) บรรทัดหัวคิวโชว์ได้แค่ **ตัวแรกของแต่ละชนิด** และ **ตัดทิ้งเมื่อไฟล์อ่านไม่ได้** (ความยาว 0 → หายทั้งรายการ) ส่วน download/convert ไม่โผล่ที่ไหนเลย นอกจากข้อความสั้นในแถบล่างสุด
  - `_queue_plan()` คืนสองอย่างพร้อมกัน: `rows[i] = (start, stop)` ที่ **stop รวมเวลาเบรกที่แถวนั้นกระตุ้น** (ไทม์ไลน์ต่อเนื่อง ไม่มีช่องว่าง) และ `events[]` ที่แยก jingle / ad / anthem / wait ออกมาเป็น **รายการของตัวเองพร้อมเวลานาฬิกา** แล้ว `_queue_times` มาจากplan เดียวกัน (คำนวณครั้งเดียว)
  - เพลงชาติถูกวาง **ตรงจุดที่มันตัดเข้าจริง** — เดิมคิดเฉพาะแถวที่ *เริ่ม* หลังเวลาชาติ จึงไม่ยืดแถวที่ **คร่อม** เวลานั้น (แถวที่กำลังเล่นถูกตัด → ต้องยาวขึ้น) ตอนนี้แถวที่คร่อมถูกยืด และได้รายการ `anthem` พร้อมเวลาจริง
  - ไฟล์ที่วัดความยาวไม่ได้ = `(length ?)` แทนที่จะหายเงียบ ๆ · เบรกที่ตก **นอกคิว** ไม่เดาเวลา แต่บอก `⏭ later: … after N more songs` (เวลาขึ้นกับเพลงที่ยังไม่ถูกเพิ่ม จึงคำนวณไม่ได้จริง)
  - **download**: แถวที่กำลังรอไฟล์จะ **เริ่มตอนไฟล์พร้อม** (เดิมเริ่มตอนนี้ทั้งที่ยังโหลดไม่เสร็จ) ทุกแถวถัดไปต่อเนื่องกัน · **convert**: yt-dlp ไม่รายงาน % เลย จึง **วัดความเร็วจริงของการแปลงที่จบไปแล้วในเซสชัน** (เก็บอัตราส่วนวินาทีแปลง/วินาทีเพลง 5 ครั้งหลังสุด) แล้วประมาณเวลาจบ **ติดป้าย `(est.)`** — ยังไม่เคยวัด = `ETA unknown` ไม่ประดิษฐ์ตัวเลข
  - คิวว่างแต่ยังโหลด/แปลงอยู่ ก็ยังขึ้นบรรทัดรายงาน (เมื่อก่อนหายไปพร้อมคิว) · ระหว่างโหลด บรรทัด/แถวประทับใหม่ **ไม่เกิน 1 ครั้ง/วินาที** (`_maybe_refresh_download_plan`) progress callback ยิงถี่กว่านั้นมาก
  - หลักฐาน: `_smoke_queue_times_1019.py` **47/47** (ของเดิม 35 + 12 ด่านใหม่: stop รวมเบรก/ไม่มีช่องว่าง · event แต่ละชนิดมีเวลา+ความยาว · ชาติคร่อมแถว · แถวที่รอโหลดเริ่มตอนพร้อม · บรรทัด download/convert และ ETA ที่วัดได้) · `_capture_plan_1020.py` + `_ui_rundown_1020.png` เห็นของจริง · `test_ui_1016.py` 112/112 · `test_gui_wiring.py` 18/18 — ต้องอยู่ในชุด gate ของ R7

### 🧱 ตารางงานของรอบ 1019.1 (R0–R7)

| # | งาน | รายละเอียด | เกณฑ์ผ่าน |
|---|---|---|---|
| **R0** | **ปิดหนี้ repo ให้จบ** (ค้างมา 3 รอบ) | track `release.py`, `push_source_code.py`, `plugins/`, `templates/` จริง · ใส่ `!START FreQ (Auto-Restart).bat` ต่อจาก `*.bat` ใน `.gitignore` · **ตัดสิน `_smoke_marker_0926.py`**: มันยังทดสอบแบบ "หนึ่งแถว = หนึ่ง widget" ซึ่งเลิกจริงตั้งแต่คิวเป็น canvas — ให้เขียนใหม่ให้ยิง `QueueRowHandle` ตัวจริง **หรือ** ลบพร้อมจดใน RELEASENOTES ว่าถอดเพราะอะไร (ห้ามเหลือเป็นแดงที่ไม่มีเจ้าภาพเป็นรอบที่ 4) · ลำดับตายตัว: commit → ตรวจ `git status` → ค่อย push | `git status --porcelain` **ว่าง** · `git ls-files` เห็นไฟล์ทั้ง 4 ครบ · ด่าน "working tree clean" ของ `release.py publish()` ผ่าน (ทดสอบโดยไม่ tag) · `_smoke_marker_0926.py` ได้ **rc=0** หรือไม่มีไฟล์เหลือ |
| **R1** | **Sentinel self-heal เข้า Settings** (ยกจาก 1018.1 R1) | กติกาเดิมทั้งข้อ: สร้าง `self.sentinel_heal` ผูกกับ `silence_sentinel` · dropdown `sentinel_heal_mode_var` (off / dry_run / active, **default off**) · persist `settings["silence_sentinel"]["heal_mode"]` + restore ตอนเปิดไฟล์ · ต่อ callback จริงขั้นละ 1 จุด (skip → เปลี่ยนแทร็ก, restart → restart เอนจิน, alert → toast/log) · โชว์ขั้นปัจจุบัน/ผลครั้งล่าสุดจาก `stats()` · **dry_run ต้องเรียก callback 0 ครั้ง** | `_smoke_heal_0928.py` **25/25** · `test_silence_sentinel.py` เขียว · `test_ui_1016.py` 112/112 · ถอดชื่อ `sentinel_heal*` ออกจาก `BASELINE_MISSING_WIRING` |
| **R2** | **Air-Check แยกแทร็กต่อสายไฟ** (ยกจาก 1018.1 R2) | `aircheck_split_var` (check box หน้า Air-Check, **default OFF**, ใช้ได้เฉพาะเมื่อมีเครื่องอัด) · `self._aircheck_tracklog = TrackLog()` · `_play_index` จด event เฉพาะตอนกำลังอัด · หยุดอัด → `split_recording(wav, log, progress=...)` + toast `N แทร็ก` · ชื่อไฟล์ `track_001 - Artist - Title.wav` · retention เดิมเกลาต่อ · persist ลงไฟล์ตั้งค่า | `_smoke_split_0928.py` **33/33** · `test_combo_aircheck_stream.py` เขียว · ปิดสวิตช์แล้ว WAV ต้องเป็นก้อนเดียวเป๊ะ · ถอด 3 ชื่อออกจาก baseline |
| **R3** | **Supervisor: หน้าจอสถานะ + แพ็กเข้า installer** (ยกจาก 1018.1 R3) | gui อ่าน status file ของ `freq_supervisor.py` (state / restarts / ts) มาโชว์บน Settings + ปุ่ม "Open log" + toast "กู้คืนหลังค้าง" · `installer.iss` กำหนด entry ของ supervisor exe และให้ shortcut ตัวหลักเรียกผ่าน supervisor · `release.py verify` ตรวจ entry ใหม่ · **โหมด dev ต้องไม่ขึ้นเตือนหลอก** | `_smoke_sup_0928.py` **16/16** · `_smoke_supervisor_0926.py` + `_smoke_watchdog_0926.py` ยังเขียว · `release.py verify` ผ่านใน bundle จริง |
| **R4** | **ของเล็กที่ค้างจริง** (ยกจาก 1018.1 R5) | (1) `Enable All` / `Disable All` ทั้ง **9 ขั้น** ของไมค์ chain — สลับ mode เท่านั้น **ห้ามแตะระดับเสียง** (2) Broadcast Check เพิ่ม 2 แถว: **สถานะสตรีม** (`streamer.state` + host) และ **Master chain** (จาก `get_master_chain()`; OFF คือค่าที่ถูก ไม่ใช่ fail) — ทั้งคู่ **read-only** เหมือนแถวเดิม (ห้ามเปิด device) | เทสใหม่ยืนยัน bulk toggle เขียน mode ครบ 9 ขั้นและค่าที่ไม่ใช่ mode ไม่ถูกแตะ · broadcast คืน **9 แถว** แบบ read-only · `test_ui_1016.py` 112/112 |
| **R5** | **เปิดโปรแกรม: เลิกหน่วงเปล่า** (งานใหม่ — หัวใจของรอบ) | ให้สแปลชสะท้อนงานจริง ไม่ใช่รอให้ครบเวลา: เริ่มแอนิเมชัน **พร้อม** `RadioApp()` (หรือเริ่มสแปลชก่อนงานเสร็จ) แล้ว **จบด้วย fade ทันทีที่แอปพร้อม** โดยเหลือเวลาขั้นต่ำสั้น ๆ ให้ยังเห็นแบรนด์ · เพิ่มสคริปต์วัดที่ commit ได้ เช่น `_measure_open_1019.py` — **เปิดโปรแกรมจริง N รอบ** แล้วรายงานค่ามัธยฐาน (รอบที่แล้วตัวเลข 4.63 → 1.32 วิ วัดในโปรเซสเดียว จึงเชื่อไม่พอจะเขียนลง RELEASENOTES) | เปิดจริง **≥ 10 รอบ**: ค่ามัธยฐาน "ถึงใช้งานได้" ลดลงชัดเจน และ **สแปลชเติมเวลาได้ไม่เกิน 0.4 วิ** หลังแอปพร้อม · แอนิเมชัน/fade ยังครบ ไม่กระพริบ · `test_ui_1016.py` 112/112 · ตัวเลขที่จะเขียนใน RELEASENOTES ต้องมาจากสคริปต์นี้เท่านั้น |
| **R6** | **สร้างแท็บข้างเมื่อถูกเปิด** (lazy sidebar — งานใหม่) | เลิกสร้างทั้ง 9 แท็บตอนเปิด: สร้างเมื่อกดครั้งแรก (แท็บตั้งต้นยังสร้างทันที) · **ค่าที่โหลดจาก `.freq` ต้องไม่ขึ้นกับว่าแท็บถูกสร้างหรือยัง** — เก็บค่าไว้ในตัวแอป แล้ว widget sync ตอนสร้าง (แบบเดียวกับที่ Immediate / Autosave ทำใน 1017.1) · หน้า Settings / Preset / Log / Keys ต้องเหมือนเดิมทุกจุดเมื่อเปิดครั้งแรก | `RadioApp()` init **≤ 2.50 วิ** (จาก 5.45 วิ — วัดด้วยสคริปต์ของ R5) · เปิดทุกแท็บแล้วไม่มี exception · ค่าใน `.freq` round-trip เท่าเดิม (เปิด–ปิด–เปิด ซ้ำ 2 รอบ) · `test_edge_cases.py` เขียวทั้งชุด (รวม 17 ด่านของคอลัมน์เวลาเปิด–หยุด) · `test_ui_1016.py` 112/112 · `test_gui_wiring.py` 18/18 |
| **R7** | **ปิดเกณฑ์คุณภาพ + ออก 1019.1** | ชุดเทสกลาง + smoke ทั้งชุดรันคำสั่งเดียวจบ (จับ exit code จริง ไม่กรองทิ้ง) · `test_gui_wiring.py --strict` ต้องผ่าน → หนี้ baseline ต้องเหลือ **0** (R1/R2/R3 จ่ายครบ) · soak ≥ 20,000 ops (RSS นิ่ง, เธรดกลับฐาน, start พลาด 0) · `release.py verify` ครบ entry point · ออกอากาศจริง ≥ 8 ชม. เปิด split + heal(dry_run) + supervisor พร้อมกัน · แล้วค่อย `--set-version 2026.1019.1` → RELEASENOTES หัวใหม่เหนือ 1018.1 → installer → commit → push | ทุกชุดออก **0** · `version.txt` = `installer.iss` = **2026.1019.1** · `git status --porcelain` ว่างหลัง push · ตัวเลขทุกตัวใน RELEASENOTES มีที่มาจากสคริปต์หรือ log ที่เก็บไว้ |

### ลำดับทำงาน (phase)

1. **R0 ก่อนเสมอ** — `release.py` ยังไม่ติด git และ `publish()` ปฏิเสธ tree ที่ไม่สะอาด → เส้นทางออกรุ่นยัง **ใช้ไม่ได้จริง** จนกว่า R0 จะปิด
2. **R1 / R2 ขนานกันได้** — คนละหน้าใน gui แต่ **commit แยกต่อสาย** เพื่อย้อนกลับได้ทีละเส้น
3. **R3 หลัง R0** — เพราะต้องพึ่งข้อยกเว้น `*.bat` ไม่งั้น supervisor ผ่านในเครื่อง dev แต่หายในเครื่องติดตั้ง
4. **R4 ปิดท้ายของงานต่อสายไฟ** — แตะ Settings / Broadcast Check หน้าเดียวกับ R1/R3 (ทำทีหลังเพื่อไม่ให้ rebase กันเอง)
5. **R5 ก่อน R6** — R5 สร้างสคริปต์วัดก่อน แล้ว R6 ใช้สคริปต์นั้นเป็นเกณฑ์ตัดสิน (ถ้าทำ R6 ก่อน จะไม่มีตัวเลขยืนยันว่าได้จริง)
6. **R6 หลังงานต่อสายไฟทั้งหมด** — lazy tab ทำให้ widget ยังไม่เกิดตอนเปิด ซึ่งจะกระทบ smoke ของ R1–R4 ที่อ่าน widget บนหน้า Settings; ทำ R6 ทีหลังจึงแก้ smoke รอบเดียว
7. **R7 ท้ายสุดเสมอ** — ไม่มีอะไรขึ้น installer โดยข้าม quality gate และไม่ bump เลขก่อนโค้ดสะอาด

### ⚠️ ความเสี่ยงที่จดไว้ล่วงหน้า

- **หนี้ 3 รอบซ้อน**: R1–R3 ถูกยกจาก 1017.1 → 1018.1 → 1019.1 ถ้ารอบนี้ยังไม่ปิด ให้เขียนเหตุผลลง RELEASENOTES ไม่ใช่ย้ายเงียบ ๆ อีกรอบ
- **ตัวเลขเวลาเปิดต้องมาจากการเปิดจริง**: รอบก่อนวัด A/B ในโปรเซสเดียวได้ 4.63 → 1.32 วิ ซึ่งเชื่อไม่พอจะเขียนลง RELEASENOTES (เปิดจริงได้ 1.15–1.20 วิ) — ห้ามเขียนตัวเลขจากการวัดในโปรเซสอีก
- **heal ต้องไม่กลายเป็นผู้ควบคุมเสียง**: default `off` · `dry_run` เรียก callback **0 ครั้ง** — ถ้าแดง ห้ามผ่อน assertion
- **split ปิดแล้วต้องเป๊ะ**: WAV ก้อนเดียวเหมือนก่อนทุกไบต์ ไม่ใช่ "ใกล้เคียง"
- **Broadcast Check ห้ามเปิด device**: แถวใหม่ต้องอ่านค่าที่มีอยู่แล้วเท่านั้น
- **lazy tab ห้ามทำค่าตั้งค่าหาย**: ค่าที่โหลดจาก `.freq` ต้องถูกเก็บก่อน widget เกิด — ถ้าให้ widget เป็นเจ้าของค่า ค่าจะหายเมื่อยังไม่เปิดแท็บ (นี่คือความเสี่ยงอันดับหนึ่งของ R6)
- **`test_ui_1016.py` เป็นด่านกันถอย**: 112/112 ต้องคงอยู่ทุก R

---

## 📅 Build 2026.1018.1 — แผนรอบที่แล้ว (วาง 2026-10-18)

> **สถานะปิดรอบ (2026-10-19):** ออกแจกเป็นเลข **2026.1018.1** แล้ว (`version.txt` = `installer.iss`)
> แต่สิ่งที่ส่งจริง **ไม่ใช่ตาราง R0–R6 ของรอบนี้**: งานที่ขึ้นเครื่องคือ "คิวเป็น canvas"
> (virtualized queue, keyboard nav, design pass, restore เร็วขึ้น) ซึ่งเป็นงานที่เกิดหลังวางแผน
> · R0 ปิดได้ครึ่งเดียว — commit `0f57ac7` มีแล้ว แต่ `release.py`, `push_source_code.py`,
> `plugins/`, `templates/` **ยัง untracked 4 path** จนถึงวันนี้
> · **R1–R5 ไม่ถูกแตะแม้แต่ข้อเดียว** (grep วันนี้ยังได้ 0) → ยกทั้งชุดไปเป็น R1–R4 ของ 1019.1
> · ชุดเทสที่เขียว 900 check ของ 1018.1 **ไม่รวม** 4 smoke ที่ยังแดง (`_smoke_heal_0928`,
> `_smoke_split_0928`, `_smoke_sup_0928`, `_smoke_marker_0926`) — ทั้งสี่ตัวยังแดงอยู่ใน tree ที่ออกแจก

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
