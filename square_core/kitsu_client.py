import logging
import uuid

logger = logging.getLogger("SquareKitsu")

class KitsuClient:
    """Wrapper around CGWire gazu API."""

    def __init__(self, host=None, email=None, password=None, dry_run=False):
        self.host = host or "https://kitsu.squarevfx.com/api"
        self.email = email or "pipeline@squarevfx.com"
        self.password = password or "secret"
        self.dry_run = dry_run
        self.is_connected = False
        self.gazu = None

    def connect(self):
        """Attempts to connect to Kitsu via gazu API using configured host and credentials."""
        try:
            import gazu
            self.gazu = gazu
            gazu.set_host(self.host)
            gazu.log_in(self.email, self.password)
            self.is_connected = True
            logger.info(f"[KitsuClient] Successfully connected to Kitsu at {self.host}")
            return True
        except Exception as e:
            logger.warning(f"[KitsuClient] Could not connect to live Kitsu server ({self.host}): {e}. Using offline mock mode.")
            self.is_connected = False
            return False

    def get_all_projects(self):
        """Returns list of active open projects from Kitsu."""
        if self.gazu and self.is_connected:
            try:
                projects = self.gazu.project.all_open_projects()
                if projects:
                    return projects
            except Exception as e:
                logger.error(f"[KitsuClient] Error fetching projects from Kitsu: {e}")

        # Fallback preset projects if Kitsu server is unreachable
        return [
            {"id": "11111111-1111-1111-1111-111111111111", "name": "Feature Film Alpha", "code": "FFA"},
            {"id": "22222222-2222-2222-2222-222222222222", "name": "Commercial Brand X", "code": "CBX"},
            {"id": "33333333-3333-3333-3333-333333333333", "name": "VFX Demo Showreel", "code": "DEMO"}
        ]

    def create_project(self, project_name, project_code):
        """Creates a new project in Kitsu."""
        if self.gazu and self.is_connected:
            try:
                proj = self.gazu.project.new_project(project_name, code=project_code)
                logger.info(f"[KitsuClient] Created new Kitsu project: '{project_name}' ({project_code})")
                return proj
            except Exception as e:
                logger.error(f"[KitsuClient] Failed to create Kitsu project: {e}")

        # Mock fallback return
        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"proj-{project_code}")),
            "name": project_name,
            "code": project_code
        }

    def get_or_create_sequence(self, project, sequence_name):
        """Fetches sequence or creates it if missing."""
        project_arg = project if isinstance(project, dict) else {"id": str(project)}

        if self.gazu and self.is_connected:
            try:
                seq = self.gazu.shot.get_sequence_by_name(project_arg, sequence_name)
                if not seq:
                    logger.info(f"[Kitsu] Creating new sequence '{sequence_name}'")
                    seq = self.gazu.shot.new_sequence(project_arg, sequence_name)
                return seq
            except Exception as e:
                logger.warning(f"[KitsuClient] gazu sequence error: {e}. Using mock sequence.")

        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"seq-{sequence_name}")),
            "name": sequence_name,
            "project_id": project_arg.get("id")
        }

    def get_or_create_shot(self, project, sequence, shot_name, frame_in=1001, frame_out=1100, fps=24.0, description=""):
        """Fetches shot or creates it with frame range metadata."""
        project_arg = project if isinstance(project, dict) else {"id": str(project)}
        sequence_arg = sequence if isinstance(sequence, dict) else {"id": str(sequence)}

        if self.gazu and self.is_connected:
            try:
                shot = self.gazu.shot.get_shot_by_name(sequence_arg, shot_name)
                if not shot:
                    logger.info(f"[Kitsu] Creating new shot '{shot_name}'")
                    shot = self.gazu.shot.new_shot(
                        project_arg,
                        sequence_arg,
                        shot_name,
                        nb_frames=(frame_out - frame_in + 1),
                        data={"frame_in": frame_in, "frame_out": frame_out, "fps": fps, "description": description}
                    )
                return shot
            except Exception as e:
                logger.warning(f"[KitsuClient] gazu shot error: {e}. Using mock shot.")

        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"shot-{shot_name}")),
            "name": shot_name,
            "sequence_id": sequence_arg.get("id"),
            "nb_frames": frame_out - frame_in + 1,
            "data": {"frame_in": frame_in, "frame_out": frame_out, "fps": fps}
        }

    def create_default_tasks(self, shot, task_types=None):
        """Creates default tasks (Prep, Roto, Comp, 3D) for a shot."""
        shot_arg = shot if isinstance(shot, dict) else {"id": str(shot)}
        task_types = task_types or ["Prep", "Roto", "Matchmove", "3D", "Comp"]
        created_tasks = []

        if self.gazu and self.is_connected:
            try:
                for task_type_name in task_types:
                    tt = self.gazu.task.get_task_type_by_name(task_type_name)
                    if not tt:
                        tt = self.gazu.task.new_task_type(task_type_name)
                    
                    task = self.gazu.task.new_task(shot_arg, tt)
                    created_tasks.append(task)
                return created_tasks
            except Exception as e:
                logger.warning(f"[KitsuClient] gazu task error: {e}. Using mock tasks.")

        for tt in task_types:
            task_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"task-{shot_arg.get('id')}-{tt}"))
            created_tasks.append({"id": task_id, "name": tt, "shot_id": shot_arg.get("id")})
        return created_tasks

    def upload_preview_proxy(self, task, preview_file_path, comment="Plate Ingest Preview"):
        """Uploads a low-res MP4 preview to a Kitsu task."""
        task_arg = task if isinstance(task, dict) else {"id": str(task)}

        if self.gazu and self.is_connected and not self.dry_run:
            try:
                comment_obj = self.gazu.task.add_comment(task_arg, comment)
                preview_obj = self.gazu.task.add_preview(task_arg, comment_obj, preview_file_path)
                logger.info(f"[Kitsu] Uploaded preview to task")
                return preview_obj
            except Exception as e:
                logger.error(f"[Kitsu] Failed to upload preview: {e}")

        logger.info(f"[Mock Kitsu] Uploaded preview proxy '{preview_file_path}' with comment: '{comment}'")
        return {"id": str(uuid.uuid4()), "task_id": task_arg.get("id"), "path": preview_file_path}
