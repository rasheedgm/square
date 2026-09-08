"""SquareRead / SquareWrite.

Not `.gizmo` files -- a stock Read / Write node gets a **Square** tab whose
knobs (project / episode / sequence / shot / task / media type / name /
version) resolve the node's `file` from the pipeline. A `knobChanged` callback
keeps `file` in sync and blocks rendering a locked output version.

`menu.py` calls `register_callbacks()` once so hand-built or loaded nodes keep
working.
"""

from __future__ import annotations

from .context import Target, from_env
from .ops import NEW_VERSION, OpsError

MARK = "sq_kind"                    # hidden String knob: "read" | "write"
_CASCADE = ["sq_project", "sq_episode", "sq_sequence", "sq_shot", "sq_task"]
_ALL = _CASCADE + ["sq_media_type", "sq_name", "sq_version"]


def _ops():
    from .panel import get_ops
    return get_ops()


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------

def create_square_write(nuke):
    return _create(nuke, "Write", "write")


def create_square_read(nuke):
    return _create(nuke, "Read", "read")


def _create(nuke, node_class: str, kind: str):
    node = nuke.createNode(node_class, inpanel=False)
    node.addKnob(nuke.Tab_Knob("square", "Square"))

    mark = nuke.String_Knob(MARK, "")
    mark.setValue(kind)
    mark.setVisible(False)
    node.addKnob(mark)

    labels = [("sq_project", "Project"), ("sq_episode", "Episode"),
              ("sq_sequence", "Sequence"), ("sq_shot", "Shot"), ("sq_task", "Task"),
              ("sq_media_type", "Media type"), ("sq_name", "Name"),
              ("sq_version", "Version")]
    for name, label in labels:
        node.addKnob(nuke.Enumeration_Knob(name, label, [""]))
    if kind == "write":
        prev = nuke.Boolean_Knob("sq_preview", "Make review preview")
        prev.setValue(True)
        node.addKnob(prev)
        dop = nuke.Boolean_Knob("sq_do_publish", "Publish after render")
        dop.setValue(True)
        node.addKnob(dop)
        node.addKnob(nuke.PyScript_Knob(
            "sq_publish", "Render",
            "from tools.dcc.nuke import panel; panel.render_and_publish_node(nuke.thisNode())"))
    status = nuke.Text_Knob("sq_status", "")
    node.addKnob(status)

    _populate(nuke, node, from_env())
    return node


# ---------------------------------------------------------------------------
# the knobChanged callback
# ---------------------------------------------------------------------------

def register_callbacks(nuke) -> None:
    nuke.addKnobChanged(lambda: on_knob_changed(nuke), nodeClass="Write")
    nuke.addKnobChanged(lambda: on_knob_changed(nuke), nodeClass="Read")


def on_knob_changed(nuke) -> None:
    node = nuke.thisNode()
    if MARK not in node.knobs():
        return
    knob = nuke.thisKnob()
    if knob is None or knob.name() not in _ALL:
        return
    if knob.name() in _CASCADE:
        _guard(node, _repopulate_downstream, nuke, node, knob.name())
    _guard(node, _apply_file, nuke, node)


