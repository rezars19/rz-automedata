"""
RZ Studio - Build Script
Build the application into a standalone exe using PyInstaller.

Usage:
    python build.py
"""

import subprocess
import sys
import os
import shutil
import stat
import time


def remove_readonly(func, path, _):
    """Clear the readonly bit and reattempt the removal"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def main():
    print("=" * 60)
    print("  RZ Studio- Build to EXE")
    print("=" * 60)
    print()

    # Clean old builds
    for folder in ["build", "dist"]:
        if os.path.exists(folder):
            print(f"[*] Cleaning {folder}/...")
            try:
                shutil.rmtree(folder, onerror=remove_readonly)
            except PermissionError:
                print(f"[!] Could not clean {folder}. Retrying in 2 seconds...")
                time.sleep(2)
                try:
                    shutil.rmtree(folder, onerror=remove_readonly)
                except Exception as e:
                    print(f"[!] Warning: Could not fully clean {folder}: {e}")

    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=RZ Studio",
        "--onefile",                    # Single exe file
        "--windowed",                   # No console window
        "--icon=icon.ico",              # App icon
        "--add-data=icon.ico;.",        # Include icon for runtime
        # Hidden imports for libraries that PyInstaller might miss
        "--hidden-import=customtkinter",
        "--hidden-import=PIL",
        "--hidden-import=PIL._tkinter_finder",
        "--hidden-import=cv2",
        "--hidden-import=numpy",
        "--hidden-import=supabase",
        "--hidden-import=gotrue",
        "--hidden-import=httpx",
        "--hidden-import=postgrest",
        "--hidden-import=storage3",
        "--hidden-import=realtime",
        "--hidden-import=packaging",
        "--hidden-import=packaging.version",
        "--hidden-import=tkinterdnd2",
        # Collect all customtkinter data files (themes, etc.)
        "--collect-all=customtkinter",
        "--collect-all=tkinterdnd2",
        # Main script
        "app.py"
    ]

    print()
    print("[*] Building exe... This may take a few minutes.")
    print()

    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

    if result.returncode == 0:
        exe_path = os.path.join("dist", "RZAutomedata.exe")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print()
            print("=" * 60)
            print(f"  [OK] BUILD SUCCESSFUL!")
            print(f"  Output: {os.path.abspath(exe_path)}")
            print(f"  Size: {size_mb:.1f} MB")
            print("=" * 60)
        else:
            print("[!] Build finished but exe not found at expected path")
    else:
        print()
        print("[FAIL] BUILD FAILED! Check the error messages above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
