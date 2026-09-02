"""GazuBackend -- the thin, verified-against-live layer of raw gazu calls.

Every method is small and returns raw gazu dicts (or ints). Policy and
model-mapping live one level up in `api.KitsuApi`. Folds in the old
`square_core.kitsu_gateway.GazuKitsuGateway` and adds the version-tracking /
project-template / file-tree calls verified 2026-09-02 against Zou 1.0.58.

Tests do not touch this class -- they hand `KitsuApi` a fake with the same
method surface.
"""

from __future__ import annotations

import logging

from square_core.errors import KitsuError

logger = logging.getLogger("square.kitsu")

# minimal file tree just to satisfy Zou's new_working_file / new_output_file,
# which reject with "No tree can be found" when a project has none. Its computed
# output is never used -- PathResolver owns real paths, we PUT them over the top.
_MINIMAL_FILE_TREE = {
    "working": {
        "mountpoint": "/mnt", "root": "square",
        "folder_path": {
            "shot": "<Project>/shots/<Sequence>/<Shot>/<TaskType>/work",
            "asset": "<Project>/assets/<AssetType>/<Asset>/<TaskType>/work",
        },
        "file_name": {
            "shot": "<Project>_<Sequence>_<Shot>_<TaskType>_v<Revision>",
            "asset": "<Project>_<Asset>_<TaskType>_v<Revision>",
        },
    },
    "output": {
        "mountpoint": "/mnt", "root": "square",
        "folder_path": {
            "shot": "<Project>/shots/<Sequence>/<Shot>/<TaskType>/output/<OutputType>/v<Version>/<Representation>",
            "asset": "<Project>/assets/<AssetType>/<Asset>/<TaskType>/output/<OutputType>/v<Version>",
        },
        "file_name": {
            "shot": "<Project>_<Sequence>_<Shot>_<TaskType>_<OutputType>_v<Version>",
            "asset": "<Project>_<Asset>_<OutputType>_v<Version>",
        },
    },
}


def _ref(obj):
    return obj if isinstance(obj, dict) else {"id": str(obj)}


