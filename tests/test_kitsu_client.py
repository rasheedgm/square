import http.server
import socketserver
import threading
import time
import unittest
import uuid

from square_core.kitsu_client import KitsuClient


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")


class TestKitsuClientConnectErrorSurfacing(unittest.TestCase):
    """
    Real-world report: a studio's Kitsu is hosted on their NAS without SSL,
    and connecting showed either an opaque SSL error or just failed to
    connect, with no way to tell which from the UI. connect() previously
    only logged the real exception -- swallowing it everywhere the UI could
    have shown it (Settings' Test Connection, the main window's status
    indicator). last_error now carries it so those can surface the actual
    reason (SSL, DNS, refused, wrong credentials, ...) instead of a generic
    message.
    """

    def test_https_against_a_plain_http_server_surfaces_an_ssl_error(self):
        # Reproduces the exact reported scenario: a server that only speaks
        # plain HTTP, reached with a https:// configured host.
        httpd = socketserver.TCPServer(("127.0.0.1", 0), _QuietHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        time.sleep(0.2)

        client = KitsuClient(host=f"https://127.0.0.1:{port}/api", email="a@b.com", password="x", dry_run=False)
        ok = client.connect()

        self.assertFalse(ok)
        self.assertFalse(client.is_connected)
        self.assertIsNotNone(client.last_error)
        self.assertIn("ssl", client.last_error.lower())

    def test_successful_connect_clears_last_error(self):
        client = KitsuClient(dry_run=True)
        client.last_error = "stale error from a previous attempt"
        ok = client.connect()
        self.assertTrue(ok)
        self.assertIsNone(client.last_error)

    def test_default_host_is_unencrypted_matching_the_studio_config_default(self):
        # The hardcoded fallback previously defaulted to https://, disagreeing
        # with config.py's own DEFAULT_KITSU_URL ("http://localhost/api") --
        # a studio's real host always comes from config, but the two
        # defaults silently drifting apart is its own bug waiting to bite.
        client = KitsuClient()
        self.assertTrue(client.host.startswith("http://"))


class _FakeGazuShot:
    """
    Stands in for gazu.shot against a fixed in-memory shot list, so
    check_shots() can be exercised without a live Kitsu server.
    """

    def __init__(self, shots, sequences=None):
        self._shots = shots
        self._sequences = sequences or []

    def all_shots_for_project(self, project):
        return self._shots

    def all_sequences_for_project(self, project):
        return self._sequences


class _FakeGazu:
    def __init__(self, shots, sequences=None):
        self.shot = _FakeGazuShot(shots, sequences)


class TestKitsuCheckShots(unittest.TestCase):
    """
    check_shots() is the pre-flight that answers a question the NAS check
    cannot: a shot code from a client's folder structure may already exist
    in Kitsu under a DIFFERENT sequence. Ingesting then either attaches
    media to the wrong shot or creates a duplicate -- so that state (and a
    shot name split across several sequences) must be surfaced as a
    conflict, while a shot that simply doesn't exist yet is only
    informational.
    """

    def _connected_client(self, shots, sequences=None):
        client = KitsuClient(dry_run=False)
        client.gazu = _FakeGazu(shots, sequences)
        client.is_connected = True
        return client

    def test_matching_sequence_is_ok_not_a_conflict(self):
        client = self._connected_client([
            {"name": "SH0100", "sequence_name": "SQ010", "sequence_id": "s1"},
        ])
        report = client.check_shots("proj", [("SQ010", "SH0100")])
        finding = report[("SQ010", "SH0100")]
        self.assertEqual(finding["state"], KitsuClient.KITSU_OK)
        self.assertNotIn(finding["state"], KitsuClient.KITSU_CONFLICT_STATES)

    def test_shot_under_a_different_sequence_is_a_conflict(self):
        client = self._connected_client([
            {"name": "SH0100", "sequence_name": "SQ099", "sequence_id": "s1"},
        ])
        report = client.check_shots("proj", [("SQ010", "SH0100")])
        finding = report[("SQ010", "SH0100")]
        self.assertEqual(finding["state"], KitsuClient.KITSU_WRONG_SEQUENCE)
        self.assertIn(finding["state"], KitsuClient.KITSU_CONFLICT_STATES)
        self.assertIn("SQ099", finding["message"])

    def test_shot_name_split_across_sequences_is_ambiguous_conflict(self):
        client = self._connected_client([
            {"name": "SH0100", "sequence_name": "SQ010", "sequence_id": "s1"},
            {"name": "SH0100", "sequence_name": "SQ020", "sequence_id": "s2"},
        ])
        report = client.check_shots("proj", [("SQ010", "SH0100")])
        finding = report[("SQ010", "SH0100")]
        self.assertEqual(finding["state"], KitsuClient.KITSU_AMBIGUOUS)
        self.assertIn(finding["state"], KitsuClient.KITSU_CONFLICT_STATES)

    def test_unknown_shot_is_informational_new_shot_not_a_conflict(self):
        client = self._connected_client([])
        report = client.check_shots("proj", [("SQ010", "SH0100")])
        finding = report[("SQ010", "SH0100")]
        self.assertEqual(finding["state"], KitsuClient.KITSU_NEW_SHOT)
        self.assertNotIn(finding["state"], KitsuClient.KITSU_CONFLICT_STATES)

    def test_missing_sequence_name_falls_back_to_the_project_sequence_list(self):
        # Older Kitsu servers omit sequence_name from the shot dict -- the
        # shot must still be resolvable via sequence_id, not silently read
        # as if it had no sequence at all (which would hide a real conflict).
        client = self._connected_client(
            shots=[{"name": "SH0100", "sequence_id": "s1"}],
            sequences=[{"id": "s1", "name": "SQ099"}],
        )
        report = client.check_shots("proj", [("SQ010", "SH0100")])
        self.assertEqual(report[("SQ010", "SH0100")]["state"], KitsuClient.KITSU_WRONG_SEQUENCE)

    def test_disconnected_client_reports_unknown_for_every_row(self):
        client = KitsuClient(dry_run=False)
        client.is_connected = False
        report = client.check_shots("proj", [("SQ010", "SH0100"), ("SQ020", "SH0200")])
        self.assertTrue(all(f["state"] == KitsuClient.KITSU_UNKNOWN for f in report.values()))
        self.assertTrue(all(f["state"] not in KitsuClient.KITSU_CONFLICT_STATES for f in report.values()))

    def test_rows_missing_a_shot_code_are_skipped(self):
        client = self._connected_client([])
        report = client.check_shots("proj", [("SQ010", "")])
        self.assertEqual(report, {})

    def test_duplicate_rows_collapse_to_one_lookup_key(self):
        client = self._connected_client([
            {"name": "SH0100", "sequence_name": "SQ010", "sequence_id": "s1"},
        ])
        report = client.check_shots("proj", [("sq010", "sh0100"), ("SQ010", "SH0100")])
        self.assertEqual(len(report), 1)
        self.assertIn(("SQ010", "SH0100"), report)


class _FakeGazuShotWithData:
    """
    A minimal live gazu.shot stand-in that actually PERSISTS shot.data
    across calls (a MagicMock's return_value doesn't), so the merge-vs-
    overwrite behavior of get_or_create_shot/record_version can be verified
    against something resembling a real server round-trip.
    """

    def __init__(self):
        self.shots = {}   # name -> shot dict

    def get_shot_by_name(self, sequence, shot_name):
        return self.shots.get(shot_name)

    def new_shot(self, project, sequence, shot_name, nb_frames=0, data=None):
        # A proper 36-char UUID, matching what a real Kitsu server (and this
        # codebase'''s own offline uuid5 fallback) actually returns -- record_version()
        # deliberately skips its live write for anything shorter, the same
        # guard add_version_comment/upload_preview_proxy already use to
        # avoid writing against an obviously-fake test/mock ID.
        shot_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"shot-{shot_name}"))
        shot = {"id": shot_id, "name": shot_name, "data": dict(data or {})}
        self.shots[shot_name] = shot
        return shot

    def update_shot_data(self, shot, data):
        name = shot.get("name")
        stored = self.shots.get(name, dict(shot))
        stored["data"] = dict(data)
        self.shots[name] = stored
        return stored


