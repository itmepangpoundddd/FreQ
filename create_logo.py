#!/usr/bin/env python3
"""
Create FreQ logo as PNG using Pillow
Run: python create_logo.py
"""

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Requires Pillow: pip install Pillow")
    exit(1)


def create_logo(size: int = 200, output: str = "logo.png") -> Image.Image:
    """Create FreQ logo of size x size"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    radius = int(size * 0.45)

    # ── Background circle ──
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=(13, 17, 23, 255),       # #0d1117
        outline=(48, 54, 61, 255),    # #30363d
        width=2,
    )

    # ── Sound wave bars ──
    bar_width = int(size * 0.04)
    gap = int(size * 0.025)
    bar_heights = [0.22, 0.35, 0.50, 0.65, 0.50, 0.35, 0.22]
    n_bars = len(bar_heights)
    total_width = n_bars * bar_width + (n_bars - 1) * gap
    start_x = cx - total_width // 2
    wave_top = cy - int(size * 0.08)  # center the waves slightly above center

    # Gradient colors for bars (blue → purple → pink)
    colors = [
        (88, 166, 255),   # blue
        (100, 170, 255),
        (140, 160, 255),
        (188, 140, 255),  # purple
        (220, 120, 230),
        (240, 110, 200),
        (247, 120, 186),  # pink
    ]

    for i, (h_ratio, color) in enumerate(zip(bar_heights, colors)):
        bar_h = int(size * h_ratio)
        x = start_x + i * (bar_width + gap)
        y_top = wave_top - bar_h // 2
        y_bot = y_top + bar_h
        r = bar_width // 2
        draw.rounded_rectangle(
            [x, y_top, x + bar_width, y_bot],
            radius=r,
            fill=color,
        )

    # ── "FreQ" text ──
    text_y = cy + int(size * 0.20)
    try:
        font = ImageFont.truetype("arialbd.ttf", int(size * 0.20))
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", int(size * 0.20))
        except OSError:
            font = ImageFont.load_default()

    text = "FreQ"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(
        (cx - tw // 2, text_y),
        text,
        fill=(230, 237, 243, 255),  # #e6edf3
        font=font,
    )

    # ── Decorative dots ──
    dot_y = cy + int(size * 0.40)
    dot_r = int(size * 0.012)
    for dx, c in [(-int(size*0.10), (88,166,255)), (0, (188,140,255)), (int(size*0.10), (247,120,186))]:
        draw.ellipse(
            [cx+dx-dot_r, dot_y-dot_r, cx+dx+dot_r, dot_y+dot_r],
            fill=c,
        )

    img.save(output, "PNG")
    return img


if __name__ == "__main__":
    logo = create_logo(400, "logo.png")
    print(f"✅ Created logo.png ({logo.size[0]}x{logo.size[1]})")
