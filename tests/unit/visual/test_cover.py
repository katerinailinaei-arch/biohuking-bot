from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from bodrye_bot.visual.cover import DEFAULT_LOGO_PATH, LogoPlace, brand_cover, default_logo_bytes


def _jpeg(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _striped_source() -> bytes:
    image = Image.new("RGB", (200, 100), (0, 180, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 199, 19), fill=(220, 0, 0))
    draw.rectangle((0, 80, 199, 99), fill=(0, 0, 220))
    return _jpeg(image)


def _yellow_badge() -> bytes:
    image = Image.new("RGB", (64, 64), (255, 220, 0))
    return _jpeg(image)


def test_cover_crops_top_and_bottom_chrome() -> None:
    result = Image.open(
        BytesIO(
            brand_cover(
                _striped_source(),
                crop_top=0.2,
                crop_bottom=0.2,
            )
        )
    )

    assert result.size == (200, 60)
    middle = result.getpixel((100, 30))
    assert middle[1] > middle[0]
    assert middle[1] > middle[2]


def test_cover_stamps_logo_in_bottom_left() -> None:
    image = Image.new("RGB", (400, 400), (0, 180, 0))
    result = Image.open(
        BytesIO(
            brand_cover(
                _jpeg(image),
                logo=_yellow_badge(),
                stamp_logo=True,
            )
        )
    )
    logo_center = result.getpixel((50, result.height - 50))
    assert logo_center[0] > 180
    assert logo_center[1] > 150
    assert logo_center[2] < 80


def test_cover_logo_top_right_leaves_bottom_left_clean() -> None:
    image = Image.new("RGB", (400, 400), (0, 180, 0))
    result = Image.open(
        BytesIO(
            brand_cover(
                _jpeg(image),
                logo=_yellow_badge(),
                stamp_logo=True,
                logo_place=LogoPlace.TOP_RIGHT,
            )
        )
    )
    top_right = result.getpixel((350, 40))
    bottom_left = result.getpixel((40, 360))
    assert top_right[0] > 180
    assert bottom_left[1] > 140
    assert bottom_left[0] < 80


def test_default_brand_logo_is_packaged() -> None:
    assert DEFAULT_LOGO_PATH.is_file()
    assert len(default_logo_bytes()) > 1000


def test_cover_without_edits_keeps_full_frame() -> None:
    result = Image.open(BytesIO(brand_cover(_striped_source())))

    assert result.size == (200, 100)
    top = result.getpixel((100, 8))
    assert top[0] > 180


def test_cover_paints_caption_on_top_banner() -> None:
    result = Image.open(
        BytesIO(
            brand_cover(
                _striped_source(),
                overlay_text="Ночью неудобно, утром — куда ни ляг",
                crop_top=0.2,
                crop_bottom=0.2,
            )
        )
    )
    banner = result.getpixel((100, 8))
    assert banner[0] > 200
    assert banner[1] > 200
    assert banner[2] > 180
