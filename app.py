#!/usr/bin/env python3
"""
CMG Forge — PixelForge  |  app.py
===================================
A multi-tool for generating game-ready PNG UI assets.
Built and owned by CMG Forge.

LOCAL:      python app.py          → starts server, opens browser automatically
PRODUCTION: gunicorn app:app       → Render / Railway / Fly.io / any WSGI host

Routes
------
  GET  /                    → main UI page
  POST /api/frame           → returns frame PNG (inline)
  POST /api/button/<state>  → returns single button-state PNG (inline)
  POST /api/buttons/zip     → returns ZIP of all 3 button states
  POST /api/remove-bg       → returns background-removed PNG
  POST /api/convert         → returns converted image(s) as ZIP

© CMG Forge — https://github.com/CMGForge/pixelforge
"""

import io
import os
import socket
import threading
import webbrowser
import zipfile

from flask import Flask, jsonify, render_template, request, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from PIL import Image, ImageChops, ImageDraw

# rembg is optional — app starts fine without it; the route returns a clear
# error message if someone tries to use BG removal without it installed.
try:
    from rembg import remove as _rembg_remove
    REMBG_AVAILABLE = True
except ImportError:
    REMBG_AVAILABLE = False

# ── Absolute path to this file's directory ────────────────────────────────────
# Ensures Flask always finds /templates regardless of where the process is
# launched from (Gunicorn, double-click, PyInstaller, etc.)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))

# Flask needs a secret key in production (sessions, signed cookies).
# Set the SECRET_KEY environment variable on your host; falls back to a
# random value that is fine for this stateless app.
app.config["SECRET_KEY"]        = os.environ.get("SECRET_KEY", os.urandom(32))
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024   # 15 MB upload cap

# ── Rate limiter ───────────────────────────────────────────────────────────────
# Protects the server from heavy CPU abuse (large image generation spam).
# Limits are per IP address; in-memory storage is fine for a single-process app.
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[],          # no default — only annotated routes are limited
    storage_uri="memory://",
)

# ══════════════════════════════════════════════════════════════════════════════
# PALETTE
# Six neon/game-UI colors available to the user.
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
    """Return RGB tuple for a named palette color (defaults to blue)."""
    return PALETTE.get(name.lower(), PALETTE["blue"])


def dim(color: tuple, factor: float) -> tuple[int, int, int]:
    """Scale all RGB channels by factor and clamp to [0, 255]."""
    return tuple(min(255, max(0, int(c * factor))) for c in color[:3])


