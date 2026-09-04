"""PathResolver -- ProjectConfig + PathContext -> path string.

Pure. No Kitsu, no filesystem. Version numbers, slot inspection, folder
creation and the copy all belong elsewhere (see `docs/config_and_paths.md` §9).

The rendering contract (§7):
- `{token}` / `{token:spec}` substitution; aliases mapped to canonical
- case of substituted values is PRESERVED, then slugified
- `version` / `version_label` / `minor` / `frame` are special
- `frame` is None  -> the `.{frame}` / `_{frame}` segment is removed
- a required token that is empty raises `PathError`; an optional one collapses
- an optional block-level `case` of "upper" / "lower" is applied last
"""

from __future__ import annotations

import re
from typing import Iterable

from square_core.model import PathContext

_TOKEN_RE = re.compile(r"\{([a-z_]+)(?::([^}]+))?\}")
_ROOT_REF_RE = re.compile(r"\{([a-z_]+)_root\}")

_ALIASES = {
    "project_code": "project",
    "seq": "sequence",
    "sequence_code": "sequence",
    "shot_code": "shot",
    "output_type": "media_type",
    "type": "media_type",
    "media_name": "name",
    "task_type": "task",
    "dept": "department",
    "dcc": "software",
    "repr": "representation",
    "res": "resolution",
    "ep": "episode",
}

# specials handled outside plain field lookup
_SPECIAL = {"version", "version_label", "minor", "frame"}

# trusted path prefixes -- substituted verbatim, never slugified (a drive
# letter's ':' and the '/' separators must survive)
_RAW = {"nas_root"}

# empty is fine for these; the surrounding path segment collapses
_OPTIONAL = {
    "episode",
    "department",
    "software",
    "representation",
    "resolution",
    "fps",
    "minor",
    "version_label",
    "package",
    "date",
    "user",
    "site",
}

_KNOWN_TOKENS = set(PathContext.field_names()) | set(_ALIASES) | _SPECIAL


class PathError(ValueError):
    """A template needs a value the PathContext did not supply, or a template
    is malformed."""


# --------------------------------------------------------------------------
# slugify
# --------------------------------------------------------------------------

def slugify(value: str, rules: dict | None = None) -> str:
    """Make a token value path-safe WITHOUT changing its case."""
    rules = rules or {}
    spaces_to = rules.get("spaces_to", "_")
    strip_chars = rules.get("strip", '<>:"/\\|?*')
    collapse = rules.get("collapse", "_")
    s = str(value)
    s = s.replace(" ", spaces_to)
    s = "".join(c for c in s if c not in strip_chars and ord(c) >= 32)
    if collapse:
        s = re.sub(re.escape(collapse) + r"{2,}", collapse, s)
    return s


# --------------------------------------------------------------------------
# token rendering
# --------------------------------------------------------------------------

def render_tokens(
    template: str,
    ctx: PathContext,
    *,
    version_pad: int = 3,
    frame_pad: int = 4,
    slug_rules: dict | None = None,
    optional: Iterable[str] = (),
) -> str:
    """Substitute every `{token}` in `template` from `ctx`. Raises `PathError`
    for an unknown token or a required token with no value."""
    optional_set = _OPTIONAL | set(optional)

    # frame: drop the segment entirely when there is no frame number
    if ctx.frame is None:
        template = re.sub(r"[._]?\{frame(?::[^}]*)?\}", "", template)

    def repl(m: re.Match) -> str:
        raw_name, spec = m.group(1), m.group(2)
        name = _ALIASES.get(raw_name, raw_name)

        if name == "version":
            fmt = spec or f"0{version_pad}d"
            return format(int(ctx.version), fmt)
        if name == "minor":
            return f"{ctx.minor:02d}" if ctx.minor else ""
        if name == "version_label":
            lbl = f"v{ctx.version:0{version_pad}d}"
            if ctx.minor:
                lbl += f".{ctx.minor:02d}"
            return lbl
        if name == "frame":
            fmt = spec or f"0{frame_pad}d"
            return format(int(ctx.frame), fmt)

        if name not in _KNOWN_TOKENS:
            raise PathError(f"unknown token {{{raw_name}}} in template {template!r}")

        value = getattr(ctx, name, "")
        if value in ("", None):
            if name in optional_set:
                return ""
            raise PathError(
                f"template {template!r} needs {{{raw_name}}} but the context has no value"
            )
        if name in _RAW:
            return str(value).replace("\\", "/")
        return slugify(value, slug_rules)

    out = _TOKEN_RE.sub(repl, template)
    return _cleanup(out)


