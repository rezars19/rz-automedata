"""
RZ Automedata — Google Drive Bridge
Manages ALL communication with Colab through Google Drive.
No ngrok, no tunnels — purely filesystem-based.

Folder structure in Google Drive:
    {My Drive}/RZ_Upscaler/
        Input/     ← Desktop app copies videos here
        Output/    ← Colab saves upscaled results here
        Jobs/      ← Desktop writes job JSON files, Colab reads them
        Status/    ← Colab writes status JSON files, Desktop reads them

Flow:
    1. copy_to_input()     → Copy video from PC to Drive Input folder
    2. write_job()         → Write job.json with processing instructions
    3. (Google Drive syncs to cloud → Colab reads from Drive mount)
    4. (Colab picks up job, processes video, updates status.json)
    5. read_status()       → Desktop reads status.json for progress + logs
    6. watch_for_output()  → Wait for output file to sync back to PC
    7. save_to_final()     → Copy from Drive to user's chosen output folder
    8. cleanup_task()      → Delete all task files from Drive
"""

import os
import json
import shutil
import time
import uuid
import string
import logging

logger = logging.getLogger(__name__)


class GDriveBridge:
    """
    Manages ALL communication with Colab through Google Drive.
    No network code — purely local filesystem operations.
    Google Drive for Desktop handles the sync automatically.
    """

    SUBFOLDER = "RZ_Upscaler"
    INPUT_DIR = "Input"
    OUTPUT_DIR = "Output"
    JOBS_DIR = "Jobs"
    STATUS_DIR = "Status"

    def __init__(self, gdrive_local_path=None):
        self.gdrive_path = None
        self._input_dir = None
        self._output_dir = None
        self._jobs_dir = None
        self._status_dir = None

        if gdrive_local_path:
            try:
                self.set_drive_path(gdrive_local_path)
            except Exception:
                pass

    # ─── Properties ──────────────────────────────────────────────────────

    @property
    def is_configured(self):
        return (
            self.gdrive_path is not None
            and os.path.isdir(self.gdrive_path)
            and self._input_dir is not None
        )

    @property
    def input_dir(self):
        return self._input_dir

    @property
    def output_dir(self):
        return self._output_dir

    @property
    def jobs_dir(self):
        return self._jobs_dir

    @property
    def status_dir(self):
        return self._status_dir

    # ─── Configuration ───────────────────────────────────────────────────

    def set_drive_path(self, path):
        """Set and validate the Google Drive local folder path."""
        path = path.strip()
        if not path:
            raise ValueError("Path cannot be empty")
        if not os.path.isdir(path):
            raise FileNotFoundError(f"Folder not found: {path}")

        self.gdrive_path = path
        base = os.path.join(path, self.SUBFOLDER)
        self._input_dir = os.path.join(base, self.INPUT_DIR)
        self._output_dir = os.path.join(base, self.OUTPUT_DIR)
        self._jobs_dir = os.path.join(base, self.JOBS_DIR)
        self._status_dir = os.path.join(base, self.STATUS_DIR)

        os.makedirs(self._input_dir, exist_ok=True)
        os.makedirs(self._output_dir, exist_ok=True)
        os.makedirs(self._jobs_dir, exist_ok=True)
        os.makedirs(self._status_dir, exist_ok=True)

        logger.info(f"Google Drive path set: {path}")
        return True

    @staticmethod
    def generate_task_id():
        """Generate a short unique task ID (8 hex chars)."""
        return uuid.uuid4().hex[:8]

    # ─── Job & Status Files ──────────────────────────────────────────────

    def write_job(self, task_id, filename, scale=4, model="realesr-animevideov3",
                  face_enhance=False, mute_audio=False, output_format="mp4",
                  target_fps=30):
        """
        Write a job JSON file to the Jobs/ folder.
        Colab will pick this up and start processing.
        """
        if not self.is_configured:
            raise RuntimeError("Google Drive path not configured.")

        job = {
            "task_id": task_id,
            "filename": filename,
            "scale": int(scale),
            "model": model,
            "face_enhance": bool(face_enhance),
            "mute_audio": bool(mute_audio),
            "output_format": str(output_format).lower(),
            "target_fps": int(target_fps),
            "created_at": time.time(),
        }

        job_path = os.path.join(self._jobs_dir, f"{task_id}.json")
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Also touch the parent directory to nudge GDrive Desktop sync
        try:
            dir_fd = os.open(self._jobs_dir, os.O_RDONLY)
            os.fsync(dir_fd)
            os.close(dir_fd)
        except (OSError, PermissionError):
            pass

        logger.info(f"Job written: {task_id}.json (model={model}, scale={scale})")

    def read_status(self, task_id):
        """
        Read the status JSON file for a task.

        Returns dict with:
            status, progress, stage, error, log (list of strings)
        Or None if file not found yet.
        """
        if not self.is_configured:
            return None

        status_path = os.path.join(self._status_dir, f"{task_id}.json")
        if not os.path.exists(status_path):
            return None

        try:
            with open(status_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Status read error for {task_id}: {e}")
            return None

    # ─── File Operations ─────────────────────────────────────────────────

    def copy_to_input(self, source_path, task_id, progress_cb=None):
        """
        Copy a video file to the Google Drive Input folder.

        Returns: Filename placed in the Input folder (e.g. "a1b2c3d4_myvideo.mp4")
        """
        if not self.is_configured:
            raise RuntimeError("Google Drive path not configured. Please set it first.")
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"Source file not found: {source_path}")

        original_name = os.path.basename(source_path)
        safe_name = "".join(
            c if (c.isalnum() or c in ".-_") else "_"
            for c in original_name
        )
        dest_name = f"{task_id}_{safe_name}"
        dest_path = os.path.join(self._input_dir, dest_name)

        total_size = os.path.getsize(source_path)

        if progress_cb:
            copied = 0
            with open(source_path, "rb") as fsrc, open(dest_path, "wb") as fdst:
                while True:
                    chunk = fsrc.read(1024 * 1024)
                    if not chunk:
                        break
                    fdst.write(chunk)
                    copied += len(chunk)
                    progress_cb(copied, total_size)
                fdst.flush()
                os.fsync(fdst.fileno())
        else:
            shutil.copy2(source_path, dest_path)

        # Nudge GDrive Desktop to sync faster
        try:
            dir_fd = os.open(self._input_dir, os.O_RDONLY)
            os.fsync(dir_fd)
            os.close(dir_fd)
        except (OSError, PermissionError):
            pass

        logger.info(f"Copied to Drive Input: {dest_name} ({total_size / (1024*1024):.1f} MB)")
        return dest_name

    def watch_for_output(self, task_id, output_format, timeout=7200, poll_interval=3,
                         download_progress_cb=None):
        """Watch for the completed output file in Drive Output folder.
        download_progress_cb is accepted for API compatibility but not used
        in filesystem mode (file is already local via Google Drive for Desktop).
        """
        if not self.is_configured:
            return None

        expected_name = f"{task_id}_UPSCALED.{output_format}"
        expected_path = os.path.join(self._output_dir, expected_name)

        start_time = time.time()
        prev_size = -1
        stable_count = 0

        while (time.time() - start_time) < timeout:
            if os.path.exists(expected_path):
                try:
                    current_size = os.path.getsize(expected_path)
                    if current_size > 0:
                        if current_size == prev_size:
                            stable_count += 1
                            if stable_count >= 3:
                                logger.info(f"Output synced: {expected_name} ({current_size / (1024*1024):.1f} MB)")
                                if download_progress_cb:
                                    download_progress_cb(1.0)  # signal 100%
                                return expected_path
                        else:
                            stable_count = 0
                            if download_progress_cb and prev_size > 0:
                                # Rough estimate of sync progress
                                download_progress_cb(0.5)
                        prev_size = current_size
                except OSError:
                    pass
            time.sleep(poll_interval)

        logger.warning(f"Timeout waiting for output: {expected_name}")
        return None

    def save_to_final(self, drive_output_path, final_output_dir, original_filename, output_format):
        """Copy completed file from Google Drive to user's final output folder."""
        name_base = os.path.splitext(original_filename)[0]
        final_name = f"UPSCALED_{name_base}.{output_format}"
        final_path = os.path.join(final_output_dir, final_name)

        counter = 1
        while os.path.exists(final_path):
            final_name = f"UPSCALED_{name_base}_{counter}.{output_format}"
            final_path = os.path.join(final_output_dir, final_name)
            counter += 1

        os.makedirs(final_output_dir, exist_ok=True)
        shutil.copy2(drive_output_path, final_path)
        logger.info(f"Saved to output: {final_path}")
        return final_path

    def cleanup_task(self, task_id):
        """Remove all files for a given task from Google Drive."""
        cleaned = 0
        for d in [self._input_dir, self._output_dir, self._jobs_dir, self._status_dir]:
            if d and os.path.isdir(d):
                for filename in os.listdir(d):
                    if filename.startswith(task_id):
                        try:
                            os.remove(os.path.join(d, filename))
                            cleaned += 1
                        except Exception as e:
                            logger.warning(f"Cleanup failed for {filename}: {e}")
        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} file(s) for task {task_id}")

    # ─── Auto-Detection ──────────────────────────────────────────────────

    @staticmethod
    def detect_gdrive_paths():
        """Auto-detect Google Drive local folder paths on Windows."""
        candidates = []
        for letter in string.ascii_uppercase:
            my_drive = f"{letter}:\\My Drive"
            if os.path.isdir(my_drive):
                candidates.append(my_drive)
            for name in ["Mon Drive", "Mi unidad", "Meine Ablage"]:
                p = f"{letter}:\\{name}"
                if os.path.isdir(p):
                    candidates.append(p)

        home = os.environ.get("USERPROFILE", "")
        if home:
            for sub in [
                "Google Drive\\My Drive",
                "Google Drive",
                "GoogleDrive\\My Drive",
                "GoogleDrive",
            ]:
                p = os.path.join(home, sub)
                if os.path.isdir(p):
                    candidates.append(p)

        seen = set()
        unique = []
        for c in candidates:
            norm = os.path.normpath(c).lower()
            if norm not in seen:
                seen.add(norm)
                unique.append(c)
        return unique
