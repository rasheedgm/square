import http.server
import socketserver
import threading
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
