"""Create wizard images for Inno Setup installer."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def create_wizard_images():
    logo = Path("logo.png")
    if not logo.exists():
        print("[ERROR] logo.png not found!")
        return

    img = Image.open(logo).convert("RGBA")

    # Large wizard image (164x314)
    large = Image.new("RGBA", (164, 314), (10, 14, 20, 255))
    logo_resized = img.resize((120, 120), Image.Resampling.LANCZOS)
    large.paste(logo_resized, (22, 40), logo_resized)

    # Add title text
    draw = ImageDraw.Draw(large)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
        small_font = ImageFont.truetype("arial.ttf", 11)
    except Exception:
        font = ImageFont.load_default()
        small_font = font

    # FreQ title
    bbox = draw.textbbox((0, 0), "FreQ", font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((164 - tw) // 2, 175), "FreQ", fill=(88, 166, 255), font=font)

    # Subtitle
    sub = "Radio Playlist"
    bbox2 = draw.textbbox((0, 0), sub, font=small_font)
    sw = bbox2[2] - bbox2[0]
    draw.text(((164 - sw) // 2, 205), sub, fill=(139, 149, 165), font=small_font)

    sub2 = "Manager"
    bbox3 = draw.textbbox((0, 0), sub2, font=small_font)
    sw2 = bbox3[2] - bbox3[0]
    draw.text(((164 - sw2) // 2, 222), sub2, fill=(139, 149, 165), font=small_font)

    # Version
    ver = "v2.0.0"
    bbox4 = draw.textbbox((0, 0), ver, font=small_font)
    vw = bbox4[2] - bbox4[0]
    draw.text(((164 - vw) // 2, 260), ver, fill=(78, 87, 105), font=small_font)

    large.save("wizard_large.bmp")
    print("[OK] wizard_large.bmp (164x314)")

    # Small wizard image (55x58)
    small = Image.new("RGBA", (55, 58), (10, 14, 20, 255))
    logo_small = img.resize((48, 48), Image.Resampling.LANCZOS)
    small.paste(logo_small, (3, 5), logo_small)
    small.save("wizard_small.bmp")
    print("[OK] wizard_small.bmp (55x58)")

if __name__ == "__main__":
    create_wizard_images()
