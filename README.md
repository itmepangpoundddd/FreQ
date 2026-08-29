# 🎙️ FreQ — Radio Playlist Manager

> Radio Playlist Manager with YouTube Integration

![Logo](logo.svg)

---

## ✨ Features

| Features | CLI | Desktop GUI | Web GUI |
|---------|:---:|:-----------:|:-------:|
| Add / Remove / Insert songs in queue | ✅ | ✅ | ✅ |
| Drag/Drag/reorder songs (Drag & Drop) | — | ✅ | ✅ |
| Add songs from YouTube (Single video) | ✅ | ✅ | ✅ |
| Add from YouTube Playlist | ✅ | ✅ | ✅ |
| Search songs in library | ✅ | ✅ | ✅ |
| Save / Load queue (JSON) | ✅ | ✅ | ✅ |
| Manage Preset Playlist | ✅ | ✅ | ✅ |
| Play history | ✅ | ✅ | ✅ |
| Playback simulation (Play/Pause/Skip) | ✅ | ✅ | ✅ |
| Select Output Device | ✅ | ✅ | — |

---

## 🚀 Installation

```bash
# Install dependencies
pip install customtkinter flask yt-dlp sounddevice

# Or install all
pip install -r requirements.txt
```

## 🎯 How to run

### Desktop GUI (Recommended)
```bash
python gui.py
```

### Web GUI
```bash
python app.py
# Open browser to http://localhost:5000
```

### CLI
```bash
python radio_manager.py
```

---

## 📁 File Structure

```
FreQ/
├── radio_manager.py    # Core logic + CLI
├── gui.py              # Desktop GUI (CustomTkinter)
├── app.py              # Web GUI (Flask)
├── audio.py            # Audio Device Manager
├── templates/
│   └── index.html      # Web frontend
├── logo.svg            # Program logo
├── requirements.txt    # Dependencies
└── README.md           # this file
```

---

## 🎨 Logo

See file `logo.svg` For the program logo

---

## 📝 License

FreQ uses a **dual-license model**:

| License | Use | Cost |
|---------|-----|------|
| **GPL-3.0** | Personal, educational, non-commercial | **Free** |
| **Commercial License** | Business, commercial, revenue-generating | **Paid** |

See [LICENSE](LICENSE) for GPL-3.0 terms.
See [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) for commercial terms.
See [PRICING.md](PRICING.md) for pricing tiers.

**TL;DR**: Use it for free at home. Pay if you use it to make money.
