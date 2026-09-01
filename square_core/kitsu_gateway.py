"""
GazuKitsuGateway -- the real (gazu-backed) implementation of the primitive
Kitsu operations KitsuRecorder needs.

Every method here is deliberately small and verified against a live Zou
(1.0.58 / gazu 1.2.1). The ingest policy -- what to write, when -- lives in
KitsuRecorder, not here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("SquareKitsuGateway")


class KitsuConnectionError(RuntimeError):
    pass


class NullKitsuGateway:
    """
    Used when Kitsu is unreachable but the user still wants to ingest to the
    NAS. Every write is a no-op that returns a minimal shape so KitsuRecorder
    runs through without special-casing; nothing reaches a server.
    """
    def get_or_create_sequence(self, project, name):
        return {"id": f"offline-seq-{name}", "name": name}

    def get_or_create_shot(self, project, sequence, name, **kw):
        return {"id": f"offline-shot-{name}", "name": name, "data": {}}

    def update_shot_data(self, shot, data):
        if isinstance(shot, dict):
            shot["data"] = data
        return shot

    def ensure_tasks(self, shot, task_type_names):
        return [{"id": f"offline-task-{n}", "task_type_name": n} for n in (task_type_names or [])]

    def add_comment(self, task, text, status=None):
        return {"id": "offline-comment"}

    def upload_preview(self, task, text, file_path, status=None):
        return {"id": "", "data": {}}

    def set_main_preview(self, preview):
        pass

    def get_preview_data(self, preview_id):
        return {}

    def update_preview_data(self, preview, data):
        return preview


class GazuKitsuGateway:
    def __init__(self, host: str, email: str, password: str):
        self.host = host
        self.email = email
        self.password = password
        self._gazu = None

    # ------------------------------------------------------------------

    def connect(self):
        import gazu
        gazu.set_host(self.host)
        try:
            gazu.log_in(self.email, self.password)
        except Exception as e:
            raise KitsuConnectionError(str(e)) from e
        self._gazu = gazu
        return self

    @property
    def gazu(self):
        if self._gazu is None:
            raise KitsuConnectionError("gateway is not connected -- call connect() first")
        return self._gazu

    # ------------------------------------------------------------------
    # Sequence / shot
    # ------------------------------------------------------------------

    @staticmethod
    def _as_ref(obj):
        return obj if isinstance(obj, dict) else {"id": str(obj)}

    def get_or_create_sequence(self, project, name) -> dict:
        proj = self._as_ref(project)
        seq = self.gazu.shot.get_sequence_by_name(proj, name)
        if not seq:
            logger.info("[Kitsu] creating sequence %s", name)
            seq = self.gazu.shot.new_sequence(proj, name)
        return seq

    def get_or_create_shot(self, project, sequence, name, *, frame_in=1001, frame_out=1100,
                           fps=None, nb_frames=None) -> dict:
        proj = self._as_ref(project)
        seq = self._as_ref(sequence)
        shot = self.gazu.shot.get_shot_by_name(seq, name)
        if shot:
            return shot
        logger.info("[Kitsu] creating shot %s", name)
        data = {"frame_in": frame_in, "frame_out": frame_out}
        if fps:
            data["fps"] = fps
        return self.gazu.shot.new_shot(
            proj, seq, name,
            nb_frames=nb_frames if nb_frames is not None else max(1, frame_out - frame_in + 1),
            data=data,
        )

    def update_shot_data(self, shot, data) -> dict:
        return self.gazu.shot.update_shot_data(self._as_ref(shot), data)

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def ensure_tasks(self, shot, task_type_names) -> list[dict]:
        """
        Idempotent: creates any missing task types (scoped to Shot) and any
        missing tasks on this shot, returns the full set for the requested
        names. Lifted from the verified-live create_default_tasks().
        """
        shot_ref = self._as_ref(shot)
        names = list(task_type_names or [])
        if not names:
            return []

        all_tts = self.gazu.task.all_task_types() or []
        tt_by_name = {t["name"].lower(): t for t in all_tts}

        existing = self.gazu.task.all_tasks_for_shot(shot_ref) or []
        existing_by_type = {t["task_type_id"]: t for t in existing}

        out = []
        for name in names:
            low = name.lower()
            tt = tt_by_name.get(low)
            if tt is None:
                try:
                    tt = self.gazu.task.new_task_type(name, for_entity="Shot")
                except Exception:
                    tt = self.gazu.task.get_task_type_by_name(name)
                if tt:
                    tt_by_name[low] = tt
            elif tt.get("for_entity") not in ("Shot", None):
                try:
                    tt["for_entity"] = "Shot"
                    tt = self.gazu.task.update_task_type(tt)
                    tt_by_name[low] = tt
                except Exception as e:
                    logger.warning("[Kitsu] could not rescope task type %s to Shot: %s", name, e)

            if not (tt and tt.get("id")):
                continue
            task = existing_by_type.get(tt["id"])
            if task is None:
                task = self.gazu.task.new_task(shot_ref, tt)
                existing_by_type[tt["id"]] = task
            # Kitsu names a shot's first task "main"; the *type* ("Ingest",
            # "Comp", ...) is what the recorder routes on, so stamp it.
            if isinstance(task, dict):
                task["task_type_name"] = tt.get("name", name)
            out.append(task)
        return out

    def _resolve_status(self, task_ref, status_name=None):
        """
        A task-status dict. `status_name` (e.g. "Done") is matched against
        each status' name / short_name; falls back to the task's current
        status, then the server default.
        """
        if status_name:
            want = status_name.strip().lower()
            for s in (self.gazu.task.all_task_statuses() or []):
                if want in ((s.get("name") or "").lower(), (s.get("short_name") or "").lower()):
                    return s
        return (
            task_ref.get("task_status_id")
            or self.gazu.task.get_default_task_status()
            or (self.gazu.task.all_task_statuses() or ["todo"])[0]
        )

    def add_comment(self, task, text, status=None) -> dict:
        task_ref = self._as_ref(task)
        return self.gazu.task.add_comment(
            task_ref, self._resolve_status(task_ref, status), comment=text
        )

    def upload_preview(self, task, text, file_path, status=None) -> dict:
        task_ref = self._as_ref(task)
        comment = self.gazu.task.add_comment(
            task_ref, self._resolve_status(task_ref, status), comment=text
        )
        return self.gazu.task.add_preview(task_ref, comment, file_path)

    def set_main_preview(self, preview) -> None:
        self.gazu.task.set_main_preview(self._as_ref(preview))

    # ------------------------------------------------------------------
    # Preview file data
    # ------------------------------------------------------------------

    def get_preview_data(self, preview_id) -> dict:
        pf = self.gazu.files.get_preview_file(str(preview_id)) or {}
        return dict(pf.get("data") or {})

    def update_preview_data(self, preview, data) -> dict:
        return self.gazu.files.update_preview(self._as_ref(preview), {"data": data})
