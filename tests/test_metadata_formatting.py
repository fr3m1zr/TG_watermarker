"""Offline regression tests for metadata formatting and image handling."""

from __future__ import annotations

import asyncio
import importlib
import os
import struct
import sys
import types
import unittest
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch


class _ExifRatio:
    """Small stand-in for the rational objects returned by EXIF readers."""

    def __init__(self, numerator: int, denominator: int) -> None:
        self.num = numerator
        self.den = denominator


def _install_optional_dependency_stubs() -> None:
    """Allow metadata-only tests to run without bot/Telegram dependencies."""

    try:
        import exifread  # noqa: F401
    except ModuleNotFoundError:
        exifread = types.ModuleType("exifread")
        exifread.process_file = lambda *args, **kwargs: {}
        sys.modules["exifread"] = exifread

    try:
        import rawpy  # noqa: F401
    except ModuleNotFoundError:
        rawpy = types.ModuleType("rawpy")
        rawpy.LibRawError = type("LibRawError", (Exception,), {})
        rawpy.ThumbFormat = types.SimpleNamespace(JPEG="jpeg", BITMAP="bitmap")
        sys.modules["rawpy"] = rawpy

    try:
        import pillow_heif  # noqa: F401
    except ModuleNotFoundError:
        pillow_heif = types.ModuleType("pillow_heif")
        pillow_heif.register_heif_opener = lambda: None
        sys.modules["pillow_heif"] = pillow_heif

    try:
        from PIL import ExifTags  # noqa: F401
    except ModuleNotFoundError:
        pil = types.ModuleType("PIL")
        pil.ExifTags = types.SimpleNamespace(TAGS={})
        pil.Image = types.ModuleType("PIL.Image")
        pil.ImageDraw = types.ModuleType("PIL.ImageDraw")
        pil.ImageFont = types.ModuleType("PIL.ImageFont")
        pil.ImageOps = types.ModuleType("PIL.ImageOps")
        pil.Image.open = None
        pil.Image.DecompressionBombError = type(
            "DecompressionBombError", (Exception,), {}
        )
        pil.Image.DecompressionBombWarning = type(
            "DecompressionBombWarning", (Warning,), {}
        )
        pil.UnidentifiedImageError = type("UnidentifiedImageError", (Exception,), {})
        sys.modules.update(
            {
                "PIL": pil,
                "PIL.ExifTags": pil.ExifTags,
                "PIL.Image": pil.Image,
                "PIL.ImageDraw": pil.ImageDraw,
                "PIL.ImageFont": pil.ImageFont,
                "PIL.ImageOps": pil.ImageOps,
            }
        )

    try:
        import telegram  # noqa: F401
    except ModuleNotFoundError:
        telegram = types.ModuleType("telegram")
        telegram.InputFile = object
        telegram.Update = object
        telegram_constants = types.ModuleType("telegram.constants")
        telegram_constants.ChatAction = types.SimpleNamespace(UPLOAD_DOCUMENT="upload")
        telegram_ext = types.ModuleType("telegram.ext")
        telegram_ext.Application = object
        telegram_ext.CommandHandler = object
        telegram_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
        telegram_ext.MessageHandler = object
        telegram_ext.filters = types.SimpleNamespace()

        sys.modules.update({
            "telegram": telegram,
            "telegram.constants": telegram_constants,
            "telegram.ext": telegram_ext,
        })


_install_optional_dependency_stubs()
bot = importlib.import_module("bot")


class _HeaderImage:
    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size

    def __enter__(self) -> "_HeaderImage":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakeStatus:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_text(self, text: str) -> None:
        self.edits.append(text)


class _FakeMessage:
    chat_id = 123

    def __init__(self, document: object) -> None:
        self.document = document
        self.status = _FakeStatus()
        self.replies: list[str] = []
        self.documents: list[dict[str, object]] = []

    async def reply_text(self, text: str) -> _FakeStatus:
        self.replies.append(text)
        return self.status

    async def reply_document(self, **kwargs: object) -> None:
        self.documents.append(kwargs)


