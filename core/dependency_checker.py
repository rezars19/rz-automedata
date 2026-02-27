"""
RZ Studio — Dependency Checker
Auto-downloads FFmpeg and RealESRGAN engine if not found.
Runs at app startup to ensure all dependencies are available.
"""

import os
import sys
import logging
import zipfile
import threading
import urllib.request
import shutil
import tempfile

logger = logging.getLogger(__name__)

# ── Download URLs ─────────────────────────────────────────────────────────────
# FFmpeg static build (GPL, Windows x64)
FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "latest/ffmpeg-master-latest-win64-gpl.zip"
)

# RealESRGAN ncnn-vulkan (Windows)
REALESRGAN_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/"
    "v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip"
)


def _get_app_dir() -> str:
    """Get the application directory (where exe lives or project root)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_writable(path: str) -> bool:
    """Check if directory is writable."""
    try:
        test_file = os.path.join(path, ".write_test")
        with open(test_file, 'w') as f:
            f.write("test")
        os.remove(test_file)
        return True
    except (OSError, PermissionError):
        return False


def check_ffmpeg() -> bool:
    """Check if FFmpeg is available (bundled or in PATH)."""
    app_dir = _get_app_dir()
    bundled = os.path.join(app_dir, "ffmpeg", "ffmpeg.exe")
    if os.path.isfile(bundled):
        return True
    # Check PATH
    return shutil.which("ffmpeg") is not None


def check_realesrgan() -> bool:
    """Check if RealESRGAN engine is available."""
    app_dir = _get_app_dir()
    engine = os.path.join(app_dir, "realesrgan-engine", "realesrgan-ncnn-vulkan.exe")
    return os.path.isfile(engine)


def _download_file(url: str, dest_path: str, on_progress=None) -> bool:
    """Download a file with optional progress callback."""
    try:
        logger.info("Downloading: %s", url)
        req = urllib.request.Request(url, headers={
            "User-Agent": "RZStudio-DependencyChecker/1.0"
        })
        response = urllib.request.urlopen(req, timeout=120)

        total_size = int(response.headers.get('Content-Length', 0))
        downloaded = 0
        block_size = 65536

        with open(dest_path, 'wb') as f:
            while True:
                chunk = response.read(block_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress and total_size > 0:
                    pct = downloaded / total_size * 100
                    on_progress(pct, downloaded / 1048576, total_size / 1048576)

        logger.info("Download complete: %s (%.1f MB)", dest_path,
                     os.path.getsize(dest_path) / 1048576)
        return True
    except Exception as e:
        logger.error("Download failed: %s", e)
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def download_ffmpeg(on_status=None) -> bool:
    """Download and extract FFmpeg to app directory."""
    app_dir = _get_app_dir()
    ffmpeg_dir = os.path.join(app_dir, "ffmpeg")

    if not _is_writable(app_dir):
        # Try app data directory as fallback
        appdata = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", ""))
        if appdata:
            ffmpeg_dir = os.path.join(appdata, "RZAutomedata", "ffmpeg")
        else:
            logger.error("Cannot find writable directory for FFmpeg")
            return False

    os.makedirs(ffmpeg_dir, exist_ok=True)

    if on_status:
        on_status("Downloading FFmpeg...")

    # Download zip to temp
    temp_zip = os.path.join(tempfile.gettempdir(), "ffmpeg_download.zip")
    try:
        def _progress(pct, dl_mb, total_mb):
            if on_status:
                on_status(f"Downloading FFmpeg... {pct:.0f}% ({dl_mb:.1f}/{total_mb:.1f} MB)")

        if not _download_file(FFMPEG_URL, temp_zip, _progress):
            return False

        if on_status:
            on_status("Extracting FFmpeg...")

        # Extract — find ffmpeg.exe inside the zip
        with zipfile.ZipFile(temp_zip, 'r') as zf:
            for member in zf.namelist():
                # Look for ffmpeg.exe (could be in a subdirectory)
                if member.endswith("ffmpeg.exe") and "ffprobe" not in member:
                    # Extract just ffmpeg.exe to target dir
                    data = zf.read(member)
                    dest = os.path.join(ffmpeg_dir, "ffmpeg.exe")
                    with open(dest, 'wb') as f:
                        f.write(data)
                    logger.info("FFmpeg extracted to: %s", dest)
                    break
            else:
                logger.error("ffmpeg.exe not found in zip!")
                return False

        if on_status:
            on_status("FFmpeg ready ✅")
        return True

    except Exception as e:
        logger.error("FFmpeg extraction failed: %s", e)
        return False
    finally:
        if os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except Exception:
                pass


def download_realesrgan(on_status=None) -> bool:
    """Download and extract RealESRGAN engine to app directory."""
    app_dir = _get_app_dir()
    engine_dir = os.path.join(app_dir, "realesrgan-engine")

    if not _is_writable(app_dir):
        appdata = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", ""))
        if appdata:
            engine_dir = os.path.join(appdata, "RZAutomedata", "realesrgan-engine")
        else:
            logger.error("Cannot find writable directory for RealESRGAN")
            return False

    os.makedirs(engine_dir, exist_ok=True)

    if on_status:
        on_status("Downloading RealESRGAN engine...")

    temp_zip = os.path.join(tempfile.gettempdir(), "realesrgan_download.zip")
    try:
        def _progress(pct, dl_mb, total_mb):
            if on_status:
                on_status(f"Downloading RealESRGAN... {pct:.0f}% ({dl_mb:.1f}/{total_mb:.1f} MB)")

        if not _download_file(REALESRGAN_URL, temp_zip, _progress):
            return False

        if on_status:
            on_status("Extracting RealESRGAN...")

        # Extract all relevant files
        models_dir = os.path.join(engine_dir, "models")
        os.makedirs(models_dir, exist_ok=True)

        with zipfile.ZipFile(temp_zip, 'r') as zf:
            for member in zf.namelist():
                basename = os.path.basename(member)
                if not basename:
                    continue

                # Extract exe and dlls to engine_dir
                if basename.endswith(('.exe', '.dll')):
                    data = zf.read(member)
                    dest = os.path.join(engine_dir, basename)
                    with open(dest, 'wb') as f:
                        f.write(data)
                    logger.info("Extracted: %s", basename)

                # Extract model files to models_dir
                elif basename.endswith(('.bin', '.param')):
                    data = zf.read(member)
                    dest = os.path.join(models_dir, basename)
                    with open(dest, 'wb') as f:
                        f.write(data)
                    logger.info("Extracted model: %s", basename)

        if on_status:
            on_status("RealESRGAN ready ✅")
        return True

    except Exception as e:
        logger.error("RealESRGAN extraction failed: %s", e)
        return False
    finally:
        if os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except Exception:
                pass


def ensure_dependencies(on_status=None):
    """
    Check and download missing dependencies.
    Runs in background thread to not block UI.
    """
    has_ffmpeg = check_ffmpeg()
    has_realesrgan = check_realesrgan()

    if has_ffmpeg and has_realesrgan:
        logger.info("All dependencies present ✅")
        return

    def _worker():
        if not has_ffmpeg:
            logger.info("FFmpeg not found — downloading...")
            download_ffmpeg(on_status)

        if not has_realesrgan:
            logger.info("RealESRGAN not found — downloading...")
            download_realesrgan(on_status)

        if on_status:
            on_status(None)  # Signal done

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
