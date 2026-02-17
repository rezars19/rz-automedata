"""
RZ Automedata - Desktop Application
Adobe Stock Metadata Generator with Blue Neon Theme
Built with CustomTkinter

Main entry point — assembles all UI modules via mixins.
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False
from PIL import Image, ImageTk
import threading
import os
import sys
import pathlib
import webbrowser
import logging

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core.database as db
from core.ai_providers import get_provider_names, get_models_for_provider, ADOBE_STOCK_CATEGORIES, SHUTTERSTOCK_CATEGORIES, FREEPIK_MODELS
from core.metadata_processor import (
    get_file_type, load_preview_image, ALL_EXTENSIONS,
    process_all_assets
)
from core.csv_exporter import export_csv
from core.license_manager import (
    register_or_load_license, check_license, check_for_updates,
    get_current_version, is_configured, get_machine_id, CURRENT_VERSION
)
from core.auto_updater import download_update, apply_update_and_restart, is_frozen

from ui.theme import COLORS, PREVIEW_SIZE, compress_preview
from ui.license import LicenseUpdateMixin
from ui.header import HeaderMixin
from ui.sidebar import SidebarMixin
from ui.table import TableMixin
from ui.actions import ActionsMixin
from ui.navigation import NavigationMixin
from ui.keyword_research import KeywordResearchMixin

logger = logging.getLogger(__name__)


class RZAutomedata(
    LicenseUpdateMixin,
    HeaderMixin,
    NavigationMixin,
    SidebarMixin,
    TableMixin,
    ActionsMixin,
    KeywordResearchMixin,
    ctk.CTk,
    TkinterDnD.DnDWrapper if HAS_DND else object,
):
    def __init__(self):
        super().__init__()
        if HAS_DND:
            self.TkdndVersion = TkinterDnD._require(self)

        # ─── Window Setup ────────────────────────────────────────────────
        self.title("⚡ RZ Automedata — Stock Metadata Generator")
        self.geometry("1360x880")
        self.minsize(1200, 780)
        self.configure(fg_color=COLORS["bg_darkest"])

        # ─── App Icon ────────────────────────────────────────────────────
        try:
            if getattr(sys, 'frozen', False):
                # For --onefile builds, data files are in sys._MEIPASS (temp dir)
                base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
            else:
                base_path = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(base_path, "icon.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
                self.after(200, lambda: self.iconbitmap(icon_path))
        except Exception:
            pass

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # ─── License Check ───────────────────────────────────────────────
        self.license_key = None
        self.license_data = None
        self.machine_id = get_machine_id() if is_configured() else "N/A"

        if is_configured():
            self.license_key = register_or_load_license()
            is_valid, result = check_license()

            if not is_valid:
                # Show license activation screen instead of main app
                self._show_license_screen(result)
                self.protocol("WM_DELETE_WINDOW", self.destroy)
                return
            else:
                self.license_data = result

        # ─── State ───────────────────────────────────────────────────────
        self.asset_cards = {}
        self.preview_images = {}
        self.is_generating = False
        self.stop_event = threading.Event()
        self.generation_thread = None
        self.card_row_counter = 0
        self.log_visible = True
        self.api_keys = {}  # Per-provider API key storage: {provider_name: key}
        self.current_platform = "adobestock"  # "adobestock" or "shutterstock"

        # ─── Clear stale assets from previous session ────────────────────
        db.clear_all()

        # ─── Build UI ────────────────────────────────────────────────────
        self._build_ui()

        # ─── Load saved settings ─────────────────────────────────────────
        self._load_settings()

        # ─── Save settings and clear assets on close ─────────────────────
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ─── Check for updates (background, non-blocking) ────────────────
        if is_configured():
            self.after(1500, self._check_for_updates)

    # ═════════════════════════════════════════════════════════════════════════════
    # SETTINGS PERSISTENCE
    # ═════════════════════════════════════════════════════════════════════════════

    def _load_settings(self):
        """Load saved provider, model, per-provider API keys, and custom prompt from database."""
        saved_provider = db.get_setting("provider", "")
        saved_model = db.get_setting("model", "")
        saved_custom_prompt = db.get_setting("custom_prompt", "")

        # Load ALL per-provider API keys first (before any UI changes)
        for pname in get_provider_names():
            key = db.get_setting(f"api_key_{pname}", "")
            if key:
                self.api_keys[pname] = key

        # Set provider and model WITHOUT triggering _on_provider_changed
        # which would wipe the api_key_entry
        if saved_provider and saved_provider in get_provider_names():
            self.provider_var.set(saved_provider)
            self._last_provider = saved_provider
            # Update models dropdown directly (if exists)
            models = get_models_for_provider(saved_provider)
            if self.model_dropdown:
                self.model_dropdown.configure(values=models)
            if saved_model and saved_model in models:
                self.model_var.set(saved_model)
            elif models:
                self.model_var.set(models[0])
        else:
            self._last_provider = self.provider_var.get()

        # Load API key for the current provider into the entry field
        current_provider = self.provider_var.get()
        saved_key = self.api_keys.get(current_provider, "")
        if saved_key:
            self.api_key_entry.delete(0, "end")
            self.api_key_entry.insert(0, saved_key)

        # Load custom prompt
        if saved_custom_prompt:
            self.custom_prompt_entry.insert("1.0", saved_custom_prompt)

    def _save_settings(self):
        """Save current provider, model, per-provider API keys, and custom prompt to database."""
        current_provider = self.provider_var.get()
        # Save current API key to the per-provider dict
        current_key = self.api_key_entry.get().strip()
        if current_key:
            self.api_keys[current_provider] = current_key

        db.save_setting("provider", current_provider)
        db.save_setting("model", self.model_var.get())

        # Save all per-provider API keys
        for pname, pkey in self.api_keys.items():
            db.save_setting(f"api_key_{pname}", pkey)

        # Save custom prompt
        custom_prompt = self.custom_prompt_entry.get("1.0", "end-1c").strip()
        db.save_setting("custom_prompt", custom_prompt)

    def _on_close(self):
        """Handle window close — save settings then exit."""
        self._save_settings()
        self.destroy()

    # ═════════════════════════════════════════════════════════════════════════════
    # UI CONSTRUCTION
    # ═════════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        """Build the complete user interface."""
        self.main_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_darkest"])
        self.main_frame.pack(fill="both", expand=True)

        # Header
        self._build_header()

        # ── Outer body: Navigation bar + Page container ──
        outer_body = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        outer_body.pack(fill="both", expand=True, padx=0, pady=0)
        outer_body.grid_rowconfigure(0, weight=1)
        outer_body.grid_columnconfigure(0, weight=0)  # Nav bar (narrow)
        outer_body.grid_columnconfigure(1, weight=1)  # Page container

        # Left navigation (icon sidebar)
        self._build_navigation(outer_body)

        # Page container — holds switchable pages
        page_container = ctk.CTkFrame(outer_body, fg_color="transparent")
        page_container.grid(row=0, column=1, sticky="nsew", padx=(0, 0))
        page_container.grid_rowconfigure(0, weight=1)
        page_container.grid_columnconfigure(0, weight=1)

        # ═══════════════════════════════════════════════════════════════════
        # PAGE 1: METADATA (sidebar + table/log)
        # ═══════════════════════════════════════════════════════════════════
        self.metadata_page_frame = ctk.CTkFrame(page_container, fg_color="transparent")
        self.metadata_page_frame.grid(row=0, column=0, sticky="nsew")
        self.metadata_page_frame.grid_rowconfigure(0, weight=1)
        self.metadata_page_frame.grid_columnconfigure(0, weight=0)  # Sidebar
        self.metadata_page_frame.grid_columnconfigure(1, weight=1)  # Content

        body = self.metadata_page_frame

        # Left sidebar (metadata settings)
        self._build_sidebar(body)

        # Right content area (table + log)
        self.right_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 12), pady=(8, 12))
        self.right_frame.grid_rowconfigure(0, weight=3)
        self.right_frame.grid_rowconfigure(2, weight=1)
        self.right_frame.grid_columnconfigure(0, weight=1)

        self._build_asset_table(self.right_frame)
        self._build_log_toggle(self.right_frame)
        self._build_log_panel(self.right_frame)

        # ═══════════════════════════════════════════════════════════════════
        # PAGE 2: KEYWORD RESEARCH
        # ═══════════════════════════════════════════════════════════════════
        self._build_keyword_research_page(page_container)


# ═════════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = RZAutomedata()
    app.mainloop()