class TestGetOrCreateShotPreservesVersionHistory(unittest.TestCase):
    """
    get_or_create_shot used to overwrite media_items[media_name] wholesale
    on every call -- ingesting v2 of the same media erased v1's record
    entirely, with no way to see from Kitsu what had actually been
    delivered before. It's called before the version to ingest is even
    resolved (so it can't record a version itself, see record_version
    below), but it must never destroy a "versions" history that's already
    there.
    """

    def _connected_client(self):
        client = KitsuClient(dry_run=False)
        client.gazu = type("_FakeGazu", (), {"shot": _FakeGazuShotWithData()})()
        client.is_connected = True
        return client

    def test_creates_the_shot_with_an_empty_versions_dict(self):
        client = self._connected_client()
        shot = client.get_or_create_shot({"id": "p1"}, {"id": "s1"}, "SH0100", media_name="BG")
        self.assertEqual(shot["data"]["media_items"]["BG"]["versions"], {})

    def test_a_second_call_does_not_erase_a_version_already_recorded(self):
        client = self._connected_client()
        shot = client.get_or_create_shot({"id": "p1"}, {"id": "s1"}, "SH0100", media_name="BG")
        client.record_version(shot, "BG", 1, {"nas_path": "/nas/v001"})

        # A later ingest re-syncs basic shot metadata (frame range etc.)
        # before the NEW version is known -- must not wipe v1's entry.
        shot2 = client.get_or_create_shot({"id": "p1"}, {"id": "s1"}, "SH0100", media_name="BG")
        self.assertIn("v001", shot2["data"]["media_items"]["BG"]["versions"])

    def test_a_different_media_name_on_the_same_shot_keeps_its_own_history(self):
        client = self._connected_client()
        shot = client.get_or_create_shot({"id": "p1"}, {"id": "s1"}, "SH0100", media_name="BG")
        client.record_version(shot, "BG", 1, {"nas_path": "/nas/bg_v001"})
        shot = client.get_or_create_shot({"id": "p1"}, {"id": "s1"}, "SH0100", media_name="FG")
        client.record_version(shot, "FG", 1, {"nas_path": "/nas/fg_v001"})

        final = client.gazu.shot.shots["SH0100"]
        self.assertIn("v001", final["data"]["media_items"]["BG"]["versions"])
        self.assertIn("v001", final["data"]["media_items"]["FG"]["versions"])