def rgba(color: tuple, alpha: int = 255) -> tuple[int, int, int, int]:
    """Append an alpha channel to an RGB tuple."""
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
    """
    Draw 45-degree diagonal lines (top-left → bottom-right, slope = +1)
    that are perfectly clipped to the rectangle (rx1, ry1) – (rx2, ry2).

    Maths
    -----
    Line equation :  y = x + c
      c_min = ry1 - rx2   (tangent to top-right corner of rect)
      c_max = ry2 - rx1   (tangent to bottom-left corner of rect)

    For each c we intersect the line with all 4 edges and keep only
    the 2 points that lie on the edge *and* inside the rect.
    """
    if rx2 <= rx1 or ry2 <= ry1:
        return

    step  = max(1, spacing)
    c     = ry1 - rx2   # first line value
    c_max = ry2 - rx1   # last line value

    while c <= c_max:
        pts: list[tuple[int, int]] = []

        # ── left edge  x = rx1  →  y = rx1 + c ─────────────────────────
        y = rx1 + c
        if ry1 <= y <= ry2:
            pts.append((rx1, int(y)))

        # ── top edge   y = ry1  →  x = ry1 − c ─────────────────────────
        x = ry1 - c
        if rx1 < x < rx2:          # strict inequality avoids corner duplicates
            pts.append((int(x), ry1))

        # ── right edge x = rx2  →  y = rx2 + c ─────────────────────────
        y = rx2 + c
        if ry1 <= y <= ry2:
            pts.append((rx2, int(y)))

        # ── bottom edge y = ry2 →  x = ry2 − c ─────────────────────────
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
    """
    Procedurally render a game UI frame on a transparent RGBA canvas.

    Layer order (back to front)
    ───────────────────────────
    1. Outer glow   — 5 concentric rings at decreasing alpha (NO blur)
    2. Outer border — full-brightness color, `border_thickness` px thick
    3. Gap          — transparent space between the two borders
    4. Inner border — 65 % brightness, 1 px thinner
    5. Pattern      — 45 ° diagonal lines clipped to interior (optional)
    6. Rounded mask — applied last to clip all layers to rounded shape

    corner_radius = 0  →  sharp square corners (default)
    corner_radius > 0  →  rounded corners; inner border radius auto-adjusted
    """
    base = get_color(color_name)
    bt   = max(1, border_thickness)
    gap  = bt + 3          # pixels between outer border edge and inner border
    cr   = max(0, corner_radius)            # outer corner radius
    cr_i = max(0, cr - gap)                 # inner border corner radius (concentric)

    img  = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── 1. Outer glow (stacked semi-transparent concentric rects) ─────────
    # Drawn inside the image boundary; border covers the innermost layers.
    # Alpha steps: 10 → 20 → 30 → 40 → 50  (outermost → innermost)
    for g in range(5, 0, -1):
        draw.rectangle(
            [g, g, width - 1 - g, height - 1 - g],
            outline=rgba(base, 10 * g),
            width=1,
        )

    # ── 2. Outer border ───────────────────────────────────────────────────
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

    # ── 3 + 4. Inner border ───────────────────────────────────────────────
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

    # ── 5. Interior diagonal line pattern (optional) ──────────────────────
    if enable_pattern:
        # Clip region: just inside the inner border
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

    # ── 6. Rounded corner mask ────────────────────────────────────────────
    # Clips every layer drawn above (including the glow rings) to the
    # rounded rectangle shape.  At cr=0 this step is skipped entirely.
    if cr > 0:
        mask      = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            [0, 0, width - 1, height - 1],
            radius=cr,
            fill=255,
        )
        # Multiply existing per-pixel alpha with the shape mask
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
    """Utility: render a vertical linear-gradient RGBA image."""
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
    """
    NORMAL state
    ─────────────
    • Dark vertical gradient (top slightly lighter → bottom darker)
    • Thin colored border at 200 alpha
    • 1-px inner top highlight at low opacity
    """
    base = get_color(color_name)
    bt   = max(1, border_thickness)

    img  = _vertical_gradient(width, height, (18, 21, 33), (10, 13, 22), 242)
    draw = ImageDraw.Draw(img)

    # Colored border
    draw.rectangle(
        [0, 0, width - 1, height - 1],
        outline=rgba(base, 200),
        width=bt,
    )

    # Subtle 1-px highlight just inside the top border
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
    """
    HOVERED state
    ──────────────
    • Lighter gradient base + translucent color tint overlay
    • Inner glow: 5 concentric rings at decreasing alpha (no blur)
    • Brighter border at full opacity
    • Gradient inner highlight block at top (~1/5 height)
    • Crisp 1-px top highlight line at high opacity
    """
    base   = get_color(color_name)
    bright = dim(base, 1.35)
    bt     = max(1, border_thickness)

    # Lighter background
    img = _vertical_gradient(width, height, (26, 30, 46), (16, 19, 31), 245)

    # Translucent color tint overlay (gives button a "colored" feel on hover)
    tint = Image.new("RGBA", (width, height), rgba(base, 20))
    img  = Image.alpha_composite(img, tint)

    draw = ImageDraw.Draw(img)

    # Inner glow rings (simulate outer glow within the image boundary)
    # Drawn before the border so the border sits cleanly on top.
    for g in range(6, 0, -1):
        draw.rectangle(
            [g, g, width - 1 - g, height - 1 - g],
            outline=rgba(base, 10 * g),   # 10 → 20 → 30 → 40 → 50 → 60
            width=1,
        )

    # Bright border
    draw.rectangle(
        [0, 0, width - 1, height - 1],
        outline=rgba(bright, 255),
        width=bt,
    )

    # Gradient inner highlight block (top portion of button interior)
    hl   = dim(base, 1.9)
    hl_h = max(2, height // 5)
    for y in range(hl_h):
        t     = 1.0 - y / hl_h           # 1.0 (top) → 0.0 (bottom) — fades out
        alpha = int(50 * t)
        draw.line(
            [(bt, bt + y), (width - 1 - bt, bt + y)],
            fill=rgba(hl, alpha),
        )

    # Crisp 1-px top highlight line
    draw.line(
        [(bt, bt), (width - 1 - bt, bt)],
        fill=rgba(hl, 210),
        width=1,
    )

    return img


def generate_button_clicked(
    width: int, height: int, color_name: str, border_thickness: int = 2
) -> Image.Image:
    """
    CLICKED state
    ──────────────
    • Darker gradient (pressed-into-surface feel)
    • Dimmer border at 55 % brightness
    • Inner shadow: gradient dark overlay on top edge (top → transparent)
    • Inner shadow: gradient dark overlay on left edge (left → transparent)
    • Reverse highlights on bottom + right edges (lifted-edge illusion)
    """
    base = get_color(color_name)
    dark = dim(base, 0.55)
    bt   = max(1, border_thickness)

    # Darker background
    img  = _vertical_gradient(width, height, (9, 11, 19), (6, 8, 15), 255)
    draw = ImageDraw.Draw(img)

    # Dimmer border
    draw.rectangle(
        [0, 0, width - 1, height - 1],
        outline=rgba(dark, 210),
        width=bt,
    )

    # Inner shadow — top edge (gradient: opaque black → transparent)
    sh = max(3, height // 6)
    for i in range(sh):
        t = (1.0 - i / sh) ** 1.6      # non-linear falloff for realism
        draw.line(
            [(bt, bt + i), (width - 1 - bt, bt + i)],
            fill=(0, 0, 0, int(115 * t)),
        )

    # Inner shadow — left edge
    sw = max(2, width // 10)
    for i in range(sw):
        t = (1.0 - i / sw) ** 1.6
        draw.line(
            [(bt + i, bt), (bt + i, height - 1 - bt)],
            fill=(0, 0, 0, int(75 * t)),
        )

    # Reverse highlights (bottom + right appear lighter → pressed look)
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

@app.route("/")
def index() -> str:
    """Serve the main UI page."""
    return render_template("index.html", colors=list(PALETTE.keys()))


@app.route("/api/frame", methods=["POST"])
@limiter.limit("30 per minute; 200 per hour")
def api_frame():
    """
    POST body (JSON):
      width, height, color, border_thickness,
      enable_pattern, pattern_spacing, pattern_thickness,
      corner_radius
    Returns: PNG image (inline — client decides download vs. preview).
    """
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


@app.route("/api/button/<state>", methods=["POST"])
@limiter.limit("30 per minute; 200 per hour")
def api_button(state: str):
    """
    POST body (JSON):  width, height, color, border_thickness
    URL param <state>: normal | hovered | clicked
    Returns: PNG image (inline).
    """
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


@app.route("/api/buttons/zip", methods=["POST"])
@limiter.limit("15 per minute; 100 per hour")
def api_buttons_zip():
    """
    POST body (JSON):  width, height, color, border_thickness
    Returns: ZIP archive containing all 3 button-state PNGs.
    """
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


@app.route("/api/remove-bg", methods=["POST"])
@limiter.limit("10 per minute; 40 per hour")
def api_remove_bg():
    """
    Remove the background from an uploaded image and return a transparent PNG.

    Expects multipart/form-data with a single field named 'image'.
    Accepted formats: PNG, JPG/JPEG, WEBP, BMP — max 15 MB.

    Uses the rembg library (U²-Net neural network, runs fully offline).
    On first call the model is downloaded automatically (~170 MB, cached
    in ~/.u2net/ for all future calls).
    """
    if not REMBG_AVAILABLE:
        return jsonify({
            "error": "rembg is not installed. Run: pip install rembg"
        }), 503

    if "image" not in request.files:
        return jsonify({"error": "No image field in request."}), 400

    file = request.files["image"]
    if not file or file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    # Validate extension
    ALLOWED = {"png", "jpg", "jpeg", "webp", "bmp"}
    ext = (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "").lower()
    if ext not in ALLOWED:
        return jsonify({
            "error": f"Unsupported format '{ext}'. Accepted: {', '.join(sorted(ALLOWED))}"
        }), 415

    input_bytes  = file.read()
    output_bytes = _rembg_remove(input_bytes)   # returns PNG bytes with alpha

    buf = io.BytesIO(output_bytes)
    buf.seek(0)

    # Preserve original stem, always output as PNG (transparency requires PNG)
    stem     = os.path.splitext(file.filename)[0]
    filename = f"{stem}_nobg.png"
    return send_file(buf, mimetype="image/png",
                     as_attachment=False, download_name=filename)


@app.errorhandler(413)
def too_large(_e):
    """File exceeds MAX_CONTENT_LENGTH."""
    return jsonify({"error": "File too large. Maximum upload size is 15 MB."}), 413


# ══════════════════════════════════════════════════════════════════════════════
# LAUNCH HELPERS
# ══════════════════════════════════════════════════════════════════════════════

# Cloud platforms set well-known environment variables.
# We use their presence to decide local vs. production behaviour.
_CLOUD_SIGNALS = ("RENDER", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME", "DYNO")

def _is_local() -> bool:
    """Return True when running on a developer's machine, False on any cloud host."""
    return not any(os.environ.get(sig) for sig in _CLOUD_SIGNALS)


def find_free_port(start: int = 5000) -> int:
    """Scan ports from `start` upward and return the first available one."""
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Could not find a free port in range 5000-5100.")


def _open_browser(port: int) -> None:
    """Wait briefly for Flask to start, then open the default browser."""
    import time
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    local = _is_local()

    # Cloud platforms inject PORT; locally we scan for a free one.
    port = int(os.environ.get("PORT", find_free_port() if local else 8080))

    # Bind to localhost only when running locally (safer).
    # Bind to all interfaces in production so the platform can route traffic.
    host = "127.0.0.1" if local else "0.0.0.0"

    print(f"\n  ◈ CMG Forge — PixelForge")
    print(f"  ──────────────────────────")
    print(f"  Mode    →  {'local' if local else 'production'}")
    print(f"  Server  →  http://{host}:{port}")
    print(f"  Press   Ctrl+C to stop\n")

    # Auto-open browser only on local — no desktop on a cloud server.
    if local:
        threading.Thread(target=_open_browser, args=(port,), daemon=True).start()

    app.run(debug=False, host=host, port=port)
