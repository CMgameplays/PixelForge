#!/usr/bin/env python3
"""
CMG Forge — PixelForge  |  pixelforge.py
=========================================
A multi-tool for generating game-ready PNG UI assets.
Built and owned by CMG Forge.

LOCAL:      python pixelforge.py          → starts server, opens browser automatically
PRODUCTION: gunicorn pixelforge:app       → Render / Railway / Fly.io / any WSGI host

Routes
------
  GET  /                    → main UI page
  POST /api/frame           → returns frame PNG (inline)
  POST /api/button/<state>  → returns single button-state PNG (inline)
  POST /api/buttons/zip     → returns ZIP of all 3 button states
  POST /api/remove-bg       → returns background-removed PNG
  POST /api/convert         → returns converted image(s) as ZIP
  POST /api/video/convert   → returns mp3 or gif converted from video (requires ffmpeg)

© CMG Forge — https://github.com/CMGForge/pixelforge
"""

import io
import os
import sys
import shutil
import socket
import subprocess
import tempfile
import threading
import webbrowser
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Blueprint, jsonify, render_template, request, send_file
from PIL import Image, ImageChops, ImageDraw

try:
    from shared.limiter import limiter
except ImportError:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(get_remote_address, storage_uri="memory://")

# rembg is optional — app starts fine without it; the route returns a clear
# error message if someone tries to use BG removal without it installed.
try:
    from rembg import remove as _rembg_remove
    REMBG_AVAILABLE = True
except ImportError:
    REMBG_AVAILABLE = False

# ffmpeg / ffprobe are optional — required only for video conversion.
FFMPEG_PATH      = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
FFPROBE_PATH     = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
FFMPEG_AVAILABLE = FFMPEG_PATH is not None

bp = Blueprint("pixelforge", __name__, template_folder="templates")

# ══════════════════════════════════════════════════════════════════════════════
# PALETTE
# ══════════════════════════════════════════════════════════════════════════════

PALETTE: dict[str, tuple[int, int, int]] = {
    "blue":   (  0, 150, 255),
    "pink":   (255,  80, 180),
    "yellow": (255, 210,   0),
    "red":    (255,  50,  50),
    "purple": (160,  50, 255),
    "cyan":   (  0, 220, 255),
}


def get_color(name: str) -> tuple[int, int, int]:
    return PALETTE.get(name.lower(), PALETTE["blue"])


def dim(color: tuple, factor: float) -> tuple[int, int, int]:
    return tuple(min(255, max(0, int(c * factor))) for c in color[:3])


def rgba(color: tuple, alpha: int = 255) -> tuple[int, int, int, int]:
    return (*color[:3], int(max(0, min(255, alpha))))


# ══════════════════════════════════════════════════════════════════════════════
# DIAGONAL PATTERN
# ══════════════════════════════════════════════════════════════════════════════

def draw_diagonal_pattern(
    draw: ImageDraw.ImageDraw,
    rx1: int, ry1: int,
    rx2: int, ry2: int,
    spacing: int,
    thickness: int,
    color: tuple,
) -> None:
    if rx2 <= rx1 or ry2 <= ry1:
        return

    step  = max(1, spacing)
    c     = ry1 - rx2
    c_max = ry2 - rx1

    while c <= c_max:
        pts: list[tuple[int, int]] = []

        y = rx1 + c
        if ry1 <= y <= ry2:
            pts.append((rx1, int(y)))

        x = ry1 - c
        if rx1 < x < rx2:
            pts.append((int(x), ry1))

        y = rx2 + c
        if ry1 <= y <= ry2:
            pts.append((rx2, int(y)))

        x = ry2 - c
        if rx1 < x < rx2:
            pts.append((int(x), ry2))

        if len(pts) >= 2:
            draw.line([pts[0], pts[-1]], fill=color, width=thickness)

        c += step


# ══════════════════════════════════════════════════════════════════════════════
# FRAME GENERATION
# ══════════════════════════════════════════════════════════════════════════════

