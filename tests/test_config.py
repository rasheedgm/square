import json
import shutil
import tempfile
import unittest
from pathlib import Path

from square_core.config import (
    StudioConfig, SHOT_DIRECTORY_TEMPLATE, DEFAULT_MEDIA_TYPE_CONFIGS,
    dest_template_renders, dest_template_versions_safely,
)


class TestDestTemplateValidation(unittest.TestCase):
    """
    The two low-level checks StudioConfig.load() uses to decide whether a
    persisted destination template is safe to trust.
    """

    def test_current_placeholder_names_render(self):
        self.assertTrue(dest_template_renders(
            "{nas_root}/{project_code}/shots/{sequence_code}/{shot_code}/{media_type}_{media_name}/v{version:03d}"
        ))

    def test_retired_placeholder_names_do_not_render(self):
        # {plate_type}/{plate_name} -- from before the plate-to-media rename.
        self.assertFalse(dest_template_renders(
            "{nas_root}/{project_code}/shots/{sequence_code}/{shot_code}/{plate_type}_{plate_name}/v{version:03d}"
        ))

    def test_empty_or_none_does_not_render(self):
        self.assertFalse(dest_template_renders(""))
        self.assertFalse(dest_template_renders(None))

    def test_template_without_version_does_not_version_safely(self):
        self.assertFalse(dest_template_versions_safely(
            "{nas_root}/{project_code}/shots/{sequence_code}/{shot_code}/{media_type}_{media_name}"
        ))

    def test_template_with_version_versions_safely(self):
        self.assertTrue(dest_template_versions_safely(
            "{nas_root}/{project_code}/shots/{sequence_code}/{shot_code}/{media_type}_{media_name}/v{version:03d}"
        ))

    def test_default_media_type_configs_all_version_safely(self):
        # Element/LUT/Audio/Matte used to have no {version} placeholder at
        # all -- every one of the 9 presets must include it now.
        for media_type, template in DEFAULT_MEDIA_TYPE_CONFIGS.items():
            with self.subTest(media_type=media_type):
                self.assertTrue(dest_template_versions_safely(template))


class TestStudioConfigSelfHeals(unittest.TestCase):
    """
    StudioConfig persists nas_dir_template / media_type_configs to disk on
    every Settings save, but neither is directly user-editable in the UI --
    so a copy saved before the plate-to-media rename (using {plate_type}/
    {plate_name}) would silently round-trip forever: load() reads the stale
    value back, save() writes it right back out. get_dest_dir()'s own
    except-and-fall-back for a template that fails to render then fires on
    EVERY call for any media type not in media_type_configs -- this was
    found live in this repo's own studio_config.json.

    load() must reject a persisted template that doesn't render with the
    CURRENT placeholder names, or that renders but doesn't vary by
    {version} (every version would alias onto the same folder), and fall
    back to the built-in default instead of trusting it.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.config_path = self.tmp / "studio_config.json"

    def _write(self, **overrides):
        data = {"kitsu_url": "http://localhost/api"}
        data.update(overrides)
        self.config_path.write_text(json.dumps(data))

    def test_stale_plate_placeholder_template_is_rejected(self):
        self._write(nas_dir_template=(
            "{nas_root}/{project_code}/shots/{sequence_code}/{shot_code}/"
            "{plate_type}_{plate_name}/v{version:03d}/{resolution}"
        ))
        config = StudioConfig(config_path=self.config_path)
        self.assertEqual(config.nas_dir_template, SHOT_DIRECTORY_TEMPLATE)

    def test_current_placeholder_template_is_kept(self):
        custom = "{nas_root}/{project_code}/{sequence_code}_{shot_code}/{media_type}_{media_name}/v{version:03d}"
        self._write(nas_dir_template=custom)
        config = StudioConfig(config_path=self.config_path)
        self.assertEqual(config.nas_dir_template, custom)

    def test_a_media_type_template_without_version_is_healed_to_the_default(self):
        self._write(media_type_configs={
            "Element": "{nas_root}/{project_code}/shots/{seq}/{shot}/elements/{media_name}",
        })
        config = StudioConfig(config_path=self.config_path)
        self.assertEqual(config.media_type_configs["Element"], DEFAULT_MEDIA_TYPE_CONFIGS["Element"])

    def test_a_custom_media_type_without_version_is_kept_but_not_silently_rewritten(self):
        # No built-in default exists for a studio's own custom type -- their
        # choice is preserved (load() only warns), not overwritten.
        self._write(media_type_configs={
            "VendorCam": "{nas_root}/{project_code}/shots/{seq}/{shot}/vendor/{media_name}",
        })
        config = StudioConfig(config_path=self.config_path)
        self.assertEqual(
            config.media_type_configs["VendorCam"],
            "{nas_root}/{project_code}/shots/{seq}/{shot}/vendor/{media_name}",
        )

    def test_a_valid_custom_media_type_template_is_kept_as_is(self):
        custom = "{nas_root}/{project_code}/shots/{seq}/{shot}/vendor/{media_name}_v{version:03d}"
        self._write(media_type_configs={"VendorCam": custom})
        config = StudioConfig(config_path=self.config_path)
        self.assertEqual(config.media_type_configs["VendorCam"], custom)

    def test_the_known_presets_are_unaffected_when_the_file_omits_them(self):
        self._write(media_type_configs={})
        config = StudioConfig(config_path=self.config_path)
        self.assertEqual(config.media_type_configs, DEFAULT_MEDIA_TYPE_CONFIGS)


if __name__ == "__main__":
    unittest.main()
