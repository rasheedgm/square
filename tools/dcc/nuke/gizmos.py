"""SquareRead / SquareWrite.

Not `.gizmo` files -- a stock Read / Write node gets a **Square** tab whose
knobs (project / episode / sequence / shot / task / media type / name /
version) resolve the node's `file` from the pipeline. A `knobChanged` callback
keeps `file` in sync and blocks rendering a locked output version.

Lazy, one level at a time: picking a project loads only episode + sequence;
picking a sequence loads only shots; picking a shot loads only tasks; picking
a task loads media types + versions. Node creation seeds the cascade from the
launch context (`SQUARE_PROJECT` / `_SEQUENCE` / `_SHOT` / `_TASK`) but only
walks as far as that context actually specifies -- a bare project with
nothing else set costs exactly one Kitsu call (the project list) to create
the node, not the whole tree underneath it. Any knob change anywhere in the
cascade re-triggers everything below it -- picking a new shot refreshes media
type / name / version right away, not only once media type is next touched
by hand -- plus a manual **Refresh** button for state that changed outside
this Nuke session entirely (a teammate's publish, a new lock).

`sq_name` lists known name-streams for the current (shot, media type) --
e.g. a Precomp shot might have `fg` / `bg` / `keying`, not just `main` -- but
accepts any typed value too, since a brand new stream has no Kitsu record to
list yet.

`menu.py` calls `register_callbacks()` once so hand-built or loaded nodes keep
working.
"""

from __future__ import annotations

from .context import Target, from_env
from .ops import NEW_VERSION, SYNC_VERSION, OpsError

MARK = "sq_kind"                    # hidden String knob: "read" | "write"
_CASCADE = ["sq_project", "sq_episode", "sq_sequence", "sq_shot", "sq_task"]
_ALL = _CASCADE + ["sq_media_type", "sq_name", "sq_version"]
_HAS_NEXT_LEVEL = {"sq_project", "sq_episode", "sq_sequence", "sq_shot"}


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


def _divider(nuke, name: str):
    """An unset Text_Knob -- no label, no value -- the standard Nuke
    convention for a blank separating row between knob groups."""
    return nuke.Text_Knob(name, "")


def _create(nuke, node_class: str, kind: str):
    node = nuke.createNode(node_class, inpanel=False)
    node.addKnob(nuke.Tab_Knob("square", "Square"))

    mark = nuke.String_Knob(MARK, "")
    mark.setValue(kind)
    mark.setVisible(False)
    node.addKnob(mark)

    # -- context: project / episode+seq / shot ---------------------------
    node.addKnob(nuke.Enumeration_Knob("sq_project", "Project", [""]))
    node.addKnob(nuke.Enumeration_Knob("sq_episode", "Episode", [""]))
    seq_knob = nuke.Enumeration_Knob("sq_sequence", "Seq", [""])
    seq_knob.clearFlag(nuke.STARTLINE)     # shares Episode's line
    node.addKnob(seq_knob)
    node.addKnob(nuke.Enumeration_Knob("sq_shot", "Shot", [""]))
    node.addKnob(_divider(nuke, "sq_div1"))

    # -- what: task+media type / name -------------------------------------
    node.addKnob(nuke.Enumeration_Knob("sq_task", "Task", [""]))
    mt_knob = nuke.Enumeration_Knob("sq_media_type", "Media type", [""])
    mt_knob.clearFlag(nuke.STARTLINE)      # shares Task's line
    node.addKnob(mt_knob)
    # editable so a brand new stream (no Kitsu record yet) can just be
    # typed, not only picked from known name-streams for this media type.
    node.addKnob(nuke.EditableEnumeration_Knob("sq_name", "Name", ["main"]))
    node.addKnob(_divider(nuke, "sq_div2"))

    # -- version + status --------------------------------------------------
    node.addKnob(nuke.Enumeration_Knob("sq_version", "Version", [""]))
    refresh = nuke.PyScript_Knob(
        "sq_refresh", "Refresh",
        "from tools.dcc.nuke import gizmos; gizmos.refresh_node(nuke, nuke.thisNode())")
    refresh.clearFlag(nuke.STARTLINE)      # sits beside the version it refreshes
    node.addKnob(refresh)
    node.addKnob(nuke.Text_Knob("sq_status", ""))

    if kind == "write":
        node.addKnob(_divider(nuke, "sq_div3"))

        # -- preview / publish toggles + actions --------------------------
        # explicit STARTLINE throughout, not left to each knob type's own
        # default: PyScript_Knob's real default does NOT start a new line
        # (confirmed against real Nuke), unlike most other knob types, so
        # relying on defaults here previously put Render/Publish on the
        # checkbox's own line by accident.
        prev = nuke.Boolean_Knob("sq_preview", "Make preview")
        prev.setValue(True)
        prev.setFlag(nuke.STARTLINE)
        node.addKnob(prev)
        dop = nuke.Boolean_Knob("sq_do_publish", "Publish after render")
        dop.setValue(True)
        dop.setFlag(nuke.STARTLINE)
        node.addKnob(dop)
        render_btn = nuke.PyScript_Knob(
            "sq_publish", "Render",
            "from tools.dcc.nuke import panel; panel.render_and_publish_node(nuke.thisNode())")
        render_btn.setFlag(nuke.STARTLINE)
        node.addKnob(render_btn)
        # for frames that already exist (rendered with "Publish after
        # render" off, or via Nuke's own Render) -- publish them without
        # re-rendering. Shares Render's line.
        publish_only_btn = nuke.PyScript_Knob(
            "sq_publish_only", "Publish",
            "from tools.dcc.nuke import panel; panel.publish_dialog(nuke.thisNode())")
        publish_only_btn.clearFlag(nuke.STARTLINE)
        node.addKnob(publish_only_btn)

        node.addKnob(_divider(nuke, "sq_div4"))
        create_read_btn = nuke.PyScript_Knob(
            "sq_create_read", "Create Read",
            "from tools.dcc.nuke import panel; panel.create_read_from_write(nuke.thisNode())")
        create_read_btn.setFlag(nuke.STARTLINE)
        node.addKnob(create_read_btn)

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
    name = knob.name()
    if name in _HAS_NEXT_LEVEL:
        # a cascade level changing (e.g. shot) reseeds every level below it
        # (task) purely by script, which never raises its OWN knobChanged --
        # so media type / name / version must be re-run here too, not just
        # when the user happens to touch one of those knobs directly.
        _guard(node, _repopulate_next_level, nuke, node, name)
        _guard(node, _repopulate_media, nuke, node)
    elif name in ("sq_task", "sq_media_type", "sq_name"):
        _guard(node, _repopulate_media, nuke, node)
    _guard(node, _apply_file, nuke, node)