def generate_frame(
    width: int,
    height: int,
    color_name: str,
    border_thickness: int = 3,
    enable_pattern: bool = False,
    pattern_spacing: int = 20,
    pattern_thickness: int = 1,
    corner_radius: int = 0,
) -> Image.Image:
    base = get_color(color_name)
    bt   = max(1, border_thickness)
    gap  = bt + 3
    cr   = max(0, corner_radius)
    cr_i = max(0, cr - gap)

    img  = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for g in range(5, 0, -1):
        draw.rectangle(
            [g, g, width - 1 - g, height - 1 - g],
            outline=rgba(base, 10 * g),
            width=1,
        )

    if cr > 0:
        draw.rounded_rectangle(
            [0, 0, width - 1, height - 1],
            radius=cr,
            outline=rgba(base, 255),
            width=bt,
        )
    else:
        draw.rectangle(
            [0, 0, width - 1, height - 1],
            outline=rgba(base, 255),
            width=bt,
        )

    inner_bt    = max(1, bt - 1)
    inner_color = rgba(dim(base, 0.65), 200)
    ix1, iy1    = gap, gap
    ix2, iy2    = width - 1 - gap, height - 1 - gap

    if ix2 > ix1 + 2 and iy2 > iy1 + 2:
        if cr_i > 0:
            draw.rounded_rectangle(
                [ix1, iy1, ix2, iy2],
                radius=cr_i,
                outline=inner_color,
                width=inner_bt,
            )
        else:
            draw.rectangle([ix1, iy1, ix2, iy2], outline=inner_color, width=inner_bt)

    if enable_pattern:
        px1 = ix1 + inner_bt + 2
        py1 = iy1 + inner_bt + 2
        px2 = ix2 - inner_bt - 2
        py2 = iy2 - inner_bt - 2

        if px2 > px1 + 4 and py2 > py1 + 4:
            pat_color = rgba(dim(base, 0.40), 145)
            draw_diagonal_pattern(
                draw, px1, py1, px2, py2,
                pattern_spacing, pattern_thickness, pat_color,
            )

    if cr > 0:
        mask      = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            [0, 0, width - 1, height - 1],
            radius=cr,
            fill=255,
        )
        _, _, _, alpha = img.split()
        img.putalpha(ImageChops.multiply(alpha, mask))

    return img


# ══════════════════════════════════════════════════════════════════════════════
# BUTTON GENERATION  (3 states)
# ══════════════════════════════════════════════════════════════════════════════

def _vertical_gradient(
    width: int, height: int,
    top_rgb: tuple[int, int, int],
    bot_rgb: tuple[int, int, int],
    alpha: int,
) -> Image.Image:
    img  = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(top_rgb[0] + (bot_rgb[0] - top_rgb[0]) * t)
        g = int(top_rgb[1] + (bot_rgb[1] - top_rgb[1]) * t)
        b = int(top_rgb[2] + (bot_rgb[2] - top_rgb[2]) * t)
        draw.line([(0, y), (width - 1, y)], fill=(r, g, b, alpha))
    return img


def generate_button_normal(
    width: int, height: int, color_name: str, border_thickness: int = 2
) -> Image.Image:
    base = get_color(color_name)
    bt   = max(1, border_thickness)

    img  = _vertical_gradient(width, height, (18, 21, 33), (10, 13, 22), 242)
    draw = ImageDraw.Draw(img)

    draw.rectangle(
        [0, 0, width - 1, height - 1],
        outline=rgba(base, 200),
        width=bt,
    )

    hl = dim(base, 1.7)
    draw.line(
        [(bt, bt), (width - 1 - bt, bt)],
        fill=rgba(hl, 55),
        width=1,
    )

    return img