def _cleanup(path: str) -> str:
    path = path.replace("\\", "/")
    path = re.sub(r"/{2,}", "/", path)
    # tidy separators an emptied optional token left behind
    parts = path.split("/")
    cleaned = []
    for i, seg in enumerate(parts):
        seg = re.sub(r"_{2,}", "_", seg)
        seg = seg.replace("_.", ".").replace("._", ".")
        seg = re.sub(r"^[._-]+", "", seg)
        seg = re.sub(r"[._-]+$", "", seg) if i == len(parts) - 1 and "." not in seg else seg.rstrip("_-")
        if seg or i == 0:  # keep a leading "" so "X:/…" or "/…" survives
            cleaned.append(seg)
    result = "/".join(cleaned)
    return result.rstrip("/")


# --------------------------------------------------------------------------
# PathResolver
# --------------------------------------------------------------------------

class PathResolver:
    def __init__(self, config):
        self.config = config
        self._roots = _resolve_roots(config.roots)

    # ---- roots --------------------------------------------------------

    def project_root(self, ctx: PathContext) -> str:
        return self._render_root("project", ctx)

    def shot_dir(self, ctx: PathContext) -> str:
        return self._render_root("shot", ctx, required=("sequence", "shot"))

    def asset_dir(self, ctx: PathContext) -> str:
        return self._render_root("asset", ctx, required=("asset", "asset_type"))

    # ---- media -- ONE path for ingest / render / workfile / cache ----

    def media_entry(self, media_type: str) -> dict:
        return self.config.media_type(media_type)

    def _media_required(self, entry: dict, ctx: PathContext) -> tuple:
        base = entry.get("base")
        if base == "asset":
            return ("asset", "asset_type")
        return ("sequence", "shot")

    def media_dir(self, media_type: str, ctx: PathContext) -> str:
        entry = self.config.media_type(media_type)
        block = {k: v for k, v in entry.items() if k != "file"}
        ctx = _with_media_type(ctx, media_type)
        return self._render_block(f"media[{media_type}]", block, ctx,
                                  required=self._media_required(entry, ctx))

    def media_file(self, media_type: str, ctx: PathContext) -> str:
        entry = self.config.media_type(media_type)
        return self._render_file(entry, _with_media_type(ctx, media_type))

    def media_path(self, media_type: str, ctx: PathContext) -> str:
        entry = self.config.media_type(media_type)
        ctx = _with_media_type(ctx, media_type)
        return self._render_block(f"media[{media_type}]", entry, ctx,
                                  required=self._media_required(entry, ctx))

    def media_sequence(self, media_type: str, ctx: PathContext, frames) -> list:
        d = self.media_dir(media_type, ctx)
        return [f"{d}/{self.media_file(media_type, ctx.with_(frame=f))}" for f in frames]

    # ---- delivery ------------------------------------------------

    def delivery_dir(self, ctx: PathContext) -> str:
        block = dict(self.config.delivery_template(ctx.client))
        block.pop("file", None)
        return self._render_block("delivery", block, ctx, required=("client",))

    def delivery_file(self, ctx: PathContext) -> str:
        return self._render_file(self.config.delivery_template(ctx.client), ctx)

    def delivery_preset(self, client: str = "") -> dict:
        return self.config.delivery_template(client)

    # ---- skeleton ----------------------------------------------

    def shot_folders(self, ctx: PathContext) -> list:
        base = self.shot_dir(ctx)
        return [f"{base}/{sub}" for sub in self.config.shot_folder_structure]

    def asset_folders(self, ctx: PathContext) -> list:
        base = self.asset_dir(ctx)
        return [f"{base}/{sub}" for sub in self.config.asset_folder_structure]

    # ---- internals --------------------------------------------

    def _render_kw(self, pad_override: dict | None = None) -> dict:
        po = pad_override or {}
        return dict(
            version_pad=int(po.get("version_pad", self.config.version_pad)),
            frame_pad=int(po.get("frame_pad", self.config.frame_pad)),
            slug_rules=self.config.slugify,
        )

    def _render_root(self, name: str, ctx: PathContext, required=()) -> str:
        if name not in self._roots:
            raise PathError(f"no root named {name!r}")
        return render_tokens(self._roots[name], ctx, **self._render_kw(),
                             optional=set(_ROOT_OPTIONAL) - set(required))

    def _render_block(self, kind: str, block: dict, ctx: PathContext, required=()) -> str:
        base_name = block.get("base")
        if base_name and base_name not in self._roots:
            raise PathError(f"{kind}.base = {base_name!r} names no root")
        parts = []
        if base_name:
            parts.append(self._roots[base_name])
        if block.get("dir"):
            parts.append(block["dir"])
        if block.get("file"):
            parts.append(block["file"])
        combined = "/".join(p.strip("/") if i else p for i, p in enumerate(parts))
        optional = (set(_ROOT_OPTIONAL) | {"name"}) - set(required)
        out = render_tokens(combined, ctx, **self._render_kw(pad_override=block), optional=optional)
        return _apply_case(out, block.get("case", "preserve"))

    def _render_file(self, block: dict, ctx: PathContext) -> str:
        """Just the filename part of a block, with the block's `case` applied."""
        if not block.get("file"):
            return ""
        out = render_tokens(block["file"], ctx, **self._render_kw(pad_override=block),
                            optional=("name",))
        return _apply_case(out, block.get("case", "preserve"))

    # ---- validation ------------------------------------------

    def validate(self) -> list:
        errs: list = []
        probe = _probe_ctx()

        try:
            self._roots  # already resolved in __init__; re-run to surface the error text
        except PathError as e:  # pragma: no cover - __init__ would have raised
            return [str(e)]

        blocks = []
        cfg = self.config
        for mt in [None, *cfg.media_type_names()]:
            try:
                blocks.append((f"media[{mt or '_default'}]", cfg.media_type(mt or "_probe_")))
            except Exception as e:
                errs.append(str(e))
        presets = cfg.delivery_presets
        if presets:                                  # delivery config is optional
            for client in [""] + [c for c in presets if c not in ("_default", "default")]:
                try:
                    blocks.append((f"delivery[{client or '_default'}]",
                                   cfg.delivery_template(client)))
                except Exception as e:
                    errs.append(str(e))

        for label, block in blocks:
            if "{frame" in (block.get("dir") or ""):
                errs.append(f"{label}.dir must not contain {{frame}}")

            combined = "/".join(p for p in (block.get("dir"), block.get("file")) if p)
            if not combined:
                continue
            kw = self._render_kw(pad_override=block)
            try:
                v1 = render_tokens(combined, probe, **kw, optional=("name",))
                v2 = render_tokens(combined, probe.with_(version=probe.version + 1),
                                   **kw, optional=("name",))
            except PathError as e:
                errs.append(f"{label}: {e}")
                continue
            if v1 == v2:
                errs.append(
                    f"{label} does not vary by version -- v2 would overwrite v1"
                    " (add {version} / {version_label} to its dir or file)"
                )

        # check a {X_root} that looks like a root ref (not {nas_root}) actually exists
        root_names = set(cfg.roots)
        for name, tmpl in cfg.roots.items():
            for ref in _ROOT_REF_RE.findall(tmpl):
                if ref != "nas" and ref not in root_names:
                    errs.append(f"roots.{name} references {{{ref}_root}} which does not exist")

        return errs