class _FakeDocument:
    file_name = "synthetic.jpg"
    file_size = 128

    def __init__(self, path: Path) -> None:
        self.path = path

    async def get_file(self, **kwargs: object) -> types.SimpleNamespace:
        del kwargs
        return types.SimpleNamespace(file_path=str(self.path))


class _FakeBot:
    def __init__(self) -> None:
        self.actions: list[dict[str, object]] = []

    async def send_chat_action(self, **kwargs: object) -> None:
        self.actions.append(kwargs)


def _handler_context(
    document: _FakeDocument,
) -> tuple[types.SimpleNamespace, types.SimpleNamespace, _FakeMessage]:
    settings = bot.Settings(
        bot_token="placeholder",
        bot_api_base_url="http://telegram.test/bot",
        local_mode=True,
        signature_image_dir=Path("."),
        signature_image_file="signature.png",
        jpeg_quality=95,
        max_file_size_mb=100,
        max_image_pixels=bot.DEFAULT_MAX_IMAGE_PIXELS,
        file_transfer_timeout_seconds=1,
    )
    message = _FakeMessage(document)
    fake_bot = _FakeBot()
    context = types.SimpleNamespace(
        application=types.SimpleNamespace(bot_data={"settings": settings}),
        bot=fake_bot,
    )
    update = types.SimpleNamespace(effective_message=message)
    return update, context, message


