from __future__ import annotations

from enum import StrEnum
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bodrye_bot.domain.errors import SafeError, SafeErrorCode

_BRAND = Path(__file__).resolve().parent / "brand"
DEFAULT_LOGO_PATH = _BRAND / "badge.jpg"
CROP_TOP = 0.18
CROP_BOTTOM = 0.10
LOGO_RATIO = 0.20
LOGO_RATIO_MIN = 0.08
LOGO_RATIO_MAX = 0.45
LOGO_RATIO_STEP = 0.05
MARGIN_RATIO = 0.03
BANNER_COLOR = (250, 244, 230, 235)
TEXT_COLOR = (18, 56, 38, 255)
_FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


class LogoPlace(StrEnum):
    TOP_LEFT = "tl"
    TOP_CENTER = "tc"
    TOP_RIGHT = "tr"
    MID_LEFT = "ml"
    CENTER = "c"
    MID_RIGHT = "mr"
    BOTTOM_LEFT = "bl"
    BOTTOM_CENTER = "bc"
    BOTTOM_RIGHT = "br"


def default_logo_bytes() -> bytes:
    return DEFAULT_LOGO_PATH.read_bytes()


def brand_cover(
    image: bytes,
    *,
    overlay_text: str = "",
    logo: bytes | None = None,
    crop_top: float = 0.0,
    crop_bottom: float = 0.0,
    stamp_logo: bool = False,
    logo_place: LogoPlace = LogoPlace.BOTTOM_LEFT,
    logo_ratio: float = LOGO_RATIO,
) -> bytes:
    try:
        source = Image.open(BytesIO(image)).convert("RGBA")
        cropped = _crop_chrome(source, crop_top=crop_top, crop_bottom=crop_bottom)
        if overlay_text.strip():
            cropped = _paint_banner(cropped, overlay_text.strip())
        if stamp_logo:
            cropped = _stamp_logo(
                cropped,
                logo if logo is not None else default_logo_bytes(),
                place=logo_place,
                ratio=logo_ratio,
            )
        output = BytesIO()
        cropped.convert("RGB").save(output, format="JPEG", quality=92)
        return output.getvalue()
    except SafeError:
        raise
    except Exception as error:
        raise SafeError.for_code(
            SafeErrorCode.COVER_FAILED, developer_detail=type(error).__name__
        ) from error


def _crop_chrome(image: Image.Image, *, crop_top: float, crop_bottom: float) -> Image.Image:
    width, height = image.size
    top = int(height * max(0.0, min(crop_top, 0.4)))
    bottom = int(height * max(0.0, min(crop_bottom, 0.4)))
    lower = height - bottom
    if lower - top < 32:
        return image
    return image.crop((0, top, width, lower))


def _stamp_logo(
    image: Image.Image,
    logo_bytes: bytes,
    *,
    place: LogoPlace,
    ratio: float,
) -> Image.Image:
    canvas = image.copy()
    size = max(24, int(min(canvas.size) * max(LOGO_RATIO_MIN, min(ratio, LOGO_RATIO_MAX))))
    badge = _circular_badge(logo_bytes, size=size)
    left, top = _logo_origin(canvas.size, badge.size, place)
    canvas.alpha_composite(badge, dest=(left, top))
    return canvas


def _logo_origin(
    canvas: tuple[int, int], badge: tuple[int, int], place: LogoPlace
) -> tuple[int, int]:
    width, height = canvas
    badge_w, badge_h = badge
    margin = max(8, int(min(width, height) * MARGIN_RATIO))
    left = {
        LogoPlace.TOP_LEFT: margin,
        LogoPlace.MID_LEFT: margin,
        LogoPlace.BOTTOM_LEFT: margin,
        LogoPlace.TOP_CENTER: (width - badge_w) // 2,
        LogoPlace.CENTER: (width - badge_w) // 2,
        LogoPlace.BOTTOM_CENTER: (width - badge_w) // 2,
        LogoPlace.TOP_RIGHT: width - badge_w - margin,
        LogoPlace.MID_RIGHT: width - badge_w - margin,
        LogoPlace.BOTTOM_RIGHT: width - badge_w - margin,
    }[place]
    top = {
        LogoPlace.TOP_LEFT: margin,
        LogoPlace.TOP_CENTER: margin,
        LogoPlace.TOP_RIGHT: margin,
        LogoPlace.MID_LEFT: (height - badge_h) // 2,
        LogoPlace.CENTER: (height - badge_h) // 2,
        LogoPlace.MID_RIGHT: (height - badge_h) // 2,
        LogoPlace.BOTTOM_LEFT: height - badge_h - margin,
        LogoPlace.BOTTOM_CENTER: height - badge_h - margin,
        LogoPlace.BOTTOM_RIGHT: height - badge_h - margin,
    }[place]
    return left, top


def _circular_badge(logo_bytes: bytes, *, size: int) -> Image.Image:
    logo = Image.open(BytesIO(logo_bytes)).convert("RGBA")
    side = min(logo.size)
    left = (logo.width - side) // 2
    top = (logo.height - side) // 2
    square = logo.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.Resampling.LANCZOS
    )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, size - 2, size - 2), fill=255)
    square.putalpha(mask)
    return square


def _paint_banner(image: Image.Image, text: str) -> Image.Image:
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = _caption_font(max(22, int(canvas.width * 0.045)))
    max_width = int(canvas.width * 0.88)
    lines = _wrap_text(draw, text, font, max_width)
    font_size = int(getattr(font, "size", 22))
    line_height = int(font_size * 1.25)
    padding = max(16, int(canvas.height * 0.025))
    banner_height = padding * 2 + line_height * len(lines)
    banner_height = min(banner_height, int(canvas.height * 0.42))
    draw.rectangle((0, 0, canvas.width, banner_height), fill=BANNER_COLOR)
    y = padding
    for line in lines:
        width = draw.textlength(line, font=font)
        x = max(0, int((canvas.width - width) / 2))
        draw.text((x, y), line, font=font, fill=TEXT_COLOR)
        y += line_height
        if y > banner_height - padding:
            break
    return canvas


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines or [text]


def _caption_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


__all__ = [
    "CROP_BOTTOM",
    "CROP_TOP",
    "DEFAULT_LOGO_PATH",
    "LOGO_RATIO",
    "LOGO_RATIO_MAX",
    "LOGO_RATIO_MIN",
    "LOGO_RATIO_STEP",
    "LogoPlace",
    "brand_cover",
    "default_logo_bytes",
]
