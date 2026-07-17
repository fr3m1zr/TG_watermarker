"""Telegram bot that adds EXIF details and a watermark to image files."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

register_heif_opener()

LOGGER = logging.getLogger(__name__)
EXIF_IFD = 34665
PANEL_BACKGROUND = (247, 245, 240)
PANEL_INK = (28, 29, 31)
PANEL_MUTED = (119, 116, 110)
PANEL_ACCENT = (190, 105, 67)
PANEL_HEIGHT_RATIO = 0.078
PANEL_MIN_HEIGHT = 78
BRAND_ICON_DIR = Path(__file__).resolve().parent / "assets" / "brands"
LENS_BADGE_DIR = Path(__file__).resolve().parent / "assets" / "lenses"
BRAND_NAMES = {
    "sony": "SONY",
    "nikon": "NIKON",
    "canon": "Canon",
    "leica": "LEICA",
    "apple": "APPLE",
    "ricoh": "RICOH",
    "fujifilm": "FUJIFILM",
    "panasonic": "LUMIX",
    "olympus": "OLYMPUS",
    "om digital": "OM SYSTEM",
    "pentax": "PENTAX",
    "hasselblad": "HASSELBLAD",
}


@dataclass(frozen=True)
class LensAlias:
    brand: str
    source: str
    display: str


def _lens_aliases(
    brand: str, aliases: tuple[tuple[str, str], ...]
) -> tuple[LensAlias, ...]:
    return tuple(
        LensAlias(brand=brand, source=source, display=display)
        for source, display in aliases
    )


LENS_NAME_ALIASES = (
    *_lens_aliases("sony", (
    # Sony FE zoom lenses
    ("FE 12-24mm F2.8 GM", "FE 12-24mm F2.8 GM"),
    ("FE 12-24mm F4 G", "FE 12-24mm F4 G"),
    ("FE 16-25mm F2.8 G", "FE 16-25mm F2.8 G"),
    ("FE 16-35mm F2.8 GM II", "FE 16-35mm F2.8 GM II"),
    ("FE 16-35mm F2.8 GM", "FE 16-35mm F2.8 GM"),
    ("FE PZ 16-35mm F4 G", "FE PZ 16-35mm F4 G"),
    ("FE C 16-35mm T3.1 G", "FE C 16-35mm T3.1 G"),
    ("FE 16-35mm F4 ZA OSS", "FE 16-35mm F4 ZA"),
    ("Vario-Tessar T* FE 16-35mm F4 ZA OSS", "FE 16-35mm F4 ZA"),
    ("FE 20-70mm F4 G", "FE 20-70mm F4 G"),
    ("FE 24-50mm F2.8 G", "FE 24-50mm F2.8 G"),
    ("FE 24-70mm F2.8 GM II", "FE 24-70mm F2.8 GM II"),
    ("FE 24-70mm F2.8 GM", "FE 24-70mm F2.8 GM"),
    ("FE 24-70mm F4 ZA OSS", "FE 24-70mm F4 ZA"),
    ("Vario-Tessar T* FE 24-70mm F4 ZA OSS", "FE 24-70mm F4 ZA"),
    ("FE 24-105mm F4 G OSS", "FE 24-105mm F4 G"),
    ("FE 24-240mm F3.5-6.3 OSS", "FE 24-240mm F3.5-6.3"),
    ("FE 28-60mm F4-5.6", "FE 28-60mm F4-5.6"),
    ("FE 28-70mm F2 GM", "FE 28-70mm F2 GM"),
    ("FE 28-70mm F3.5-5.6 OSS II", "FE 28-70mm F3.5-5.6 II"),
    ("FE 28-70mm F3.5-5.6 OSS", "FE 28-70mm F3.5-5.6"),
    ("FE PZ 28-135mm F4 G OSS", "FE PZ 28-135mm F4 G"),
    ("FE 50-150mm F2 GM", "FE 50-150mm F2 GM"),
    ("FE 70-200mm F2.8 GM OSS II", "FE 70-200mm F2.8 GM II"),
    ("FE 70-200mm F2.8 GM OSS", "FE 70-200mm F2.8 GM"),
    ("FE 70-200mm F4 Macro G OSS II", "FE 70-200mm F4 Macro G II"),
    ("FE 70-200mm F4 G OSS", "FE 70-200mm F4 G"),
    ("FE 70-300mm F4.5-5.6 G OSS", "FE 70-300mm F4.5-5.6 G"),
    ("FE 100-400mm F4.5-5.6 GM OSS", "FE 100-400mm F4.5-5.6 GM"),
    ("FE 200-600mm F5.6-6.3 G OSS", "FE 200-600mm F5.6-6.3 G"),
    ("FE 400-800mm F6.3-8 G OSS", "FE 400-800mm F6.3-8 G"),
    # Sony FE prime lenses
    ("FE 14mm F1.8 GM", "FE 14mm F1.8 GM"),
    ("FE 16mm F1.8 G", "FE 16mm F1.8 G"),
    ("FE 20mm F1.8 G", "FE 20mm F1.8 G"),
    ("FE 24mm F1.4 GM", "FE 24mm F1.4 GM"),
    ("FE 24mm F2.8 G", "FE 24mm F2.8 G"),
    ("FE 28mm F2", "FE 28mm F2"),
    ("FE 35mm F1.4 GM", "FE 35mm F1.4 GM"),
    ("FE 35mm F1.4 ZA", "FE 35mm F1.4 ZA"),
    ("Distagon T* FE 35mm F1.4 ZA", "FE 35mm F1.4 ZA"),
    ("FE 35mm F1.8", "FE 35mm F1.8"),
    ("FE 35mm F2.8 ZA", "FE 35mm F2.8 ZA"),
    ("Sonnar T* FE 35mm F2.8 ZA", "FE 35mm F2.8 ZA"),
    ("FE 40mm F2.5 G", "FE 40mm F2.5 G"),
    ("FE 50mm F1.2 GM", "FE 50mm F1.2 GM"),
    ("FE 50mm F1.4 GM", "FE 50mm F1.4 GM"),
    ("FE 50mm F1.4 ZA", "FE 50mm F1.4 ZA"),
    ("Planar T* FE 50mm F1.4 ZA", "FE 50mm F1.4 ZA"),
    ("FE 50mm F1.8", "FE 50mm F1.8"),
    ("FE 50mm F2.5 G", "FE 50mm F2.5 G"),
    ("FE 50mm F2.8 Macro", "FE 50mm F2.8 Macro"),
    ("FE 55mm F1.8 ZA", "FE 55mm F1.8 ZA"),
    ("Sonnar T* FE 55mm F1.8 ZA", "FE 55mm F1.8 ZA"),
    ("FE 85mm F1.4 GM II", "FE 85mm F1.4 GM II"),
    ("FE 85mm F1.4 GM", "FE 85mm F1.4 GM"),
    ("FE 85mm F1.8", "FE 85mm F1.8"),
    ("FE 90mm F2.8 Macro G OSS", "FE 90mm F2.8 Macro G"),
    ("FE 100mm F2.8 Macro GM OSS", "FE 100mm F2.8 Macro GM"),
    ("FE 100mm F2.8 STF GM OSS", "FE 100mm F2.8 STF GM"),
    ("FE 135mm F1.8 GM", "FE 135mm F1.8 GM"),
    ("FE 300mm F2.8 GM OSS", "FE 300mm F2.8 GM"),
    ("FE 400mm F2.8 GM OSS", "FE 400mm F2.8 GM"),
    ("FE 600mm F4 GM OSS", "FE 600mm F4 GM"),
    )),
    *_lens_aliases("sigma", (
    # Common SIGMA full-frame Sony E lenses
    ("SIGMA 14-24mm F2.8 DG DN Art", "SIGMA 14-24mm F2.8 Art"),
    ("SIGMA 16-28mm F2.8 DG DN Contemporary", "SIGMA 16-28mm F2.8 C"),
    ("SIGMA 20-200mm F3.5-6.3 DG Contemporary", "SIGMA 20-200mm F3.5-6.3 C"),
    ("SIGMA 20-200mm F3.5-6.3 DG DN Contemporary", "SIGMA 20-200mm F3.5-6.3 C"),
    ("SIGMA 24-70mm F2.8 DG DN II Art", "SIGMA 24-70mm F2.8 Art II"),
    ("SIGMA 24-70mm F2.8 DG DN Art", "SIGMA 24-70mm F2.8 Art"),
    ("SIGMA 28-45mm F1.8 DG DN Art", "SIGMA 28-45mm F1.8 Art"),
    ("SIGMA 28-70mm F2.8 DG DN Contemporary", "SIGMA 28-70mm F2.8 C"),
    ("SIGMA 28-105mm F2.8 DG DN Art", "SIGMA 28-105mm F2.8 Art"),
    ("SIGMA 60-600mm F4.5-6.3 DG DN OS Sports", "SIGMA 60-600mm F4.5-6.3 Sports"),
    ("SIGMA 70-200mm F2.8 DG DN OS Sports", "SIGMA 70-200mm F2.8 Sports"),
    ("SIGMA 100-400mm F5-6.3 DG DN OS Contemporary", "SIGMA 100-400mm F5-6.3 C"),
    ("SIGMA 150-600mm F5-6.3 DG DN OS Sports", "SIGMA 150-600mm F5-6.3 Sports"),
    ("SIGMA 14mm F1.4 DG DN Art", "SIGMA 14mm F1.4 Art"),
    ("SIGMA 14mm F1.8 DG HSM Art", "SIGMA 14mm F1.8 Art"),
    ("SIGMA 15mm F1.4 DG DN Diagonal Fisheye Art", "SIGMA 15mm F1.4 Fisheye Art"),
    ("SIGMA 17mm F4 DG DN Contemporary", "SIGMA 17mm F4 C"),
    ("SIGMA 20mm F1.4 DG DN Art", "SIGMA 20mm F1.4 Art"),
    ("SIGMA 20mm F2 DG DN Contemporary", "SIGMA 20mm F2 C"),
    ("SIGMA 24mm F1.4 DG DN Art", "SIGMA 24mm F1.4 Art"),
    ("SIGMA 24mm F2 DG DN Contemporary", "SIGMA 24mm F2 C"),
    ("SIGMA 24mm F3.5 DG DN Contemporary", "SIGMA 24mm F3.5 C"),
    ("SIGMA 28mm F1.4 DG HSM Art", "SIGMA 28mm F1.4 Art"),
    ("SIGMA 35mm F1.2 DG DN II Art", "SIGMA 35mm F1.2 Art II"),
    ("SIGMA 35mm F1.2 DG DN Art", "SIGMA 35mm F1.2 Art"),
    ("SIGMA 35mm F1.4 DG DN II Art", "SIGMA 35mm F1.4 Art II"),
    ("SIGMA 35mm F1.4 DG DN Art", "SIGMA 35mm F1.4 Art"),
    ("SIGMA 35mm F2 DG DN Contemporary", "SIGMA 35mm F2 C"),
    ("SIGMA 40mm F1.4 DG HSM Art", "SIGMA 40mm F1.4 Art"),
    ("SIGMA 45mm F2.8 DG DN Contemporary", "SIGMA 45mm F2.8 C"),
    ("SIGMA 50mm F1.2 DG DN Art", "SIGMA 50mm F1.2 Art"),
    ("SIGMA 50mm F1.4 DG DN Art", "SIGMA 50mm F1.4 Art"),
    ("SIGMA 50mm F2 DG DN Contemporary", "SIGMA 50mm F2 C"),
    ("SIGMA 65mm F2 DG DN Contemporary", "SIGMA 65mm F2 C"),
    ("SIGMA 70mm F2.8 DG Macro Art", "SIGMA 70mm F2.8 Macro Art"),
    ("SIGMA 85mm F1.4 DG DN Art", "SIGMA 85mm F1.4 Art"),
    ("SIGMA 90mm F2.8 DG DN Contemporary", "SIGMA 90mm F2.8 C"),
    ("SIGMA 105mm F1.4 DG HSM Art", "SIGMA 105mm F1.4 Art"),
    ("SIGMA 105mm F2.8 DG DN Macro Art", "SIGMA 105mm F2.8 Macro Art"),
    ("SIGMA 135mm F1.4 DG Art", "SIGMA 135mm F1.4 Art"),
    ("SIGMA 135mm F1.8 DG HSM Art", "SIGMA 135mm F1.8 Art"),
    ("SIGMA 200mm F2 DG OS Sports", "SIGMA 200mm F2 Sports"),
    )),
    *_lens_aliases("tamron", (
    # Common TAMRON full-frame Sony E lenses
    ("Tamron 12-20mm F2.8", "12-20 F2.8"),
    ("E 12-20mm F2.8", "12-20 F2.8"),
    ("Tamron 16-30mm F2.8 Di III VXD G2", "16-30 F2.8 G2"),
    ("E 16-30mm F2.8 A064", "16-30 F2.8 G2"),
    ("Tamron 17-28mm F2.8 Di III RXD", "17-28 F2.8"),
    ("E 17-28mm F2.8 A046", "17-28 F2.8"),
    ("Tamron 17-50mm F4 Di III VXD", "17-50 F4"),
    ("E 17-50mm F4 A068", "17-50 F4"),
    ("Tamron 20-40mm F2.8 Di III VXD", "20-40 F2.8"),
    ("E 20-40mm F2.8 A062", "20-40 F2.8"),
    ("Tamron 20mm F2.8 Di III OSD M1:2", "20 F2.8"),
    ("E 20mm F2.8 F050", "20 F2.8"),
    ("Tamron 24mm F2.8 Di III OSD M1:2", "24 F2.8"),
    ("E 24mm F2.8 F051", "24 F2.8"),
    ("Tamron 25-200mm F2.8-5.6 Di III VXD G2", "25-200 F2.8-5.6 G2"),
    ("E 25-200mm F2.8-5.6 A075", "25-200 F2.8-5.6 G2"),
    ("Tamron 28-75mm F2.8 Di III VXD G2", "28-75 F2.8 G2"),
    ("E 28-75mm F2.8 A063", "28-75 F2.8 G2"),
    ("Tamron 28-75mm F2.8 Di III RXD", "28-75 F2.8"),
    ("E 28-75mm F2.8 A036", "28-75 F2.8"),
    ("Tamron 28-200mm F2.8-5.6 Di III RXD", "28-200 F2.8-5.6"),
    ("E 28-200mm F2.8-5.6 A071", "28-200 F2.8-5.6"),
    ("Tamron 28-300mm F4-7.1 Di III VC VXD", "28-300 F4-7.1"),
    ("E 28-300mm F4-7.1 A074", "28-300 F4-7.1"),
    ("Tamron 35mm F2.8 Di III OSD M1:2", "35 F2.8"),
    ("E 35mm F2.8 F053", "35 F2.8"),
    ("Tamron 35-100mm F2.8 Di III VXD", "35-100 F2.8"),
    ("E 35-100mm F2.8 A078", "35-100 F2.8"),
    ("Tamron 35-150mm F2-2.8 Di III VXD", "35-150 F2-2.8"),
    ("E 35-150mm F2-2.8 A058", "35-150 F2-2.8"),
    ("Tamron 50-300mm F4.5-6.3 Di III VC VXD", "50-300 F4.5-6.3"),
    ("E 50-300mm F4.5-6.3 A069", "50-300 F4.5-6.3"),
    ("Tamron 50-400mm F4.5-6.3 Di III VC VXD", "50-400 F4.5-6.3"),
    ("E 50-400mm F4.5-6.3 A067", "50-400 F4.5-6.3"),
    ("Tamron 70-180mm F2.8 Di III VC VXD G2", "70-180 F2.8 G2"),
    ("70-180mm F2.8 Di III VC VXD G2", "70-180 F2.8 G2"),
    ("E 70-180mm F2.8 A065", "70-180 F2.8 G2"),
    ("Tamron 70-180mm F2.8 Di III VXD", "70-180 F2.8"),
    ("Tamron 70-180mm F2.8 Di III RXD", "70-180 F2.8"),
    ("E 70-180mm F2.8 A056", "70-180 F2.8"),
    ("Tamron 70-300mm F4.5-6.3 Di III RXD", "70-300 F4.5-6.3"),
    ("E 70-300mm F4.5-6.3 A047", "70-300 F4.5-6.3"),
    ("Tamron 90mm F2.8 Di III Macro VXD", "90 F2.8 Macro"),
    ("E 90mm F2.8 F072", "90 F2.8 Macro"),
    ("Tamron 150-500mm F5-6.7 Di III VC VXD", "150-500 F5-6.7"),
    ("E 150-500mm F5-6.7 A057", "150-500 F5-6.7"),
    )),
)
APPLE_LENS_SPECS = (
    (1.54, 2.4, "Ultra Wide"),
    (1.55, 2.4, "Ultra Wide"),
    (1.57, 1.8, "Ultra Wide"),
    (2.22, 2.2, "Ultra Wide"),
    (2.69, 1.9, "Front"),
    (2.71, 1.9, "Front"),
    (3.99, 1.8, "Wide"),
    (4.2, 1.6, "Wide"),
    (5.1, 1.6, "Wide"),
    (5.7, 1.5, "Wide"),
    (5.96, 1.6, "Wide"),
    (6.0, 2.0, "Tele"),
    (6.765, 1.78, "Main"),
    (6.86, 1.78, "Main"),
    (7.0, 1.6, "Wide"),
    (9.0, 2.8, "Tele"),
    (15.66, 2.8, "Tele"),
)
FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/msjh.ttc",
    "C:/Windows/Fonts/arial.ttf",
)
FONT_BOLD_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/msjhbd.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_api_base_url: str
    local_mode: bool
    signature_image_dir: Path
    signature_image_file: str
    jpeg_quality: int
    max_file_size_mb: int
    file_transfer_timeout_seconds: int

    @property
    def signature_image_path(self) -> Path:
        return self.signature_image_dir / self.signature_image_file

    @classmethod
    def from_environment(cls) -> "Settings":
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

        quality = int(os.getenv("JPEG_QUALITY", "95"))
        if not 80 <= quality <= 100:
            raise RuntimeError("JPEG_QUALITY must be between 80 and 100")

        return cls(
            bot_token=bot_token,
            bot_api_base_url=os.getenv(
                "TELEGRAM_BOT_API_URL", "https://api.telegram.org/bot"
            ).rstrip("/"),
            local_mode=os.getenv("TELEGRAM_LOCAL_MODE", "false").lower()
            in {"1", "true", "yes"},
            signature_image_dir=Path(
                os.getenv("SIGNATURE_IMAGE_DIR", "/app/assets")
            ),
            signature_image_file=os.getenv(
                "SIGNATURE_IMAGE_FILE", "signature.png"
            ).strip(),
            jpeg_quality=quality,
            max_file_size_mb=int(os.getenv("MAX_FILE_SIZE_MB", "20")),
            file_transfer_timeout_seconds=int(
                os.getenv("FILE_TRANSFER_TIMEOUT_SECONDS", "300")
            ),
        )


def _as_float(value: Any) -> float | None:
    try:
        if isinstance(value, tuple) and len(value) == 2:
            return float(Fraction(value[0], value[1]))
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _read_exif(image: Image.Image) -> dict[str, Any]:
    exif = image.getexif()
    values = {
        ExifTags.TAGS.get(tag_id, str(tag_id)): value
        for tag_id, value in exif.items()
    }

    try:
        values.update(
            {
                ExifTags.TAGS.get(tag_id, str(tag_id)): value
                for tag_id, value in exif.get_ifd(EXIF_IFD).items()
            }
        )
    except (AttributeError, KeyError, TypeError):
        pass

    return values


def _clean_text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return " ".join(str(value).replace("\x00", "").split())


def _lens_match_key(value: str) -> str:
    text = value.replace("–", "-").replace("—", "-")
    text = text.replace("|", " ")
    text = re.sub(r"\bF\s*/\s*", "F", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def _strip_sony_fe_prefix(value: str) -> str:
    return re.sub(r"^FE\s+", "", value).strip()


def _format_lens_display(alias: LensAlias) -> str:
    if alias.brand == "sony":
        return _strip_sony_fe_prefix(alias.display)
    if alias.brand == "tamron":
        return re.sub(r"^([0-9]+(?:-[0-9]+)?)\b", r"\1mm", alias.display)
    return alias.display


def _format_decimal(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _apple_lens_role(focal_mm: float, aperture: float, side: str) -> str:
    for spec_focal, spec_aperture, role in APPLE_LENS_SPECS:
        focal_matches = abs(focal_mm - spec_focal) <= 0.035
        aperture_matches = abs(aperture - spec_aperture) <= 0.035
        if focal_matches and aperture_matches:
            return role
    return "Front" if side.casefold() == "front" else "Back"


def _format_apple_lens_name(value: str) -> str | None:
    if "iphone" not in value.casefold():
        return None

    match = re.search(
        r"\b(?P<side>front|back)\b.*?\bcamera\b.*?"
        r"(?P<focal>[0-9]+(?:\.[0-9]+)?)\s*mm\s*"
        r"f\s*/?\s*(?P<aperture>[0-9]+(?:\.[0-9]+)?)",
        value,
        flags=re.I,
    )
    if not match:
        return None

    focal_mm = float(match.group("focal"))
    aperture = float(match.group("aperture"))
    role = _apple_lens_role(focal_mm, aperture, match.group("side"))
    return f"{role} {_format_decimal(focal_mm)}mm F{_format_decimal(aperture)}"


def _match_lens_alias(value: Any) -> LensAlias | None:
    original = _clean_text(value)
    match_key = _lens_match_key(original)
    for alias in sorted(
        LENS_NAME_ALIASES, key=lambda item: len(item.source), reverse=True
    ):
        source_key = _lens_match_key(alias.source)
        source_without_brand = re.sub(
            r"^(sigma|tamron|sony)\s+", "", source_key
        )
        if source_key in match_key or source_without_brand in match_key:
            return alias
    return None


def _lens_badge_key_from_alias(alias: LensAlias | None) -> str | None:
    if alias is None:
        return None
    if alias.brand in {"sigma", "tamron"}:
        return alias.brand
    if alias.brand == "sony":
        if re.search(r"\bGM\b", alias.display):
            return "gm"
        if re.search(r"\bG\b", alias.display):
            return "g"
    return None


def _format_lens_name(value: Any) -> str:
    original = _clean_text(value)
    if not original:
        return "UNKNOWN LENS"

    apple_lens = _format_apple_lens_name(original)
    if apple_lens:
        return apple_lens

    alias = _match_lens_alias(original)
    if alias:
        return _format_lens_display(alias)

    cleaned = re.sub(r"\b(Sony|Lens|E-mount|for Sony E)\b", "", original, flags=re.I)
    cleaned = re.sub(r"\bOptical SteadyShot\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(DG DN|DG HSM|DG OS|Di III|VC|VXD|RXD|OSD)\b", "", cleaned)
    cleaned = re.sub(r"\bContemporary\b", "C", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned.replace("|", " ")).strip(" -")
    cleaned = _strip_sony_fe_prefix(cleaned)
    return cleaned or original


def _format_exposure(value: Any) -> str | None:
    seconds = _as_float(value)
    if seconds is None or seconds <= 0:
        return None
    if seconds < 1:
        return f"1/{round(1 / seconds)}s"
    return f"{seconds:g}s"


@dataclass(frozen=True)
class PhotoMetadata:
    iso: str
    aperture: str
    focal_length_35mm: str
    shutter_speed: str
    camera_make: str
    camera_model: str
    lens: str
    lens_badge_key: str | None
    captured_at: str


def _format_capture_time(value: Any) -> str:
    if value is None:
        return "DATE UNKNOWN"
    text = _clean_text(value)
    if len(text) >= 19 and text[4] == ":" and text[7] == ":":
        return f"{text[:4]}.{text[5:7]}.{text[8:10]}  {text[11:16]}"
    return text or "DATE UNKNOWN"


def _camera_brand_key(make: str, model: str) -> str | None:
    for source in (model.casefold(), make.casefold()):
        for key in BRAND_NAMES:
            if key in source:
                return key
    return None


def _strip_camera_make(make: str, model: str, brand_key: str | None) -> str:
    prefixes = [make]
    if brand_key:
        prefixes.extend((brand_key, BRAND_NAMES[brand_key]))

    result = model.strip()
    for prefix in sorted(set(prefixes), key=len, reverse=True):
        if prefix and result.casefold().startswith(prefix.casefold()):
            stripped = result[len(prefix) :].lstrip(" -_/")
            if stripped:
                result = stripped
                break
    return result or "UNKNOWN MODEL"


def _format_metadata(exif: dict[str, Any]) -> PhotoMetadata:
    make = _clean_text(exif.get("Make", ""))
    model = _clean_text(exif.get("Model", ""))
    brand_key = _camera_brand_key(make, model)
    lens_model = _clean_text(exif.get("LensModel", ""))
    lens_alias = _match_lens_alias(lens_model)

    aperture = _as_float(exif.get("FNumber"))
    exposure = _format_exposure(exif.get("ExposureTime"))
    iso = exif.get("PhotographicSensitivity", exif.get("ISOSpeedRatings"))
    captured_at = exif.get("DateTimeOriginal", exif.get("DateTime"))
    focal_length_35mm = _as_float(exif.get("FocalLengthIn35mmFilm"))

    return PhotoMetadata(
        iso=_clean_text(iso) if iso else "—",
        aperture=f"F{aperture:g}" if aperture else "—",
        focal_length_35mm=f"{focal_length_35mm:g}MM"
        if focal_length_35mm
        else "—",
        shutter_speed=exposure.upper() if exposure else "—",
        camera_make=make or "UNKNOWN",
        camera_model=_strip_camera_make(make, model, brand_key),
        lens=_format_lens_display(lens_alias)
        if lens_alias
        else _format_lens_name(lens_model),
        lens_badge_key=_lens_badge_key_from_alias(lens_alias)
        or _lens_badge_key(lens_model),
        captured_at=_format_capture_time(captured_at),
    )


def _load_font(
    size: int, *, bold: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_BOLD_PATHS if bold else FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text

    ellipsis = "…"
    candidate = text
    while candidate and draw.textlength(candidate + ellipsis, font=font) > max_width:
        candidate = candidate[:-1]
    return candidate.rstrip() + ellipsis


def _centered_text_y(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    center_y: int,
) -> int:
    bounds = draw.textbbox((0, 0), text, font=font)
    text_height = bounds[3] - bounds[1]
    return round(center_y - text_height / 2 - bounds[1])


def _centered_text_x(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    left: int,
    right: int,
) -> int:
    bounds = draw.textbbox((0, 0), text, font=font)
    text_width = bounds[2] - bounds[0]
    return round(left + (right - left - text_width) / 2 - bounds[0])


def _lens_badge_key(lens_name: str) -> str | None:
    match_key = _lens_match_key(lens_name)
    tamron_model_codes = (
        "a036",
        "a046",
        "a047",
        "a056",
        "a057",
        "a058",
        "a062",
        "a063",
        "a064",
        "a065",
        "a067",
        "a068",
        "a069",
        "a071",
        "a074",
        "a075",
        "a078",
        "f050",
        "f051",
        "f053",
        "f072",
    )

    if "sigma" in match_key:
        return "sigma"
    if (
        "tamron" in match_key
        or "di iii" in match_key
        or any(term in match_key for term in ("vxd", "rxd", "osd"))
        or any(
            re.search(rf"\b{code}\b", match_key)
            for code in tamron_model_codes
        )
    ):
        return "tamron"
    if re.search(r"\bgm\b", match_key):
        return "gm"
    if re.search(r"\bg\b", match_key):
        return "g"
    return None


def _load_brand_icon(
    icon_dir: Path,
    make: str,
    model: str,
    max_width: int,
    max_height: int,
) -> Image.Image | None:
    brand_key = _camera_brand_key(make, model)
    if not brand_key:
        return None

    path = icon_dir / f"{brand_key.replace(' ', '-')}.png"
    if not path.is_file():
        return None

    try:
        with Image.open(path) as source:
            icon = source.convert("RGBA")
            alpha = icon.getchannel("A").point(lambda value: 255 if value > 16 else 0)
            bounds = alpha.getbbox()
            if bounds:
                icon = icon.crop(bounds)
            icon.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            return icon
    except (UnidentifiedImageError, OSError):
        LOGGER.warning("Unable to load brand icon: %s", path)
        return None


def _is_badge_background_pixel(
    red: int, green: int, blue: int, alpha: int
) -> bool:
    if not alpha:
        return False
    is_black = red < 24 and green < 24 and blue < 24
    is_white = red > 238 and green > 238 and blue > 238
    return is_black or is_white


def _make_edge_background_transparent(image: Image.Image) -> Image.Image:
    pixels = image.load()
    width, height = image.size
    queue: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()

    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if (x, y) in visited or not (0 <= x < width and 0 <= y < height):
            continue
        visited.add((x, y))

        red, green, blue, alpha = pixels[x, y]
        if not _is_badge_background_pixel(red, green, blue, alpha):
            continue

        pixels[x, y] = (red, green, blue, 0)
        queue.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))

    return image


def _load_lens_badge(
    badge_dir: Path,
    badge_key: str | None,
    max_width: int,
    max_height: int,
) -> Image.Image | None:
    if not badge_key:
        return None

    path = badge_dir / f"{badge_key}.png"
    if not path.is_file():
        return None

    try:
        with Image.open(path) as source:
            badge = source.convert("RGBA")
            if not source.info.get("transparency"):
                badge = _make_edge_background_transparent(badge)
            alpha = badge.getchannel("A").point(lambda value: 255 if value > 16 else 0)
            bounds = alpha.getbbox()
            if bounds:
                badge = badge.crop(bounds)
            badge.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            return badge
    except (UnidentifiedImageError, OSError):
        LOGGER.warning("Unable to load lens badge: %s", path)

    return None


def _load_signature(
    configured_path: Path,
    max_width: int,
    max_height: int,
) -> Image.Image | None:
    candidates = (
        configured_path,
        configured_path.parent / "signature.example.png",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            with Image.open(path) as source:
                signature = source.convert("RGBA")
                bounds = signature.getbbox()
                if bounds:
                    signature = signature.crop(bounds)
                signature.thumbnail(
                    (max_width, max_height), Image.Resampling.LANCZOS
                )
                return signature
        except (UnidentifiedImageError, OSError):
            LOGGER.warning("Unable to load signature image: %s", path)

    LOGGER.warning("No usable signature image found at %s", configured_path)
    return None


def _add_metadata_panel(
    image: Image.Image,
    metadata: PhotoMetadata,
    signature_path: Path,
) -> Image.Image:
    photo = image.convert("RGB")
    width, height = photo.size
    panel_height = max(PANEL_MIN_HEIGHT, round(width * PANEL_HEIGHT_RATIO))
    padding = max(20, round(width * 0.026))
    label_font = _load_font(max(9, round(width * 0.0088)))
    value_font = _load_font(max(15, round(width * 0.017)), bold=True)
    detail_font = _load_font(max(11, round(width * 0.011)))
    equipment_font = _load_font(max(13, round(width * 0.0135)), bold=True)

    panel = Image.new("RGB", (width, panel_height), PANEL_BACKGROUND)
    draw = ImageDraw.Draw(panel)
    line_width = max(2, round(width * 0.002))
    draw.line((0, 0, width, 0), fill=(226, 222, 214), width=line_width)
    draw.line(
        (padding, 0, padding + round(width * 0.07), 0),
        fill=PANEL_ACCENT,
        width=line_width * 2,
    )

    stats_right = round(width * 0.55)
    signature_left = round(width * 0.59)
    signature_badge_left = round(width * 0.70)
    signature_right = round(width * 0.78)
    equipment_left = round(width * 0.80)
    divider_top = round(panel_height * 0.16)
    divider_bottom = round(panel_height * 0.84)
    divider_color = (218, 214, 206)
    draw.line(
        (round(width * 0.575), divider_top, round(width * 0.575), divider_bottom),
        fill=divider_color,
        width=max(1, line_width // 2),
    )
    draw.line(
        (signature_right, divider_top, signature_right, divider_bottom),
        fill=divider_color,
        width=max(1, line_width // 2),
    )

    stats = (
        ("ISO", metadata.iso),
        ("APERTURE", metadata.aperture),
        ("SHUTTER", metadata.shutter_speed),
        ("FOCAL", metadata.focal_length_35mm),
    )
    stat_width = max(1, (stats_right - padding) // len(stats))
    label_y = round(panel_height * 0.22)
    value_y = round(panel_height * 0.42)
    for index, (label, value) in enumerate(stats):
        x = padding + stat_width * index
        draw.text((x, label_y), label, font=label_font, fill=PANEL_MUTED)
        fitted_value = _fit_text(
            draw, value, value_font, stat_width - round(width * 0.012)
        )
        draw.text((x, value_y), fitted_value, font=value_font, fill=PANEL_INK)

    captured_y = round(panel_height * 0.76)
    draw.text(
        (padding, captured_y),
        "CAPTURED",
        font=label_font,
        fill=PANEL_ACCENT,
    )
    captured_x = padding + round(width * 0.072)
    captured_width = max(1, stats_right - captured_x - padding)
    captured_text = _fit_text(
        draw, metadata.captured_at, label_font, captured_width
    )
    draw.text(
        (captured_x, captured_y),
        captured_text,
        font=label_font,
        fill=PANEL_MUTED,
    )

    draw.text(
        (signature_left, round(panel_height * 0.18)),
        "SHOT BY",
        font=label_font,
        fill=PANEL_MUTED,
    )
    signature_gap = max(8, round(width * 0.008))
    signature_width = max(1, signature_badge_left - signature_left - signature_gap)
    signature = _load_signature(
        signature_path,
        max_width=signature_width,
        max_height=max(1, round(panel_height * 0.38)),
    )
    if signature:
        signature_y = round(panel_height * 0.41)
        panel.paste(signature, (signature_left, signature_y), signature)
    else:
        draw.text(
            (signature_left, round(panel_height * 0.48)),
            "SIGNATURE",
            font=detail_font,
            fill=PANEL_INK,
        )

    badge_area_left = signature_badge_left
    badge_area_right = signature_right - signature_gap
    badge_area_width = max(1, badge_area_right - badge_area_left)
    lens_badge = _load_lens_badge(
        LENS_BADGE_DIR,
        metadata.lens_badge_key,
        max_width=badge_area_width,
        max_height=max(1, round(panel_height * 0.24)),
    )
    brand_icon = _load_brand_icon(
        BRAND_ICON_DIR,
        metadata.camera_make,
        metadata.camera_model,
        max_width=max(1, round(badge_area_width * 0.94)),
        max_height=max(
            1,
            round(panel_height * (0.22 if lens_badge else 0.30)),
        ),
    )
    brand_center_y = round(panel_height * (0.32 if lens_badge else 0.54))
    if brand_icon:
        brand_x = round(badge_area_left + (badge_area_width - brand_icon.width) / 2)
        brand_y = round(brand_center_y - brand_icon.height / 2)
        panel.paste(brand_icon, (brand_x, brand_y), brand_icon)
    else:
        brand_key = _camera_brand_key(metadata.camera_make, metadata.camera_model)
        brand_text = BRAND_NAMES.get(brand_key, metadata.camera_make).upper()
        brand_text = _fit_text(draw, brand_text, value_font, badge_area_width)
        brand_x = _centered_text_x(
            draw, brand_text, value_font, badge_area_left, badge_area_right
        )
        brand_y = _centered_text_y(draw, brand_text, value_font, brand_center_y)
        draw.text((brand_x, brand_y), brand_text, font=value_font, fill=PANEL_INK)
    if lens_badge:
        lens_badge_x = round(
            badge_area_left + (badge_area_width - lens_badge.width) / 2
        )
        lens_badge_y = round(panel_height * 0.65 - lens_badge.height / 2)
        panel.paste(lens_badge, (lens_badge_x, lens_badge_y), lens_badge)

    equipment_right = width - padding
    equipment_width = max(1, equipment_right - equipment_left)
    model_width = max(1, equipment_width)
    camera_text = _fit_text(
        draw, metadata.camera_model, equipment_font, model_width
    )
    camera_y = _centered_text_y(
        draw, camera_text, equipment_font, round(panel_height * 0.34)
    )
    draw.text(
        (equipment_left, camera_y),
        camera_text,
        font=equipment_font,
        fill=PANEL_INK,
    )

    lens_text = _fit_text(draw, metadata.lens, equipment_font, equipment_width)
    lens_y = _centered_text_y(
        draw, lens_text, equipment_font, round(panel_height * 0.65)
    )
    draw.text(
        (equipment_left, lens_y),
        lens_text,
        font=equipment_font,
        fill=PANEL_INK,
    )

    result = Image.new("RGB", (width, height + panel_height), PANEL_BACKGROUND)
    result.paste(photo, (0, 0))
    result.paste(panel, (0, height))
    return result


def process_image(
    image_source: bytes | Path,
    signature_path: Path,
    jpeg_quality: int,
) -> tuple[BytesIO, bool]:
    source_input = (
        image_source if isinstance(image_source, Path) else BytesIO(image_source)
    )
    with Image.open(source_input) as source:
        source.load()
        exif_values = _read_exif(source)
        image = ImageOps.exif_transpose(source)
        exif_bytes = image.getexif().tobytes()
        metadata = _format_metadata(exif_values)
        result = _add_metadata_panel(image, metadata, signature_path)

    output = BytesIO()
    save_options: dict[str, Any] = {
        "format": "JPEG",
        "quality": jpeg_quality,
        "subsampling": 0,
        "optimize": True,
    }
    if exif_bytes:
        save_options["exif"] = exif_bytes
    result.save(output, **save_options)
    output.seek(0)
    return output, bool(exif_values)


def _output_filename(original_name: str | None) -> str:
    stem = Path(original_name or "photo").stem
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "photo"
    return f"{safe_stem}_watermarked.jpg"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.effective_message:
        await update.effective_message.reply_text(
            "Send an image as a file. I will add its EXIF details and watermark."
        )


async def remind_file_upload(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    del context
    if update.effective_message:
        await update.effective_message.reply_text(
            "Please send the image as a file to preserve its quality and EXIF data."
        )


async def handle_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    document = message.document if message else None
    if not message or not document:
        return

    settings: Settings = context.application.bot_data["settings"]
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if document.file_size and document.file_size > max_bytes:
        await message.reply_text(
            f"The file exceeds the {settings.max_file_size_mb} MB limit."
        )
        return

    status = await message.reply_text("Uploading image....")
    await context.bot.send_chat_action(
        chat_id=message.chat_id, action=ChatAction.UPLOAD_DOCUMENT
    )

    try:
        telegram_file = await document.get_file(
            read_timeout=settings.file_transfer_timeout_seconds
        )
        if settings.local_mode:
            if not telegram_file.file_path:
                raise OSError("Local Bot API returned no file path")
            image_source: bytes | Path = Path(telegram_file.file_path)
            if not image_source.is_file():
                raise OSError(
                    f"Shared Telegram file is unavailable: {image_source}"
                )
        else:
            image_source = bytes(await telegram_file.download_as_bytearray())

        await status.edit_text("Processing image...")
        output, has_exif = await asyncio.to_thread(
            process_image,
            image_source,
            settings.signature_image_path,
            settings.jpeg_quality,
        )
        caption = (
            "Watermark and EXIF details added."
            if has_exif
            else "Watermark added. No EXIF data was found."
        )
        await message.reply_document(
            document=InputFile(output, filename=_output_filename(document.file_name)),
            caption=caption,
            read_timeout=settings.file_transfer_timeout_seconds,
            write_timeout=settings.file_transfer_timeout_seconds,
        )
        await status.edit_text("Done!")
    except (UnidentifiedImageError, OSError, ValueError):
        LOGGER.warning("Unsupported or invalid image received", exc_info=True)
        await status.edit_text("This image format is unsupported or the file is invalid.")
    except Exception:
        LOGGER.exception("Failed to process image")
        await status.edit_text("Image processing failed. Please try another file.")


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.from_environment()
    application = (
        Application.builder()
        .token(settings.bot_token)
        .base_url(settings.bot_api_base_url)
        .local_mode(settings.local_mode)
        .build()
    )
    application.bot_data["settings"] = settings
    application.add_handler(CommandHandler("start", start))
    image_document_filter = (
        filters.Document.IMAGE
        | filters.Document.FileExtension("heic")
        | filters.Document.FileExtension("heif")
    )
    application.add_handler(MessageHandler(image_document_filter, handle_image))
    application.add_handler(MessageHandler(filters.PHOTO, remind_file_upload))
    LOGGER.info("Starting Telegram bot")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
