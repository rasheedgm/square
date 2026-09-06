"""The Square panel for Nuke and the menu-command entry points.

Everything pipeline-side goes through `ops.NukeOps`; this module only talks to
Nuke. It imports `nuke` / `nukescripts` lazily so the file itself imports fine
outside Nuke (the tests import `ops` and `nodes`, not this).
"""

from __future__ import annotations

import traceback

from square_core.errors import NeedsLogin

from . import nodes
from .context import Target, from_env, to_env
from .ops import DEFAULT_OUTPUT_TYPE, NukeOps, OpsError

_ops: NukeOps | None = None
_panel = None


def _nuke():
    import nuke
    return nuke


def _get_ops() -> NukeOps:
    global _ops
    if _ops is None:
        try:
            _ops = NukeOps()
        except NeedsLogin:
            raise OpsError("Not signed in to Kitsu. Run any Square tool once to log in.")
    return _ops


def _err(msg: str) -> None:
    try:
        _nuke().message(f"Square: {msg}")
    except Exception:
        print(f"Square: {msg}")


def _guard(fn):
    def wrapped(*a, **k):
        try:
            return fn(*a, **k)
        except OpsError as e:
            _err(str(e))
        except Exception as e:                       # keep Nuke usable
            _err(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
    return wrapped


# ---------------------------------------------------------------------------
# actions (also the menu commands)
# ---------------------------------------------------------------------------

@_guard
def save_version(comment: str = "") -> None:
    nuke = _nuke()
    target = _current_target()
    path, rev = _get_ops().next_workfile_path(target)
    nuke.scriptSaveAs(path.replace("\\", "/"))
    _get_ops().register_saved(target, path, comment=comment or f"v{rev:03d}")
    to_env(target)
    nuke.message(f"Saved workfile v{rev:03d}\n{path}")


@_guard
def publish_render(media_type: str = DEFAULT_OUTPUT_TYPE) -> None:
    nuke = _nuke()
    sel = [n for n in nuke.selectedNodes() if n.Class() == "Write"]
    write = sel[0] if sel else next((n for n in nuke.allNodes("Write")), None)
    if write is None:
        raise OpsError("Select the Write node whose render you want to publish.")
    pattern = write["file"].value()
    first, last = int(nuke.root()["first_frame"].value()), int(nuke.root()["last_frame"].value())
    frames = [_expand(pattern, f) for f in range(first, last + 1)]
    missing = [f for f in frames if not _exists(f)]
    if missing:
        raise OpsError(f"{len(missing)} frame(s) not on disk yet — render first "
                       f"(e.g. {missing[0]}).")
    target = _current_target()
    res = _get_ops().publish_render(target, frames, media_type=media_type,
                                    comment=f"from {nuke.root().name()}")
    nuke.message(f"Published {media_type} v{res.version:03d}"
                 + (" + review proxy" if getattr(res, 'preview', None) else "")
                 + f"\n{res.dir}")


@_guard
def add_write_for_output(media_type: str = DEFAULT_OUTPUT_TYPE) -> None:
    nuke = _nuke()
    target = _current_target()
    path, rev = _get_ops().next_output_path(target, media_type)
    cs = _get_ops().colorspace_for(target, media_type)
    nodes.square_write(nuke, path=path, colorspace=cs, label=f"[Square] {media_type} v{rev:03d}")


@_guard
def load_plate(media_type: str = "Plate") -> None:
    nuke = _nuke()
    target = _current_target()
    info = _get_ops().plate_for_read(target, media_type=media_type)
    nodes.square_read(nuke, path=info["path"], colorspace=info["colorspace"],
                      frame_in=info["frame_in"], frame_out=info["frame_out"],
                      label=f"[Square] {media_type} v{info['version']:03d}")


@_guard
def open_version() -> None:
    nuke = _nuke()
    target = _current_target()
    vs = {f"v{w.revision:03d}": w for w in _get_ops().versions(target)}
    if not vs:
        raise OpsError("No saved workfile versions on this task.")
    p = nuke.Panel("Open Workfile Version")
    p.addEnumerationPulldown("version", " ".join(reversed(list(vs))))
    if not p.show():
        return
    chosen = vs.get(p.value("version"))
    if chosen:
        nuke.scriptOpen(chosen.path.replace("\\", "/"))
        to_env(target)


# ---------------------------------------------------------------------------
# the panel
# ---------------------------------------------------------------------------

def show() -> None:
    global _panel
    import nukescripts

    if _panel is None:
        _panel = _build_panel(nukescripts)
    _panel.show()


def _build_panel(nukescripts):
    ops = _get_ops()

    class SquarePanel(nukescripts.PythonPanel):
        def __init__(self):
            super().__init__("Square", "com.square.workfile_panel")
            import nuke

            self._projects = ops.projects()
            t = from_env()
            self.k_project = nuke.Enumeration_Knob("project", "Project", self._projects or [""])
            self.k_shot = nuke.Enumeration_Knob("shot", "Sequence / Shot", [""])
            self.k_task = nuke.Enumeration_Knob("task", "Task", [""])
            self.b_save = nuke.PyScript_Knob("save", "Save Version")
            self.b_open = nuke.PyScript_Knob("open", "Open Version…")
            self.b_write = nuke.PyScript_Knob("write", "Add Write")
            self.b_publish = nuke.PyScript_Knob("publish", "Publish Render")
            self.b_plate = nuke.PyScript_Knob("plate", "Load Plate")
            for k in (self.k_project, self.k_shot, self.k_task, self.b_save,
                      self.b_open, self.b_write, self.b_publish, self.b_plate):
                self.addKnob(k)
            if t.project in self._projects:
                self.k_project.setValue(t.project)
            self._reload_shots(preselect=f"{t.sequence}/{t.shot}" if t.complete else "")

        # ----

        def _reload_shots(self, preselect=""):
            proj = self.k_project.value()
            self._shot_map = {}
            entries = []
            if proj:
                for seq, shots in ops.shots_by_sequence(proj).items():
                    for sh in shots:
                        key = f"{seq}/{sh}"
                        entries.append(key)
                        self._shot_map[key] = (seq, sh)
            self.k_shot.setValues(entries or [""])
            if preselect in self._shot_map:
                self.k_shot.setValue(preselect)
            self._reload_tasks()

        def _reload_tasks(self):
            proj = self.k_project.value()
            seq_sh = self._shot_map.get(self.k_shot.value())
            tasks = ops.task_types(proj, *seq_sh) if (proj and seq_sh) else []
            self.k_task.setValues(tasks or [""])

        def target(self) -> Target:
            seq_sh = self._shot_map.get(self.k_shot.value(), ("", ""))
            return Target(self.k_project.value(), seq_sh[0], seq_sh[1], self.k_task.value())

        def knobChanged(self, knob):
            if knob is self.k_project:
                self._reload_shots()
            elif knob is self.k_shot:
                self._reload_tasks()
            elif knob is self.b_save:
                _panel_target_action(self, save_version)
            elif knob is self.b_open:
                _panel_target_action(self, open_version)
            elif knob is self.b_write:
                _panel_target_action(self, add_write_for_output)
            elif knob is self.b_publish:
                _panel_target_action(self, publish_render)
            elif knob is self.b_plate:
                _panel_target_action(self, load_plate)

    return SquarePanel()


def _panel_target_action(panel, fn):
    global _forced_target
    _forced_target = panel.target()
    try:
        fn()
    finally:
        _forced_target = None


_forced_target: Target | None = None


def _current_target() -> Target:
    if _forced_target and _forced_target.complete:
        return _forced_target
    t = from_env()
    if not t.complete:
        raise OpsError("No shot selected — open the Square panel (Square ▸ Workfile Panel).")
    return t


# ---- tiny helpers for frame checking -------------------------------

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