def refresh_node(nuke, node) -> None:
    """The manual Refresh button: drops this shot's cached output-file lists
    (another artist's publish or lock since this node was last touched
    wouldn't otherwise be seen for the rest of the Nuke session) and
    re-resolves media type / name / version / file from a live fetch."""
    t = _target(node)
    if t.complete:
        _guard(node, lambda: _ops().refresh(t))
    _guard(node, _repopulate_media, nuke, node)
    _guard(node, _apply_file, nuke, node)


def refresh_all_square_nodes(nuke) -> None:
    """Re-applies every Square Write/Read node's file resolution against
    whatever script is open right now. Needed after anything that changes
    the open script WITHOUT going through a node's own knobChanged -- Minor
    Up / Major Up / Save Version… all call nuke.scriptSaveAs() directly, so
    a Write left on (sync) would otherwise keep pointing at the major that
    was open before the bump until the artist happened to touch one of its
    own knobs."""
    for node in nuke.allNodes():
        if MARK in node.knobs():
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


def _name(node) -> str:
    return (node["sq_name"].value() if "sq_name" in node.knobs() else "") or "main"


def _open_script_path(nuke) -> str:
    try:
        return nuke.root().name() or ""
    except Exception:
        return ""


def _populate(nuke, node, t: Target) -> None:
    """Fast: sets only the project list + selected value (one Kitsu call).
    Nothing downstream loads unless the launch context actually names a
    project -- and even then only as far down the cascade as that context
    specifies; see _seed_cascade()."""
    ops = _ops()
    _set_values(node, "sq_project", ops.projects(), t.project)
    if t.project:
        _guard(node, _seed_cascade, nuke, node, t)


def _seed_cascade(nuke, node, t: Target) -> None:
    """Auto-continues the lazy, one-level-at-a-time cascade exactly as far as
    `t` (the launch context) actually resolves -- a project alone stops
    after loading episode + sequence; a full project/sequence/shot/task
    context (the common case, launched from the workfile manager) resolves
    the whole node in one pass, same end result as before, just without the
    redundant re-fetches each step used to cost.

    Checks `t`'s OWN fields to decide whether to continue, not the knobs'
    values: a freshly-populated Enumeration_Knob defaults to showing its
    first entry the moment `setValues()` runs, whether or not anything was
    actually seeded -- reading the knob back would make "project alone"
    look identical to "project, sequence, shot and task all seeded" and
    always walk the whole tree regardless."""
    _repopulate_next_level(nuke, node, "sq_project", t)
    if not t.sequence:
        return
    _repopulate_next_level(nuke, node, "sq_sequence", t)
    if not t.shot:
        return
    _repopulate_next_level(nuke, node, "sq_shot", t)
    if not t.task_type:
        return
    _repopulate_media(nuke, node)
    _apply_file(nuke, node)


