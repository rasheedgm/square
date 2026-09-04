"""Headless driver for the config editor -- same `ConfigStore`, no Qt.

    python -m tools.config_editor --cli list  --scope studio
    python -m tools.config_editor --cli list  --scope project --project ABC
    python -m tools.config_editor --cli get   --scope project --project ABC fps
    python -m tools.config_editor --cli set   --scope project --project ABC fps 25
    python -m tools.config_editor --cli reset --project ABC version_pad
    python -m tools.config_editor --cli diff  --scope project --project ABC
"""

from __future__ import annotations

import argparse
import json
import sys

from square_core.config import ConfigError
from square_core.context import PipelineContext
from square_core.errors import NeedsLogin

from .core import ConfigStore, NotAuthorized


def _store(args) -> ConfigStore:
    try:
        ctx = PipelineContext.connect(offline=args.offline)
    except NeedsLogin:
        print("not logged in -- run the ingest tool / editor GUI once to sign in, "
              "or set SQUARE_KITSU_TOKEN", file=sys.stderr)
        raise SystemExit(2)
    store = ConfigStore(ctx.config, user=ctx.user)
    if getattr(args, "project", None):
        pctx = ctx.project(args.project)
        store.open_project(pctx.project.root_path, pctx.project.code)
    return store


def _parse_value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _fmt(fv) -> str:
    tag = f"  [{fv.source}]"
    if fv.overridden:
        tag += " *override*"
    val = "********" if fv.secret and fv.value else json.dumps(fv.value)
    req = " (required)" if fv.required else ""
    return f"{fv.key:38} {val:<30}{tag}{req}"


def cmd_list(args):
    store = _store(args)
    for fv in store.fields(args.scope):
        print(_fmt(fv))


def cmd_get(args):
    store = _store(args)
    fv = store.field(args.scope, args.key)
    print(json.dumps(fv.value))


def cmd_set(args):
    store = _store(args)
    try:
        store.set(args.scope, args.key, _parse_value(args.value))
        path, bak = (store.save_studio() if args.scope == "studio"
                     else store.save_project())
    except (ValueError, ConfigError, NotAuthorized) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)
    print(f"set {args.key} -> wrote {path}" + (f" (backup {bak.name})" if bak else ""))


def cmd_reset(args):
    args.scope = "project"
    store = _store(args)
    if not store.reset(args.key):
        print(f"{args.key}: no project override to reset")
        return
    try:
        path, bak = store.save_project()
    except (ConfigError, NotAuthorized) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)
    print(f"reset {args.key} -> wrote {path}" + (f" (backup {bak.name})" if bak else ""))


def cmd_diff(args):
    store = _store(args)
    pending = store.pending(args.scope)
    if not pending:
        print("no pending changes")
        return
    for key, (old, new) in sorted(pending.items()):
        print(f"{key}\n  - {json.dumps(old)}\n  + {json.dumps(new)}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="config_editor --cli")
    p.add_argument("--offline", action="store_true",
                   help="don't contact Kitsu (read-only: no admin role, no save)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, need_key=False, need_value=False):
        sp.add_argument("--scope", choices=("studio", "project"), default="project")
        sp.add_argument("--project", help="project code (project scope)")
        if need_key:
            sp.add_argument("key")
        if need_value:
            sp.add_argument("value")

    lp = sub.add_parser("list"); common(lp); lp.set_defaults(func=cmd_list)
    gp = sub.add_parser("get"); common(gp, need_key=True); gp.set_defaults(func=cmd_get)
    sp = sub.add_parser("set"); common(sp, need_key=True, need_value=True); sp.set_defaults(func=cmd_set)
    rp = sub.add_parser("reset"); rp.add_argument("--project"); rp.add_argument("key")
    rp.set_defaults(func=cmd_reset)
    dp = sub.add_parser("diff"); common(dp); dp.set_defaults(func=cmd_diff)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
