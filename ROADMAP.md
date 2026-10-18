# FreQ Roadmap

> อัปเดตล่าสุด: 2026-09-23 — หลังเปิดตัว **2.7.3-SP2**
> หลักการใหญ่ของทุกเวอร์ชัน: **ความเสถียรมาก่อนฟีเจอร์** — ทุกรุ่นต้องผ่านชุดเทสเดิม (jingle stress, edge cases, soak) ก่อนแจกเสมอ

---

## 📍 จุดตั้งต้น (สิ่งที่มีแล้วใน SP2)

- เอนจินเสียง C++ เนทีฟครบวงจร: render (WASAPI + miniaudio), decode, capture, stream pump, meter, output — ผ่าน soak 20,000 ops, RSS นิ่ง, ไม่มีเธรดซอมบี้
- Emergency Mic สดแบบ mix-in เรียลไทม์ (~20 ms) ทำงานแม้เพลงหยุด + duck อัตโนมัติ
- Mic segment เรียลไทม์ + **Dual Mic** (เอนจินรองรับถึง 4 ตัว แต่ UI เปิดได้ 2)
- Seek ระดับเอนจิน ไม่ rebuild — ไม่มีเสียงซ้อน
- Streaming: Shoutcast / Icecast (FFmpeg) และ WebRTC (aiortc)
- Web GUI, Mobile Remote, ตัวตรวจอัปเดต, installer อัตโนมัติ (release.py)

---

## 🩹 2.7.4 (SP3) — แพตช์เสถียรภาพถัดไป

เป้าหมาย: เก็บงานเล็กที่พบหลังเปิดตัว SP2 ปล่อยเร็ว ไม่ขึ้นเครื่องใหม่

| งาน | รายละเอียด |
|---|---|
| แก้ update checker | `update_checker.py` ยัง hardcode `CURRENT_VERSION = "2.5.47"` — เปลี่ยนไปอ่านจาก `version.txt` (single source of truth) แล้วเทียบกับ GitHub Releases |
| Multi-mic ×4 | เอนจินรองรับ 4 ไมค์แล้ว เปิดใน UI ที่เหลือ (ไดอะล็อก + radio_manager + player) พร้อม per-mic gain |
| Crash telemetry | เขียน minidump/บันทึก Event Viewer ของตัวเองตอน native module ล้ม เพื่อรายงานอาการได้ทันทีไม่ต้องรอผู้ใช้เปิด Event Viewer |
| เก็บงานที่ผู้ใช้รายงาน | สรุปจาก feedback หลังแจก SP2 |

---

## ✨ 2.8.0 — ฟีเจอร์สถานี

เป้าหมาย: ของที่ DJ ใช้จริงทุกวัน ขยายจากฐานเดิมโดยไม่แตะโครงเอนจิน

| ฟีเจอร์ | รายละเอียด |
|---|---|
| Voice tracking | อัดเสียงพูดทับช่วงเปลี่ยนเพลง (segue) ล่วงหน้า — ใช้ mix-in เดิม + จัดคิวใน scheduler |
| Dayparting อัตโนมัติ | กำหนดกฎรายช่วงเวลา (เพลง/jingle/โฆษณาตามชั่วโมง) ต่อยอด scheduler.py |
| Auto-DJ mode | เอนจินจัดคิวเองจากกฎ (ห้ามซ้ำศิลปิน, สัดส่วน jingle, หมุนเวียน playlist) |
| Monitor/Deck routing | แยกเสียงหูฟัง (cue) ออกจากเสียงออกอากาศด้วย output device ที่สอง — ใช้ `native_audio_output` |
| สถิติออกอากาศ | นับ play/histogram รายเพลง เก็บลง .freq เพื่อรายงานรายวัน |

---

## 🚀 2.9.0 — เชื่อมสู่ผู้ฟัง

เป้าหมาย: ขยายขอบเขตจากเครื่อง DJ ไปสู่ผู้ฟัง/ทีม

| ฟีเจอร์ | รายละเอียด |
|---|---|
| Listen link เรียลไทม์ | ปุ่มสร้างลิงก์ WebRTC ให้ผู้ฟังเปิดฟังจากมือถือโดยไม่ต้องมี Icecast server — ต่อยอด aiortc ที่มีอยู่ |
| Remote 2.0 | Mobile remote ครบสเปก: จัดคิว, กด jingle, mic segment, ปรับ mixer 5 ช่อง (เดิมเป็นควบคุมพื้นฐาน) |
| Multi-station | รันหลายสถานีในโปรเซสเดียว (คิว/ตาราง/สตรีมแยกกัน) |
| Cloud sync (ออปชัน) | ซิงก์ playlist/ตารางผ่าน GitHub Gist หรือ WebDAV — ไม่บังคับสมัครบริการ |

---

## 🏗 3.0 — โครงสร้างระดับโปรดักชัน

เป้าหมาย: เปิดทิ้งเป็นเดือนในสถานีจริงโดยไม่ต้องแตะเครื่อง

| งาน | รายละเอียด |
|---|---|
| Native streaming เต็มรูปแบบ | เข้ารหัส MP3/AAC/Opus ใน C++ (แทนพาธ FFmpeg) — ลด CPU/latency และตัด dependency ภายนอก |
| Unattended mode | รันเป็น background service + auto-recovery ถ้า device หลุด/เอนจินค้าง (ต่อยอด recovery ladder เดิม) |
| Watchdog | กระบวนดูแลแยกตัว: ถ้าโปรแกรมค้าง รีสตาร์ตเองและคืนคิวจาก .freq ล่าสุด |
| ระบบภาษา | แยก string ออกจาก gui.py เป็นไฟล์แปล (ไทย/อังกฤษก่อน แล้วเปิดให้ community) |
| แพลตฟอร์มอื่น | เส้นทาง build บน Linux/macOS — miniaudio รองรับอยู่แล้ว เหลือ WASAPI-specific ให้มี abstraction |

---

## ✅ เกณฑ์คุณภาพ (ผูกกับทุกรุ่น)

- `test_jingle_stress.py` + `test_edge_cases.py` เขียว 100%
- Soak อย่างน้อย 20,000 ops ต่อ release: RSS นิ่ง, เธรดกลับฐาน, start พลาด 0
- ทุก native module ผ่าน `release.py verify` (entry points ครบใน bundle จริง)
- ไม่มี Event Viewer crash ระหว่าง soak ยาว (≥ 4 ชม.)

## 📝 Backlog (ยังไม่กำหนดรุ่น)

- บันทึกออกอากาศย้อนหลัง (aircheck archive) พร้อมตัดต่อเบื้องต้น
- ป้าย "ตอนนี้เล่นอะไร" ส่งเข้า API ของสถานีภายนอก
- Text-to-speech jingles จากข้อความในคิว
- ปลั๊กอิน Python สำหรับ effect ต่อเนื่อง (chain ที่ผู้ใช้ต่อเอง)
