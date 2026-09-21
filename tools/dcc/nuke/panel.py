"""The Square panels for Nuke and the menu-command entry points.

Pipeline work goes through `ops.NukeOps`; this module talks to Nuke. `nuke` /
`nukescripts` are imported lazily so the file imports fine outside Nuke.
"""

from __future__ import annotations

import traceback

from square_core.errors import NeedsLogin

from . import gizmos
from .context import from_env, to_env
from .ops import NukeOps, OpsError

_ops: NukeOps | None = None


def _nuke():
    import nuke
    return nuke


def get_ops() -> NukeOps:
    global _ops
    was_unset = _ops is None
    if _ops is None:
        try:
            _ops = NukeOps()
        except NeedsLogin:
            raise OpsError(
                "Not signed in to Kitsu — Square -> Sign In…, or run any other Square tool.")
    if was_unset:
        # the cached token turned out to still be valid -- the menu title
        # (built before we knew that) still just says "Square"; catch it up
        # now that we actually have a name to show.
        _rebuild_menu()
    return _ops


def _msg(text: str) -> None:
    try:
        _nuke().message(f"Square\n\n{text}")
    except Exception:
        print(f"Square: {text}")


def _pipeline_host() -> str:
    from square_core.config import PipelineConfig
    return PipelineConfig.load().kitsu_host


def _user_label(user) -> str:
    return user.name or user.email or user.id


def menu_title() -> str:
    """The Square menu's own top-level label -- never makes a live Kitsu
    call (Nuke startup must not block on the network): if `ops` is already
    resolved in this session, "Square — <real name>"; otherwise a cheap
    LOCAL check of whether some cached token exists at all, with no way to
    know whose it is without asking the server; otherwise plain "Square"."""
    if _ops is not None:
        return f"Square — {_user_label(_ops._ctx.user)}"
    from square_core.kitsu import auth
    return "Square — signed in" if auth.cached_session(_pipeline_host()) else "Square"


def is_signed_in() -> bool:
    """Same no-live-call rule as menu_title() -- drives whether the Square
    menu offers Sign In… or Sign Out (never both at once)."""
    if _ops is not None:
        return True
    from square_core.kitsu import auth
    return bool(auth.cached_session(_pipeline_host()))


def _rebuild_menu() -> None:
    try:
        from tools.dcc.nuke import menu
        menu.build()
    except Exception:
        pass          # outside Nuke (e.g. unit tests), or menu.py hasn't run yet


