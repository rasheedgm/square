"""square_core.model -- the pure value objects. No I/O, no gazu, no Qt."""

import unittest

from square_core.model import (
    EntityRef,
    Version,
    Project,
    Shot,
    Task,
    MediaInfo,
    Provenance,
    PathContext,
    ProjectCreated,
    PublishResult,
    Output,
    KITSU_DATA_KEY,
)


class TestEntityRef(unittest.TestCase):
    def test_str_prefers_code(self):
        self.assertEqual(str(EntityRef("shot", "uuid-1", "SH0100")), "shot:SH0100")

    def test_str_falls_back_to_id(self):
        self.assertEqual(str(EntityRef("shot", "uuid-1")), "shot:uuid-1")

    def test_frozen_hashable(self):
        r = EntityRef("shot", "uuid-1", "SH0100")
        self.assertEqual(r, EntityRef("shot", "uuid-1", "SH0100"))
        self.assertIn(r, {r})

    def test_as_dict(self):
        self.assertEqual(
            EntityRef("task", "t1").as_dict(),
            {"type": "task", "id": "t1", "code": ""},
        )


class TestVersion(unittest.TestCase):
    def test_label_major_only(self):
        self.assertEqual(Version(3).label(), "v003")

    def test_label_with_minor(self):
        self.assertEqual(Version(3, 2).label(), "v003.02")

    def test_label_custom_pad(self):
        self.assertEqual(Version(3).label(pad=4), "v0003")

    def test_ordering(self):
        vs = [Version(2), Version(1, 5), Version(1), Version(2, 1)]
        self.assertEqual(
            sorted(vs),
            [Version(1), Version(1, 5), Version(2), Version(2, 1)],
        )

    def test_max(self):
        self.assertEqual(max([Version(1), Version(3), Version(2, 9)]), Version(3))

    def test_bump(self):
        self.assertEqual(Version(3, 2).bump_major(), Version(4, 0))
        self.assertEqual(Version(3, 2).bump_minor(), Version(3, 3))


class TestEntities(unittest.TestCase):
    def test_raw_excluded_from_equality(self):
        a = Shot("s1", "SH0100", raw={"a": 1})
        b = Shot("s1", "SH0100", raw={"b": 2})
        self.assertEqual(a, b)

    def test_project_is_episodic(self):
        self.assertTrue(Project("p1", "ABC", production_type="tvshow").is_episodic)
        self.assertFalse(Project("p1", "ABC", production_type="feature").is_episodic)

    def test_ref_helpers(self):
        self.assertEqual(Shot("s1", "SH0100").ref(), EntityRef("shot", "s1", "SH0100"))
        t = Task("t1", entity_id="s1", entity_type="shot", task_type_name="Comp")
        self.assertEqual(t.ref(), EntityRef("task", "t1", "Comp"))
        self.assertEqual(t.entity_ref(), EntityRef("shot", "s1"))

    def test_defaults_do_not_share_mutable_state(self):
        a, b = Shot("s1", "A"), Shot("s2", "B")
        a.data["x"] = 1
        self.assertEqual(b.data, {})


class TestMediaInfo(unittest.TestCase):
    def test_frame_count_and_range_label(self):
        m = MediaInfo(frame_in=1001, frame_out=1096)
        self.assertEqual(m.frame_count, 96)
        self.assertEqual(m.range_label(), "1001-1096 (96 frames)")

    def test_range_label_single_frame(self):
        self.assertEqual(MediaInfo(frame_in=1, frame_out=1).range_label(), "1-1 (1 frame)")

    def test_range_label_with_missing(self):
        m = MediaInfo(frame_in=1, frame_out=10, missing_frames=[3, 4])
        self.assertIn("2 missing", m.range_label())

    def test_range_label_empty_when_unknown(self):
        self.assertEqual(MediaInfo().range_label(), "")

    def test_missing_required_flags_unverified(self):
        m = MediaInfo(resolution="3840x2160", fps=24.0, colorspace="ACEScg",
                      verified={"resolution": True, "fps": True})
        # colorspace present but not verified -> still "missing"
        self.assertEqual(m.missing_required(), ["colorspace"])

    def test_missing_required_all_good(self):
        m = MediaInfo(resolution="3840x2160", fps=24.0, colorspace="ACEScg",
                      verified={"resolution": True, "fps": True, "colorspace": True})
        self.assertEqual(m.missing_required(), [])


class TestProvenance(unittest.TestCase):
    def test_kitsu_data_key(self):
        self.assertEqual(KITSU_DATA_KEY, "square")

    def test_round_trip(self):
        p = Provenance(kind="ingest", shot_code="SH0100", version=3, checksum="abc")
        data = p.to_kitsu_data({"original_width": 3840})
        self.assertEqual(data["original_width"], 3840)          # Zou's own key preserved
        back = Provenance.from_kitsu_data(data)
        self.assertEqual(back, p)

    def test_from_kitsu_data_none_when_absent(self):
        self.assertIsNone(Provenance.from_kitsu_data({"original_width": 100}))
        self.assertIsNone(Provenance.from_kitsu_data(None))

    def test_from_dict_tolerates_unknown_and_missing(self):
        p = Provenance.from_dict({"shot_code": "SH0100", "some_future_field": 9})
        self.assertEqual(p.shot_code, "SH0100")
        self.assertEqual(p.version, 1)

    def test_to_kitsu_data_does_not_mutate_existing(self):
        existing = {"original_width": 1}
        Provenance(shot_code="X").to_kitsu_data(existing)
        self.assertEqual(existing, {"original_width": 1})


class TestPathContext(unittest.TestCase):
    def test_media_type_aliases_output_type(self):
        self.assertEqual(PathContext("X:", "ABC", output_type="Plate").media_type, "Plate")

    def test_with_override(self):
        ctx = PathContext("X:", "ABC", shot="SH0100")
        self.assertEqual(ctx.with_(frame=1001).frame, 1001)
        self.assertIsNone(ctx.frame)                            # original unchanged

    def test_frozen(self):
        ctx = PathContext("X:", "ABC")
        with self.assertRaises(Exception):
            ctx.shot = "SH0100"

    def test_field_names_includes_frame(self):
        self.assertIn("frame", PathContext.field_names())


class TestResults(unittest.TestCase):
    def test_shapes(self):
        pc = ProjectCreated(project=Project("p1", "ABC"), config_path="X:/ABC/_pipeline/project_config.json")
        self.assertEqual(pc.folders_created, [])
        pr = PublishResult(output=Output(output_type="comp", revision=3), path="X:/o")
        self.assertIsNone(pr.preview)
        self.assertEqual(pr.checksums, {})


if __name__ == "__main__":
    unittest.main()
