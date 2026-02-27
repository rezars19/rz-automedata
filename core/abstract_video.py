"""
RZ Studio — Abstract Video Background Generator Engine
Generates commercially valuable abstract motion backgrounds for stock platforms.

Supports:
    - 15 unique background pattern types
    - 15 overlay effect types
    - Custom 4-color RGB palettes
    - Auto color harmony generation
    - MP4/MOV output with H.264 codec
    - Live preview frame generation
"""

import cv2
import numpy as np
import math
import colorsys
import random
import threading
import os
import time
import logging
from typing import List, Tuple, Optional, Callable

logger = logging.getLogger(__name__)


def _get_ffmpeg_path() -> str:
    """Find FFmpeg executable — bundled first, then system PATH."""
    import shutil
    import sys
    # 1. Check bundled path (next to exe or in project root)
    search_bases = [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
    if getattr(sys, 'frozen', False):
        search_bases.insert(0, os.path.dirname(sys.executable))
    for base in search_bases:
        # ../ffmpeg/ffmpeg.exe (one level up from core/)
        bundled = os.path.join(os.path.dirname(base), "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(bundled):
            return bundled
        # same level: ffmpeg/ffmpeg.exe
        bundled2 = os.path.join(base, "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(bundled2):
            return bundled2
    # 2. Check sys._MEIPASS (PyInstaller bundled)
    if hasattr(sys, '_MEIPASS'):
        bundled3 = os.path.join(sys._MEIPASS, "ffmpeg", "ffmpeg.exe")
        if os.path.isfile(bundled3):
            return bundled3
    # 3. Check LOCALAPPDATA fallback (auto-downloaded)
    for env_var in ["LOCALAPPDATA", "APPDATA"]:
        appdata = os.environ.get(env_var, "")
        if appdata:
            fallback = os.path.join(appdata, "RZAutomedata", "ffmpeg", "ffmpeg.exe")
            if os.path.isfile(fallback):
                return fallback
    # 4. Fallback to system PATH
    found = shutil.which("ffmpeg")
    if found:
        return found
    # 5. Last resort — just return "ffmpeg" and let OS figure it out
    return "ffmpeg"


# ═══════════════════════════════════════════════════════════════════════════════
# GPU ENCODER DETECTION (shared across all modules)
# ═══════════════════════════════════════════════════════════════════════════════

# Module-level cache so we only test once per app session
_hw_encoder_cache = None  # Will be set to (encoder_name_or_None, label_str)


def detect_working_hw_encoder(force_recheck=False):
    """Detect a working hardware H.264 encoder by actually test-encoding.

    Simply checking FFmpeg's `-encoders` list is NOT enough because most
    FFmpeg builds list h264_nvenc, h264_amf, and h264_qsv regardless of
    what GPU hardware is present.  This function encodes a tiny 1-frame
    8×8 video with each candidate to verify it really works.

    Returns:
        (encoder_name, label)  e.g. ("h264_amf", "AMF (AMD GPU)")
        or (None, "libx264 (CPU)") if no HW encoder works.

    GPU matching priority:
        1. Match encoder to detected GPU vendor (AMD → amf, NVIDIA → nvenc, Intel → qsv)
        2. Fall through remaining encoders in order
    """
    global _hw_encoder_cache
    if _hw_encoder_cache is not None and not force_recheck:
        return _hw_encoder_cache

    import subprocess, tempfile

    ffmpeg = _get_ffmpeg_path()

    # All candidate encoders with their vendor-specific preset args
    ALL_ENCODERS = [
        ("h264_nvenc", "NVENC (NVIDIA GPU)", ["-preset", "p1"]),
        ("h264_amf",   "AMF (AMD GPU)",      ["-quality", "speed"]),
        ("h264_qsv",   "QSV (Intel GPU)",    ["-preset", "veryfast"]),
    ]

    # Step 1: Detect GPU vendor via WMI to prioritize the right encoder
    gpu_vendor = None  # "nvidia", "amd", "intel", or None
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode == 0 and r.stdout.strip():
            gpu_text = r.stdout.lower()
            if any(k in gpu_text for k in ["nvidia", "geforce", "rtx ", "gtx "]):
                gpu_vendor = "nvidia"
            elif any(k in gpu_text for k in ["amd", "radeon"]):
                gpu_vendor = "amd"
            elif "intel" in gpu_text:
                gpu_vendor = "intel"
            logger.info("GPU vendor detected via WMI: %s", gpu_vendor or "unknown")
    except Exception as e:
        logger.debug("WMI GPU vendor detection failed: %s", e)

    # Step 2: Reorder encoder list to try matching vendor first
    vendor_encoder_map = {
        "nvidia": "h264_nvenc",
        "amd":    "h264_amf",
        "intel":  "h264_qsv",
    }
    ordered_encoders = list(ALL_ENCODERS)
    if gpu_vendor and gpu_vendor in vendor_encoder_map:
        preferred = vendor_encoder_map[gpu_vendor]
        # Move the preferred encoder to the front
        ordered_encoders.sort(key=lambda x: 0 if x[0] == preferred else 1)

    # Step 3: Check which encoders are listed by FFmpeg
    enc_list_text = ""
    try:
        enc_check = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        enc_list_text = enc_check.stdout
    except Exception as e:
        logger.debug("FFmpeg encoder listing failed: %s", e)

    # Step 4: Test each encoder with a real tiny encode
    for enc_name, enc_label, preset_args in ordered_encoders:
        if enc_name not in enc_list_text:
            # Encoder not even listed — skip
            continue

        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name

            # Encode a single 8x8 black frame — takes <100ms
            test_cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=black:s=8x8:d=0.04:r=25",
                "-c:v", enc_name,
            ] + preset_args + [
                "-pix_fmt", "yuv420p",
                tmp_path,
            ]
            result = subprocess.run(
                test_cmd,
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            # Clean up temp file
            try:
                os.remove(tmp_path)
            except Exception:
                pass

            if result.returncode == 0:
                logger.info(
                    "✅ HW encoder verified: %s (%s) — works on this system",
                    enc_name, enc_label,
                )
                _hw_encoder_cache = (enc_name, enc_label)
                return _hw_encoder_cache
            else:
                logger.info(
                    "❌ HW encoder %s listed but FAILED test: %s",
                    enc_name, result.stderr[:200] if result.stderr else "unknown",
                )
        except Exception as e:
            logger.debug("HW encoder test for %s failed: %s", enc_name, e)
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    logger.info("No working HW encoder found, will use libx264 (CPU)")
    _hw_encoder_cache = (None, "libx264 (CPU)")
    return _hw_encoder_cache


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND PATTERN DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

BACKGROUND_PATTERNS = {
    "gradient_flow": {
        "name": "Gradient Flow",
        "desc": "Smooth animated gradient transitions between colors",
        "icon": "🌊",
    },
    "particle_wave": {
        "name": "Particle Wave",
        "desc": "Flowing particles forming wave-like patterns",
        "icon": "✨",
    },

    "liquid_marble": {
        "name": "Liquid Marble",
        "desc": "Organic marble-like fluid animation",
        "icon": "🫧",
    },
    "aurora_borealis": {
        "name": "Aurora Borealis",
        "desc": "Northern lights curtain effect",
        "icon": "🌌",
    },
    "smoke_plume": {
        "name": "Smoke Plume",
        "desc": "Soft smoke/fog rising and swirling",
        "icon": "🌫️",
    },
    "fractal_tunnel": {
        "name": "Fractal Tunnel",
        "desc": "Hypnotic tunnel zoom with fractal patterns",
        "icon": "🌀",
    },
    "wave_interference": {
        "name": "Wave Interference",
        "desc": "Overlapping sine wave interference patterns",
        "icon": "〰️",
    },
    "diamond_grid": {
        "name": "Diamond Grid",
        "desc": "Animated diamond/rhombus tessellation",
        "icon": "💠",
    },
    "plasma_field": {
        "name": "Plasma Field",
        "desc": "Classic plasma effect with flowing colors",
        "icon": "🔥",
    },
    "spiral_vortex": {
        "name": "Spiral Vortex",
        "desc": "Rotating spiral with color transitions",
        "icon": "🌪️",
    },
    "stripe_cascade": {
        "name": "Stripe Cascade",
        "desc": "Diagonal stripes cascading with color shifts",
        "icon": "📊",
    },
    "dot_matrix": {
        "name": "Dot Matrix",
        "desc": "Animated halftone dot pattern with depth",
        "icon": "⚫",
    },
    "nebula_cloud": {
        "name": "Nebula Cloud",
        "desc": "Deep space nebula with swirling cosmic dust",
        "icon": "🌠",
    },
    "kaleidoscope": {
        "name": "Kaleidoscope",
        "desc": "Mesmerizing mirror-symmetry kaleidoscope animation",
        "icon": "🔮",
    },
    "fluid_ink": {
        "name": "Fluid Ink",
        "desc": "Ink drops spreading in water with organic flow",
        "icon": "🎨",
    },

    "ripple_pond": {
        "name": "Ripple Pond",
        "desc": "Concentric water ripples with interference",
        "icon": "💧",
    },

    "holographic": {
        "name": "Holographic",
        "desc": "Iridescent holographic color shifting",
        "icon": "🌈",
    },
    "topographic": {
        "name": "Topographic",
        "desc": "Animated topographic contour map lines",
        "icon": "🗺️",
    },
    "hexagon_grid": {
        "name": "Hexagon Grid",
        "desc": "Animated honeycomb hexagonal grid pattern",
        "icon": "🔲",
    },
    "voronoi_cells": {
        "name": "Voronoi Cells",
        "desc": "Organic Voronoi tessellation with moving seeds",
        "icon": "🧬",
    },

    "watercolor_blend": {
        "name": "Watercolor Blend",
        "desc": "Soft watercolor paint spreading and blending",
        "icon": "🖌️",
    },
    "ocean_waves": {
        "name": "Ocean Waves",
        "desc": "Rolling ocean wave simulation with foam",
        "icon": "🌊",
    },
    "rolling_clouds": {
        "name": "Rolling Clouds",
        "desc": "Soft cloud formations drifting across the sky",
        "icon": "☁️",
    },
    "geometric_bloom": {
        "name": "Geometric Bloom",
        "desc": "Sacred geometry flower patterns expanding and contracting",
        "icon": "🌸",
    },
    "color_explosion": {
        "name": "Color Explosion",
        "desc": "Vibrant radial burst of saturated colors from center",
        "icon": "🎆",
    },
    "oil_slick": {
        "name": "Oil Slick",
        "desc": "Iridescent oil-on-water rainbow color shifting",
        "icon": "💎",
    },
    "prismatic_waves": {
        "name": "Prismatic Waves",
        "desc": "Full spectrum prismatic waves flowing across screen",
        "icon": "🔮",
    },
    "gradient_mesh": {
        "name": "Gradient Mesh",
        "desc": "Rich overlapping color gradients filling every pixel",
        "icon": "🎨",
    },
    "chromatic_pulse": {
        "name": "Chromatic Pulse",
        "desc": "Pulsating concentric rings of vivid saturated colors",
        "icon": "💜",
    },
    "color_smoke": {
        "name": "Color Smoke",
        "desc": "Vivid smoke plumes in saturated flowing colors",
        "icon": "🌈",
    },
    "rainbow_flow": {
        "name": "Rainbow Flow",
        "desc": "Smooth flowing rainbow gradient transitions",
        "icon": "🏳️‍🌈",
    },
    "paint_pour": {
        "name": "Paint Pour",
        "desc": "Acrylic paint pour with rich color mixing",
        "icon": "🎨",
    },
    "silk_fabric": {
        "name": "Silk Fabric",
        "desc": "Flowing silk fabric with iridescent color folds",
        "icon": "🧣",
    },
    "neon_waves": {
        "name": "Neon Waves",
        "desc": "Bright neon-colored flowing wave patterns",
        "icon": "💡",
    },
    "lava_flow": {
        "name": "Lava Flow",
        "desc": "Flowing molten lava with vivid warm colors",
        "icon": "🌋",
    },
    "color_vortex": {
        "name": "Color Vortex",
        "desc": "Spinning vortex whirlpool of saturated colors",
        "icon": "🌪️",
    },
    "aurora_curtain": {
        "name": "Aurora Curtain",
        "desc": "Full-color dancing aurora curtain effect",
        "icon": "🌌",
    },
    "marble_ink": {
        "name": "Marble Ink",
        "desc": "Colorful ink marble pattern in water",
        "icon": "🫧",
    },
    "electric_gradient": {
        "name": "Electric Gradient",
        "desc": "Sharp vivid gradient transitions with energy",
        "icon": "⚡",
    },
    "color_cells": {
        "name": "Color Cells",
        "desc": "Organic cellular pattern in saturated colors",
        "icon": "🧬",
    },
    "neon_grid": {
        "name": "Neon Grid",
        "desc": "Glowing animated neon wireframe grid",
        "icon": "🔲",
    },
    "paint_drip": {
        "name": "Paint Drip",
        "desc": "Thick paint dripping with rich color blends",
        "icon": "🖌️",
    },
    "crystal_facets": {
        "name": "Crystal Facets",
        "desc": "Faceted crystal blocks with vibrant color fills",
        "icon": "💎",
    },
    "thermal_map": {
        "name": "Thermal Map",
        "desc": "Animated thermal heat map in vivid colors",
        "icon": "🌡️",
    },
    "color_storm": {
        "name": "Color Storm",
        "desc": "Turbulent storm of mixed vibrant colors",
        "icon": "🌩️",
    },
    "pixel_mosaic": {
        "name": "Pixel Mosaic",
        "desc": "Animated colorful pixel mosaic grid pattern",
        "icon": "🟩",
    },
    "liquid_chrome": {
        "name": "Liquid Chrome",
        "desc": "Liquid metal chrome with rainbow reflections",
        "icon": "🪩",
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# OVERLAY EFFECT DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

OVERLAY_EFFECTS = {
    "none": {
        "name": "None",
        "desc": "No overlay effect",
        "icon": "❌",
    },
    "light_leak": {
        "name": "Light Leak",
        "desc": "Film-style light leak flares",
        "icon": "☀️",
    },
    "film_grain": {
        "name": "Film Grain",
        "desc": "Subtle analog film grain texture",
        "icon": "📽️",
    },
    "lens_flare": {
        "name": "Lens Flare",
        "desc": "Cinematic lens flare sweeping across",
        "icon": "🔆",
    },
    "dust_particles": {
        "name": "Dust Particles",
        "desc": "Floating micro dust particles",
        "icon": "💫",
    },

    "chromatic_aberration": {
        "name": "Chromatic Shift",
        "desc": "RGB split/chromatic aberration effect",
        "icon": "🌈",
    },

    "sparkle_stars": {
        "name": "Sparkle Stars",
        "desc": "Twinkling star sparkle overlay",
        "icon": "⭐",
    },
    "prism_rainbow": {
        "name": "Prism Rainbow",
        "desc": "Prismatic rainbow light dispersion",
        "icon": "🌈",
    },
    "soft_blur_edge": {
        "name": "Soft Edge Blur",
        "desc": "Gaussian blur on edges keeping center sharp",
        "icon": "🔲",
    },
    "radial_rays": {
        "name": "Radial Rays",
        "desc": "Sun-like radial light rays from center",
        "icon": "☀️",
    },

    "noise_texture": {
        "name": "Noise Texture",
        "desc": "Perlin-style soft noise overlay",
        "icon": "🏔️",
    },
    "motion_streak": {
        "name": "Motion Streak",
        "desc": "Horizontal motion blur streaks",
        "icon": "💨",
    },

    "god_rays": {
        "name": "God Rays",
        "desc": "Volumetric light beams streaming from above",
        "icon": "☀️",
    },
    "color_wash": {
        "name": "Color Wash",
        "desc": "Sweeping color gradient wash across frame",
        "icon": "🎨",
    },
    "kaleidoscope_overlay": {
        "name": "Kaleidoscope Refract",
        "desc": "Prismatic kaleidoscope light refraction",
        "icon": "💎",
    },
    "heat_haze": {
        "name": "Heat Haze",
        "desc": "Shimmering heat distortion ripples",
        "icon": "🌡️",
    },
    "snow_fall": {
        "name": "Snow Fall",
        "desc": "Gentle falling snow particles",
        "icon": "❄️",
    },
    "rain_drops": {
        "name": "Rain Drops",
        "desc": "Falling rain streaks across frame",
        "icon": "🌧️",
    },
    "bubble_float": {
        "name": "Bubble Float",
        "desc": "Translucent floating bubbles rising",
        "icon": "🫧",
    },
    "confetti": {
        "name": "Confetti",
        "desc": "Colorful confetti particles falling",
        "icon": "🎊",
    },
    "golden_dust": {
        "name": "Golden Dust",
        "desc": "Floating golden dust particles shimmering",
        "icon": "✨",
    },
    "fog_drift": {
        "name": "Fog Drift",
        "desc": "Drifting fog and mist layers",
        "icon": "🌫️",
    },
    "light_rays_top": {
        "name": "Light Rays Top",
        "desc": "Light rays streaming from the top",
        "icon": "🔦",
    },

    "light_streak": {
        "name": "Light Streak",
        "desc": "Diagonal light streaks across frame",
        "icon": "💫",
    },
    "edge_glow": {
        "name": "Edge Glow",
        "desc": "Glowing neon edges around the frame",
        "icon": "🔮",
    },
    "wave_distort": {
        "name": "Wave Distort",
        "desc": "Subtle wave distortion overlay",
        "icon": "🌊",
    },

    "vintage_fade": {
        "name": "Vintage Fade",
        "desc": "Warm vintage color fade effect",
        "icon": "📷",
    },
    "shimmer": {
        "name": "Shimmer",
        "desc": "Subtle sparkle shimmer across the frame",
        "icon": "💫",
    },
    "gradient_wipe": {
        "name": "Gradient Wipe",
        "desc": "Animated gradient sweep across frame",
        "icon": "🎬",
    },

    "ripple_overlay": {
        "name": "Ripple Distort",
        "desc": "Concentric ripple distortion from center",
        "icon": "💧",
    },
    "star_field": {
        "name": "Star Field",
        "desc": "Twinkling star field overlay",
        "icon": "⭐",
    },
    "smoke_wisp": {
        "name": "Smoke Wisp",
        "desc": "Subtle smoke wisps drifting across",
        "icon": "💨",
    },
    "pulse_ring": {
        "name": "Pulse Ring",
        "desc": "Expanding concentric pulse rings",
        "icon": "🔵",
    },
    "diamond_sparkle": {
        "name": "Diamond Sparkle",
        "desc": "Bright diamond sparkle highlights",
        "icon": "💎",
    },

    "color_overlay": {
        "name": "Color Overlay",
        "desc": "Moving color gradient tint overlay",
        "icon": "🎨",
    },

    "bloom_glow": {
        "name": "Bloom Glow",
        "desc": "Soft bloom glow on bright areas",
        "icon": "🌟",
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# COLOR HARMONY UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB to hex string."""
    return f"#{r:02x}{g:02x}{b:02x}"


def generate_harmony_colors(harmony_type: str = "random") -> List[str]:
    """
    Generate 4 harmonious colors based on color theory.
    Returns list of 4 hex color strings.
    """
    base_hue = random.random()

    if harmony_type == "analogous":
        hues = [base_hue, (base_hue + 0.08) % 1, (base_hue + 0.16) % 1, (base_hue + 0.24) % 1]
    elif harmony_type == "complementary":
        hues = [base_hue, (base_hue + 0.05) % 1, (base_hue + 0.5) % 1, (base_hue + 0.55) % 1]
    elif harmony_type == "triadic":
        hues = [base_hue, (base_hue + 0.33) % 1, (base_hue + 0.66) % 1, (base_hue + 0.15) % 1]
    elif harmony_type == "split_complementary":
        hues = [base_hue, (base_hue + 0.42) % 1, (base_hue + 0.58) % 1, (base_hue + 0.08) % 1]
    elif harmony_type == "tetradic":
        hues = [base_hue, (base_hue + 0.25) % 1, (base_hue + 0.5) % 1, (base_hue + 0.75) % 1]
    elif harmony_type == "warm":
        base_hue = random.uniform(0.0, 0.12)  # Red-yellow range
        hues = [base_hue, (base_hue + 0.04) % 1, (base_hue + 0.08) % 1, (base_hue + 0.12) % 1]
    elif harmony_type == "cool":
        base_hue = random.uniform(0.5, 0.72)  # Blue-cyan range
        hues = [base_hue, (base_hue + 0.04) % 1, (base_hue + 0.08) % 1, (base_hue + 0.12) % 1]
    elif harmony_type == "pastel":
        hues = [random.random() for _ in range(4)]
        colors = []
        for h in hues:
            r, g, b = colorsys.hls_to_rgb(h, 0.82, 0.45)
            colors.append(rgb_to_hex(int(r*255), int(g*255), int(b*255)))
        return colors
    elif harmony_type == "neon":
        hues = [random.random() for _ in range(4)]
        colors = []
        for h in hues:
            r, g, b = colorsys.hls_to_rgb(h, 0.55, 1.0)
            colors.append(rgb_to_hex(int(r*255), int(g*255), int(b*255)))
        return colors
    elif harmony_type == "dark_rich":
        hues = [random.random() for _ in range(4)]
        colors = []
        for h in hues:
            sat = random.uniform(0.7, 1.0)
            r, g, b = colorsys.hls_to_rgb(h, 0.3, sat)
            colors.append(rgb_to_hex(int(r*255), int(g*255), int(b*255)))
        return colors
    else:  # random
        hues = [random.random() for _ in range(4)]

    colors = []
    for i, h in enumerate(hues):
        sat = random.uniform(0.6, 1.0)
        light = random.uniform(0.35, 0.65)
        r, g, b = colorsys.hls_to_rgb(h, light, sat)
        colors.append(rgb_to_hex(int(r*255), int(g*255), int(b*255)))
    return colors


HARMONY_TYPES = [
    "analogous", "complementary", "triadic", "split_complementary",
    "tetradic", "warm", "cool", "pastel", "neon", "dark_rich", "random"
]


# ═══════════════════════════════════════════════════════════════════════════════
# FRAME RENDERING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class AbstractVideoRenderer:
    """
    Renders individual frames for abstract video backgrounds.
    Each pattern is a method that generates a frame given colors, time, and resolution.
    """

    def __init__(self, width: int, height: int, colors: List[Tuple[int, int, int]]):
        self.w = width
        self.h = height
        self.colors = colors  # List of 4 (R, G, B) tuples
        # Pre-compute coordinate grids
        self.y_grid, self.x_grid = np.mgrid[0:height, 0:width].astype(np.float32)
        self.cx = width / 2.0
        self.cy = height / 2.0

    def _blend_colors(self, t: float, idx1: int, idx2: int) -> np.ndarray:
        """Blend between two colors based on t (0-1)."""
        c1 = np.array(self.colors[idx1 % 4], dtype=np.float32)
        c2 = np.array(self.colors[idx2 % 4], dtype=np.float32)
        return (c1 * (1 - t) + c2 * t).astype(np.uint8)

    # ── PATTERN: Gradient Flow ──
    def gradient_flow(self, t: float) -> np.ndarray:
        frame = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        # Multi-directional gradient that shifts over time
        angle = t * 0.5
        dx = math.cos(angle)
        dy = math.sin(angle)
        grad = (self.x_grid * dx + self.y_grid * dy)
        grad = (grad - grad.min()) / (grad.max() - grad.min() + 1e-6)
        # Map gradient through 4 colors
        phase = (grad + t * 0.3) % 1.0
        for y in range(self.h):
            for x in range(0, self.w, 4):
                end_x = min(x + 4, self.w)
                p = phase[y, x]
                idx = int(p * 3.99)
                frac = (p * 3.99) - idx
                c1 = np.array(self.colors[idx % 4], dtype=np.float32)
                c2 = np.array(self.colors[(idx + 1) % 4], dtype=np.float32)
                color = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
                frame[y, x:end_x] = color
        # Gaussian blur for smoothness
        frame = cv2.GaussianBlur(frame, (31, 31), 0)
        return frame

    def _fast_gradient_flow(self, t: float) -> np.ndarray:
        """Optimized gradient flow using vectorized operations."""
        angle = t * 0.5
        dx = math.cos(angle)
        dy = math.sin(angle)
        grad = (self.x_grid * dx + self.y_grid * dy)
        grad = (grad - grad.min()) / (grad.max() - grad.min() + 1e-6)
        phase = (grad + t * 0.3) % 1.0

        # Vectorized color mapping
        scaled = phase * 3.99
        idx = scaled.astype(np.int32)
        idx = np.clip(idx, 0, 3)
        frac = (scaled - idx)[..., np.newaxis]

        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (31, 31), 0)
        return frame

    # ── PATTERN: Particle Wave ──
    def particle_wave(self, t: float) -> np.ndarray:
        frame = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        # Background gradient
        bg_grad = self.y_grid / self.h
        c_bg1 = np.array(self.colors[0], dtype=np.float32) * 0.3
        c_bg2 = np.array(self.colors[1], dtype=np.float32) * 0.3
        frame = (c_bg1[np.newaxis, np.newaxis, :] * (1 - bg_grad[..., np.newaxis]) +
                 c_bg2[np.newaxis, np.newaxis, :] * bg_grad[..., np.newaxis]).astype(np.uint8)

        # Draw particles along wave
        num_particles = 200
        for i in range(num_particles):
            px = (i / num_particles * self.w + t * 80) % self.w
            wave_y = self.cy + math.sin(px * 0.02 + t * 2 + i * 0.1) * self.h * 0.2
            wave_y += math.sin(px * 0.006 + t * 0.5) * self.h * 0.1
            py = int(wave_y)
            if 0 <= py < self.h:
                color_idx = i % 4
                radius = random.randint(2, 6)
                color = tuple(int(c) for c in self.colors[color_idx])
                cv2.circle(frame, (int(px), py), radius, color, -1, cv2.LINE_AA)

        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Geometric Mesh ──
    def geometric_mesh(self, t: float) -> np.ndarray:
        frame = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        # Dark background
        frame[:] = (np.array(self.colors[0]) * 0.15).astype(np.uint8)

        spacing = 80
        offset_x = (t * 30) % spacing
        offset_y = (t * 20) % spacing

        points = []
        for gy in range(-1, self.h // spacing + 2):
            for gx in range(-1, self.w // spacing + 2):
                px = int(gx * spacing + offset_x + math.sin(t + gy * 0.5) * 15)
                py = int(gy * spacing + offset_y + math.cos(t + gx * 0.5) * 15)
                points.append((px, py))

        # Draw connections
        for i, p1 in enumerate(points):
            for j, p2 in enumerate(points):
                if i >= j:
                    continue
                dist = math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)
                if dist < spacing * 1.5:
                    alpha = max(0, 1 - dist / (spacing * 1.5))
                    color = tuple(int(c * alpha * 0.6) for c in self.colors[(i+j) % 4])
                    cv2.line(frame, p1, p2, color, 1, cv2.LINE_AA)

        # Draw nodes
        for i, p in enumerate(points):
            if 0 <= p[0] < self.w and 0 <= p[1] < self.h:
                color = tuple(int(c) for c in self.colors[i % 4])
                cv2.circle(frame, p, 4, color, -1, cv2.LINE_AA)

        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: Liquid Marble ──
    def liquid_marble(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 4
        ny = self.y_grid / self.h * 4

        val = (np.sin(nx * 2 + t) + np.sin(ny * 3 + t * 0.7) +
               np.sin((nx + ny) * 1.5 + t * 1.3) +
               np.sin(np.sqrt(nx**2 + ny**2) * 2 + t * 0.5)) / 4.0

        val = (val + 1) / 2.0  # Normalize to 0-1
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]

        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (15, 15), 0)
        return frame

    # ── PATTERN: Neon Glow ──
    def neon_glow(self, t: float) -> np.ndarray:
        frame = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        # Dark base
        frame[:] = (10, 5, 20)

        for i in range(6):
            cx = self.w * (0.2 + 0.6 * math.sin(t * 0.3 + i * 1.2))
            cy = self.h * (0.2 + 0.6 * math.cos(t * 0.4 + i * 0.8))
            radius = int(self.w * 0.15 + self.w * 0.1 * math.sin(t + i))
            color = self.colors[i % 4]

            overlay = np.zeros_like(frame, dtype=np.float32)
            cv2.circle(overlay, (int(cx), int(cy)), radius, color, -1, cv2.LINE_AA)
            overlay = cv2.GaussianBlur(overlay, (101, 101), 0)
            frame = np.clip(frame.astype(np.float32) + overlay * 0.7, 0, 255).astype(np.uint8)

        return frame

    # ── PATTERN: Bokeh Circles ──
    def bokeh_circles(self, t: float) -> np.ndarray:
        frame = np.zeros((self.h, self.w, 3), dtype=np.float32)
        # Gradient background
        bg = self.y_grid / self.h
        c1 = np.array(self.colors[0], dtype=np.float32) * 0.2
        c2 = np.array(self.colors[1], dtype=np.float32) * 0.2
        frame = c1[np.newaxis, np.newaxis, :] * (1 - bg[..., np.newaxis]) + \
                c2[np.newaxis, np.newaxis, :] * bg[..., np.newaxis]

        np.random.seed(42)
        num_bokeh = 25
        positions = np.random.random((num_bokeh, 2))
        radii = np.random.randint(15, 60, num_bokeh)

        for i in range(num_bokeh):
            bx = int((positions[i, 0] * self.w + t * (20 + i * 3)) % self.w)
            by = int((positions[i, 1] * self.h + math.sin(t * 0.5 + i) * 30) % self.h)
            radius = int(radii[i])
            alpha = 0.15 + 0.15 * math.sin(t * 2 + i)
            color = self.colors[i % 4]

            # Draw directly on float frame
            cv2.circle(frame, (bx, by), radius, tuple(float(c) * alpha for c in color), 2, cv2.LINE_AA)
            inner_color = tuple(float(c) * 0.3 * alpha for c in color)
            cv2.circle(frame, (bx, by), max(1, radius - 4), inner_color, -1, cv2.LINE_AA)

        frame = cv2.GaussianBlur(frame, (11, 11), 0)
        return np.clip(frame, 0, 255).astype(np.uint8)

    # ── PATTERN: Aurora Borealis (vectorized) ──
    def aurora_borealis(self, t: float) -> np.ndarray:
        # Dark sky base
        frame = np.zeros((self.h, self.w, 3), dtype=np.float32)
        bg_val = self.y_grid / self.h * 20
        frame[:, :, 0] = bg_val
        frame[:, :, 1] = bg_val * 0.5
        frame[:, :, 2] = bg_val * 2

        # Aurora curtains - vectorized along x
        x_range = np.arange(self.w, dtype=np.float32)
        for layer in range(4):
            color = np.array(self.colors[layer], dtype=np.float32)
            wave = np.sin(x_range * 0.01 + t * (0.5 + layer * 0.2) + layer) * 0.3
            wave += np.sin(x_range * 0.005 + t * 0.3 + layer * 2) * 0.2
            center_y = (self.h * (0.25 + wave)).astype(np.float32)  # (W,)
            spread = self.h * 0.15

            # Compute distance of each pixel from the curtain center
            y_dist = self.y_grid - center_y[np.newaxis, :]  # (H, W)
            alpha = np.clip(1 - np.abs(y_dist) / spread, 0, 1) * 0.4
            modulation = 0.5 + 0.5 * np.sin(t * 3 + x_range[np.newaxis, :] * 0.02 + layer)
            alpha *= modulation

            frame += alpha[..., np.newaxis] * color[np.newaxis, np.newaxis, :]

        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (9, 9), 0)
        return frame

    # ── PATTERN: Smoke Plume ──
    def smoke_plume(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 5
        ny = self.y_grid / self.h * 5

        # Multiple noise layers simulating smoke
        v1 = np.sin(nx * 1.5 + t * 0.8) * np.cos(ny * 2 - t * 0.5)
        v2 = np.sin(nx * 3 + ny * 2 + t * 1.2) * 0.5
        v3 = np.cos(nx * 0.5 + ny * 3 - t * 0.3) * 0.3
        val = (v1 + v2 + v3 + 1.5) / 3.0
        val = np.clip(val, 0, 1)

        c_arr = np.array(self.colors, dtype=np.float32)
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (21, 21), 0)
        return frame

    # ── PATTERN: Fractal Tunnel ──
    def fractal_tunnel(self, t: float) -> np.ndarray:
        dx = self.x_grid - self.cx
        dy = self.y_grid - self.cy
        dist = np.sqrt(dx**2 + dy**2) + 1e-6
        angle = np.arctan2(dy, dx)

        # Tunnel mapping
        tunnel_val = (1.0 / dist * self.w * 2 + t * 50) % 1.0
        spiral = (angle / (2 * math.pi) + t * 0.2 + tunnel_val * 0.5) % 1.0

        scaled = spiral * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]

        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)

        # Darken edges
        vignette = np.clip(dist / (max(self.w, self.h) * 0.5), 0, 1)
        vignette = (1 - vignette * 0.6)[..., np.newaxis]
        frame = (frame.astype(np.float32) * vignette).astype(np.uint8)

        return frame

    # ── PATTERN: Wave Interference ──
    def wave_interference(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 8
        ny = self.y_grid / self.h * 8

        val = (np.sin(nx * 2 + t * 3) + np.sin(ny * 2 + t * 2) +
               np.sin((nx + ny) + t * 1.5) + np.sin(np.sqrt(nx**2 + ny**2) * 3 + t * 2)) / 4.0
        val = (val + 1) / 2.0

        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]

        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
        return frame

    # ── PATTERN: Diamond Grid ──
    def diamond_grid(self, t: float) -> np.ndarray:
        size = 80
        offset = t * 40

        dx = (self.x_grid + offset) % size - size / 2
        dy = (self.y_grid + offset * 0.7) % size - size / 2
        diamond = (np.abs(dx) + np.abs(dy)) / (size * 0.5)
        diamond = np.clip(diamond, 0, 1)

        # Color based on position
        grid_x = ((self.x_grid + offset) // size).astype(np.int32)
        grid_y = ((self.y_grid + offset * 0.7) // size).astype(np.int32)
        color_idx = (grid_x + grid_y) % 4

        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[color_idx % 4]
        c2 = c_arr[(color_idx + 1) % 4]
        frame = (c1 * (1 - diamond[..., np.newaxis]) + c2 * diamond[..., np.newaxis]).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: Plasma Field ──
    def plasma_field(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 6
        ny = self.y_grid / self.h * 6

        v1 = np.sin(nx + t)
        v2 = np.sin(ny + t * 0.7)
        v3 = np.sin(nx + ny + t * 1.3)
        v4 = np.sin(np.sqrt((nx - 3)**2 + (ny - 3)**2) + t)

        val = (v1 + v2 + v3 + v4) / 4.0
        val = (val + 1) / 2.0

        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]

        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
        return frame

    # ── PATTERN: Spiral Vortex ──
    def spiral_vortex(self, t: float) -> np.ndarray:
        dx = self.x_grid - self.cx
        dy = self.y_grid - self.cy
        dist = np.sqrt(dx**2 + dy**2)
        angle = np.arctan2(dy, dx)

        spiral = (angle + dist * 0.02 - t * 2) / (2 * math.pi) % 1.0

        scaled = spiral * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]

        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)

        # Center glow
        glow = np.exp(-dist**2 / (self.w * 100))
        frame = np.clip(frame.astype(np.float32) + glow[..., np.newaxis] * 60, 0, 255).astype(np.uint8)
        return frame

    # ── PATTERN: Stripe Cascade ──
    def stripe_cascade(self, t: float) -> np.ndarray:
        stripe_w = 60
        diag = (self.x_grid * 0.7 + self.y_grid * 0.7 + t * 100)
        stripe_idx = (diag / stripe_w).astype(np.int32) % 4
        frac = (diag / stripe_w) % 1.0

        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[stripe_idx % 4]
        c2 = c_arr[(stripe_idx + 1) % 4]

        # Smooth stripe edges
        edge = np.clip(frac * 5, 0, 1)
        edge = np.where(frac > 0.8, np.clip((1 - frac) * 5, 0, 1), edge)[..., np.newaxis]

        frame = (c1 * edge + c2 * (1 - edge)).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: Dot Matrix ──
    def dot_matrix(self, t: float) -> np.ndarray:
        frame = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        frame[:] = (np.array(self.colors[0]) * 0.1).astype(np.uint8)

        spacing = 30
        max_radius = spacing // 2 - 2

        for gy in range(0, self.h + spacing, spacing):
            for gx in range(0, self.w + spacing, spacing):
                px = gx + int(math.sin(t + gy * 0.05) * 5)
                py = gy + int(math.cos(t + gx * 0.05) * 5)
                cx_d = abs(px - self.cx) / self.cx
                cy_d = abs(py - self.cy) / self.cy
                dist_factor = 1 - math.sqrt(cx_d**2 + cy_d**2) * 0.5
                pulse = 0.5 + 0.5 * math.sin(t * 3 + gx * 0.02 + gy * 0.02)
                radius = int(max_radius * dist_factor * pulse)
                radius = max(2, min(max_radius, radius))

                color_idx = (gx // spacing + gy // spacing) % 4
                color = tuple(int(c) for c in self.colors[color_idx])
                if 0 <= px < self.w and 0 <= py < self.h:
                    cv2.circle(frame, (px, py), radius, color, -1, cv2.LINE_AA)

        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: Nebula Cloud ──
    def nebula_cloud(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 3
        ny = self.y_grid / self.h * 3
        # Multiple noise octaves for cloud-like structure
        v1 = np.sin(nx * 1.5 + t * 0.3) * np.cos(ny * 2.0 - t * 0.2) * 0.5
        v2 = np.sin(nx * 3.0 + ny * 1.5 + t * 0.5) * 0.3
        v3 = np.cos(np.sqrt((nx - 1.5)**2 + (ny - 1.5)**2) * 2 + t * 0.4) * 0.4
        v4 = np.sin(nx * 5 + t) * np.sin(ny * 5 - t * 0.7) * 0.15
        val = (v1 + v2 + v3 + v4 + 1) / 2.0
        val = np.clip(val, 0, 1)
        # Dark space background with nebula colors
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        # Darken edges for space look
        dist = np.sqrt((self.x_grid - self.cx)**2 + (self.y_grid - self.cy)**2)
        vignette = 1 - np.clip(dist / max(self.w, self.h) * 0.8, 0, 0.6)
        frame = (frame * vignette[..., np.newaxis]).astype(np.uint8)
        # Add subtle stars
        np.random.seed(42)
        star_mask = np.random.random((self.h, self.w)) > 0.9997
        twinkle = (0.5 + 0.5 * np.sin(t * 5 + np.random.random((self.h, self.w)) * 10))
        frame[star_mask] = np.clip(frame[star_mask].astype(np.float32) + 200 * twinkle[star_mask, np.newaxis], 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: Kaleidoscope ──
    def kaleidoscope(self, t: float) -> np.ndarray:
        dx = self.x_grid - self.cx
        dy = self.y_grid - self.cy
        angle = np.arctan2(dy, dx)
        dist = np.sqrt(dx**2 + dy**2)
        # Create kaleidoscope symmetry (6 segments)
        segments = 6
        mirror_angle = np.abs(((angle + t * 0.5) % (2 * math.pi / segments)) - math.pi / segments)
        # Map to color
        val1 = np.sin(mirror_angle * segments + dist * 0.015 - t * 2) * 0.5 + 0.5
        val2 = np.cos(dist * 0.02 + t * 1.5) * 0.5 + 0.5
        val = (val1 + val2) / 2.0
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Fluid Ink ──
    def fluid_ink(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 4
        ny = self.y_grid / self.h * 4
        # Turbulent flow simulation
        u = np.sin(ny * 2 + t * 0.8) * np.cos(nx * 1.5 + t * 0.3)
        v = np.cos(nx * 2 - t * 0.6) * np.sin(ny * 1.5 + t * 0.5)
        distorted_x = nx + u * 0.5
        distorted_y = ny + v * 0.5
        val = (np.sin(distorted_x * 3 + t) + np.cos(distorted_y * 3 + t * 0.7) +
               np.sin((distorted_x + distorted_y) * 2 + t * 1.2)) / 3.0
        val = (val + 1) / 2.0
        # Create ink-like contrast
        val = np.clip(val * 1.3 - 0.15, 0, 1)
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (9, 9), 0)
        return frame

    # ── PATTERN: Electric Storm ──
    def electric_storm(self, t: float) -> np.ndarray:
        frame = np.zeros((self.h, self.w, 3), dtype=np.float32)
        frame[:] = np.array(self.colors[0], dtype=np.float32) * 0.06
        dx = self.x_grid - self.cx
        dy = self.y_grid - self.cy
        dist = np.sqrt(dx**2 + dy**2) / max(self.w, self.h)
        angle = np.arctan2(dy, dx)
        # Pulsating energy field (subtle background)
        energy = np.sin(dist * 20 - t * 4) * np.cos(angle * 3 + t * 2) * 0.5 + 0.5
        energy *= np.exp(-dist * 3)
        frame += energy[..., np.newaxis] * np.array(self.colors[1], dtype=np.float32) * 0.15
        # Electric arcs — sharp, contained bolts
        for i in range(8):
            arc_angle = i * math.pi / 4 + t * 0.3 + math.sin(t * 2 + i) * 0.5
            arc_dist = np.abs(angle - arc_angle)
            arc_dist = np.minimum(arc_dist, 2 * math.pi - arc_dist)
            # Sharper falloff, stronger distance decay
            arc = np.exp(-arc_dist * 25) * np.exp(-dist * 4) * (0.5 + 0.5 * np.sin(t * 8 + i * 2))
            color = np.array(self.colors[i % 4], dtype=np.float32)
            frame += arc[..., np.newaxis] * color[np.newaxis, np.newaxis, :] * 0.7
        # Subtle central glow
        glow = np.exp(-dist * 8) * (0.3 + 0.2 * np.sin(t * 3))
        frame += glow[..., np.newaxis] * np.array(self.colors[2], dtype=np.float32) * 0.3
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Ripple Pond ──
    def ripple_pond(self, t: float) -> np.ndarray:
        # Multiple ripple sources
        val = np.zeros((self.h, self.w), dtype=np.float32)
        sources = [
            (self.cx, self.cy, 0),
            (self.w * 0.25, self.h * 0.3, 1.5),
            (self.w * 0.75, self.h * 0.7, 3.0),
            (self.w * 0.6, self.h * 0.2, 4.5),
        ]
        for sx, sy, phase in sources:
            d = np.sqrt((self.x_grid - sx)**2 + (self.y_grid - sy)**2)
            ripple = np.sin(d * 0.05 - t * 4 + phase) * np.exp(-d * 0.003)
            val += ripple
        val = (val / 4 + 1) / 2.0
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: Morphing Blobs ──
    def morphing_blobs(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        # Metaball-like field function
        field = np.zeros((self.h, self.w), dtype=np.float32)
        blob_positions = [
            (0.3 + 0.15 * math.sin(t * 0.7), 0.4 + 0.2 * math.cos(t * 0.5), 0.12),
            (0.7 + 0.1 * math.cos(t * 0.6), 0.3 + 0.15 * math.sin(t * 0.8), 0.10),
            (0.5 + 0.2 * math.sin(t * 0.4), 0.7 + 0.1 * math.cos(t * 0.9), 0.14),
            (0.4 + 0.15 * math.cos(t * 0.5 + 1), 0.5 + 0.15 * math.sin(t * 0.7 + 2), 0.11),
            (0.6 + 0.1 * math.sin(t * 0.9), 0.6 + 0.1 * math.cos(t * 0.3), 0.09),
        ]
        for bx, by, radius in blob_positions:
            d = np.sqrt((nx - bx)**2 + (ny - by)**2)
            field += radius / (d + 0.01)
        # Threshold and smooth
        val = np.clip((field - 2.0) * 0.5, 0, 1)
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        bg = np.array(self.colors[0], dtype=np.float32) * 0.1
        frame = np.where(val[..., np.newaxis] > 0.01,
                         (c1 * (1 - frac) + c2 * frac),
                         bg[np.newaxis, np.newaxis, :])
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (15, 15), 0)
        return frame

    # ── PATTERN: Holographic ──
    def holographic(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        # Iridescent color shifting based on angle and position
        angle = np.arctan2(ny - 0.5, nx - 0.5)
        dist = np.sqrt((nx - 0.5)**2 + (ny - 0.5)**2)
        # Rainbow hue shift
        hue = (angle / (2 * math.pi) + dist * 2 + t * 0.3) % 1.0
        # Convert HSV-like to color index with smooth shifting
        val = (np.sin(hue * 2 * math.pi + t) * 0.3 +
               np.sin(dist * 15 - t * 3) * 0.2 +
               np.cos(nx * 10 + t * 2) * 0.15 + 0.5)
        val = np.clip(val, 0, 1)
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        # Add shimmer
        shimmer = (np.sin(nx * 40 + t * 5) * np.sin(ny * 40 - t * 3) * 30)
        frame = np.clip(frame + shimmer[..., np.newaxis], 0, 255).astype(np.uint8)
        return frame

    # ── PATTERN: Topographic ──
    def topographic(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 5
        ny = self.y_grid / self.h * 5
        # Elevation map
        elev = (np.sin(nx + t * 0.3) * np.cos(ny * 0.8 + t * 0.2) +
                np.sin(nx * 2 + ny + t * 0.5) * 0.5 +
                np.cos(np.sqrt((nx - 2.5)**2 + (ny - 2.5)**2) + t * 0.4) * 0.3)
        elev = (elev + 2) / 4.0
        num_contours = 15
        contour_val = (elev * num_contours) % 1.0
        edge = np.abs(contour_val - 0.5) < 0.05
        scaled = elev * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac) * 0.6
        frame[edge] = np.clip(frame[edge] * 2.5 + 40, 0, 255)
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: Hexagon Grid ──
    def hexagon_grid(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 8
        ny = self.y_grid / self.h * 8
        # Offset every other row for honeycomb
        offset = np.where((ny.astype(int) % 2) == 0, 0.5, 0.0)
        hx = (nx + offset) % 1.0
        hy = ny % 1.0
        # Distance to hex center
        dx = np.abs(hx - 0.5)
        dy = np.abs(hy - 0.5)
        hex_dist = np.maximum(dx * 1.5 + dy * 0.866, dy)
        # Animated edge glow
        edge = np.clip(1 - (hex_dist - 0.35) * 15, 0, 1)
        pulse = (np.sin(nx * 2 + ny * 2 + t * 2) * 0.5 + 0.5) * edge
        # Color mapping
        val = pulse * 0.7 + (1 - edge) * 0.3
        val = np.clip(val + np.sin(t + nx) * 0.1, 0, 1)
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: Matrix Rain ──
    def matrix_rain(self, t: float) -> np.ndarray:
        frame = np.zeros((self.h, self.w, 3), dtype=np.float32)
        # Dark background with subtle color
        frame[:] = np.array(self.colors[0], dtype=np.float32) * 0.03
        np.random.seed(99)
        n_columns = self.w // 12
        for i in range(n_columns):
            x = int((i + 0.5) * 12)
            speed = 40 + np.random.random() * 80
            col_offset = np.random.random() * 500
            # Rain drop position
            head_y = (t * speed + col_offset) % (self.h + 200) - 100
            trail_len = 80 + int(np.random.random() * 120)
            color = np.array(self.colors[i % 4], dtype=np.float32)
            for j in range(trail_len):
                py = int(head_y - j * 4)
                if 0 <= py < self.h and 0 <= x < self.w:
                    fade = 1.0 - (j / trail_len)
                    bright = fade * fade * (0.3 + 0.7 * (j == 0))
                    for dx_off in range(min(8, self.w - x)):
                        frame[py, x + dx_off] = np.clip(
                            frame[py, x + dx_off] + color * bright, 0, 255)
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: Voronoi Cells ──
    def voronoi_cells(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        # Moving seed points
        np.random.seed(55)
        n_seeds = 20
        seed_x = np.random.random(n_seeds)
        seed_y = np.random.random(n_seeds)
        # Animate seeds
        sx = seed_x + 0.05 * np.sin(t * 0.5 + np.arange(n_seeds) * 0.7)
        sy = seed_y + 0.05 * np.cos(t * 0.4 + np.arange(n_seeds) * 0.9)
        # Find closest and second-closest seed for each pixel
        min_dist = np.full((self.h, self.w), 999.0, dtype=np.float32)
        min_idx = np.zeros((self.h, self.w), dtype=np.int32)
        second_dist = np.full((self.h, self.w), 999.0, dtype=np.float32)
        for i in range(n_seeds):
            d = np.sqrt((nx - sx[i])**2 + (ny - sy[i])**2)
            mask = d < min_dist
            second_dist = np.where(mask, min_dist, np.where(d < second_dist, d, second_dist))
            min_idx = np.where(mask, i, min_idx)
            min_dist = np.where(mask, d, min_dist)
        # Edge detection (difference between closest and second-closest)
        edge = np.clip((second_dist - min_dist) * 15, 0, 1)
        # Color by cell index
        cell_val = (min_idx / n_seeds + t * 0.1) % 1.0
        scaled = cell_val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac) * edge[..., np.newaxis] * 0.8
        # Bright edges
        border = np.clip(1 - edge, 0, 1) * 0.3
        frame += border[..., np.newaxis] * 255
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: Fiber Optic ──
    def fiber_optic(self, t: float) -> np.ndarray:
        frame = np.zeros((self.h, self.w, 3), dtype=np.float32)
        frame[:] = np.array(self.colors[0], dtype=np.float32) * 0.05
        n_fibers = 30
        np.random.seed(33)
        for i in range(n_fibers):
            # Each fiber is a curved line
            phase = np.random.random() * 10
            amp = 0.1 + np.random.random() * 0.3
            freq = 1.5 + np.random.random() * 2
            base_x = np.random.random()
            color = np.array(self.colors[i % 4], dtype=np.float32)
            bright = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(t * 2 + phase))
            # Sample points along the fiber
            for y_frac in np.linspace(0, 1, 80):
                x_frac = base_x + amp * math.sin(y_frac * freq * math.pi + t * 1.5 + phase)
                px = int(x_frac * self.w) % self.w
                py = int(y_frac * self.h)
                if 0 <= py < self.h and 0 <= px < self.w:
                    r = max(1, int(3 * bright))
                    cv2.circle(frame, (px, py), r, (float(color[0]*bright), float(color[1]*bright), float(color[2]*bright)), -1, cv2.LINE_AA)
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Color Explosion ──
    def color_explosion(self, t: float) -> np.ndarray:
        dx = (self.x_grid - self.cx) / max(self.w, self.h)
        dy = (self.y_grid - self.cy) / max(self.w, self.h)
        dist = np.sqrt(dx**2 + dy**2)
        angle = np.arctan2(dy, dx)
        # Radial burst with multiple color bands
        burst = np.sin(dist * 25 - t * 4) * 0.3
        rays = np.sin(angle * 8 + t * 1.5) * 0.2
        swirl = np.sin(angle * 3 + dist * 10 - t * 2) * 0.2
        val = (burst + rays + swirl + 0.5)
        val = np.clip(val, 0, 1)
        # Full saturation color mapping — 4 colors cycled densely
        scaled = (val * 8 + t * 0.3) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        # Brighten everything — no dark areas
        frame = frame * 0.85 + 30
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: Tie Dye ──
    def tie_dye(self, t: float) -> np.ndarray:
        dx = (self.x_grid - self.cx) / max(self.w, self.h)
        dy = (self.y_grid - self.cy) / max(self.w, self.h)
        dist = np.sqrt(dx**2 + dy**2)
        angle = np.arctan2(dy, dx)
        # Gentle spiral — fewer turns for smoother look
        spiral = angle + dist * 8 - t * 0.8
        # Multiple soft sine layers blended together
        val1 = np.sin(spiral * 0.8) * 0.5 + 0.5
        val2 = np.sin(spiral * 0.4 + math.pi * 0.7) * 0.5 + 0.5
        val3 = np.cos(spiral * 0.6 + t * 0.3) * 0.5 + 0.5
        val4 = np.sin(dist * 5 + t * 0.5) * 0.3 + 0.5
        val = (val1 * 0.35 + val2 * 0.3 + val3 * 0.2 + val4 * 0.15)
        # Smooth color mapping with gentle cycling
        scaled = (val * 3 + t * 0.15) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        # Smooth the fraction for softer transitions
        frac = frac * frac * (3 - 2 * frac)  # smoothstep
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        frame = frame * 0.9 + 20
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (21, 21), 0)
        return frame

    # ── PATTERN: Oil Slick ──
    def oil_slick(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 6
        ny = self.y_grid / self.h * 6
        # Thin-film interference simulation
        film1 = np.sin(nx * 2 + ny * 1.5 + t * 0.5) * 0.5 + 0.5
        film2 = np.sin(nx * 3 - ny * 2 + t * 0.7) * 0.5 + 0.5
        film3 = np.cos((nx + ny) * 2.5 + t * 0.3) * 0.5 + 0.5
        # Each channel gets different film interference
        r = (film1 * 0.5 + film2 * 0.3 + 0.2)
        g = (film2 * 0.5 + film3 * 0.3 + 0.2)
        b = (film3 * 0.5 + film1 * 0.3 + 0.2)
        # Map through palette colors
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = (c_arr[0] * r[..., np.newaxis] +
                 c_arr[1] * g[..., np.newaxis] +
                 c_arr[2] * b[..., np.newaxis] +
                 c_arr[3][np.newaxis, np.newaxis, :] * 0.15)
        # Iridescent shimmer
        shimmer = np.sin(nx * 15 + ny * 10 + t * 3) * 20
        frame += shimmer[..., np.newaxis]
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Prismatic Waves ──
    def prismatic_waves(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        # Multiple flowing wave layers
        w1 = np.sin(nx * 6 + t * 1.2 + ny * 2) * 0.5 + 0.5
        w2 = np.sin(ny * 8 - t * 0.8 + nx * 3) * 0.5 + 0.5
        w3 = np.cos((nx + ny) * 5 + t * 1.5) * 0.5 + 0.5
        w4 = np.sin(nx * 4 - ny * 3 + t * 0.6) * 0.5 + 0.5
        # Blend into full-color spectrum
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = (c_arr[0] * w1[..., np.newaxis] +
                 c_arr[1] * w2[..., np.newaxis] +
                 c_arr[2] * w3[..., np.newaxis] +
                 c_arr[3] * w4[..., np.newaxis])
        # Normalize to prevent darkness
        frame = frame * 0.55 + 30
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Gradient Mesh ──
    def gradient_mesh(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        # Multiple overlapping radial gradients with animated centers
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = np.zeros((self.h, self.w, 3), dtype=np.float32)
        centers = [
            (0.25 + 0.15 * math.sin(t * 0.4), 0.25 + 0.15 * math.cos(t * 0.3)),
            (0.75 + 0.15 * math.cos(t * 0.5), 0.25 + 0.15 * math.sin(t * 0.35)),
            (0.25 + 0.15 * math.sin(t * 0.45), 0.75 + 0.15 * math.cos(t * 0.5)),
            (0.75 + 0.15 * math.cos(t * 0.3), 0.75 + 0.15 * math.sin(t * 0.4)),
        ]
        for i, (cx, cy) in enumerate(centers):
            d = np.sqrt((nx - cx)**2 + (ny - cy)**2)
            weight = np.exp(-d * 3)[..., np.newaxis]
            frame += c_arr[i] * weight
        # Normalize — ensures always full color
        max_val = np.maximum(frame.max(axis=2, keepdims=True), 1)
        frame = frame / max_val * 230 + 20
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (15, 15), 0)
        return frame

    # ── PATTERN: Chromatic Pulse ──
    def chromatic_pulse(self, t: float) -> np.ndarray:
        dx = (self.x_grid - self.cx) / max(self.w, self.h)
        dy = (self.y_grid - self.cy) / max(self.w, self.h)
        dist = np.sqrt(dx**2 + dy**2)
        # Concentric pulsating rings
        rings = np.sin(dist * 30 - t * 3) * 0.5 + 0.5
        # Slow color rotation
        color_phase = (dist * 8 + t * 0.5) % 4
        scaled = np.clip(color_phase, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        # Brighten ring peaks, but keep base colorful
        frame = frame * (0.6 + rings[..., np.newaxis] * 0.4) + 15
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: Watercolor Blend ──
    def watercolor_blend(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 3
        ny = self.y_grid / self.h * 3
        # Multiple soft noise layers simulating paint spread
        v1 = np.sin(nx * 1.2 + t * 0.2) * np.cos(ny * 1.5 - t * 0.15) * 0.4
        v2 = np.cos(nx * 2 + ny + t * 0.3) * 0.3
        v3 = np.sin((nx + ny) * 0.8 + t * 0.25) * 0.3
        val = (v1 + v2 + v3 + 1) / 2.0
        val = np.clip(val, 0, 1)
        # Soft edges (watercolor look)
        val = val ** 0.7  # Compress tones
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        # Add white paper base bleed through
        paper = (1 - val) * 0.15
        frame = frame * (1 - paper[..., np.newaxis]) + 240 * paper[..., np.newaxis]
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (15, 15), 0)
        return frame

    # ── PATTERN: Ocean Waves ──
    def ocean_waves(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        # Multiple wave layers at different frequencies
        w1 = np.sin(nx * 8 + t * 1.5) * np.cos(ny * 3 + t * 0.5) * 0.3
        w2 = np.sin(nx * 15 + ny * 5 + t * 2.5) * 0.15
        w3 = np.cos(nx * 4 - t * 1.0 + ny * 2) * 0.2
        wave = w1 + w2 + w3
        # Depth gradient (darker at bottom)
        depth = ny * 0.6 + 0.2
        val = np.clip((wave + 0.5) * depth + 0.2, 0, 1)
        # Foam at wave peaks
        foam = np.clip((wave - 0.25) * 5, 0, 1)
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        # Add white foam
        frame += foam[..., np.newaxis] * 80
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Rolling Clouds ──
    def rolling_clouds(self, t: float) -> np.ndarray:
        nx = (self.x_grid / self.w + t * 0.03) * 4  # Drift right
        ny = self.y_grid / self.h * 4
        # Multi-octave cloud noise
        v1 = np.sin(nx + t * 0.2) * np.cos(ny * 0.8) * 0.5
        v2 = np.sin(nx * 2 + ny * 1.5 + t * 0.3) * 0.25
        v3 = np.cos(nx * 4 + ny * 3 - t * 0.4) * 0.125
        v4 = np.sin(nx * 8 + ny * 6 + t * 0.5) * 0.0625
        cloud = (v1 + v2 + v3 + v4 + 0.5)
        cloud = np.clip(cloud, 0, 1)
        # Sky base color
        sky_grad = 1 - (self.y_grid / self.h) * 0.3
        bg = np.array(self.colors[0], dtype=np.float32) * 0.4
        frame = bg[np.newaxis, np.newaxis, :] * sky_grad[..., np.newaxis]
        # Cloud color (mix of palette)
        cloud_color = (np.array(self.colors[1], dtype=np.float32) * 0.5 +
                       np.array(self.colors[2], dtype=np.float32) * 0.5)
        # Blend cloud over sky
        cloud_alpha = np.clip(cloud * 1.5 - 0.2, 0, 1)[..., np.newaxis]
        frame = frame * (1 - cloud_alpha) + cloud_color * cloud_alpha
        # Highlight bright edges
        bright = np.clip(cloud - 0.6, 0, 1) * 100
        frame += bright[..., np.newaxis]
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (11, 11), 0)
        return frame

    # ── PATTERN: Geometric Bloom ──
    def geometric_bloom(self, t: float) -> np.ndarray:
        dx = self.x_grid - self.cx
        dy = self.y_grid - self.cy
        dist = np.sqrt(dx**2 + dy**2)
        angle = np.arctan2(dy, dx)
        # Sacred geometry: multiple petal layers
        petals = 6
        bloom1 = np.cos(angle * petals + t) * 0.5 + 0.5
        bloom2 = np.cos(angle * (petals * 2) - t * 1.5 + dist * 0.01) * 0.5 + 0.5
        # Radial rings
        rings = (np.sin(dist * 0.03 - t * 2) * 0.5 + 0.5) * 0.5
        val = (bloom1 * 0.4 + bloom2 * 0.3 + rings * 0.3)
        # Fade at edges
        fade = np.clip(1 - dist / max(self.w, self.h) * 1.2, 0, 1)
        val *= fade
        scaled = val * 3.99
        idx = np.clip(scaled.astype(np.int32), 0, 3)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: 3D Sphere ──
    def sphere_3d(self, t: float) -> np.ndarray:
        nx = (self.x_grid - self.cx) / (min(self.w, self.h) * 0.4)
        ny = (self.y_grid - self.cy) / (min(self.w, self.h) * 0.4)
        r2 = nx**2 + ny**2
        # Sphere mask
        sphere_mask = r2 <= 1.0
        # Normal z-component for lighting
        nz = np.sqrt(np.clip(1.0 - r2, 0, 1))
        # Rotate light source
        lx = math.cos(t * 0.5) * 0.6
        ly = math.sin(t * 0.5) * 0.4
        lz = 0.7
        # Phong diffuse
        dot = np.clip(nx * lx + ny * ly + nz * lz, 0, 1)
        # Specular highlight
        spec = np.clip(dot, 0, 1) ** 32 * 0.8
        # UV mapping on sphere for color
        u = (np.arctan2(ny, nx) + t * 0.3) / (2 * math.pi) + 0.5
        v = np.arcsin(np.clip(ny, -1, 1)) / math.pi + 0.5
        color_val = (u * 4 + v * 2) % 4
        scaled = np.clip(color_val, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        surface = (c1 * (1 - frac) + c2 * frac)
        # Apply lighting
        lit = surface * (dot[..., np.newaxis] * 0.7 + 0.3) + spec[..., np.newaxis] * 200
        # Background gradient
        bg_val = (self.y_grid / self.h)
        bg = c_arr[0] * (1 - bg_val)[..., np.newaxis] * 0.15 + c_arr[3] * bg_val[..., np.newaxis] * 0.15
        frame = np.where(sphere_mask[..., np.newaxis], lit, bg)
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: 3D Terrain ──
    def terrain_3d(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 6
        ny = self.y_grid / self.h
        # Perspective projection — compress Y based on depth
        depth = np.clip(ny, 0.01, 1.0)
        world_x = (nx - 3) / depth
        world_z = 1.0 / depth + t * 2
        # Height map
        elev = (np.sin(world_x * 0.8 + world_z * 0.3) * 0.4 +
                np.sin(world_x * 1.5 + world_z * 0.8) * 0.2 +
                np.cos(world_x * 0.3 + world_z * 0.5) * 0.3)
        # Color by elevation
        val = (elev + 1) / 2.0
        scaled = np.clip(val * 3.99, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        # Depth fog — fade distant areas
        fog = np.clip(1 - depth * 0.6, 0.2, 1.0)[..., np.newaxis]
        frame = frame * fog + c_arr[0] * 0.2 * (1 - fog)
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: 3D Cubes ──
    def cubes_3d(self, t: float) -> np.ndarray:
        c_arr = np.array(self.colors, dtype=np.float32)
        # Dark gradient background
        ny = self.y_grid / self.h
        frame = (c_arr[0] * 0.1 * (1 - ny)[..., np.newaxis] +
                 c_arr[1] * 0.1 * ny[..., np.newaxis])
        frame = np.broadcast_to(frame, (self.h, self.w, 3)).copy().astype(np.float32)
        np.random.seed(42)
        n_cubes = 12
        for i in range(n_cubes):
            # Cube center position (animated)
            cx = int(self.w * (0.1 + np.random.random() * 0.8))
            cy_base = np.random.random()
            cy = int(self.h * ((cy_base + t * 0.02 * (i % 3 + 1)) % 1.0))
            size = int(min(self.w, self.h) * (0.04 + np.random.random() * 0.06))
            # 3D rotation angle
            a = t * (0.5 + i * 0.2)
            cos_a, sin_a = math.cos(a), math.sin(a)
            # Simple isometric cube — 3 visible faces
            depth_val = 0.3 + 0.7 * (i / n_cubes)
            color = c_arr[i % 4] * depth_val
            # Top face (brighter)
            pts_top = np.array([
                [cx - size, cy - size], [cx + size, cy - size],
                [cx + size + size//3, cy - size - size//2],
                [cx - size + size//3, cy - size - size//2]
            ], dtype=np.int32)
            cv2.fillPoly(frame, [pts_top], (float(color[0]*1.3), float(color[1]*1.3), float(color[2]*1.3)))
            # Front face
            pts_front = np.array([
                [cx - size, cy - size], [cx + size, cy - size],
                [cx + size, cy + size], [cx - size, cy + size]
            ], dtype=np.int32)
            cv2.fillPoly(frame, [pts_front], (float(color[0]), float(color[1]), float(color[2])))
            # Side face (darker)
            pts_side = np.array([
                [cx + size, cy - size], [cx + size + size//3, cy - size - size//2],
                [cx + size + size//3, cy + size - size//2], [cx + size, cy + size]
            ], dtype=np.int32)
            cv2.fillPoly(frame, [pts_side], (float(color[0]*0.6), float(color[1]*0.6), float(color[2]*0.6)))
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: 3D Tunnel ──
    def tunnel_3d(self, t: float) -> np.ndarray:
        dx = (self.x_grid - self.cx) / max(self.w, self.h) * 2
        dy = (self.y_grid - self.cy) / max(self.w, self.h) * 2
        dist = np.sqrt(dx**2 + dy**2)
        angle = np.arctan2(dy, dx)
        # Tunnel mapping — inverse distance gives depth illusion
        safe_dist = np.clip(dist, 0.01, 10)
        tunnel_z = 1.0 / safe_dist + t * 2
        tunnel_u = angle / math.pi
        # Animated tunnel texture
        tex = np.sin(tunnel_z * 3) * np.cos(tunnel_u * 4) * 0.5 + 0.5
        tex2 = np.sin(tunnel_z * 6 + tunnel_u * 8) * 0.3 + 0.5
        val = (tex * 0.6 + tex2 * 0.4)
        # Color mapping
        scaled = np.clip((val * 4 + t * 0.2) % 4, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        frame = (c1 * (1 - frac) + c2 * frac)
        # Depth darkening at edges (far = bright center)
        depth_shade = np.clip(1 - dist * 0.3, 0.2, 1.0)
        frame = frame * depth_shade[..., np.newaxis]
        # Bright center glow
        center_glow = np.exp(-dist * 5) * 60
        frame += center_glow[..., np.newaxis]
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: 3D Crystal ──
    def crystal_3d(self, t: float) -> np.ndarray:
        dx = (self.x_grid - self.cx) / (min(self.w, self.h) * 0.35)
        dy = (self.y_grid - self.cy) / (min(self.w, self.h) * 0.35)
        angle = np.arctan2(dy, dx)
        dist = np.sqrt(dx**2 + dy**2)
        # Crystal facets — hexagonal sections
        n_facets = 6
        facet_angle = ((angle + t * 0.2) % (2 * math.pi / n_facets))
        facet_id = ((angle + t * 0.2) / (2 * math.pi / n_facets)).astype(np.int32) % n_facets
        # Crystal shape — tapered hexagon
        crystal_radius = 0.7 + 0.15 * np.cos(facet_angle * n_facets) - dist * 0.1
        inside = dist < crystal_radius
        # Each facet has different brightness (simulates 3D normals)
        facet_bright = 0.4 + 0.6 * np.abs(np.sin(facet_id * 1.5 + t * 0.5))
        # Refraction rainbow effect inside crystal
        refract = np.sin(angle * 8 + dist * 10 - t * 3) * 0.5 + 0.5
        val = (facet_id / n_facets + refract * 0.3 + t * 0.1) % 1.0
        scaled = np.clip(val * 3.99, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        crystal_color = (c1 * (1 - frac) + c2 * frac) * facet_bright[..., np.newaxis]
        # Specular highlights on facet edges
        edge_highlight = np.exp(-np.abs(facet_angle - math.pi / n_facets) * 20) * 150
        crystal_color += edge_highlight[..., np.newaxis]
        # Background
        bg = c_arr[0] * 0.08
        frame = np.where(inside[..., np.newaxis], crystal_color, bg)
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: 3D Metaballs ──
    def metaballs_3d(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        c_arr = np.array(self.colors, dtype=np.float32)
        # Metaball centers (animated)
        balls = [
            (0.35 + 0.15 * math.sin(t * 0.6), 0.4 + 0.15 * math.cos(t * 0.5), 0.12),
            (0.65 + 0.12 * math.cos(t * 0.7), 0.5 + 0.12 * math.sin(t * 0.4), 0.1),
            (0.5 + 0.18 * math.sin(t * 0.4 + 2), 0.6 + 0.1 * math.cos(t * 0.6), 0.09),
            (0.4 + 0.1 * math.cos(t * 0.8 + 1), 0.35 + 0.15 * math.sin(t * 0.5 + 3), 0.11),
            (0.6 + 0.15 * math.sin(t * 0.5 + 4), 0.45 + 0.1 * math.cos(t * 0.7 + 2), 0.08),
        ]
        # Compute metaball field
        field = np.zeros((self.h, self.w), dtype=np.float32)
        color_field = np.zeros((self.h, self.w, 3), dtype=np.float32)
        for i, (bx, by, br) in enumerate(balls):
            d = np.sqrt((nx - bx)**2 + (ny - by)**2)
            influence = br**2 / (d**2 + 0.001)
            field += influence
            color_field += influence[..., np.newaxis] * c_arr[i % 4]
        # Normalize color by field
        safe_field = np.maximum(field, 0.001)[..., np.newaxis]
        color_field = color_field / safe_field
        # 3D shading — fake normal from field gradient
        shade = np.clip(field * 0.5, 0, 1)
        # Specular-like highlights where field is strongest
        highlight = np.clip((field - 1.5) * 3, 0, 1)
        frame = color_field * shade[..., np.newaxis] * 0.8 + highlight[..., np.newaxis] * 120
        # Background
        bg_mask = (field < 0.5)[..., np.newaxis]
        bg = c_arr[0] * 0.08
        frame = np.where(bg_mask, bg, frame)
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (9, 9), 0)
        return frame

    # ── PATTERN: Color Smoke ──
    def color_smoke(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 4
        ny = self.y_grid / self.h * 4
        v1 = np.sin(nx * 1.5 + t * 0.3) * np.cos(ny * 2 - t * 0.2) * 0.4
        v2 = np.cos(nx * 2.5 + ny + t * 0.4) * 0.3
        v3 = np.sin((nx - ny) * 1.8 + t * 0.5) * 0.3
        val = (v1 + v2 + v3 + 1) / 2.0
        val = np.clip(val ** 0.6, 0, 1)
        scaled = (val * 4 + t * 0.1) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = frac = (scaled - idx)[..., np.newaxis]
        frac = frac * frac * (3 - 2 * frac)
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.85 + 25
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (21, 21), 0)
        return frame

    # ── PATTERN: Rainbow Flow ──
    def rainbow_flow(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        val = (nx * 2 + ny + t * 0.15) % 1.0
        scaled = val * 3.99
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        frac = frac * frac * (3 - 2 * frac)
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        wave = np.sin(nx * 8 + ny * 4 + t * 2) * 20
        frame += wave[..., np.newaxis]
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (11, 11), 0)
        return frame

    # ── PATTERN: Paint Pour ──
    def paint_pour(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 5
        ny = self.y_grid / self.h * 5
        v1 = np.sin(nx * 1.3 + ny * 0.7 + t * 0.2)
        v2 = np.cos(nx * 0.9 - ny * 1.1 + t * 0.3)
        v3 = np.sin((nx + ny) * 0.6 + t * 0.15)
        val = (v1 + v2 + v3 + 3) / 6.0
        scaled = (val * 6 + t * 0.1) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        frac = frac * frac * (3 - 2 * frac)
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.9 + 15
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (15, 15), 0)
        return frame

    # ── PATTERN: Silk Fabric ──
    def silk_fabric(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        fold1 = np.sin(nx * 12 + t * 0.8) * np.cos(ny * 3 + t * 0.3) * 0.5 + 0.5
        fold2 = np.sin(ny * 8 - t * 0.5 + nx * 2) * 0.3 + 0.5
        val = fold1 * 0.6 + fold2 * 0.4
        highlight = np.clip((fold1 - 0.7) * 5, 0, 1) * 40
        scaled = (val * 3 + t * 0.1) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame += highlight[..., np.newaxis]
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (9, 9), 0)
        return frame

    # ── PATTERN: Neon Waves ──
    def neon_waves(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        w1 = np.sin(nx * 10 + t * 2) * 0.5 + 0.5
        w2 = np.sin(ny * 8 - t * 1.5 + nx * 3) * 0.5 + 0.5
        w3 = np.cos((nx - ny) * 6 + t * 1.8) * 0.5 + 0.5
        val = (w1 + w2 + w3) / 3.0
        scaled = (val * 5 + t * 0.2) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        glow = np.clip(val - 0.6, 0, 1) * 60
        frame += glow[..., np.newaxis]
        frame = frame * 0.9 + 20
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Lava Flow ──
    def lava_flow(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 4
        ny = (self.y_grid / self.h + t * 0.05) * 4
        v1 = np.sin(nx * 2 + ny * 1.5 + t * 0.3) * 0.4
        v2 = np.cos(nx * 1.5 - ny * 0.8 + t * 0.4) * 0.3
        v3 = np.sin((nx + ny) * 0.7 + t * 0.2) * 0.3
        val = (v1 + v2 + v3 + 1) / 2.0
        val = np.clip(val ** 0.8, 0, 1)
        scaled = (val * 3 + t * 0.05) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        bright = np.clip(val - 0.5, 0, 1) * 50
        frame += bright[..., np.newaxis]
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (13, 13), 0)
        return frame

    # ── PATTERN: Candy Swirl ──
    def candy_swirl(self, t: float) -> np.ndarray:
        dx = (self.x_grid - self.cx) / max(self.w, self.h)
        dy = (self.y_grid - self.cy) / max(self.w, self.h)
        dist = np.sqrt(dx**2 + dy**2)
        angle = np.arctan2(dy, dx)
        swirl = angle * 3 + dist * 12 - t * 1.2
        val = np.sin(swirl) * 0.5 + 0.5
        val2 = np.cos(swirl * 0.5 + math.pi / 4) * 0.5 + 0.5
        combined = val * 0.6 + val2 * 0.4
        scaled = (combined * 4 + t * 0.15) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        frac = frac * frac * (3 - 2 * frac)
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.9 + 20
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (11, 11), 0)
        return frame

    # ── PATTERN: Aurora Curtain ──
    def aurora_curtain(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        curtain = np.sin(nx * 6 + t * 0.8) * 0.15
        val = ny + curtain
        v1 = np.sin(val * 8 + t * 0.5) * 0.5 + 0.5
        v2 = np.cos(nx * 4 + val * 3 - t * 0.3) * 0.3 + 0.5
        combined = v1 * 0.7 + v2 * 0.3
        scaled = (combined * 4 + t * 0.1) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.85 + 25
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (11, 11), 0)
        return frame

    # ── PATTERN: Color Vortex ──
    def color_vortex(self, t: float) -> np.ndarray:
        dx = (self.x_grid - self.cx) / max(self.w, self.h)
        dy = (self.y_grid - self.cy) / max(self.w, self.h)
        dist = np.sqrt(dx**2 + dy**2)
        angle = np.arctan2(dy, dx)
        vortex = angle + dist * 6 + t * 1.5
        val = np.sin(vortex * 2) * 0.3 + np.cos(vortex + dist * 10) * 0.2 + 0.5
        val = np.clip(val, 0, 1)
        scaled = (val * 5 + t * 0.2) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.85 + 25
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (9, 9), 0)
        return frame

    # ── PATTERN: Marble Ink ──
    def marble_ink(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 4
        ny = self.y_grid / self.h * 4
        v1 = np.sin(nx + ny * 0.5 + t * 0.2) * np.cos(ny - nx * 0.3 + t * 0.15)
        v2 = np.sin(nx * 2.5 + t * 0.3) * 0.3
        v3 = np.cos(ny * 2 + nx + t * 0.25) * 0.3
        val = (v1 + v2 + v3 + 1.5) / 3.0
        val = np.clip(val, 0, 1)
        scaled = (val * 5 + t * 0.08) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        frac = frac * frac * (3 - 2 * frac)
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.9 + 15
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (13, 13), 0)
        return frame

    # ── PATTERN: Electric Gradient ──
    def electric_gradient(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        val = (nx + ny * 0.5 + t * 0.1) % 1.0
        sharp = np.abs(np.sin(val * math.pi * 6 + t * 2))
        scaled = (sharp * 4 + t * 0.15) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        pulse = np.sin(nx * 20 + t * 5) * 15
        frame += pulse[..., np.newaxis]
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: Color Cells ──
    def color_cells(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        np.random.seed(77)
        n_seeds = 25
        sx = np.random.random(n_seeds) + 0.03 * np.sin(t * 0.5 + np.arange(n_seeds) * 0.5)
        sy = np.random.random(n_seeds) + 0.03 * np.cos(t * 0.4 + np.arange(n_seeds) * 0.7)
        min_dist = np.full((self.h, self.w), 999.0, dtype=np.float32)
        min_idx = np.zeros((self.h, self.w), dtype=np.int32)
        for i in range(n_seeds):
            d = np.sqrt((nx - sx[i])**2 + (ny - sy[i])**2)
            mask = d < min_dist
            min_idx = np.where(mask, i, min_idx)
            min_dist = np.where(mask, d, min_dist)
        val = (min_idx / n_seeds + t * 0.05) % 1.0
        scaled = np.clip(val * 3.99, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.9 + 15
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: Neon Grid ──
    def neon_grid(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 10
        ny = self.y_grid / self.h * 10
        gx = np.abs(np.sin(nx * math.pi))
        gy = np.abs(np.sin(ny * math.pi))
        grid = np.minimum(gx, gy)
        edge = np.clip(1 - grid * 8, 0, 1)
        cell_val = ((nx.astype(int) + ny.astype(int) + int(t * 2)) % 4) / 4.0
        scaled = np.clip(cell_val * 3.99, 0, 3.99).astype(np.float32)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        c1 = c_arr[idx % 4]
        c2 = c_arr[(idx + 1) % 4]
        fill = (c1 * (1 - frac) + c2 * frac) * 0.4
        glow_color = c_arr[int(t * 0.5) % 4]
        frame = fill + edge[..., np.newaxis] * glow_color * 0.8
        frame = frame + 15
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
        return frame

    # ── PATTERN: Paint Drip ──
    def paint_drip(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = np.zeros((self.h, self.w, 3), dtype=np.float32)
        frame += c_arr[0] * 0.3
        np.random.seed(88)
        n_drips = 20
        for i in range(n_drips):
            dx = np.random.random()
            speed = 0.02 + np.random.random() * 0.05
            width = 0.02 + np.random.random() * 0.04
            head_y = (t * speed + np.random.random()) % 1.2
            d = np.abs(nx - dx)
            drip_mask = (d < width) & (ny < head_y) & (ny > head_y - 0.4)
            fade = np.clip(1 - (head_y - ny) * 4, 0, 1)
            drip_val = np.where(drip_mask, fade * (1 - d / width), 0)
            frame += drip_val[..., np.newaxis] * c_arr[i % 4] * 0.8
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (9, 9), 0)
        return frame

    # ── PATTERN: Crystal Facets ──
    def crystal_facets(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 6
        ny = self.y_grid / self.h * 6
        fx = np.floor(nx + 0.5 * np.floor(ny)).astype(np.int32)
        fy = np.floor(ny).astype(np.int32)
        cell_id = (fx * 7 + fy * 13 + int(t * 0.5)) % 4
        bright = 0.6 + 0.4 * np.sin(fx * 2.0 + fy * 3.0 + t * 0.8)
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[cell_id] * bright[..., np.newaxis]
        edge_x = np.abs((nx + 0.5 * np.floor(ny)) % 1.0 - 0.5) < 0.05
        edge_y = np.abs(ny % 1.0 - 0.5) < 0.05
        frame[edge_x | edge_y] = np.clip(frame[edge_x | edge_y] + 60, 0, 255)
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: Thermal Map ──
    def thermal_map(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 5
        ny = self.y_grid / self.h * 5
        heat = (np.sin(nx * 1.5 + t * 0.3) * np.cos(ny * 2 + t * 0.2) * 0.4 +
                np.sin(nx * 3 + ny + t * 0.5) * 0.3 +
                np.cos(np.sqrt((nx - 2.5)**2 + (ny - 2.5)**2) * 2 + t * 0.4) * 0.3)
        heat = (heat + 1) / 2.0
        scaled = np.clip(heat * 3.99, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.9 + 15
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (9, 9), 0)
        return frame

    # ── PATTERN: Color Storm ──
    def color_storm(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 5
        ny = self.y_grid / self.h * 5
        v1 = np.sin(nx * 3 + t * 1.5) * np.cos(ny * 2 - t * 1.2) * 0.4
        v2 = np.cos(nx * 2 - ny * 3 + t * 1.8) * 0.3
        v3 = np.sin((nx + ny) * 2.5 + t * 2) * 0.2
        v4 = np.cos(nx * 5 + ny * 4 - t * 1.5) * 0.1
        val = (v1 + v2 + v3 + v4 + 1) / 2.0
        val = np.clip(val, 0, 1)
        scaled = (val * 6 + t * 0.3) % 4
        scaled = np.clip(scaled, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.85 + 25
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Pixel Mosaic ──
    def pixel_mosaic(self, t: float) -> np.ndarray:
        block = max(8, int(min(self.w, self.h) * 0.03))
        bx = (self.x_grid // block).astype(np.int32)
        by = (self.y_grid // block).astype(np.int32)
        cell_val = ((bx * 7 + by * 13 + int(t * 3)) % 4) / 4.0
        wave = np.sin(bx * 0.3 + by * 0.2 + t * 1.5) * 0.15
        val = np.clip(cell_val + wave, 0, 1)
        scaled = np.clip(val * 3.99, 0, 3.99)
        idx = scaled.astype(np.int32)
        frac = (scaled - idx)[..., np.newaxis]
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = c_arr[idx % 4] * (1 - frac) + c_arr[(idx + 1) % 4] * frac
        frame = frame * 0.9 + 15
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        return frame

    # ── PATTERN: Liquid Chrome ──
    def liquid_chrome(self, t: float) -> np.ndarray:
        nx = self.x_grid / self.w * 4
        ny = self.y_grid / self.h * 4
        v1 = np.sin(nx * 2 + ny + t * 0.5) * 0.5 + 0.5
        v2 = np.cos(nx + ny * 2 - t * 0.4) * 0.5 + 0.5
        v3 = np.sin((nx - ny) * 3 + t * 0.6) * 0.5 + 0.5
        chrome = (v1 + v2 + v3) / 3.0
        reflect = np.abs(np.sin(chrome * math.pi * 4 + t)) * 0.4 + 0.6
        c_arr = np.array(self.colors, dtype=np.float32)
        frame = (c_arr[0] * v1[..., np.newaxis] + c_arr[1] * v2[..., np.newaxis] +
                 c_arr[2] * v3[..., np.newaxis]) * 0.5
        frame = frame * reflect[..., np.newaxis]
        specular = np.clip((chrome - 0.7) * 8, 0, 1) * 100
        frame += specular[..., np.newaxis]
        frame = frame * 0.8 + 30
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Floating Particles ──
    def floating_particles(self, t: float) -> np.ndarray:
        """Elegant particles gently rising upward — #1 microstock seller."""
        c_arr = np.array(self.colors, dtype=np.float32)
        # Dark background from first color, dimmed
        bg = (c_arr[0] * 0.15).astype(np.uint8)
        frame = np.full((self.h, self.w, 3), bg, dtype=np.uint8)

        rng = np.random.RandomState(42)
        n_particles = 120
        base_x = rng.rand(n_particles)
        base_speed = rng.rand(n_particles) * 0.3 + 0.05
        base_size = (rng.rand(n_particles) * 3 + 1).astype(int)
        base_brightness = rng.rand(n_particles) * 0.6 + 0.4
        color_idx = rng.randint(0, 4, n_particles)

        for i in range(n_particles):
            x = int((base_x[i] + math.sin(t * 0.3 + i) * 0.02) * self.w) % self.w
            y = int((1.0 - ((t * base_speed[i] + i * 0.1) % 1.2)) * self.h)
            if y < -10 or y > self.h + 10:
                continue
            r = base_size[i]
            brightness = base_brightness[i] * (0.7 + 0.3 * math.sin(t * 2 + i))
            color = (c_arr[color_idx[i]] * brightness).astype(int).tolist()
            cv2.circle(frame, (x, y), r, color, -1, cv2.LINE_AA)
            # Glow
            if r >= 2:
                cv2.circle(frame, (x, y), r * 3, [c // 4 for c in color], -1, cv2.LINE_AA)

        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        return frame

    # ── PATTERN: Bokeh Lights ──
    def bokeh_lights(self, t: float) -> np.ndarray:
        """Beautiful out-of-focus light circles — top seller."""
        c_arr = np.array(self.colors, dtype=np.float32)
        bg = (c_arr[0] * 0.1).astype(np.uint8)
        frame = np.full((self.h, self.w, 3), bg, dtype=np.uint8)

        rng = np.random.RandomState(123)
        n_bokeh = 40
        base_x = rng.rand(n_bokeh)
        base_y = rng.rand(n_bokeh)
        base_r = (rng.rand(n_bokeh) * 40 + 15).astype(int)
        base_alpha = rng.rand(n_bokeh) * 0.3 + 0.1
        color_idx = rng.randint(0, 4, n_bokeh)
        drift_speed = rng.rand(n_bokeh) * 0.02 + 0.005

        for i in range(n_bokeh):
            x = int((base_x[i] + math.sin(t * drift_speed[i] * 10 + i) * 0.05) * self.w)
            y = int((base_y[i] + math.cos(t * drift_speed[i] * 8 + i * 2) * 0.03) * self.h)
            r = base_r[i]
            pulse = 0.7 + 0.3 * math.sin(t * 1.5 + i * 0.8)
            alpha = base_alpha[i] * pulse
            color = (c_arr[color_idx[i]] * alpha).astype(int).tolist()
            cv2.circle(frame, (x, y), r, color, 2, cv2.LINE_AA)
            # Inner glow
            cv2.circle(frame, (x, y), max(1, r // 2), [int(c * 1.5) for c in color], -1, cv2.LINE_AA)

        frame = cv2.GaussianBlur(frame, (7, 7), 0)
        return frame

    # ── PATTERN: Plexus Network ──
    def plexus_network(self, t: float) -> np.ndarray:
        """Connected nodes with dynamic linking lines — tech/corporate favorite."""
        c_arr = np.array(self.colors, dtype=np.float32)
        bg = (c_arr[0] * 0.08).astype(np.uint8)
        frame = np.full((self.h, self.w, 3), bg, dtype=np.uint8)

        rng = np.random.RandomState(77)
        n_nodes = 60
        base_x = rng.rand(n_nodes)
        base_y = rng.rand(n_nodes)
        speed_x = (rng.rand(n_nodes) - 0.5) * 0.02
        speed_y = (rng.rand(n_nodes) - 0.5) * 0.02

        # Calculate node positions
        nodes = []
        for i in range(n_nodes):
            x = ((base_x[i] + speed_x[i] * t + math.sin(t * 0.5 + i) * 0.02) % 1.0) * self.w
            y = ((base_y[i] + speed_y[i] * t + math.cos(t * 0.4 + i) * 0.02) % 1.0) * self.h
            nodes.append((int(x), int(y)))

        # Draw connections
        max_dist = min(self.w, self.h) * 0.2
        line_color = (c_arr[1] * 0.3).astype(int).tolist()
        for i in range(n_nodes):
            for j in range(i + 1, n_nodes):
                dx = nodes[i][0] - nodes[j][0]
                dy = nodes[i][1] - nodes[j][1]
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < max_dist:
                    alpha = 1.0 - dist / max_dist
                    c = [int(v * alpha) for v in line_color]
                    cv2.line(frame, nodes[i], nodes[j], c, 1, cv2.LINE_AA)

        # Draw nodes
        node_color = (c_arr[2] * 0.8).astype(int).tolist()
        glow_color = (c_arr[3] * 0.3).astype(int).tolist()
        for x, y in nodes:
            cv2.circle(frame, (x, y), 4, glow_color, -1, cv2.LINE_AA)
            cv2.circle(frame, (x, y), 2, node_color, -1, cv2.LINE_AA)

        return frame

    # ── PATTERN: Soft Gradient Shift ──
    def soft_gradient_shift(self, t: float) -> np.ndarray:
        """Smooth slow-moving color gradient transitions — presentation staple."""
        c_arr = np.array(self.colors, dtype=np.float32)
        nx = self.x_grid / self.w
        ny = self.y_grid / self.h

        # Very slow, smooth weight transitions
        w0 = (np.sin(nx * math.pi + t * 0.15) * 0.5 + 0.5) * (np.cos(ny * math.pi + t * 0.1) * 0.5 + 0.5)
        w1 = (np.cos(nx * math.pi - t * 0.12) * 0.5 + 0.5) * (np.sin(ny * math.pi - t * 0.08) * 0.5 + 0.5)
        w2 = (np.sin((nx + ny) * math.pi * 0.5 + t * 0.1) * 0.5 + 0.5)
        w3 = 1.0 - (w0 + w1 + w2) / 3.0

        total = w0 + w1 + w2 + w3 + 1e-6
        w0 /= total
        w1 /= total
        w2 /= total
        w3 /= total

        frame = (c_arr[0] * w0[..., np.newaxis] +
                 c_arr[1] * w1[..., np.newaxis] +
                 c_arr[2] * w2[..., np.newaxis] +
                 c_arr[3] * w3[..., np.newaxis])
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = cv2.GaussianBlur(frame, (15, 15), 0)
        return frame

    # ── PATTERN: Geometric Float ──
    def geometric_float(self, t: float) -> np.ndarray:
        """Floating geometric shapes drifting gently — modern/trendy."""
        c_arr = np.array(self.colors, dtype=np.float32)
        bg = (c_arr[0] * 0.12).astype(np.uint8)
        frame = np.full((self.h, self.w, 3), bg, dtype=np.uint8)

        rng = np.random.RandomState(55)
        n_shapes = 30
        shape_type = rng.randint(0, 3, n_shapes)  # 0=triangle, 1=rect, 2=circle
        base_x = rng.rand(n_shapes)
        base_y = rng.rand(n_shapes)
        base_size = (rng.rand(n_shapes) * 40 + 10).astype(int)
        base_alpha = rng.rand(n_shapes) * 0.3 + 0.1
        color_idx = rng.randint(0, 4, n_shapes)
        rotation = rng.rand(n_shapes) * math.pi * 2

        for i in range(n_shapes):
            x = int((base_x[i] + math.sin(t * 0.2 + i * 0.5) * 0.05) * self.w)
            y = int((base_y[i] + math.cos(t * 0.15 + i * 0.3) * 0.04) * self.h)
            s = base_size[i]
            alpha = base_alpha[i] * (0.6 + 0.4 * math.sin(t + i))
            color = (c_arr[color_idx[i]] * alpha).astype(int).tolist()
            angle = rotation[i] + t * 0.3

            if shape_type[i] == 0:  # Triangle
                pts = []
                for k in range(3):
                    a = angle + k * 2 * math.pi / 3
                    pts.append([int(x + s * math.cos(a)), int(y + s * math.sin(a))])
                cv2.polylines(frame, [np.array(pts)], True, color, 2, cv2.LINE_AA)
            elif shape_type[i] == 1:  # Rectangle
                half = s // 2
                cos_a, sin_a = math.cos(angle), math.sin(angle)
                pts = []
                for dx, dy in [(-half, -half), (half, -half), (half, half), (-half, half)]:
                    rx = int(x + dx * cos_a - dy * sin_a)
                    ry = int(y + dx * sin_a + dy * cos_a)
                    pts.append([rx, ry])
                cv2.polylines(frame, [np.array(pts)], True, color, 2, cv2.LINE_AA)
            else:  # Circle
                cv2.circle(frame, (x, y), s, color, 2, cv2.LINE_AA)

        return frame

    # ── PATTERN: Digital Data ──
    def digital_data(self, t: float) -> np.ndarray:
        """Subtle data stream with numbers — tech/AI/cybersecurity."""
        c_arr = np.array(self.colors, dtype=np.float32)
        bg = (c_arr[0] * 0.05).astype(np.uint8)
        frame = np.full((self.h, self.w, 3), bg, dtype=np.uint8)

        rng = np.random.RandomState(99)
        n_cols = 30
        col_x = (rng.rand(n_cols) * self.w).astype(int)
        col_speed = rng.rand(n_cols) * 0.5 + 0.1
        col_chars = rng.randint(8, 20, n_cols)
        color_idx = rng.randint(0, 4, n_cols)

        chars = "0123456789ABCDEF<>{}[]#@$"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.35
        char_h = 16

        for i in range(n_cols):
            x = col_x[i]
            offset = (t * col_speed[i] * 100) % (self.h + col_chars[i] * char_h)
            base_color = c_arr[color_idx[i]]

            for j in range(col_chars[i]):
                y = int(offset - j * char_h)
                if y < -char_h or y > self.h:
                    continue
                # Fade out trailing chars
                fade = max(0.05, 1.0 - j / col_chars[i])
                color = (base_color * fade * 0.7).astype(int).tolist()
                # Deterministic char selection that changes over time
                char_idx = (int(t * 3 + i * 7 + j * 13) % len(chars))
                ch = chars[char_idx]
                cv2.putText(frame, ch, (x, y), font, font_scale, color, 1, cv2.LINE_AA)

        return frame

    # ── RENDER DISPATCHER ──
    def render_frame(self, pattern: str, t: float) -> np.ndarray:
        """Render a single frame for the given pattern at time t."""
        renderers = {
            "gradient_flow": self._fast_gradient_flow,
            "particle_wave": self.particle_wave,
            "liquid_marble": self.liquid_marble,
            "aurora_borealis": self.aurora_borealis,
            "smoke_plume": self.smoke_plume,
            "fractal_tunnel": self.fractal_tunnel,
            "wave_interference": self.wave_interference,
            "diamond_grid": self.diamond_grid,
            "plasma_field": self.plasma_field,
            "spiral_vortex": self.spiral_vortex,
            "stripe_cascade": self.stripe_cascade,
            "dot_matrix": self.dot_matrix,
            "nebula_cloud": self.nebula_cloud,
            "kaleidoscope": self.kaleidoscope,
            "fluid_ink": self.fluid_ink,
            "ripple_pond": self.ripple_pond,
            "holographic": self.holographic,
            "topographic": self.topographic,
            "hexagon_grid": self.hexagon_grid,
            "voronoi_cells": self.voronoi_cells,
            "watercolor_blend": self.watercolor_blend,
            "ocean_waves": self.ocean_waves,
            "rolling_clouds": self.rolling_clouds,
            "geometric_bloom": self.geometric_bloom,
            "color_explosion": self.color_explosion,
            "oil_slick": self.oil_slick,
            "prismatic_waves": self.prismatic_waves,
            "gradient_mesh": self.gradient_mesh,
            "chromatic_pulse": self.chromatic_pulse,
            "color_smoke": self.color_smoke,
            "rainbow_flow": self.rainbow_flow,
            "paint_pour": self.paint_pour,
            "silk_fabric": self.silk_fabric,
            "neon_waves": self.neon_waves,
            "lava_flow": self.lava_flow,
            "color_vortex": self.color_vortex,
            "aurora_curtain": self.aurora_curtain,
            "marble_ink": self.marble_ink,
            "electric_gradient": self.electric_gradient,
            "color_cells": self.color_cells,
            "neon_grid": self.neon_grid,
            "paint_drip": self.paint_drip,
            "crystal_facets": self.crystal_facets,
            "thermal_map": self.thermal_map,
            "color_storm": self.color_storm,
            "pixel_mosaic": self.pixel_mosaic,
            "liquid_chrome": self.liquid_chrome,

        }
        renderer = renderers.get(pattern, self._fast_gradient_flow)
        return renderer(t)


# ═══════════════════════════════════════════════════════════════════════════════
# OVERLAY RENDERER
# ═══════════════════════════════════════════════════════════════════════════════

class OverlayRenderer:
    """Applies overlay effects on top of rendered frames."""

    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height

    def apply(self, frame: np.ndarray, effect: str, t: float) -> np.ndarray:
        """Apply an overlay effect to the frame."""
        if effect == "none" or not effect:
            return frame

        effects = {
            "light_leak": self._light_leak,
            "film_grain": self._film_grain,
            "lens_flare": self._lens_flare,
            "dust_particles": self._dust_particles,

            "chromatic_aberration": self._chromatic_aberration,

            "sparkle_stars": self._sparkle_stars,
            "prism_rainbow": self._prism_rainbow,
            "soft_blur_edge": self._soft_blur_edge,
            "radial_rays": self._radial_rays,

            "noise_texture": self._noise_texture,
            "motion_streak": self._motion_streak,

            "god_rays": self._god_rays,
            "color_wash": self._color_wash,
            "kaleidoscope_overlay": self._kaleidoscope_overlay,
            "heat_haze": self._heat_haze,
            "snow_fall": self._snow_fall,
            "rain_drops": self._rain_drops,
            "bubble_float": self._bubble_float,
            "confetti": self._confetti,
            "golden_dust": self._golden_dust,
            "fog_drift": self._fog_drift,
            "light_rays_top": self._light_rays_top,

            "light_streak": self._light_streak,
            "edge_glow": self._edge_glow,
            "wave_distort": self._wave_distort,

            "vintage_fade": self._vintage_fade,
            "shimmer": self._shimmer,
            "gradient_wipe": self._gradient_wipe,

            "ripple_overlay": self._ripple_overlay,
            "star_field": self._star_field,
            "smoke_wisp": self._smoke_wisp,
            "pulse_ring": self._pulse_ring,
            "diamond_sparkle": self._diamond_sparkle,

            "color_overlay": self._color_overlay,
            "bloom_glow": self._bloom_glow,

        }
        fn = effects.get(effect)
        if fn:
            return fn(frame, t)
        return frame

    def _light_leak(self, frame: np.ndarray, t: float) -> np.ndarray:
        overlay = np.zeros_like(frame, dtype=np.float32)
        cx = int(self.w * (0.3 + 0.4 * math.sin(t * 0.5)))
        cy = int(self.h * 0.3)
        color = (255, 200, 100)
        cv2.circle(overlay, (cx, cy), self.w // 3, color, -1)
        overlay = cv2.GaussianBlur(overlay, (151, 151), 0)
        alpha = 0.2 + 0.1 * math.sin(t * 2)
        return np.clip(frame.astype(np.float32) + overlay * alpha, 0, 255).astype(np.uint8)

    def _film_grain(self, frame: np.ndarray, t: float) -> np.ndarray:
        grain = np.random.randint(-15, 15, frame.shape, dtype=np.int16)
        return np.clip(frame.astype(np.int16) + grain, 0, 255).astype(np.uint8)

    def _lens_flare(self, frame: np.ndarray, t: float) -> np.ndarray:
        overlay = np.zeros_like(frame, dtype=np.float32)
        fx = int(self.w * (0.5 + 0.4 * math.cos(t * 0.3)))
        fy = int(self.h * (0.3 + 0.1 * math.sin(t * 0.4)))
        for r, intensity in [(120, 0.3), (80, 0.5), (40, 0.8)]:
            color = (255, 240, 200)
            sub = np.zeros_like(overlay)
            cv2.circle(sub, (fx, fy), r, color, -1)
            sub = cv2.GaussianBlur(sub, (51, 51), 0)
            overlay += sub * intensity
        return np.clip(frame.astype(np.float32) + overlay * 0.3, 0, 255).astype(np.uint8)

    def _dust_particles(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        np.random.seed(int(t * 5) % 1000)
        for _ in range(50):
            x = np.random.randint(0, self.w)
            y = (np.random.randint(0, self.h) + int(t * 20)) % self.h
            brightness = np.random.randint(150, 255)
            r = np.random.randint(1, 3)
            cv2.circle(result, (x, y), r, (brightness, brightness, brightness), -1, cv2.LINE_AA)
        return result

    def _vignette_pulse(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        dx = x_grid - self.w / 2
        dy = y_grid - self.h / 2
        dist = np.sqrt(dx**2 + dy**2)
        max_dist = math.sqrt((self.w/2)**2 + (self.h/2)**2)
        strength = 0.5 + 0.3 * math.sin(t * 1.5)
        vignette = 1 - np.clip((dist / max_dist) ** 2 * strength * 2, 0, 1)
        return (frame.astype(np.float32) * vignette[..., np.newaxis]).astype(np.uint8)

    def _chromatic_aberration(self, frame: np.ndarray, t: float) -> np.ndarray:
        shift = int(3 + 2 * math.sin(t * 2))
        result = frame.copy()
        result[:, shift:, 2] = frame[:, :-shift, 2]   # Red shift right
        result[:, :-shift, 0] = frame[:, shift:, 0]    # Blue shift left
        return result

    def _glitch_lines(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        if int(t * 10) % 7 < 2:  # Intermittent glitch
            for _ in range(5):
                y = np.random.randint(0, self.h)
                h = np.random.randint(1, 5)
                shift = np.random.randint(-20, 20)
                y_end = min(y + h, self.h)
                if shift > 0:
                    result[y:y_end, shift:] = frame[y:y_end, :-shift]
                elif shift < 0:
                    result[y:y_end, :shift] = frame[y:y_end, -shift:]
        return result

    def _sparkle_stars(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        np.random.seed(int(t * 3) % 500)
        for _ in range(30):
            x = np.random.randint(0, self.w)
            y = np.random.randint(0, self.h)
            brightness = int(150 + 105 * math.sin(t * 5 + x * 0.1 + y * 0.1))
            size = np.random.randint(1, 4)
            color = (brightness, brightness, brightness)
            cv2.drawMarker(result, (x, y), color, cv2.MARKER_STAR, size * 3, 1)
        return result

    def _prism_rainbow(self, frame: np.ndarray, t: float) -> np.ndarray:
        overlay = np.zeros_like(frame, dtype=np.float32)
        for i in range(7):
            hue = i / 7.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.8, 255)
            y_center = int(self.h * 0.2 + self.h * (i / 7.0) * 0.6 + math.sin(t + i) * 30)
            stripe_h = self.h // 10
            y_start = max(0, y_center - stripe_h // 2)
            y_end = min(self.h, y_center + stripe_h // 2)
            overlay[y_start:y_end, :] = (b, g, r)
        overlay = cv2.GaussianBlur(overlay, (51, 51), 0)
        alpha = 0.15 + 0.05 * math.sin(t)
        return np.clip(frame.astype(np.float32) + overlay * alpha, 0, 255).astype(np.uint8)

    def _soft_blur_edge(self, frame: np.ndarray, t: float) -> np.ndarray:
        blurred = cv2.GaussianBlur(frame, (51, 51), 0)
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        dx = (x_grid - self.w / 2) / (self.w / 2)
        dy = (y_grid - self.h / 2) / (self.h / 2)
        dist = np.sqrt(dx**2 + dy**2)
        mask = np.clip((dist - 0.5) * 2, 0, 1)[..., np.newaxis]
        return (frame.astype(np.float32) * (1 - mask) + blurred.astype(np.float32) * mask).astype(np.uint8)

    def _radial_rays(self, frame: np.ndarray, t: float) -> np.ndarray:
        overlay = np.zeros((self.h, self.w), dtype=np.float32)
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        angle = np.arctan2(y_grid - self.h/2, x_grid - self.w/2)
        rays = (np.sin(angle * 12 + t * 2) + 1) / 2.0
        dist = np.sqrt((x_grid - self.w/2)**2 + (y_grid - self.h/2)**2)
        dist_norm = dist / max(self.w, self.h)
        rays *= np.clip(1 - dist_norm, 0, 1) * 0.3
        result = np.clip(frame.astype(np.float32) + rays[..., np.newaxis] * 100, 0, 255).astype(np.uint8)
        return result

    def _scan_line(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        offset = int(t * 60) % 4
        result[offset::4, :, :] = (result[offset::4, :, :].astype(np.float32) * 0.7).astype(np.uint8)
        return result

    def _noise_texture(self, frame: np.ndarray, t: float) -> np.ndarray:
        noise = np.random.randint(0, 30, (self.h // 4, self.w // 4), dtype=np.uint8)
        noise = cv2.resize(noise, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
        return np.clip(frame.astype(np.int16) + noise[..., np.newaxis].astype(np.int16) - 15, 0, 255).astype(np.uint8)

    def _motion_streak(self, frame: np.ndarray, t: float) -> np.ndarray:
        ksize = 15
        kernel = np.zeros((ksize, ksize), dtype=np.float32)
        kernel[ksize // 2, :] = 1.0 / ksize
        blurred = cv2.filter2D(frame, -1, kernel)
        alpha = 0.3 + 0.1 * math.sin(t * 2)
        return cv2.addWeighted(frame, 1 - alpha, blurred, alpha, 0)

    # ── OVERLAY: Bokeh Rain ──
    def _bokeh_rain(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        np.random.seed(77)
        n_drops = 25
        for i in range(n_drops):
            speed = 30 + np.random.random() * 40
            x = int(np.random.random() * self.w)
            y = int((t * speed + np.random.random() * self.h) % (self.h + 40) - 20)
            r = int(5 + np.random.random() * 15)
            bright = 0.15 + np.random.random() * 0.25
            color = (int(200 * bright), int(220 * bright), int(255 * bright))
            cv2.circle(result, (x, y), r, color, -1, cv2.LINE_AA)
            cv2.circle(result, (x, y), r + 4, tuple(int(c * 0.3) for c in color), 2, cv2.LINE_AA)
        result = cv2.GaussianBlur(result, (3, 3), 0)
        return result

    # ── OVERLAY: God Rays ──
    def _god_rays(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        # Light source position (moves slowly across top)
        src_x = self.w * (0.3 + 0.4 * math.sin(t * 0.3))
        src_y = -self.h * 0.1
        dx = x_grid - src_x
        dy = y_grid - src_y
        angle = np.arctan2(dy, dx)
        # Create ray pattern
        num_rays = 8
        rays = (np.sin(angle * num_rays + t * 0.5) + 1) / 2.0
        rays = rays ** 3  # Sharpen rays
        # Fade with distance
        dist = np.sqrt(dx**2 + dy**2) / max(self.w, self.h)
        rays *= np.clip(1.0 - dist * 0.5, 0, 1)
        intensity = rays * 60 * (0.5 + 0.5 * math.sin(t * 0.7))
        return np.clip(frame.astype(np.float32) + intensity[..., np.newaxis], 0, 255).astype(np.uint8)

    # ── OVERLAY: Color Wash ──
    def _color_wash(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        # Sweeping gradient across frame
        nx = x_grid / self.w
        phase = (nx + t * 0.15) % 1.0
        # Warm to cool color wash
        wash = np.zeros((self.h, self.w, 3), dtype=np.float32)
        wash[:, :, 0] = np.sin(phase * math.pi * 2) * 30 + 10  # Blue channel
        wash[:, :, 1] = np.sin(phase * math.pi * 2 + 2) * 20   # Green
        wash[:, :, 2] = np.sin(phase * math.pi * 2 + 4) * 25   # Red
        alpha = 0.25 + 0.1 * math.sin(t * 0.5)
        return np.clip(frame.astype(np.float32) + wash * alpha, 0, 255).astype(np.uint8)

    # ── OVERLAY: Kaleidoscope Refract ──
    def _kaleidoscope_overlay(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        cx, cy = self.w / 2, self.h / 2
        dx = x_grid - cx
        dy = y_grid - cy
        angle = np.arctan2(dy, dx)
        dist = np.sqrt(dx**2 + dy**2)
        # Prismatic refraction pattern
        segments = 6
        mirror_a = np.abs(((angle + t * 0.3) % (2 * math.pi / segments)) - math.pi / segments)
        prism = np.sin(mirror_a * 10 + dist * 0.01) * 0.5 + 0.5
        overlay = np.zeros((self.h, self.w, 3), dtype=np.float32)
        overlay[:, :, 0] = prism * 15  # Blue tint
        overlay[:, :, 1] = prism * 10
        overlay[:, :, 2] = prism * 20  # Red tint
        return np.clip(frame.astype(np.float32) + overlay, 0, 255).astype(np.uint8)

    # ── OVERLAY: Heat Haze ──
    def _heat_haze(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        # Heat distortion - shift pixels
        amplitude = 3.0
        freq = 0.02
        dx_shift = (amplitude * np.sin(y_grid * freq + t * 3)).astype(np.float32)
        dy_shift = (amplitude * 0.5 * np.cos(x_grid * freq + t * 2.5)).astype(np.float32)
        map_x = np.clip(x_grid + dx_shift, 0, self.w - 1).astype(np.float32)
        map_y = np.clip(y_grid + dy_shift, 0, self.h - 1).astype(np.float32)
        result = cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR)
        return result

    # ── 30 NEW OVERLAY EFFECTS ──

    def _snow_fall(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        np.random.seed(int(t * 10) % 1000)
        for _ in range(60):
            x = np.random.randint(0, self.w)
            y = int((np.random.random() * self.h + t * 80 * (0.5 + np.random.random())) % self.h)
            sz = np.random.randint(2, 5)
            cv2.circle(result, (x, y), sz, (255, 255, 255), -1, cv2.LINE_AA)
        return cv2.GaussianBlur(result, (3, 3), 0)

    def _rain_drops(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        np.random.seed(42)
        for _ in range(40):
            x = np.random.randint(0, self.w)
            y0 = int((np.random.random() * self.h + t * 300) % self.h)
            length = np.random.randint(10, 25)
            alpha = 0.3 + np.random.random() * 0.3
            cv2.line(result, (x, y0), (x - 1, y0 + length), (200, 220, 255), 1, cv2.LINE_AA)
        return result

    def _bubble_float(self, frame: np.ndarray, t: float) -> np.ndarray:
        overlay = frame.astype(np.float32)
        np.random.seed(55)
        for _ in range(20):
            x = np.random.randint(0, self.w)
            y = int((1 - ((np.random.random() + t * 0.03 * (0.5 + np.random.random())) % 1.0)) * self.h)
            r = np.random.randint(8, 25)
            cv2.circle(overlay, (x, y), r, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.circle(overlay, (x - r // 3, y - r // 3), r // 4, (255, 255, 255), -1, cv2.LINE_AA)
        return np.clip(overlay, 0, 255).astype(np.uint8)

    def _confetti(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        np.random.seed(99)
        colors = [(255, 100, 100), (100, 255, 100), (100, 100, 255), (255, 255, 100), (255, 100, 255)]
        for _ in range(50):
            x = np.random.randint(0, self.w)
            y = int((np.random.random() * self.h + t * 60 * (0.3 + np.random.random())) % self.h)
            w_c, h_c = np.random.randint(3, 8), np.random.randint(3, 8)
            color = colors[np.random.randint(0, len(colors))]
            cv2.rectangle(result, (x, y), (x + w_c, y + h_c), color, -1)
        return result

    def _golden_dust(self, frame: np.ndarray, t: float) -> np.ndarray:
        overlay = frame.astype(np.float32)
        np.random.seed(33)
        for _ in range(80):
            x = np.random.randint(0, self.w)
            y = np.random.randint(0, self.h)
            brightness = 0.5 + 0.5 * math.sin(t * 3 + np.random.random() * 10)
            if brightness > 0.3:
                sz = np.random.randint(1, 3)
                cv2.circle(overlay, (x, y), sz, (255 * brightness, 220 * brightness, 100 * brightness), -1)
        return np.clip(overlay, 0, 255).astype(np.uint8)

    def _fog_drift(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        nx = x_grid / self.w * 3  + t * 0.2
        ny = y_grid / self.h * 2
        fog = np.sin(nx * 2 + ny) * 0.3 + np.cos(nx + ny * 1.5 + t * 0.3) * 0.2 + 0.5
        fog = np.clip(fog, 0, 1) * 0.25
        fog_layer = (fog * 255)[..., np.newaxis]
        return np.clip(frame.astype(np.float32) + fog_layer, 0, 255).astype(np.uint8)

    def _light_rays_top(self, frame: np.ndarray, t: float) -> np.ndarray:
        overlay = np.zeros((self.h, self.w), dtype=np.float32)
        x_grid = np.arange(self.w, dtype=np.float32)
        for i in range(6):
            cx = self.w * (0.1 + 0.15 * i + 0.05 * math.sin(t * 0.3 + i))
            spread = self.w * 0.08
            ray = np.exp(-((x_grid - cx) / spread) ** 2)
            overlay += ray[np.newaxis, :] * (0.15 + 0.05 * math.sin(t * 2 + i * 1.5))
        fade = np.linspace(1, 0, self.h, dtype=np.float32)[:, np.newaxis]
        overlay = overlay * fade
        return np.clip(frame.astype(np.float32) + overlay[..., np.newaxis] * 180, 0, 255).astype(np.uint8)

    def _halftone(self, frame: np.ndarray, t: float) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        spacing = max(6, int(min(self.w, self.h) * 0.008))
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w]
        dot = np.sin(x_grid * math.pi / spacing) * np.sin(y_grid * math.pi / spacing)
        threshold = gray * 0.8 + 0.1
        pattern = (dot > (1 - threshold * 2)).astype(np.float32)
        return np.clip(frame.astype(np.float32) * (0.7 + pattern[..., np.newaxis] * 0.3), 0, 255).astype(np.uint8)

    def _cross_hatch(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        spacing = 8
        line1 = np.abs(np.sin((x_grid + y_grid + t * 20) * math.pi / spacing))
        line2 = np.abs(np.sin((x_grid - y_grid + t * 15) * math.pi / spacing))
        hatch = np.minimum(line1, line2)
        hatch = np.clip(hatch * 2, 0, 1)
        return np.clip(frame.astype(np.float32) * (0.6 + hatch[..., np.newaxis] * 0.4), 0, 255).astype(np.uint8)

    def _light_streak(self, frame: np.ndarray, t: float) -> np.ndarray:
        overlay = np.zeros((self.h, self.w), dtype=np.float32)
        for i in range(3):
            offset = (t * 100 + i * self.w // 3) % (self.w + self.h)
            y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
            dist = np.abs(x_grid + y_grid - offset)
            streak = np.exp(-(dist / 30) ** 2) * (0.2 + 0.1 * math.sin(t * 3 + i))
            overlay += streak
        return np.clip(frame.astype(np.float32) + overlay[..., np.newaxis] * 200, 0, 255).astype(np.uint8)

    def _edge_glow(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        dx = np.minimum(x_grid, self.w - 1 - x_grid) / self.w
        dy = np.minimum(y_grid, self.h - 1 - y_grid) / self.h
        edge = 1 - np.minimum(dx, dy) * 10
        edge = np.clip(edge, 0, 1) * (0.3 + 0.15 * math.sin(t * 2))
        phase = t * 0.5
        color = np.array([math.sin(phase) * 0.5 + 0.5, math.sin(phase + 2) * 0.5 + 0.5, math.sin(phase + 4) * 0.5 + 0.5])
        return np.clip(frame.astype(np.float32) + edge[..., np.newaxis] * color * 200, 0, 255).astype(np.uint8)

    def _wave_distort(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        dx_shift = (4 * np.sin(y_grid * 0.03 + t * 2)).astype(np.float32)
        dy_shift = (4 * np.cos(x_grid * 0.03 + t * 1.5)).astype(np.float32)
        map_x = np.clip(x_grid + dx_shift, 0, self.w - 1).astype(np.float32)
        map_y = np.clip(y_grid + dy_shift, 0, self.h - 1).astype(np.float32)
        return cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR)

    def _color_split(self, frame: np.ndarray, t: float) -> np.ndarray:
        shift = int(4 + 3 * math.sin(t * 2))
        result = frame.copy()
        result[:, shift:, 2] = frame[:, :-shift, 2]
        result[:, :-shift, 0] = frame[:, shift:, 0]
        return result

    def _vintage_fade(self, frame: np.ndarray, t: float) -> np.ndarray:
        tint = np.array([20, 10, -10], dtype=np.float32)
        fade = 0.85 + 0.05 * math.sin(t * 0.5)
        result = frame.astype(np.float32) * fade + tint + 15
        return np.clip(result, 0, 255).astype(np.uint8)

    def _shimmer(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        shimmer = np.sin(x_grid * 0.05 + t * 5) * np.cos(y_grid * 0.05 + t * 3) * 0.5 + 0.5
        bright = np.clip((shimmer - 0.8) * 10, 0, 1) * 80
        return np.clip(frame.astype(np.float32) + bright[..., np.newaxis], 0, 255).astype(np.uint8)

    def _gradient_wipe(self, frame: np.ndarray, t: float) -> np.ndarray:
        x_grid = np.arange(self.w, dtype=np.float32) / self.w
        wipe_pos = (t * 0.15) % 1.2 - 0.1
        grad = np.clip(1 - np.abs(x_grid - wipe_pos) * 5, 0, 1)
        return np.clip(frame.astype(np.float32) + grad[np.newaxis, :, np.newaxis] * 100, 0, 255).astype(np.uint8)

    def _motion_lines(self, frame: np.ndarray, t: float) -> np.ndarray:
        overlay = np.zeros((self.h, self.w), dtype=np.float32)
        np.random.seed(int(t * 5) % 100)
        for _ in range(15):
            y = np.random.randint(0, self.h)
            thickness = np.random.randint(1, 3)
            alpha = 0.1 + np.random.random() * 0.15
            overlay[y:y + thickness, :] = alpha
        return np.clip(frame.astype(np.float32) + overlay[..., np.newaxis] * 200, 0, 255).astype(np.uint8)

    def _ripple_overlay(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        cx, cy = self.w / 2, self.h / 2
        dist = np.sqrt((x_grid - cx)**2 + (y_grid - cy)**2)
        ripple = np.sin(dist * 0.05 - t * 4) * 4
        map_x = np.clip(x_grid + ripple, 0, self.w - 1).astype(np.float32)
        map_y = np.clip(y_grid + ripple * 0.5, 0, self.h - 1).astype(np.float32)
        return cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR)

    def _star_field(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        np.random.seed(44)
        for _ in range(100):
            x = np.random.randint(0, self.w)
            y = np.random.randint(0, self.h)
            brightness = 0.5 + 0.5 * math.sin(t * 4 + np.random.random() * 20)
            if brightness > 0.5:
                sz = 1 if brightness < 0.7 else 2
                val = int(180 + brightness * 75)
                cv2.circle(result, (x, y), sz, (val, val, val), -1)
        return result

    def _smoke_wisp(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        nx = x_grid / self.w * 3 + t * 0.1
        ny = y_grid / self.h * 3
        wisp = np.sin(nx * 3 + ny * 2 + t * 0.5) * np.cos(nx + ny * 3 - t * 0.3) * 0.5 + 0.5
        wisp = np.clip(wisp - 0.5, 0, 0.5) * 0.3
        return np.clip(frame.astype(np.float32) + wisp[..., np.newaxis] * 200, 0, 255).astype(np.uint8)

    def _pulse_ring(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid, x_grid = np.mgrid[0:self.h, 0:self.w].astype(np.float32)
        cx, cy = self.w / 2, self.h / 2
        dist = np.sqrt((x_grid - cx)**2 + (y_grid - cy)**2)
        ring_r = (t * 100) % max(self.w, self.h)
        ring = np.exp(-((dist - ring_r) / 15) ** 2) * 0.4
        return np.clip(frame.astype(np.float32) + ring[..., np.newaxis] * 200, 0, 255).astype(np.uint8)

    def _diamond_sparkle(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        np.random.seed(77)
        for _ in range(30):
            x = np.random.randint(0, self.w)
            y = np.random.randint(0, self.h)
            brightness = 0.5 + 0.5 * math.sin(t * 6 + np.random.random() * 15)
            if brightness > 0.7:
                sz = int(3 + brightness * 4)
                val = int(200 + brightness * 55)
                cv2.drawMarker(result, (x, y), (val, val, val), cv2.MARKER_DIAMOND, sz, 1)
        return result

    def _neon_edge(self, frame: np.ndarray, t: float) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edges = cv2.GaussianBlur(edges, (5, 5), 0)
        phase = t * 0.8
        color = np.array([math.sin(phase) * 0.5 + 0.5, math.sin(phase + 2) * 0.5 + 0.5, math.sin(phase + 4) * 0.5 + 0.5])
        edge_color = edges.astype(np.float32)[..., np.newaxis] * color * 0.8
        return np.clip(frame.astype(np.float32) + edge_color, 0, 255).astype(np.uint8)

    def _color_overlay(self, frame: np.ndarray, t: float) -> np.ndarray:
        y_grid = np.arange(self.h, dtype=np.float32) / self.h
        x_grid = np.arange(self.w, dtype=np.float32) / self.w
        r = (np.sin(t * 0.3) * 0.5 + 0.5) * 40
        g = (np.sin(t * 0.3 + 2) * 0.5 + 0.5) * 40
        b = (np.sin(t * 0.3 + 4) * 0.5 + 0.5) * 40
        tint = np.array([r, g, b], dtype=np.float32)
        return np.clip(frame.astype(np.float32) + tint, 0, 255).astype(np.uint8)

    def _grid_overlay(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        spacing = max(20, min(self.w, self.h) // 20)
        offset = int(t * 10) % spacing
        color = (255, 255, 255)
        for x in range(offset, self.w, spacing):
            cv2.line(result, (x, 0), (x, self.h), color, 1)
        for y in range(offset, self.h, spacing):
            cv2.line(result, (0, y), (self.w, y), color, 1)
        return cv2.addWeighted(frame, 0.9, result, 0.1, 0)

    def _bloom_glow(self, frame: np.ndarray, t: float) -> np.ndarray:
        bright = cv2.GaussianBlur(frame, (51, 51), 0)
        alpha = 0.25 + 0.1 * math.sin(t * 2)
        return np.clip(frame.astype(np.float32) + bright.astype(np.float32) * alpha, 0, 255).astype(np.uint8)

    def _anamorphic_flare(self, frame: np.ndarray, t: float) -> np.ndarray:
        """Cinematic horizontal lens flare streak."""
        h, w = frame.shape[:2]
        overlay = np.zeros((h, w, 3), dtype=np.float32)
        # Horizontal streak position oscillates
        cy = int(h * (0.35 + 0.15 * math.sin(t * 0.5)))
        intensity = 0.4 + 0.2 * math.sin(t * 0.8)
        # Streak width
        streak_h = int(h * 0.08)
        for dy in range(-streak_h, streak_h + 1):
            y = cy + dy
            if 0 <= y < h:
                falloff = max(0, 1.0 - abs(dy) / streak_h) ** 2
                # Warm tint (orange-white)
                overlay[y, :, 0] = 255 * falloff * intensity
                overlay[y, :, 1] = 200 * falloff * intensity
                overlay[y, :, 2] = 150 * falloff * intensity
        # Add hot spot
        cx = int(w * (0.3 + 0.4 * math.sin(t * 0.3)))
        cv2.circle(overlay, (cx, cy), int(w * 0.05),
                   (255 * intensity, 240 * intensity, 200 * intensity), -1)
        overlay = cv2.GaussianBlur(overlay, (31, 3), 0)
        return np.clip(frame.astype(np.float32) + overlay, 0, 255).astype(np.uint8)

    def _floating_embers(self, frame: np.ndarray, t: float) -> np.ndarray:
        """Glowing ember particles rising gently."""
        h, w = frame.shape[:2]
        result = frame.copy()
        rng = np.random.RandomState(88)
        n = 60
        base_x = rng.rand(n)
        base_speed = rng.rand(n) * 0.2 + 0.05
        base_size = (rng.rand(n) * 2 + 1).astype(int)
        base_brightness = rng.rand(n) * 0.5 + 0.5

        for i in range(n):
            x = int((base_x[i] + math.sin(t * 0.5 + i * 0.7) * 0.03) * w) % w
            y = int((1.0 - ((t * base_speed[i] + i * 0.08) % 1.3)) * h)
            if y < -5 or y > h + 5:
                continue
            r = base_size[i]
            b = base_brightness[i] * (0.6 + 0.4 * math.sin(t * 3 + i))
            # Warm ember color (orange-yellow)
            color = (int(255 * b), int(180 * b), int(50 * b))
            cv2.circle(result, (x, y), r, color, -1, cv2.LINE_AA)
            # Soft glow
            cv2.circle(result, (x, y), r * 3, (int(80 * b), int(40 * b), int(10 * b)), -1, cv2.LINE_AA)

        return result

    def _lens_dust(self, frame: np.ndarray, t: float) -> np.ndarray:
        """Subtle dust particles visible on camera lens."""
        h, w = frame.shape[:2]
        result = frame.copy()
        rng = np.random.RandomState(44)
        n = 80
        xs = (rng.rand(n) * w).astype(int)
        ys = (rng.rand(n) * h).astype(int)
        sizes = (rng.rand(n) * 3 + 1).astype(int)
        brightness = rng.rand(n) * 0.15 + 0.05

        for i in range(n):
            b = brightness[i] * (0.7 + 0.3 * math.sin(t * 0.5 + i * 0.3))
            color = (int(255 * b), int(255 * b), int(240 * b))
            cv2.circle(result, (xs[i], ys[i]), sizes[i], color, -1, cv2.LINE_AA)

        # Subtle overall haze
        bright = cv2.GaussianBlur(result, (21, 21), 0)
        haze = 0.03 + 0.02 * math.sin(t * 0.3)
        return np.clip(result.astype(np.float32) * (1 - haze) + bright.astype(np.float32) * haze, 0, 255).astype(np.uint8)

    def _soft_light_orbs(self, frame: np.ndarray, t: float) -> np.ndarray:
        """Gentle glowing orbs floating dreamily."""
        h, w = frame.shape[:2]
        overlay = np.zeros((h, w, 3), dtype=np.float32)
        rng = np.random.RandomState(66)
        n = 15
        base_x = rng.rand(n)
        base_y = rng.rand(n)
        base_r = (rng.rand(n) * 60 + 20).astype(int)
        base_alpha = rng.rand(n) * 0.15 + 0.05

        for i in range(n):
            x = int((base_x[i] + math.sin(t * 0.2 + i * 1.2) * 0.06) * w)
            y = int((base_y[i] + math.cos(t * 0.15 + i * 0.9) * 0.05) * h)
            r = base_r[i]
            pulse = 0.6 + 0.4 * math.sin(t * 0.8 + i * 1.5)
            alpha = base_alpha[i] * pulse
            # Warm soft white-yellow
            color = (255 * alpha, 240 * alpha, 200 * alpha)
            cv2.circle(overlay, (x, y), r, color, -1, cv2.LINE_AA)

        overlay = cv2.GaussianBlur(overlay, (31, 31), 0)
        return np.clip(frame.astype(np.float32) + overlay, 0, 255).astype(np.uint8)

    def _film_burn(self, frame: np.ndarray, t: float) -> np.ndarray:
        """Classic film burn and light leak transition."""
        h, w = frame.shape[:2]
        overlay = np.zeros((h, w, 3), dtype=np.float32)
        # Burn position sweeps across
        cx = int(w * ((t * 0.1) % 1.5 - 0.25))
        cy = int(h * 0.5)
        # Burn intensity pulsates
        intensity = 0.3 + 0.2 * math.sin(t * 1.5)
        # Elliptical burn shape
        rx, ry = int(w * 0.3), int(h * 0.5)
        y_grid, x_grid = np.mgrid[0:h, 0:w]
        dist = ((x_grid - cx) / max(rx, 1)) ** 2 + ((y_grid - cy) / max(ry, 1)) ** 2
        burn = np.clip(1.0 - dist, 0, 1) * intensity
        # Warm burn tint
        overlay[:, :, 0] = burn * 255  # R
        overlay[:, :, 1] = burn * 180  # G
        overlay[:, :, 2] = burn * 80   # B
        overlay = cv2.GaussianBlur(overlay, (51, 51), 0)
        return np.clip(frame.astype(np.float32) + overlay, 0, 255).astype(np.uint8)

    def _pixel_scatter(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        np.random.seed(int(t * 3) % 50)
        n = 200
        xs = np.random.randint(0, self.w, n)
        ys = np.random.randint(0, self.h, n)
        offsets = np.random.randint(-5, 5, (n, 2))
        for i in range(n):
            sx = np.clip(xs[i] + offsets[i, 0], 0, self.w - 1)
            sy = np.clip(ys[i] + offsets[i, 1], 0, self.h - 1)
            result[ys[i], xs[i]] = frame[sy, sx]
        return result

    def _border_frame(self, frame: np.ndarray, t: float) -> np.ndarray:
        result = frame.copy()
        border = max(5, int(min(self.w, self.h) * 0.02))
        alpha = 0.6 + 0.2 * math.sin(t * 1.5)
        result[:border, :] = np.clip(result[:border, :].astype(np.float32) * (1 - alpha) + alpha * 255, 0, 255).astype(np.uint8)
        result[-border:, :] = np.clip(result[-border:, :].astype(np.float32) * (1 - alpha) + alpha * 255, 0, 255).astype(np.uint8)
        result[:, :border] = np.clip(result[:, :border].astype(np.float32) * (1 - alpha) + alpha * 255, 0, 255).astype(np.uint8)
        result[:, -border:] = np.clip(result[:, -border:].astype(np.float32) * (1 - alpha) + alpha * 255, 0, 255).astype(np.uint8)
        return result

    def _lightning_flash(self, frame: np.ndarray, t: float) -> np.ndarray:
        flash = math.sin(t * 8) ** 20
        if flash > 0.3:
            return np.clip(frame.astype(np.float32) + flash * 120, 0, 255).astype(np.uint8)
        return frame

    def _zoom_pulse(self, frame: np.ndarray, t: float) -> np.ndarray:
        pulse = 0.02 * math.sin(t * 3)
        if abs(pulse) < 0.005:
            return frame
        h, w = frame.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), 0, 1 + pulse)
        return cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REFLECT)


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO GENERATOR (Main orchestrator)
# ═══════════════════════════════════════════════════════════════════════════════

# Resolution presets
RESOLUTIONS = {
    "1920x1080 (Full HD)": (1920, 1080),
    "3840x2160 (4K UHD)": (3840, 2160),
}

# FPS options
FPS_OPTIONS = [30, 60]

# Duration presets (seconds)
DURATION_PRESETS = [5, 10, 15, 20, 30, 60]


class AbstractVideoGenerator:
    """
    Orchestrates the generation of abstract video backgrounds.
    Handles threading, progress callbacks, and video writing.
    """

    def __init__(self):
        self._stop_event = threading.Event()
        self._generating = False
        self._thread = None

    @property
    def is_generating(self) -> bool:
        return self._generating

    def stop(self):
        """Signal the generator to stop."""
        self._stop_event.set()

    def generate_preview_frame(
        self,
        pattern: str,
        overlay: str,
        colors: List[str],
        resolution: Tuple[int, int],
        t: float = 0.0,
    ) -> np.ndarray:
        """
        Generate a single preview frame (for live preview).
        Returns BGR numpy array.
        """
        w, h = resolution
        # Use smaller resolution for preview
        preview_w, preview_h = min(w, 640), min(h, 360)
        if w > 640:
            ratio = 640 / w
            preview_w = 640
            preview_h = int(h * ratio)

        rgb_colors = [hex_to_rgb(c) for c in colors]
        renderer = AbstractVideoRenderer(preview_w, preview_h, rgb_colors)
        overlay_renderer = OverlayRenderer(preview_w, preview_h)

        frame = renderer.render_frame(pattern, t)
        frame = overlay_renderer.apply(frame, overlay, t)
        return frame

    def generate_video(
        self,
        output_path: str,
        pattern: str,
        overlay: str,
        colors: List[str],
        resolution: Tuple[int, int],
        fps: int = 30,
        duration: int = 10,
        output_format: str = "mp4",
        bitrate: int = 20,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        done_callback: Optional[Callable[[bool, str], None]] = None,
    ):
        """
        Start video generation in a background thread.

        Args:
            output_path: Output file path
            pattern: Background pattern key
            overlay: Overlay effect key
            colors: List of 4 hex color strings
            resolution: (width, height) tuple
            fps: Frames per second
            duration: Duration in seconds
            output_format: "mp4" or "mov"
            bitrate: Bitrate in Mbps
            progress_callback: Called with (progress_0_to_1, status_text)
            done_callback: Called with (success, message)
        """
        if self._generating:
            if done_callback:
                done_callback(False, "Already generating a video")
            return

        self._stop_event.clear()
        self._generating = True

        def _worker():
            try:
                self._do_generate(
                    output_path, pattern, overlay, colors, resolution,
                    fps, duration, output_format, bitrate, progress_callback
                )
                if self._stop_event.is_set():
                    # Clean up partial file
                    if os.path.exists(output_path):
                        try:
                            os.remove(output_path)
                        except Exception:
                            pass
                    if done_callback:
                        done_callback(False, "Generation cancelled")
                else:
                    if done_callback:
                        done_callback(True, f"Video saved to:\n{output_path}")
            except Exception as e:
                logger.error(f"Video generation error: {e}", exc_info=True)
                if done_callback:
                    done_callback(False, f"Error: {str(e)}")
            finally:
                self._generating = False

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def _detect_hw_encoder(self) -> Tuple[Optional[str], str]:
        """
        Detect a working hardware H.264 encoder.
        Uses the shared detect_working_hw_encoder() which test-encodes
        a tiny video to verify the encoder actually works on this GPU.
        Returns (encoder_name, label) or (None, "libx264 (CPU)").
        """
        return detect_working_hw_encoder()

    def _do_generate(
        self,
        output_path: str,
        pattern: str,
        overlay: str,
        colors: List[str],
        resolution: Tuple[int, int],
        fps: int,
        duration: int,
        output_format: str,
        bitrate: int,
        progress_callback: Optional[Callable],
    ):
        """
        Internal: performs the actual frame-by-frame generation.
        Uses FFmpeg subprocess with raw frame piping for professional-grade
        H.264 encoding with exact bitrate control and GPU acceleration.

        Performance optimizations:
          - Renders at max 1080p internally, FFmpeg upscales if target is larger
            (abstract patterns are mathematical, so upscaling looks identical)
          - Multi-threaded frame rendering pipeline (uses all CPU cores)
        """
        import subprocess
        from queue import Queue

        target_w, target_h = resolution
        total_frames = fps * duration

        # ── OPTIMIZATION: Render at max 1080p, FFmpeg upscales ──
        # For abstract/mathematical patterns, rendering at 1080p and upscaling
        # to 4K is visually identical but ~4x faster.
        MAX_RENDER_H = 1080
        if target_h > MAX_RENDER_H:
            render_h = MAX_RENDER_H
            render_w = int(target_w * (MAX_RENDER_H / target_h))
            # Ensure even dimensions (required by H.264)
            render_w = render_w + (render_w % 2)
            need_upscale = True
            logger.info(
                "Render at %dx%d, FFmpeg upscale to %dx%d",
                render_w, render_h, target_w, target_h
            )
        else:
            render_w, render_h = target_w, target_h
            need_upscale = False

        rgb_colors = [hex_to_rgb(c) for c in colors]

        # Determine extension
        ext = ".mp4" if output_format == "mp4" else ".mov"
        if not output_path.lower().endswith(ext):
            base = output_path.rsplit('.', 1)[0] if '.' in os.path.basename(output_path) else output_path
            output_path = base + ext

        # ── Detect GPU encoder ──
        hw_encoder, encoder_label = self._detect_hw_encoder()

        # ── Build FFmpeg command ──
        bitrate_str = f"{bitrate}M"
        maxrate_str = f"{int(bitrate * 1.2)}M"
        bufsize_str = f"{bitrate * 2}M"

        ff_cmd = [
            _get_ffmpeg_path(), "-y", "-hide_banner",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{render_w}x{render_h}",
            "-r", str(fps),
            "-i", "pipe:0",
        ]

        # Add upscale filter if rendering at lower resolution
        vf_filters = []
        if need_upscale:
            vf_filters.append(f"scale={target_w}:{target_h}:flags=lanczos")

        if vf_filters:
            ff_cmd.extend(["-vf", ",".join(vf_filters)])

        if hw_encoder:
            if hw_encoder == "h264_nvenc":
                ff_cmd.extend([
                    "-c:v", "h264_nvenc",
                    "-preset", "p4",
                    "-rc", "vbr",
                    "-b:v", bitrate_str,
                    "-maxrate", maxrate_str,
                    "-bufsize", bufsize_str,
                ])
            elif hw_encoder == "h264_amf":
                ff_cmd.extend([
                    "-c:v", "h264_amf",
                    "-quality", "balanced",
                    "-b:v", bitrate_str,
                    "-maxrate", maxrate_str,
                    "-bufsize", bufsize_str,
                ])
            elif hw_encoder == "h264_qsv":
                ff_cmd.extend([
                    "-c:v", "h264_qsv",
                    "-preset", "medium",
                    "-b:v", bitrate_str,
                    "-maxrate", maxrate_str,
                    "-bufsize", bufsize_str,
                ])
            logger.info("Encoding with %s @ %s", hw_encoder, bitrate_str)
        else:
            ff_cmd.extend([
                "-c:v", "libx264",
                "-preset", "medium",
                "-b:v", bitrate_str,
                "-maxrate", maxrate_str,
                "-bufsize", bufsize_str,
            ])
            logger.info("Encoding with libx264 (CPU) @ %s", bitrate_str)

        ff_cmd.extend([
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ])

        logger.info("FFmpeg cmd: %s", " ".join(ff_cmd))

        if progress_callback:
            progress_callback(0.0, f"Starting encoder ({encoder_label})...")

        # ── Launch FFmpeg process ──
        stderr_lines = []

        proc = subprocess.Popen(
            ff_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        def _drain_stderr():
            try:
                for line in iter(proc.stderr.readline, b''):
                    stderr_lines.append(line.decode('utf-8', errors='replace').strip())
            except Exception:
                pass

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        # ── Multi-threaded frame rendering pipeline ──
        num_workers = max(2, min(os.cpu_count() or 4, 6))
        frame_queue = Queue(maxsize=num_workers * 2)
        task_queue = Queue()
        render_error = [None]

        def _render_worker(w_renderer, w_overlay):
            """Worker that renders frames and puts them in the output queue."""
            try:
                while True:
                    task = task_queue.get()
                    if task is None:
                        break
                    frame_idx, t_val = task
                    if self._stop_event.is_set():
                        frame_queue.put((frame_idx, None))
                        continue
                    frame = w_renderer.render_frame(pattern, t_val)
                    frame = w_overlay.apply(frame, overlay, t_val)
                    frame_queue.put((frame_idx, frame))
            except Exception as e:
                render_error[0] = e
                frame_queue.put((-1, None))

        # Create per-worker renderers (each needs own coordinate grids)
        workers = []
        for _ in range(num_workers):
            wr = AbstractVideoRenderer(render_w, render_h, rgb_colors)
            wo = OverlayRenderer(render_w, render_h)
            t = threading.Thread(target=_render_worker, args=(wr, wo), daemon=True)
            t.start()
            workers.append(t)

        try:
            render_start = time.time()

            # Submit all frame tasks
            for frame_idx in range(total_frames):
                task_queue.put((frame_idx, frame_idx / fps))

            # Sentinel values to stop workers
            for _ in range(num_workers):
                task_queue.put(None)

            # Collect frames IN ORDER and write to FFmpeg
            pending = {}
            next_frame = 0

            while next_frame < total_frames:
                if self._stop_event.is_set():
                    break
                if render_error[0]:
                    raise render_error[0]

                idx, frame = frame_queue.get(timeout=60)

                if idx == -1:
                    if render_error[0]:
                        raise render_error[0]
                    break

                if frame is None:
                    next_frame = max(next_frame, idx + 1)
                    continue

                pending[idx] = frame

                # Write frames in order
                while next_frame in pending:
                    f = pending.pop(next_frame)
                    try:
                        proc.stdin.write(f.tobytes())
                    except (BrokenPipeError, OSError):
                        self._stop_event.set()
                        break
                    next_frame += 1

                    if progress_callback and next_frame % 3 == 0:
                        pct = next_frame / total_frames
                        elapsed_time = time.time() - render_start
                        fps_actual = next_frame / elapsed_time if elapsed_time > 0 else 0
                        eta = (total_frames - next_frame) / fps_actual if fps_actual > 0 else 0

                        progress_callback(
                            pct,
                            f"[{encoder_label}] Frame {next_frame}/{total_frames} "
                            f"({int(pct * 100)}%) \u2022 {fps_actual:.1f} fps \u2022 "
                            f"ETA {int(eta)}s"
                        )

            # Wait for workers to finish
            for w in workers:
                w.join(timeout=5)

            # Close stdin to signal end of input
            try:
                proc.stdin.close()
            except Exception:
                pass

            # Wait for FFmpeg to finalize
            if progress_callback:
                progress_callback(0.99, "Finalizing video (faststart)...")

            finalize_start = time.time()
            while proc.poll() is None:
                time.sleep(0.5)
                if self._stop_event.is_set():
                    proc.kill()
                    break
                elapsed_fin = time.time() - finalize_start
                if progress_callback:
                    progress_callback(0.99, f"Finalizing video... ({int(elapsed_fin)}s)")

            stderr_thread.join(timeout=5)

            if proc.returncode is not None and proc.returncode != 0:
                err = "\n".join(stderr_lines[-10:]) if stderr_lines else "Unknown error"
                raise RuntimeError(
                    f"FFmpeg encoding failed (code {proc.returncode}):\n"
                    f"{err[-500:]}"
                )

        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            raise
        finally:
            for closeable in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if closeable:
                        closeable.close()
                except Exception:
                    pass

        if progress_callback:
            if os.path.exists(output_path):
                mb = os.path.getsize(output_path) / (1024 * 1024)
                total_time = time.time() - render_start
                progress_callback(
                    1.0,
                    f"\u2705 Complete! ({mb:.1f} MB in {int(total_time)}s)"
                )
            else:
                progress_callback(1.0, "Complete!")

