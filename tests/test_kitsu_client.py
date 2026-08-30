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


if __name__ == "__main__":
    unittest.main()
