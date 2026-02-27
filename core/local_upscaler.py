"""
RZ Automedata — Local Upscaler Engine
Uses realesrgan-ncnn-vulkan for GPU/CPU upscaling without internet.
Supports both images and videos.

Binary: https://github.com/xinntao/Real-ESRGAN (ncnn-vulkan release)
"""

import os
import re
import glob
import json
import time
import shutil
import zipfile
import logging
import subprocess
import threading
import urllib.request

logger = logging.getLogger(__name__)

# Import FFmpeg path resolver and GPU encoder detection
try:
    from core.abstract_video import _get_ffmpeg_path, detect_working_hw_encoder
except ImportError:
    def _get_ffmpeg_path():
        found = shutil.which("ffmpeg")
        return found or "ffmpeg"
    def detect_working_hw_encoder(force_recheck=False):
        return (None, "libx264 (CPU)")

# ── Binary info ──────────────────────────────────────────────────────────────
NCNN_RELEASE_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/"
    "v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip"
)
ENGINE_DIR_NAME = "realesrgan-engine"

# Supported file extensions
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".webm", ".flv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}

# Model name mapping: Python inference script → ncnn-vulkan binary
# The ncnn binary uses lowercase-hyphen names for its model files
NCNN_MODEL_MAP = {
    "RealESRGAN_x4plus": "realesrgan-x4plus",
    "RealESRGAN_x4plus_anime_6B": "realesrgan-x4plus-anime",
    "realesr-animevideov3": "realesr-animevideov3",
}

# Tile sizes to avoid OOM (0 = no tiling). x4plus uses much more VRAM.
# x4plus model is ~33 MB — needs small tiles on consumer GPUs (4-8 GB).
# iGPUs (AMD Radeon Graphics, Intel UHD/Iris) have 512MB-2GB shared VRAM
# and need even smaller tiles.
NCNN_TILE_MAP = {
    "realesrgan-x4plus": 100,
    "realesrgan-x4plus-anime": 150,
    "realesr-animevideov3": 0,  # lightweight model, no tiling needed
}

# Tile fallback chain: if OOM, try progressively smaller tiles
# Includes very small tiles (16) for iGPU with limited shared VRAM
NCNN_TILE_FALLBACKS = {
    "realesrgan-x4plus": [100, 50, 32, 16],
    "realesrgan-x4plus-anime": [150, 100, 50, 32],
    "realesr-animevideov3": [200, 100, 50],
}

# Vulkan error strings that indicate GPU memory issues
_VRAM_ERROR_STRINGS = [
    "vkAllocateMemory failed",
    "vkQueueSubmit failed",
    "vkWaitForFences failed",
    "out of memory",
]

# Vulkan initialization errors (broken driver, no Vulkan support)
_VULKAN_INIT_ERRORS = [
    "vkcreateinstance failed",
    "vkenumeratephysicaldevices failed",
    "no vulkan device",
    "failed to create gpu instance",
]

# Default tile size for CPU mode (smaller = less RAM usage)
CPU_DEFAULT_TILE = 200


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


