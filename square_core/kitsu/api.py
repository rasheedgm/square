"""KitsuApi -- the facade every service calls for anything touching the
production DB.

Wraps a backend (`_gazu.GazuBackend` live, a fake in tests), applies the small
amount of policy that belongs here (status name resolution, idempotent
ensure_*, the "create the file record then PUT our path" dance) and returns
`square_core.model` objects.

What is NOT here: ingest's comment wording / task-preference / shot.data blob
(that's ingest policy, ported into `tools/ingest_tool/core/`), and path
computation (that's `square_core.paths`).
"""

from __future__ import annotations

import logging

from square_core.errors import KitsuError, NeedsLogin
from square_core.model import (
    Provenance, TaskType, TaskStatus,
)
from . import _map
from . import auth as _auth

logger = logging.getLogger("square.kitsu")

_INGEST_TASK_PREFERENCE = ("Ingest", "Prep")


def connect(host: str, *, session: dict | None = None) -> "KitsuApi":
    """Attach to Kitsu using `session` or a cached one. Raises `NeedsLogin`
    if there is nothing usable -- the tool prompts and calls `auth.login`.
    gazu auto-refreshes an expired access token on the fly; the rotated token
    is written back to the cache."""
    session = session or _auth.cached_session(host)
    if not session or not session.get("access_token"):
        raise NeedsLogin(host)
    from ._gazu import GazuBackend

    backend = GazuBackend(host).attach(
        session, on_refresh=lambda t: _auth.store_session(host, t)
    )
    return KitsuApi(backend, host=host)


