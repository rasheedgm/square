import unittest
from square_core.kitsu_client import KitsuClient

class TestProjectCreation(unittest.TestCase):

    def test_create_and_fetch_project(self):
        client = KitsuClient(dry_run=True)
        client.connect()

        initial_projects = client.get_all_projects()
        initial_count = len(initial_projects)
        print(f"\n[Test] Initial projects count: {initial_count}")

        # Create new project
        new_proj = client.create_project("Avatar Sequel", "AVT2")
        self.assertIsNotNone(new_proj)
        self.assertEqual(new_proj["name"], "Avatar Sequel")
        self.assertEqual(new_proj["code"], "AVT2")

        # Fetch all projects again
        updated_projects = client.get_all_projects()
        print(f"[Test] Updated projects count: {len(updated_projects)}")

        # Verify newly created project is in the list
        names = [p["name"] for p in updated_projects]
        self.assertIn("Avatar Sequel", names, "Newly created project should be present in project list")

if __name__ == "__main__":
    unittest.main()
