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
import sys
import tempfile
import types
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


class TestKeyNormalization(unittest.TestCase):
    def test_collapses_an_internal_double_slash(self):
        self.assertEqual(auth._key("http://10.10.10.10:8012//api"),
                         "http://10.10.10.10:8012/api")

    def test_collapses_multiple_internal_double_slashes(self):
        self.assertEqual(auth._key("http://kitsu///api//v1"), "http://kitsu/api/v1")

    def test_strips_a_trailing_slash(self):
        self.assertEqual(auth._key("http://kitsu/api/"), "http://kitsu/api")

    def test_idempotent_on_an_already_clean_host(self):
        self.assertEqual(auth._key("http://kitsu/api"), "http://kitsu/api")


class TestSessionKeyConsistency(_IsolatedStateDir):
    """Regression coverage for a real bug found 2026-09-15 from a studio
    crash log: a session got cached under a malformed double-slash host
    (`http://host//api`, from a hand-typed login-dialog value) while every
    real lookup used the correctly-formed single-slash host that
    `PipelineConfig.load()` produces. A plain `rstrip("/")` never fixes an
    *internal* double slash, so the cached session was permanently
    unreachable and every `connect()` raised `NeedsLogin` as if the login had
    never happened, even though `login()` itself never failed."""

    @patch("gazu.set_host")
    def test_login_with_a_malformed_host_is_found_by_a_clean_lookup(self, _set_host):
        with patch("gazu.log_in", return_value={"access_token": "AT", "refresh_token": "RT"}):
            auth.login("http://10.10.10.10:8012//api", "a@x.com", "pw")
        self.assertIsNotNone(auth.cached_session("http://10.10.10.10:8012/api"))

    @patch("gazu.set_host")
    def test_login_with_a_clean_host_is_found_by_a_malformed_lookup(self, _set_host):
        with patch("gazu.log_in", return_value={"access_token": "AT", "refresh_token": "RT"}):
            auth.login("http://10.10.10.10:8012/api", "a@x.com", "pw")
        self.assertIsNotNone(auth.cached_session("http://10.10.10.10:8012//api"))

    def test_forget_removes_it_regardless_of_slash_spelling(self):
        auth.store_session("http://kitsu//api", {"access_token": "AT", "refresh_token": "RT"})
        auth.forget("http://kitsu/api")
        self.assertIsNone(auth.cached_session("http://kitsu//api"))


def _fake_keyring(store: dict) -> types.ModuleType:
    mod = types.ModuleType("keyring")
    mod.get_password = lambda service, name: store.get((service, name))
    mod.set_password = lambda service, name, value: store.__setitem__((service, name), value)
    return mod


class TestFileFallbackAlwaysWritten(_IsolatedStateDir):
    """Regression: a login in an environment WITH `keyring` (the studio's own
    venv) used to write ONLY to keyring and skip the file entirely -- so an
    embedded DCC interpreter that can never have `keyring` (Nuke, xStudio;
    see requirements-dcc.txt: pure-Python only) could never find a session
    that plainly existed, and every menu command reported "not logged in"
    despite a perfectly valid cached login sitting in Credential Manager."""

    def test_store_session_writes_the_file_even_when_keyring_succeeds(self):
        keyring_store: dict = {}
        with patch.dict(sys.modules, {"keyring": _fake_keyring(keyring_store)}):
            auth.store_session("http://kitsu/api", {"access_token": "AT", "refresh_token": "RT"})
        self.assertTrue(keyring_store)                     # keyring did get it...
        # ...but simulate an interpreter that CANNOT import keyring at all
        # (like Nuke's) reading right after -- it must still find the session
        with patch.dict(sys.modules, {"keyring": None}):
            sess = auth.cached_session("http://kitsu/api")
        self.assertIsNotNone(sess)
        self.assertEqual(sess["access_token"], "AT")

    def test_load_falls_back_to_the_file_when_keyring_has_no_entry(self):
        auth.store_session("http://kitsu/api", {"access_token": "AT", "refresh_token": "RT"})
        empty_keyring_store: dict = {}          # importable, but nothing stored under it
        with patch.dict(sys.modules, {"keyring": _fake_keyring(empty_keyring_store)}):
            self.assertIsNotNone(auth.cached_session("http://kitsu/api"))


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
