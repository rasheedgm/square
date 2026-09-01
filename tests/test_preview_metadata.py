import unittest

from square_core.preview_metadata import PreviewMetadata, KITSU_DATA_KEY, SCHEMA_VERSION


class TestPreviewMetadataRoundTrip(unittest.TestCase):
    def _sample(self):
        return PreviewMetadata(
            source_path=r"\\deliveries\showX\SQ010\SH0100\plate",
            source_sample_file="showX_SQ010_SH0100_plate.1001.exr",
            nas_path=r"X:\showX\shots\SQ010\SH0100\plates\main_v003",
            nas_sample_file="SQ010_SH0100_plate_main_v003.1001.exr",
            frame_range="1001-1096 (96 frames)",
            file_count=96,
            fps=24.0,
            resolution="3840x2160",
            colorspace="ACEScg",
            checksum="a1b2c3d4e5f60718",
            transfer_mode="copy",
            sequence_code="SQ010",
            shot_code="SH0100",
            media_type="Plate",
            media_name="main",
            version=3,
            ingested_at="2026-09-01T12:00:00Z",
            ingested_by="artist@studio.com",
            batch_id="batch-uuid-1",
        )

    def test_dict_round_trip_is_lossless(self):
        meta = self._sample()
        again = PreviewMetadata.from_dict(meta.to_dict())
        self.assertEqual(meta, again)

    def test_default_schema_version_is_stamped(self):
        self.assertEqual(PreviewMetadata().schema_version, SCHEMA_VERSION)


class TestToKitsuData(unittest.TestCase):
    def test_nests_under_the_namespaced_key(self):
        meta = PreviewMetadata(nas_path="/x/y", media_name="bg")
        data = meta.to_kitsu_data()
        self.assertIn(KITSU_DATA_KEY, data)
        self.assertEqual(data[KITSU_DATA_KEY]["nas_path"], "/x/y")

    def test_preserves_zous_own_media_metadata(self):
        existing = {"original_width": 1280, "original_height": 720, "original_duration": 4.0}
        data = PreviewMetadata(media_name="bg").to_kitsu_data(existing_data=existing)
        self.assertEqual(data["original_width"], 1280)
        self.assertEqual(data["original_height"], 720)
        self.assertEqual(data[KITSU_DATA_KEY]["media_name"], "bg")

    def test_does_not_mutate_the_passed_in_dict(self):
        existing = {"original_width": 1280}
        PreviewMetadata().to_kitsu_data(existing_data=existing)
        self.assertEqual(existing, {"original_width": 1280})

    def test_re_stamp_replaces_only_our_key(self):
        first = PreviewMetadata(version=1).to_kitsu_data({"original_width": 1280})
        second = PreviewMetadata(version=2).to_kitsu_data(first)
        self.assertEqual(second["original_width"], 1280)
        self.assertEqual(second[KITSU_DATA_KEY]["version"], 2)


class TestFromKitsuData(unittest.TestCase):
    def test_extracts_the_sub_dict(self):
        preview_data = {
            "original_width": 1280,
            KITSU_DATA_KEY: {"nas_path": "/x", "version": 5},
        }
        meta = PreviewMetadata.from_kitsu_data(preview_data)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.nas_path, "/x")
        self.assertEqual(meta.version, 5)

    def test_none_when_no_square_ingest_record(self):
        self.assertIsNone(PreviewMetadata.from_kitsu_data({"original_width": 1280}))
        self.assertIsNone(PreviewMetadata.from_kitsu_data(None))
        self.assertIsNone(PreviewMetadata.from_kitsu_data("not a dict"))

    def test_unknown_keys_are_ignored(self):
        meta = PreviewMetadata.from_dict({"nas_path": "/x", "some_future_field": "ignore me"})
        self.assertEqual(meta.nas_path, "/x")

    def test_missing_keys_take_defaults(self):
        meta = PreviewMetadata.from_dict({"nas_path": "/x"})
        self.assertEqual(meta.version, 1)
        self.assertEqual(meta.checksum_algo, "xxh3_64")
        self.assertIsNone(meta.fps)

    def test_empty_dict_is_none(self):
        self.assertIsNone(PreviewMetadata.from_dict({}))


if __name__ == "__main__":
    unittest.main()