def generate_button_hovered(
    width: int, height: int, color_name: str, border_thickness: int = 2
) -> Image.Image:
    base   = get_color(color_name)
    bright = dim(base, 1.35)
    bt     = max(1, border_thickness)

    img = _vertical_gradient(width, height, (26, 30, 46), (16, 19, 31), 245)

    tint = Image.new("RGBA", (width, height), rgba(base, 20))
    img  = Image.alpha_composite(img, tint)

    draw = ImageDraw.Draw(img)

    for g in range(6, 0, -1):
        draw.rectangle(
            [g, g, width - 1 - g, height - 1 - g],
            outline=rgba(base, 10 * g),
            width=1,
        )

    draw.rectangle(
        [0, 0, width - 1, height - 1],
        outline=rgba(bright, 255),
        width=bt,
    )

    hl   = dim(base, 1.9)
    hl_h = max(2, height // 5)
    for y in range(hl_h):
        t     = 1.0 - y / hl_h
        alpha = int(50 * t)
        draw.line(
            [(bt, bt + y), (width - 1 - bt, bt + y)],
            fill=rgba(hl, alpha),
        )

    draw.line(
        [(bt, bt), (width - 1 - bt, bt)],
        fill=rgba(hl, 210),
        width=1,
    )

    return img


def generate_button_clicked(
    width: int, height: int, color_name: str, border_thickness: int = 2
) -> Image.Image:
    base = get_color(color_name)
    dark = dim(base, 0.55)
    bt   = max(1, border_thickness)

    img  = _vertical_gradient(width, height, (9, 11, 19), (6, 8, 15), 255)
    draw = ImageDraw.Draw(img)

    draw.rectangle(
        [0, 0, width - 1, height - 1],
        outline=rgba(dark, 210),
        width=bt,
    )

    sh = max(3, height // 6)
    for i in range(sh):
        t = (1.0 - i / sh) ** 1.6
        draw.line(
            [(bt, bt + i), (width - 1 - bt, bt + i)],
            fill=(0, 0, 0, int(115 * t)),
        )

    sw = max(2, width // 10)
    for i in range(sw):
        t = (1.0 - i / sw) ** 1.6
        draw.line(
            [(bt + i, bt), (bt + i, height - 1 - bt)],
            fill=(0, 0, 0, int(75 * t)),
        )

    hl = dim(base, 0.72)
    draw.line(
        [(bt, height - 1 - bt), (width - 1 - bt, height - 1 - bt)],
        fill=rgba(hl, 85),
        width=1,
    )
    draw.line(
        [(width - 1 - bt, bt), (width - 1 - bt, height - 1 - bt)],
        fill=rgba(hl, 60),
        width=1,
    )

    return img


# ══════════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@bp.route("/")
def index() -> str:
    return render_template("pixelforge/index.html", colors=list(PALETTE.keys()))


@bp.route("/api/frame", methods=["POST"])
@limiter.limit("30 per minute; 200 per hour")
def api_frame():
    d  = request.get_json(force=True) or {}
    w  = max(20, min(4000, int(d.get("width",  400))))
    h  = max(20, min(4000, int(d.get("height", 300))))
    cn = str(d.get("color", "blue"))
    bt = max(1,  min(30,   int(d.get("border_thickness", 3))))
    ep = bool(d.get("enable_pattern", False))
    ps = max(4,  min(200,  int(d.get("pattern_spacing",  20))))
    pt = max(1,  min(10,   int(d.get("pattern_thickness", 1))))
    cr = max(0,  min(200,  int(d.get("corner_radius", 0))))

    img = generate_frame(w, h, cn, bt, ep, ps, pt, cr)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    suffix   = "_pattern" if ep else ""
    r_suffix = f"_r{cr}" if cr > 0 else ""
    filename = f"frame_{w}x{h}_{cn}{suffix}{r_suffix}.png"
    return send_file(buf, mimetype="image/png",
                     as_attachment=False, download_name=filename)


@bp.route("/api/button/<state>", methods=["POST"])
@limiter.limit("30 per minute; 200 per hour")
def api_button(state: str):
    generators = {
        "normal":  generate_button_normal,
        "hovered": generate_button_hovered,
        "clicked": generate_button_clicked,
    }
    if state not in generators:
        return jsonify({"error": "Invalid state. Use: normal | hovered | clicked"}), 400

    d  = request.get_json(force=True) or {}
    w  = max(20, min(4000, int(d.get("width",  270))))
    h  = max(10, min(4000, int(d.get("height",  68))))
    cn = str(d.get("color", "blue"))
    bt = max(1,  min(30,   int(d.get("border_thickness", 2))))

    img = generators[state](w, h, cn, bt)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return send_file(buf, mimetype="image/png",
                     as_attachment=False,
                     download_name=f"btn_{state}_{w}x{h}.png")


@bp.route("/api/buttons/zip", methods=["POST"])
@limiter.limit("15 per minute; 100 per hour")
def api_buttons_zip():
    d  = request.get_json(force=True) or {}
    w  = max(20, min(4000, int(d.get("width",  270))))
    h  = max(10, min(4000, int(d.get("height",  68))))
    cn = str(d.get("color", "blue"))
    bt = max(1,  min(30,   int(d.get("border_thickness", 2))))

    states = {
        "normal":  generate_button_normal(w, h, cn, bt),
        "hovered": generate_button_hovered(w, h, cn, bt),
        "clicked": generate_button_clicked(w, h, cn, bt),
    }

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for state_name, img in states.items():
            png_buf = io.BytesIO()
            img.save(png_buf, format="PNG")
            zf.writestr(f"btn_{state_name}_{w}x{h}.png", png_buf.getvalue())

    zip_buf.seek(0)
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"buttons_{w}x{h}_{cn}.zip",
    )


@bp.route("/api/remove-bg", methods=["POST"])
@limiter.limit("10 per minute; 40 per hour")
def api_remove_bg():
    if not REMBG_AVAILABLE:
        return jsonify({
            "error": "rembg is not installed. Run: pip install rembg"
        }), 503

    if "image" not in request.files:
        return jsonify({"error": "No image field in request."}), 400

    file = request.files["image"]
    if not file or file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    ALLOWED = {"png", "jpg", "jpeg", "webp", "bmp"}
    ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "").lower()
    if ext not in ALLOWED:
        return jsonify({
            "error": f"Unsupported format '{ext}'. Accepted: {', '.join(sorted(ALLOWED))}"
        }), 415

    input_bytes  = file.read()
    output_bytes = _rembg_remove(input_bytes)

    buf = io.BytesIO(output_bytes)
    buf.seek(0)

    stem     = os.path.splitext(file.filename)[0]
    filename = f"{stem}_nobg.png"
    return send_file(buf, mimetype="image/png",
                     as_attachment=False, download_name=filename)


@bp.route("/api/convert", methods=["POST"])
@limiter.limit("20 per minute; 100 per hour")
def api_convert():
    ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "BMP", "TIFF", "ICO"}
    ALLOWED_IN      = {"png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif", "ico"}

    files = request.files.getlist("images")
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify({"error": "No image files provided."}), 400

    fmt = request.form.get("format", "PNG").upper()
    if fmt not in ALLOWED_FORMATS:
        return jsonify({"error": f"Unsupported output format '{fmt}'."}), 415

    try:
        quality = max(1, min(100, int(request.form.get("quality") or 90)))
    except (ValueError, TypeError):
        quality = 90

    ext_map  = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp",
                "BMP": "bmp", "TIFF": "tiff", "ICO": "ico"}
    mime_map = {
        "PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
        "BMP": "image/bmp", "TIFF": "image/tiff", "ICO": "image/x-icon",
    }
    out_ext = ext_map[fmt]

    converted: list[tuple[str, bytes]] = []

    for f in files:
        in_ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "").lower()
        if in_ext not in ALLOWED_IN:
            return jsonify({
                "error": f"Unsupported input format '{in_ext}' for file '{f.filename}'."
            }), 415

        try:
            img  = Image.open(f.stream)
            stem = os.path.splitext(f.filename)[0]

            if fmt == "JPEG":
                if img.mode == "P":
                    img = img.convert("RGBA")
                if img.mode in ("RGBA", "LA"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[-1])
                    img = bg
                elif img.mode != "RGB":
                    img = img.convert("RGB")
            elif fmt in ("BMP",) and img.mode not in ("RGB", "L", "RGBA"):
                img = img.convert("RGB")

            buf         = io.BytesIO()
            save_kwargs: dict = {}
            if fmt in ("JPEG", "WEBP"):
                save_kwargs["quality"] = quality
            if fmt == "JPEG":
                save_kwargs["optimize"] = True

            img.save(buf, format=fmt, **save_kwargs)
            buf.seek(0)
            converted.append((f"{stem}.{out_ext}", buf.getvalue()))

        except Exception as exc:
            return jsonify({
                "error": f"Failed to convert '{f.filename}': {exc}"
            }), 500

    if not converted:
        return jsonify({"error": "No valid files to convert."}), 400

    if len(converted) == 1:
        name, data = converted[0]
        return send_file(
            io.BytesIO(data),
            mimetype=mime_map[fmt],
            as_attachment=False,
            download_name=name,
        )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in converted:
            zf.writestr(name, data)
    zip_buf.seek(0)
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"converted_{len(converted)}_files.zip",
    )


