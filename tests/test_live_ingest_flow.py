import sys
import unittest
from pathlib import Path

# Add root directory to sys.path
root_dir = Path(__file__).parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from square_core.kitsu_client import KitsuClient
from square_core.plate_scanner import IngestSequenceItem
from square_core.proxy_generator import ProxyGenerator

class TestLiveIngestFlow(unittest.TestCase):

    def setUp(self):
        self.kitsu = KitsuClient(dry_run=True)
        self.kitsu.connect()
        self.proxy_gen = ProxyGenerator(dry_run=False)

    def test_01_task_type_and_task_creation(self):
        """Test task creation does not throw duplicate task type errors."""
        proj = self.kitsu.create_project("Test Flow Proj", "TFP")
        seq = self.kitsu.get_or_create_sequence(proj, "SQ010")
        shot = self.kitsu.get_or_create_shot(proj, seq, "SH0100")
        
        # Run twice to test idempotency & existing task types
        tasks1 = self.kitsu.create_default_tasks(shot)
        tasks2 = self.kitsu.create_default_tasks(shot)
        
        self.assertEqual(len(tasks1), 6)
        self.assertEqual(len(tasks2), 6)

    def test_02_proxy_slate_generation_and_upload(self):
        """Test fallback slate MP4 generation and preview upload formatting."""
        item = IngestSequenceItem(
            name="MYPROJ_SQ010_SH0100_PL01",
            files=[str(root_dir / "test_data" / "incoming_plates" / "SQ010" / "SQ010_SH0100_PL01" / "MYPROJ_SQ010_SH0100_PL01.1001.exr")],
            ext=".exr",
            is_video=False
        )
        item.sequence_code = "SQ010"
        item.shot_code = "SH0100"
        item.media_name = "PL01"

        mp4_path = self.proxy_gen.generate_proxy(item, "slate_test_preview.mp4")
        self.assertIsNotNone(mp4_path)
        self.assertTrue(mp4_path.endswith(".mp4"))

        # Test upload formatting
        task = {"id": "mock-task-id-12345"}
        res = self.kitsu.upload_preview_proxy(task, mp4_path, comment="Ingest Test Slate")
        self.assertIsNotNone(res)

if __name__ == "__main__":
    unittest.main()
