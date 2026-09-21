"""Per-user Kitsu auth with a cached JWT.

Non-interactive: `login(host, email, password)` authenticates and caches the
tokens; `cached_session(host)` returns them if still usable, else None. The
*tool* is what prompts for credentials -- `PipelineContext.connect()` raises
`NeedsLogin` and the tool calls `login()` then retries.

Cache: always a 0600 JSON file under `$SQUARE_STATE_DIR` or `~/.square/`, PLUS
the OS keyring when `keyring` is importable (preferred on read, when present).
The file is never skipped just because keyring succeeded -- an embedded DCC
interpreter (Nuke, xStudio) deliberately never gets `keyring` (see
requirements-dcc.txt: pure-Python only), so it can only ever read a session
through the file; a desktop tool's login, wherever it happens, has to leave
one there too, not just in Credential Manager. Farm nodes set
`SQUARE_KITSU_TOKEN` directly and never touch either store.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

_SERVICE = "square-pipeline"


def _state_dir() -> Path:
    root = os.environ.get("SQUARE_STATE_DIR") or (Path.home() / ".square")
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file() -> Path:
    return _state_dir() / "kitsu_session.json"


def _key(host: str) -> str:
    """Normalize a host string for use as a session-cache key. Store and
    lookup both go through this, so it must be idempotent and collision-free
    for any two spellings of "the same host" -- a stray trailing slash, or a
    doubled slash from pasting a host that already ends in "/" next to a
    path that starts with "/" (this exact bug: a session got cached under
    "http://host//api" while every real lookup asks for "http://host/api" --
    a plain `rstrip("/")` never touches an *internal* double slash, so the
    cached session was silently unreachable and every login looked like it
    had never happened)."""
    host = (host or "").strip()
    if "://" in host:
        scheme, rest = host.split("://", 1)
        while "//" in rest:
            rest = rest.replace("//", "/")
        host = f"{scheme}://{rest}"
    return host.rstrip("/")


# --------------------------------------------------------------------------


def _load_all() -> dict:
    # env override wins -- used by render farm / CI
    env = os.environ.get("SQUARE_KITSU_TOKEN")
    if env:
        return {"__env__": {"access_token": env, "refresh_token": ""}}
    # file first (the portable baseline every interpreter can read -- no
    # embedded-DCC interpreter, e.g. Nuke's or xStudio's, ships `keyring`;
    # see requirements-dcc.txt), then keyring on top where it's available and
    # actually has an entry -- it's the preferred store when both exist.
    out: dict = {}
    try:
        out.update(json.loads(_file().read_text(encoding="utf-8")))
    except Exception:
        pass
    try:
        import keyring  # type: ignore

        blob = keyring.get_password(_SERVICE, "sessions")
        if blob:
            out.update(json.loads(blob))
    except Exception:
        pass
    return out


def _save_all(data: dict) -> None:
    """Always write the file, keyring or not: a normal desktop tool's login
    (in an environment with `keyring`, e.g. the studio's own venv) must still
    leave something an embedded DCC interpreter WITHOUT keyring can read --
    that gap is exactly why Nuke reported "not logged in" despite a perfectly
    valid cached session sitting in Windows Credential Manager, unreachable
    from a Python that can't `import keyring` at all."""
    payload = json.dumps({k: v for k, v in data.items() if k != "__env__"})
    f = _file()
    f.write_text(payload, encoding="utf-8")
    try:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    try:
        import keyring  # type: ignore

        keyring.set_password(_SERVICE, "sessions", payload)
    except Exception:
        pass


# --------------------------------------------------------------------------


def cached_session(host: str = "") -> dict | None:
    """The stored `{access_token, refresh_token}` for `host`, or None."""
    data = _load_all()
    if "__env__" in data:
        return data["__env__"]
    return data.get(_key(host))


def store_session(host: str, tokens: dict) -> None:
    data = _load_all()
    data[_key(host)] = {
        "access_token": tokens.get("access_token", ""),
        "refresh_token": tokens.get("refresh_token", ""),
    }
    _save_all(data)


def forget(host: str = "") -> None:
    data = _load_all()
    data.pop(_key(host), None)
    _save_all(data)


def _tokens_from_client() -> dict:
    """Best-effort fallback reading the current tokens straight off gazu's
    client -- it exposes them two different ways depending on how they got
    there: `KitsuClient.refresh_access_token()` sets `.access_token` /
    `.refresh_token` as plain attributes, but `gazu.log_in()` (via
    `client.set_tokens()`) only ever sets a `.tokens` dict, never those
    attributes. Check both, attribute first."""
    import gazu

    dc = getattr(getattr(gazu, "client", None), "default_client", None)
    tok = getattr(dc, "tokens", None) or {}
    return {
        "access_token": getattr(dc, "access_token", "") or tok.get("access_token") or "",
        "refresh_token": getattr(dc, "refresh_token", "") or tok.get("refresh_token") or "",
    }


def login(host: str, email: str, password: str) -> dict:
    """Authenticate against Kitsu and cache the tokens. Returns the token dict."""
    import gazu

    gazu.set_host(host)
    # gazu.log_in() raises on bad creds and otherwise returns the tokens
    # directly -- access_token/refresh_token at the top level of the dict,
    # NOT nested under a "tokens" key (that was the actual bug here: this
    # used to look for `result["tokens"]`, which never exists, silently
    # falling through to a client-attribute fallback that was ALSO wrong for
    # the login path -- see `_tokens_from_client`'s docstring. Net effect: an
    # empty token got cached after a successful login, and the very next
    # connect() attempt raised NeedsLogin again as if nothing had happened)
    result = gazu.log_in(email, password)
    tokens = {"access_token": (result or {}).get("access_token", ""),
             "refresh_token": (result or {}).get("refresh_token", "")}
    if not tokens["access_token"]:
        tokens = _tokens_from_client()
    store_session(host, tokens)
    return tokens


def refresh(host: str) -> dict:
    """Exchange the cached refresh token for a fresh access token and re-store.
    Raises if there is no usable refresh token."""
    import gazu

    gazu.set_host(host)
    sess = cached_session(host) or {}
    try:
        gazu.set_token(dict(sess))
    except Exception:
        pass
    gazu.refresh_access_token()             # raises on an expired / missing refresh token
    tokens = _tokens_from_client()
    store_session(host, tokens)
    return tokens
