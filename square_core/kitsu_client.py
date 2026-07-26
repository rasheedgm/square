import logging

logger = logging.getLogger("SquareKitsu")

class KitsuClient:
    """Wrapper around CGWire gazu API with mock mode support."""

    def __init__(self, host=None, email=None, password=None, dry_run=True):
        self.host = host or "https://kitsu.squarevfx.com/api"
        self.email = email or "pipeline@squarevfx.com"
        self.password = password or "secret"
        self.dry_run = dry_run
        self.is_connected = False
        self.gazu = None

    def connect(self):
        """Attempts to connect to Kitsu via gazu API."""
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
            logger.warning(f"[KitsuClient] Failed to connect to live Kitsu server: {e}. Falling back to mock mode.")
            self.dry_run = True
            self.is_connected = True
            return False

    def get_all_projects(self):
        """Returns list of active projects."""
        if self.dry_run or not self.gazu:
            return [
                {"id": "proj-001", "name": "Feature Film Alpha", "code": "FFA"},
                {"id": "proj-002", "name": "Commercial Brand X", "code": "CBX"},
                {"id": "proj-003", "name": "VFX Demo Showreel", "code": "DEMO"}
            ]

        return self.gazu.project.all_open_projects()

    def get_or_create_sequence(self, project_id, sequence_name):
        """Fetches sequence or creates it if missing."""
        if self.dry_run or not self.gazu:
            logger.info(f"[Mock Kitsu] Ensured Sequence '{sequence_name}' in project '{project_id}'")
            return {"id": f"seq-{sequence_name}", "name": sequence_name, "project_id": project_id}

        seq = self.gazu.shot.get_sequence_by_name(project_id, sequence_name)
        if not seq:
            logger.info(f"[Kitsu] Creating new sequence '{sequence_name}'")
            seq = self.gazu.shot.new_sequence(project_id, sequence_name)
        return seq

    def get_or_create_shot(self, project_id, sequence_id, shot_name, frame_in=1001, frame_out=1100, fps=24.0, description=""):
        """Fetches shot or creates it with frame range metadata."""
        if self.dry_run or not self.gazu:
            logger.info(f"[Mock Kitsu] Ensured Shot '{shot_name}' (Frames {frame_in}-{frame_out}, {fps} FPS)")
            return {
                "id": f"shot-{shot_name}",
                "name": shot_name,
                "sequence_id": sequence_id,
                "nb_frames": frame_out - frame_in + 1,
                "data": {"frame_in": frame_in, "frame_out": frame_out, "fps": fps}
            }

        shot = self.gazu.shot.get_shot_by_name(sequence_id, shot_name)
        if not shot:
            logger.info(f"[Kitsu] Creating new shot '{shot_name}'")
            shot = self.gazu.shot.new_shot(
                project_id,
                sequence_id,
                shot_name,
                nb_frames=(frame_out - frame_in + 1),
                data={"frame_in": frame_in, "frame_out": frame_out, "fps": fps, "description": description}
            )
        return shot

    def create_default_tasks(self, shot_id, task_types=None):
        """Creates default tasks (Prep, Roto, Comp, 3D) for a shot."""
        task_types = task_types or ["Prep", "Roto", "Matchmove", "3D", "Comp"]
        created_tasks = []

        if self.dry_run or not self.gazu:
            for tt in task_types:
                logger.info(f"[Mock Kitsu] Created Task '{tt}' for Shot '{shot_id}'")
                created_tasks.append({"id": f"task-{shot_id}-{tt}", "name": tt, "shot_id": shot_id})
            return created_tasks

        for task_type_name in task_types:
            tt = self.gazu.task.get_task_type_by_name(task_type_name)
            if not tt:
                tt = self.gazu.task.new_task_type(task_type_name)
            
            task = self.gazu.task.new_task(shot_id, tt)
            created_tasks.append(task)
            
        return created_tasks

    def upload_preview_proxy(self, task_id, preview_file_path, comment="Plate Ingest Preview"):
        """Uploads a low-res MP4 preview to a Kitsu task."""
        if self.dry_run or not self.gazu:
            logger.info(f"[Mock Kitsu] Uploaded preview proxy '{preview_file_path}' to task '{task_id}' with comment: '{comment}'")
            return {"id": "preview-mock-123", "task_id": task_id, "path": preview_file_path}

        try:
            comment_obj = self.gazu.task.add_comment(task_id, comment)
            preview_obj = self.gazu.task.add_preview(task_id, comment_obj, preview_file_path)
            logger.info(f"[Kitsu] Uploaded preview to task {task_id}")
            return preview_obj
        except Exception as e:
            logger.error(f"[Kitsu] Failed to upload preview: {e}")
            return None