def _guard(fn):
    def wrapped(*a, **k):
        try:
            return fn(*a, **k)
        except OpsError as e:
            _msg(str(e))
        except Exception as e:
            _msg(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
    return wrapped


@_guard
def sign_in():
    from tools.qt_compat import exec_dialog
    from tools.widgets.login_dialog import LoginDialog
    global _ops
    if not exec_dialog(LoginDialog(_pipeline_host())):
        return
    _ops = None                       # drop any stale session, force a fresh one
    try:
        ops = get_ops()               # reconnect now (also rebuilds the menu)
    except OpsError as e:
        _msg(str(e))
        return
    _msg(f"Signed in as {_user_label(ops._ctx.user)}")


@_guard
def sign_out():
    from square_core.kitsu import auth
    global _ops
    auth.forget(_pipeline_host())
    _ops = None
    _rebuild_menu()
    _msg("Signed out.")


# ---------------------------------------------------------------------------
# shared: a project -> episode -> sequence -> shot -> task knob cascade
# ---------------------------------------------------------------------------

class _Picker:
    """Builds and wires the 5 cascade knobs onto a PythonPanel."""

    def __init__(self, panel, nuke, ops: NukeOps, *, with_name=False):
        self.panel, self.nuke, self.ops = panel, nuke, ops
        seed = from_env()
        self.k_project = nuke.Enumeration_Knob("project", "Project", ops.projects() or [""])
        self.k_episode = nuke.Enumeration_Knob("episode", "Episode", [""])
        self.k_sequence = nuke.Enumeration_Knob("sequence", "Sequence", [""])
        self.k_shot = nuke.Enumeration_Knob("shot", "Shot", [""])
        self.k_task = nuke.Enumeration_Knob("task", "Task", [""])
        self.knobs = [self.k_project, self.k_episode, self.k_sequence, self.k_shot, self.k_task]
        self.k_name = None
        if with_name:
            self.k_name = nuke.Enumeration_Knob("name", "Workfile name", ["main"])
        for k in self.knobs:
            panel.addKnob(k)
        if self.k_name:
            panel.addKnob(self.k_name)
        if seed.project in (ops.projects() or []):
            self.k_project.setValue(seed.project)
        self.reload("project", seed=seed)

    def target(self):
        from .context import Target
        return Target(self.k_project.value(), self.k_episode.value(),
                      self.k_sequence.value(), self.k_shot.value(), self.k_task.value())

    def handles(self, knob) -> bool:
        return knob in self.knobs

    def reload(self, changed: str, seed=None):
        proj = self.k_project.value()
        order = ["project", "episode", "sequence", "shot", "task"]
        below = order[order.index(changed) + 1:]
        if "episode" in below:
            eps = self.ops.episodes(proj) if (proj and self.ops.is_episodic(proj)) else []
            self.k_episode.setEnabled(bool(eps))
            self._set(self.k_episode, eps or [""], getattr(seed, "episode", ""))
        if "sequence" in below:
            seqs = self.ops.sequences(proj, self.k_episode.value()) if proj else []
            self._set(self.k_sequence, seqs or [""], getattr(seed, "sequence", ""))
        if "shot" in below:
            shots = self.ops.shots(proj, self.k_sequence.value()) if proj else []
            self._set(self.k_shot, shots or [""], getattr(seed, "shot", ""))
        if "task" in below:
            tt = []
            if proj and self.k_sequence.value() and self.k_shot.value():
                tt = self.ops.task_types(proj, self.k_sequence.value(), self.k_shot.value())
            want = getattr(seed, "task_type", "") or (
                self.ops.default_task_for(proj, self.k_sequence.value(), self.k_shot.value())
                if tt else "")
            self._set(self.k_task, tt or [""], want)

    @staticmethod
    def _set(knob, values, selected=""):
        knob.setValues([str(v) for v in values])
        if selected and selected in values:
            knob.setValue(str(selected))


# ---------------------------------------------------------------------------
# Open Version
# ---------------------------------------------------------------------------

def open_version() -> None:
    import nukescripts
    _OpenVersionPanel(_nuke(), nukescripts).showModalDialog()


class _OpenVersionPanel:
    def __init__(self, nuke, nukescripts):
        self.nuke = nuke
        self.p = nukescripts.PythonPanel("Square — Open Version", "com.square.open_version")
        self.picker = _Picker(self.p, nuke, get_ops(), with_name=True)
        self.k_version = nuke.Enumeration_Knob("version", "Version", [""])
        self.k_info = nuke.Text_Knob("info", "")
        for k in (self.k_version, self.k_info):
            self.p.addKnob(k)
        self.p.knobChanged = self._changed
        self._reload_versions()

    def showModalDialog(self):
        if not self.p.showModalDialog():
            return
        self._open()

    def _changed(self, knob):
        if self.picker.handles(knob) or knob is self.picker.k_name:
            self.picker.reload(knob.name() if self.picker.handles(knob) else "task")
            self._reload_versions()

    def _reload_versions(self):
        self._versions = []
        try:
            majors = get_ops().workfile_versions(self.picker.target(),
                                                 name=(self.picker.k_name.value() or "main"))
        except OpsError as e:
            self.k_version.setValues([""])
            self.k_info.setValue(str(e))
            return
        labels = []
        for mv in reversed(majors):
            for m in mv.minors:
                labels.append(f"v{mv.major:03d}.{m.minor:03d}")
                self._versions.append((mv, m))
            if not mv.minors:
                labels.append(f"v{mv.major:03d}  (offline)")
                self._versions.append((mv, None))
        self.k_version.setValues(labels or ["(none)"])
        self.k_info.setValue(f"{len(self._versions)} version(s)")

    def _open(self):
        idx = self.k_version.value()
        try:
            mv, minor = self._versions[
                [f"v{m.major:03d}.{f.minor:03d}" if f else f"v{m.major:03d}  (offline)"
                 for m, f in self._versions].index(idx)]
        except (ValueError, IndexError):
            return
        if minor is None or not minor.online:
            _msg("That version isn't on disk.")
            return
        nuke = self.nuke
        if nuke.root().modified() or nuke.allNodes():
            choice = nuke.ask("This session has unsaved nodes.\n\n"
                              "OK = clear and open here, Cancel = open in a new Nuke.")
            if choice:
                nuke.scriptClear()
                nuke.scriptOpen(minor.path.replace("\\", "/"))
            else:
                _open_in_new_nuke(minor.path)
        else:
            nuke.scriptOpen(minor.path.replace("\\", "/"))
        to_env(self.picker.target())


def _open_in_new_nuke(path: str) -> None:
    import subprocess

    from tools.workfile_manager.core import WorkfileHub

    hub = WorkfileHub(get_ops()._ctx)
    cmd = hub.launcher_for(_target_project(), "nuke")
    if not cmd:
        _msg("No Nuke launcher configured (tools.workfile_manager.launchers.nuke).")
        return
    import shlex
    parts = [path if p == "{file}" else p for p in shlex.split(cmd, posix=False)]
    if "{file}" not in cmd:
        parts.append(path)
    subprocess.Popen(parts)


def _target_project() -> str:
    return from_env().project


# ---------------------------------------------------------------------------
# Save Version
# ---------------------------------------------------------------------------

def save_version() -> None:
    import nukescripts
    _SaveVersionPanel(_nuke(), nukescripts).run()


class _SaveVersionPanel:
    def __init__(self, nuke, nukescripts):
        self.nuke = nuke
        self.p = nukescripts.PythonPanel("Square — Save Version", "com.square.save_version")
        self.picker = _Picker(self.p, nuke, get_ops(), with_name=True)
        self.k_bump = nuke.Enumeration_Knob("bump", "Bump", ["minor", "major"])
        self.k_dest = nuke.Text_Knob("dest", "")
        for k in (self.k_bump, self.k_dest):
            self.p.addKnob(k)
        self.p.knobChanged = self._changed
        self._refresh_dest()

    def run(self):
        if self.p.showModalDialog():
            self._save()

    def _changed(self, knob):
        if self.picker.handles(knob) or knob in (self.picker.k_name, self.k_bump):
            if self.picker.handles(knob):
                self.picker.reload(knob.name())
            self._refresh_dest()

    def _refresh_dest(self):
        try:
            t = get_ops().next_save(self.picker.target(),
                                    name=(self.picker.k_name.value() or "main"),
                                    bump=self.k_bump.value())
            self.k_dest.setValue(f"-> {t.label()}   {t.path}")
            self._pending = t
        except OpsError as e:
            self.k_dest.setValue(str(e))
            self._pending = None

    def _save(self):
        if not self._pending:
            return
        nuke = self.nuke
        t = self._pending
        name = self.picker.k_name.value() or "main"
        nuke.scriptSaveAs(t.path.replace("\\", "/"))
        if t.is_new_major:
            get_ops().register_major(self.picker.target(), t, name=name)
        to_env(self.picker.target())
        _msg(f"Saved {t.label()}\n{t.path}")


# ---------------------------------------------------------------------------
# gizmos + render (menu commands)
# ---------------------------------------------------------------------------

@_guard
def create_square_write():
    gizmos.create_square_write(_nuke())


@_guard
def create_square_read():
    gizmos.create_square_read(_nuke())


# ---------------------------------------------------------------------------
# Publish  (Write render *or* an existing Read)
# ---------------------------------------------------------------------------

def _selected_square_node(nuke):
    """The Write / Read to publish -- a selected one, else the only Write."""
    sel = [n for n in nuke.selectedNodes() if n.Class() in ("Write", "Read")]
    sel.sort(key=lambda n: 0 if n.Class() == "Write" else 1)
    node = sel[0] if sel else next(iter(nuke.allNodes("Write")), None)
    if node is None:
        raise OpsError("Select a Square Write or Read node to publish.")
    return node


def _node_context(nuke, node):
    """(Target, media_type, name, make_preview) seeded from the node's Square
    tab, falling back to the launch env."""
    from .context import Target
    k = node.knobs()
    if gizmos.MARK in k:
        t = Target(node["sq_project"].value(), node["sq_episode"].value(),
                   node["sq_sequence"].value(), node["sq_shot"].value(),
                   node["sq_task"].value())
        media_type = node["sq_media_type"].value() or "CompRender"
        name = (node["sq_name"].value() if "sq_name" in k else "") or "main"
        make_preview = bool(node["sq_preview"].value()) if "sq_preview" in k else True
    else:
        t, media_type, name, make_preview = from_env(), "CompRender", "main", True
    return t, media_type, name, make_preview


def _node_frames(nuke, node):
    """The source frame paths for `node` over its render range."""
    pattern = (node["file"].value() or "").strip()
    if not pattern:
        return []
    if "#" not in pattern and "%0" not in pattern:      # single file (mov, ...)
        return [pattern]
    if node.Class() == "Read":
        first, last = int(node["first"].value()), int(node["last"].value())
    else:
        first = int(nuke.root()["first_frame"].value())
        last = int(nuke.root()["last_frame"].value())
    return [_expand(pattern, f) for f in range(first, last + 1)]


@_guard
def publish_dialog(node=None):
    """The Publish panel -- prefilled from a Square Write/Read (or the selection),
    everything editable, then publishes."""
    nuke = _nuke()
    node = node or _selected_square_node(nuke)
    _PublishPanel(nuke, node).run()


# back-compat menu names
def publish_selected_write():
    publish_dialog()


@_guard
def render_and_publish_selected():
    render_and_publish_node(_selected_square_node(_nuke()))


@_guard
def render_and_publish_node(node):
    """Render a Write over the script range, then open the Publish panel. If the
    node's 'Publish after render' box is off, just render."""
    nuke = _nuke()
    if node.Class() != "Write":
        raise OpsError("Render & Publish needs a Write node.")
    first = int(nuke.root()["first_frame"].value())
    last = int(nuke.root()["last_frame"].value())
    nuke.execute(node, first, last)
    if "sq_do_publish" in node.knobs() and not node["sq_do_publish"].value():
        _msg(f"Rendered {last - first + 1} frame(s).\n"
             "Publish later: Square -> Publish Output.")
        return
    _PublishPanel(nuke, node).run()


class _PublishPanel:
    def __init__(self, nuke, node):
        import nukescripts
        self.nuke, self.node = nuke, node
        ops = get_ops()
        seed, mtype, name, preview = _node_context(nuke, node)

        self.p = nukescripts.PythonPanel("Square — Publish Output", "com.square.publish")
        self.picker = _Picker(self.p, nuke, ops, with_name=True)
        self.k_mtype = nuke.Enumeration_Knob("mtype", "Media type", [""])
        self.k_version = nuke.Enumeration_Knob("version", "Version", [NEW_VERSION])
        self.k_comment = nuke.Multiline_Eval_String_Knob("comment", "Comment")
        self.k_preview = nuke.Boolean_Knob("preview", "Make review preview")
        self.k_src = nuke.Text_Knob("src", "Source")
        self.k_info = nuke.Text_Knob("info", "")
        for kb in (self.k_mtype, self.k_version, self.k_comment, self.k_preview,
                   self.k_src, self.k_info):
            self.p.addKnob(kb)
        self.k_preview.setValue(bool(preview))
        if self.picker.k_name and name:
            self.picker.k_name.setValue(name)
        self.p.knobChanged = self._changed
        self._want_mtype = mtype
        self._reload_types()

    def run(self):
        if self.p.showModalDialog():
            self._publish()

    # ---- wiring ----
    def _changed(self, knob):
        if self.picker.handles(knob):
            self.picker.reload(knob.name())
            self._reload_types()
        elif knob is self.picker.k_name or knob is self.k_mtype:
            self._reload_versions()

    def _reload_types(self):
        try:
            types = get_ops().output_types(self.picker.target())
        except OpsError as e:
            types = []
            self.k_info.setValue(str(e))
        self.picker._set(self.k_mtype, types or [""],
                         self._want_mtype if self._want_mtype in types else "")
        self._reload_versions()

    def _reload_versions(self):
        t, mt = self.picker.target(), self.k_mtype.value()
        self._src_frames = _node_frames(self.nuke, self.node) if self.node else []
        missing = [f for f in self._src_frames if not _exists(f)]
        src = self.node.name() if self.node else "(no node)"
        self.k_src.setValue(f"{len(self._src_frames)} frame(s), "
                            f"{len(missing)} missing   <-  {src}")
        vs = []
        try:
            if t.complete and mt:
                vs = [f"v{o.revision:03d}" for o in reversed(get_ops().output_versions(t, mt))]
        except OpsError:
            pass
        self.picker._set(self.k_version, [NEW_VERSION] + vs, self.k_version.value())
        self.k_info.setValue("(new) = the current workfile major"
                             if self.k_version.value() == NEW_VERSION else
                             "re-render of an existing version")

    # ---- do it ----
    def _publish(self):
        t = self.picker.target()
        if not t.complete:
            raise OpsError("Pick project / sequence / shot / task.")
        frames = getattr(self, "_src_frames", None) or _node_frames(self.nuke, self.node)
        if not frames:
            raise OpsError("No source frames — select a Square Write or Read.")
        missing = [f for f in frames if not _exists(f)]
        if missing:
            raise OpsError(f"{len(missing)} frame(s) not on disk yet (e.g. {missing[0]}).")
        v = self.k_version.value()
        version = None if v in (NEW_VERSION, "", None) else int(str(v).lstrip("v"))
        name = (self.picker.k_name.value() if self.picker.k_name else "main") or "main"
        res = get_ops().publish_render(
            t, frames, media_type=self.k_mtype.value(), name=name, version=version,
            make_preview=bool(self.k_preview.value()),
            comment=self.k_comment.value() or f"from {self.nuke.root().name()}")
        to_env(t)
        _msg(f"Published {self.k_mtype.value()} v{res.version:03d}"
             + (" + review preview" if getattr(res, "preview", None) else "")
             + f"\n{res.dir}")


# ---- helpers ------------------------------------------------------

def _expand(pattern: str, frame: int) -> str:
    import re
    m = re.search(r"#+", pattern)
    if m:
        return pattern[:m.start()] + str(frame).zfill(len(m.group())) + pattern[m.end():]
    m = re.search(r"%0(\d+)d", pattern)
    if m:
        return pattern[:m.start()] + str(frame).zfill(int(m.group(1))) + pattern[m.end():]
    return pattern


def _exists(path: str) -> bool:
    import os
    return os.path.isfile(path)
