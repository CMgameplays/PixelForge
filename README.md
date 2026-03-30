# PixelForge

A lightweight, locally-hosted web app for game-ready image asset processing. Built with Flask and Pillow — no cloud required, no data leaves your machine.

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0%2B-black?logo=flask)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

| Tab | What it does |
|---|---|
| **Frame** | Generate pixel-art UI frames / borders as PNG |
| **Button** | Generate pixel-art buttons in Normal / Hover / Pressed states (single or ZIP) |
| **BG Remove** | Remove image backgrounds using AI (via `rembg`) |
| **Convert** | Batch convert images between PNG, JPG, WEBP, BMP, TIFF, ICO — and convert video files to MP3 or GIF |

---

## Requirements

### Software

| Requirement | Version | Notes |
|---|---|---|
| [Python](https://www.python.org/downloads/) | 3.11+ | Required |
| [ffmpeg](https://ffmpeg.org/download.html) | Any recent | **Optional** — only needed for video → MP3 / GIF conversion |

### Python packages

All listed in `requirements.txt`:

```
flask>=3.0.0
pillow>=10.0.0
flask-limiter>=3.5.0
gunicorn>=21.0.0
rembg[cpu]>=2.0.50
```

> **Note:** `rembg` and `ffmpeg` are both optional. The app starts and runs fine without them — those specific features will show a friendly error if you try to use them without the dependency installed.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/CMGForge/pixelforge.git
cd pixelforge
```

### 2. Create and activate a virtual environment

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

> `rembg` includes a large AI model (~170 MB) that downloads automatically on first use of the BG Remove feature.

### 4. (Optional) Install ffmpeg

Only needed if you want to use the **video → MP3 / GIF** conversion feature.

**Windows** — download the pre-built binary:
1. Go to [https://github.com/BtbN/FFmpeg-Builds/releases](https://github.com/BtbN/FFmpeg-Builds/releases)
2. Download `ffmpeg-master-latest-win64-gpl.zip`
3. Extract and copy `ffmpeg.exe`, `ffprobe.exe`, `ffplay.exe` to a folder (e.g. `C:\Users\<you>\ffmpeg\`)
4. Add that folder to your system PATH

**macOS** (with Homebrew):
```bash
brew install ffmpeg
```

**Linux (Debian/Ubuntu):**
```bash
sudo apt update && sudo apt install ffmpeg
```

Verify it works:
```bash
ffmpeg -version
```

---

## Running locally

```bash
python pixelforge.py
```

The server starts on `http://127.0.0.1:5000` and opens in your browser automatically.

---

## Project structure

```
pixelforge/
├── pixelforge.py        # Flask app — all routes and business logic
├── requirements.txt     # Python dependencies
├── runtime.txt          # Python version pin (for deployment)
├── Procfile             # Gunicorn entry point (for deployment)
├── templates/
│   └── index.html       # Single-page UI (HTML + CSS + JS)
└── static/              # Static assets (if any)
```

---

## API Routes

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | Main UI page |
| `POST` | `/api/frame` | Generate a frame PNG |
| `POST` | `/api/button/<state>` | Generate a single button-state PNG |
| `POST` | `/api/buttons/zip` | Generate all 3 button states as a ZIP |
| `POST` | `/api/remove-bg` | Remove background from an image |
| `POST` | `/api/convert` | Batch convert image files |
| `POST` | `/api/video/convert` | Convert video to MP3 or GIF (requires ffmpeg) |

---

## Deployment

The app is production-ready with Gunicorn. It can be deployed to any WSGI-compatible host.

**Render / Railway / Fly.io:**

The `Procfile` is already configured:
```
web: gunicorn pixelforge:app --workers 2 --timeout 120 --bind 0.0.0.0:$PORT
```

Just connect your GitHub repo and deploy — no extra configuration needed for the core image features. Note that `rembg` and `ffmpeg` may require additional setup depending on your host's environment.

---

## License

MIT — see [LICENSE](LICENSE) for details.

© CMG Forge