def _guard(node, fn, *a) -> None:
    """Run a step; a pipeline error just lands in the Square status knob so the
    node graph never breaks."""
    try:
        fn(*a)
    except OpsError as e:
        _set(node, "sq_status", f"[Square] {e}")
    except Exception as e:
        _set(node, "sq_status", f"[Square] {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _target(node) -> Target:
    g = lambda k: (node[k].value() if k in node.knobs() else "") or ""
    return Target(project=g("sq_project"), episode=g("sq_episode"),
                  sequence=g("sq_sequence"), shot=g("sq_shot"), task_type=g("sq_task"))


def _kind(node) -> str:
    return node[MARK].value() if MARK in node.knobs() else ""


def _populate(nuke, node, t: Target) -> None:
    ops = _ops()
    _set_values(node, "sq_project", ops.projects(), t.project)
    _guard(node, _repopulate_downstream, nuke, node, "sq_project", t)
    _guard(node, _apply_file, nuke, node)


def _repopulate_downstream(nuke, node, changed: str, seed: Target | None = None) -> None:
    ops = _ops()
    t = seed or _target(node)
    proj = node["sq_project"].value()
    order = ["sq_project", "sq_episode", "sq_sequence", "sq_shot", "sq_task"]
    below = order[order.index(changed) + 1:]

    if "sq_episode" in below:
        eps = ops.episodes(proj) if (proj and ops.is_episodic(proj)) else []
        node["sq_episode"].setEnabled(bool(eps))
        _set_values(node, "sq_episode", eps or [""], getattr(seed, "episode", "") if seed else "")
    if "sq_sequence" in below:
        seqs = ops.sequences(proj, node["sq_episode"].value()) if proj else []
        _set_values(node, "sq_sequence", seqs or [""],
                    getattr(seed, "sequence", "") if seed else "")
    if "sq_shot" in below:
        shots = ops.shots(proj, node["sq_sequence"].value()) if proj else []
        _set_values(node, "sq_shot", shots or [""], getattr(seed, "shot", "") if seed else "")
    if "sq_task" in below:
        tt = []
        if proj and node["sq_sequence"].value() and node["sq_shot"].value():
            tt = ops.task_types(proj, node["sq_sequence"].value(), node["sq_shot"].value())
        want = (getattr(seed, "task_type", "") if seed else "") or \
            (ops.default_task_for(proj, node["sq_sequence"].value(), node["sq_shot"].value())
             if tt else "")
        _set_values(node, "sq_task", tt or [""], want)

    _repopulate_media(nuke, node)


def _repopulate_media(nuke, node) -> None:
    ops = _ops()
    t = _target(node)
    if not t.complete:
        return
    kind = _kind(node)
    types = ops.output_types(t) if kind == "write" else ops.read_types(t)
    _set_values(node, "sq_media_type", types or [""], node["sq_media_type"].value())
    mt = node["sq_media_type"].value()
    if not mt:
        return
    if kind == "write":
        vs = [f"v{o.revision:03d}" for o in reversed(ops.output_versions(t, mt))]
        _set_values(node, "sq_version", [NEW_VERSION] + vs, node["sq_version"].value() or NEW_VERSION)
        _set_values(node, "sq_name", ["main"], "main")
    else:
        try:
            info = ops.resolve_read_path(t, mt, "latest")
            vs = [f"v{n:03d}" for n in info["versions"]]
        except OpsError:
            vs = [""]
        _set_values(node, "sq_version", vs or [""], node["sq_version"].value())


def _apply_file(nuke, node) -> None:
    t = _target(node)
    mt = node["sq_media_type"].value() if "sq_media_type" in node.knobs() else ""
    if not (t.complete and mt):
        return
    ops = _ops()
    ver = node["sq_version"].value().lstrip("v") if "sq_version" in node.knobs() else ""
    if _kind(node) == "write":
        info = ops.resolve_output_path(t, mt, ver or NEW_VERSION)
        _set(node, "file", info["path"].replace("\\", "/"))
        _set(node, "file_type", "exr")
        _set(node, "create_directories", True)
        if info["colorspace"]:
            _set(node, "colorspace", info["colorspace"])
        _set(node, "sq_status", "[Square] LOCKED — pick (new)" if info["locked"]
             else f"[Square] -> v{info['version']:03d}")
    else:
        info = ops.resolve_read_path(t, mt, ver or "latest")
        _set(node, "file", info["path"].replace("\\", "/"))
        if info["colorspace"]:
            _set(node, "colorspace", info["colorspace"])
        _set(node, "first", int(info["frame_in"] or 1))
        _set(node, "last", int(info["frame_out"] or 1))
        _set(node, "sq_status", f"[Square] {mt} v{info['version']:03d}")


def _set(node, knob, value) -> None:
    try:
        node[knob].setValue(value)
    except Exception:
        pass


def _set_values(node, knob, values, selected="") -> None:
    if knob not in node.knobs():
        return
    node[knob].setValues([str(v) for v in (values or [""])])
    if selected and selected in values:
        node[knob].setValue(str(selected))
