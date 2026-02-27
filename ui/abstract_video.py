"""
RZ Studio — Abstract Video Background Generator UI
Sidebar-based page for creating commercially valuable abstract motion backgrounds.

Features:
    - 15 background pattern types
    - 15 overlay effect types
    - Live animated preview
    - Custom 4-color RGB palette with color pickers
    - Auto color harmony generator (11 types)
    - MP4/MOV H.264 output
    - Resolution, FPS, duration, bitrate settings
    - Save location picker
"""

from core.abstract_video import _get_ffmpeg_path, detect_working_hw_encoder

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, colorchooser
import threading
import os
import time
import webbrowser
import logging

from PIL import Image, ImageTk
import numpy as np
import cv2

from ui.theme import COLORS
from core.abstract_video import (
    BACKGROUND_PATTERNS, OVERLAY_EFFECTS,
    RESOLUTIONS, FPS_OPTIONS, DURATION_PRESETS, HARMONY_TYPES,
    AbstractVideoGenerator, generate_harmony_colors, hex_to_rgb, rgb_to_hex,
)

logger = logging.getLogger(__name__)

try:
    import core.database as db
    _HAS_DB = True
except ImportError:
    _HAS_DB = False

# ─── Visual Constants ─────────────────────────────────────────────────────────
_VC = {
    "sidebar_bg":       "#080c22",
    "card_bg":          "#0d1335",
    "card_hover":       "#111a48",
    "divider":          "#1a2555",
    "preview_bg":       "#050810",
    "preview_border":   "#1a2555",
    "color_swatch_border": "#2a357a",
}