class TestRecordVersion(unittest.TestCase):
    """record_version() is the one place that writes a version's own ledger entry."""

    def _connected_client(self):
        client = KitsuClient(dry_run=False)
        client.gazu = type("_FakeGazu", (), {"shot": _FakeGazuShotWithData()})()
        client.is_connected = True
        return client

    def test_recording_v2_does_not_remove_v1(self):
        client = self._connected_client()
        shot = client.get_or_create_shot({"id": "p1"}, {"id": "s1"}, "SH0100", media_name="BG")
        client.record_version(shot, "BG", 1, {"nas_path": "/nas/v001"})
        shot = client.gazu.shot.shots["SH0100"]
        client.record_version(shot, "BG", 2, {"nas_path": "/nas/v002"})

        versions = client.gazu.shot.shots["SH0100"]["data"]["media_items"]["BG"]["versions"]
        self.assertEqual(set(versions.keys()), {"v001", "v002"})
        self.assertEqual(versions["v001"]["nas_path"], "/nas/v001")
        self.assertEqual(versions["v002"]["nas_path"], "/nas/v002")

    def test_latest_version_pointer_tracks_the_most_recent_call(self):
        client = self._connected_client()
        shot = client.get_or_create_shot({"id": "p1"}, {"id": "s1"}, "SH0100", media_name="BG")
        client.record_version(shot, "BG", 1, {})
        shot = client.gazu.shot.shots["SH0100"]
        client.record_version(shot, "BG", 5, {})

        self.assertEqual(
            client.gazu.shot.shots["SH0100"]["data"]["media_items"]["BG"]["latest_version"], 5
        )

    def test_re_recording_the_same_version_overwrites_only_that_entry(self):
        client = self._connected_client()
        shot = client.get_or_create_shot({"id": "p1"}, {"id": "s1"}, "SH0100", media_name="BG")
        client.record_version(shot, "BG", 1, {"checksum": "aaa"})
        shot = client.gazu.shot.shots["SH0100"]
        client.record_version(shot, "BG", 1, {"checksum": "bbb"})

        versions = client.gazu.shot.shots["SH0100"]["data"]["media_items"]["BG"]["versions"]
        self.assertEqual(versions["v001"]["checksum"], "bbb")

    def test_offline_client_still_returns_a_usable_dict(self):
        client = KitsuClient(dry_run=True)
        shot = {"id": "mock-shot", "data": {}}
        result = client.record_version(shot, "BG", 1, {"nas_path": "/nas/v001"})
        self.assertEqual(
            result["data"]["media_items"]["BG"]["versions"]["v001"]["nas_path"], "/nas/v001"
        )


