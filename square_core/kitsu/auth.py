"""Per-user Kitsu auth with a cached JWT.

Non-interactive: `login(host, email, password)` authenticates and caches the
tokens; `cached_session(host)` returns them if still usable, else None. The
*tool* is what prompts for credentials -- `PipelineContext.connect()` raises
`NeedsLogin` and the tool calls `login()` then retries.

Cache: OS keyring if `keyring` is importable, else a 0600 JSON file under
`$SQUARE_STATE_DIR` or `~/.square/`. Farm nodes set `SQUARE_KITSU_TOKEN`
directly and never touch this.
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
    return host.rstrip("/")


# --------------------------------------------------------------------------


def _load_all() -> dict:
    # env override wins -- used by render farm / CI
    env = os.environ.get("SQUARE_KITSU_TOKEN")
    out: dict = {}
    if env:
        out["__env__"] = {"access_token": env, "refresh_token": ""}
    try:
        import keyring  # type: ignore

        blob = keyring.get_password(_SERVICE, "sessions")
        if blob:
            out.update(json.loads(blob))
        return out
    except Exception:
        pass
    try:
        out.update(json.loads(_file().read_text(encoding="utf-8")))
    except Exception:
        pass
    return out


def _save_all(data: dict) -> None:
    payload = json.dumps({k: v for k, v in data.items() if k != "__env__"})
    try:
        import keyring  # type: ignore

        keyring.set_password(_SERVICE, "sessions", payload)
        return
    except Exception:
        pass
    f = _file()
    f.write_text(payload, encoding="utf-8")
    try:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
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
    import gazu

    dc = getattr(getattr(gazu, "client", None), "default_client", None)
    return {
        "access_token": getattr(dc, "access_token", "") or "",
        "refresh_token": getattr(dc, "refresh_token", "") or "",
    }


def login(host: str, email: str, password: str) -> dict:
    """Authenticate against Kitsu and cache the tokens. Returns the token dict."""
    import gazu

    gazu.set_host(host)
    result = gazu.log_in(email, password)  # raises on bad creds
    tokens = (result or {}).get("tokens") or _tokens_from_client()
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
