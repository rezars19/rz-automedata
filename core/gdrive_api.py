"""
RZ Automedata — Google Drive API Bridge
Direct cloud upload/download via Google Drive REST API.
No Google Drive for Desktop needed — no sync delays!

Folder structure in Google Drive:
    {My Drive}/RZ_Upscaler/
        Input/     ← Desktop app uploads videos here (via API)
        Output/    ← Colab saves upscaled results here
        Jobs/      ← Desktop uploads job JSON files, Colab reads them
        Status/    ← Colab writes status JSON files, Desktop reads them (via API)

Usage:
    User clicks "Login Google" → browser opens → authorize → done!
    Token saved locally, auto-refresh on next launch.
"""

import os
import io
import json
import time
import uuid
import logging
import threading
import tempfile

logger = logging.getLogger(__name__)

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    HAS_GDRIVE_API = True
except ImportError:
    HAS_GDRIVE_API = False
    logger.warning("Google Drive API libraries not installed. "
                    "Run: pip install google-api-python-client google-auth-oauthlib")

# IMPORTANT: Must use 'drive' scope (not 'drive.file') so the app can read
# files created by Colab (status JSONs, output files). 'drive.file' only
# allows access to files the app itself created.
SCOPES = ["https://www.googleapis.com/auth/drive"]

# ── OAuth 2.0 credentials ────────────────────────────────────────────────
# Store in %APPDATA%/RZAutomedata/ so they persist across app updates.
# Same location as license and database files.
_DATA_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "RZAutomedata"
)
os.makedirs(_DATA_DIR, exist_ok=True)
_CREDS_CONFIG_FILE = os.path.join(_DATA_DIR, ".gdrive_creds.json")
_TOKEN_FILE = os.path.join(_DATA_DIR, ".gdrive_token.json")

# Bundled default credentials — members don't need to enter Client ID/Secret.
# These are loaded automatically if no saved credentials exist.
_BUNDLED_CLIENT_ID = "125900541309-mbrlramr268abk5ehf4h9vfe76rummo9.apps.googleusercontent.com"
_BUNDLED_CLIENT_SECRET = "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"


def _load_saved_credentials():
    """Load Client ID & Secret from config file, or use bundled defaults."""
    # Try saved credentials first
    if os.path.exists(_CREDS_CONFIG_FILE):
        try:
            with open(_CREDS_CONFIG_FILE, "r") as f:
                data = json.load(f)
            cid = data.get("client_id", "")
            csec = data.get("client_secret", "")
            if cid and csec:
                return cid, csec
        except Exception:
            pass
    # Fall back to bundled defaults
    if _BUNDLED_CLIENT_ID and _BUNDLED_CLIENT_SECRET:
        return _BUNDLED_CLIENT_ID, _BUNDLED_CLIENT_SECRET
    return "", ""


def _save_credentials(client_id, client_secret):
    """Save Client ID & Secret to config file."""
    with open(_CREDS_CONFIG_FILE, "w") as f:
        json.dump({"client_id": client_id.strip(), "client_secret": client_secret.strip()}, f)


def _build_client_config(client_id, client_secret):
    """Build OAuth client config dict from Client ID & Secret."""
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


