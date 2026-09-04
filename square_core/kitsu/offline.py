"""OfflineApi -- the no-op stand-in for `KitsuApi`.

Lets the ingest flow (tag -> resolve -> copy to the NAS) run with no Kitsu
reachable. Every write is a no-op that returns a minimal shape so callers
don't special-case; nothing hits a server. Reads return empties.

Same method surface as `KitsuApi` for the calls the offline path uses.
"""

from __future__ import annotations

from square_core.model import (
    User, Project, Sequence, Shot, TaskType, TaskStatus, Task, Output, PreviewMedia, Comment,
)

_UNSET = object()


class OfflineApi:
    host = ""

    def current_user(self):
        return User(id="offline", name="offline", email="")

    # projects
    def projects(self, *, status=None):
        return []

    def project(self, ident):
        return Project(id=f"offline-{ident}", code=str(ident), name=str(ident))

    def create_project(self, *, code, **kw):
        return Project(id=f"offline-{code}", code=code, name=kw.get("name", code))

    def project_templates(self):
        return []

    # breakdown
    def ensure_sequence(self, project, code):
        return Sequence(id=f"offline-seq-{code}", code=code)

    def ensure_shot(self, project, sequence, code, **kw):
        return Shot(id=f"offline-shot-{code}", code=code,
                    frame_in=kw.get("frame_in", 0), frame_out=kw.get("frame_out", 0))

    def shots(self, project):
        return []

    def merge_entity_data(self, entity, data):
        pass

    # tasks
    def task_types(self, *, for_entity=None):
        return []

    def task_statuses(self):
        return []

    def ensure_tasks(self, shot, names):
        return [Task(id=f"offline-task-{n}", task_type_name=n, entity_id=getattr(shot, "id", ""))
                for n in (names or [])]

    def resolve_status(self, name):
        return None

    def set_status(self, task, status_name, *, comment="", author=None):
        return None

    def comment(self, task, text, *, status=None):
        return Comment(id="offline-comment", text=text)

    def assign(self, task, users):
        pass

    # previews
    def upload_preview(self, task, file_path, *, comment="", status=None):
        return PreviewMedia(id="", path=file_path)

    def set_main_preview(self, preview):
        pass

    def preview_data(self, preview):
        return {}

    def merge_preview_data(self, preview, data):
        pass

    def stamp_provenance(self, record, provenance, *, on="preview"):
        pass

    def annotations(self, preview):
        return []

    def update_annotations(self, preview, **kw):
        return {}

    # output types / versions
    def output_types(self):
        return []

    def ensure_output_type(self, name, short_name=""):
        return {"id": f"offline-ot-{name}", "name": name}

    def next_output_revision(self, entity, output_type_name, task_type, *, name="main"):
        return 1

    def next_working_revision(self, task, *, name="main"):
        return 1

    def working_files(self, task):
        return []

    def output_files(self, entity, *, output_type_name=None):
        return []

    def record_working_file(self, task, *, revision, path, name="main", software=None, data=None):
        return Output(revision=revision, path=path)

    def record_output_file(self, entity, output_type_name, task_type, *, revision, path,
                           representation="", name="main", comment="", data=None):
        return Output(output_type=output_type_name, revision=revision, path=path,
                      representation=representation, name=name)

    def ingest_task(self, tasks):
        return tasks[0] if tasks else None
