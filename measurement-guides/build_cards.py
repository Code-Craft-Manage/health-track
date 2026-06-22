#!/usr/bin/env python3
"""Assemble finished measurement-guide cards: photo on top + numbered steps below.

Reads the photoreal images in ``photos/<key>.png`` and emits a self-contained
card (``cards/<key>.svg`` + ``cards/<key>.png``) that mirrors the original
reference layout. Pure standard library; rasterize with rsvg-convert.

    python3 build_cards.py        # writes cards/*.svg and cards/*.png
"""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
PHOTOS = HERE / "photos"
OUT = HERE / "cards"

# --- palette / layout -----------------------------------------------------
TEXT = "#2B2F36"
ACCENT = "#1FA98F"
FONT = "Helvetica, 'Helvetica Neue', Arial, sans-serif"

W = 760
M = 30                      # outer margin
PW = W - 2 * M             # photo width (700)


def png_size(path):
    """Read (width, height) from a PNG header — no external deps."""
    b = Path(path).read_bytes()[:24]
    return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")

FS = 30
LH = 42
STEP_GAP = 22
TEXT_X = M + 64
WRAP_X = W - M
BADGE_CX = M + 26


# --- text wrapping with inline **bold** -----------------------------------
def _runs(text):
    out = []
    for i, part in enumerate(text.split("**")):
        bold = i % 2 == 1
        for w in part.split(" "):
            if w:
                out.append((w, bold))
    return out


def _wcalc(word, bold):
    return len(word) * FS * (0.585 if bold else 0.545) + FS * 0.30


def _wrap(text, max_w):
    lines, cur, cur_w = [], [], 0.0
    for word, bold in _runs(text):
        w = _wcalc(word, bold)
        if cur and cur_w + w > max_w:
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur.append((word, bold))
        cur_w += w
    if cur:
        lines.append(cur)
    return lines


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_steps(steps, y0):
    out, y = [], y0
    for n, step in enumerate(steps, 1):
        lines = _wrap(step, WRAP_X - TEXT_X)
        by = y - FS * 0.34
        out.append(
            f'<circle cx="{BADGE_CX}" cy="{by:.0f}" r="23" fill="{ACCENT}"/>'
            f'<text x="{BADGE_CX}" y="{by + 9:.0f}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="27" font-weight="700" '
            f'fill="#fff">{n}</text>'
        )
        spans = []
        for li, line in enumerate(lines):
            runs = []
            for i, (word, bold) in enumerate(line):
                glue = i == 0 or word[:1] in ",.;:!?)%"
                token = ("" if glue else " ") + word
                if runs and runs[-1][1] == bold:
                    runs[-1][0] += token
                else:
                    runs.append([token, bold])
            inner = "".join(
                (f'<tspan font-weight="700">{_esc(t)}</tspan>' if b
                 else f'<tspan>{_esc(t)}</tspan>') for t, b in runs
            )
            dy = "" if li == 0 else f' dy="{LH}"'
            spans.append(f'<tspan x="{TEXT_X}"{dy}>{inner}</tspan>')
        out.append(
            f'<text y="{y}" xml:space="preserve" font-family="{FONT}" '
            f'font-size="{FS}" fill="{TEXT}">{"".join(spans)}</text>'
        )
        y += len(lines) * LH + STEP_GAP
    return "\n".join(out), y


