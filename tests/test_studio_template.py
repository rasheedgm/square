"""`studio_config.template.json` must be exactly `default_template()` -- it is
the full reference ("everything the config supports") that the README tells
people to copy and deploy refreshes on the NAS, generated from code so it can
never quietly drift out of sync. Run `python -m
tools.pipeline_deploy.gen_studio_template` after touching
`DEFAULT_PROJECT_CONFIG` or the studio defaults, then commit the file."""

import json
import unittest
from pathlib import Path

from square_core.config.pipeline import default_template
from square_core.config.project import DEFAULT_PROJECT_CONFIG

_REPO_ROOT = Path(__file__).resolve().parents[1]


class TestStudioTemplate(unittest.TestCase):
    def test_generator_embeds_the_full_project_defaults(self):
        t = default_template()
        self.assertEqual(t["project_defaults"], DEFAULT_PROJECT_CONFIG)
        self.assertIn("media_types", t["project_defaults"])
        self.assertIn("roots", t["project_defaults"])

    def test_checked_in_file_matches_the_generator(self):
        p = _REPO_ROOT / "studio_config.template.json"
        self.assertTrue(p.exists(), f"{p} missing")
        on_disk = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, default_template(),
                         "studio_config.template.json is stale -- run "
                         "`python -m tools.pipeline_deploy.gen_studio_template` and commit it")


if __name__ == "__main__":
    unittest.main()
