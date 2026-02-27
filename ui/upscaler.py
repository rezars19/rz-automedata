"""
RZ Automedata — Media Upscaler UI
Supports Video + Image upscaling via Google Colab GPU or Local GPU/CPU.

Modes:
    - Colab: Google Drive communication (no ngrok)
    - Local: realesrgan-ncnn-vulkan binary (auto GPU/CPU detection)

Queue uses ttk.Treeview for maximum performance — handles thousands of files
without lag or excessive memory usage.
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import os
import webbrowser
import logging

logger = logging.getLogger(__name__)

from ui.theme import COLORS
from core.upscaler_client import UpscalerClient, MODELS, DEFAULT_MODEL
from core.gdrive_api import GDriveAPI, HAS_GDRIVE_API
from core.local_upscaler import LocalUpscaler, is_video, is_image, VIDEO_EXTS, IMAGE_EXTS

try:
    import core.database as db
    HAS_DB = True
except ImportError:
    HAS_DB = False

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False


# ─── Visual Constants ─────────────────────────────────────────────────────────
_C = {
    "sidebar_bg":       "#080c22",
    "card_idle":        "#0d1335",
    "card_active":      "#111a48",
    "card_success":     "#081a14",
    "card_error":       "#1a0a0e",
    "header_bg":        "#0b1030",
    "header_border":    "#1a2555",
    "divider":          "#1a2555",
    "log_bg":           "#060a1a",
    "log_border":       "#1a2555",
    # Stage colors
    "stage_copy":       "#3b82f6",
    "stage_sync":       "#8b5cf6",
    "stage_process":    "#a855f7",
    "stage_merge":      "#f59e0b",
    "stage_save":       "#06b6d4",
    "stage_done":       "#00ff88",
    "stage_error":      "#ff4466",
    # Pill backgrounds
    "pill_queued_bg":   "#1e2d6a",
    "pill_queued_fg":   "#8890b5",
    "pill_active_bg":   "#1a0d3a",
    "pill_active_fg":   "#a855f7",
    "pill_done_bg":     "#0a2a1a",
    "pill_done_fg":     "#00ff88",
    "pill_error_bg":    "#2a0a0e",
    "pill_error_fg":    "#ff4466",
}


class UpscalerMixin:
    """Mixin for Media Upscaler page — Colab (Google Drive) or Local GPU/CPU."""

    # ═══════════════════════════════════════════════════════════════════════════
    # PAGE BUILDER
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_upscaler_page(self, parent):
        self.upscaler_page_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.upscaler_page_frame.grid_columnconfigure(0, weight=0, minsize=300)
        self.upscaler_page_frame.grid_columnconfigure(1, weight=1)
        self.upscaler_page_frame.grid_rowconfigure(0, weight=1)

        # ── Left sidebar (compact) ──
        sidebar = ctk.CTkFrame(
            self.upscaler_page_frame, fg_color=_C["sidebar_bg"],
            width=300, corner_radius=0
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
        self._build_upscaler_sidebar(sidebar_inner)

        # ── Right content (queue via Treeview + detail panel) ──
        content = ctk.CTkFrame(self.upscaler_page_frame, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_rowconfigure(0, weight=0)   # status bar
        content.grid_rowconfigure(1, weight=0)   # queue header
        content.grid_rowconfigure(2, weight=1)   # treeview queue
        content.grid_rowconfigure(3, weight=0)   # detail panel
        content.grid_columnconfigure(0, weight=1)

        self._build_upscaler_status_bar(content)
        self._build_upscaler_queue(content)
        self._build_detail_panel(content)

        # ── State ──
        self.gdrive_bridge = GDriveAPI()
        self.upscaler_client = UpscalerClient()
        self.local_upscaler = LocalUpscaler()
        self.upscaler_tasks = {}      # task_key -> task dict
        self._task_order = []         # ordered list of task keys
        self._upscaler_batch_running = False
        self._upscaler_stop_event = threading.Event()  # Stop signal for all tasks

        # Use Documents folder for output (safe even if app is in C:\Program Files)
        try:
            docs_path = os.path.join(os.path.expanduser("~"), "Documents", "RZ Studio", "Output", "Upscaled")
            os.makedirs(docs_path, exist_ok=True)
            self.upscale_output_path = docs_path
        except Exception:
            # Fallback to app directory if Documents is unavailable
            self.upscale_output_path = os.path.join(os.getcwd(), "Output", "Upscaled")
            try:
                os.makedirs(self.upscale_output_path, exist_ok=True)
            except Exception:
                self.upscale_output_path = os.path.join(os.path.expanduser("~"), "Desktop")
        self._load_upscaler_settings()
        # Auto-detect GPU after UI is ready
        self.after(500, self._detect_local_gpu)
        # Auto-connect Google Drive if token exists (no manual login needed)
        self.after(1000, self._auto_connect_gdrive)

    # ═══════════════════════════════════════════════════════════════════════════
    # SIDEBAR (Compact — minimal scroll)
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_upscaler_sidebar(self, parent):
        # ── Title ──
        ctk.CTkLabel(
            parent, text="⚡ Media Upscaler",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=COLORS["neon_blue"]
        ).pack(pady=(0, 1), anchor="w")
        self._upscaler_subtitle = ctk.CTkLabel(
            parent, text="Real-ESRGAN • Colab GPU",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"]
        )
        self._upscaler_subtitle.pack(pady=(0, 10), anchor="w")

        # ── PROCESSING MODE ──
        self._lbl(parent, "⚙️  Processing Mode")
        self.processing_mode_var = ctk.StringVar(value="colab")
        mode_seg = ctk.CTkSegmentedButton(
            parent, values=["☁️ Colab", "💻 Local"],
            variable=None,
            selected_color=COLORS["accent_purple"],
            selected_hover_color="#9b4dff",
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), height=32, corner_radius=6,
            command=self._on_mode_change
        )
        mode_seg.set("☁️ Colab")
        mode_seg.pack(fill="x", pady=(0, 4))
        self._mode_seg = mode_seg

        # GPU info label (shown in local mode)
        self._gpu_info_label = ctk.CTkLabel(
            parent, text="",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"]
        )
        self._gpu_info_label.pack(pady=(0, 4), anchor="w")

        # Auto-detected device mode (no manual selector — fully automatic)
        self._auto_use_gpu = False  # Will be set by _detect_local_gpu

        # Download engine button (shown in local mode when not installed)
        self._download_engine_btn = ctk.CTkButton(
            parent, text="📥 Download Engine (~15 MB)",
            height=28, corner_radius=6,
            command=self._download_local_engine,
            fg_color="#1a3a1a", hover_color="#2a5a2a",
            text_color="#00ff88",
            border_width=1, border_color="#2a5a2a",
            font=ctk.CTkFont(size=10, weight="bold")
        )
        # NOT packed yet — shown only when needed

        # Colab setup guide frame
        self._colab_guide_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self._colab_guide_frame.pack(fill="x")

        guide = ctk.CTkFrame(self._colab_guide_frame, fg_color=COLORS["bg_card"], corner_radius=8,
                              border_width=1, border_color=COLORS["border"])
        guide.pack(fill="x", pady=(4, 0))
        gi = ctk.CTkFrame(guide, fg_color="transparent")
        gi.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(
            gi, text="📋  Quick Setup",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=COLORS["text_primary"]
        ).pack(anchor="w")
        ctk.CTkLabel(
            gi,
            text="1. Login Google (below)\n"
                 "2. Run the Colab notebook\n"
                 "3. Add files & Start!",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"], justify="left"
        ).pack(anchor="w", pady=(2, 4))
        ctk.CTkButton(
            gi, text="Open Colab ↗", height=24, corner_radius=5,
            command=lambda: webbrowser.open("https://colab.research.google.com/"),
            fg_color="transparent", hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["neon_blue"], font=ctk.CTkFont(size=10, weight="bold"),
            border_width=1, border_color=COLORS["neon_blue_dim"]
        ).pack(anchor="w")

        self._sep(parent)

        # ── ADD FILES (video + image) ──
        ctk.CTkButton(
            parent, text="📂  Browse Files",
            command=self._browse_upscale_files,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
            height=40, corner_radius=8
        ).pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(
            parent, text="Video: MP4 MOV AVI MKV  •  Image: PNG JPG WEBP",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"]
        ).pack(pady=(0, 8), anchor="center")

        self._sep(parent)

        # ── GOOGLE DRIVE API (only shown in Colab mode) ──
        self._gdrive_section = ctk.CTkFrame(parent, fg_color="transparent")
        self._gdrive_section.pack(fill="x")

        self._lbl(self._gdrive_section, "☁️  Google Drive")
        self._gdrive_path_label = ctk.CTkLabel(
            self._gdrive_section, text="Not logged in",
            text_color=COLORS["error"], font=ctk.CTkFont(size=10),
            wraplength=250, justify="left"
        )
        self._gdrive_path_label.pack(pady=(0, 6), anchor="w")

        # Login / Logout buttons (credentials are built-in, no manual input needed)
        dr = ctk.CTkFrame(self._gdrive_section, fg_color="transparent")
        dr.pack(fill="x", pady=(0, 8))
        self._gdrive_login_btn = ctk.CTkButton(
            dr, text="🔑 Login Google", height=32, corner_radius=6,
            command=self._login_gdrive_api,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self._gdrive_login_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self._gdrive_logout_btn = ctk.CTkButton(
            dr, text="↩ Logout", height=32, corner_radius=6,
            command=self._logout_gdrive_api,
            fg_color=COLORS["bg_input"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_muted"],
            border_width=1, border_color=COLORS["border"],
            font=ctk.CTkFont(size=10)
        )
        # Logout button packed conditionally later

        self._sep(parent)

        # ── MODEL + SCALE (same section) ──
        self._lbl(parent, "🤖  AI Model")
        model_keys = list(MODELS.keys())
        model_names = [MODELS[k]["name"] for k in model_keys]
        self.upscaler_model_var = ctk.StringVar(value=MODELS[DEFAULT_MODEL]["name"])
        self._model_keys = model_keys
        self._model_names = model_names

        ctk.CTkOptionMenu(
            parent, values=model_names, variable=self.upscaler_model_var,
            fg_color=COLORS["bg_input"], button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["neon_blue"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11), dropdown_font=ctk.CTkFont(size=11),
            height=32, corner_radius=6
        ).pack(fill="x", pady=(0, 8))
        self.upscaler_model_var.trace_add("write", self._on_model_change)

        self._lbl(parent, "Scale")
        self.scale_var = ctk.StringVar(value="x4")
        self._scale_seg = ctk.CTkSegmentedButton(
            parent, values=["x2", "x3", "x4"], variable=self.scale_var,
            selected_color=COLORS["accent_blue"],
            selected_hover_color=COLORS["neon_blue"],
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), height=30, corner_radius=6
        )
        self._scale_seg.pack(fill="x", pady=(0, 8))

        self._sep(parent)

        # ── OPTIONS (compact) ──
        self._lbl(parent, "Options")
        self.face_enhance_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            parent, text="Face Enhance (GFPGAN)", variable=self.face_enhance_var,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            checkmark_color="white", text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=11), corner_radius=4, height=22, border_width=2
        ).pack(pady=(0, 4), anchor="w")

        self.mute_audio_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            parent, text="Mute Audio", variable=self.mute_audio_var,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            checkmark_color="white", text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=11), corner_radius=4, height=22, border_width=2
        ).pack(pady=(0, 8), anchor="w")

        self._sep(parent)

        # ── OUTPUT FORMAT ──
        self._lbl(parent, "Output Format")
        self.output_format_var = ctk.StringVar(value="MP4")
        ctk.CTkSegmentedButton(
            parent, values=["MP4", "MOV"], variable=self.output_format_var,
            selected_color=COLORS["accent_purple"],
            selected_hover_color="#9b4dff",
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), height=30, corner_radius=6
        ).pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(
            parent, text="Both use H.264 @ 50 Mbps",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"]
        ).pack(pady=(0, 8), anchor="w")

        # ── OUTPUT FPS ──
        self._lbl(parent, "🎬  Output FPS")
        self.output_fps_var = ctk.StringVar(value="Original")
        ctk.CTkSegmentedButton(
            parent, values=["Original", "30 FPS", "60 FPS"], variable=self.output_fps_var,
            selected_color=COLORS["accent_blue"],
            selected_hover_color=COLORS["neon_blue"],
            unselected_color=COLORS["bg_input"],
            unselected_hover_color=COLORS["bg_card_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), height=30, corner_radius=6
        ).pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(
            parent, text="Original = keep source FPS • 60 = smooth interpolation",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"]
        ).pack(pady=(0, 8), anchor="w")

        # ── SAVE LOCATION ──
        self._lbl(parent, "Save Location")
        self._output_path_label = ctk.CTkLabel(
            parent, text="...", text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=10), wraplength=250, justify="left"
        )
        self._output_path_label.pack(pady=(0, 4), anchor="w")

        or_ = ctk.CTkFrame(parent, fg_color="transparent")
        or_.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(
            or_, text="📁 Change", height=28, corner_radius=6,
            command=self._change_output_folder,
            fg_color=COLORS["bg_input"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            border_width=1, border_color=COLORS["border"],
            font=ctk.CTkFont(size=11)
        ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(
            or_, text="📂 Open", height=28, corner_radius=6,
            command=lambda: webbrowser.open(self.upscale_output_path),
            fg_color=COLORS["bg_input"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            border_width=1, border_color=COLORS["border"],
            font=ctk.CTkFont(size=11)
        ).pack(side="left", expand=True, fill="x")

        self._sep(parent)

        # ── START / STOP ALL ──
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x")

        self.start_all_btn = ctk.CTkButton(
            btn_row, text="🚀  Start All Upscale",
            command=self._start_all_tasks,
            fg_color="#00c96a", hover_color="#00a554",
            text_color="white", font=ctk.CTkFont(size=14, weight="bold"),
            height=44, corner_radius=8
        )
        self.start_all_btn.pack(fill="x")

        self.stop_all_btn = ctk.CTkButton(
            btn_row, text="⏹  Stop All",
            command=self._stop_all_tasks,
            fg_color="#cc3355", hover_color="#aa2244",
            text_color="white", font=ctk.CTkFont(size=14, weight="bold"),
            height=44, corner_radius=8
        )
        # Stop button NOT packed yet — shown only when running

        self.retry_all_btn = ctk.CTkButton(
            btn_row, text="🔄  Retry All Failed",
            command=self._retry_all_failed,
            fg_color="#cc8800", hover_color="#aa7700",
            text_color="white", font=ctk.CTkFont(size=12, weight="bold"),
            height=36, corner_radius=8
        )
        # Retry button NOT packed yet — shown only after failures

    def _on_model_change(self, *args):
        selected = self.upscaler_model_var.get()
        for key, info in MODELS.items():
            if info["name"] == selected:
                scales = info["scales"]
                vals = [f"x{s}" for s in scales]
                self._scale_seg.configure(values=vals)
                if self.scale_var.get() not in vals:
                    self.scale_var.set(vals[-1])
                break

    def _get_selected_model_key(self):
        selected = self.upscaler_model_var.get()
        for key, info in MODELS.items():
            if info["name"] == selected:
                return key
        return DEFAULT_MODEL

    # ═══════════════════════════════════════════════════════════════════════════
    # STATUS BAR
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_upscaler_status_bar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color=_C["header_bg"], height=44, corner_radius=0)
        bar.grid(row=0, column=0, sticky="ew")
        bar.pack_propagate(False)
        ctk.CTkFrame(bar, fg_color=_C["header_border"], height=1, corner_radius=0).pack(side="bottom", fill="x")

        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16)

        sf = ctk.CTkFrame(inner, fg_color="transparent")
        sf.pack(side="left")
        self._up_status_dot = ctk.CTkLabel(sf, text="●", text_color=COLORS["warning"], font=ctk.CTkFont(size=16))
        self._up_status_dot.pack(side="left", padx=(0, 4))
        self._up_status_text = ctk.CTkLabel(sf, text="Drive not set", font=ctk.CTkFont(size=11, weight="bold"), text_color=COLORS["warning"])
        self._up_status_text.pack(side="left")

        self._drive_info_label = ctk.CTkLabel(
            inner, text="Set Drive folder in sidebar",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"]
        )
        self._drive_info_label.pack(side="left", padx=(12, 0))

    def _update_drive_status(self):
        if not hasattr(self, "_up_status_dot"):
            return
        if self.gdrive_bridge.is_configured:
            self._up_status_dot.configure(text_color=COLORS["success"])
            self._up_status_text.configure(text="Drive Ready", text_color=COLORS["success"])
            self._drive_info_label.configure(text="Run Colab, add videos & click Start")
            # Update login button state
            if hasattr(self, "_gdrive_login_btn"):
                self._gdrive_login_btn.configure(text="✅ Connected", state="disabled",
                                                  fg_color="#0a2a1a")
                self._gdrive_logout_btn.pack(side="left", expand=True, fill="x")
        else:
            self._up_status_dot.configure(text_color=COLORS["warning"])
            self._up_status_text.configure(text="Not logged in", text_color=COLORS["warning"])
            self._drive_info_label.configure(text="Click 'Login Google' in sidebar")
            if hasattr(self, "_gdrive_login_btn"):
                self._gdrive_login_btn.configure(text="🔑 Login Google", state="normal",
                                                  fg_color=COLORS["accent_blue"])
                self._gdrive_logout_btn.pack_forget()

    # ═══════════════════════════════════════════════════════════════════════════
    # FILE QUEUE — Treeview-based (handles thousands of files)
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_upscaler_queue(self, parent):
        # ── Header row ──
        qh = ctk.CTkFrame(parent, fg_color="transparent")
        qh.grid(row=1, column=0, sticky="ew", padx=16, pady=(10, 4))

        self._queue_title = ctk.CTkLabel(
            qh, text="File Queue", font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        )
        self._queue_title.pack(side="left")

        self._queue_badge = ctk.CTkLabel(
            qh, text="0", font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["text_muted"], fg_color=COLORS["bg_input"],
            corner_radius=8, width=24, height=20
        )
        self._queue_badge.pack(side="left", padx=(6, 0))

        ctk.CTkButton(
            qh, text="✕ Clear All", width=90, height=24,
            fg_color="transparent", text_color=COLORS["error"],
            hover_color="#1a0a0e",
            border_width=1, border_color=COLORS["error"],
            corner_radius=5, font=ctk.CTkFont(size=10),
            command=self._clear_all_tasks
        ).pack(side="right")

        ctk.CTkButton(
            qh, text="✕ Clear Done", width=90, height=24,
            fg_color="transparent", text_color=COLORS["text_secondary"],
            hover_color=COLORS["bg_input"],
            border_width=1, border_color=COLORS["border"],
            corner_radius=5, font=ctk.CTkFont(size=10),
            command=self._clear_finished_tasks
        ).pack(side="right", padx=(0, 6))

        # ── Treeview with scrollbar ──
        tree_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_dark"],
                                   corner_radius=0)
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 0))
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Configure Treeview style matching the dark neon theme
        style = ttk.Style()
        style.configure("Upscaler.Treeview",
            background=COLORS["bg_dark"],
            foreground=COLORS["text_primary"],
            fieldbackground=COLORS["bg_dark"],
            rowheight=32,
            font=("Segoe UI", 10),
            borderwidth=0,
        )
        style.configure("Upscaler.Treeview.Heading",
            background=COLORS["table_header"],
            foreground=COLORS["text_primary"],
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
            relief="flat",
        )
        style.map("Upscaler.Treeview",
            background=[("selected", COLORS["accent_blue"])],
            foreground=[("selected", "#ffffff")],
        )
        style.map("Upscaler.Treeview.Heading",
            background=[("active", COLORS["bg_card_hover"])],
        )

        cols = ("type", "filename", "size", "status", "progress", "detail")
        self._up_tree = ttk.Treeview(
            tree_frame, columns=cols, show="headings",
            style="Upscaler.Treeview", selectmode="extended"
        )

        self._up_tree.heading("type",     text="Type",   anchor="center")
        self._up_tree.heading("filename", text="File",   anchor="w")
        self._up_tree.heading("size",     text="Size",   anchor="center")
        self._up_tree.heading("status",   text="Status", anchor="center")
        self._up_tree.heading("progress", text="%",      anchor="center")
        self._up_tree.heading("detail",   text="Detail", anchor="w")

        self._up_tree.column("type",     width=50,  minwidth=40,  stretch=False, anchor="center")
        self._up_tree.column("filename", width=250, minwidth=120, stretch=True,  anchor="w")
        self._up_tree.column("size",     width=80,  minwidth=60,  stretch=False, anchor="center")
        self._up_tree.column("status",   width=100, minwidth=80,  stretch=False, anchor="center")
        self._up_tree.column("progress", width=55,  minwidth=45,  stretch=False, anchor="center")
        self._up_tree.column("detail",   width=200, minwidth=100, stretch=True,  anchor="w")

        # Row tags for status coloring
        self._up_tree.tag_configure("queued",
            background=COLORS["table_row_even"], foreground=COLORS["text_secondary"])
        self._up_tree.tag_configure("active",
            background="#111a48", foreground="#a855f7")
        self._up_tree.tag_configure("done",
            background="#081a14", foreground="#00ff88")
        self._up_tree.tag_configure("error",
            background="#1a0a0e", foreground="#ff4466")
        self._up_tree.tag_configure("stopped",
            background="#1a1400", foreground="#ffaa33")
        self._up_tree.tag_configure("even",
            background=COLORS["table_row_even"])
        self._up_tree.tag_configure("odd",
            background=COLORS["table_row_odd"])

        # Scrollbar
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._up_tree.yview)
        self._up_tree.configure(yscrollcommand=vsb.set)
        self._up_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # Bind events
        self._up_tree.bind("<<TreeviewSelect>>", self._on_queue_select)
        self._up_tree.bind("<Double-1>", self._on_queue_double_click)
        self._up_tree.bind("<Button-3>", self._on_queue_right_click)

        # Drag-and-drop
        if HAS_DND:
            self._up_tree.drop_target_register(DND_FILES)
            self._up_tree.dnd_bind("<<Drop>>", self._on_upscale_drop)

    def _build_detail_panel(self, parent):
        """Bottom panel showing progress bar for the selected / active task."""
        panel = ctk.CTkFrame(parent, fg_color=_C["header_bg"], height=56,
                              corner_radius=0)
        panel.grid(row=3, column=0, sticky="ew")
        panel.pack_propagate(False)
        ctk.CTkFrame(panel, fg_color=_C["header_border"],
                      height=1, corner_radius=0).pack(side="top", fill="x")

        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=6)

        self._detail_status = ctk.CTkLabel(
            inner, text="No file selected",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text_secondary"]
        )
        self._detail_status.pack(side="left", padx=(0, 12))

        self._detail_bar = ctk.CTkProgressBar(
            inner, height=10,
            progress_color=_C["stage_process"], fg_color=COLORS["bg_input"],
            corner_radius=5, width=300
        )
        self._detail_bar.set(0)
        self._detail_bar.pack(side="left", fill="x", expand=True, padx=(0, 12))

        self._detail_pct = ctk.CTkLabel(
            inner, text="", width=50,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"]
        )
        self._detail_pct.pack(side="left")

        # Right-click context menu
        self._queue_menu = tk.Menu(self, tearoff=0,
            bg=COLORS["bg_card"], fg=COLORS["text_primary"],
            activebackground=COLORS["accent_blue"], activeforeground="white",
            font=("Segoe UI", 10), relief="flat", borderwidth=1
        )
        self._queue_menu.add_command(label="▶ Start",        command=self._ctx_start_task)
        self._queue_menu.add_command(label="⏹ Stop",        command=self._ctx_stop_task)
        self._queue_menu.add_command(label="↻ Retry",        command=self._ctx_retry_task)
        self._queue_menu.add_separator()
        self._queue_menu.add_command(label="✕ Remove",       command=self._ctx_remove_task)
        self._queue_menu.add_command(label="✕ Clear Done",   command=self._clear_finished_tasks)
        self._queue_menu.add_command(label="✕ Clear All",    command=self._clear_all_tasks)

    # ── Queue interaction events ──

    def _on_queue_select(self, event=None):
        """Update detail panel when a queue item is selected."""
        sel = self._up_tree.selection()
        if not sel:
            return
        iid = sel[0]
        task = self.upscaler_tasks.get(iid)
        if not task:
            return
        fname = os.path.basename(task["path"])
        status = task.get("status_text", "QUEUED")
        detail = task.get("detail_text", "Waiting to start")
        pct = task.get("progress_pct", 0)

        self._detail_status.configure(text=f"{fname}  •  {status}")
        self._detail_bar.set(max(0, min(100, pct)) / 100.0)
        self._detail_pct.configure(text=f"{pct}%" if pct > 0 else "")

        # Color the bar based on stage
        stage = task.get("stage_type", "queued")
        color_map = {
            "copy": _C["stage_copy"], "sync": _C["stage_sync"],
            "process": _C["stage_process"], "merge": _C["stage_merge"],
            "save": _C["stage_save"], "done": _C["stage_done"],
            "error": _C["stage_error"], "stopped": "#ffaa33",
        }
        self._detail_bar.configure(
            progress_color=color_map.get(stage, _C["stage_process"])
        )

    def _on_queue_double_click(self, event):
        """Double-click to start a queued task."""
        iid = self._up_tree.identify_row(event.y)
        if not iid:
            return
        task = self.upscaler_tasks.get(iid)
        if task and not task["started"]:
            self._start_single_task(task)

    def _on_queue_right_click(self, event):
        """Show context menu on right-click."""
        iid = self._up_tree.identify_row(event.y)
        if iid:
            self._up_tree.selection_set(iid)
            self._queue_menu.tk_popup(event.x_root, event.y_root)

    def _ctx_start_task(self):
        sel = self._up_tree.selection()
        for iid in sel:
            task = self.upscaler_tasks.get(iid)
            if task and not task["started"]:
                self._start_single_task(task)

    def _ctx_stop_task(self):
        """Stop a currently running task via context menu (local mode only)."""
        if self.processing_mode_var.get() != "local":
            return
        sel = self._up_tree.selection()
        for iid in sel:
            task = self.upscaler_tasks.get(iid)
            if task and task.get("started") and not task.get("finished"):
                task["_stop_requested"] = True
                self.local_upscaler.cancel()

    def _ctx_retry_task(self):
        sel = self._up_tree.selection()
        for iid in sel:
            task = self.upscaler_tasks.get(iid)
            if task and (task.get("has_error") or task.get("stopped")):
                self._retry_task(task)

    def _ctx_remove_task(self):
        sel = list(self._up_tree.selection())
        for iid in sel:
            task = self.upscaler_tasks.get(iid)
            if task and (not task["started"] or task.get("finished")):
                self._up_tree.delete(iid)
                self.upscaler_tasks.pop(iid, None)
                if iid in self._task_order:
                    self._task_order.remove(iid)
        self._update_queue_badge()

    # ═══════════════════════════════════════════════════════════════════════════
    # LOG PANEL (separate, like metadata processing)
    # ═══════════════════════════════════════════════════════════════════════════

    # (log panel removed — log is now inside each card)

    # ═══════════════════════════════════════════════════════════════════════════
    # SIDEBAR HELPERS
    # ═══════════════════════════════════════════════════════════════════════════

    def _lbl(self, parent, text):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=11, weight="bold"), text_color=COLORS["text_primary"]
        ).pack(pady=(0, 3), anchor="w")

    def _sep(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color=_C["divider"]).pack(fill="x", pady=(0, 8))

    # ═══════════════════════════════════════════════════════════════════════════
    # SETTINGS PERSISTENCE
    # ═══════════════════════════════════════════════════════════════════════════

    def _load_upscaler_settings(self):
        if not HAS_DB:
            self._update_path_labels()
            self._update_drive_status()
            return
        saved_output = db.get_setting("upscaler_output_path", "")
        if saved_output and os.path.isdir(saved_output):
            self.upscale_output_path = saved_output

        # NOTE: Auto-login is handled by _auto_connect_gdrive (called via self.after)
        # Do NOT duplicate here — two simultaneous auth calls cause race conditions
        self._update_path_labels()
        self._update_drive_status()

    def _save_upscaler_setting(self, key, value):
        if HAS_DB:
            db.save_setting(key, value)

    def _update_path_labels(self):
        if hasattr(self, "_gdrive_path_label"):
            if self.gdrive_bridge.is_configured:
                p = self.gdrive_bridge.gdrive_path or "Connected"
                self._gdrive_path_label.configure(text=f"✅ {p}", text_color=COLORS["success"])
            else:
                self._gdrive_path_label.configure(
                    text="⚠ Not logged in — click Login Google", text_color=COLORS["warning"]
                )
        if hasattr(self, "_output_path_label"):
            p = self.upscale_output_path
            d = p if len(p) <= 35 else "..." + p[-32:]
            self._output_path_label.configure(text=d)

    # ═══════════════════════════════════════════════════════════════════════════
    # GOOGLE DRIVE API LOGIN
    # ═══════════════════════════════════════════════════════════════════════════

    def _login_gdrive_api(self):
        """Login to Google Drive via API (opens browser for OAuth)."""
        if not HAS_GDRIVE_API:
            messagebox.showerror("Missing Libraries",
                "Google Drive API libraries not installed.\n\n"
                "Run in terminal:\n"
                "pip install google-api-python-client google-auth-oauthlib")
            return

        # Check bundled/saved credentials are available
        if not GDriveAPI.has_credentials():
            messagebox.showerror("Configuration Error",
                "Google Drive credentials not found.\n"
                "Please contact the developer.")
            return

        self._gdrive_login_btn.configure(text="⏳ Logging in...", state="disabled")

        def do_login():
            try:
                self.gdrive_bridge.authenticate()
                self.upscaler_client.set_bridge(self.gdrive_bridge)
                email = self.gdrive_bridge._user_email or "Connected"
                self.after(0, lambda: self._on_gdrive_login_success(email))
            except Exception as e:
                self.after(0, lambda: self._on_gdrive_login_error(str(e)))

        threading.Thread(target=do_login, daemon=True).start()

    def _auto_connect_gdrive(self):
        """Auto-connect to Google Drive if a saved token exists."""
        if not HAS_GDRIVE_API:
            return
        if not GDriveAPI.has_saved_token():
            return
        if not GDriveAPI.has_credentials():
            return

        def do_auto():
            try:
                self.gdrive_bridge.authenticate()
                self.upscaler_client.set_bridge(self.gdrive_bridge)
                email = self.gdrive_bridge._user_email or "Connected"
                self.after(0, lambda: self._on_gdrive_login_success(email, silent=True))
            except Exception:
                pass  # Silent fail — user can click Login manually

        threading.Thread(target=do_auto, daemon=True).start()

    def _on_gdrive_login_success(self, email, silent=False):
        self._update_path_labels()
        self._update_drive_status()
        if not silent:
            messagebox.showinfo("Connected!", f"Google Drive connected!\n{email}")

    def _on_gdrive_login_error(self, error):
        self._gdrive_login_btn.configure(text="🔑 Login Google", state="normal")
        messagebox.showerror("Login Failed", f"Could not login:\n{error}")

    def _logout_gdrive_api(self):
        """Logout from Google Drive API."""
        self.gdrive_bridge.logout()
        self.upscaler_client.set_bridge(self.gdrive_bridge)
        self._update_path_labels()
        self._update_drive_status()

    def _change_output_folder(self):
        folder = filedialog.askdirectory(initialdir=self.upscale_output_path, title="Output Folder")
        if folder:
            self.upscale_output_path = folder
            self._save_upscaler_setting("upscaler_output_path", folder)
            self._update_path_labels()

    # ═══════════════════════════════════════════════════════════════════════════
    # FILE HANDLING
    # ═══════════════════════════════════════════════════════════════════════════

    def _browse_upscale_files(self):
        all_exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS | IMAGE_EXTS))
        vid_exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        img_exts = " ".join(f"*{e}" for e in sorted(IMAGE_EXTS))
        files = filedialog.askopenfilenames(
            title="Select Files to Upscale",
            filetypes=[
                ("All Supported", all_exts),
                ("Video Files", vid_exts),
                ("Image Files", img_exts),
                ("All", "*.*"),
            ]
        )
        if files:
            self._add_upscale_tasks(files)

    def _on_upscale_drop(self, event):
        raw = event.data
        files = self._parse_dnd_upscale(raw)
        valid_exts = VIDEO_EXTS | IMAGE_EXTS
        valid = [f for f in files if os.path.splitext(f)[1].lower() in valid_exts]
        if valid:
            self._add_upscale_tasks(valid)

    def _parse_dnd_upscale(self, raw):
        if "{" in raw:
            import re
            return re.findall(r"\{([^}]+)\}", raw)
        return raw.split()

    # ═══════════════════════════════════════════════════════════════════════════
    # MODE SWITCHING & LOCAL ENGINE
    # ═══════════════════════════════════════════════════════════════════════════

    def _on_mode_change(self, selected):
        if "Local" in selected:
            self.processing_mode_var.set("local")
            self._upscaler_subtitle.configure(text="Real-ESRGAN • Local GPU/CPU")
            # Hide Colab sections, show local info
            self._colab_guide_frame.pack_forget()
            self._gdrive_section.pack_forget()
            self._update_local_gpu_label()
        else:
            self.processing_mode_var.set("colab")
            self._upscaler_subtitle.configure(text="Real-ESRGAN • Colab GPU (API)")
            # Show Colab sections, hide local info
            self._colab_guide_frame.pack(fill="x", after=self._gpu_info_label)
            self._gdrive_section.pack(fill="x", after=self._colab_guide_frame)
            self._download_engine_btn.pack_forget()
            self._gpu_info_label.configure(text="")

    def _detect_local_gpu(self):
        """Detect GPU in background thread."""
        def detect():
            info = self.local_upscaler.detect_gpu()
            self._local_gpu_info = info
        threading.Thread(target=detect, daemon=True).start()

    def _update_local_gpu_label(self):
        info = getattr(self, "_local_gpu_info", None)
        if info is None:
            # Don't block main thread — detect in background, update label when done
            self._gpu_info_label.configure(
                text="🔍 Detecting GPU...",
                text_color=COLORS["text_muted"]
            )
            def bg_detect():
                detected = self.local_upscaler.detect_gpu()
                self._local_gpu_info = detected
                self.after(0, self._update_local_gpu_label)
            threading.Thread(target=bg_detect, daemon=True).start()
            return

        all_gpus = info.get("all_gpus", [])

        if info["has_gpu"]:
            gpu_text = f"🟢 GPU: {info['gpu_name']}"
            if len(all_gpus) > 1:
                gpu_text += f" (+{len(all_gpus)-1} more)"
            gpu_text += "  •  Auto-selected"
            self._gpu_info_label.configure(
                text=gpu_text,
                text_color="#00ff88"
            )
            self._auto_use_gpu = True
        else:
            self._gpu_info_label.configure(
                text="🟡 No Vulkan GPU detected — using CPU (slower)",
                text_color=COLORS["warning"]
            )
            self._auto_use_gpu = False

        # Show/hide download button
        if self.local_upscaler.is_installed:
            self._download_engine_btn.pack_forget()
        else:
            self._download_engine_btn.pack(fill="x", pady=(0, 8))

    def _download_local_engine(self):
        """Download realesrgan-ncnn-vulkan engine."""
        self._download_engine_btn.configure(
            text="⏳ Downloading...", state="disabled"
        )

        def do_download():
            try:
                def progress_cb(dl_mb, total_mb):
                    if dl_mb < 0:  # extracting
                        self.after(0, lambda: self._download_engine_btn.configure(
                            text="📦 Extracting..."
                        ))
                    elif total_mb > 0:
                        pct = int((dl_mb / total_mb) * 100)
                        self.after(0, lambda p=pct, d=dl_mb, t=total_mb:
                            self._download_engine_btn.configure(
                                text=f"⏳ {d:.1f}/{t:.1f} MB ({p}%)"
                            )
                        )

                self.local_upscaler.download_engine(progress_cb=progress_cb)
                self.after(0, self._on_engine_downloaded)
            except Exception as e:
                self.after(0, lambda: self._on_engine_download_failed(str(e)))

        threading.Thread(target=do_download, daemon=True).start()

    def _on_engine_downloaded(self):
        self._download_engine_btn.pack_forget()
        messagebox.showinfo("Done!", "Real-ESRGAN engine installed!\nReady for local upscaling.")
        self._update_local_gpu_label()

    def _on_engine_download_failed(self, err):
        self._download_engine_btn.configure(
            text="📥 Download Engine (~15 MB)", state="normal"
        )
        messagebox.showerror("Download Failed", f"Could not download engine:\n{err}")

    # ═══════════════════════════════════════════════════════════════════════════
    # TASK MANAGEMENT — Treeview rows instead of widget cards
    # ═══════════════════════════════════════════════════════════════════════════

    def _file_size_str(self, path):
        try:
            s = os.path.getsize(path)
            if s < 1024: return f"{s} B"
            if s < 1024**2: return f"{s/1024:.1f} KB"
            if s < 1024**3: return f"{s/1024**2:.1f} MB"
            return f"{s/1024**3:.2f} GB"
        except Exception:
            return "—"

    def _add_upscale_tasks(self, file_paths):
        """Add files to the Treeview queue. Instant even for thousands of files."""
        existing_paths = {t["path"] for t in self.upscaler_tasks.values()}
        new_paths = [p for p in file_paths if p not in existing_paths]
        if not new_paths:
            return

        for path in new_paths:
            fname = os.path.basename(path)
            ext = os.path.splitext(path)[1].upper().replace(".", "")
            size_str = self._file_size_str(path)
            ftype = "🎬" if is_video(path) else "🖼"

            iid = self._up_tree.insert(
                "", "end",
                values=(ftype, fname, size_str, "QUEUED", "", "Waiting to start"),
                tags=("queued",)
            )

            self.upscaler_tasks[iid] = {
                "iid": iid,
                "path": path,
                "started": False,
                "finished": False,
                "has_error": False,
                "stopped": False,
                "_stop_requested": False,
                "task_id": None,
                "drive_filename": None,
                "status_text": "QUEUED",
                "detail_text": "Waiting to start",
                "progress_pct": 0,
                "stage_type": "queued",
            }
            self._task_order.append(iid)

        self._update_queue_badge()

    def _update_queue_badge(self):
        if hasattr(self, "_queue_badge"):
            self._queue_badge.configure(text=str(len(self.upscaler_tasks)))

    # (Thumbnails removed — Treeview queue uses text icons for max performance)

    # (No inline log — progress shown in card, details in Colab output)

    # ═══════════════════════════════════════════════════════════════════════════
    # TASK EXECUTION
    # ═══════════════════════════════════════════════════════════════════════════

    def _preflight_check(self):
        mode = self.processing_mode_var.get()
        if mode == "local":
            if not self.local_upscaler.is_installed:
                return "Local engine not installed.\nClick 'Download Engine' in sidebar."
            return None
        else:
            if not self.gdrive_bridge.is_configured:
                return "Google Drive not connected.\nClick 'Login Google' in sidebar."
            return None

    def _start_single_task(self, task):
        err = self._preflight_check()
        if err:
            messagebox.showwarning("Cannot Start", err)
            return
        if task["started"]:
            return
        task["started"] = True
        task["_stop_requested"] = False
        # Reset cancel flag on local upscaler for fresh start
        self.local_upscaler._cancel = False
        self._set_ui(task, "process", "STARTING", "Starting...", 0)
        threading.Thread(target=self._run_task, args=(task,), daemon=True).start()

    def _start_all_tasks(self):
        err = self._preflight_check()
        if err:
            messagebox.showwarning("Cannot Start", err)
            return
        queued = [t for t in self.upscaler_tasks.values() if not t["started"]]
        if not queued:
            messagebox.showinfo("Nothing", "No pending tasks.")
            return
        self._upscaler_batch_running = True
        self._upscaler_stop_event.clear()
        mode = self.processing_mode_var.get()

        # Toggle buttons: Local = show Stop, Colab = show Processing
        self.start_all_btn.pack_forget()
        self.retry_all_btn.pack_forget()
        if mode == "local":
            self.stop_all_btn.pack(fill="x")
        else:
            self.start_all_btn.configure(state="disabled", text="⏳  Processing...")
            self.start_all_btn.pack(fill="x")

        def run_batch():
            for t in queued:
                # Only check stop event in local mode
                if mode == "local" and self._upscaler_stop_event.is_set():
                    break
                if not t["started"]:
                    t["started"] = True
                    t["_stop_requested"] = False
                    self._set_ui(t, "process", "STARTING", "Starting...", 0)
                    self._run_task(t)
            self.after(0, self._on_batch_done)

        threading.Thread(target=run_batch, daemon=True).start()

    def _stop_all_tasks(self):
        """Stop all running tasks (local mode only)."""
        self._upscaler_stop_event.set()

        # Mark all in-progress tasks as stop-requested
        for t in self.upscaler_tasks.values():
            if t.get("started") and not t.get("finished"):
                t["_stop_requested"] = True

        # Cancel local upscaler subprocess
        self.local_upscaler.cancel()

        # Update button immediately
        self.stop_all_btn.configure(text="⏳ Stopping...", state="disabled")

    def _on_batch_done(self):
        self._upscaler_batch_running = False
        self._upscaler_stop_event.clear()
        mode = self.processing_mode_var.get()

        # Toggle buttons back
        self.stop_all_btn.pack_forget()
        self.stop_all_btn.configure(text="⏹  Stop All", state="normal")
        self.start_all_btn.pack_forget()  # Remove if already packed (colab processing state)
        self.start_all_btn.configure(state="normal", text="🚀  Start All Upscale")
        self.start_all_btn.pack(fill="x")

        # Show Retry button only in local mode if there are failed/stopped tasks
        if mode == "local":
            failed = [t for t in self.upscaler_tasks.values()
                      if t.get("has_error") or t.get("stopped")]
            if failed:
                self.retry_all_btn.pack(fill="x", pady=(4, 0))
            else:
                self.retry_all_btn.pack_forget()
        else:
            self.retry_all_btn.pack_forget()

    def _retry_all_failed(self):
        """Retry all failed and stopped tasks."""
        to_retry = [t for t in self.upscaler_tasks.values()
                    if t.get("has_error") or t.get("stopped")]
        if not to_retry:
            return

        # Reset all failed/stopped tasks first
        for t in to_retry:
            t["started"] = False
            t["finished"] = False
            t["has_error"] = False
            t["stopped"] = False
            t["_stop_requested"] = False
            t["task_id"] = None
            t["drive_filename"] = None
            t["status_text"] = "QUEUED"
            t["detail_text"] = "Waiting to retry"
            t["progress_pct"] = 0
            t["stage_type"] = "queued"
            iid = t["iid"]
            if self._up_tree.exists(iid):
                fname = os.path.basename(t["path"])
                size_str = self._file_size_str(t["path"])
                ftype = "🎬" if is_video(t["path"]) else "🖼"
                self._up_tree.item(iid, values=(
                    ftype, fname, size_str, "QUEUED", "", "Waiting to retry"
                ), tags=("queued",))

        # Now start them
        self._start_all_tasks()

    # ═══════════════════════════════════════════════════════════════════════════
    # WORKER THREAD
    # ═══════════════════════════════════════════════════════════════════════════

    def _is_task_stopped(self, task):
        """Check if this task was requested to stop."""
        return (task.get("_stop_requested") or
                self._upscaler_stop_event.is_set())

    def _run_task(self, task):
        mode = self.processing_mode_var.get()
        try:
            if mode == "local":
                self._run_local_task(task)
            else:
                self._run_colab_task(task)
        except Exception as e:
            err_msg = str(e)

            # Check if this was a user-initiated stop (local mode only)
            if mode == "local" and (self._is_task_stopped(task) or "Cancelled" in err_msg):
                task["finished"] = True
                task["stopped"] = True
                task["has_error"] = False
                self._set_ui(task, "stopped", "STOPPED", "⏹ Stopped by user", 0)
            else:
                task["finished"] = True
                task["has_error"] = True
                if len(err_msg) > 100:
                    err_msg = err_msg[:97] + "..."
                self._set_ui(task, "error", "FAILED", err_msg, 0)

            if mode == "colab" and task.get("task_id"):
                try:
                    self.gdrive_bridge.cleanup_task(task["task_id"])
                except Exception:
                    pass

    def _run_local_task(self, task):
        """Process file locally using realesrgan-ncnn-vulkan.
        Auto-fallback: if GPU fails, automatically retries with CPU.
        """
        scale = int(self.scale_var.get().replace("x", ""))
        model_key = self._get_selected_model_key()
        face = self.face_enhance_var.get()
        mute = self.mute_audio_var.get()
        fmt = self.output_format_var.get().lower()
        target_fps = 60 if "60" in self.output_fps_var.get() else (30 if "30" in self.output_fps_var.get() else 0)
        path = task["path"]

        # Auto-detect GPU (no manual selector — fully automatic)
        gpu = self.local_upscaler.detect_gpu()
        if gpu["has_gpu"]:
            use_cpu = False
            device = gpu["gpu_name"]
        else:
            use_cpu = True
            device = "CPU"

        def progress_cb(stage, pct, msg):
            stage_map = {
                "process": ("process", "PROCESSING"),
                "merge": ("merge", "MERGING"),
                "done": ("done", "DONE"),
            }
            st, pill = stage_map.get(stage, ("process", "PROCESSING"))
            self._set_ui(task, st, pill, msg, pct)

        self._set_ui(task, "process", "STARTING", f"Using {device}...", 5)

        # Check stop before starting
        if self._is_task_stopped(task):
            raise Exception("Cancelled by user")

        def do_upscale(cpu_mode):
            if is_image(path):
                return self.local_upscaler.upscale_image(
                    input_path=path,
                    output_dir=self.upscale_output_path,
                    scale=scale,
                    model=model_key,
                    face_enhance=face,
                    progress_cb=progress_cb,
                    force_cpu=cpu_mode,
                )
            else:
                return self.local_upscaler.upscale_video(
                    input_path=path,
                    output_dir=self.upscale_output_path,
                    scale=scale,
                    model=model_key,
                    face_enhance=face,
                    mute_audio=mute,
                    output_format=fmt,
                    progress_cb=progress_cb,
                    force_cpu=cpu_mode,
                    target_fps=target_fps,
                )

        try:
            result = do_upscale(use_cpu)
        except Exception as gpu_err:
            # Auto-fallback: GPU failed → retry with CPU
            if not use_cpu:
                logger.warning("GPU failed (%s), falling back to CPU: %s", device, gpu_err)
                self._set_ui(task, "process", "RETRYING",
                             f"⚠️ GPU ({device}) failed — retrying with CPU...", 5)
                time.sleep(1)  # Brief pause for user to see the message
                try:
                    result = do_upscale(True)  # Force CPU mode
                except Exception as cpu_err:
                    raise Exception(
                        f"Both GPU and CPU failed.\n"
                        f"GPU ({device}): {str(gpu_err)[:100]}\n"
                        f"CPU: {str(cpu_err)[:100]}"
                    )
            else:
                raise  # Already on CPU, nothing to fallback to

        # Check stop after upscale
        if self._is_task_stopped(task):
            raise Exception("Cancelled by user")

        saved_name = os.path.basename(result)
        final_mb = os.path.getsize(result) / (1024 * 1024)
        self._set_ui(task, "done", "DONE", f"✅ {saved_name} ({final_mb:.1f} MB)", 100)
        task["finished"] = True

    def _run_colab_task(self, task):
        """Process file via Google Colab (original flow).
        Supports both video and image files.
        Images get a direct upscale flow (no frame extraction/merging).
        """
        scale = int(self.scale_var.get().replace("x", ""))
        model_key = self._get_selected_model_key()
        face = self.face_enhance_var.get()
        mute = self.mute_audio_var.get()
        fmt = self.output_format_var.get().lower()
        target_fps = 60 if "60" in self.output_fps_var.get() else (30 if "30" in self.output_fps_var.get() else 0)
        orig = os.path.basename(task["path"])
        file_is_image = is_image(task["path"])

        # For images, output format = original image extension
        if file_is_image:
            img_ext = os.path.splitext(task["path"])[1].lstrip(".").lower()
            if img_ext in ("jpg", "jpeg"):
                img_ext = "png"  # upscaled images are better as PNG
            fmt = img_ext or "png"

        task_id = GDriveAPI.generate_task_id()
        task["task_id"] = task_id

        # ── PHASE 1: UPLOAD TO DRIVE ──
        self._set_ui(task, "copy", "UPLOADING", "Uploading to Drive...", 0)

        def copy_cb(done, total):
            p = int((done / total) * 100) if total > 0 else 0
            mb_d = done / (1024*1024)
            mb_t = total / (1024*1024)
            self._set_ui(task, "copy", "UPLOADING", f"Uploading {mb_d:.1f}/{mb_t:.1f} MB", p)

        drive_fn = self.gdrive_bridge.copy_to_input(task["path"], task_id, progress_cb=copy_cb)
        task["drive_filename"] = drive_fn

        # ── PHASE 2: SUBMIT JOB & WAIT FOR COLAB ──
        file_type_label = "image" if file_is_image else "video"
        self._set_ui(task, "sync", "SYNCING", f"Submitting {file_type_label} job to Colab...", 0)
        self.upscaler_client.start_process(
            task_id=task_id, filename=drive_fn, scale=scale,
            model=model_key, face_enhance=face, mute_audio=mute,
            output_format=fmt, target_fps=target_fps
        )
        self._set_ui(task, "sync", "SYNCING", "Waiting for Colab to pick up job...", 10)

        # Poll until Colab starts processing
        t0 = time.time()
        while True:
            r = self.upscaler_client.poll_status(task_id)
            s = r["status"]
            if s in ("processing", "encoding", "merging", "completed", "failed"):
                break
            if s == "error":
                raise Exception(r.get("error", "Error from Colab"))
            elapsed = int(time.time() - t0)
            self._set_ui(task, "sync", "WAITING", f"Waiting for Colab... ({elapsed}s)", 15)
            time.sleep(3)  # Poll faster for better responsiveness

        # ── PHASE 3: PROCESSING ON COLAB GPU ──
        # Poll Colab status with detailed stage display
        while True:
            r = self.upscaler_client.poll_status(task_id)
            s = r["status"]
            p = r.get("progress", 0)
            st = r.get("stage", "")

            if s == "completed":
                break
            elif s == "failed":
                raise Exception(r.get("error", "Processing failed on Colab"))
            elif s in ("encoding", "merging"):
                # Colab is encoding/merging frames into video
                display = st or "Merging frames into video..."
                self._set_ui(task, "merge", "MERGING", display, p)
            else:
                # Map Colab stage messages to display-friendly text
                if st:
                    display = st
                elif file_is_image:
                    display = "Upscaling image on Colab GPU..."
                else:
                    display = "Upscaling on Colab GPU..."
                self._set_ui(task, "process", "PROCESSING", display, p)
            time.sleep(2)  # Poll every 2s for closer to realtime

        # ── PHASE 4: DOWNLOAD FROM DRIVE ──
        self._set_ui(task, "save", "DOWNLOADING", "Searching for output in Drive...", 5)

        def download_progress_cb(progress_pct):
            dl_pct = 10 + int(progress_pct * 55)  # 10-65% range
            self._set_ui(task, "save", "DOWNLOADING",
                         f"Downloading from Drive... {int(progress_pct*100)}%", dl_pct)

        out = self.gdrive_bridge.watch_for_output(
            task_id, fmt, timeout=3600, poll_interval=3,
            download_progress_cb=download_progress_cb
        )
        if not out:
            raise Exception("Timeout waiting for output file in Google Drive.")

        # File is now downloaded to temp/local
        dl_mb = os.path.getsize(out) / (1024 * 1024)
        self._set_ui(task, "save", "DOWNLOADED", f"Downloaded {dl_mb:.1f} MB from Drive", 70)

        # ── PHASE 5: SAVE TO LOCAL OUTPUT FOLDER ──
        out_dir_display = self.upscale_output_path
        if len(out_dir_display) > 30:
            out_dir_display = "..." + out_dir_display[-27:]
        self._set_ui(task, "save", "SAVING", f"Saving to {out_dir_display}...", 75)
        final = self.gdrive_bridge.save_to_final(out, self.upscale_output_path, orig, fmt)

        # Verify the file actually landed in the output folder
        if not os.path.exists(final):
            raise Exception(f"File was not saved to local folder: {final}")

        final_mb = os.path.getsize(final) / (1024 * 1024)
        self._set_ui(task, "save", "SAVED",
                     f"✅ Saved to PC: {os.path.basename(final)} ({final_mb:.1f} MB)", 95)

        # ── CLEANUP ──
        self._set_ui(task, "save", "CLEANUP", "Cleaning up Drive...", 98)
        try:
            self.gdrive_bridge.cleanup_task(task_id)
        except Exception as e:
            logger.warning(f"Cleanup error (non-fatal): {e}")

        saved_name = os.path.basename(final)
        self._set_ui(task, "done", "DONE", f"✅ {saved_name} ({final_mb:.1f} MB)", 100)
        task["finished"] = True

    # ═══════════════════════════════════════════════════════════════════════════
    # UI UPDATE (thread-safe)
    # ═══════════════════════════════════════════════════════════════════════════

    def _set_ui(self, task, stage_type, pill_text, stage_msg, progress):
        """Update a task's Treeview row (thread-safe via .after)."""
        def do():
            try:
                iid = task["iid"]
                if not self._up_tree.exists(iid):
                    return

                # Store state for detail panel
                task["status_text"] = pill_text
                task["detail_text"] = stage_msg
                task["progress_pct"] = progress
                task["stage_type"] = stage_type

                # Build progress string
                if stage_type == "done":
                    pct_str = "100%"
                elif stage_type == "error":
                    pct_str = "✕"
                elif stage_type == "stopped":
                    pct_str = "⏹"
                else:
                    p = max(0, min(100, progress))
                    pct_str = f"{p}%" if p > 0 else ""

                # Update treeview row values
                fname = os.path.basename(task["path"])
                ext = os.path.splitext(task["path"])[1].upper().replace(".", "")
                size_str = self._file_size_str(task["path"])
                ftype = "🎬" if is_video(task["path"]) else "🖼"

                self._up_tree.item(iid, values=(
                    ftype, fname, size_str, pill_text, pct_str, stage_msg
                ))

                # Update row tag for coloring
                tag_map = {
                    "copy": "active", "sync": "active",
                    "process": "active", "merge": "active",
                    "save": "active", "done": "done",
                    "error": "error", "stopped": "stopped",
                }
                tag = tag_map.get(stage_type, "queued")
                self._up_tree.item(iid, tags=(tag,))

                # Auto-update detail panel if this task is selected
                sel = self._up_tree.selection()
                if sel and iid in sel:
                    self._on_queue_select()

            except Exception:
                pass
        self.after(0, do)

    # ═══════════════════════════════════════════════════════════════════════════
    # RETRY / CLEAR
    # ═══════════════════════════════════════════════════════════════════════════

    def _retry_task(self, task):
        task["started"] = False
        task["finished"] = False
        task["has_error"] = False
        task["stopped"] = False
        task["_stop_requested"] = False
        task["task_id"] = None
        task["drive_filename"] = None
        task["status_text"] = "QUEUED"
        task["detail_text"] = "Waiting to start"
        task["progress_pct"] = 0
        task["stage_type"] = "queued"
        # Reset cancel flag on local upscaler
        self.local_upscaler._cancel = False

        iid = task["iid"]
        if self._up_tree.exists(iid):
            fname = os.path.basename(task["path"])
            size_str = self._file_size_str(task["path"])
            ftype = "🎬" if is_video(task["path"]) else "🖼"
            self._up_tree.item(iid, values=(
                ftype, fname, size_str, "QUEUED", "", "Waiting to retry"
            ), tags=("queued",))

        self._start_single_task(task)

    def _clear_finished_tasks(self):
        to_remove = []
        for iid, t in self.upscaler_tasks.items():
            if t["finished"]:
                to_remove.append(iid)

        for iid in to_remove:
            if self._up_tree.exists(iid):
                self._up_tree.delete(iid)
            self.upscaler_tasks.pop(iid, None)
            if iid in self._task_order:
                self._task_order.remove(iid)

        self._update_queue_badge()

        # Trigger garbage collection after mass cleanup
        import gc
        gc.collect()

    def _clear_all_tasks(self):
        """Remove ALL tasks from the queue (queued, done, error — skips actively running)."""
        to_remove = []
        for iid, t in self.upscaler_tasks.items():
            # Skip tasks that are currently being processed
            if t.get("started") and not t.get("finished") and not t.get("has_error"):
                continue
            to_remove.append(iid)

        for iid in to_remove:
            if self._up_tree.exists(iid):
                self._up_tree.delete(iid)
            self.upscaler_tasks.pop(iid, None)
            if iid in self._task_order:
                self._task_order.remove(iid)

        self._update_queue_badge()

        import gc
        gc.collect()
