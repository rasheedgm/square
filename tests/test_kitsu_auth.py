"""square_core.kitsu.auth -- login/refresh token extraction + session cache.

Regression coverage for a real bug found 2026-09-11: `gazu.log_in()` returns
the tokens as the top-level dict (`access_token`/`refresh_token`), not nested
under a `"tokens"` key, and gazu's client only ever gets a `.tokens` dict set
by `set_tokens()` (what `log_in()` uses) -- never `.access_token`/
`.refresh_token` attributes (those come only from
`KitsuClient.refresh_access_token()`). The old code checked both wrong
shapes, so a successful login silently cached an empty token, and the very
next `connect()` raised `NeedsLogin` again as if nothing had happened.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from square_core.kitsu import auth


class _FakeClient:
    """Mimics gazu.client.KitsuClient's real attribute shape -- only the
    attributes actually passed are set, matching how a fresh client behaves
    (no `.access_token`/`.refresh_token` unless something explicitly set
    them)."""
    def __init__(self, *, tokens=None, access_token=None, refresh_token=None):
        if tokens is not None:
            self.tokens = tokens
        if access_token is not None:
            self.access_token = access_token
        if refresh_token is not None:
            self.refresh_token = refresh_token


class _IsolatedStateDir(unittest.TestCase):
    """Every auth test needs its own session-cache file, never the real
    developer machine's ~/.square."""
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("SQUARE_STATE_DIR")
        os.environ["SQUARE_STATE_DIR"] = self._td.name

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("SQUARE_STATE_DIR", None)
        else:
            os.environ["SQUARE_STATE_DIR"] = self._old_env
        self._td.cleanup()


class TestTokensFromClient(_IsolatedStateDir):
    def test_prefers_attributes_over_the_tokens_dict(self):
        fake = _FakeClient(access_token="ATTR_AT", refresh_token="ATTR_RT",
                           tokens={"access_token": "DICT_AT", "refresh_token": "DICT_RT"})
        with patch("gazu.client.default_client", fake):
            self.assertEqual(auth._tokens_from_client(),
                             {"access_token": "ATTR_AT", "refresh_token": "ATTR_RT"})

    def test_falls_back_to_the_tokens_dict_when_no_attributes(self):
        # this is the real shape after gazu.log_in() -> set_tokens(): only
        # .tokens gets set, never .access_token / .refresh_token
        fake = _FakeClient(tokens={"access_token": "DICT_AT", "refresh_token": "DICT_RT"})
        with patch("gazu.client.default_client", fake):
            self.assertEqual(auth._tokens_from_client(),
                             {"access_token": "DICT_AT", "refresh_token": "DICT_RT"})

    def test_no_client_at_all_is_empty_not_a_crash(self):
        with patch("gazu.client.default_client", None):
            self.assertEqual(auth._tokens_from_client(),
                             {"access_token": "", "refresh_token": ""})


class TestLogin(_IsolatedStateDir):
    @patch("gazu.set_host")
    def test_reads_top_level_tokens_from_log_in_result(self, _set_host):
        """The primary, common-case path: gazu.log_in()'s return value IS the
        tokens dict -- not nested under a "tokens" key."""
        with patch("gazu.log_in", return_value={"access_token": "AT", "refresh_token": "RT",
                                                 "login": True}):
            tokens = auth.login("http://kitsu/api", "a@x.com", "pw")
        self.assertEqual(tokens, {"access_token": "AT", "refresh_token": "RT"})
        self.assertEqual(auth.cached_session("http://kitsu/api"), tokens)

    @patch("gazu.set_host")
    def test_falls_back_to_client_when_result_has_no_top_level_tokens(self, _set_host):
        fake = _FakeClient(tokens={"access_token": "AT2", "refresh_token": "RT2"})
        with patch("gazu.log_in", return_value={"login": True}), \
             patch("gazu.client.default_client", fake):
            tokens = auth.login("http://kitsu/api", "a@x.com", "pw")
        self.assertEqual(tokens, {"access_token": "AT2", "refresh_token": "RT2"})

    @patch("gazu.set_host")
    def test_regression_a_successful_login_never_caches_an_empty_token(self, _set_host):
        """The exact bug: gazu.log_in() succeeds (doesn't raise), its result
        has no nested "tokens" key (the real shape), and the client only has
        a .tokens dict (also the real shape after set_tokens()) -- both used
        to be read wrong, landing on an empty access_token that got cached
        anyway, so the next connect() saw NeedsLogin again despite a
        successful login."""
        fake = _FakeClient(tokens={"access_token": "REAL_AT", "refresh_token": "REAL_RT"})
        with patch("gazu.log_in", return_value={"login": True}), \
             patch("gazu.client.default_client", fake):
            tokens = auth.login("http://kitsu/api", "a@x.com", "pw")
        self.assertTrue(tokens["access_token"])
        cached = auth.cached_session("http://kitsu/api")
        self.assertTrue(cached and cached.get("access_token"))


if __name__ == "__main__":
    unittest.main()