def _repopulate_next_level(nuke, node, changed: str, seed: Target | None = None) -> None:
    """Loads ONLY the level(s) immediately below `changed` -- lazy, not the
    whole downstream chain -- so picking a project doesn't also fetch every
    shot and task underneath it before the user (or the seed target) has
    even reached a sequence."""
    ops = _ops()
    proj = node["sq_project"].value()

    if changed == "sq_project":
        eps = ops.episodes(proj) if (proj and ops.is_episodic(proj)) else []
        node["sq_episode"].setEnabled(bool(eps))
        _set_values(node, "sq_episode", eps or [""], getattr(seed, "episode", "") if seed else "")
        seqs = ops.sequences(proj, node["sq_episode"].value()) if proj else []
        _set_values(node, "sq_sequence", seqs or [""],
                    getattr(seed, "sequence", "") if seed else "")
    elif changed == "sq_episode":
        seqs = ops.sequences(proj, node["sq_episode"].value()) if proj else []
        _set_values(node, "sq_sequence", seqs or [""],
                    getattr(seed, "sequence", "") if seed else "")
    elif changed == "sq_sequence":
        shots = ops.shots(proj, node["sq_sequence"].value()) if proj else []
        _set_values(node, "sq_shot", shots or [""], getattr(seed, "shot", "") if seed else "")
    elif changed == "sq_shot":
        tt = []
        if proj and node["sq_sequence"].value() and node["sq_shot"].value():
            tt = ops.task_types(proj, node["sq_sequence"].value(), node["sq_shot"].value())
        want = (getattr(seed, "task_type", "") if seed else "") or \
            (ops.default_task_for(proj, node["sq_sequence"].value(), node["sq_shot"].value())
             if tt else "")
        _set_values(node, "sq_task", tt or [""], want)


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
    _set_name_values(node, ops.name_streams(t, mt))
    stream = _name(node)
    if kind == "write":
        vs = [f"v{o.revision:03d}" for o in reversed(ops.output_versions(t, mt, name=stream))]
        _set_values(node, "sq_version", [NEW_VERSION, SYNC_VERSION] + vs,
                    node["sq_version"].value() or SYNC_VERSION)
    else:
        try:
            info = ops.resolve_read_path(t, mt, "latest", name=stream)
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
    stream = _name(node)
    ver = node["sq_version"].value().lstrip("v") if "sq_version" in node.knobs() else ""
    if _kind(node) == "write":
        info = ops.resolve_output_path(t, mt, ver or SYNC_VERSION, name=stream,
                                       open_script_path=_open_script_path(nuke))
        if info["locked"]:
            # a locked revision must not be renderable at all: blank `file`
            # so Nuke's OWN native render / farm submit can't silently
            # overwrite it either, not just our Render/Publish buttons
            # (which already refuse via publish_render()'s own lock check).
            _set(node, "file", "")
            _set(node, "sq_status", f"[Square] v{info['version']:03d} is LOCKED — "
                 "pick (new) or a different version")
            _set_enabled(node, "sq_publish", False)
            _set_enabled(node, "sq_publish_only", False)
            return
        _set_enabled(node, "sq_publish", True)
        _set_enabled(node, "sq_publish_only", True)
        _set(node, "file", info["path"].replace("\\", "/"))
        _set(node, "file_type", "exr")
        _set(node, "create_directories", True)
        if info["colorspace"]:
            _set(node, "colorspace", info["colorspace"])
        _set(node, "sq_status", f"[Square] -> v{info['version']:03d}")
    else:
        info = ops.resolve_read_path(t, mt, ver or "latest", name=stream)
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


def _set_enabled(node, knob, enabled: bool) -> None:
    if knob in node.knobs():
        try:
            node[knob].setEnabled(enabled)
        except Exception:
            pass


def _set_values(node, knob, values, selected="") -> None:
    if knob not in node.knobs():
        return
    node[knob].setValues([str(v) for v in (values or [""])])
    if selected and selected in values:
        node[knob].setValue(str(selected))


def _set_name_values(node, names: list) -> None:
    """Like _set_values(), but for sq_name specifically: whatever's already
    typed/selected is kept even when it's not (yet) one of the known
    streams `names` lists -- a brand new stream the artist just typed has no
    Kitsu record to appear in that list until something publishes under it."""
    if "sq_name" not in node.knobs():
        return
    current = node["sq_name"].value() or "main"
    values = list(dict.fromkeys([current] + list(names or ["main"])))
    node["sq_name"].setValues(values)
    node["sq_name"].setValue(current)
