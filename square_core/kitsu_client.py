import os
import logging
import uuid

logger = logging.getLogger("SquareKitsu")

class KitsuClient:
    """Wrapper around CGWire gazu API for live Kitsu DB operations."""

    def __init__(self, host=None, email=None, password=None, dry_run=False):
        self.host = host or "https://kitsu.squarevfx.com/api"
        self.email = email or "pipeline@squarevfx.com"
        self.password = password or "secret"
        self.dry_run = dry_run
        self.is_connected = False
        self.gazu = None
        self._mock_projects = [
            {"id": "11111111-1111-1111-1111-111111111111", "name": "Feature Film Alpha", "code": "FFA"},
            {"id": "22222222-2222-2222-2222-222222222222", "name": "Commercial Brand X", "code": "CBX"},
            {"id": "33333333-3333-3333-3333-333333333333", "name": "VFX Demo Showreel", "code": "DEMO"}
        ]

    def connect(self):
        """Attempts to connect to Kitsu via gazu API using configured host and credentials."""
        if self.dry_run:
            logger.info("[KitsuClient] Operating in DRY-RUN / MOCK mode.")
            self.is_connected = True
            return True

        try:
            import gazu
            self.gazu = gazu
            gazu.set_host(self.host)
            gazu.log_in(self.email, self.password)
            self.is_connected = True
            logger.info(f"[KitsuClient] Successfully connected to Kitsu at {self.host}")
            return True
        except Exception as e:
            logger.warning(f"[KitsuClient] Could not connect to live Kitsu server ({self.host}): {e}. Operating in offline mock mode.")
            self.is_connected = False
            return False

    def get_all_projects(self):
        """Returns list of active open projects from Kitsu."""
        if self.gazu and self.is_connected:
            try:
                projects = self.gazu.project.all_open_projects()
                if projects:
                    for p in projects:
                        if "code" not in p or not p["code"]:
                            p["code"] = "".join([w[0] for w in p["name"].split()]).upper()[:4]
                    return projects
            except Exception as e:
                logger.error(f"[KitsuClient] Error fetching projects from Kitsu: {e}")

        # Fallback preset projects if Kitsu server is unreachable or in mock mode
        return self._mock_projects

    def create_project(self, project_name, project_code):
        """Creates a new project in Kitsu."""
        if self.gazu and self.is_connected:
            try:
                proj = self.gazu.project.new_project(project_name)
                proj["code"] = project_code
                try:
                    self.gazu.project.update_project(proj)
                except Exception as e:
                    logger.warning(f"[KitsuClient] update_project code note: {e}")
                logger.info(f"[KitsuClient] Created new Kitsu project on server: '{project_name}' ({project_code})")
                return proj
            except Exception as e:
                logger.error(f"[KitsuClient] Failed to create Kitsu project on server: {e}")

        # Mock fallback return
        new_proj = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"proj-{project_code}")),
            "name": project_name,
            "code": project_code
        }
        self._mock_projects.append(new_proj)
        logger.info(f"[Mock Kitsu] Created mock project: '{project_name}' ({project_code})")
        return new_proj

    def get_or_create_sequence(self, project, sequence_name):
        """Fetches sequence or creates it if missing in Kitsu."""
        project_arg = project if isinstance(project, dict) else {"id": str(project)}

        if self.gazu and self.is_connected:
            try:
                seq = self.gazu.shot.get_sequence_by_name(project_arg, sequence_name)
                if not seq:
                    logger.info(f"[Kitsu Live] Creating new sequence '{sequence_name}' in Kitsu DB...")
                    seq = self.gazu.shot.new_sequence(project_arg, sequence_name)
                return seq
            except Exception as e:
                logger.error(f"[Kitsu Live Error] Sequence error: {e}")

        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"seq-{sequence_name}")),
            "name": sequence_name,
            "project_id": project_arg.get("id")
        }

    def get_or_create_shot(self, project, sequence, shot_name, plate_name="PL01", frame_in=1001, frame_out=1100, fps=24.0, resolution="1920x1080", colorspace="ACEScg", nas_path="", description=""):
        """Fetches shot or creates it in Kitsu DB with frame range & plate metadata."""
        project_arg = project if isinstance(project, dict) else {"id": str(project)}
        sequence_arg = sequence if isinstance(sequence, dict) else {"id": str(sequence)}

        shot_data = {
            "frame_in": frame_in,
            "frame_out": frame_out,
            "fps": fps,
            "plate_name": plate_name,
            "resolution": resolution,
            "colorspace": colorspace,
            "nas_path": nas_path,
            "description": description
        }

        if self.gazu and self.is_connected:
            try:
                shot = self.gazu.shot.get_shot_by_name(sequence_arg, shot_name)
                if not shot:
                    logger.info(f"[Kitsu Live] Creating new shot '{shot_name}' in Kitsu DB...")
                    shot = self.gazu.shot.new_shot(
                        project_arg,
                        sequence_arg,
                        shot_name,
                        nb_frames=(frame_out - frame_in + 1),
                        data=shot_data
                    )
                else:
                    logger.info(f"[Kitsu Live] Updating metadata on shot '{shot_name}'...")
                    try:
                        self.gazu.shot.update_shot_data(shot, shot_data)
                    except Exception as ex:
                        logger.warning(f"[Kitsu Live] Could not update shot_data: {ex}")
                return shot
            except Exception as e:
                logger.error(f"[Kitsu Live Error] Shot error: {e}")

        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"shot-{shot_name}")),
            "name": shot_name,
            "sequence_id": sequence_arg.get("id"),
            "nb_frames": frame_out - frame_in + 1,
            "data": shot_data
        }

    def create_default_tasks(self, shot, task_types=None):
        """Creates default tasks (Prep, Roto, Matchmove, 3D, Comp) for a shot in Kitsu."""
        shot_arg = shot if isinstance(shot, dict) else {"id": str(shot)}
        task_types = task_types or ["Prep", "Roto", "Matchmove", "3D", "Comp"]
        created_tasks = []

        if self.gazu and self.is_connected:
            try:
                # 1. Fetch task types defined on server
                all_tts = self.gazu.task.all_task_types()
                tt_by_name = {t["name"].lower(): t for t in all_tts} if all_tts else {}

                # 2. Fetch existing tasks for this shot to avoid duplicate task creation
                existing_tasks = self.gazu.task.all_tasks_for_shot(shot_arg)
                existing_type_ids = {t["task_type_id"]: t for t in existing_tasks} if existing_tasks else {}

                for task_type_name in task_types:
                    lower_name = task_type_name.lower()
                    if lower_name in tt_by_name:
                        tt = tt_by_name[lower_name]
                        # Ensure task type is scoped for Shot entity so task creation succeeds
                        if tt.get("for_entity") != "Shot":
                            try:
                                tt["for_entity"] = "Shot"
                                tt = self.gazu.task.update_task_type(tt)
                                tt_by_name[lower_name] = tt
                            except Exception as ex:
                                logger.warning(f"[Kitsu Live] Could not update task type for_entity: {ex}")
                    else:
                        try:
                            tt = self.gazu.task.new_task_type(task_type_name, for_entity="Shot")
                            tt_by_name[lower_name] = tt
                        except Exception:
                            tt = self.gazu.task.get_task_type_by_name(task_type_name)
                            if tt:
                                tt_by_name[lower_name] = tt

                    if tt and tt.get("id"):
                        if tt["id"] in existing_type_ids:
                            task = existing_type_ids[tt["id"]]
                        else:
                            task = self.gazu.task.new_task(shot_arg, tt)
                            existing_type_ids[tt["id"]] = task
                        created_tasks.append(task)

                return created_tasks
            except Exception as e:
                logger.error(f"[Kitsu Live Error] Task creation error: {e}")

        for tt in task_types:
            task_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"task-{shot_arg.get('id')}-{tt}"))
            created_tasks.append({"id": task_id, "name": tt, "shot_id": shot_arg.get("id")})
        return created_tasks

    def upload_preview_proxy(self, task, preview_file_path, comment="Plate Ingest Preview v001"):
        """Uploads a low-res MP4 preview to a Kitsu task."""
        task_arg = task if isinstance(task, dict) else {"id": str(task)}

        if self.gazu and self.is_connected and os.path.exists(preview_file_path):
            try:
                task_id = str(task_arg.get("id", ""))
                if "mock" in task_id or len(task_id) != 36:
                    logger.info(f"[Mock Kitsu] Skipping live upload for non-UUID task ID '{task_id}'")
                    return {"id": str(uuid.uuid4()), "task_id": task_id, "path": preview_file_path}

                logger.info(f"[Kitsu Live] Uploading preview proxy file to Kitsu task...")
                
                # Fetch valid task status object or default status
                status = task_arg.get("task_status_id") or self.gazu.task.get_default_task_status()
                if not status:
                    statuses = self.gazu.task.all_task_statuses()
                    status = statuses[0] if statuses else "todo"

                comment_obj = self.gazu.task.add_comment(task_arg, status, comment=comment)
                preview_obj = self.gazu.task.add_preview(task_arg, comment_obj, preview_file_path)
                logger.info(f"[Kitsu Live] Uploaded preview proxy successfully!")
                return preview_obj
            except Exception as e:
                logger.error(f"[Kitsu Live Error] Failed to upload preview to Kitsu: {e}")

        logger.info(f"[Mock Kitsu] Uploaded preview proxy '{preview_file_path}'")
        return {"id": str(uuid.uuid4()), "task_id": task_arg.get("id"), "path": preview_file_path}