class KitsuApi:
    def __init__(self, backend, *, host: str = ""):
        self._b = backend
        self.host = host
        self._tt_cache: list | None = None
        self._ts_cache: list | None = None

    # ---- identity --------------------------------------------------

    def current_user(self):
        return _map.user(self._b.current_user())

    # ---- projects ------------------------------------------------

    def projects(self, *, status: str | None = None) -> list:
        out = [_map.project(p) for p in self._b.all_projects()]
        if status:
            out = [p for p in out if p.status.lower() == status.lower()]
        return out

    def project(self, ident: str):
        d = self._b.get_project(ident)
        return _map.project(d) if d else None

    def create_project(self, *, code: str, name: str = "", production_type: str = "short",
                       kitsu_template: str = "", fps: float | None = None,
                       resolution: str = ""):
        raw = self._b.new_project(name or code, production_type=production_type)
        updates = {}
        if raw.get("code") != code:
            updates["code"] = code
        if fps:
            updates["fps"] = fps
        if resolution:
            updates["resolution"] = resolution
        if updates:
            raw.update(updates)
            raw = self._b.update_project(raw)
        if kitsu_template:
            self._b.apply_project_template(raw, kitsu_template)
        self._b.set_minimal_file_tree(raw)
        return _map.project(self._b.get_project(raw["id"]) or raw)

    def project_templates(self) -> list:
        return [t.get("name", "") for t in self._b.all_project_templates()]

    def set_project_status(self, project, status_name: str):
        want = status_name.strip().lower()
        sid = ""
        for s in (getattr(self._b, "all_project_statuses", lambda: [])() or []):
            if (s.get("name") or "").lower() == want:
                sid = s["id"]
                break
        raw = dict(getattr(project, "raw", None) or (project if isinstance(project, dict) else {}))
        if sid:
            raw["project_status_id"] = sid
        raw = self._b.update_project(raw)
        return _map.project(raw)

    # ---- breakdown ---------------------------------------------

    def ensure_sequence(self, project, code: str):
        return _map.sequence(self._b.get_or_create_sequence(_id(project), code))

    def ensure_shot(self, project, sequence, code: str, *, frame_in=1001, frame_out=1100,
                    fps=None, nb_frames=None):
        raw = self._b.get_or_create_shot(
            _id(project), _id(sequence), code,
            frame_in=frame_in, frame_out=frame_out, fps=fps, nb_frames=nb_frames,
        )
        return _map.shot(raw)

    def shots(self, project) -> list:
        return [_map.shot(s) for s in self._b.all_shots_for_project(_id(project))]

    def merge_entity_data(self, entity, data: dict) -> None:
        ref = _id(entity)
        etype = getattr(entity, "raw", {}).get("type") if hasattr(entity, "raw") else None
        if etype == "Shot" or _looks_like_shot(entity):
            existing = dict((getattr(entity, "data", None) or {}))
            existing.update(data)
            self._b.update_shot_data(ref, existing)
        else:
            self._b.update_entity_data(ref, data)

    # ---- tasks -----------------------------------------------

    def task_types(self, *, for_entity: str | None = None) -> list:
        if self._tt_cache is None:
            self._tt_cache = self._b.all_task_types()
        out = [_map.task_type(t) for t in self._tt_cache]
        if for_entity:
            out = [t for t in out if t.for_entity in (for_entity, "", None)]
        return out

    def task_statuses(self) -> list:
        if self._ts_cache is None:
            self._ts_cache = self._b.all_task_statuses()
        return [_map.task_status(s) for s in self._ts_cache]

    def ensure_tasks(self, shot, task_type_names) -> list:
        """Idempotent: create any missing task types (scoped Shot) and tasks."""
        names = [n for n in (task_type_names or [])]
        if not names:
            return []
        shot_ref = _id(shot)
        tt_by_name = {t["name"].lower(): t for t in self._b.all_task_types()}
        existing = {t["task_type_id"]: t for t in self._b.all_tasks_for_shot(shot_ref)}
        out = []
        for name in names:
            tt = tt_by_name.get(name.lower())
            if tt is None:
                tt = self._b.new_task_type(name, for_entity="Shot")
                if tt:
                    tt_by_name[name.lower()] = tt
            elif tt.get("for_entity") not in ("Shot", None, ""):
                try:
                    tt["for_entity"] = "Shot"
                    tt = self._b.update_task_type(tt)
                except Exception:
                    pass
            if not (tt and tt.get("id")):
                continue
            t = existing.get(tt["id"]) or self._b.new_task(shot_ref, tt)
            existing[tt["id"]] = t
            if isinstance(t, dict):
                t.setdefault("task_type_name", tt.get("name", name))
            out.append(_map.task(t))
        return out

    def resolve_status(self, name: str | None) -> dict | None:
        if not name:
            return None
        want = name.strip().lower()
        for s in self._b.all_task_statuses():
            if want in ((s.get("name") or "").lower(), (s.get("short_name") or "").lower()):
                return s
        return None

    def set_status(self, task, status_name: str, *, comment: str = "", author=None):
        st = self.resolve_status(status_name) or self._b.get_default_task_status()
        if st is None:
            raise KitsuError(f"no task status matching {status_name!r}")
        c = self._b.add_comment(_id(task), st, comment or "")
        return _map.comment(c) if isinstance(c, dict) else None

    def comment(self, task, text: str, *, status: str | None = None):
        st = self.resolve_status(status) if status else None
        st = st or self._task_current_status(task) or self._b.get_default_task_status()
        return _map.comment(self._b.add_comment(_id(task), st, text))

    def assign(self, task, users) -> None:
        for u in (users or []):
            self._b.assign_task(_id(task), _id(u))

    # ---- previews / annotations ----------------------------

    def upload_preview(self, task, file_path: str, *, comment: str = "",
                       status: str | None = None):
        st = self.resolve_status(status) if status else None
        st = st or self._task_current_status(task) or self._b.get_default_task_status()
        c = self._b.add_comment(_id(task), st, comment or "Preview")
        p = self._b.add_preview(_id(task), c, file_path)
        return _map.preview(p) if isinstance(p, dict) else None

    def set_main_preview(self, preview) -> None:
        self._b.set_main_preview(_id(preview))

    def preview_data(self, preview) -> dict:
        return dict(self._b.get_preview_file(_pid(preview)).get("data") or {})

    def merge_preview_data(self, preview, data: dict) -> None:
        existing = self.preview_data(preview)
        existing.update(data)
        self._b.update_preview(_id(preview), {"data": existing})

    def stamp_provenance(self, record, provenance: Provenance, *, on: str = "preview") -> None:
        """Merge a Provenance under data['square'] on a preview / working /
        output file record."""
        if on == "preview":
            merged = provenance.to_kitsu_data(self.preview_data(record))
            self._b.update_preview(_id(record), {"data": merged})
        elif on == "output":
            self._b.update_output_file(_pid(record), _path_of(record),
                                       provenance.to_kitsu_data(_data_of(record)))
        elif on == "working":
            self._b.set_working_file_path(_pid(record), _path_of(record),
                                          provenance.to_kitsu_data(_data_of(record)))

    def annotations(self, preview) -> list:
        return self._b.get_annotations(_id(preview))

    def update_annotations(self, preview, *, additions=None, updates=None, deletions=None):
        return self._b.update_preview_annotations(
            _id(preview), additions=additions, updates=updates, deletions=deletions)

    # ---- output types ----------------------------------

    def output_types(self) -> list:
        return [(o.get("name", ""), o) for o in self._b.all_output_types()]

    def ensure_output_type(self, name: str, short_name: str = "") -> dict:
        for o in self._b.all_output_types():
            if (o.get("name") or "").lower() == name.lower():
                return o
        return self._b.new_output_type(name, short_name)

    # ---- versions -------------------------------------

    def next_output_revision(self, entity, output_type_name: str, task=None, *, name="main") -> int:
        ot = self.ensure_output_type(output_type_name)
        existing = self._b.output_files_for_entity(_id(entity), output_type=ot)
        revs = [int(o.get("revision") or 0) for o in existing
                if (o.get("name") or "main") == name]
        return (max(revs) + 1) if revs else 1

    def next_working_revision(self, task, *, name="main") -> int:
        wfs = self._b.working_files_for_task(_id(task))
        revs = [int(w.get("revision") or 0) for w in wfs if (w.get("name") or "main") == name]
        return (max(revs) + 1) if revs else 1

    def working_files(self, task) -> list:
        return [_map.workfile(w) for w in self._b.working_files_for_task(_id(task))]

    def output_files(self, entity, *, output_type_name: str | None = None) -> list:
        ot = self.ensure_output_type(output_type_name) if output_type_name else None
        return [_map.output(o) for o in self._b.output_files_for_entity(_id(entity), output_type=ot)]

    def record_working_file(self, task, *, revision: int, path: str, name: str = "main",
                            software: str | None = None, data: dict | None = None):
        raw = self._b.new_working_file(_id(task), name=name, revision=revision, software=software)
        wf_id = (raw.get("file") or raw).get("id") if isinstance(raw.get("file"), dict) else raw["id"]
        updated = self._b.set_working_file_path(wf_id, path, data)
        return _map.workfile(updated if isinstance(updated, dict) else {**raw, "path": path})

    def record_output_file(self, entity, output_type_name: str, task, *, revision: int,
                           path: str, representation: str = "", name: str = "main",
                           comment: str = "", data: dict | None = None):
        ot = self.ensure_output_type(output_type_name)
        raw = self._b.new_output_file(_id(entity), ot, _task_type_ref(task), comment=comment,
                                      name=name, revision=revision, representation=representation)
        of_id = (raw.get("file") or raw).get("id") if isinstance(raw.get("file"), dict) else raw["id"]
        updated = self._b.update_output_file(of_id, path, data)
        return _map.output(updated if isinstance(updated, dict) else {**raw, "path": path})

    # ---- internals ---------------------------------

    def _task_current_status(self, task):
        raw = getattr(task, "raw", None) or (task if isinstance(task, dict) else {})
        sid = raw.get("task_status_id")
        if not sid:
            return None
        for s in self._b.all_task_statuses():
            if s.get("id") == sid:
                return s
        return None

    def ingest_task(self, tasks: list):
        """The task an ingest record should land on -- first of the preference
        list, else the first task."""
        by_type = {}
        for t in tasks:
            by_type.setdefault(getattr(t, "task_type_name", "") or "", t)
        for pref in _INGEST_TASK_PREFERENCE:
            if pref in by_type:
                return by_type[pref]
        return tasks[0] if tasks else None