class MetadataFormattingTests(unittest.TestCase):
    def test_aperture_formatting_handles_common_exif_values(self) -> None:
        self.assertEqual(bot._format_aperture(4), "F4")
        self.assertEqual(bot._format_aperture("5.6"), "F5.6")
        self.assertEqual(bot._format_aperture(_ExifRatio(56, 10)), "F5.6")
        self.assertEqual(bot._format_aperture("5.649"), "F5.6")
        self.assertEqual(bot._format_aperture("5.65"), "F5.7")
        self.assertEqual(bot._format_aperture("2.675"), "F2.7")

    def test_variable_aperture_preserves_minimum_and_formats_range(self) -> None:
        self.assertEqual(bot._format_aperture("F1.48"), "F1.48")
        self.assertEqual(
            bot._format_aperture("ƒ/1.48–ƒ/4.0"),
            "F1.48-F4",
        )

    def test_aperture_invalid_values_are_omitted(self) -> None:
        for value in (None, "not-a-number", float("nan"), float("inf"), 0, -1):
            with self.subTest(value=value):
                self.assertIsNone(bot._format_aperture(value))

    def test_focal_length_uses_decimal_half_up_rounding(self) -> None:
        cases = (
            (202, "202MM"),
            (202.6, "203MM"),
            (486.7, "487MM"),
            ("202.5", "203MM"),
            (Fraction(4867, 10), "487MM"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(bot._format_focal_length(value), expected)

    def test_metadata_falls_back_to_physical_focal_length(self) -> None:
        metadata = bot._format_metadata(
            {
                "FNumber": _ExifRatio(4, 1),
                "FocalLengthIn35mmFilm": None,
                "FocalLength": "202.6",
            }
        )
        self.assertEqual(metadata.aperture, "F4")
        self.assertEqual(metadata.focal_length_35mm, "—")
        self.assertEqual(metadata.focal_length_physical, "203MM")
        self.assertIn(("FOCAL", "203MM"), bot._metadata_stats(metadata))

    def test_img_6005_uses_35mm_equivalent_without_crop_estimate(self) -> None:
        metadata = bot._format_metadata(
            {
                "Make": "Apple",
                "Model": "iPhone 18 Pro",
                "FocalLength": _ExifRatio(693, 100),
                "FocalLengthIn35mmFilm": 24,
                "PixelXDimension": 6048,
                "PixelYDimension": 8064,
            },
            (6048, 8064),
        )
        self.assertEqual(metadata.focal_length_physical, "7MM")
        self.assertEqual(metadata.focal_length_35mm, "24MM")
        self.assertEqual(metadata.focal_length_cropped, "—")
        self.assertIsNone(metadata.focal_length_cropped_basis)
        self.assertEqual(bot._metadata_stats(metadata)[-1], ("35MM EQ", "24MM"))

    def test_dimensions_without_original_crop_metadata_do_not_scale_focal_length(self) -> None:
        # Neither EXIF pixel dimensions nor Image IFD dimensions identify the
        # uncropped source or prove that the 2:1 change was a crop, not a resize.
        exif_pixel_size = (6048, 8064)
        image_ifd_size = (3024, 4032)
        self.assertEqual(
            (
                exif_pixel_size[0] // image_ifd_size[0],
                exif_pixel_size[1] // image_ifd_size[1],
            ),
            (2, 2),
        )
        exif = {
            "FocalLength": _ExifRatio(100, 1),
            "PixelXDimension": exif_pixel_size[0],
            "PixelYDimension": exif_pixel_size[1],
            "ImageWidth": image_ifd_size[0],
            "ImageLength": image_ifd_size[1],
        }
        for equivalent, expected in ((310, "310MM"), (200, "200MM")):
            with self.subTest(equivalent=equivalent):
                metadata = bot._format_metadata(
                    {**exif, "FocalLengthIn35mmFilm": _ExifRatio(equivalent, 1)}
                )
                self.assertEqual(metadata.focal_length_physical, "100MM")
                self.assertEqual(metadata.focal_length_cropped, "—")
                self.assertEqual(metadata.focal_length_35mm, expected)
                self.assertEqual(bot._metadata_stats(metadata)[-1], ("35MM EQ", expected))

        physical_only = bot._format_metadata(exif)
        self.assertEqual(physical_only.focal_length_35mm, "—")
        self.assertEqual(physical_only.focal_length_cropped, "—")
        self.assertIn(("FOCAL", "100MM"), bot._metadata_stats(physical_only))

    def test_dng_user_crop_doubles_physical_focal_without_changing_35mm_exif(self) -> None:
        # DNG DefaultCropSize is the source area; DefaultUserCrop is an
        # explicit crop, unlike mismatched EXIF pixel dimensions.
        crop = bot.DngUserCrop((6048, 8064), (1512, 2016, 4536, 6048))
        exif = {
            "FocalLength": _ExifRatio(100, 1),
            "FocalLengthIn35mmFilm": _ExifRatio(310, 1),
            "DngUserCrops": (crop,),
            "Orientation": 1,
        }
        metadata = bot._format_metadata(exif, (3024, 4032))
        self.assertEqual(metadata.focal_length_physical, "100MM")
        self.assertEqual(metadata.focal_length_cropped, "200MM")
        self.assertEqual(metadata.focal_length_35mm, "310MM")
        self.assertIn(("CROP FOCAL", "200MM"), bot._metadata_stats(metadata))

    def test_dng_user_crop_does_not_scale_35mm_equivalent_without_physical_focal(
        self,
    ) -> None:
        crop = bot.DngUserCrop((6048, 8064), (1512, 2016, 4536, 6048))
        metadata = bot._format_metadata(
            {"FocalLengthIn35mmFilm": 310, "DngUserCrops": (crop,)},
            (3024, 4032),
        )
        self.assertEqual(metadata.focal_length_physical, "—")
        self.assertEqual(metadata.focal_length_cropped, "—")
        self.assertEqual(bot._metadata_stats(metadata)[-1], ("35MM EQ", "310MM"))

    def test_dng_crop_requires_unambiguous_geometry_and_final_raster(self) -> None:
        crop = bot.DngUserCrop((6048, 8064), (1512, 2016, 4536, 6048))
        exif = {"FocalLength": 100, "DngUserCrops": (crop,)}
        cases = (
            ({}, (3024, 4032)),
            (exif, (1512, 2016)),  # Resized after the specified crop.
            ({**exif, "DngUserCrops": (crop, crop)}, (3024, 4032)),
            ({**exif, "Orientation": 6}, (3024, 4032)),
        )
        for case_exif, raster_size in cases:
            with self.subTest(exif=case_exif, raster_size=raster_size):
                metadata = bot._format_metadata(case_exif, raster_size)
                self.assertEqual(metadata.focal_length_cropped, "—")
        rotated = bot._format_metadata({**exif, "Orientation": 6}, (4032, 3024))
        self.assertEqual(rotated.focal_length_cropped, "200MM")

    def test_dng_crop_rejects_changed_aspect_or_nonunit_scale(self) -> None:
        def tag(values: object) -> types.SimpleNamespace:
            return types.SimpleNamespace(values=values)

        base = {
            "Image Tag 0xC612": tag([1, 4, 0, 0]),
            "EXIF SubIFD0 Tag 0xC620": tag([6048, 8064]),
            "EXIF SubIFD0 Tag 0xC7B5": tag(
                [_ExifRatio(1, 4), _ExifRatio(1, 4),
                 _ExifRatio(3, 4), _ExifRatio(3, 4)]
            ),
        }
        self.assertEqual(
            bot._dng_user_crops(base),
            (bot.DngUserCrop((6048, 8064), (1512, 2016, 4536, 6048)),),
        )
        self.assertEqual(
            bot._dng_user_crops(
                {key: value for key, value in base.items() if key != "Image Tag 0xC612"}
            ),
            (),
        )
        self.assertEqual(
            bot._dng_user_crops({**base, "EXIF SubIFD0 Tag 0xC61E": tag([2, 2])}),
            (),
        )
        nonuniform = bot.DngUserCrop((6048, 8064), (1512, 0, 4536, 8064))
        self.assertIsNone(
            bot._dng_crop_factor(
                {"DngUserCrops": (nonuniform,)}, nonuniform.size
            )
        )

    def test_exifread_extracts_crop_from_dng_raw_subifd(self) -> None:
        # A minimal TIFF/DNG directory tree with a RAW SubIFD. It tests
        # ExifRead's actual tag names and details=True traversal.
        if not hasattr(bot.exifread, "__version__"):
            self.skipTest("ExifRead is not installed")
        header = b"II*\x00" + struct.pack("<I", 8)
        ifd0_offset = 8
        raw_ifd_offset = ifd0_offset + 2 + 2 * 12 + 4
        crop_size_offset = raw_ifd_offset + 2 + 4 * 12 + 4
        user_crop_offset = crop_size_offset + 8
        entry = lambda tag_id, field_type, count, value: struct.pack(
            "<HHII", tag_id, field_type, count, value
        )
        ifd0 = (
            struct.pack("<H", 2)
            + entry(330, 4, 1, raw_ifd_offset)
            + entry(50706, 1, 4, 0x00000401)
            + struct.pack("<I", 0)
        )
        raw_ifd = (
            struct.pack("<H", 4)
            + entry(256, 4, 1, 6048)
            + entry(257, 4, 1, 8064)
            + entry(50720, 4, 2, crop_size_offset)
            + entry(51125, 5, 4, user_crop_offset)
            + struct.pack("<I", 0)
        )
        fractions = ((1, 4), (1, 4), (3, 4), (3, 4))
        fixture = (
            header + ifd0 + raw_ifd + struct.pack("<II", 6048, 8064)
            + b"".join(struct.pack("<II", *fraction) for fraction in fractions)
        )
        values = bot._read_raw_exif(fixture, "synthetic.dng")
        self.assertEqual(
            values["DngUserCrops"],
            (bot.DngUserCrop((6048, 8064), (1512, 2016, 4536, 6048)),),
        )

    def test_process_dng_applies_user_crop_and_displays_effective_focal(self) -> None:
        def tag(values: object) -> types.SimpleNamespace:
            return types.SimpleNamespace(values=values)

        tags = {
            "Image Tag 0xC612": tag([1, 4, 0, 0]),
            "Image Orientation": tag([1]),
            "EXIF SubIFD0 Tag 0xC620": tag([6048, 8064]),
            "EXIF SubIFD0 Tag 0xC7B5": tag(
                [_ExifRatio(1, 4), _ExifRatio(1, 4),
                 _ExifRatio(3, 4), _ExifRatio(3, 4)]
            ),
            "EXIF FocalLength": tag([_ExifRatio(100, 1)]),
            "EXIF FocalLengthIn35mmFilm": tag([310]),
        }
        full = MagicMock()
        full.size = (6048, 8064)
        cropped = MagicMock()
        cropped.size = (3024, 4032)
        full.crop.return_value = cropped

        with (
            patch.object(bot.exifread, "process_file", return_value=tags) as read_tags,
            patch.object(bot, "_decode_raw_image", return_value=full),
            patch.object(bot, "_add_metadata_panel", return_value=MagicMock())
            as add_panel,
        ):
            bot.process_image(b"synthetic dng", Path("signature.png"), 95, "photo.dng")

        self.assertTrue(read_tags.call_args.kwargs["details"])
        full.crop.assert_called_once_with((1512, 2016, 4536, 6048))
        processed_photo, metadata, _ = add_panel.call_args.args
        self.assertIs(processed_photo, cropped)
        self.assertEqual(metadata.focal_length_cropped, "200MM")
        self.assertEqual(metadata.focal_length_physical, "100MM")
        self.assertEqual(metadata.focal_length_35mm, "310MM")
        self.assertIn(("CROP FOCAL", "200MM"), bot._metadata_stats(metadata))

    def test_existing_sony_alias_and_camera_brand_regression(self) -> None:
        metadata = bot._format_metadata(
            {
                "Make": "SONY",
                "Model": "Sony ILCE-7M4",
                "LensModel": "FE 24-70mm F2.8 GM II",
                "FNumber": "2.8",
                "FocalLengthIn35mmFilm": 70,
                "ExposureTime": _ExifRatio(1, 125),
                "PhotographicSensitivity": 100,
                "DateTimeOriginal": "2026:09:20 12:34:56",
            }
        )
        self.assertEqual(metadata.camera_make, "SONY")
        self.assertEqual(metadata.camera_model, "ILCE-7M4")
        self.assertEqual(metadata.lens, "24-70mm F2.8 GM II")
        self.assertEqual(metadata.lens_badge_key, "gm")
        self.assertEqual(metadata.shutter_speed, "1/125S")
        self.assertEqual(metadata.captured_at, "2026.09.20  12:34")

    def test_existing_sigma_alias_regression(self) -> None:
        metadata = bot._format_metadata(
            {
                "Make": "SIGMA",
                "Model": "SIGMA fp",
                "LensModel": "SIGMA 24-70mm F2.8 DG DN II Art",
            }
        )
        self.assertEqual(metadata.camera_model, "fp")
        self.assertEqual(metadata.lens, "24-70mm F2.8 Art II")
        self.assertEqual(metadata.lens_badge_key, "sigma")

    def test_existing_apple_lens_regression(self) -> None:
        self.assertEqual(
            bot._format_lens_name("iPhone 15 Pro back triple camera 6.765mm f/1.78"),
            "Main 7mm F1.8",
        )


class ImageLimitTests(unittest.TestCase):
    def test_default_pixel_limit_allows_66mp_image(self) -> None:
        self.assertEqual(bot.DEFAULT_MAX_IMAGE_PIXELS, 100_000_000)
        self.assertEqual(9984 * 6656, 66_453_504)

        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "synthetic.jpg"
            source_path.write_bytes(b"header-only test fixture")
            document = _FakeDocument(source_path)
            update, context, message = _handler_context(document)
            output = BytesIO(b"synthetic output")

            with (
                patch.object(
                    bot.Image,
                    "open",
                    return_value=_HeaderImage((9984, 6656)),
                ),
                patch.object(
                    bot,
                    "process_image",
                    return_value=(output, True),
                ) as process_image,
                patch.object(bot, "InputFile", return_value="input-file"),
            ):
                asyncio.run(bot.handle_image(update, context))

            process_image.assert_called_once_with(
                source_path,
                Path("signature.png"),
                95,
                "synthetic.jpg",
            )
            self.assertEqual(message.status.edits, ["Processing image...", "Done!"])

    def test_raw_processing_uses_full_raster_and_preserves_exif_focal_lengths(self) -> None:
        preview = MagicMock()
        preview.size = (6348, 4232)
        preview.__enter__.return_value = preview
        preview.convert.return_value = preview
        full_raster = MagicMock()
        full_raster.size = (9984, 6656)
        raw = MagicMock()
        raw.__enter__.return_value = raw
        raw.extract_thumb.return_value = types.SimpleNamespace(
            format=bot.rawpy.ThumbFormat.JPEG,
            data=b"embedded preview",
        )
        raw.postprocess.return_value = object()
        exif_tags = {
            "EXIF FocalLength": types.SimpleNamespace(values=[_ExifRatio(2026, 10)]),
            "EXIF FocalLengthIn35mmFilm": types.SimpleNamespace(
                values=[_ExifRatio(487, 1)]
            ),
        }

        with (
            patch.object(bot.rawpy, "imread", return_value=raw, create=True),
            patch.object(
                bot.rawpy,
                "HighlightMode",
                types.SimpleNamespace(Blend="blend"),
                create=True,
            ),
            patch.object(bot.Image, "open", return_value=preview),
            patch.object(
                bot.ImageOps, "exif_transpose", return_value=preview, create=True
            ),
            patch.object(bot.Image, "fromarray", return_value=full_raster, create=True),
            patch.object(bot.exifread, "process_file", return_value=exif_tags),
            patch.object(bot, "_add_metadata_panel") as add_panel,
        ):
            bot.process_image(b"synthetic raw", Path("signature.png"), 95, "photo.arw")

        processed_photo, metadata, _ = add_panel.call_args.args
        self.assertEqual(processed_photo.size, (9984, 6656))
        self.assertEqual(metadata.focal_length_35mm, "487MM")
        self.assertEqual(metadata.focal_length_physical, "203MM")
        self.assertEqual(bot._metadata_stats(metadata)[-1], ("35MM EQ", "487MM"))
        raw.extract_thumb.assert_not_called()
        raw.postprocess.assert_called_once()

    def test_image_over_pixel_limit_is_rejected_before_process_image(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "synthetic.jpg"
            source_path.write_bytes(b"header-only test fixture")
            document = _FakeDocument(source_path)
            update, context, message = _handler_context(document)

            with (
                patch.object(
                    bot.Image,
                    "open",
                    return_value=_HeaderImage((10001, 10000)),
                ),
                patch.object(bot, "process_image") as process_image,
            ):
                asyncio.run(bot.handle_image(update, context))

            process_image.assert_not_called()
            self.assertEqual(
                message.status.edits,
                ["Processing image...", bot.IMAGE_TOO_LARGE_MESSAGE],
            )
            self.assertIn("解析度/像素過高", message.status.edits[-1])

    def test_pillow_decompression_bomb_is_reported_as_too_large(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "synthetic.jpg"
            source_path.write_bytes(b"header-only test fixture")
            document = _FakeDocument(source_path)
            update, context, message = _handler_context(document)

            with (
                patch.object(
                    bot.Image,
                    "open",
                    side_effect=bot.Image.DecompressionBombError("too large"),
                ),
                patch.object(bot, "process_image") as process_image,
            ):
                asyncio.run(bot.handle_image(update, context))

            process_image.assert_not_called()
            self.assertEqual(message.status.edits[-1], bot.IMAGE_TOO_LARGE_MESSAGE)

    def test_normal_image_path_still_calls_process_image(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "synthetic.jpg"
            source_path.write_bytes(b"header-only test fixture")
            document = _FakeDocument(source_path)
            update, context, message = _handler_context(document)
            output = BytesIO(b"synthetic output")

            with (
                patch.object(
                    bot.Image,
                    "open",
                    return_value=_HeaderImage((1200, 800)),
                ),
                patch.object(
                    bot,
                    "process_image",
                    return_value=(output, True),
                ) as process_image,
                patch.object(bot, "InputFile", return_value="input-file"),
            ):
                asyncio.run(bot.handle_image(update, context))

            process_image.assert_called_once_with(
                source_path,
                Path("signature.png"),
                95,
                "synthetic.jpg",
            )
            self.assertEqual(message.status.edits, ["Processing image...", "Done!"])
            self.assertEqual(
                message.documents[0]["caption"],
                "Watermark and EXIF details added.",
            )

    def test_max_image_pixels_is_read_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "placeholder", "MAX_IMAGE_PIXELS": "123456"},
        ):
            settings = bot.Settings.from_environment()

        self.assertEqual(settings.max_image_pixels, 123456)

    def test_default_max_image_pixels_is_100mp(self) -> None:
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "placeholder"},
            clear=True,
        ):
            settings = bot.Settings.from_environment()

        self.assertEqual(settings.max_image_pixels, 100_000_000)


if __name__ == "__main__":
    unittest.main()
