"""square_core.kitsu -- the ONE package that imports gazu.

Services call this; tools never do. It speaks Kitsu's own nouns (task, shot,
preview, output-type) and returns `square_core.model` objects or plain dicts --
no neutral "backend protocol", no two-way mapper for its own sake. The
"swap trackers someday" insurance is simply that all gazu use is here.

- `api.KitsuApi`      -- the facade every service calls
- `api.connect` / `auth` -- per-user login, JWT cache (non-interactive)
- `offline.OfflineApi` -- no-op stand-in: tag + resolve + copy to NAS w/o Kitsu
"""

from __future__ import annotations

from .api import KitsuApi, connect
from .offline import OfflineApi
from . import auth

__all__ = ["KitsuApi", "connect", "OfflineApi", "auth"]