# --------------------------------------------------------------------------

def _id(obj):
    """A backend-ready ref. Model objects hand over their full `raw` gazu dict
    (some gazu calls need `project_id` etc., not just `id`); everything else
    falls back to `{"id": ...}`."""
    raw = getattr(obj, "raw", None)
    if raw:
        return raw
    if isinstance(obj, dict):
        return obj
    return {"id": getattr(obj, "id", str(obj))}


def _pid(obj) -> str:
    """The bare string id -- for backend calls that take an id, not a ref."""
    if hasattr(obj, "id"):
        return obj.id
    if isinstance(obj, dict):
        return str(obj.get("id", ""))
    return str(obj)


def _task_type_ref(task) -> dict:
    """A `{"id": <task_type_id>}` ref from a Task (or a task dict, or an
    already-a-task-type ref)."""
    raw = getattr(task, "raw", None) or (task if isinstance(task, dict) else {})
    ttid = (raw.get("task_type_id")
            or getattr(task, "task_type_id", "")
            or raw.get("id")
            or getattr(task, "id", ""))
    return {"id": ttid}


def _path_of(record) -> str:
    return getattr(record, "path", "") or (record.get("path", "") if isinstance(record, dict) else "")


def _data_of(record) -> dict:
    d = getattr(record, "data", None)
    if d is None and isinstance(record, dict):
        d = record.get("data")
    return dict(d or {})


def _looks_like_shot(entity) -> bool:
    raw = getattr(entity, "raw", None) or (entity if isinstance(entity, dict) else {})
    return bool(raw.get("nb_frames") is not None or raw.get("sequence_id") or raw.get("parent_id"))
