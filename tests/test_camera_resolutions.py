"""Resolution catalogue and crop-estimate regressions."""

from __future__ import annotations

import base64
import csv
import hashlib
import unittest
import zlib
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_metadata_formatting import _ExifRatio, bot


DATA_FILE = Path(__file__).resolve().parents[1] / "camera_resolutions.csv"


class CameraResolutionTests(unittest.TestCase):
    def test_deployed_catalogue_matches_readable_source_and_covers_each_brand(
        self,
    ) -> None:
        body = "".join(
            line
            for line in DATA_FILE.read_text().splitlines(keepends=True)
            if not line.startswith("#")
        )
        embedded = (
            zlib.decompress(base64.b85decode(bot._CAMERA_CATALOG_B85)).decode()
            + bot._CAMERA_CATALOG_ADDITIONS
        )
        self.assertEqual(embedded, body)
        self.assertEqual(
            hashlib.sha256(body.encode()).hexdigest(), bot._CAMERA_CATALOG_SHA256
        )
        rows = list(csv.DictReader(StringIO(body)))
        self.assertEqual(len(rows), 2251)
        self.assertEqual(
            hashlib.sha256("".join(body.splitlines(keepends=True)[:-2]).encode()).hexdigest(),
            "bbc305b627c0388db450402a941202a616272c18768029e18ac80fc0e787463f",
        )
        self.assertEqual(
            [(row["model"], row["source"]) for row in rows[-2:]],
            [
                ("iPhone 18 Pro", "estimated_iphone17pro_proxy_not_verified"),
                ("iPhone 18 Pro Max", "estimated_iphone17pro_proxy_not_verified"),
            ],
        )
        self.assertEqual({row["brand"] for row in rows}, set(bot.BRAND_NAMES))
        self.assertTrue(
            all(int(row["width"]) > 0 and int(row["height"]) > 0 for row in rows)
        )
        self.assertTrue(all(row["source"] for row in rows))

    def test_actual_source_mappings_from_each_supported_brand(self) -> None:
        cases = (
            ("SONY", "A7 IV", (7008, 4672)),
            ("SIGMA", "fp", (6000, 4000)),
            ("NIKON CORPORATION", "Z6 III", (6048, 4032)),
            ("Canon Inc.", "EOS 850D", (6000, 4000)),
            ("LEICA CAMERA AG", "Q3", (9520, 6336)),
            ("Apple", "iPhone 15 Pro Max", (8064, 6048)),
            ("Apple", "iPhone 6 Plus", (3264, 2448)),
            ("RICOH IMAGING COMPANY, LTD.", "GR IV", (6192, 4128)),
            ("FUJIFILM", "X100VI", (7728, 5152)),
            ("Panasonic", "Lumix DC-S1RII", (8144, 5424)),
            ("OLYMPUS", "OM-D E-M5 Mark III", (5184, 3888)),
            ("OM Digital Solutions", "OM-1 Mark II", (5184, 3888)),
            ("PENTAX", "645Z", (8256, 6192)),
            ("HASSELBLAD", "X2D 100C", (11656, 8742)),
        )
        for make, model, expected in cases:
            with self.subTest(make=make, model=model):
                entry = bot._resolve_camera_resolution(make, model)
                self.assertIsNotNone(entry)
                self.assertEqual((entry.width, entry.height), expected)

    def test_exif_make_model_normalization_and_documented_aliases(self) -> None:
        cases = (
            ("SONY CORPORATION", "Sony ILCE-7M4", "A7 IV"),
            ("SONY", "α7R V", "a7R V"),
            ("Canon", "Canon EOS Rebel T8i", "EOS 850D"),
            (
                "OM Digital Solutions",
                "OM System OM-1 Mark II",
                "OM System OM-1 Mark II",
            ),
            ("OLYMPUS", "OM-1 Mark II", "OM System OM-1 Mark II"),
            ("FUJI FILM", "FUJIFILM X100VI", "X100VI"),
            ("Panasonic", "Panasonic Lumix DC-S1RII", "Lumix DC-S1RII"),
        )
        for make, model, expected in cases:
            with self.subTest(make=make, model=model):
                self.assertEqual(
                    bot._resolve_camera_resolution(make, model).model, expected
                )

    def test_model_catalogue_to_formatted_focal_without_dng_crop(self) -> None:
        # CrateCamDB documents 8064 x 6048 for iPhone 15 Pro Max; portrait
        # orientation yields 6048 x 8064 before the 3024 x 4032 final raster.
        exif = {
            "Make": "Apple",
            "Model": "iPhone 15 Pro Max",
            "FocalLength": _ExifRatio(100, 1),
            "FocalLengthIn35mmFilm": _ExifRatio(310, 1),
        }
        entry = bot._resolve_camera_resolution(exif["Make"], exif["Model"])
        self.assertEqual((entry.height, entry.width), (6048, 8064))
        self.assertEqual(
            bot._catalog_crop_factor("Apple", exif["Model"], exif, (3024, 4032)), 2
        )
        metadata = bot._format_metadata(exif, (3024, 4032))
        self.assertEqual(metadata.focal_length_physical, "100MM")
        self.assertEqual(metadata.focal_length_cropped, "200MM")
        self.assertEqual(metadata.focal_length_35mm, "310MM")
        self.assertEqual(
            metadata.focal_length_cropped_basis, "catalog_max_still_inferred"
        )
        self.assertEqual(bot._metadata_stats(metadata)[-1], ("CROP EST.", "200MM"))

    def test_iphone_18_models_resolve_estimated_catalog_crop_from_physical_focal(
        self,
    ) -> None:
        for model in ("iPhone 18 Pro", "iPhone 18 Pro Max"):
            with self.subTest(model=model):
                entry = bot._resolve_camera_resolution("Apple", model)
                self.assertIsNotNone(entry)
                self.assertEqual((entry.width, entry.height), (8064, 6048))
                self.assertEqual(entry.source, "estimated_iphone17pro_proxy_not_verified")
                exif = {
                    "Make": "Apple",
                    "Model": model,
                    "FocalLength": _ExifRatio(100, 1),
                    "FocalLengthIn35mmFilm": _ExifRatio(310, 1),
                }
                metadata = bot._format_metadata(exif, (3024, 4032))
                self.assertEqual(metadata.focal_length_physical, "100MM")
                self.assertEqual(metadata.focal_length_cropped, "200MM")
                self.assertEqual(metadata.focal_length_35mm, "310MM")
                self.assertEqual(
                    metadata.focal_length_cropped_basis, "catalog_max_still_inferred"
                )
                self.assertEqual(
                    bot._metadata_stats(metadata)[-1], ("CROP EST.", "200MM")
                )

                without_physical = bot._format_metadata(
                    {"Make": "Apple", "Model": model, "FocalLengthIn35mmFilm": 310},
                    (3024, 4032),
                )
                self.assertEqual(without_physical.focal_length_cropped, "—")
                self.assertEqual(
                    bot._metadata_stats(without_physical)[-1], ("35MM EQ", "310MM")
                )

                explicit = bot._format_metadata(
                    {
                        **exif,
                        "DngUserCrops": (
                            bot.DngUserCrop((4000, 6000), (1000, 1500, 3000, 4500)),
                        ),
                    },
                    (2000, 3000),
                )
                self.assertEqual(explicit.focal_length_cropped_basis, "dng_explicit")
                self.assertEqual(
                    bot._metadata_stats(explicit)[-1], ("CROP FOCAL", "200MM")
                )

    def test_unknown_models_prefer_35mm_equivalent_without_crop(self) -> None:
        cases = (
            ("Apple", "Unknown Model"),
            ("Sony", "Unknown Model"),
            ("Unknown", "Mystery Model"),
        )
        for make, model in cases:
            with self.subTest(make=make, model=model):
                exif = {
                    "Make": make,
                    "Model": model,
                    "FocalLength": _ExifRatio(100, 1),
                    "FocalLengthIn35mmFilm": _ExifRatio(310, 1),
                }
                self.assertIsNone(bot._resolve_camera_resolution(make, model))
                metadata = bot._format_metadata(exif, (3024, 4032))
                self.assertEqual(metadata.focal_length_physical, "100MM")
                self.assertEqual(metadata.focal_length_35mm, "310MM")
                self.assertEqual(metadata.focal_length_cropped, "—")
                self.assertEqual(bot._metadata_stats(metadata)[-1], ("35MM EQ", "310MM"))

    def test_unresolved_models_use_35mm_equivalent_only_without_physical_focal(
        self,
    ) -> None:
        cases = (
            ("Apple", "Unknown Model"),
            ("Sony", "Unknown Model"),
            ("Unknown", "Mystery Model"),
        )
        for make, model in cases:
            with self.subTest(make=make, model=model):
                metadata = bot._format_metadata(
                    {"Make": make, "Model": model, "FocalLengthIn35mmFilm": 310},
                    (3024, 4032),
                )
                self.assertEqual(metadata.focal_length_physical, "—")
                self.assertEqual(metadata.focal_length_cropped, "—")
                self.assertEqual(bot._metadata_stats(metadata)[-1], ("35MM EQ", "310MM"))

    def test_non_dng_image_processing_uses_catalogue(self) -> None:
        source = MagicMock()
        source.__enter__.return_value = source
        final_raster = MagicMock()
        final_raster.size = (3024, 4032)
        exif = {"Make": "Apple", "Model": "iPhone 15 Pro Max", "FocalLength": 100}
        with (
            patch.object(bot.Image, "open", return_value=source),
            patch.object(bot, "_read_exif", return_value=exif),
            patch.object(
                bot.ImageOps, "exif_transpose", return_value=final_raster, create=True
            ),
            patch.object(bot, "_add_metadata_panel", return_value=MagicMock()) as panel,
        ):
            bot.process_image(
                b"synthetic image", Path("signature.png"), 95, "photo.jpg"
            )
        self.assertEqual(panel.call_args.args[1].focal_length_cropped, "200MM")
        self.assertEqual(
            panel.call_args.args[1].focal_length_cropped_basis,
            "catalog_max_still_inferred",
        )

    def test_dng_without_default_user_crop_uses_catalogue(self) -> None:
        # Most DNGs have no DefaultUserCrop. The model route still works.
        tags = {
            "Image Make": MagicMock(values=["Apple"]),
            "Image Model": MagicMock(values=["iPhone 15 Pro Max"]),
            "EXIF FocalLength": MagicMock(values=[_ExifRatio(100, 1)]),
        }
        final_raster = MagicMock()
        final_raster.size = (3024, 4032)
        with (
            patch.object(bot.exifread, "process_file", return_value=tags),
            patch.object(bot, "_decode_raw_image", return_value=final_raster),
            patch.object(bot, "_add_metadata_panel", return_value=MagicMock()) as panel,
        ):
            bot.process_image(b"synthetic dng", Path("signature.png"), 95, "photo.dng")
        metadata = panel.call_args.args[1]
        self.assertEqual(metadata.focal_length_cropped, "200MM")
        self.assertEqual(
            metadata.focal_length_cropped_basis, "catalog_max_still_inferred"
        )

    def test_bad_explicit_dng_crop_does_not_hide_conflict_with_catalogue(self) -> None:
        exif = {
            "Make": "Apple",
            "Model": "iPhone 15 Pro Max",
            "FocalLength": 100,
            "DngUserCrops": ("invalid",),
        }
        self.assertEqual(
            bot._format_metadata(exif, (3024, 4032)).focal_length_cropped, "—"
        )

    def test_unknown_or_ambiguous_model_does_not_infer_crop(self) -> None:
        for make, model in (
            ("Sony", "Unknown Model"),
            ("Unknown", "A7 IV"),
            ("", "A7 IV"),
        ):
            with self.subTest(make=make, model=model):
                self.assertIsNone(bot._resolve_camera_resolution(make, model))
        a = bot.CameraResolution("sony", "A7 IV", 7008, 4672, "source_a")
        b = bot.CameraResolution("sony", "A7 IV variant", 8000, 6000, "source_b")
        with patch.object(
            bot, "_camera_resolution_catalog", return_value={("sony", "a7iv"): (a, b)}
        ):
            self.assertIsNone(bot._resolve_camera_resolution("Sony", "A7 IV"))
            self.assertIsNone(
                bot._catalog_crop_factor("Sony", "A7 IV", {}, (3504, 2336))
            )

    def test_incompatible_geometry_and_orientation_do_not_scale(self) -> None:
        exif = {"Make": "Apple", "Model": "iPhone 15 Pro Max", "FocalLength": 100}
        for size, orientation in (
            ((3024, 4000), 1),  # aspect change
            ((3024, 6048), 1),  # unequal axis magnification
            ((10000, 7500), 1),  # larger than catalogue maximum
            ((3024, 4032), 9),  # invalid orientation
            ((4032, 3024), 6),  # EXIF says transpose but raster was not transposed
        ):
            with self.subTest(size=size, orientation=orientation):
                metadata = bot._format_metadata(
                    {**exif, "Orientation": orientation}, size
                )
                self.assertEqual(metadata.focal_length_cropped, "—")
        rotated = bot._format_metadata({**exif, "Orientation": 6}, (3024, 4032))
        self.assertEqual(rotated.focal_length_cropped, "200MM")

    def test_35mm_equivalent_never_scales_and_no_physical_focal_means_no_crop(
        self,
    ) -> None:
        exif = {
            "Make": "Apple",
            "Model": "iPhone 15 Pro Max",
            "FocalLengthIn35mmFilm": 310,
        }
        metadata = bot._format_metadata(exif, (3024, 4032))
        self.assertEqual(metadata.focal_length_35mm, "310MM")
        self.assertEqual(metadata.focal_length_cropped, "—")
        self.assertIn(("35MM EQ", "310MM"), bot._metadata_stats(metadata))


if __name__ == "__main__":
    unittest.main()