@bp.route("/api/video/convert", methods=["POST"])
@limiter.limit("5 per minute; 20 per hour")
def api_video_convert():
    if not FFMPEG_AVAILABLE:
        return jsonify({
            "error": "ffmpeg is not installed or not found in PATH. "
                     "Download it from https://ffmpeg.org/download.html and make sure "
                     "it is accessible from the command line."
        }), 503

    if "video" not in request.files:
        return jsonify({"error": "No video field in request."}), 400

    file = request.files["video"]
    if not file or file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    ALLOWED_IN = {
        "mp4", "mkv", "avi", "mov", "webm",
        "flv", "wmv", "m4v", "mpeg", "mpg", "ts", "3gp",
    }
    in_ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "").lower()
    if in_ext not in ALLOWED_IN:
        return jsonify({"error": f"Unsupported video format '{in_ext}'."}), 415

    output_type = request.form.get("output", "MP3").upper()
    if output_type not in ("MP3", "GIF"):
        return jsonify({"error": "output must be MP3 or GIF."}), 400

    bitrate = request.form.get("bitrate", "192k")
    if bitrate not in ("128k", "192k", "256k", "320k"):
        bitrate = "192k"

    try:
        fps = max(5, min(20, int(request.form.get("fps") or 10)))
    except (ValueError, TypeError):
        fps = 10

    try:
        width = max(240, min(800, int(request.form.get("width") or 480)))
        if width % 2 != 0:
            width -= 1
    except (ValueError, TypeError):
        width = 480

    stem    = os.path.splitext(file.filename)[0]
    tmp_dir = tempfile.mkdtemp()

    try:
        in_path = os.path.join(tmp_dir, f"input.{in_ext}")
        file.save(in_path)

        if output_type == "MP3":
            if FFPROBE_PATH:
                probe = subprocess.run(
                    [
                        FFPROBE_PATH, "-v", "error",
                        "-select_streams", "a",
                        "-show_entries", "stream=codec_type",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        in_path,
                    ],
                    capture_output=True, timeout=15,
                )
                if not probe.stdout.strip():
                    return jsonify({
                        "error": (
                            "This video has no audio track — MP3 conversion is not possible. "
                            "The file may be a video-only clip (common with Twitter/X downloads)."
                        )
                    }), 422

            out_path = os.path.join(tmp_dir, f"{stem}.mp3")
            cmd = [
                FFMPEG_PATH, "-y", "-i", in_path,
                "-vn", "-acodec", "libmp3lame", "-ab", bitrate,
                out_path,
            ]
            mime    = "audio/mpeg"
            dl_name = f"{stem}.mp3"

        else:
            out_path = os.path.join(tmp_dir, f"{stem}.gif")
            vf = (
                f"fps={fps},scale={width}:-2:flags=lanczos,"
                "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
            )
            cmd = [
                FFMPEG_PATH, "-y", "-i", in_path,
                "-vf", vf,
                out_path,
            ]
            mime    = "image/gif"
            dl_name = f"{stem}.gif"

        result = subprocess.run(cmd, capture_output=True, timeout=180)

        if result.returncode != 0:
            err_text = result.stderr.decode("utf-8", errors="replace")
            return jsonify({"error": f"ffmpeg error: {err_text[-400:]}"}), 500

        with open(out_path, "rb") as fh:
            data = fh.read()

        return send_file(
            io.BytesIO(data),
            mimetype=mime,
            as_attachment=True,
            download_name=dl_name,
        )

    except subprocess.TimeoutExpired:
        return jsonify({
            "error": "Conversion timed out (180 s). Try a shorter or smaller video."
        }), 500
    except Exception as exc:
        return jsonify({"error": f"Conversion failed: {exc}"}), 500
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# LAUNCH HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_CLOUD_SIGNALS = ("RENDER", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME", "DYNO")

def _is_local() -> bool:
    return not any(os.environ.get(sig) for sig in _CLOUD_SIGNALS)


def find_free_port(start: int = 5000) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Could not find a free port in range 5000-5100.")


def _open_browser(port: int) -> None:
    import time
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    from flask import Flask
    local = _is_local()
    port  = int(os.environ.get("PORT", find_free_port() if local else 8080))
    host  = "127.0.0.1" if local else "0.0.0.0"

    standalone = Flask(__name__)
    standalone.config["SECRET_KEY"]         = os.environ.get("SECRET_KEY", os.urandom(32))
    standalone.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    standalone.register_blueprint(bp, url_prefix="/")
    limiter.init_app(standalone)

    @standalone.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "File too large. Maximum upload size is 50 MB."}), 413

    print(f"\n  ◈ CMG Forge — PixelForge")
    print(f"  ──────────────────────────")
    print(f"  Mode    →  {'local' if local else 'production'}")
    print(f"  Server  →  http://{host}:{port}")
    print(f"  Press   Ctrl+C to stop\n")

    if local:
        threading.Thread(target=_open_browser, args=(port,), daemon=True).start()

    standalone.run(debug=False, host=host, port=port)
