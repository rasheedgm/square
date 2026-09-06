"""tools.ingest_tool.core.config_keys -- the ingest tool's tools.ingest.* keys."""

import tempfile
import unittest
from pathlib import Path

from square_core.config import schema
from square_core.context import PipelineContext
from square_core.config.pipeline import PipelineConfig
from square_core.services import projects
from square_core.services.projects import ProjectSpec

from tools.ingest_tool.core import config_keys
from tools.ingest_tool.core.controller import IngestController
from tools.ingest_tool.core.ledger import NullLedger
from tests.test_ingest_controller import _TrackingKitsu


def _pctx(nas, project_data=None):
    cfg = PipelineConfig(nas_roots={"default": nas})
    api = _TrackingKitsu()
    ctx = PipelineContext(config=cfg, kitsu=api, user=api.current_user())
    projects.create(ctx, ProjectSpec(code="ABC", fps=24.0))
    pctx = ctx.project("ABC")
    if project_data:
        pctx.config.data.setdefault("tools", {}).setdefault("ingest", {}).update(project_data)
    return pctx


class TestIngestConfigKeys(unittest.TestCase):
    def test_the_three_keys_are_registered(self):
        for k in ("tools.ingest.task_types", "tools.ingest.task_status",
                  "tools.ingest.transfer_mode"):
            self.assertIsNotNone(schema.get(k), k)
        self.assertEqual(schema.get("tools.ingest.transfer_mode").choices,
                         ("copy", "hardlink", "symlink"))

    def test_read_returns_the_schema_default_when_unset(self):
        with tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            self.assertEqual(config_keys.read(pctx, "task_status"), "Done")
            self.assertEqual(config_keys.read(pctx, "transfer_mode"), "copy")
            self.assertIn("Ingest", config_keys.read(pctx, "task_types"))

    def test_read_returns_a_project_override(self):
        with tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work, {"task_status": "", "transfer_mode": "hardlink",
                                "task_types": ["Ingest", "Comp"]})
            self.assertEqual(config_keys.read(pctx, "task_status"), "")
            self.assertEqual(config_keys.read(pctx, "transfer_mode"), "hardlink")
            self.assertEqual(config_keys.read(pctx, "task_types"), ["Ingest", "Comp"])

    def test_controller_honours_configured_transfer_mode(self):
        with tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work, {"transfer_mode": "hardlink"})
            c = IngestController(pctx, ledger=NullLedger(), task_types=["Ingest"],
                                 transfer_mode=config_keys.read(pctx, "transfer_mode"))
            self.assertEqual(c.transfer_mode, "hardlink")

    def test_controller_defaults_transfer_mode_to_copy_for_a_none(self):
        with tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            c = IngestController(pctx, ledger=NullLedger(), task_types=["Ingest"],
                                 transfer_mode=None)
            self.assertEqual(c.transfer_mode, "copy")

    def test_the_config_editor_registers_these_keys_on_import(self):
        # editor.py imports config_keys so the admin sees them even when the
        # ingest tool itself isn't the process running.
        import importlib
        import tools.config_editor.core.editor as ed
        importlib.reload(ed)
        self.assertIsNotNone(schema.get("tools.ingest.task_types"))


if __name__ == "__main__":
    unittest.main()
