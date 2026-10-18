"""Translate Thai in index.html using chr() approach."""
import pathlib, re

p = pathlib.Path("templates/index.html")
c = p.read_text(encoding="utf-8")

# Extract all unique Thai words and their codepoints
thai_words = set(re.findall(r'[\u0e00-\u0e7f]+', c))
print(f"Found {len(thai_words)} unique Thai words:")
for tw in sorted(thai_words, key=lambda x: len(x), reverse=True):
    cp = '+'.join(f'0x{ord(ch):04x}' for ch in tw)
    print(f"  {tw} ({len(tw)} chars): {cp}")

# Map each Thai word to English
eng_map = {}
for tw in thai_words:
    tw_lower = tw.lower()
    if tw == '\u0e1e\u0e25\u0e48\u0e32':  # เพลง
        eng_map[tw] = 'songs'
    elif tw == '\u0e1e\u0e25\u0e48\u0e32\u0e43\u0e19\u0e01\u0e34\u0e27':  # เพลงในคิว
        eng_map[tw] = 'songs in queue'
    elif tw == '\u0e1e\u0e25\u0e48\u0e32\u0e08\u0e32\u0e01\u0e40\u0e1e\u0e25\u0e48\u0e32\u0e25\u0e34\u0e2a\u0e15\u0e4c':  # เพลงจากเพลย์ลิสต์
        eng_map[tw] = 'songs from playlist'
    elif tw == '\u0e1e\u0e25\u0e48\u0e32\u0e16\u0e32\u0e14\u0e44\u0e1b':  # เพลงถัดไป
        eng_map[tw] = 'Next song'
    elif tw == '\u0e1e\u0e25\u0e48\u0e32\u0e01\u0e48\u0e2d\u0e07\u0e25\u0e32':  # เพลงก่อนหน้า
        eng_map[tw] = 'Previous song'
    elif tw == '\u0e01\u0e23\u0e38\u0e48\u0e21':  # กรุณา
        eng_map[tw] = 'Please'
    elif tw == '\u0e23\u0e32\u0e22':  # ใส่
        eng_map[tw] = 'enter'
    elif tw == '\u0e0b\u0e37\u0e48\u0e21':  # ชื่อ
        eng_map[tw] = 'name'
    elif tw == '\u0e1e\u0e25\u0e48\u0e32\u0e17\u0e2d\u0e22':  # เพลงเอง
        eng_map[tw] = 'Manually'
    elif tw == '\u0e0b\u0e37\u0e48\u0e21\u0e1e\u0e25\u0e48\u0e32':  # ชื่อเพลง
        eng_map[tw] = 'Song title'
    elif tw == '\u0e28\u0e34\u0e25\u0e1b\u0e34\u0e19':  # ศิลปิน
        eng_map[tw] = 'Artist'
    elif tw == '\u0e44\u0e21\u0e48\u0e1a\u0e31\u0e07\u0e04\u0e31\u0e1a':  # ไม่บังคับ
        eng_map[tw] = 'optional'
    elif tw == '\u0e04\u0e27\u0e32\u0e21\u0e25\u0e32\u0e22':  # ความยาว
        eng_map[tw] = 'Duration'
    elif tw == '\u0e27\u0e34\u0e19\u0e32\u0e21\u0e34':  # วินาที
        eng_map[tw] = 'seconds'
    elif tw == '\u0e1e\u0e25\u0e48\u0e32\u0e17\u0e32\u0e14\u0e40\u0e23\u0e37\u0e2d\u0e01':  # เพลงถัดไป
        eng_map[tw] = 'Next song'
    elif tw == '\u0e2b\u0e32\u0e01\u0e2a\u0e48\u0e07\u0e44\u0e21\u0e48':  # ยังไม่ได้
        eng_map[tw] = 'No song'
    elif tw == '\u0e40\u0e23\u0e34\u0e48\u0e21':  # เรียก
        eng_map[tw] = ''
    elif tw == '\u0e23\u0e32\u0e22\u0e40\u0e23\u0e37\u0e2d\u0e01\u0e42\u0e23\u0e22\u0e14\u0e32\u0e27':  # ใส่เรียกรถloader...
        eng_map[tw] = tw  # skip complex ones for now
    else:
        print(f"  UNKNOWN: {tw}")
        eng_map[tw] = tw  # keep as-is for now

# Print unknowns
unknowns = {tw: eng for tw, eng in eng_map.items() if eng == tw and len(tw) > 1}
if unknowns:
    print(f"\nUnknown Thai words: {list(unknowns.keys())}")
