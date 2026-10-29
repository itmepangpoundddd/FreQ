"""Create wizard images for Inno Setup installer."""
import argparse
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

PROJECT_DIR = Path(__file__).resolve().parent


def installer_version() -> str:
    """Read the canonical application version from the Inno Setup script."""
    installer_script = PROJECT_DIR / "installer.iss"
    content = installer_script.read_text(encoding="utf-8")
    match = re.search(r'^\s*#define\s+MyAppVersion\s+"([^"]+)"', content, re.MULTILINE)
    if not match:
        raise ValueError(f"MyAppVersion was not found in {installer_script}")
    return match.group(1)


def create_wizard_images():
    logo = PROJECT_DIR / "logo.png"
    if not logo.exists():
        # Fail loudly: exiting 0 here would let the release pipeline package
        # stale wizard BMPs from a previous build with no error shown.
        raise RuntimeError(
            f"logo.png not found in {PROJECT_DIR} — cannot generate wizard images."
        )

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
    ver = f"v{installer_version()}"
    bbox4 = draw.textbbox((0, 0), ver, font=small_font)
    vw = bbox4[2] - bbox4[0]
    draw.text(((164 - vw) // 2, 260), ver, fill=(78, 87, 105), font=small_font)

    large.save(PROJECT_DIR / "wizard_large.bmp")
    print("[OK] wizard_large.bmp (164x314)")

    # Small wizard image (55x58)
    small = Image.new("RGBA", (55, 58), (10, 14, 20, 255))
    logo_small = img.resize((48, 48), Image.Resampling.LANCZOS)
    small.paste(logo_small, (3, 5), logo_small)
    small.save(PROJECT_DIR / "wizard_small.bmp")
    print("[OK] wizard_small.bmp (55x58)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Render wizard_large.bmp (164x314) and wizard_small.bmp "
                    "(55x58) for the Inno Setup installer from logo.png, "
                    "stamped with the version from installer.iss. "
                    "release.py runs this automatically during a build.",
        epilog="example:  python create_wizard_images.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.parse_args()
    create_wizard_images()