class AbstractVideoMixin:
    """Mixin that adds the Abstract Video Background Generator page."""

    # ═══════════════════════════════════════════════════════════════════════════
    # PAGE BUILDER
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_abstract_video_page(self, parent):
        self.av_page_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.av_page_frame.grid_columnconfigure(0, weight=0, minsize=320)
        self.av_page_frame.grid_columnconfigure(1, weight=1)
        self.av_page_frame.grid_rowconfigure(0, weight=1)

        # ── State (must be initialized BEFORE sidebar build) ──
        self._av_generator = AbstractVideoGenerator()
        self._av_preview_running = False
        self._av_preview_time = 0.0
        # Use Documents folder for output (safe even if app is in C:\Program Files)
        try:
            docs_path = os.path.join(os.path.expanduser("~"), "Documents", "RZ Studio", "Output", "AbstractVideos")
            os.makedirs(docs_path, exist_ok=True)
            self._av_output_path = docs_path
        except Exception:
            self._av_output_path = os.path.join(os.getcwd(), "Output", "AbstractVideos")
            try:
                os.makedirs(self._av_output_path, exist_ok=True)
            except Exception:
                self._av_output_path = os.path.join(os.path.expanduser("~"), "Desktop")

        # Restore previously saved output path
        if _HAS_DB:
            saved = db.get_setting("av_output_path", "")
            if saved and os.path.isdir(saved):
                self._av_output_path = saved

        # ── Left sidebar ──
        sidebar = ctk.CTkFrame(
            self.av_page_frame, fg_color=_VC["sidebar_bg"],
            width=320, corner_radius=0
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        sidebar_scroll = ctk.CTkScrollableFrame(
            sidebar, fg_color="transparent",
            scrollbar_button_color=COLORS["accent_blue"],
            scrollbar_button_hover_color=COLORS["neon_blue"]
        )
        sidebar_scroll.pack(fill="both", expand=True)
        sidebar_inner = ctk.CTkFrame(sidebar_scroll, fg_color="transparent")
        sidebar_inner.pack(fill="both", expand=True, padx=16, pady=12)
        self._build_av_sidebar(sidebar_inner)

        # ── Right content (preview + generate) ──
        content = ctk.CTkFrame(self.av_page_frame, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        content.grid_rowconfigure(0, weight=1)   # Preview area
        content.grid_rowconfigure(1, weight=0)   # Progress area
        content.grid_columnconfigure(0, weight=1)
        self._build_av_content(content)

        # Start preview loop
        self.after(100, self._av_start_preview_loop)

    # ═══════════════════════════════════════════════════════════════════════════
    # SIDEBAR
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_av_sidebar(self, parent):
        # ── Title ──
        ctk.CTkLabel(
            parent, text="🎬 Abstract Video",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=COLORS["neon_blue"]
        ).pack(pady=(0, 1), anchor="w")
        ctk.CTkLabel(
            parent, text="Motion Background Generator",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"]
        ).pack(pady=(0, 12), anchor="w")

        # Detect GPU encoder in background
        self.after(300, self._av_detect_gpu)

        # ═══════════════════════════════════════════════════════════════════════
        # BACKGROUND PATTERN
        # ═══════════════════════════════════════════════════════════════════════
        self._av_lbl(parent, "🎨  Background Pattern")

        pattern_keys = list(BACKGROUND_PATTERNS.keys())
        pattern_display = [f"{BACKGROUND_PATTERNS[k]['icon']} {BACKGROUND_PATTERNS[k]['name']}" for k in pattern_keys]
        self._av_pattern_keys = pattern_keys
        self._av_pattern_display = pattern_display
        self.av_pattern_var = ctk.StringVar(value=pattern_display[0])

        ctk.CTkOptionMenu(
            parent, values=pattern_display, variable=self.av_pattern_var,
            fg_color=COLORS["bg_input"], button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["neon_blue"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11), dropdown_font=ctk.CTkFont(size=11),
            height=32, corner_radius=6
        ).pack(fill="x", pady=(0, 2))

        # Pattern description
        self._av_pattern_desc = ctk.CTkLabel(
            parent, text=BACKGROUND_PATTERNS[pattern_keys[0]]["desc"],
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"],
            wraplength=280, justify="left"
        )
        self._av_pattern_desc.pack(pady=(0, 8), anchor="w")
        self.av_pattern_var.trace_add("write", self._av_on_pattern_change)

        self._av_sep(parent)

        # ═══════════════════════════════════════════════════════════════════════
        # OVERLAY EFFECT
        # ═══════════════════════════════════════════════════════════════════════
        self._av_lbl(parent, "✨  Overlay Effect")

        overlay_keys = list(OVERLAY_EFFECTS.keys())
        overlay_display = [f"{OVERLAY_EFFECTS[k]['icon']} {OVERLAY_EFFECTS[k]['name']}" for k in overlay_keys]
        self._av_overlay_keys = overlay_keys
        self._av_overlay_display = overlay_display
        self.av_overlay_var = ctk.StringVar(value=overlay_display[0])

        ctk.CTkOptionMenu(
            parent, values=overlay_display, variable=self.av_overlay_var,
            fg_color=COLORS["bg_input"], button_color=COLORS["accent_purple"],
            button_hover_color="#9b4dff",
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11), dropdown_font=ctk.CTkFont(size=11),
            height=32, corner_radius=6
        ).pack(fill="x", pady=(0, 2))

        # Overlay description
        self._av_overlay_desc = ctk.CTkLabel(
            parent, text=OVERLAY_EFFECTS[overlay_keys[0]]["desc"],
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"],
            wraplength=280, justify="left"
        )
        self._av_overlay_desc.pack(pady=(0, 8), anchor="w")
        self.av_overlay_var.trace_add("write", self._av_on_overlay_change)

        self._av_sep(parent)

        # ═══════════════════════════════════════════════════════════════════════
        # COLOR PALETTE (4 colors)
        # ═══════════════════════════════════════════════════════════════════════
        self._av_lbl(parent, "🎨  Color Palette (4 Colors)")

        self._av_color_vars = [
            ctk.StringVar(value="#0066ff"),
            ctk.StringVar(value="#7b2fff"),
            ctk.StringVar(value="#00d4ff"),
            ctk.StringVar(value="#ff4466"),
        ]

        colors_frame = ctk.CTkFrame(parent, fg_color="transparent")
        colors_frame.pack(fill="x", pady=(0, 4))

        self._av_color_swatches = []
        self._av_color_entries = []

        for i in range(4):
            row = ctk.CTkFrame(colors_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)

            # Color swatch button
            swatch = ctk.CTkButton(
                row, text="", width=32, height=32, corner_radius=6,
                fg_color=self._av_color_vars[i].get(),
                hover_color=self._av_color_vars[i].get(),
                border_width=2, border_color=_VC["color_swatch_border"],
                command=lambda idx=i: self._av_pick_color(idx)
            )
            swatch.pack(side="left", padx=(0, 6))
            self._av_color_swatches.append(swatch)

            # Label
            ctk.CTkLabel(
                row, text=f"Color {i+1}",
                font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"],
                width=50
            ).pack(side="left", padx=(0, 4))

            # Hex entry
            entry = ctk.CTkEntry(
                row, textvariable=self._av_color_vars[i],
                font=ctk.CTkFont(size=11, family="Consolas"),
                fg_color=COLORS["bg_input"], border_color=COLORS["border"],
                text_color=COLORS["text_primary"],
                height=30, corner_radius=6, width=100
            )
            entry.pack(side="left", fill="x", expand=True)
            entry.bind("<Return>", lambda e, idx=i: self._av_on_color_entry(idx))
            entry.bind("<FocusOut>", lambda e, idx=i: self._av_on_color_entry(idx))
            self._av_color_entries.append(entry)

        # ── Auto Color Harmony ──
        harmony_row = ctk.CTkFrame(parent, fg_color="transparent")
        harmony_row.pack(fill="x", pady=(6, 2))

        ctk.CTkLabel(
            harmony_row, text="Harmony:",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 6))

        harmony_display = [h.replace("_", " ").title() for h in HARMONY_TYPES]
        self.av_harmony_var = ctk.StringVar(value="Random")
        ctk.CTkOptionMenu(
            harmony_row, values=harmony_display, variable=self.av_harmony_var,
            fg_color=COLORS["bg_input"], button_color="#2a5a2a",
            button_hover_color="#3a7a3a",
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=10), dropdown_font=ctk.CTkFont(size=10),
            height=28, corner_radius=6, width=120
        ).pack(side="left", fill="x", expand=True)

        # Generate Colors button
        ctk.CTkButton(
            parent, text="🎲  Generate Colors",
            command=self._av_generate_colors,
            fg_color="#1a3a1a", hover_color="#2a5a2a",
            text_color="#00ff88",
            border_width=1, border_color="#2a5a2a",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=34, corner_radius=8
        ).pack(fill="x", pady=(4, 8))

        self._av_sep(parent)

        # ═══════════════════════════════════════════════════════════════════════
        # VIDEO SETTINGS
        # ═══════════════════════════════════════════════════════════════════════
        self._av_lbl(parent, "📐  Resolution")

        res_names = list(RESOLUTIONS.keys())
        self.av_resolution_var = ctk.StringVar(value=res_names[0])
        ctk.CTkOptionMenu(
            parent, values=res_names, variable=self.av_resolution_var,
            fg_color=COLORS["bg_input"], button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["neon_blue"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11), dropdown_font=ctk.CTkFont(size=11),
            height=32, corner_radius=6
        ).pack(fill="x", pady=(0, 8))

        # FPS
        self._av_lbl(parent, "🎞️  FPS")
        fps_strs = [str(f) for f in FPS_OPTIONS]
        self.av_fps_var = ctk.StringVar(value="30")
        ctk.CTkSegmentedButton(
            parent, values=fps_strs, variable=self.av_fps_var,
            selected_color=COLORS["accent_blue"],
            selected_hover_color=COLORS["neon_blue"],
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), height=30, corner_radius=6
        ).pack(fill="x", pady=(0, 8))

        # Duration
        self._av_lbl(parent, "⏱️  Duration (seconds)")

        dur_frame = ctk.CTkFrame(parent, fg_color="transparent")
        dur_frame.pack(fill="x", pady=(0, 2))

        dur_strs = [str(d) for d in DURATION_PRESETS]
        self.av_duration_var = ctk.StringVar(value="10")
        ctk.CTkSegmentedButton(
            dur_frame, values=dur_strs, variable=self.av_duration_var,
            selected_color=COLORS["accent_purple"],
            selected_hover_color="#9b4dff",
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), height=30, corner_radius=6
        ).pack(fill="x")

        # Custom duration entry
        custom_dur = ctk.CTkFrame(parent, fg_color="transparent")
        custom_dur.pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(
            custom_dur, text="Custom:",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"]
        ).pack(side="left", padx=(0, 4))
        self._av_custom_dur_entry = ctk.CTkEntry(
            custom_dur, placeholder_text="e.g. 45",
            font=ctk.CTkFont(size=10), height=26, width=60,
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            text_color=COLORS["text_primary"], corner_radius=6
        )
        self._av_custom_dur_entry.pack(side="left")
        ctk.CTkLabel(
            custom_dur, text="sec",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"]
        ).pack(side="left", padx=(4, 0))

        self._av_sep(parent)

        # ═══════════════════════════════════════════════════════════════════════
        # OUTPUT FORMAT
        # ═══════════════════════════════════════════════════════════════════════
        self._av_lbl(parent, "📦  Output Format")
        self.av_format_var = ctk.StringVar(value="MP4")
        ctk.CTkSegmentedButton(
            parent, values=["MP4", "MOV"], variable=self.av_format_var,
            selected_color=COLORS["accent_purple"],
            selected_hover_color="#9b4dff",
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), height=30, corner_radius=6
        ).pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(
            parent, text="Both use H.264 codec",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"]
        ).pack(pady=(0, 8), anchor="w")

        # Speed (animation speed multiplier)
        self._av_lbl(parent, "⚡  Animation Speed")
        self.av_speed_var = ctk.StringVar(value="1.0x")
        ctk.CTkSegmentedButton(
            parent, values=["0.5x", "1.0x", "1.5x", "2.0x"], variable=self.av_speed_var,
            selected_color=COLORS["accent_blue"],
            selected_hover_color=COLORS["neon_blue"],
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), height=30, corner_radius=6
        ).pack(fill="x", pady=(0, 8))

        # Blur smoothing
        self._av_lbl(parent, "🔮  Smoothing")
        self.av_smooth_var = ctk.StringVar(value="Medium")
        ctk.CTkSegmentedButton(
            parent, values=["None", "Light", "Medium", "Heavy"], variable=self.av_smooth_var,
            selected_color=COLORS["accent_purple"],
            selected_hover_color="#9b4dff",
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), height=30, corner_radius=6
        ).pack(fill="x", pady=(0, 8))

        self._av_sep(parent)

        # ═══════════════════════════════════════════════════════════════════════
        # SAVE LOCATION
        # ═══════════════════════════════════════════════════════════════════════
        self._av_lbl(parent, "💾  Save Location")
        self._av_path_label = ctk.CTkLabel(
            parent, text="...", text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=10), wraplength=270, justify="left"
        )
        self._av_path_label.pack(pady=(0, 4), anchor="w")

        path_btns = ctk.CTkFrame(parent, fg_color="transparent")
        path_btns.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(
            path_btns, text="📁 Change", height=28, corner_radius=6,
            command=self._av_change_output,
            fg_color=COLORS["bg_input"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            border_width=1, border_color=COLORS["border"],
            font=ctk.CTkFont(size=11)
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(
            path_btns, text="📂 Open", height=28, corner_radius=6,
            command=lambda: webbrowser.open(self._av_output_path),
            fg_color=COLORS["bg_input"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            border_width=1, border_color=COLORS["border"],
            font=ctk.CTkFont(size=11)
        ).pack(side="left", expand=True, fill="x")

        self._av_sep(parent)

        # ═══════════════════════════════════════════════════════════════════════
        # GENERATE BUTTON
        # ═══════════════════════════════════════════════════════════════════════
        self._av_generate_btn = ctk.CTkButton(
            parent, text="🚀  Generate Video",
            command=self._av_start_generate,
            fg_color="#00c96a", hover_color="#00a554",
            text_color="white", font=ctk.CTkFont(size=14, weight="bold"),
            height=44, corner_radius=8
        )
        self._av_generate_btn.pack(fill="x", pady=(0, 4))

        self._av_stop_btn = ctk.CTkButton(
            parent, text="⏹  Stop Generation",
            command=self._av_stop_generate,
            fg_color="#cc3355", hover_color="#aa2244",
            text_color="white", font=ctk.CTkFont(size=14, weight="bold"),
            height=44, corner_radius=8
        )
        # Not packed yet — shown only during generation

        self._av_sep(parent)

        # ═══════════════════════════════════════════════════════════════════════
        # BATCH GENERATE
        # ═══════════════════════════════════════════════════════════════════════
        self._av_lbl(parent, "📦  Batch Generate")
        ctk.CTkLabel(
            parent, text="Auto-create multiple videos with random\npattern, overlay & color combinations",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"],
            justify="left"
        ).pack(pady=(0, 4), anchor="w")

        batch_row = ctk.CTkFrame(parent, fg_color="transparent")
        batch_row.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            batch_row, text="Count:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 6))

        self._av_batch_count = ctk.CTkEntry(
            batch_row, placeholder_text="10", width=60, height=30,
            font=ctk.CTkFont(size=12), corner_radius=6,
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            text_color=COLORS["text_primary"], justify="center"
        )
        self._av_batch_count.pack(side="left", padx=(0, 8))
        self._av_batch_count.insert(0, "10")

        # Overlay toggle for batch
        self._av_batch_overlay_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            parent, text="Include Overlay Effects",
            variable=self._av_batch_overlay_var,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["neon_blue"],
            border_color=COLORS["border"],
            corner_radius=4, height=24,
            checkbox_width=18, checkbox_height=18,
        ).pack(fill="x", pady=(2, 6), anchor="w")

        self._av_batch_btn = ctk.CTkButton(
            parent, text="🎲  Batch Generate",
            command=self._av_start_batch,
            fg_color="#1a3a6a", hover_color="#2a5a9a",
            text_color="#66bbff",
            border_width=1, border_color="#2a5a9a",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=38, corner_radius=8
        )
        self._av_batch_btn.pack(fill="x", pady=(0, 4))

        # Batch stop button (hidden by default)
        self._av_batch_stop_btn = ctk.CTkButton(
            parent, text="⏹  Stop Batch",
            command=self._av_stop_batch,
            fg_color="#cc3355", hover_color="#aa2244",
            text_color="white", font=ctk.CTkFont(size=12, weight="bold"),
            height=38, corner_radius=8
        )
        # Not packed — shown only during batch

        # Batch state
        self._av_batch_queue = []
        self._av_batch_index = 0
        self._av_batch_total = 0
        self._av_batch_running = False

        # Update path label
        self._av_update_path_label()

    # ═══════════════════════════════════════════════════════════════════════════
    # RIGHT CONTENT — PREVIEW + PROGRESS
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_av_content(self, parent):
        # Preview container with border
        preview_container = ctk.CTkFrame(
            parent, fg_color=_VC["preview_bg"],
            corner_radius=12, border_width=2, border_color=_VC["preview_border"]
        )
        preview_container.grid(row=0, column=0, sticky="nsew", padx=4, pady=(0, 8))
        preview_container.grid_rowconfigure(0, weight=0)  # header
        preview_container.grid_rowconfigure(1, weight=1)  # preview
        preview_container.grid_columnconfigure(0, weight=1)

        # Preview header
        hdr = ctk.CTkFrame(preview_container, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))

        ctk.CTkLabel(
            hdr, text="🖥️  Live Preview",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")

        self._av_preview_fps_label = ctk.CTkLabel(
            hdr, text="",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"]
        )
        self._av_preview_fps_label.pack(side="right")

        # Preview canvas
        self._av_preview_label = ctk.CTkLabel(
            preview_container, text="",
            fg_color=_VC["preview_bg"]
        )
        self._av_preview_label.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 16))

        # Keep a reference to the PhotoImage
        self._av_preview_photo = None

        # ── Progress bar area ──
        progress_frame = ctk.CTkFrame(
            parent, fg_color=_VC["card_bg"],
            corner_radius=12, border_width=1, border_color=_VC["divider"],
            height=80
        )
        progress_frame.grid(row=1, column=0, sticky="ew", padx=4)
        progress_frame.pack_propagate(False)

        inner = ctk.CTkFrame(progress_frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=10)

        self._av_progress_status = ctk.CTkLabel(
            inner, text="Ready to generate",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"]
        )
        self._av_progress_status.pack(anchor="w")

        bar_row = ctk.CTkFrame(inner, fg_color="transparent")
        bar_row.pack(fill="x", pady=(4, 0))

        self._av_progress_bar = ctk.CTkProgressBar(
            bar_row, height=12,
            progress_color=COLORS["accent_blue"],
            fg_color=COLORS["bg_input"],
            corner_radius=6
        )
        self._av_progress_bar.set(0)
        self._av_progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 12))

        self._av_progress_pct = ctk.CTkLabel(
            bar_row, text="0%", width=50,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"]
        )
        self._av_progress_pct.pack(side="left")

    # ═══════════════════════════════════════════════════════════════════════════
    # SIDEBAR HELPERS
    # ═══════════════════════════════════════════════════════════════════════════

    def _av_lbl(self, parent, text):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=11, weight="bold"), text_color=COLORS["text_primary"]
        ).pack(pady=(0, 3), anchor="w")

    def _av_sep(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color=_VC["divider"]).pack(fill="x", pady=(0, 8))

    def _av_detect_gpu(self):
        """Detect GPU encoder in background and update label."""

        def _detect():
            # Use test-based detection that actually verifies encoder works
            encoder_name, encoder_label = detect_working_hw_encoder()
            gpu_name = ""

            # Get actual GPU name via WMI
            try:
                import subprocess
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_VideoController | "
                     "Select-Object -ExpandProperty Name"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if r.returncode == 0 and r.stdout.strip():
                    gpu_names = [n.strip() for n in r.stdout.strip().split("\n") if n.strip()]
                    # Filter out Microsoft Basic Display
                    real_gpus = [n for n in gpu_names
                                 if "basic" not in n.lower()
                                 and "microsoft" not in n.lower()]
                    if real_gpus:
                        # Pick best match for the detected encoder type
                        if encoder_name == "h264_nvenc":
                            for g in real_gpus:
                                if any(k in g.lower() for k in ["nvidia", "geforce", "rtx", "gtx"]):
                                    gpu_name = g
                                    break
                        elif encoder_name == "h264_amf":
                            for g in real_gpus:
                                if "amd" in g.lower() or "radeon" in g.lower():
                                    gpu_name = g
                                    break
                        elif encoder_name == "h264_qsv":
                            for g in real_gpus:
                                if "intel" in g.lower():
                                    gpu_name = g
                                    break
                        if not gpu_name and real_gpus:
                            gpu_name = real_gpus[0]
            except Exception:
                pass

            # Log detection result
            if encoder_name:
                logger.info("GPU encoder verified: %s (%s)", encoder_label, gpu_name or "unknown")
            else:
                logger.info("No working GPU encoder found, will use libx264 (CPU)")

        threading.Thread(target=_detect, daemon=True).start()



    # ═══════════════════════════════════════════════════════════════════════════
    # COLOR MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════════

    def _av_pick_color(self, idx: int):
        """Open system color picker for color at index."""
        current = self._av_color_vars[idx].get()
        try:
            initial_rgb = hex_to_rgb(current)
        except Exception:
            initial_rgb = (0, 102, 255)

        color = colorchooser.askcolor(
            initialcolor=current,
            title=f"Pick Color {idx + 1}"
        )
        if color and color[1]:
            hex_val = color[1]
            self._av_color_vars[idx].set(hex_val)
            self._av_color_swatches[idx].configure(
                fg_color=hex_val, hover_color=hex_val
            )

    def _av_on_color_entry(self, idx: int):
        """Validate and update swatch when hex entry is changed."""
        val = self._av_color_vars[idx].get().strip()
        if not val.startswith("#"):
            val = "#" + val
        if len(val) == 7:
            try:
                hex_to_rgb(val)
                self._av_color_vars[idx].set(val)
                self._av_color_swatches[idx].configure(
                    fg_color=val, hover_color=val
                )
            except Exception:
                pass

    def _av_generate_colors(self):
        """Generate harmonious color palette."""
        harmony = self.av_harmony_var.get().lower().replace(" ", "_")
        if harmony not in HARMONY_TYPES:
            harmony = "random"
        colors = generate_harmony_colors(harmony)
        for i, c in enumerate(colors[:4]):
            self._av_color_vars[i].set(c)
            self._av_color_swatches[i].configure(
                fg_color=c, hover_color=c
            )

    def _av_get_colors(self) -> list:
        """Get current 4 hex colors."""
        return [v.get() for v in self._av_color_vars]

    # ═══════════════════════════════════════════════════════════════════════════
    # PATTERN/OVERLAY CHANGE
    # ═══════════════════════════════════════════════════════════════════════════

    def _av_on_pattern_change(self, *args):
        display = self.av_pattern_var.get()
        for key, info in BACKGROUND_PATTERNS.items():
            full = f"{info['icon']} {info['name']}"
            if full == display:
                self._av_pattern_desc.configure(text=info["desc"])
                break

    def _av_on_overlay_change(self, *args):
        display = self.av_overlay_var.get()
        for key, info in OVERLAY_EFFECTS.items():
            full = f"{info['icon']} {info['name']}"
            if full == display:
                self._av_overlay_desc.configure(text=info["desc"])
                break

    def _av_get_pattern_key(self) -> str:
        display = self.av_pattern_var.get()
        for key, info in BACKGROUND_PATTERNS.items():
            full = f"{info['icon']} {info['name']}"
            if full == display:
                return key
        return "gradient_flow"

    def _av_get_overlay_key(self) -> str:
        display = self.av_overlay_var.get()
        for key, info in OVERLAY_EFFECTS.items():
            full = f"{info['icon']} {info['name']}"
            if full == display:
                return key
        return "none"

    # ═══════════════════════════════════════════════════════════════════════════
    # OUTPUT PATH
    # ═══════════════════════════════════════════════════════════════════════════

    def _av_change_output(self):
        folder = filedialog.askdirectory(title="Select Output Folder", initialdir=self._av_output_path)
        if folder:
            self._av_output_path = folder
            self._av_update_path_label()
            # Persist the chosen path
            if _HAS_DB:
                db.save_setting("av_output_path", folder)

    def _av_update_path_label(self):
        if hasattr(self, "_av_path_label"):
            p = self._av_output_path
            d = p if len(p) <= 35 else "..." + p[-32:]
            self._av_path_label.configure(text=d)

    # ═══════════════════════════════════════════════════════════════════════════
    # LIVE PREVIEW
    # ═══════════════════════════════════════════════════════════════════════════

    def _av_start_preview_loop(self):
        """Start the preview animation loop."""
        self._av_preview_running = True
        self._av_preview_time = 0.0
        self._av_update_preview()

    def _av_update_preview(self):
        """Update preview frame at ~24 fps with fixed render resolution."""
        if not self._av_preview_running:
            return

        # Only render if on this page
        if hasattr(self, '_current_page') and self._current_page != "abstract_video":
            self.after(500, self._av_update_preview)
            return

        try:
            pattern = self._av_get_pattern_key()
            overlay = self._av_get_overlay_key()
            colors = self._av_get_colors()

            # Validate colors
            valid_colors = []
            for c in colors:
                try:
                    hex_to_rgb(c)
                    valid_colors.append(c)
                except Exception:
                    valid_colors.append("#0066ff")

            # Higher render resolution for sharp, clear preview
            render_w, render_h = 960, 540

            # Display size from widget
            display_w = self._av_preview_label.winfo_width()
            display_h = self._av_preview_label.winfo_height()
            if display_w < 100 or display_h < 100:
                display_w, display_h = 960, 540

            # Speed multiplier
            speed_str = self.av_speed_var.get() if hasattr(self, 'av_speed_var') else "1.0x"
            speed = float(speed_str.replace("x", ""))

            t_start = time.time()

            # Render at 960x540 for clear preview
            frame = self._av_generator.generate_preview_frame(
                pattern, overlay, valid_colors,
                (render_w, render_h), self._av_preview_time
            )

            # NOTE: Smoothing is NOT applied in preview — only in final video
            # This keeps patterns and overlays sharp and clear

            # Frame is already RGB (colors from hex_to_rgb are RGB)
            pil_img = Image.fromarray(frame)
            if (display_w, display_h) != (render_w, render_h):
                pil_img = pil_img.resize((display_w, display_h), Image.LANCZOS)

            # Convert to PhotoImage
            photo = ImageTk.PhotoImage(pil_img)
            self._av_preview_photo = photo  # Keep reference
            self._av_preview_label.configure(image=photo, text="")

            elapsed = time.time() - t_start
            fps_display = 1.0 / elapsed if elapsed > 0 else 0
            self._av_preview_fps_label.configure(text=f"{fps_display:.0f} preview fps")

            self._av_preview_time += (1.0 / 24.0) * speed

            # Adaptive scheduling: target ~24fps, min 16ms delay
            target_interval = 1000 // 24  # ~42ms
            render_ms = int(elapsed * 1000)
            delay = max(16, target_interval - render_ms)

        except Exception as e:
            logger.debug(f"Preview error: {e}")
            delay = 66

        # Schedule next frame
        self.after(delay, self._av_update_preview)

    # ═══════════════════════════════════════════════════════════════════════════
    # GENERATE VIDEO
    # ═══════════════════════════════════════════════════════════════════════════

    def _av_start_generate(self):
        """Start video generation."""
        if self._av_generator.is_generating:
            messagebox.showwarning("Busy", "Already generating a video. Please wait or stop.")
            return

        pattern = self._av_get_pattern_key()
        overlay = self._av_get_overlay_key()
        colors = self._av_get_colors()

        # Validate colors
        for i, c in enumerate(colors):
            try:
                hex_to_rgb(c)
            except Exception:
                messagebox.showerror("Invalid Color", f"Color {i+1} '{c}' is not a valid hex color.")
                return

        # Resolution
        res_name = self.av_resolution_var.get()
        resolution = RESOLUTIONS.get(res_name, (1920, 1080))

        # FPS
        fps = int(self.av_fps_var.get())

        # Duration
        custom_dur = self._av_custom_dur_entry.get().strip()
        if custom_dur:
            try:
                duration = int(custom_dur)
                if duration < 1 or duration > 300:
                    messagebox.showerror("Invalid Duration", "Duration must be 1-300 seconds.")
                    return
            except ValueError:
                messagebox.showerror("Invalid Duration", "Please enter a valid number.")
                return
        else:
            duration = int(self.av_duration_var.get())

        # Format (bitrate fixed at 50 Mbps for maximum quality)
        fmt = self.av_format_var.get().lower()
        bitrate = 50

        # Speed
        speed_str = self.av_speed_var.get()
        speed = float(speed_str.replace("x", ""))

        # Generate filename
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"abstract_{pattern}_{timestamp}.{fmt}"
        output_path = os.path.join(self._av_output_path, filename)

        # UI state
        self._av_generate_btn.pack_forget()
        self._av_stop_btn.pack(fill="x", pady=(0, 4))
        self._av_progress_status.configure(
            text="Starting generation...", text_color=COLORS["neon_blue"]
        )
        self._av_progress_bar.set(0)
        self._av_progress_pct.configure(text="0%")

        # Callbacks
        def on_progress(pct, text):
            self.after(0, lambda: self._av_on_progress(pct, text))

        def on_done(success, message):
            self.after(0, lambda: self._av_on_done(success, message))

        self._av_generator.generate_video(
            output_path=output_path,
            pattern=pattern,
            overlay=overlay,
            colors=colors,
            resolution=resolution,
            fps=fps,
            duration=duration,
            output_format=fmt,
            bitrate=bitrate,
            progress_callback=on_progress,
            done_callback=on_done,
        )

    def _av_stop_generate(self):
        """Stop video generation."""
        self._av_generator.stop()
        self._av_progress_status.configure(
            text="Stopping...", text_color=COLORS["warning"]
        )

    def _av_on_progress(self, pct: float, text: str):
        """Update progress UI from callback."""
        self._av_progress_bar.set(pct)
        self._av_progress_pct.configure(text=f"{int(pct * 100)}%")
        self._av_progress_status.configure(text=text, text_color=COLORS["neon_blue"])

    def _av_on_done(self, success: bool, message: str):
        """Handle generation completion — also triggers next batch item."""
        # If batch is running, chain to next video
        if self._av_batch_running:
            self._av_batch_on_video_done(success, message)
            return

        self._av_stop_btn.pack_forget()
        self._av_generate_btn.pack(fill="x", pady=(0, 4))

        if success:
            self._av_progress_bar.set(1.0)
            self._av_progress_pct.configure(text="100%")
            self._av_progress_status.configure(
                text=f"✅ {message}", text_color=COLORS["success"]
            )
            messagebox.showinfo("Success", f"Video generated successfully!\n\n{message}")
        else:
            self._av_progress_status.configure(
                text=f"❌ {message}", text_color=COLORS["error"]
            )
            if "cancelled" not in message.lower():
                messagebox.showerror("Error", message)

    # ═══════════════════════════════════════════════════════════════════════════
    # BATCH GENERATE
    # ═══════════════════════════════════════════════════════════════════════════

    def _av_start_batch(self):
        """Start batch generation with random pattern/overlay/colors per video."""
        import random

        if self._av_generator.is_generating or self._av_batch_running:
            messagebox.showwarning("Busy", "Already generating. Please wait or stop.")
            return

        # Parse batch count
        try:
            count = int(self._av_batch_count.get().strip())
            if count < 1 or count > 100:
                messagebox.showerror("Invalid Count", "Batch count must be 1-100.")
                return
        except ValueError:
            messagebox.showerror("Invalid Count", "Please enter a valid number.")
            return

        # Read current sidebar settings (shared across batch)
        res_name = self.av_resolution_var.get()
        resolution = RESOLUTIONS.get(res_name, (1920, 1080))
        fps = int(self.av_fps_var.get())

        custom_dur = self._av_custom_dur_entry.get().strip()
        if custom_dur:
            try:
                duration = int(custom_dur)
                if duration < 1 or duration > 300:
                    messagebox.showerror("Invalid Duration", "Duration must be 1-300 seconds.")
                    return
            except ValueError:
                messagebox.showerror("Invalid Duration", "Please enter a valid number.")
                return
        else:
            duration = int(self.av_duration_var.get())

        fmt = self.av_format_var.get().lower()
        speed_str = self.av_speed_var.get()

        # Build batch queue with balanced pattern distribution
        # Every pattern appears equally before any repeats
        pattern_keys = list(BACKGROUND_PATTERNS.keys())
        use_overlay = self._av_batch_overlay_var.get()
        if use_overlay:
            overlay_keys = list(OVERLAY_EFFECTS.keys())
            overlay_keys_visual = [k for k in overlay_keys if k != "none"]
            if not overlay_keys_visual:
                overlay_keys_visual = ["none"]
        else:
            overlay_keys_visual = ["none"]

        # Balanced rotation: shuffle all patterns, cycle through evenly
        shuffled_patterns = pattern_keys[:]
        random.shuffle(shuffled_patterns)
        shuffled_overlays = overlay_keys_visual[:]
        random.shuffle(shuffled_overlays)

        queue = []

        for i in range(count):
            # Pick pattern from shuffled list (re-shuffle when exhausted)
            pat_idx = i % len(shuffled_patterns)
            if pat_idx == 0 and i > 0:
                random.shuffle(shuffled_patterns)
            pat = shuffled_patterns[pat_idx]

            # Pick overlay from shuffled list (re-shuffle when exhausted)
            ovl_idx = i % len(shuffled_overlays)
            if ovl_idx == 0 and i > 0:
                random.shuffle(shuffled_overlays)
            ovl = shuffled_overlays[ovl_idx]

            # Generate harmonious color palette
            harmony_types = [
                "analogous", "complementary", "triadic",
                "split_complementary", "tetradic", "warm",
                "cool", "pastel", "neon", "dark_rich", "random"
            ]
            harmony = random.choice(harmony_types)
            colors = generate_harmony_colors(harmony)

            # Build job info
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            pat_name = BACKGROUND_PATTERNS[pat]["name"].lower().replace(" ", "_")
            filename = f"abstract_{pat_name}_{i+1:03d}_{timestamp}.{fmt}"
            output_path = os.path.join(self._av_output_path, filename)

            queue.append({
                "pattern": pat,
                "overlay": ovl,
                "colors": colors,
                "resolution": resolution,
                "fps": fps,
                "duration": duration,
                "output_format": fmt,
                "bitrate": 50,
                "output_path": output_path,
                "pattern_name": BACKGROUND_PATTERNS[pat]["name"],
                "overlay_name": OVERLAY_EFFECTS[ovl]["name"],
            })

        # Store batch state
        self._av_batch_queue = queue
        self._av_batch_index = 0
        self._av_batch_total = count
        self._av_batch_running = True
        self._av_batch_success_count = 0

        # UI: switch buttons
        self._av_generate_btn.pack_forget()
        self._av_batch_btn.pack_forget()
        self._av_batch_stop_btn.pack(fill="x", pady=(0, 4))

        # Start first video
        self._av_batch_generate_next()

    def _av_batch_generate_next(self):
        """Generate the next video in the batch queue."""
        if not self._av_batch_running:
            return

        idx = self._av_batch_index
        total = self._av_batch_total

        if idx >= total:
            # All done!
            self._av_batch_finish()
            return

        job = self._av_batch_queue[idx]

        # Update progress
        self._av_progress_status.configure(
            text=f"[{idx+1}/{total}] {job['pattern_name']} + {job['overlay_name']}",
            text_color=COLORS["neon_blue"]
        )
        self._av_progress_bar.set(0)
        self._av_progress_pct.configure(text=f"{idx+1}/{total}")

        # Callbacks with batch prefix
        def on_progress(pct, text):
            batch_text = f"[{idx+1}/{total}] {text}"
            self.after(0, lambda: self._av_on_progress(pct, batch_text))

        def on_done(success, message):
            self.after(0, lambda: self._av_on_done(success, message))

        self._av_generator.generate_video(
            output_path=job["output_path"],
            pattern=job["pattern"],
            overlay=job["overlay"],
            colors=job["colors"],
            resolution=job["resolution"],
            fps=job["fps"],
            duration=job["duration"],
            output_format=job["output_format"],
            bitrate=job["bitrate"],
            progress_callback=on_progress,
            done_callback=on_done,
        )

    def _av_batch_on_video_done(self, success: bool, message: str):
        """Handle completion of one video in the batch, trigger next."""
        if success:
            self._av_batch_success_count += 1

        self._av_batch_index += 1

        if not self._av_batch_running:
            self._av_batch_finish()
            return

        if self._av_batch_index < self._av_batch_total:
            # Small delay before starting next to let FFmpeg release resources
            self.after(500, self._av_batch_generate_next)
        else:
            self._av_batch_finish()

    def _av_batch_finish(self):
        """Finalize batch generation."""
        self._av_batch_running = False

        # Restore buttons
        self._av_batch_stop_btn.pack_forget()
        self._av_generate_btn.pack(fill="x", pady=(0, 4))
        self._av_batch_btn.pack(fill="x", pady=(0, 4))

        total = self._av_batch_total
        ok = self._av_batch_success_count

        self._av_progress_bar.set(1.0)
        self._av_progress_pct.configure(text="Done")
        self._av_progress_status.configure(
            text=f"✅ Batch complete! {ok}/{total} videos generated",
            text_color=COLORS["success"]
        )

        messagebox.showinfo(
            "Batch Complete",
            f"Successfully generated {ok} of {total} videos!\n\n"
            f"Saved to: {self._av_output_path}"
        )

    def _av_stop_batch(self):
        """Stop batch generation after current video finishes."""
        self._av_batch_running = False
        self._av_generator.stop()
        self._av_progress_status.configure(
            text=f"Stopping batch after current video...",
            text_color=COLORS["warning"]
        )
