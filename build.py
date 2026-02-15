"""
RZ Automedata - Build Script
Build the application into a standalone exe using PyInstaller.

Usage:
    python build.py
"""

import subprocess
import sys
import os
import shutil


def main():
    print("=" * 60)
    print("  RZ Automedata - Build to EXE")
    print("=" * 60)
    print()

    # Check PyInstaller
    try:
        import PyInstaller
        print(f"✅ PyInstaller {PyInstaller.__version__} found")
    except ImportError:
        print("❌ PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("✅ PyInstaller installed")

    # Check packaging module (needed for update checker)
    try:
        import packaging
        print(f"✅ packaging module found")
    except ImportError:
        print("❌ packaging not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "packaging"])
        print("✅ packaging installed")

    # Clean old builds
    for folder in ["build", "dist"]:
        if os.path.exists(folder):
            print(f"🧹 Cleaning {folder}/...")
            shutil.rmtree(folder)

    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=RZAutomedata",
        "--onefile",                    # Single exe file
        "--windowed",                   # No console window
        "--icon=NONE",                  # No icon (add your .ico later)
        "--add-data=auto_updater.py;.",  # Include updater
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
    print("🔨 Building exe... This may take a few minutes.")
    print(f"   Command: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

    if result.returncode == 0:
        exe_path = os.path.join("dist", "RZAutomedata.exe")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print()
            print("=" * 60)
            print(f"  ✅ BUILD SUCCESSFUL!")
            print(f"  📦 Output: {os.path.abspath(exe_path)}")
            print(f"  📏 Size: {size_mb:.1f} MB")
            print("=" * 60)
        else:
            print("⚠️ Build finished but exe not found at expected path")
    else:
        print()
        print("❌ BUILD FAILED! Check the error messages above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