class LocalUpscaler:
    """Local Real-ESRGAN upscaler using ncnn-vulkan binary."""

    def __init__(self, app_dir=None):
        # Search for engine in multiple locations
        search_dirs = []
        if app_dir:
            search_dirs.append(app_dir)
        # 1. Directory of the running exe (PyInstaller)
        import sys
        if hasattr(sys, '_MEIPASS'):
            search_dirs.append(sys._MEIPASS)
        if getattr(sys, 'frozen', False):
            search_dirs.append(os.path.dirname(sys.executable))
        # 2. Current working directory
        search_dirs.append(os.getcwd())
        # 3. Script directory (for development)
        search_dirs.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # 4. LOCALAPPDATA fallback (auto-downloaded)
        for env_var in ["LOCALAPPDATA", "APPDATA"]:
            appdata = os.environ.get(env_var, "")
            if appdata:
                search_dirs.append(os.path.join(appdata, "RZAutomedata"))

        # Find first valid engine location
        self._app_dir = search_dirs[0]
        for d in search_dirs:
            candidate = os.path.join(d, ENGINE_DIR_NAME, "realesrgan-ncnn-vulkan.exe")
            if os.path.isfile(candidate):
                self._app_dir = d
                break

        self._engine_dir = os.path.join(self._app_dir, ENGINE_DIR_NAME)
        self._exe_path = os.path.join(self._engine_dir, "realesrgan-ncnn-vulkan.exe")
        self._gpu_info = None
        self._cancel = False

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def is_installed(self):
        return os.path.isfile(self._exe_path)

    @property
    def engine_dir(self):
        return self._engine_dir

    @property
    def exe_path(self):
        return self._exe_path

    # ── GPU Detection ─────────────────────────────────────────────────────

    def detect_gpu(self):
        """Detect GPU using multiple methods. Supports NVIDIA, AMD, and Intel.
        Returns dict: {'has_gpu': bool, 'gpu_name': str, 'device_id': int,
                       'all_gpus': list}
        """
        if self._gpu_info is not None:
            return self._gpu_info

        info = {"has_gpu": False, "gpu_name": "CPU Only", "device_id": -1,
                "all_gpus": []}

        # Method 1 (BEST): Use ncnn-vulkan binary to enumerate Vulkan devices
        # This detects ANY Vulkan-capable GPU: NVIDIA, AMD, Intel
        if self.is_installed:
            try:
                r = subprocess.run(
                    [self._exe_path, "-i", ".", "-o", "."],
                    capture_output=True, text=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    cwd=self._engine_dir,
                )
                output = r.stdout + r.stderr
                logger.debug("ncnn GPU detection output: %s", output[:500])

                # ncnn-vulkan prints lines like:
                #   [0 NVIDIA GeForce RTX 3060]  queueC=...
                #   [0 AMD Radeon(TM) Vega 8 Graphics]  queueC=...
                #   [0 Intel(R) UHD Graphics 620]  queueC=...
                # Some versions print:
                #   [0  11800  11310600]  AMD Radeon(TM) Vega 8 Graphics
                all_gpus = []

                # Pattern A: [id NAME] queueC=...
                for m in re.finditer(r"\[(\d+)\s+([^\]]+)\]\s+queueC", output):
                    dev_id = int(m.group(1))
                    dev_name = m.group(2).strip()
                    # Filter out pure numeric strings (vendor/device IDs)
                    if not re.match(r'^[\d\s]+$', dev_name):
                        all_gpus.append({"id": dev_id, "name": dev_name})

                # Pattern B: [id vendorId deviceId] NAME (fallback)
                if not all_gpus:
                    for m in re.finditer(r"\[(\d+)\s+\d+\s+\d+\]\s+(.+?)(?:\n|$)", output):
                        dev_id = int(m.group(1))
                        dev_name = m.group(2).strip()
                        if dev_name and not re.match(r'^[\d\s]+$', dev_name):
                            all_gpus.append({"id": dev_id, "name": dev_name})

                if all_gpus:
                    # Prefer discrete GPU over integrated
                    best = all_gpus[0]
                    for g in all_gpus:
                        name_lower = g["name"].lower()
                        # Prefer NVIDIA/AMD discrete over Intel integrated
                        if any(k in name_lower for k in ["geforce", "rtx", "gtx",
                                                          "radeon rx", "radeon pro"]):
                            best = g
                            break
                    info = {
                        "has_gpu": True,
                        "gpu_name": best["name"],
                        "device_id": best["id"],
                        "all_gpus": all_gpus,
                    }
                    self._gpu_info = info
                    logger.info("Vulkan GPU detected: %s (device %d), total: %d",
                                best["name"], best["id"], len(all_gpus))
                    return info

            except Exception as e:
                logger.debug("ncnn GPU detection failed: %s", e)

        # Method 2: Try nvidia-smi (NVIDIA only, fast)
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if r.returncode == 0 and r.stdout.strip():
                gpu_name = r.stdout.strip().split("\n")[0]
                info = {
                    "has_gpu": True,
                    "gpu_name": gpu_name,
                    "device_id": 0,
                    "all_gpus": [{"id": 0, "name": gpu_name}],
                }
                self._gpu_info = info
                logger.info("NVIDIA GPU detected via nvidia-smi: %s", gpu_name)
                return info
        except Exception:
            pass

        # Method 3: Windows WMI — detect any GPU (AMD, Intel, NVIDIA)
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | "
                 "Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if r.returncode == 0 and r.stdout.strip():
                gpu_names = [n.strip() for n in r.stdout.strip().split("\n") if n.strip()]
                if gpu_names:
                    # Filter out Microsoft Basic Display Adapter
                    real_gpus = [n for n in gpu_names
                                 if "basic" not in n.lower()
                                 and "microsoft" not in n.lower()]
                    if real_gpus:
                        # Prefer discrete GPU
                        best_name = real_gpus[0]
                        for n in real_gpus:
                            nl = n.lower()
                            if any(k in nl for k in ["geforce", "rtx", "gtx",
                                                      "radeon rx", "radeon pro"]):
                                best_name = n
                                break
                        info = {
                            "has_gpu": True,
                            "gpu_name": best_name,
                            "device_id": 0,
                            "all_gpus": [{"id": i, "name": n}
                                         for i, n in enumerate(real_gpus)],
                        }
                        self._gpu_info = info
                        logger.info("GPU detected via WMI: %s", best_name)
                        return info
        except Exception:
            pass

        # No GPU found — CPU only
        logger.info("No Vulkan GPU detected, will use CPU mode")
        self._gpu_info = info
        return info

    # ── Engine Download ───────────────────────────────────────────────────

    def download_engine(self, progress_cb=None):
        """Download realesrgan-ncnn-vulkan from GitHub.
        progress_cb(downloaded_mb, total_mb) called periodically.
        """
        os.makedirs(self._engine_dir, exist_ok=True)
        zip_path = os.path.join(self._engine_dir, "engine.zip")

        try:
            # Download
            req = urllib.request.Request(NCNN_RELEASE_URL, headers={
                "User-Agent": "RZ-Automedata/1.0"
            })
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 65536
                with open(zip_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total > 0:
                            progress_cb(
                                downloaded / (1024 * 1024),
                                total / (1024 * 1024),
                            )

            # Extract
            if progress_cb:
                progress_cb(-1, -1)  # Signal: extracting

            with zipfile.ZipFile(zip_path, "r") as zf:
                # Find the inner folder name
                names = zf.namelist()
                # Typically: realesrgan-ncnn-vulkan-20220424-windows/realesrgan-ncnn-vulkan.exe
                inner_prefix = ""
                for n in names:
                    if n.endswith("realesrgan-ncnn-vulkan.exe"):
                        inner_prefix = os.path.dirname(n)
                        break

                for member in names:
                    if inner_prefix and member.startswith(inner_prefix):
                        rel = member[len(inner_prefix):].lstrip("/").lstrip("\\")
                    else:
                        rel = member

                    if not rel:
                        continue

                    target = os.path.join(self._engine_dir, rel)
                    if member.endswith("/"):
                        os.makedirs(target, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with zf.open(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)

            return True

        except Exception as e:
            logger.error(f"Engine download failed: {e}")
            raise
        finally:
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except Exception:
                    pass

    # ── Image Upscale ─────────────────────────────────────────────────────

    def upscale_image(self, input_path, output_dir, scale=4,
                      model="realesr-animevideov3", face_enhance=False,
                      progress_cb=None, force_cpu=False):
        """Upscale a single image.
        progress_cb(stage, progress_pct, message)
        force_cpu: if True, use CPU even if GPU is available
        Returns: path to output file
        """
        if not self.is_installed:
            raise RuntimeError("Engine not installed. Download it first.")

        self._cancel = False
        if force_cpu:
            gpu = {"has_gpu": False, "gpu_name": "CPU", "device_id": -1}
        else:
            gpu = self.detect_gpu()
        base = os.path.splitext(os.path.basename(input_path))[0]
        ext = os.path.splitext(input_path)[1]
        # Output as PNG for maximum quality
        out_name = f"{base}_UPSCALED{ext}"
        out_path = os.path.join(output_dir, out_name)

        if progress_cb:
            device = gpu["gpu_name"] if gpu["has_gpu"] else "CPU"
            progress_cb("process", 10, f"Upscaling with {device}...")

        # Map model name for ncnn-vulkan binary
        ncnn_model = NCNN_MODEL_MAP.get(model, model)
        tile_fallbacks = NCNN_TILE_FALLBACKS.get(ncnn_model, None)
        tile_size = NCNN_TILE_MAP.get(ncnn_model, 0)

        tiles_to_try = [tile_size] if not tile_fallbacks else list(tile_fallbacks)

        for try_idx, cur_tile in enumerate(tiles_to_try):
            # Remove previous failed output on retry
            if try_idx > 0:
                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass
                if progress_cb:
                    progress_cb(
                        "process", 10,
                        f"Retrying with smaller tile ({cur_tile})...",
                    )
                logger.warning("VRAM OOM — retrying image with tile=%d", cur_tile)

            cmd = [
                self._exe_path,
                "-i", input_path,
                "-o", out_path,
                "-n", ncnn_model,
                "-s", str(scale),
                "-f", ext.lstrip("."),
            ]
            # Tiling: CPU always needs tile to avoid RAM OOM
            effective_tile = cur_tile
            if not gpu["has_gpu"] and effective_tile == 0:
                effective_tile = CPU_DEFAULT_TILE
            if effective_tile > 0:
                cmd.extend(["-t", str(effective_tile)])
            if gpu["has_gpu"]:
                cmd.extend(["-g", str(gpu["device_id"])])
            else:
                cmd.extend(["-g", "-1"])  # CPU mode
                cmd.extend(["-j", "1:1:1"])  # limit threads for low-end CPUs

            if face_enhance:
                # ncnn-vulkan doesn't have face enhance, skip silently
                pass

            logger.info("Image upscale cmd (tile=%d): %s", cur_tile, " ".join(cmd))

            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=self._engine_dir,
            )

            output_lines = []
            for line in iter(proc.stdout.readline, ""):
                line = line.strip()
                if line:
                    output_lines.append(line)
                    # ncnn-vulkan shows percentage
                    pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
                    if pct_match and progress_cb:
                        pct = float(pct_match.group(1))
                        progress_cb("process", int(pct), f"Upscaling... {pct:.0f}%")

            proc.wait()

            if self._cancel:
                raise Exception("Cancelled")

            if proc.returncode != 0:
                err = "\n".join(output_lines[-5:])
                all_out = "\n".join(output_lines).lower()
                # Check for Vulkan init errors (no/broken Vulkan driver)
                if any(e in all_out for e in _VULKAN_INIT_ERRORS):
                    if gpu["has_gpu"]:
                        raise Exception(
                            f"Vulkan driver error on {gpu['gpu_name']}. "
                            f"Try switching to CPU mode, or update your GPU driver."
                        )
                    else:
                        raise Exception(
                            f"CPU upscaling failed. This may be a system compatibility issue.\n"
                            f"Error: {err[:200]}"
                        )
                raise Exception(f"Upscale failed: {err[:300]}")

            # Detect Vulkan VRAM errors (binary exits 0 but output may be black)
            all_output = "\n".join(output_lines)
            has_vram_error = any(e in all_output for e in _VRAM_ERROR_STRINGS)

            if not os.path.exists(out_path):
                raise Exception("Output file not created")

            # Validate output — black image is very small
            out_size = os.path.getsize(out_path)
            looks_black = out_size < 20_000  # < 20 KB = likely black

            if has_vram_error or looks_black:
                reason = "VRAM errors" if has_vram_error else "black output"
                logger.warning(
                    "Image x4plus tile=%d produced %s (size: %d bytes)",
                    cur_tile, reason, out_size,
                )
                if try_idx < len(tiles_to_try) - 1:
                    continue  # retry with smaller tile
                raise Exception(
                    f"GPU out of memory (VRAM). Model x4plus is too heavy "
                    f"for this GPU at this image size. Try AnimVideo v3 or "
                    f"close other GPU-intensive apps."
                )

            # Success!
            break

        if progress_cb:
            mb = os.path.getsize(out_path) / (1024 * 1024)
            progress_cb("done", 100, f"✅ {out_name} ({mb:.1f} MB)")

        return out_path

    # ── Video Upscale ─────────────────────────────────────────────────────

    def upscale_video(self, input_path, output_dir, scale=4,
                      model="realesr-animevideov3", face_enhance=False,
                      mute_audio=False, output_format="mp4",
                      progress_cb=None, force_cpu=False, target_fps=30):
        """Upscale a video file (local processing).
        progress_cb(stage, progress_pct, message)
        force_cpu: if True, use CPU even if GPU is available
        target_fps: output FPS (30 or 60). If higher than source, uses
                    motion interpolation for smooth playback.
        Returns: path to output file
        """
        if not self.is_installed:
            raise RuntimeError("Engine not installed. Download it first.")

        self._cancel = False
        if force_cpu:
            gpu = {"has_gpu": False, "gpu_name": "CPU", "device_id": -1}
        else:
            gpu = self.detect_gpu()
        device_name = gpu["gpu_name"] if gpu["has_gpu"] else "CPU"
        orig = os.path.basename(input_path)
        base = os.path.splitext(orig)[0]
        out_name = f"{base}_UPSCALED.{output_format}"
        out_path = os.path.join(output_dir, out_name)

        # Work directory
        work_dir = os.path.join(output_dir, f"_work_{base}")
        frames_in = os.path.join(work_dir, "frames_in")
        frames_out = os.path.join(work_dir, "frames_out")
        os.makedirs(frames_in, exist_ok=True)
        os.makedirs(frames_out, exist_ok=True)

        try:
            # PHASE 1: Analyze + Extract
            if progress_cb:
                progress_cb("process", 2, "Analyzing video...")

            # Get video info
            probe = subprocess.run(
                [_get_ffmpeg_path(), "-i", input_path],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            probe_out = probe.stderr  # ffmpeg info goes to stderr

            # Detect source FPS for interpolation decision
            source_fps = 30.0
            fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", probe_out)
            if fps_match:
                try:
                    source_fps = float(fps_match.group(1))
                except ValueError:
                    source_fps = 30.0

            # target_fps=0 means "Original" — keep source FPS
            effective_fps = target_fps if target_fps > 0 else int(round(source_fps))
            need_interpolation = target_fps > 0 and target_fps > source_fps + 1
            has_audio = "Audio:" in probe_out

            if progress_cb:
                progress_cb("process", 5, f"Extracting frames ({device_name})...")

            # Extract frames (use all CPU threads for faster decoding)
            r = subprocess.run(
                [
                    _get_ffmpeg_path(), "-y", "-threads", "0", "-i", input_path,
                    "-qscale:v", "1", "-qmin", "1", "-qmax", "1",
                    os.path.join(frames_in, "frame_%08d.png"),
                ],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode != 0:
                raise Exception("Frame extraction failed")

            total_frames = len(glob.glob(os.path.join(frames_in, "*.png")))
            if total_frames == 0:
                raise Exception("No frames extracted")

            if progress_cb:
                progress_cb("process", 10, f"{total_frames} frames, upscaling...")

            if self._cancel:
                raise Exception("Cancelled")

            # PHASE 2: Upscale frames
            # Map model name for ncnn-vulkan binary
            ncnn_model = NCNN_MODEL_MAP.get(model, model)
            tile_fallbacks = NCNN_TILE_FALLBACKS.get(ncnn_model, None)
            tile_size = NCNN_TILE_MAP.get(ncnn_model, 0)

            # Try upscaling with tile fallback for OOM recovery
            tiles_to_try = [tile_size] if not tile_fallbacks else list(tile_fallbacks)
            upscale_ok = False

            for try_idx, cur_tile in enumerate(tiles_to_try):
                # Clean output dir on retry
                if try_idx > 0:
                    for old in glob.glob(os.path.join(frames_out, "*.png")):
                        try:
                            os.remove(old)
                        except Exception:
                            pass
                    if progress_cb:
                        progress_cb(
                            "process", 10,
                            f"Retrying with smaller tile ({cur_tile})...",
                        )
                    logger.warning("VRAM OOM — retrying with tile=%d", cur_tile)

                cmd = [
                    self._exe_path,
                    "-i", frames_in,
                    "-o", frames_out,
                    "-n", ncnn_model,
                    "-s", str(scale),
                    "-f", "png",
                ]
                # Tiling: CPU always needs tile to avoid RAM OOM
                effective_tile = cur_tile
                if not gpu["has_gpu"] and effective_tile == 0:
                    effective_tile = CPU_DEFAULT_TILE
                if effective_tile > 0:
                    cmd.extend(["-t", str(effective_tile)])
                if gpu["has_gpu"]:
                    cmd.extend(["-g", str(gpu["device_id"])])
                else:
                    cmd.extend(["-g", "-1"])
                    cmd.extend(["-j", "1:1:1"])  # limit threads for low-end CPUs

                logger.info("Upscale cmd (tile=%d): %s", cur_tile, " ".join(cmd))

                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    cwd=self._engine_dir,
                )

                # Monitor progress via output frame count
                done_frames = 0
                monitor_running = True

                def count_outputs():
                    nonlocal done_frames
                    while monitor_running:
                        try:
                            done_frames = len(glob.glob(
                                os.path.join(frames_out, "*.png")))
                        except Exception:
                            pass
                        time.sleep(2)

                counter = threading.Thread(target=count_outputs, daemon=True)
                counter.start()

                output_lines = []
                for line in iter(proc.stdout.readline, ""):
                    line = line.strip()
                    if line:
                        output_lines.append(line)
                    # Update progress from frame count
                    if total_frames > 0 and done_frames > 0 and progress_cb:
                        pct = min(10 + int((done_frames / total_frames) * 65), 75)
                        progress_cb(
                            "process", pct,
                            f"Upscaling frame {done_frames}/{total_frames}",
                        )

                proc.wait()
                monitor_running = False

                if self._cancel:
                    raise Exception("Cancelled")

                if proc.returncode != 0:
                    err = "\n".join(output_lines[-5:])
                    raise Exception(f"Upscale failed: {err[:300]}")

                # Detect Vulkan VRAM errors (binary exits 0 but frames are black)
                all_output = "\n".join(output_lines)
                has_vram_error = any(
                    e in all_output for e in _VRAM_ERROR_STRINGS
                )

                out_count = len(glob.glob(os.path.join(frames_out, "*.png")))
                if out_count == 0:
                    raise Exception("No upscaled frames produced")

                # Validate output frames — black frames are very small
                # A real upscaled PNG is typically >50 KB; a black one is <20 KB
                sample_files = sorted(
                    glob.glob(os.path.join(frames_out, "*.png"))
                )[:3]
                avg_size = sum(os.path.getsize(f) for f in sample_files) / len(sample_files)
                frames_look_black = avg_size < 20_000  # < 20 KB = likely black

                if has_vram_error or frames_look_black:
                    reason = "VRAM errors" if has_vram_error else "black frames"
                    logger.warning(
                        "x4plus tile=%d produced %s (avg frame: %.0f bytes)",
                        cur_tile, reason, avg_size,
                    )
                    if try_idx < len(tiles_to_try) - 1:
                        continue  # retry with smaller tile
                    raise Exception(
                        f"GPU out of memory (VRAM). Model x4plus is too heavy "
                        f"for this GPU. Try using AnimVideo v3 model instead, "
                        f"or close other GPU-intensive apps."
                    )

                # Success!
                upscale_ok = True
                logger.info(
                    "Upscale OK: %d frames, tile=%d, avg_size=%.0f bytes",
                    out_count, cur_tile, avg_size,
                )
                break

            if progress_cb:
                progress_cb("merge", 78, f"Encoding {output_format.upper()}...")

            # PHASE 3: Merge to video
            # Find frame pattern
            samples = sorted(glob.glob(os.path.join(frames_out, "*.png")))
            first = os.path.basename(samples[0])
            # Detect the naming pattern
            frame_pattern = os.path.join(frames_out, "frame_%08d.png")
            num_match = re.search(r"(\d{6,})", first)
            if num_match:
                num_str = num_match.group(1)
                fmt_str = f"%0{len(num_str)}d"
                frame_pattern = os.path.join(
                    frames_out, first.replace(num_str, fmt_str)
                )

            # Input framerate = SOURCE FPS to preserve original duration
            input_fps = str(int(round(source_fps)))

            # Use max threads for PNG decoding (biggest CPU bottleneck)
            ff = [_get_ffmpeg_path(), "-y", "-threads", "0",
                  "-framerate", input_fps, "-i", frame_pattern]
            if has_audio and not mute_audio:
                ff.extend(["-i", input_path, "-map", "0:v", "-map", "1:a?"])

            # ── FPS handling ─────────────────────────────────────────
            # Use FFmpeg's 'framerate' filter for smooth FPS conversion.
            # This is MUCH faster than 'minterpolate' (10-20x) while still
            # producing smooth motion-interpolated output.
            # 'minterpolate' does full per-pixel motion estimation (very heavy,
            # CPU single-thread); 'framerate' does lightweight motion-based
            # blending that runs fast on any CPU.
            vf_filters = []
            if need_interpolation:
                fps_ratio = effective_fps / max(source_fps, 1)
                if fps_ratio >= 1.8:
                    # Big jump (e.g. 30→60): motion-interpolated FPS conversion
                    if progress_cb:
                        progress_cb("merge", 78,
                                    f"Interpolating {source_fps:.0f}→{effective_fps} FPS...")
                    # framerate filter: fast motion-based interpolation
                    # scene=100 disables scene-change detection for consistent output
                    vf_filters.append(
                        f"framerate=fps={effective_fps}"
                        f":interp_start=0:interp_end=255:scene=100"
                    )
                    logger.info("Using framerate filter (ratio=%.1fx)", fps_ratio)
                else:
                    # Small jump (e.g. 24→30): simple frame rate conversion
                    if progress_cb:
                        progress_cb("merge", 78,
                                    f"Adjusting {source_fps:.0f}→{effective_fps} FPS...")
                    vf_filters.append(f"fps={effective_fps}")
                    logger.info("Using simple fps filter (ratio=%.1fx)", fps_ratio)

            if vf_filters:
                ff.extend(["-vf", ",".join(vf_filters)])

            # ── Check output resolution from upscaled frames ──
            # NVENC H.264 has a max resolution of 4096 per dimension.
            # If output exceeds this, we must use libx264 instead.
            out_w, out_h = 0, 0
            try:
                sample_frame = samples[0] if samples else None
                if sample_frame:
                    probe_res = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                         "-show_entries", "stream=width,height",
                         "-of", "csv=p=0", sample_frame],
                        capture_output=True, text=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    if probe_res.returncode == 0 and probe_res.stdout.strip():
                        parts = probe_res.stdout.strip().split(",")
                        if len(parts) >= 2:
                            out_w = int(parts[0])
                            out_h = int(parts[1])
                            logger.info("Output resolution: %dx%d", out_w, out_h)
            except Exception as e:
                logger.warning("Could not detect output resolution: %s", e)

            exceeds_nvenc_limit = out_w > 4096 or out_h > 4096

            # ── Encoder selection: GPU (NVENC/AMF/QSV) or CPU (libx264) ──
            # Uses test-based detection: actually encodes a tiny video to
            # verify the encoder works on this specific GPU hardware.
            # Supports NVIDIA (nvenc), AMD (amf), Intel (qsv), and iGPU.
            hw_encoder = None
            encoder_label = "libx264 (CPU)"
            if not exceeds_nvenc_limit:
                hw_encoder, encoder_label = detect_working_hw_encoder()
                logger.info(
                    "HW encoder check: found=%s, label=%s",
                    hw_encoder or "none", encoder_label,
                )
            else:
                logger.info(
                    "Output %dx%d exceeds HW H.264 limit (4096), using libx264",
                    out_w, out_h,
                )

            def _build_encode_cmd(ff_base, use_hw_encoder, use_encoder_label):
                """Build the FFmpeg encode command with the given encoder."""
                ff_out = list(ff_base)
                if use_hw_encoder:
                    if use_hw_encoder == "h264_nvenc":
                        preset_args = ["-preset", "p1"]
                    elif use_hw_encoder == "h264_amf":
                        preset_args = ["-quality", "speed"]
                    else:  # h264_qsv
                        preset_args = ["-preset", "veryfast"]
                    ff_out.extend(["-c:v", use_hw_encoder] + preset_args + [
                        "-b:v", "50M", "-maxrate", "55M",
                        "-bufsize", "100M", "-pix_fmt", "yuv420p",
                    ])
                else:
                    ff_out.extend([
                        "-c:v", "libx264", "-b:v", "50M", "-maxrate", "55M",
                        "-bufsize", "100M", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    ])
                if has_audio and not mute_audio:
                    ff_out.extend(["-c:a", "aac", "-b:a", "320k"])
                ff_out.extend(["-r", str(effective_fps)])
                ff_out.extend(["-movflags", "+faststart", out_path])
                return ff_out

            def _run_encode(use_hw_encoder, use_encoder_label):
                """Run FFmpeg encoding. Returns (success, error_lines)."""
                if progress_cb:
                    if use_hw_encoder:
                        progress_cb("merge", 78,
                                    f"Encoding with {use_encoder_label} 🚀...")
                    else:
                        msg = (f"Encoding with libx264 (CPU) — {out_w}x{out_h} exceeds GPU limit..."
                               if exceeds_nvenc_limit
                               else "Encoding with libx264 (CPU)...")
                        progress_cb("merge", 78, msg)

                ff_cmd = _build_encode_cmd(ff, use_hw_encoder, use_encoder_label)
                logger.info("FFmpeg encode cmd: %s", " ".join(ff_cmd))

                fp = subprocess.Popen(
                    ff_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                ff_lines = []
                for line in iter(fp.stdout.readline, ""):
                    ff_lines.append(line.strip())
                    frame_match = re.search(r"frame=\s*(\d+)", line)
                    if frame_match and total_frames > 0 and progress_cb:
                        cf = int(frame_match.group(1))
                        pct = min(78 + int((cf / total_frames) * 20), 98)
                        progress_cb("merge", pct,
                                    f"[{use_encoder_label}] Encoding frame {cf}/{total_frames}")
                fp.wait()
                return fp.returncode == 0, ff_lines

            # Try HW encoder first, fallback to CPU if it fails
            encode_ok = False
            if hw_encoder:
                encode_ok, ff_lines = _run_encode(hw_encoder, encoder_label)
                if not encode_ok:
                    err_detail = "\n".join(ff_lines[-5:])
                    logger.warning(
                        "GPU encoder %s failed, falling back to libx264 (CPU). Error: %s",
                        hw_encoder, err_detail[:200],
                    )
                    # Remove failed output
                    if os.path.exists(out_path):
                        try:
                            os.remove(out_path)
                        except Exception:
                            pass
                    # Fallback to CPU
                    hw_encoder = None
                    encoder_label = "libx264 (CPU)"

            if not encode_ok:
                if not exceeds_nvenc_limit and hw_encoder is None:
                    logger.info("Using libx264 (CPU) as fallback encoder")
                encode_ok, ff_lines = _run_encode(None, "libx264 (CPU)")

            if not encode_ok:
                err = "\n".join(ff_lines[-5:])
                raise Exception(f"FFmpeg encoding failed: {err[:300]}")

            if not os.path.exists(out_path):
                raise Exception("Output file not created")

            mb = os.path.getsize(out_path) / (1024 * 1024)
            if progress_cb:
                progress_cb("done", 100, f"✅ {out_name} ({mb:.1f} MB)")

            return out_path

        finally:
            # Cleanup work directory
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)

    def cancel(self):
        """Cancel current processing."""
        self._cancel = True