class GazuBackend:
    def __init__(self, host: str):
        self.host = host
        self._gazu = None

    # ---- connection --------------------------------------------------

    def attach(self, tokens: dict, *, on_refresh=None):
        import gazu

        gazu.set_host(self.host)
        try:
            gazu.set_token(dict(tokens))
        except Exception:
            pass
        dc = getattr(getattr(gazu, "client", None), "default_client", None)
        if dc is not None:
            try:
                dc.use_refresh_token = True
                if not getattr(dc, "access_token", ""):
                    dc.access_token = tokens.get("access_token", "")
                if not getattr(dc, "refresh_token", ""):
                    dc.refresh_token = tokens.get("refresh_token", "")
                if on_refresh:
                    # gazu auto-refreshes on a 401; re-persist the rotated token
                    def _cb(*_a, **_k):
                        gazu.refresh_access_token()
                        on_refresh({"access_token": getattr(dc, "access_token", "") or "",
                                    "refresh_token": getattr(dc, "refresh_token", "") or ""})
                    dc.callback_not_authenticated = _cb
            except Exception:
                pass
        self._gazu = gazu
        return self

    @property
    def g(self):
        if self._gazu is None:
            raise KitsuError("gazu backend is not connected")
        return self._gazu

    def current_user(self) -> dict:
        return self.g.client.get_current_user()

    # ---- projects --------------------------------------------------

    def all_projects(self) -> list:
        return self.g.project.all_projects() or []

    def get_project(self, ident: str) -> dict | None:
        try:
            return self.g.project.get_project(ident)
        except Exception:
            return self.g.project.get_project_by_name(ident)

    def new_project(self, name: str, production_type: str = "short") -> dict:
        return self.g.project.new_project(name, production_type=production_type)

    def update_project(self, project: dict) -> dict:
        return self.g.project.update_project(project)

    def all_project_statuses(self) -> list:
        try:
            return self.g.client.get("data/project-status") or []
        except Exception:
            return []

    def all_project_templates(self) -> list:
        try:
            return self.g.project_template.all_project_templates() or []
        except Exception:
            return []

    def apply_project_template(self, project: dict, template_name: str) -> None:
        tmpl = self.g.project_template.get_project_template_by_name(template_name)
        if not tmpl:
            raise KitsuError(f"no Kitsu project template named {template_name!r}")
        self.g.project_template.apply_project_template(project, tmpl)

    def set_minimal_file_tree(self, project: dict) -> None:
        self.g.files.update_project_file_tree(_ref(project), _MINIMAL_FILE_TREE)

    # ---- breakdown ------------------------------------------------

    def get_or_create_sequence(self, project, name) -> dict:
        proj = _ref(project)
        seq = self.g.shot.get_sequence_by_name(proj, name)
        if not seq:
            seq = self.g.shot.new_sequence(proj, name)
        return seq

    def get_or_create_shot(self, project, sequence, name, *, frame_in=1001, frame_out=1100,
                           fps=None, nb_frames=None) -> dict:
        proj, seq = _ref(project), _ref(sequence)
        shot = self.g.shot.get_shot_by_name(seq, name)
        if shot:
            return shot
        data = {"frame_in": frame_in, "frame_out": frame_out}
        if fps:
            data["fps"] = fps
        return self.g.shot.new_shot(
            proj, seq, name,
            nb_frames=nb_frames if nb_frames is not None else max(1, frame_out - frame_in + 1),
            data=data,
        )

    def all_shots_for_project(self, project) -> list:
        return self.g.shot.all_shots_for_project(_ref(project)) or []

    def update_shot_data(self, shot, data) -> dict:
        return self.g.shot.update_shot_data(_ref(shot), data)

    def get_entity(self, ident: str) -> dict | None:
        try:
            return self.g.entity.get_entity(ident)
        except Exception:
            return None

    def update_entity_data(self, entity, data) -> dict:
        return self.g.raw.put(f"data/entities/{_ref(entity)['id']}", {"data": data})

    # ---- tasks --------------------------------------------------

    def all_task_types(self) -> list:
        return self.g.task.all_task_types() or []

    def all_task_statuses(self) -> list:
        return self.g.task.all_task_statuses() or []

    def all_tasks_for_shot(self, shot) -> list:
        return self.g.task.all_tasks_for_shot(_ref(shot)) or []

    def new_task_type(self, name: str, for_entity: str = "Shot") -> dict:
        try:
            return self.g.task.new_task_type(name, for_entity=for_entity)
        except Exception:
            return self.g.task.get_task_type_by_name(name)

    def update_task_type(self, tt: dict) -> dict:
        return self.g.task.update_task_type(tt)

    def new_task(self, entity, task_type) -> dict:
        return self.g.task.new_task(_ref(entity), task_type)

    def get_task(self, ident: str) -> dict:
        return self.g.task.get_task(ident)

    def add_comment(self, task, status, text: str) -> dict:
        return self.g.task.add_comment(_ref(task), status, comment=text)

    def get_default_task_status(self) -> dict | None:
        try:
            return self.g.task.get_default_task_status()
        except Exception:
            return None

    def assign_task(self, task, person) -> None:
        self.g.task.assign_task(_ref(task), _ref(person))

    # ---- previews / annotations -------------------------------

    def add_preview(self, task, comment, file_path: str) -> dict:
        return self.g.task.add_preview(_ref(task), comment, file_path)

    def set_main_preview(self, preview) -> None:
        self.g.task.set_main_preview(_ref(preview))

    def get_preview_file(self, preview_id: str) -> dict:
        return self.g.files.get_preview_file(str(preview_id)) or {}

    def update_preview(self, preview, payload: dict) -> dict:
        return self.g.files.update_preview(_ref(preview), payload)

    def get_annotations(self, preview) -> list:
        pf = self.get_preview_file(_ref(preview)["id"])
        return pf.get("annotations") or []

    def update_preview_annotations(self, preview, *, additions=None, updates=None, deletions=None) -> dict:
        return self.g.files.update_preview_annotations(
            _ref(preview), additions=additions or [], updates=updates or [],
            deletions=deletions or [],
        )

    # ---- output types --------------------------------------

    def all_output_types(self) -> list:
        return self.g.files.all_output_types() or []

    def new_output_type(self, name: str, short_name: str = "") -> dict:
        return self.g.files.new_output_type(name, short_name or name[:3].lower())

    # ---- working / output files (versions) ---------------

    def working_files_for_task(self, task) -> list:
        return self.g.files.get_working_files_for_task(_ref(task)) or []

    def new_working_file(self, task, *, name="main", revision=0, software=None) -> dict:
        return self.g.files.new_working_file(
            _ref(task), name=name, revision=revision, software=software,
        )

    def set_working_file_path(self, working_file_id: str, path: str, data: dict | None = None) -> dict:
        payload = {"path": path}
        if data is not None:
            payload["data"] = data
        return self.g.raw.put(f"data/working-files/{working_file_id}", payload)

    def output_files_for_entity(self, entity, *, output_type=None) -> list:
        return self.g.files.all_output_files_for_entity(
            _ref(entity), output_type=_ref(output_type) if output_type else None,
        ) or []

    def new_output_file(self, entity, output_type, task_type, *, comment="", name="main",
                        revision=1, representation="") -> dict:
        return self.g.files.new_entity_output_file(
            _ref(entity), _ref(output_type), _ref(task_type),
            comment=comment, name=name, revision=revision, representation=representation,
        )

    def update_output_file(self, output_file_id: str, path: str, data: dict | None = None) -> dict:
        payload = {"path": path}
        if data is not None:
            payload["data"] = data
        return self.g.files.update_output_file(output_file_id, payload)