class GDriveAPI:
    """
    Google Drive API bridge — uploads/downloads directly to cloud.
    No Google Drive for Desktop required. No sync delays.
    Users paste Client ID & Secret in the app → click Login → done!
    """

    SUBFOLDER = "RZ_Upscaler"
    INPUT_DIR = "Input"
    OUTPUT_DIR = "Output"
    JOBS_DIR = "Jobs"
    STATUS_DIR = "Status"

    def __init__(self):
        self._service = None
        self._creds = None
        self._folder_ids = {}   # {"RZ_Upscaler": id, "Input": id, ...}
        self._lock = threading.Lock()
        self._user_email = None

    # ─── Properties ──────────────────────────────────────────────────────

    @property
    def is_configured(self):
        return self._service is not None and bool(self._folder_ids.get(self.SUBFOLDER))

    @property
    def gdrive_path(self):
        """Compatibility with old bridge — returns descriptive string."""
        if self._user_email:
            return f"☁ {self._user_email} (API)"
        return "☁ Google Drive API" if self.is_configured else None

    @property
    def input_dir(self):
        return self._folder_ids.get(self.INPUT_DIR)

    @property
    def output_dir(self):
        return self._folder_ids.get(self.OUTPUT_DIR)

    @property
    def jobs_dir(self):
        return self._folder_ids.get(self.JOBS_DIR)

    @property
    def status_dir(self):
        return self._folder_ids.get(self.STATUS_DIR)

    @staticmethod
    def has_credentials():
        """Check if Client ID & Secret have been configured (saved or bundled)."""
        cid, csec = _load_saved_credentials()
        return bool(cid) and bool(csec)

    @staticmethod
    def has_bundled_credentials():
        """Check if default credentials are bundled in the app."""
        return bool(_BUNDLED_CLIENT_ID) and bool(_BUNDLED_CLIENT_SECRET)

    @staticmethod
    def get_saved_credentials():
        """Return saved (client_id, client_secret) tuple."""
        return _load_saved_credentials()

    @staticmethod
    def save_credentials(client_id, client_secret):
        """Save Client ID & Secret (called from UI)."""
        _save_credentials(client_id, client_secret)
        # Clear old token if credentials changed
        if os.path.exists(_TOKEN_FILE):
            os.remove(_TOKEN_FILE)

    @staticmethod
    def has_saved_token():
        return os.path.exists(_TOKEN_FILE)

    # ─── Authentication ──────────────────────────────────────────────────

    def authenticate(self, force_new=False):
        """
        Authenticate with Google Drive API.
        First time: opens browser for OAuth consent.
        Subsequent: uses saved token (auto-refresh).
        """
        if not HAS_GDRIVE_API:
            raise RuntimeError(
                "Google Drive API libraries not installed.\n"
                "Run: pip install google-api-python-client google-auth-oauthlib"
            )

        client_id, client_secret = _load_saved_credentials()
        if not client_id or not client_secret:
            raise RuntimeError(
                "Client ID & Secret belum diisi!\n\n"
                "Paste di sidebar Upscaler → bagian Google Drive."
            )

        creds = None

        # Load existing token
        if not force_new and os.path.exists(_TOKEN_FILE):
            try:
                creds = Credentials.from_authorized_user_file(_TOKEN_FILE, SCOPES)
                # Check if token has old scope (drive.file) — force re-auth
                if creds and creds.scopes:
                    old_scopes = set(creds.scopes)
                    needed_scopes = set(SCOPES)
                    if not needed_scopes.issubset(old_scopes):
                        logger.info("Token has old scopes, forcing re-authentication")
                        creds = None
            except Exception:
                creds = None

        # Refresh or get new token
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            # Delete old token file to force fresh auth with new scopes
            if os.path.exists(_TOKEN_FILE):
                try:
                    os.remove(_TOKEN_FILE)
                except Exception:
                    pass
            # Build config from saved Client ID & Secret
            config = _build_client_config(client_id, client_secret)
            flow = InstalledAppFlow.from_client_config(config, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)

        # Save token for next time
        with open(_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

        self._creds = creds
        self._service = build("drive", "v3", credentials=creds)

        # Get user email
        try:
            about = self._service.about().get(fields="user").execute()
            self._user_email = about.get("user", {}).get("emailAddress", "")
        except Exception:
            self._user_email = ""

        # Ensure folder structure exists
        self._ensure_folders()

        logger.info(f"Google Drive API authenticated: {self._user_email}")
        return True

    def logout(self):
        """Clear saved token."""
        self._service = None
        self._creds = None
        self._folder_ids = {}
        self._user_email = None
        if os.path.exists(_TOKEN_FILE):
            os.remove(_TOKEN_FILE)
        logger.info("Google Drive API: logged out")

    # ─── Folder Management ───────────────────────────────────────────────

    def _ensure_folders(self):
        """Create RZ_Upscaler folder structure in Drive if needed."""
        root_id = self._find_or_create_folder(self.SUBFOLDER, parent_id="root")
        self._folder_ids[self.SUBFOLDER] = root_id

        for sub in [self.INPUT_DIR, self.OUTPUT_DIR, self.JOBS_DIR, self.STATUS_DIR]:
            fid = self._find_or_create_folder(sub, parent_id=root_id)
            self._folder_ids[sub] = fid

        logger.info(f"Drive folders ready: {list(self._folder_ids.keys())}")

    def _find_or_create_folder(self, name, parent_id="root"):
        """Find folder by name under parent, or create it."""
        query = (
            f"name = '{name}' and "
            f"'{parent_id}' in parents and "
            f"mimeType = 'application/vnd.google-apps.folder' and "
            f"trashed = false"
        )
        results = self._service.files().list(
            q=query, fields="files(id, name)", pageSize=1
        ).execute()
        files = results.get("files", [])

        if files:
            return files[0]["id"]

        # Create folder
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = self._service.files().create(
            body=metadata, fields="id"
        ).execute()
        logger.info(f"Created Drive folder: {name}")
        return folder["id"]

    # ─── Job & Status Files ──────────────────────────────────────────────

    def write_job(self, task_id, filename, scale=4, model="realesr-animevideov3",
                  face_enhance=False, mute_audio=False, output_format="mp4",
                  target_fps=30):
        """Upload a job JSON file to Jobs/ folder."""
        if not self.is_configured:
            raise RuntimeError("Google Drive API not authenticated.")

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

        jobs_folder_id = self._folder_ids[self.JOBS_DIR]
        json_bytes = json.dumps(job, indent=2).encode("utf-8")

        metadata = {
            "name": f"{task_id}.json",
            "parents": [jobs_folder_id],
        }
        media = MediaFileUpload(
            self._write_temp(json_bytes),
            mimetype="application/json",
            resumable=False,
        )
        self._service.files().create(
            body=metadata, media_body=media, fields="id"
        ).execute()

        logger.info(f"Job uploaded: {task_id}.json (model={model}, scale={scale})")

    def read_status(self, task_id):
        """Download and read the status JSON file for a task."""
        if not self.is_configured:
            return None

        status_folder_id = self._folder_ids[self.STATUS_DIR]
        file_id = self._find_file(f"{task_id}.json", status_folder_id)
        if not file_id:
            return None

        try:
            content = self._download_content(file_id)
            return json.loads(content)
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"Status read error for {task_id}: {e}")
            return None

    # ─── File Operations ─────────────────────────────────────────────────

    def copy_to_input(self, source_path, task_id, progress_cb=None):
        """
        Upload a file to the Google Drive Input folder via API.
        Returns: Filename placed in the Input folder.
        """
        if not self.is_configured:
            raise RuntimeError("Google Drive API not authenticated.")
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"Source file not found: {source_path}")

        original_name = os.path.basename(source_path)
        safe_name = "".join(
            c if (c.isalnum() or c in ".-_") else "_"
            for c in original_name
        )
        dest_name = f"{task_id}_{safe_name}"
        input_folder_id = self._folder_ids[self.INPUT_DIR]
        total_size = os.path.getsize(source_path)

        metadata = {
            "name": dest_name,
            "parents": [input_folder_id],
        }

        # Use resumable upload for large files
        media = MediaFileUpload(
            source_path,
            resumable=True,
            chunksize=1024 * 1024 * 2,  # 2 MB chunks
        )

        request = self._service.files().create(
            body=metadata, media_body=media, fields="id"
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and progress_cb:
                uploaded = int(status.progress() * total_size)
                progress_cb(uploaded, total_size)

        if progress_cb:
            progress_cb(total_size, total_size)

        logger.info(f"Uploaded to Drive Input: {dest_name} ({total_size / (1024*1024):.1f} MB)")
        return dest_name

    def watch_for_output(self, task_id, output_format, timeout=7200, poll_interval=5,
                         download_progress_cb=None):
        """Watch for the completed output file in Drive Output folder.

        Args:
            download_progress_cb: Optional callback(progress_float) where 0.0-1.0
        """
        if not self.is_configured:
            return None

        expected_name = f"{task_id}_UPSCALED.{output_format}"
        output_folder_id = self._folder_ids[self.OUTPUT_DIR]

        start_time = time.time()
        poll_count = 0
        while (time.time() - start_time) < timeout:
            poll_count += 1
            try:
                file_id = self._find_file(expected_name, output_folder_id)
            except Exception as e:
                logger.debug(f"Find file error (poll #{poll_count}): {e}")
                file_id = None

            if file_id:
                # Download to temp file
                tmp_path = os.path.join(tempfile.gettempdir(), expected_name)
                try:
                    self._download_file(file_id, tmp_path, progress_cb=download_progress_cb)
                    if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                        logger.info(f"Output downloaded: {expected_name} "
                                    f"({os.path.getsize(tmp_path) / (1024*1024):.1f} MB)")
                        return tmp_path
                    else:
                        logger.warning(f"Downloaded file is empty or missing: {tmp_path}")
                except Exception as e:
                    logger.error(f"Download failed for {expected_name}: {e}")
                    # Don't return None yet — maybe it's still being written, retry
            time.sleep(poll_interval)

        logger.warning(f"Timeout waiting for output: {expected_name}")
        return None

    def save_to_final(self, downloaded_path, final_output_dir, original_filename, output_format):
        """Move downloaded file to user's final output folder."""
        import shutil
        name_base = os.path.splitext(original_filename)[0]
        final_name = f"UPSCALED_{name_base}.{output_format}"
        final_path = os.path.join(final_output_dir, final_name)

        counter = 1
        while os.path.exists(final_path):
            final_name = f"UPSCALED_{name_base}_{counter}.{output_format}"
            final_path = os.path.join(final_output_dir, final_name)
            counter += 1

        os.makedirs(final_output_dir, exist_ok=True)
        shutil.move(downloaded_path, final_path)
        logger.info(f"Saved to output: {final_path}")
        return final_path

    def cleanup_task(self, task_id):
        """Delete all files for a given task from Google Drive."""
        cleaned = 0
        for folder_key in [self.INPUT_DIR, self.OUTPUT_DIR, self.JOBS_DIR, self.STATUS_DIR]:
            folder_id = self._folder_ids.get(folder_key)
            if not folder_id:
                continue
            # Find files starting with task_id
            query = (
                f"'{folder_id}' in parents and "
                f"name contains '{task_id}' and "
                f"trashed = false"
            )
            try:
                results = self._service.files().list(
                    q=query, fields="files(id, name)", pageSize=20
                ).execute()
                for f in results.get("files", []):
                    try:
                        self._service.files().delete(fileId=f["id"]).execute()
                        cleaned += 1
                    except Exception as e:
                        logger.warning(f"Cleanup failed for {f['name']}: {e}")
            except Exception:
                pass

        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} file(s) for task {task_id}")

    # ─── Helper: Generate Task ID ────────────────────────────────────────

    @staticmethod
    def generate_task_id():
        """Generate a short unique task ID (8 hex chars)."""
        return uuid.uuid4().hex[:8]

    # ─── Internal Helpers ────────────────────────────────────────────────

    def _find_file(self, name, parent_id):
        """Find a file by exact name in a folder. Returns file ID or None."""
        query = (
            f"name = '{name}' and "
            f"'{parent_id}' in parents and "
            f"trashed = false"
        )
        try:
            results = self._service.files().list(
                q=query, fields="files(id)", pageSize=1
            ).execute()
            files = results.get("files", [])
            return files[0]["id"] if files else None
        except Exception:
            return None

    def _download_content(self, file_id):
        """Download file content as string."""
        request = self._service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue().decode("utf-8")

    def _download_file(self, file_id, dest_path, progress_cb=None):
        """Download a file to local path."""
        request = self._service.files().get_media(fileId=file_id)
        with open(dest_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request, chunksize=1024 * 1024 * 5)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status and progress_cb:
                    progress_cb(status.progress())

    def _write_temp(self, data_bytes):
        """Write bytes to a temp file and return path."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.write(data_bytes)
        tmp.close()
        return tmp.name