class _FakeGazuFilesAPI:
    """Stands in for gazu.files -- just enough of update_preview to verify what gets sent."""

    def __init__(self):
        self.update_preview_calls = []

    def update_preview(self, preview_file, data):
        self.update_preview_calls.append((preview_file, dict(data)))
        return {**preview_file, "data": data}


class TestAttachPreviewSourceMetadata(unittest.TestCase):
    """
    A review or delivery tool addresses a preview by task+revision -- Kitsu's
    revision numbering belongs to the preview file, not the shot. Stamping
    the real NAS path directly onto that SAME preview file record (via
    gazu.files.update_preview) means such a tool gets the source path back
    in the one query it already makes for the movie, instead of needing a
    second lookup against our own separate version ledger.
    """

    def _connected_client(self):
        client = KitsuClient(dry_run=False)
        fake_files = _FakeGazuFilesAPI()
        client.gazu = type("_FakeGazu", (), {"files": fake_files})()
        client.is_connected = True
        return client, fake_files

    def test_live_call_updates_the_real_preview_file(self):
        client, fake_files = self._connected_client()
        preview = {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "preview-1")), "revision": 2}
        source_info = {"nas_path": "/nas/proj/SQ010/SH0100/plates/BG_v002", "sample_file": "x.exr"}

        client.attach_preview_source_metadata(preview, source_info)

        self.assertEqual(len(fake_files.update_preview_calls), 1)
        called_preview, called_data = fake_files.update_preview_calls[0]
        self.assertEqual(called_preview["id"], preview["id"])
        self.assertEqual(called_data, source_info)

    def test_non_uuid_preview_id_skips_the_live_call(self):
        # Matches the same "obviously a mock/test ID" guard used elsewhere
        # (add_version_comment, upload_preview_proxy) -- a short or
        # "mock"-tagged ID means the preview itself was never really created.
        client, fake_files = self._connected_client()
        result = client.attach_preview_source_metadata({"id": "preview1"}, {"nas_path": "/nas/x"})
        self.assertEqual(fake_files.update_preview_calls, [])
        self.assertEqual(result["nas_path"], "/nas/x")   # still returns something usable

    def test_offline_client_returns_a_usable_merged_dict(self):
        client = KitsuClient(dry_run=True)
        result = client.attach_preview_source_metadata(
            {"id": "mock-preview"}, {"nas_path": "/nas/proj/SQ010/SH0100/plates/BG_v001"}
        )
        self.assertEqual(result["nas_path"], "/nas/proj/SQ010/SH0100/plates/BG_v001")

    def test_a_gazu_error_is_caught_not_raised(self):
        client, fake_files = self._connected_client()

        def _boom(preview_file, data):
            raise RuntimeError("simulated network failure")
        fake_files.update_preview = _boom

        preview = {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "preview-2"))}
        result = client.attach_preview_source_metadata(preview, {"nas_path": "/nas/x"})
        self.assertIsNotNone(result)   # does not raise, ingest must not abort over this


if __name__ == "__main__":
    unittest.main()