# fields that are allowed to be empty in a root/block unless explicitly required
_ROOT_OPTIONAL = _OPTIONAL | {"episode"}


def _root_refs(tmpl: str, root_names: set) -> list:
    """`{X_root}` occurrences where X is an actual root name -- so `{nas_root}`
    (a PathContext token) is not mistaken for a root reference."""
    return [r for r in _ROOT_REF_RE.findall(tmpl) if r in root_names]


def _resolve_roots(roots: dict) -> dict:
    root_names = set(roots or {})
    resolved: dict = {}
    pending = dict(roots or {})
    for _ in range(len(pending) + 2):
        if not pending:
            break
        progressed = False
        for name, tmpl in list(pending.items()):
            refs = _root_refs(tmpl, root_names)
            if all(r in resolved for r in refs):
                v = tmpl
                for r in refs:
                    v = v.replace(f"{{{r}_root}}", resolved[r])
                resolved[name] = v
                del pending[name]
                progressed = True
        if not progressed:
            raise PathError(f"cyclic or unresolvable root references: {sorted(pending)}")
    return resolved


def _apply_case(s: str, case: str) -> str:
    if case == "upper":
        return s.upper()
    if case == "lower":
        return s.lower()
    return s


def _with_media_type(ctx: PathContext, media_type: str) -> PathContext:
    return ctx if ctx.media_type == media_type else ctx.with_(media_type=media_type)


def _probe_ctx() -> PathContext:
    return PathContext(
        nas_root="X:/projects",
        project="ABC",
        episode="EP01",
        sequence="SQ010",
        shot="SH0100",
        asset="hero",
        asset_type="char",
        task="comp",
        department="2d",
        software="nuke",
        media_type="Plate",
        name="bg",
        version=1,
        minor=0,
        representation="exr",
        ext="exr",
        resolution="3840x2160",
        fps="24",
        frame=1001,
        client="ACME",
        package="20260101",
        date="20260101",
        user="artist",
        site="lon",
    )
