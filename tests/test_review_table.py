import shutil
import tempfile
import unittest
from pathlib import Path

from Qt import QtCore, QtWidgets

from square_core.ingest_controller import IngestController, ControllerConfig
from square_core.ingest_ledger import IngestLedger
from square_core.ingest_item import IngestItem, Status
from tools.ingest_tool.controller_bridge import ControllerBridge
from tools.ingest_tool.widgets.review_table import (
    IngestReviewTable, C_SHOT, C_VER, C_STATUS, C_PREV, C_CS,
)
from tests.test_ingest_controller import FakeNAS, FakeRecorder, FakeProxyGen, FakeExtractor, PROJECT


class ReviewTableTest(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "d"; self.src.mkdir()
        self.nas = FakeNAS(self.tmp / "nas")
        cfg = ControllerConfig(
            nas_root=str(self.tmp / "nas"), project_code="SHW",
            filename_template="{shot}_{name}_v{version:03d}.{frame}{ext}",
            preview_media_types=["Plate"], media_type_configs={"Plate": "p"},
            task_types=["Ingest"],
        )
        self.ctrl = IngestController(
            cfg, PROJECT, nas=self.nas, ledger=IngestLedger(self.tmp / "l.db"),
            recorder=FakeRecorder(), proxy_generator=FakeProxyGen(), extractor=FakeExtractor(),
        )
        self.bridge = ControllerBridge(self.ctrl)
        self.table = IngestReviewTable(self.bridge)

    def _item(self, name, **kw):
        files = []
        for i in range(2):
            p = self.src / f"{name}.{1001+i}.exr"; p.write_bytes(b"x" + name.encode() + bytes([i]))
            files.append(str(p))
        base = dict(sequence_code="SQ010", shot_code="SH0100", media_type="Plate",
                    media_name=name, version=1, start_frame=1001, end_frame=1002, frame_count=2)
        base.update(kw)
        return IngestItem(key=IngestItem.compute_key(files), source_files=files, ext=".exr",
                          source_name=name, **base)

    def _wait_job(self, timeout=5000):
        loop = QtCore.QEventLoop()
        self.bridge.job_finished.connect(lambda *a: loop.quit())
        QtCore.QTimer.singleShot(timeout, loop.quit)
        loop.exec()

    def _t(self):
        return self.table._table

    def test_rows_appear_on_load(self):
        self.bridge.load([self._item("a"), self._item("b")])
        self.assertEqual(self._t().rowCount(), 2)
        self.assertEqual(self._t().item(0, C_SHOT).text(), "SH0100")

    def test_status_updates_from_preflight_event(self):
        self.bridge.load([self._item("a")])
        self.bridge.preflight()
        self._wait_job()
        lbl = self._t().cellWidget(0, C_STATUS)
        self.assertEqual(lbl.text(), Status.NEW.value)

    def test_editing_shot_cell_calls_bridge(self):
        self.bridge.load([self._item("a")])
        self.bridge.preflight(); self._wait_job()
        self._t().item(0, C_SHOT).setText("SH0200")
        self.assertEqual(self.ctrl.items[0].shot_code, "SH0200")

    def test_editing_version_cell_parses_int(self):
        self.bridge.load([self._item("a")])
        self.bridge.preflight(); self._wait_job()
        self._t().item(0, C_VER).setText("4")
        self.assertEqual(self.ctrl.items[0].version, 4)

    def test_preview_checkbox_reflects_and_pushes(self):
        [it] = self.bridge.load([self._item("a")])          # Plate -> preview on
        chk = self._t().cellWidget(0, C_PREV).findChild(QtWidgets.QCheckBox)
        self.assertTrue(chk.isChecked())
        chk.setChecked(False)
        self.assertFalse(self.ctrl.get(it.key).preview_wanted)

    def test_unverified_metadata_cell_is_flagged(self):
        self.ctrl.extractor = FakeExtractor(found={"resolution": "1920x1080", "width": 1920, "height": 1080})
        self.bridge.load([self._item("a")])
        self.bridge.preflight(); self._wait_job()
        cs_cell = self._t().item(0, C_CS)
        self.assertEqual(cs_cell.text(), "set…")

    def test_completed_row_is_not_editable(self):
        self.bridge.load([self._item("a")])
        self.bridge.preflight(); self._wait_job()
        self.bridge.ingest(); self._wait_job()
        flags = self._t().item(0, C_SHOT).flags()
        self.assertFalse(bool(flags & QtCore.Qt.ItemFlag.ItemIsEditable))

    def test_selection_signal(self):
        got = []
        self.table.selection_changed.connect(got.append)
        self.bridge.load([self._item("a"), self._item("b")])
        self._t().selectRow(1)
        self.assertTrue(got and got[-1])


if __name__ == "__main__":
    unittest.main()
