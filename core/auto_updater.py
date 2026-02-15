"""
RZ Automedata - Auto Updater
Downloads new version from URL, replaces current exe, and restarts.
"""

import os
import sys
import time
import shutil
import tempfile
import logging
import subprocess
import urllib.request

logger = logging.getLogger(__name__)


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


def download_update(download_url, on_progress=None):
    """
    Download the update file from URL.
    
    Args:
        download_url: URL to download the new exe from
        on_progress: Optional callback(percent, downloaded_mb, total_mb)
    
    Returns:
        str: Path to downloaded file, or None on failure
    """
    try:
        # Create temp file for download
        temp_dir = tempfile.gettempdir()
        temp_file = os.path.join(temp_dir, "RZAutomedata_update.exe")
        
        logger.info(f"Downloading update from: {download_url}")
        
        # Open URL and get content length
        req = urllib.request.Request(download_url, headers={
            'User-Agent': 'RZAutomedata-Updater/1.0'
        })
        
        response = urllib.request.urlopen(req, timeout=60)
        total_size = int(response.headers.get('Content-Length', 0))
        
        downloaded = 0
        block_size = 8192
        
        with open(temp_file, 'wb') as f:
            while True:
                chunk = response.read(block_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                
                if on_progress and total_size > 0:
                    percent = (downloaded / total_size) * 100
                    dl_mb = downloaded / (1024 * 1024)
                    total_mb = total_size / (1024 * 1024)
                    on_progress(percent, dl_mb, total_mb)
        
        logger.info(f"Download complete: {temp_file} ({downloaded} bytes)")
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
