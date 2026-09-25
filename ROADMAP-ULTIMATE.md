# FreQ Ultimate Series — Roadmap

> อัปเดตล่าสุด: 2026-09-24
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