# --- step text (cleaned from the originals) -------------------------------
CARDS = {
    "neck": ("How to measure your NECK", [
        "Stand upright with **shoulders relaxed** and your spine neutral",
        "Wrap the tape around the **base of your neck**, just above the shoulders",
        "Keep your head in a **neutral position**, looking straight ahead",
        "Keep the **tape flat** and snug, without twisting or gaps",
        "**Record** the measurement where the tape meets",
    ]),
    "shoulders": ("How to measure your SHOULDERS", [
        "**Place the tape** at the outer tip of one shoulder",
        "**Bring it across your back** to the other shoulder tip",
        "**Wrap it around the front** to meet the starting point",
        "Keep the tape **level and snug**, not tight",
        "**Read the number** where the tape meets",
    ]),
    "chest": ("How to measure your CHEST", [
        "**Wrap the tape** around the fullest part of your chest",
        "Keep the **tape level** all the way around your torso",
        "Keep it **snug but not tight** against the skin",
        "**Exhale** and let your rib cage relax to its resting size",
        "**Record** the measurement where the tape meets",
    ]),
    "biceps": ("How to measure your BICEPS", [
        "**Raise your arm** to shoulder height",
        "**Bend your elbow** to 90 degrees, making an “L” shape",
        "**Flex your biceps** and keep the muscle tight",
        "**Wrap the tape** around the peak of the biceps, at its widest point",
        "**Record** to the nearest 0.1 cm",
    ]),
    "waist": ("How to measure your WAIST", [
        "Stand with **feet together** and shoulders relaxed",
        "Find the **narrowest part** of your torso, between ribs and hips",
        "**Wrap the tape** parallel to the floor, snug but **not tight**",
        "**Exhale** naturally and record the measurement",
    ]),
    "abdomen": ("How to measure your ABDOMEN", [
        "Find the **widest part** of your stomach, around the belly button",
        "**Wrap the tape** all the way around at this level",
        "Keep the tape **straight and flat** against your skin",
        "**Exhale** normally and let your abdomen relax",
        "Make sure it is **snug, not tight**, then record",
    ]),
    "hips": ("How to measure your HIPS", [
        "Stand upright with your **feet together**",
        "Locate the **widest part of your hips** and glutes",
        "**Wrap the tape parallel to the floor**, snug but not tight",
        "**Record** the measurement",
    ]),
    "thigh": ("How to measure your THIGH", [
        "Stand with your **feet hip-width apart**",
        "Find the **midpoint** between your hip and knee",
        "**Wrap the tape** around the widest part, **snug but not tight**",
        "**Record** the measurement",
    ]),
    "weight": ("How to measure your WEIGHT", [
        "**Wear minimal clothing** for accuracy",
        "**Stand in the centre** of the scale platform",
        "Keep still and **wait** for the reading to settle",
        "**Record your weight** to the nearest 0.1 kg",
    ]),
    "calf": ("How to measure your CALF", [
        "Stand with your **feet hip-width apart**",
        "**Wrap the tape** around the **widest part** of your calf",
        "Keep the tape **flat and snug**, not tight",
        "**Record** the measurement",
    ]),
}


def build(key, title, steps):
    photo = PHOTOS / f"{key}.png"
    pw_px, ph_px = png_size(photo)
    ph = round(PW * ph_px / pw_px)          # panel height keeps the photo's aspect
    data = base64.b64encode(photo.read_bytes()).decode("ascii")
    href = f"data:image/png;base64,{data}"

    title_y = M + ph + 56
    steps_y0 = M + ph + 120
    steps_svg, end_y = render_steps(steps, steps_y0)
    H = int(end_y + 36)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">
  <defs><clipPath id="pclip"><rect x="{M}" y="{M}" width="{PW}" height="{ph}" rx="28"/></clipPath></defs>
  <rect width="{W}" height="{H}" fill="#FFFFFF"/>
  <image x="{M}" y="{M}" width="{PW}" height="{ph}" preserveAspectRatio="xMidYMid meet" clip-path="url(#pclip)" href="{href}"/>
  <rect x="{M}" y="{M}" width="{PW}" height="{ph}" rx="28" fill="none" stroke="#E4E8EC" stroke-width="2"/>
  <text x="{W/2:.0f}" y="{title_y}" text-anchor="middle" font-size="33" font-weight="700" fill="{TEXT}">{_esc(title)}</text>
  {steps_svg}
</svg>
'''


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for key, (title, steps) in CARDS.items():
        svg_path = OUT / f"{key}.svg"
        png_path = OUT / f"{key}.png"
        svg_path.write_text(build(key, title, steps), encoding="utf-8")
        subprocess.run(
            ["rsvg-convert", "-w", str(W), str(svg_path), "-o", str(png_path)],
            check=True,
        )
        print(f"built cards/{key}.png")


if __name__ == "__main__":
    main()
