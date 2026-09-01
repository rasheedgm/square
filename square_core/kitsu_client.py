import os
import re
import logging
import uuid

logger = logging.getLogger("SquareKitsu")

_NAME_SEPARATOR_RE = re.compile(r"[\s_\-]+")

class KitsuClient:
    """Wrapper around CGWire gazu API for live Kitsu DB operations."""

    def __init__(self, host=None, email=None, password=None, dry_run=False):
        self.host = host or "http://localhost/api"
        self.email = email or "pipeline@squarevfx.com"
        self.password = password or "secret"
        self.dry_run = dry_run
        self.is_connected = False
        self.gazu = None
        self.last_error = None   # the real exception from the last failed connect(), for surfacing in the UI
        self._mock_projects = [
            {"id": "11111111-1111-1111-1111-111111111111", "name": "Feature Film Alpha", "code": "FFA"},
            {"id": "22222222-2222-2222-2222-222222222222", "name": "Commercial Brand X", "code": "CBX"},
            {"id": "33333333-3333-3333-3333-333333333333", "name": "VFX Demo Showreel", "code": "DEMO"}
        ]

    def connect(self):
        """Attempts to connect to Kitsu via gazu API using configured host and credentials."""
        self.last_error = None
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
            # Kept on the instance (not just logged) so the UI -- Settings'
            # "Test Connection" button, the main window's status indicator --
            # can show the real reason (SSL, DNS, wrong credentials, refused
            # connection, ...) instead of a one-size-fits-all failure message.
            self.last_error = str(e)
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

    def get_or_create_shot(self, project, sequence, shot_name, media_name="PL01", frame_in=1001, frame_out=1100, fps=24.0, resolution="1920x1080", colorspace="ACEScg", nas_path="", description=""):
        """
        Fetches the shot or creates it, with a basic "latest known" snapshot
        for this media (frame range, fps, etc). This is called before the
        version to ingest is even resolved, so it does NOT touch per-version
        history -- record_version() below is the one place that writes a
        version's own ledger entry, and this only ensures media_items[name]
        exists (as {"versions": {}}) without disturbing any that are
        already there.
        """
        project_arg = project if isinstance(project, dict) else {"id": str(project)}
        sequence_arg = sequence if isinstance(sequence, dict) else {"id": str(sequence)}

        latest_snapshot = {
            "media_name": media_name,
            "nas_path": nas_path,
            "frame_range": f"{frame_in}-{frame_out}",
            "fps": fps,
            "resolution": resolution,
            "colorspace": colorspace,
        }

        if self.gazu and self.is_connected:
            try:
                shot = self.gazu.shot.get_shot_by_name(sequence_arg, shot_name)
                if not shot:
                    logger.info(f"[Kitsu Live] Creating new shot '{shot_name}' in Kitsu DB...")
                    shot_data = {
                        "frame_in": frame_in,
                        "frame_out": frame_out,
                        "fps": fps,
                        "media_name": media_name,
                        "resolution": resolution,
                        "colorspace": colorspace,
                        "nas_path": nas_path,
                        "description": description,
                        "media_items": {media_name: {**latest_snapshot, "versions": {}}},
                    }
                    shot = self.gazu.shot.new_shot(
                        project_arg,
                        sequence_arg,
                        shot_name,
                        nb_frames=(frame_out - frame_in + 1),
                        data=shot_data
                    )
                else:
                    logger.info(f"[Kitsu Live] Updating metadata on shot '{shot_name}'...")
                    existing_data = shot.get("data") or {}
                    existing_media_items = dict(existing_data.get("media_items") or {})
                    existing_entry = dict(existing_media_items.get(media_name) or {})
                    existing_versions = existing_entry.get("versions") or {}
                    existing_media_items[media_name] = {**latest_snapshot, "versions": existing_versions}

                    updated_data = {
                        **existing_data,
                        "frame_in": frame_in,
                        "frame_out": frame_out,
                        "fps": fps,
                        "media_name": media_name,
                        "resolution": resolution,
                        "colorspace": colorspace,
                        "nas_path": nas_path,
                        "description": description,
                        "media_items": existing_media_items
                    }
                    try:
                        shot = self.gazu.shot.update_shot_data(shot, updated_data)
                    except Exception as ex:
                        logger.warning(f"[Kitsu Live] Could not update shot_data: {ex}")
                return shot
            except Exception as e:
                logger.error(f"[Kitsu Live Error] Shot error: {e}")

        mock_data = {
            "frame_in": frame_in,
            "frame_out": frame_out,
            "fps": fps,
            "media_name": media_name,
            "resolution": resolution,
            "colorspace": colorspace,
            "nas_path": nas_path,
            "description": description,
            "media_items": {media_name: {**latest_snapshot, "versions": {}}},
        }
        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"shot-{shot_name}")),
            "name": shot_name,
            "sequence_id": sequence_arg.get("id"),
            "nb_frames": frame_out - frame_in + 1,
            "data": mock_data
        }

    def record_version(self, shot, media_name, version_num, entry: dict):
        """
        Writes ONE version's ledger entry into
        shot.data["media_items"][media_name]["versions"]["v###"], merging
        with whatever versions are already recorded there rather than
        replacing them -- get_or_create_shot's own data write used to
        overwrite media_items[media_name] wholesale on every single ingest,
        so the moment v2 was ingested, v1's record was simply gone; there
        was no way to see, from Kitsu, what had actually been delivered
        before. Also stamps media_items[media_name]["latest_version"].

        `entry` is caller-built and opaque here -- the ingest worker fills
        it with nas_path, frame range, fps, resolution, colorspace,
        transfer_mode, checksum, ingested_at, and the Kitsu preview's own
        id/revision once uploaded (or None if this version got a text-only
        comment), so disk version and Kitsu version can be cross-checked
        against each other instead of only ever being implied by comment
        order in a task's activity feed.
        """
        shot_arg = shot if isinstance(shot, dict) else {"id": str(shot)}
        version_key = f"v{int(version_num):03d}"

        existing_data = (shot_arg.get("data") if isinstance(shot_arg, dict) else None) or {}
        media_items = dict(existing_data.get("media_items") or {})
        media_entry = dict(media_items.get(media_name) or {})
        versions = dict(media_entry.get("versions") or {})
        versions[version_key] = entry
        media_entry["versions"] = versions
        media_entry["latest_version"] = version_num
        media_items[media_name] = media_entry
        updated_data = {**existing_data, "media_items": media_items}

        if self.gazu and self.is_connected:
            try:
                task_id = str(shot_arg.get("id", ""))
                if "mock" in task_id or len(task_id) != 36:
                    logger.info(f"[Mock Kitsu] Skipping live version-record for non-UUID shot ID '{task_id}'")
                else:
                    return self.gazu.shot.update_shot_data(shot_arg, updated_data)
            except Exception as e:
                logger.error(f"[Kitsu Live Error] Failed to record version metadata: {e}")

        logger.info(f"[Mock Kitsu] Recorded {version_key} for '{media_name}' on shot {shot_arg.get('id')}")
        return {**shot_arg, "data": updated_data}

    def create_default_tasks(self, shot, task_types=None):
        """Creates default tasks (Ingest, Prep, Roto, Matchmove, 3D, Comp) for a shot in Kitsu."""
        shot_arg = shot if isinstance(shot, dict) else {"id": str(shot)}
        task_types = task_types or ["Ingest", "Prep", "Roto", "Matchmove", "3D", "Comp"]
        created_tasks = []

        if self.gazu and self.is_connected:
            try:
                all_tts = self.gazu.task.all_task_types()
                tt_by_name = {t["name"].lower(): t for t in all_tts} if all_tts else {}

                existing_tasks = self.gazu.task.all_tasks_for_shot(shot_arg)
                existing_type_ids = {t["task_type_id"]: t for t in existing_tasks} if existing_tasks else {}

                for task_type_name in task_types:
                    lower_name = task_type_name.lower()
                    if lower_name in tt_by_name:
                        tt = tt_by_name[lower_name]
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

    # ------------------------------------------------------------------
    # Pre-flight check
    # ------------------------------------------------------------------

    # Per-row outcomes of check_shots(). The two conflict states are the ones
    # that block ingest; the rest are informational.
    KITSU_OK             = "ok"              # shot exists under exactly this sequence
    KITSU_NEW_SHOT       = "new_shot"        # nothing by that name yet -- will be created
    KITSU_WRONG_SEQUENCE = "wrong_sequence"  # exists, but under a different sequence
    KITSU_AMBIGUOUS      = "ambiguous"       # same shot name under several sequences
    KITSU_UNKNOWN        = "unknown"         # could not be checked (offline)

    KITSU_CONFLICT_STATES = (KITSU_WRONG_SEQUENCE, KITSU_AMBIGUOUS)

    def _project_shot_index(self, project):
        """
        {SHOT NAME -> {sequence names it exists under}} for one project.

        The project shot listing does NOT reliably carry the sequence link in
        a single field: on the Zou we test against, every shot comes back
        with sequence_id == None and sequence_name == None, and the parent
        sequence is in `parent_id` instead. So the sequence name is resolved
        through the project's sequence list, keyed by whichever of
        sequence_id / parent_id is actually populated -- without the
        parent_id fallback every existing shot indexed under sequence "",
        making every real shot read as a wrong-sequence conflict.
        """
        shots = self.gazu.shot.all_shots_for_project(project) or []

        seq_names = {}
        if any(not s.get("sequence_name") for s in shots):
            for seq in (self.gazu.shot.all_sequences_for_project(project) or []):
                seq_names[seq.get("id")] = seq.get("name") or ""

        index = {}
        for shot in shots:
            name = (shot.get("name") or "").strip()
            if not name:
                continue
            seq_id = shot.get("sequence_id") or shot.get("parent_id")
            seq_name = shot.get("sequence_name") or seq_names.get(seq_id, "")
            index.setdefault(name.upper(), set()).add((seq_name or "").strip().upper())
        return index

    def check_shots(self, project, rows):
        """
        Pre-flight the table against Kitsu, before anything is written.

        `rows` is an iterable of (sequence_code, shot_code) pairs. Returns
        {(SEQ, SHOT): {"state": ..., "message": ..., "sequences": [...]}},
        keyed upper-cased so rows sharing a shot share one result.

        The question this answers is the one the NAS check cannot: the shot
        code a client's folder structure gave us may already live under a
        DIFFERENT sequence in Kitsu. Ingesting then either attaches the media
        to the wrong shot or creates a duplicate -- both need a human to fix
        the Sequence column, so those two states are conflicts. A shot that
        simply doesn't exist yet is normal and is only reported for
        information.
        """
        pairs = []
        seen = set()
        for seq_code, shot_code in rows:
            key = ((seq_code or "").strip().upper(), (shot_code or "").strip().upper())
            if key[1] and key not in seen:
                seen.add(key)
                pairs.append(key)

        if not pairs:
            return {}

        if not (self.gazu and self.is_connected):
            return {
                key: {
                    "state": self.KITSU_UNKNOWN,
                    "message": "Not connected to Kitsu — shots could not be checked.",
                    "sequences": [],
                }
                for key in pairs
            }

        project_arg = project if isinstance(project, dict) else {"id": str(project)}
        try:
            index = self._project_shot_index(project_arg)
        except Exception as e:
            logger.error(f"[KitsuClient] Shot pre-flight failed: {e}")
            self.last_error = str(e)
            return {
                key: {
                    "state": self.KITSU_UNKNOWN,
                    "message": f"Kitsu check failed: {e}",
                    "sequences": [],
                }
                for key in pairs
            }

        report = {}
        for seq_up, shot_up in pairs:
            found = index.get(shot_up)
            if not found:
                report[(seq_up, shot_up)] = {
                    "state": self.KITSU_NEW_SHOT,
                    "message": f"'{shot_up}' does not exist in Kitsu yet — it will be created under '{seq_up}'.",
                    "sequences": [],
                }
            elif len(found) > 1:
                others = sorted(found)
                report[(seq_up, shot_up)] = {
                    "state": self.KITSU_AMBIGUOUS,
                    "message": (
                        f"'{shot_up}' exists under several sequences in Kitsu ({', '.join(others)}). "
                        "Set the Sequence column to the right one before ingesting."
                    ),
                    "sequences": others,
                }
            elif seq_up in found:
                report[(seq_up, shot_up)] = {
                    "state": self.KITSU_OK,
                    "message": f"'{seq_up} / {shot_up}' matches Kitsu.",
                    "sequences": sorted(found),
                }
            else:
                other = sorted(found)[0]
                report[(seq_up, shot_up)] = {
                    "state": self.KITSU_WRONG_SEQUENCE,
                    "message": (
                        f"Kitsu has '{shot_up}' under sequence '{other}', not '{seq_up}'. "
                        "Ingesting now would create a second shot with the same name."
                    ),
                    "sequences": sorted(found),
                }
        return report

    @staticmethod
    def _normalize_name(name):
        """
        Strips separators and case so 'SH0100', 'SH_0100' and 'sh-01 00' all
        compare equal -- used only to catch a likely formatting slip, never
        to decide two names ARE the same (that's still an exact match).
        """
        return _NAME_SEPARATOR_RE.sub("", (name or "")).strip().upper()

    def check_naming_conflicts(self, project, rows):
        """
        Pre-flight rows for a near-duplicate NAME already in Kitsu -- a
        client folder called "SH_0100" when Kitsu already has "SH0100" is
        almost always the same shot with a formatting slip, not a
        deliberately different one, and check_shots() only matches exact
        (case-insensitive) names so it would wave this straight through as
        "new shot, will be created," silently producing a confusing
        near-duplicate.

        `rows` is an iterable of (sequence_code, shot_code, media_name).
        Checks, per row, shot name against every shot in the project,
        sequence name against every sequence, and media name against the
        OTHER media already recorded on that same shot (media names aren't
        project-global, only meaningful per shot) -- shot first, then
        sequence, then media name; a row gets at most one finding, the
        highest-priority one that applies.

        An EXACT match is never flagged here (that's fine, or it's
        check_shots' wrong-sequence/ambiguous territory). A name with
        nothing close to it in Kitsu at all is not flagged either -- that's
        just a genuinely new name.

        Returns {(SEQ, SHOT, MEDIA_NAME): {"field", "existing", "message"}}
        for only the rows with a finding.
        """
        pairs = []
        seen = set()
        for seq_code, shot_code, media_name in rows:
            key = (
                (seq_code or "").strip().upper(),
                (shot_code or "").strip().upper(),
                (media_name or "").strip().upper(),
            )
            if key[1] and key not in seen:
                seen.add(key)
                pairs.append(key)

        if not pairs or not (self.gazu and self.is_connected):
            return {}

        project_arg = project if isinstance(project, dict) else {"id": str(project)}
        try:
            shots = self.gazu.shot.all_shots_for_project(project_arg) or []
            sequences = self.gazu.shot.all_sequences_for_project(project_arg) or []
        except Exception as e:
            logger.error(f"[KitsuClient] Naming pre-flight failed: {e}")
            self.last_error = str(e)
            return {}

        shot_exact, shot_norm = set(), {}
        for shot in shots:
            name = (shot.get("name") or "").strip()
            if not name:
                continue
            shot_exact.add(name.upper())
            shot_norm.setdefault(self._normalize_name(name), set()).add(name)

        seq_exact, seq_norm = set(), {}
        for seq in sequences:
            name = (seq.get("name") or "").strip()
            if not name:
                continue
            seq_exact.add(name.upper())
            seq_norm.setdefault(self._normalize_name(name), set()).add(name)

        # media names are only meaningful scoped to their own shot -- keyed
        # SHOT_NAME_UPPER -> {"exact": {...}, "norm": {normalized: {names}}}.
        # Relies on the project shot listing already carrying each shot's
        # data.media_items (true for a real Kitsu server's listing
        # endpoint); a shot with no media_items recorded yet just yields no
        # media-name finding for rows against it -- fails safe, not wrong.
        media_by_shot = {}
        for shot in shots:
            shot_name = (shot.get("name") or "").strip().upper()
            if not shot_name:
                continue
            media_items = ((shot.get("data") or {}).get("media_items") or {})
            exact = {m.upper() for m in media_items.keys()}
            norm = {}
            for m in media_items.keys():
                norm.setdefault(self._normalize_name(m), set()).add(m)
            media_by_shot[shot_name] = {"exact": exact, "norm": norm}

        report = {}
        for seq_up, shot_up, media_up in pairs:
            finding = None

            if shot_up not in shot_exact:
                close = shot_norm.get(self._normalize_name(shot_up))
                if close:
                    existing = sorted(close)
                    finding = {
                        "field": "shot",
                        "existing": existing,
                        "message": (
                            f"No shot named exactly '{shot_up}' in Kitsu, but "
                            f"'{', '.join(existing)}' is a near-identical name already there "
                            "(different spacing/underscore/case). Likely the same shot -- "
                            "fix the Shot name to match exactly, or ignore if it's really different."
                        ),
                    }

            if finding is None and seq_up not in seq_exact:
                close = seq_norm.get(self._normalize_name(seq_up))
                if close:
                    existing = sorted(close)
                    finding = {
                        "field": "sequence",
                        "existing": existing,
                        "message": (
                            f"No sequence named exactly '{seq_up}' in Kitsu, but "
                            f"'{', '.join(existing)}' is a near-identical name already there. "
                            "Likely the same sequence -- fix the Sequence name to match "
                            "exactly, or ignore if it's really different."
                        ),
                    }

            if finding is None and media_up and shot_up in media_by_shot:
                info = media_by_shot[shot_up]
                if media_up not in info["exact"]:
                    close = info["norm"].get(self._normalize_name(media_up))
                    if close:
                        existing = sorted(close)
                        finding = {
                            "field": "media_name",
                            "existing": existing,
                            "message": (
                                f"Shot '{shot_up}' already has media named '{', '.join(existing)}' "
                                f"in Kitsu, close to this row's '{media_up}' (different spacing/"
                                "underscore/case). Likely the same media -- fix the Media Name to "
                                "match exactly, or ignore if it's really different."
                            ),
                        }

            if finding:
                report[(seq_up, shot_up, media_up)] = finding

        return report

    @staticmethod
    def build_version_comment(item, version_num, dest_dir, transfer_mode="copy", checksum=None):
        """
        Builds a self-describing comment body for one ingested version --
        posted on every version (with or without a preview attached), so
        each version's own record carries its copied path and details
        instead of only the shot-level data blob being updated.
        """
        lines = [
            f"Media Ingest v{version_num:03d} ({getattr(item, 'media_name', '')})",
            "",
            f"NAS Path: {dest_dir}",
            f"Media Type: {getattr(item, 'media_type', '') or 'Plate'}",
            f"Resolution: {getattr(item, 'resolution', '')} | FPS: {getattr(item, 'fps', '')} | Colorspace: {getattr(item, 'colorspace', '')}",
            f"Frame Range: {item.frame_range_str if hasattr(item, 'frame_range_str') else ''}",
            f"Transfer Mode: {transfer_mode}",
        ]
        if checksum:
            lines.append(f"Checksum (first file, xxHash/MD5): {checksum}")
        if getattr(item, "files", None):
            lines.append(f"Source: {os.path.basename(item.files[0])}")
        extra_tags = getattr(item, "extra_tags", None)
        if extra_tags:
            lines.append("Tags: " + ", ".join(f"{k}={v}" for k, v in extra_tags.items()))
        return "\n".join(lines)

    def add_version_comment(self, task, comment):
        """
        Posts a text-only metadata comment to a task -- used for media
        types that don't get a preview generated, so every ingested
        version still gets its own self-describing Kitsu record even
        without a video attached.
        """
        task_arg = task if isinstance(task, dict) else {"id": str(task)}

        if self.gazu and self.is_connected:
            try:
                task_id = str(task_arg.get("id", ""))
                if "mock" in task_id or len(task_id) != 36:
                    logger.info(f"[Mock Kitsu] Skipping live comment for non-UUID task ID '{task_id}'")
                    return {"id": str(uuid.uuid4()), "task_id": task_id, "comment": comment}

                status = task_arg.get("task_status_id") or self.gazu.task.get_default_task_status()
                if not status:
                    statuses = self.gazu.task.all_task_statuses()
                    status = statuses[0] if statuses else "todo"

                return self.gazu.task.add_comment(task_arg, status, comment=comment)
            except Exception as e:
                logger.error(f"[Kitsu Live Error] Failed to add version comment: {e}")

        logger.info(f"[Mock Kitsu] Added version metadata comment to task {task_arg.get('id')}")
        return {"id": str(uuid.uuid4()), "task_id": task_arg.get("id"), "comment": comment}

    def upload_preview_proxy(self, task, preview_file_path, comment="Media Ingest Preview v001"):
        """Uploads a low-res MP4 preview to a Kitsu task and registers it as the main Shot Thumbnail."""
        task_arg = task if isinstance(task, dict) else {"id": str(task)}

        if self.gazu and self.is_connected and os.path.exists(preview_file_path):
            try:
                task_id = str(task_arg.get("id", ""))
                if "mock" in task_id or len(task_id) != 36:
                    logger.info(f"[Mock Kitsu] Skipping live upload for non-UUID task ID '{task_id}'")
                    return {"id": str(uuid.uuid4()), "task_id": task_id, "path": preview_file_path}

                logger.info(f"[Kitsu Live] Uploading preview proxy file to Kitsu task...")
                
                status = task_arg.get("task_status_id") or self.gazu.task.get_default_task_status()
                if not status:
                    statuses = self.gazu.task.all_task_statuses()
                    status = statuses[0] if statuses else "todo"

                comment_obj = self.gazu.task.add_comment(task_arg, status, comment=comment)
                preview_obj = self.gazu.task.add_preview(task_arg, comment_obj, preview_file_path)
                
                # Register preview as main Shot Thumbnail in Kitsu
                try:
                    self.gazu.task.set_main_preview(preview_obj)
                    logger.info(f"[Kitsu Live] Set main preview thumbnail for Shot!")
                except Exception as ex_thumb:
                    logger.warning(f"[Kitsu Live] Could not set main preview thumbnail: {ex_thumb}")

                return preview_obj
            except Exception as e:
                logger.error(f"[Kitsu Live Error] Failed to upload preview to Kitsu: {e}")

        logger.info(f"[Mock Kitsu] Uploaded preview proxy '{preview_file_path}'")
        return {"id": str(uuid.uuid4()), "task_id": task_arg.get("id"), "path": preview_file_path}

    # Keys we write into the preview file's free-form `data` blob. Kept as a
    # namespaced sub-dict (data["square_ingest"]) so it can never collide with
    # the media metadata Zou itself writes there on upload (original_width,
    # original_height, original_duration, ...).
    PREVIEW_METADATA_KEY = "square_ingest"

    def attach_preview_source_metadata(self, preview_file, source_info: dict):
        """
        Stamps `source_info` (NAS path, a real sample filename, frame range,
        checksum, ...) onto the preview file's OWN record.

        Zou's preview-file model has no columns for any of these fields and
        silently drops unknown top-level keys on PUT -- update_preview({"nas_path":
        ...}) returns 200 and stores nothing. The one writable free-form field
        is `data` (JSONB), which Zou also uses for the media dimensions it
        extracts on upload. So the source info goes into
        data["square_ingest"], merged on top of whatever `data` already holds
        rather than replacing it.

        The reason this lives on the preview file rather than only in our own
        shot-data ledger (record_version): a review or delivery tool that
        queries Kitsu "task X, revision N" -- which is how a preview file is
        addressed; Kitsu's revision numbering belongs to the preview file,
        not to the shot -- gets this metadata back on that SAME object it
        already fetched, in the one query, instead of needing a second
        lookup against a side-channel blob it would have to know exists.
        """
        preview_arg = preview_file if isinstance(preview_file, dict) else {"id": str(preview_file)}
        preview_id = str(preview_arg.get("id", ""))

        if self.gazu and self.is_connected:
            try:
                if "mock" in preview_id or len(preview_id) != 36:
                    logger.info(
                        f"[Mock Kitsu] Skipping live preview-metadata update for non-UUID preview ID '{preview_id}'"
                    )
                else:
                    existing_data = {}
                    try:
                        current = self.gazu.files.get_preview_file(preview_id) or {}
                        existing_data = dict(current.get("data") or {})
                    except Exception as ex:
                        logger.warning(f"[Kitsu Live] Could not read preview file before update: {ex}")
                        existing_data = dict(preview_arg.get("data") or {})
                    existing_data[self.PREVIEW_METADATA_KEY] = source_info
                    return self.gazu.files.update_preview(preview_arg, {"data": existing_data})
            except Exception as e:
                logger.error(f"[Kitsu Live Error] Failed to attach source metadata to preview: {e}")

        logger.info(f"[Mock Kitsu] Attached source metadata to preview {preview_id}")
        merged_data = dict(preview_arg.get("data") or {})
        merged_data[self.PREVIEW_METADATA_KEY] = source_info
        return {**preview_arg, "data": merged_data}
