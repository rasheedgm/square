import os
import shutil
import unittest
from pathlib import Path

from square_core.plate_scanner import PlateScanner
from square_core.metadata_extractor import MetadataExtractor
from square_core.kitsu_client import KitsuClient
from square_core.nas_manager import NASManager
from square_core.proxy_generator import ProxyGenerator
from tests.create_sample_plates import create_sample_media

class TestIngestPipeline(unittest.TestCase):
    """Test suite for Phase 1 Ingestion Pipeline components."""

    @classmethod
    def setUpClass(cls):
        create_sample_media()
        cls.test_media_dir = Path("d:/projects/square/test_data/incoming_plates")
        cls.test_nas_dir = Path("d:/projects/square/test_data/mock_nas")

    def test_01_scanner(self):
        scanner = PlateScanner(self.test_media_dir)
        items = scanner.scan()
        
        self.assertGreater(len(items), 0, "Scanner should discover media items")
        print(f"\n[Test] Scanner discovered {len(items)} items:")
        for i in items:
            print(f"  - {i.name}: {i.sequence_code} | {i.shot_code} | {i.plate_name} ({i.frame_range_str})")
        
        found_seqs = {item.sequence_code for item in items}
        self.assertIn("SQ010", found_seqs)
        self.assertIn("SQ020", found_seqs)

        # Verify missing frame detection on Shot 2
        sq020_item = next(i for i in items if i.sequence_code == "SQ020")
        self.assertTrue(sq020_item.has_warnings, "SQ020 should have missing frame warning")
        self.assertIn(1003, sq020_item.missing_frames)

    def test_02_kitsu_mock(self):
        client = KitsuClient(dry_run=True)
        self.assertTrue(client.connect())
        
        projects = client.get_all_projects()
        self.assertGreater(len(projects), 0)
        
        seq = client.get_or_create_sequence("proj-001", "SQ010")
        self.assertEqual(seq["name"], "SQ010")
        
        shot = client.get_or_create_shot("proj-001", seq["id"], "SH0100")
        self.assertEqual(shot["name"], "SH0100")
        
        tasks = client.create_default_tasks(shot["id"])
        self.assertEqual(len(tasks), 5)

    def test_03_nas_manager_dry_run(self):
        nas = NASManager(nas_root=str(self.test_nas_dir), dry_run=True)
        scanner = PlateScanner(self.test_media_dir)
        items = scanner.scan()
        item = items[0]
        
        dest_dir = nas.get_dest_dir("DEMO", item.sequence_code, item.shot_code, item.plate_name)
        self.assertIn("DEMO", str(dest_dir))
        
        copied = nas.copy_sequence(item, dest_dir)
        self.assertEqual(len(copied), len(item.files))

    def test_04_full_ingest_copy_and_checksum(self):
        """Tests live file copy with xxHash checksum verification."""
        nas = NASManager(nas_root=str(self.test_nas_dir / "real_nas"), dry_run=False)
        scanner = PlateScanner(self.test_media_dir)
        items = scanner.scan()
        item = items[0]  # SQ010

        dest_dir = nas.get_dest_dir("DEMO", item.sequence_code, item.shot_code, item.plate_name)
        nas.create_shot_structure(dest_dir)
        
        copied = nas.copy_sequence(item, dest_dir)
        self.assertEqual(len(copied), 10)
        self.assertTrue(os.path.exists(copied[0]))

        # Verify xxHash checksum calculation
        hash1 = nas.calculate_checksum(item.files[0])
        hash2 = nas.calculate_checksum(copied[0])
        self.assertEqual(hash1, hash2)
        print(f"\n[Test] Verified xxHash checksum match: {hash1}")

if __name__ == "__main__":
    unittest.main()
