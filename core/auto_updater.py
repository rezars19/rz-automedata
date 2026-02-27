"""
RZ Automedata - Auto Updater
Downloads new version from GitHub Releases, replaces current exe, and restarts.
Supports GitHub redirect handling and validates downloaded file.
"""

import os
import sys
import tempfile
import logging
import subprocess

logger = logging.getLogger(__name__)

# GitHub repository info
GITHUB_REPO = "rezars19/rz-automedata"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def get_app_path():
    """Get the path of the current executable or script."""
    if getattr(sys, 'frozen', False):
        # Running as compiled exe (PyInstaller)
        return sys.executable
    else:
        # Running as Python script
        return os.path.abspath(sys.argv[0])


def is_frozen():
    """Check if running as compiled exe."""
    return getattr(sys, 'frozen', False)


def get_github_download_url():
    """
    Get the direct download URL for the latest release .exe from GitHub API.
    
    Returns:
        tuple: (version, download_url) or (None, None)
    """
    try:
        import requests
        response = requests.get(GITHUB_API_URL, timeout=15, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "RZAutomedata-Updater/1.0"
        })
        
        if response.status_code != 200:
            logger.warning(f"GitHub API returned {response.status_code}")
            return None, None
        
        data = response.json()
        tag = data.get("tag_name", "")  # e.g. "v1.2.0"
        version = tag.lstrip("v")  # "1.2.0"
        
        # Find the .exe asset
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith(".exe"):
                download_url = asset.get("browser_download_url", "")
                logger.info(f"Found GitHub release: v{version} -> {download_url}")
                return version, download_url
        
        logger.warning("No .exe asset found in latest GitHub release")
        return version, None
        
    except Exception as e:
        logger.warning(f"Failed to check GitHub releases: {e}")
        return None, None


def download_update(download_url, on_progress=None):
    """
    Download the update file from URL using requests (handles redirects properly).
    
    Args:
        download_url: URL to download the new exe from
        on_progress: Optional callback(percent, downloaded_mb, total_mb)
    
    Returns:
        str: Path to downloaded file, or None on failure
    """
    try:
        import requests
        
        # Create temp file for download
        temp_dir = tempfile.gettempdir()
        temp_file = os.path.join(temp_dir, "RZAutomedata_update.exe")
        
        logger.info(f"Downloading update from: {download_url}")
        
        # Stream download with requests (handles redirects automatically)
        response = requests.get(download_url, stream=True, timeout=120, headers={
            "User-Agent": "RZAutomedata-Updater/1.0",
            "Accept": "application/octet-stream"
        })
        response.raise_for_status()
        
        # Check content type — reject HTML responses
        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type:
            logger.error(f"Download URL returned HTML instead of binary! URL: {download_url}")
            logger.error("The download URL is likely wrong. It should point to the direct asset, not a page.")
            return None
        
        total_size = int(response.headers.get('Content-Length', 0))
        
        if total_size > 0 and total_size < 1_000_000:  # Less than 1MB is suspicious
            logger.error(f"Download size too small ({total_size} bytes), likely not a valid exe")
            return None
        
        downloaded = 0
        block_size = 65536  # 64KB chunks for faster download
        
        with open(temp_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=block_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if on_progress and total_size > 0:
                        percent = (downloaded / total_size) * 100
                        dl_mb = downloaded / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        on_progress(percent, dl_mb, total_mb)
        
        # Validate downloaded file
        file_size = os.path.getsize(temp_file)
        if file_size < 1_000_000:  # Less than 1MB
            logger.error(f"Downloaded file too small ({file_size} bytes), likely corrupt")
            os.remove(temp_file)
            return None
        
        # Check if file starts with MZ (valid Windows exe header)
        with open(temp_file, 'rb') as f:
            header = f.read(2)
        
        if header != b'MZ':
            logger.error(f"Downloaded file is not a valid exe (header: {header})")
            os.remove(temp_file)
            return None
        
        logger.info(f"Download complete: {temp_file} ({file_size:,} bytes)")
        return temp_file
        
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return None


def apply_update_and_restart(downloaded_file):
    """
    Replace current exe with downloaded update and restart.
    Creates a batch script that:
    1. Waits for current app to close
    2. Replaces the exe
    3. Starts the new exe
    4. Cleans up
    
    Args:
        downloaded_file: Path to the downloaded new exe
    """
    if not is_frozen():
        logger.warning("Not running as exe, cannot auto-update. Please replace files manually.")
        return False
    
    current_exe = get_app_path()
    backup_exe = current_exe + ".old"
    
    # Create updater batch script
    batch_script = os.path.join(tempfile.gettempdir(), "rz_updater.bat")
    
    script_content = f"""@echo off
title RZ Automedata - Updating...
echo.
echo ============================================
echo   RZ Automedata - Applying Update...
echo ============================================
echo.
echo Waiting for application to close...

:: Wait for the current exe to be released
:wait_loop
timeout /t 1 /nobreak >nul
tasklist /FI "PID eq %CURRENT_PID%" 2>nul | find /i "%CURRENT_PID%" >nul
if not errorlevel 1 goto wait_loop

:: Additional safety wait
timeout /t 2 /nobreak >nul

echo Backing up current version...
if exist "{backup_exe}" del /f /q "{backup_exe}"
move /y "{current_exe}" "{backup_exe}"

echo Installing new version...
move /y "{downloaded_file}" "{current_exe}"

echo Starting updated application...
start "" "{current_exe}"

:: Cleanup
timeout /t 3 /nobreak >nul
if exist "{backup_exe}" del /f /q "{backup_exe}"
del /f /q "%~f0"
exit
""".replace("%CURRENT_PID%", str(os.getpid()))
    
    try:
        with open(batch_script, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        logger.info(f"Starting updater script: {batch_script}")
        
        # Start the batch script (hidden window)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        
        subprocess.Popen(
            ['cmd', '/c', batch_script],
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to start updater: {e}")
        return False
