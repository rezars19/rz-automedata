"""
RZ Automedata - Desktop Application
Adobe Stock Metadata Generator with Blue Neon Theme
Built with CustomTkinter
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

import database as db
from ai_providers import get_provider_names, get_models_for_provider, ADOBE_STOCK_CATEGORIES, SHUTTERSTOCK_CATEGORIES, FREEPIK_MODELS
from metadata_processor import (
    get_file_type, load_preview_image, ALL_EXTENSIONS,
    process_all_assets
)
from csv_exporter import export_csv
from license_manager import (
    register_or_load_license, check_license, check_for_updates,
    get_current_version, is_configured, get_machine_id, CURRENT_VERSION
)
from auto_updater import download_update, apply_update_and_restart, is_frozen

logger = logging.getLogger(__name__)

# ─── Theme Colors ────────────────────────────────────────────────────────────────
COLORS = {
    "bg_darkest":       "#060918",
    "bg_dark":          "#0a0e27",
    "bg_card":          "#0f1538",
    "bg_card_hover":    "#151d4a",
    "bg_input":         "#0c1230",
    "border":           "#1a2555",
    "border_glow":      "#0066ff",
    "neon_blue":        "#00d4ff",
    "neon_blue_dim":    "#0099cc",
    "accent_blue":      "#0066ff",
    "accent_purple":    "#7b2fff",
    "text_primary":     "#e8eaff",
    "text_secondary":   "#8890b5",
    "text_muted":       "#4a5280",
    "success":          "#00ff88",
    "error":            "#ff4466",
    "warning":          "#ffaa00",
    "stop_red":         "#ff2244",
    "clear_orange":     "#ff8800",
    "table_border":     "#1e2d6a",
    "table_row_even":   "#0c1235",
    "table_row_odd":    "#0f1740",
    "table_header":     "#111a45",
}

# Preview thumbnail size (small + compressed for speed)
PREVIEW_SIZE = (64, 48)


def compress_preview(img, max_size=PREVIEW_SIZE, quality=70):
    """Compress and resize preview image to reduce memory and speed up loading."""
    if img is None:
        return None
    img = img.copy()
    img.thumbnail(max_size, Image.LANCZOS)
    # Convert to RGB if RGBA
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (12, 18, 48))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img


class RZAutomedata(ctk.CTk, TkinterDnD.DnDWrapper if HAS_DND else object):
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
                base_path = os.path.dirname(sys.executable)
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
    # LICENSE & UPDATE SCREENS
    # ═════════════════════════════════════════════════════════════════════════════

    def _show_license_screen(self, error_message):
        """Show license activation screen when license is invalid."""
        self.title("⚡ RZ Automedata — Aktivasi Diperlukan")
        self.geometry("620x600")
        self.minsize(520, 550)
        self.resizable(False, False)

        # Main container
        main = ctk.CTkFrame(self, fg_color=COLORS["bg_darkest"])
        main.pack(fill="both", expand=True)

        # Glow line at top
        ctk.CTkFrame(main, fg_color=COLORS["neon_blue"], height=3, corner_radius=0).pack(fill="x")

        # Scrollable content
        scroll = ctk.CTkScrollableFrame(main, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=30, pady=20)

        # Card
        card = ctk.CTkFrame(
            scroll, fg_color=COLORS["bg_dark"], corner_radius=16,
            border_width=1, border_color=COLORS["border"]
        )
        card.pack(fill="x", pady=(0, 10))

        # Icon & Title
        ctk.CTkLabel(
            card, text="🔒",
            font=ctk.CTkFont(size=48)
        ).pack(pady=(25, 5))

        ctk.CTkLabel(
            card, text="Aktivasi Diperlukan",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color=COLORS["neon_blue"]
        ).pack(pady=(0, 10))

        # Error message
        ctk.CTkLabel(
            card, text=error_message,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"],
            wraplength=420, justify="center"
        ).pack(pady=(0, 15))

        # Separator
        ctk.CTkFrame(card, fg_color=COLORS["border"], height=1).pack(fill="x", padx=30, pady=5)

        # ── Machine ID Section (Primary) ──
        ctk.CTkLabel(
            card, text="🖥️  Machine ID Anda:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["neon_blue"]
        ).pack(pady=(12, 4), padx=30, anchor="w")

        ctk.CTkLabel(
            card, text="Kirim Machine ID ini ke admin untuk aktivasi",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"]
        ).pack(padx=30, anchor="w")

        # Machine ID display with mask
        mid_outer = ctk.CTkFrame(card, fg_color=COLORS["bg_input"], corner_radius=10,
                                  border_width=1, border_color=COLORS["accent_blue"])
        mid_outer.pack(padx=30, pady=(6, 0), fill="x")

        mid_row = ctk.CTkFrame(mid_outer, fg_color="transparent")
        mid_row.pack(fill="x", padx=10, pady=8)

        machine_id_text = self.machine_id if self.machine_id else "N/A"
        machine_id_masked = "•" * len(machine_id_text)
        self._lic_mid_visible = False

        mid_label = ctk.CTkLabel(
            mid_row, text=machine_id_masked,
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color=COLORS["neon_blue"]
        )
        mid_label.pack(side="left", expand=True, fill="x")

        def toggle_mid_visibility():
            self._lic_mid_visible = not self._lic_mid_visible
            if self._lic_mid_visible:
                mid_label.configure(text=machine_id_text)
                mid_show_btn.configure(text="🙈")
            else:
                mid_label.configure(text=machine_id_masked)
                mid_show_btn.configure(text="👁")

        mid_show_btn = ctk.CTkButton(
            mid_row, text="👁", width=36, height=28,
            command=toggle_mid_visibility,
            fg_color="transparent", hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(size=16), corner_radius=6
        )
        mid_show_btn.pack(side="right", padx=(4, 0))

        # Machine ID buttons
        mid_btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        mid_btn_frame.pack(padx=30, pady=(8, 12), fill="x")

        def copy_mid():
            self.clipboard_clear()
            self.clipboard_append(machine_id_text)
            mid_copy_btn.configure(text="✅ Copied!")
            self.after(1500, lambda: mid_copy_btn.configure(text="📋  Copy Machine ID"))

        mid_copy_btn = ctk.CTkButton(
            mid_btn_frame, text="📋  Copy Machine ID",
            command=copy_mid,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
            height=36, corner_radius=10
        )
        mid_copy_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))

        ctk.CTkButton(
            mid_btn_frame, text="🔄  Refresh",
            command=self._retry_license_check,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            border_width=1, border_color=COLORS["border"],
            font=ctk.CTkFont(size=13, weight="bold"),
            width=120, height=36, corner_radius=10
        ).pack(side="right")

        # ── Info Footer ──
        info_card = ctk.CTkFrame(
            scroll, fg_color=COLORS["bg_card"], corner_radius=12,
            border_width=1, border_color=COLORS["border"]
        )
        info_card.pack(fill="x", pady=(8, 0))

        ctk.CTkLabel(
            info_card, text="💡 Cara Aktivasi",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(padx=20, pady=(12, 4), anchor="w")

        steps_text = (
            "1. Copy Machine ID di atas\n"
            "2. Kirim ke admin untuk aktivasi\n"
            "3. Setelah admin mengaktifkan, klik Refresh\n"
            "4. Langganan: Rp30.000/bulan (30 hari)"
        )
        ctk.CTkLabel(
            info_card, text=steps_text,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
            justify="left"
        ).pack(padx=20, pady=(0, 12), anchor="w")

    def _retry_license_check(self):
        """Re-check license and restart app if valid."""
        is_valid, result = check_license()
        if is_valid:
            messagebox.showinfo("Berhasil!", "Lisensi aktif! Aplikasi akan dimulai ulang.")
            self.destroy()
            # Restart app
            new_app = RZAutomedata()
            new_app.mainloop()
        else:
            messagebox.showwarning("Belum Aktif", result)

    def _check_for_updates(self):
        """Check for app updates in background thread."""
        def _do_check():
            update_info = check_for_updates()
            if update_info:
                self.after(0, lambda: self._show_update_popup(update_info))

        threading.Thread(target=_do_check, daemon=True).start()

    def _show_update_popup(self, info):
        """Show update notification popup with auto-download."""
        is_mandatory = info.get("is_mandatory", False)

        dialog = ctk.CTkToplevel(self)
        dialog.title("Update Tersedia!" if not is_mandatory else "Update Wajib!")
        dialog.geometry("500x420")
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(fg_color=COLORS["bg_darkest"])
        dialog.resizable(False, False)

        if is_mandatory:
            dialog.protocol("WM_DELETE_WINDOW", lambda: None)

        # Glow
        ctk.CTkFrame(dialog, fg_color=COLORS["neon_blue"], height=3, corner_radius=0).pack(fill="x")

        # Content card
        card = ctk.CTkFrame(dialog, fg_color=COLORS["bg_dark"], corner_radius=14,
                            border_width=1, border_color=COLORS["border"])
        card.pack(fill="both", expand=True, padx=24, pady=20)

        # Icon
        icon = "⚠️" if is_mandatory else "🚀"
        ctk.CTkLabel(
            card, text=icon, font=ctk.CTkFont(size=40)
        ).pack(pady=(20, 5))

        # Title
        title_text = "Update Wajib!" if is_mandatory else "Update Tersedia!"
        title_color = COLORS["error"] if is_mandatory else COLORS["neon_blue"]
        ctk.CTkLabel(
            card, text=title_text,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=title_color
        ).pack(pady=(0, 8))

        # Version info
        ctk.CTkLabel(
            card, text=f"v{CURRENT_VERSION}  →  v{info['version']}",
            font=ctk.CTkFont(family="Consolas", size=15, weight="bold"),
            text_color=COLORS["success"]
        ).pack(pady=(0, 8))

        # Release notes
        if info.get("release_notes"):
            ctk.CTkLabel(
                card, text=info["release_notes"],
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_secondary"],
                wraplength=380, justify="center"
            ).pack(pady=(0, 10))

        # Progress area (hidden initially)
        progress_frame = ctk.CTkFrame(card, fg_color="transparent")

        progress_bar = ctk.CTkProgressBar(
            progress_frame, width=380, height=12,
            progress_color=COLORS["neon_blue"],
            fg_color=COLORS["bg_input"],
            corner_radius=6
        )
        progress_bar.set(0)
        progress_bar.pack(pady=(5, 3))

        progress_label = ctk.CTkLabel(
            progress_frame, text="Preparing download...",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        progress_label.pack()

        # Buttons
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.pack(pady=(10, 20))

        def _start_auto_update():
            """Download and apply update automatically."""
            download_url = info.get("download_url", "")
            if not download_url:
                messagebox.showerror("Error", "Download URL tidak tersedia.")
                return

            # Show progress, hide buttons
            btn_frame.pack_forget()
            progress_frame.pack(pady=(5, 15))

            def _on_progress(percent, dl_mb, total_mb):
                self.after(0, lambda: progress_bar.set(percent / 100))
                self.after(0, lambda: progress_label.configure(
                    text=f"Downloading... {dl_mb:.1f} / {total_mb:.1f} MB ({percent:.0f}%)"
                ))

            def _do_download():
                downloaded = download_update(download_url, on_progress=_on_progress)
                if downloaded:
                    self.after(0, lambda: progress_label.configure(
                        text="✅ Download selesai! Applying update..."
                    ))
                    self.after(0, lambda: progress_bar.set(1.0))

                    if is_frozen():
                        # Auto-replace and restart
                        self.after(500, lambda: _apply(downloaded, dialog))
                    else:
                        # Dev mode: just show success
                        self.after(500, lambda: _show_dev_done(downloaded, dialog))
                else:
                    self.after(0, lambda: _download_failed(dialog))

            threading.Thread(target=_do_download, daemon=True).start()

        def _apply(downloaded, dlg):
            """Apply update (exe mode)."""
            success = apply_update_and_restart(downloaded)
            if success:
                progress_label.configure(
                    text="🔄 Restarting...",
                    text_color=COLORS["success"]
                )
                self.after(1000, self.destroy)
            else:
                messagebox.showerror("Error", "Gagal menerapkan update. Coba download manual.")
                webbrowser.open(info["download_url"])

        def _show_dev_done(downloaded, dlg):
            """Dev mode: can't replace running script."""
            progress_label.configure(
                text=f"✅ Downloaded to: {downloaded}\nReplace file secara manual.",
                text_color=COLORS["success"]
            )
            done_frame = ctk.CTkFrame(card, fg_color="transparent")
            done_frame.pack(pady=(0, 10))
            ctk.CTkButton(
                done_frame, text="OK", command=dlg.destroy,
                fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
                text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
                width=100, height=34, corner_radius=10
            ).pack()

        def _download_failed(dlg):
            """Download failed, offer manual download."""
            progress_label.configure(
                text="❌ Download gagal. Coba download manual.",
                text_color=COLORS["error"]
            )
            fail_frame = ctk.CTkFrame(card, fg_color="transparent")
            fail_frame.pack(pady=(5, 10))
            ctk.CTkButton(
                fail_frame, text="🌐 Download Manual",
                command=lambda: webbrowser.open(info["download_url"]),
                fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
                text_color="white", font=ctk.CTkFont(size=12, weight="bold"),
                width=160, height=34, corner_radius=10
            ).pack(side="left", padx=4)
            ctk.CTkButton(
                fail_frame, text="Tutup", command=dlg.destroy,
                fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_secondary"],
                font=ctk.CTkFont(size=12),
                width=80, height=34, corner_radius=10
            ).pack(side="left", padx=4)

        # Update button
        ctk.CTkButton(
            btn_frame, text="⬇️  Update Sekarang",
            command=_start_auto_update,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
            width=180, height=38, corner_radius=10
        ).pack(side="left", padx=8)

        if not is_mandatory:
            ctk.CTkButton(
                btn_frame, text="Nanti Saja",
                command=dialog.destroy,
                fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
                text_color=COLORS["text_secondary"],
                border_width=1, border_color=COLORS["border"],
                font=ctk.CTkFont(size=13),
                width=120, height=38, corner_radius=10
            ).pack(side="left", padx=8)
        else:
            ctk.CTkButton(
                btn_frame, text="Keluar",
                command=self.destroy,
                fg_color=COLORS["error"], hover_color="#cc2244",
                text_color="white",
                font=ctk.CTkFont(size=13),
                width=120, height=38, corner_radius=10
            ).pack(side="left", padx=8)

    # ═════════════════════════════════════════════════════════════════════════════
    # UI CONSTRUCTION
    # ═════════════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        """Build the complete user interface."""
        self.main_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_darkest"])
        self.main_frame.pack(fill="both", expand=True)

        self._build_header()

        content = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self._build_sidebar(content)

        self.right_frame = ctk.CTkFrame(content, fg_color="transparent")
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        self.right_frame.grid_rowconfigure(0, weight=3)
        self.right_frame.grid_rowconfigure(1, weight=0)   # log toggle bar
        self.right_frame.grid_rowconfigure(2, weight=1)   # log panel
        self.right_frame.grid_columnconfigure(0, weight=1)

        self._build_asset_table(self.right_frame)
        self._build_log_toggle(self.right_frame)
        self._build_log_panel(self.right_frame)

    def _build_header(self):
        """Build the header bar."""
        header = ctk.CTkFrame(self.main_frame, fg_color=COLORS["bg_dark"], corner_radius=0, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        glow = ctk.CTkFrame(header, fg_color=COLORS["neon_blue"], height=2, corner_radius=0)
        glow.pack(fill="x", side="bottom")

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=24, pady=10)

        ctk.CTkLabel(
            title_box, text="⚡ RZ Automedata",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color=COLORS["neon_blue"]
        ).pack(side="left")

        ctk.CTkLabel(
            title_box, text="  |  Stock Metadata Generator",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(8, 0))

        # ── License info badge ──
        info_frame = ctk.CTkFrame(header, fg_color="transparent")
        info_frame.pack(side="right", padx=24)

        ctk.CTkLabel(
            info_frame, text=f" v{CURRENT_VERSION} ", font=ctk.CTkFont(size=11),
            text_color=COLORS["neon_blue"], fg_color=COLORS["bg_card"], corner_radius=6
        ).pack(side="right", padx=(8, 0))

        # Show license plan & days remaining
        if self.license_data and isinstance(self.license_data, dict):
            plan = self.license_data.get("plan", "")
            days_left = self.license_data.get("days_left")
            offline = self.license_data.get("offline_mode", False)

            plan_labels = {
                "trial": "🆓 Trial",
                "monthly": "⭐ Pro",
                "yearly": "💎 Pro Year",
                "lifetime": "👑 Lifetime"
            }
            plan_text = plan_labels.get(plan, plan.title())

            if days_left is not None:
                if days_left == "∞":
                    day_color = COLORS["neon_blue"]
                    day_text = " ∞ "
                elif isinstance(days_left, (int, float)):
                    if days_left <= 3:
                        day_color = COLORS["error"]
                    elif days_left <= 7:
                        day_color = COLORS["warning"]
                    else:
                        day_color = COLORS["success"]
                    day_text = f" {days_left}d left "
                else:
                    day_color = COLORS["success"]
                    day_text = f" {days_left} "

                ctk.CTkLabel(
                    info_frame,
                    text=day_text,
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color=day_color,
                    fg_color=COLORS["bg_card"], corner_radius=6
                ).pack(side="right", padx=(8, 0))

            plan_color = COLORS["warning"] if plan == "trial" else COLORS["success"]
            ctk.CTkLabel(
                info_frame,
                text=f" {plan_text} ",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=plan_color,
                fg_color=COLORS["bg_card"], corner_radius=6
            ).pack(side="right", padx=(0, 0))

            if offline:
                ctk.CTkLabel(
                    info_frame,
                    text=" 📡 Offline ",
                    font=ctk.CTkFont(size=10),
                    text_color=COLORS["warning"],
                    fg_color=COLORS["bg_card"], corner_radius=6
                ).pack(side="right", padx=(0, 8))

        # ── Machine ID badge (clickable to copy) ──
        if is_configured() and self.machine_id and self.machine_id != "N/A":
            mid_container = ctk.CTkFrame(info_frame, fg_color=COLORS["bg_card"], corner_radius=6)
            mid_container.pack(side="right", padx=(0, 8))

            ctk.CTkLabel(
                mid_container, text="  ID Machine:",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color=COLORS["text_secondary"]
            ).pack(side="left", padx=(6, 2))

            mid_short = self.machine_id[:8] + "..."
            ctk.CTkLabel(
                mid_container, text=mid_short,
                font=ctk.CTkFont(size=10, family="Consolas"),
                text_color=COLORS["neon_blue"]
            ).pack(side="left", padx=(0, 4))

            self._header_mid_btn = ctk.CTkButton(
                mid_container,
                text="📋",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_secondary"],
                fg_color="transparent",
                hover_color=COLORS["bg_card_hover"],
                corner_radius=4, height=22, width=28,
                command=self._copy_machine_id_header
            )
            self._header_mid_btn.pack(side="left", padx=(0, 4))

    def _copy_machine_id_header(self):
        """Copy machine ID to clipboard and show feedback on header badge."""
        self.clipboard_clear()
        self.clipboard_append(self.machine_id)
        self._header_mid_btn.configure(text="✅")
        self._show_toast("📋 Machine ID copied!")
        self.after(1500, lambda: self._header_mid_btn.configure(text="�"))

    def _build_sidebar(self, parent):
        """Build left sidebar with settings and action buttons."""
        sidebar_outer = ctk.CTkFrame(
            parent, fg_color=COLORS["bg_dark"], corner_radius=12,
            border_width=1, border_color=COLORS["border"], width=290
        )
        sidebar_outer.grid(row=0, column=0, sticky="nsew")
        sidebar_outer.grid_propagate(False)
        sidebar_outer.grid_rowconfigure(0, weight=1)
        sidebar_outer.grid_columnconfigure(0, weight=1)

        # Scrollable inner sidebar
        sidebar = ctk.CTkScrollableFrame(
            sidebar_outer, fg_color="transparent",
            scrollbar_button_color=COLORS["accent_blue"],
            scrollbar_button_hover_color=COLORS["neon_blue"]
        )
        sidebar.grid(row=0, column=0, sticky="nsew")

         # ── Platform Selection ────────────────────────────────────────
        self._section_label(sidebar, "🎯  Platform")

        self.platform_var = ctk.StringVar(value="Adobe Stock")
        self.platform_dropdown = ctk.CTkComboBox(
            sidebar, values=["Adobe Stock", "Shutterstock", "Freepik"],
            variable=self.platform_var, command=self._on_platform_dropdown_changed,
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            button_color=COLORS["accent_blue"], button_hover_color=COLORS["neon_blue"],
            dropdown_fg_color=COLORS["bg_card"], dropdown_hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"], font=ctk.CTkFont(size=12, weight="bold"),
            width=250, height=30
        )
        self.platform_dropdown.pack(padx=16, pady=(0, 2))

        self.platform_label = ctk.CTkLabel(
            sidebar, text="📋 CSV: Filename, Title, Keywords, Category",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"],
            wraplength=250, justify="left"
        )
        self.platform_label.pack(padx=16, pady=(1, 2), anchor="w")

        # ── Freepik-specific options (hidden by default) ───────────
        self.freepik_frame = ctk.CTkFrame(sidebar, fg_color="transparent")

        self.freepik_ai_var = ctk.BooleanVar(value=False)
        self.freepik_ai_checkbox = ctk.CTkCheckBox(
            self.freepik_frame, text="AI Generated", variable=self.freepik_ai_var,
            command=self._on_freepik_ai_toggle,
            font=ctk.CTkFont(size=11), text_color=COLORS["text_primary"],
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            border_color=COLORS["border"], height=22
        )
        self.freepik_ai_checkbox.pack(padx=0, pady=(2, 2), anchor="w")

        self.freepik_model_label = ctk.CTkLabel(
            self.freepik_frame, text="AI Model:",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_secondary"]
        )

        self.freepik_model_var = ctk.StringVar(value=FREEPIK_MODELS[0])
        self.freepik_model_dropdown = ctk.CTkComboBox(
            self.freepik_frame, values=FREEPIK_MODELS, variable=self.freepik_model_var,
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            button_color=COLORS["accent_blue"], button_hover_color=COLORS["neon_blue"],
            dropdown_fg_color=COLORS["bg_card"], dropdown_hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"], font=ctk.CTkFont(size=11),
            width=220, height=28
        )
        # Model label and dropdown hidden until AI Generated is checked

        ctk.CTkFrame(sidebar, fg_color=COLORS["border"], height=1).pack(fill="x", padx=16, pady=3)

        # ── Upload (Drag & Drop Zone + Browse) ─────────────────────────
        self._section_label(sidebar, "📁  Upload Assets")

        # Drag & Drop visual zone
        self.drop_frame = ctk.CTkFrame(
            sidebar, fg_color=COLORS["bg_input"], corner_radius=12,
            border_width=2, border_color=COLORS["border"], height=100
        )
        self.drop_frame.pack(padx=16, pady=(0, 4), fill="x")
        self.drop_frame.pack_propagate(False)

        drop_inner = ctk.CTkFrame(self.drop_frame, fg_color="transparent")
        drop_inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            drop_inner, text="📂",
            font=ctk.CTkFont(size=28), text_color=COLORS["accent_blue"]
        ).pack()
        ctk.CTkLabel(
            drop_inner, text="Drag & Drop Files Here",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=COLORS["text_primary"]
        ).pack()
        ctk.CTkLabel(
            drop_inner, text="JPG, PNG, EPS, SVG, MP4, MOV",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"]
        ).pack()

        # DnD status indicator
        dnd_status = "✅ Drag & Drop Ready" if HAS_DND else "❌ Drag & Drop unavailable"
        dnd_color = COLORS["success"] if HAS_DND else COLORS["error"]
        ctk.CTkLabel(
            drop_inner, text=dnd_status,
            font=ctk.CTkFont(size=8), text_color=dnd_color
        ).pack(pady=(2, 0))

        ctk.CTkButton(
            sidebar, text="📂  Browse Files", command=self._browse_files,
            fg_color=COLORS["bg_card"], hover_color=COLORS["accent_blue"],
            text_color=COLORS["text_primary"], border_width=1, border_color=COLORS["border"],
            font=ctk.CTkFont(size=12, weight="bold"),
            width=250, height=32, corner_radius=10
        ).pack(padx=16, pady=(0, 6))

        # Enable native drag & drop (try tkinterdnd2, fallback gracefully)
        if HAS_DND:
            try:
                self.drop_frame.drop_target_register(DND_FILES)
                self.drop_frame.dnd_bind('<<Drop>>', self._on_drop_files)
                self.drop_frame.dnd_bind('<<DragEnter>>', lambda e: self.drop_frame.configure(
                    border_color=COLORS["neon_blue"], fg_color=COLORS["bg_card"]))
                self.drop_frame.dnd_bind('<<DragLeave>>', lambda e: self.drop_frame.configure(
                    border_color=COLORS["border"], fg_color=COLORS["bg_input"]))
            except Exception:
                pass  # DnD registration failed, browse button still works

        ctk.CTkFrame(sidebar, fg_color=COLORS["border"], height=1).pack(fill="x", padx=16, pady=3)

        # ── Custom Prompt ─────────────────────────────────────────────
        self._section_label(sidebar, "✏️  Custom Prompt")
        ctk.CTkLabel(
            sidebar, text="Add keywords that MUST appear in title & keywords",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"],
            wraplength=250, justify="left"
        ).pack(padx=16, pady=(0, 2), anchor="w")

        self.custom_prompt_entry = ctk.CTkTextbox(
            sidebar, fg_color=COLORS["bg_input"], border_width=1,
            border_color=COLORS["border"], text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=12), width=250, height=50,
            wrap="word", corner_radius=8
        )
        self.custom_prompt_entry.pack(padx=16, pady=(0, 2))
        ctk.CTkLabel(
            sidebar, text="e.g: coffee, latte art, barista",
            font=ctk.CTkFont(size=9, slant="italic"), text_color=COLORS["text_muted"]
        ).pack(padx=16, pady=(0, 4), anchor="w")

        ctk.CTkFrame(sidebar, fg_color=COLORS["border"], height=1).pack(fill="x", padx=16, pady=3)

        # ── Actions ───────────────────────────────────────────────────
        self._section_label(sidebar, "⚡  Actions")

        self.generate_btn = ctk.CTkButton(
            sidebar, text="🚀  Generate All", command=self._on_generate_click,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
            width=250, height=38, corner_radius=10
        )
        self.generate_btn.pack(padx=16, pady=(0, 4))

        ctk.CTkButton(
            sidebar, text="🗑  Clear All", command=self._clear_all,
            fg_color=COLORS["error"], hover_color=COLORS["stop_red"],
            text_color="white", border_width=0,
            font=ctk.CTkFont(size=12, weight="bold"), width=250, height=34, corner_radius=10
        ).pack(padx=16, pady=(0, 4))

        self.csv_btn = ctk.CTkButton(
            sidebar, text="📥  Download CSV", command=self._download_csv,
            fg_color="#00875a", hover_color=COLORS["success"],
            text_color="white", border_width=0,
            font=ctk.CTkFont(size=12, weight="bold"), width=250, height=34, corner_radius=10,
            state="disabled"
        )
        self.csv_btn.pack(padx=16, pady=(0, 4))

        self.counter_label = ctk.CTkLabel(
            sidebar, text="Assets: 0  |  Done: 0",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_secondary"]
        )
        self.counter_label.pack(padx=16, pady=(6, 4))

        ctk.CTkFrame(sidebar, fg_color=COLORS["border"], height=1).pack(fill="x", padx=16, pady=3)

        # ── Settings Button (opens popup) ─────────────────────────────
        ctk.CTkButton(
            sidebar, text="⚙️  Settings", command=self._open_settings_popup,
            fg_color=COLORS["accent_purple"], hover_color="#9b51ff",
            text_color="white", border_width=0,
            font=ctk.CTkFont(size=13, weight="bold"),
            width=250, height=38, corner_radius=10
        ).pack(padx=16, pady=(0, 6))

        # ── Initialize provider state (used by settings popup & generation) ──
        self.provider_var = ctk.StringVar(value=get_provider_names()[0])
        self._last_provider = get_provider_names()[0]
        initial_models = get_models_for_provider(get_provider_names()[0])
        self.model_var = ctk.StringVar(value=initial_models[0] if initial_models else "")
        self.show_key_var = ctk.BooleanVar(value=False)

        # Hidden entry to store API key (used by _load_settings / _start_generation)
        self.api_key_entry = ctk.CTkEntry(sidebar, show="•", width=0, height=0)
        # Don't pack — it's hidden, just used as data holder

        # Provider/Model dropdowns (references for _load_settings compatibility)
        self.provider_dropdown = None  # Will use popup
        self.model_dropdown = None  # Will use popup


    def _on_platform_dropdown_changed(self, display_name):
        """Handle platform dropdown selection."""
        platform_map = {
            "Adobe Stock": "adobestock",
            "Shutterstock": "shutterstock",
            "Freepik": "freepik"
        }
        platform = platform_map.get(display_name, "adobestock")

        if platform == self.current_platform:
            return

        # Check if there's existing data
        if self.asset_cards:
            if self.is_generating:
                messagebox.showwarning("Busy", "Stop generation first.")
                # Revert dropdown
                rev_map = {v: k for k, v in platform_map.items()}
                self.platform_var.set(rev_map.get(self.current_platform, "Adobe Stock"))
                return
            if not messagebox.askyesno("Switch Platform",
                    f"Switching to {display_name} will clear all current assets.\n\nContinue?"):
                rev_map = {v: k for k, v in platform_map.items()}
                self.platform_var.set(rev_map.get(self.current_platform, "Adobe Stock"))
                return
            # Clear all assets
            db.clear_all()
            for card in self.asset_cards.values():
                if "row_frame" in card:
                    card["row_frame"].destroy()
            self.asset_cards.clear()
            self.preview_images.clear()
            self.card_row_counter = 0
            self._update_csv_button_state()
            self.progress_label.configure(text="")

        self.current_platform = platform

        # Update CSV format label and Freepik options
        if platform == "freepik":
            self.platform_label.configure(
                text="📋 CSV: Filename, Title, Keywords, Prompt, Model"
            )
            self.freepik_frame.pack_forget()
            self.freepik_frame.pack(padx=16, pady=(0, 2), fill="x", after=self.platform_label)
        elif platform == "shutterstock":
            self.platform_label.configure(
                text="📋 CSV: Filename, Description, Keywords, Categories, Editorial, Mature, Illustration"
            )
            self.freepik_frame.pack_forget()
        else:
            self.platform_label.configure(
                text="📋 CSV: Filename, Title, Keywords, Category"
            )
            self.freepik_frame.pack_forget()

        # Rebuild the table with new column headers
        self.table_container.destroy()
        self._build_asset_table(self.right_frame)

        self._log(f"🎯 Platform switched to {display_name}")

    def _on_freepik_ai_toggle(self):
        """Show/hide Freepik model dropdown based on AI Generated checkbox."""
        if self.freepik_ai_var.get():
            self.freepik_model_label.pack(padx=0, pady=(2, 1), anchor="w")
            self.freepik_model_dropdown.pack(padx=0, pady=(0, 2), anchor="w")
        else:
            self.freepik_model_label.pack_forget()
            self.freepik_model_dropdown.pack_forget()


    # ─── ASSET TABLE ─────────────────────────────────────────────────────────────

    def _build_asset_table(self, parent):
        """Build the scrollable asset table with proper aligned columns and borders."""
        container = ctk.CTkFrame(
            parent, fg_color=COLORS["bg_dark"], corner_radius=12,
            border_width=1, border_color=COLORS["border"]
        )
        container.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        container.grid_rowconfigure(1, weight=1)
        container.grid_columnconfigure(0, weight=1)
        self.table_container = container

        # Column config: (name, width_px, weight, anchor)
        # Fixed widths for No, Preview, Filename, Cat; flexible for Title & Keywords
        if self.current_platform == "freepik":
            self.col_config = [
                ("#",        36,  0, ""),
                ("Preview",  64,  0, ""),
                ("Filename", 120, 0, ""),
                ("Title",    0,   2, ""),
                ("Keywords", 0,   3, ""),
                ("Prompt",   0,   2, ""),
                ("Model",    120, 0, ""),
            ]
        elif self.current_platform == "shutterstock":
            self.col_config = [
                ("#",            36,  0, ""),
                ("Preview",      74,  0, ""),
                ("Filename",     150, 0, ""),
                ("Description",  0,   3, ""),
                ("Keywords",     0,   4, ""),
                ("Categories",   200, 0, ""),
            ]
        else:
            self.col_config = [
                ("#",        36,  0, ""),
                ("Preview",  74,  0, ""),
                ("Filename", 150, 0, ""),
                ("Title",    0,   3, ""),
                ("Keywords", 0,   4, ""),
                ("Category", 160, 0, ""),
            ]

        # ── Table Header ──────────────────────────────────────────────
        header = ctk.CTkFrame(container, fg_color=COLORS["table_header"], height=40, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)

        for i, (name, width_px, weight, anchor) in enumerate(self.col_config):
            header.grid_columnconfigure(i, weight=weight, minsize=width_px if width_px else 100)

            # Create a cell frame with left border separator
            cell = ctk.CTkFrame(header, fg_color="transparent", corner_radius=0)
            cell.grid(row=0, column=i, sticky="nsew")
            cell.grid_rowconfigure(0, weight=1)
            cell.grid_columnconfigure(0, weight=1)

            # Left border (separator) for columns after the first
            if i > 0:
                sep = ctk.CTkFrame(cell, fg_color=COLORS["table_border"], width=1, corner_radius=0)
                sep.place(x=0, rely=0.1, relheight=0.8, anchor="nw")

            lbl = ctk.CTkLabel(
                cell, text=name,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COLORS["neon_blue"]
            )
            lbl.grid(row=0, column=0, padx=8, pady=8, sticky=anchor if anchor else "")

        # Bottom border for header
        header_border = ctk.CTkFrame(container, fg_color=COLORS["neon_blue"], height=1, corner_radius=0)
        header_border.grid(row=0, column=0, sticky="sew")

        # ── Scrollable Body ───────────────────────────────────────────
        self.asset_scroll = ctk.CTkScrollableFrame(
            container, fg_color=COLORS["bg_dark"],
            scrollbar_button_color=COLORS["accent_blue"],
            scrollbar_button_hover_color=COLORS["neon_blue"]
        )
        self.asset_scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)

        for i, (_, width_px, weight, _) in enumerate(self.col_config):
            self.asset_scroll.grid_columnconfigure(i, weight=weight, minsize=width_px if width_px else 100)

        # Empty state
        self.empty_label = ctk.CTkLabel(
            self.asset_scroll,
            text="📁  No assets loaded\nClick 'Browse Files' to add images, vectors, or videos",
            font=ctk.CTkFont(size=14), text_color=COLORS["text_muted"], justify="center"
        )
        self.empty_label.grid(row=0, column=0, columnspan=len(self.col_config), pady=80)

    # ─── LOG PANEL with toggle ───────────────────────────────────────────────────

    def _build_log_toggle(self, parent):
        """Build the clickable bar to toggle log panel visibility."""
        self.log_toggle_bar = ctk.CTkFrame(
            parent, fg_color=COLORS["table_header"], height=28, corner_radius=6,
            cursor="hand2"
        )
        self.log_toggle_bar.grid(row=1, column=0, sticky="ew", pady=(2, 2))
        self.log_toggle_bar.grid_columnconfigure(1, weight=1)

        self.log_toggle_arrow = ctk.CTkLabel(
            self.log_toggle_bar, text="▼",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["neon_blue"]
        )
        self.log_toggle_arrow.grid(row=0, column=0, padx=(10, 4), pady=4)

        self.log_toggle_label = ctk.CTkLabel(
            self.log_toggle_bar, text="Processing Log",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["neon_blue"]
        )
        self.log_toggle_label.grid(row=0, column=1, sticky="w", pady=4)

        self.progress_label = ctk.CTkLabel(
            self.log_toggle_bar, text="", font=ctk.CTkFont(size=10),
            text_color=COLORS["text_secondary"]
        )
        self.progress_label.grid(row=0, column=2, padx=8, pady=4, sticky="e")

        # Bind click to all widgets in the toggle bar
        for widget in [self.log_toggle_bar, self.log_toggle_arrow, self.log_toggle_label]:
            widget.bind("<Button-1>", lambda e: self._toggle_log())

    def _build_log_panel(self, parent):
        """Build the processing log panel."""
        self.log_container = ctk.CTkFrame(
            parent, fg_color=COLORS["bg_dark"], corner_radius=10,
            border_width=1, border_color=COLORS["border"]
        )
        self.log_container.grid(row=2, column=0, sticky="nsew")
        self.log_container.grid_rowconfigure(1, weight=1)
        self.log_container.grid_columnconfigure(0, weight=1)

        # Header with clear button
        log_header = ctk.CTkFrame(self.log_container, fg_color=COLORS["table_header"], corner_radius=0, height=30)
        log_header.grid(row=0, column=0, sticky="ew")
        log_header.grid_propagate(False)

        ctk.CTkButton(
            log_header, text="🗑 Clear Log", command=self._clear_log,
            fg_color=COLORS["error"], hover_color=COLORS["stop_red"],
            text_color="white", font=ctk.CTkFont(size=10, weight="bold"),
            width=80, height=24, corner_radius=4
        ).pack(side="right", padx=8, pady=3)

        self.log_text = ctk.CTkTextbox(
            self.log_container, fg_color=COLORS["bg_darkest"], text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family="Consolas", size=11),
            border_width=0, scrollbar_button_color=COLORS["accent_blue"], wrap="word"
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=3, pady=3)
        self.log_text.configure(state="disabled")

    def _toggle_log(self):
        """Toggle log panel visibility — expand table when log is hidden."""
        if self.log_visible:
            self.log_container.grid_forget()
            self.log_toggle_arrow.configure(text="▶")
            self.right_frame.grid_rowconfigure(2, weight=0)
            self.right_frame.grid_rowconfigure(0, weight=1)
            self.log_visible = False
        else:
            self.log_container.grid(row=2, column=0, sticky="nsew")
            self.log_toggle_arrow.configure(text="▼")
            self.right_frame.grid_rowconfigure(2, weight=1)
            self.right_frame.grid_rowconfigure(0, weight=3)
            self.log_visible = True

    def _clear_log(self):
        """Clear all text from the processing log."""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ─── Helpers ──────────────────────────────────────────────────────────────────

    def _section_label(self, parent, text):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=13, weight="bold"), text_color=COLORS["text_primary"]
        ).pack(padx=16, pady=(8, 4), anchor="w")

    def _field_label(self, parent, text):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]
        ).pack(padx=16, pady=(0, 2), anchor="w")

    # ═════════════════════════════════════════════════════════════════════════════
    # EVENT HANDLERS
    # ═════════════════════════════════════════════════════════════════════════════

    def _on_provider_changed(self, provider_name, popup_model_dropdown=None, popup_api_entry=None):
        """Handle provider change — update models and swap API key."""
        # Save current API key for the PREVIOUS provider before switching
        if popup_api_entry:
            old_key = popup_api_entry.get().strip()
        else:
            old_key = self.api_key_entry.get().strip()

        if hasattr(self, '_last_provider') and self._last_provider:
            if old_key:
                self.api_keys[self._last_provider] = old_key

        # Update models dropdown
        models = get_models_for_provider(provider_name)
        target_dropdown = popup_model_dropdown or self.model_dropdown
        if target_dropdown:
            target_dropdown.configure(values=models)
        if models:
            self.model_var.set(models[0])
        else:
            self.model_var.set("")

        # Swap API key for the new provider
        target_entry = popup_api_entry or self.api_key_entry
        target_entry.delete(0, "end")
        new_key = self.api_keys.get(provider_name, "")
        if new_key:
            target_entry.insert(0, new_key)

        # Track current provider for next switch
        self._last_provider = provider_name

    def _toggle_api_key_visibility(self):
        self.api_key_entry.configure(show="" if self.show_key_var.get() else "•")

    def _on_drop_files(self, event):
        """Handle drag-and-drop files onto the drop zone."""
        # Reset drop zone visual
        self.drop_frame.configure(
            border_color=COLORS["border"], fg_color=COLORS["bg_input"])

        # Parse dropped file paths (tkinterdnd2 format)
        raw = event.data
        files = []
        # Handle paths with spaces enclosed in {}
        if '{' in raw:
            import re
            files = re.findall(r'\{([^}]+)\}', raw)
            # Also get non-braced parts
            remaining = re.sub(r'\{[^}]+\}', '', raw).strip()
            if remaining:
                files.extend(remaining.split())
        else:
            files = raw.split()

        if files:
            self._add_assets(files)

    def _open_settings_popup(self):
        """Open a popup dialog for AI Provider Settings."""
        popup = ctk.CTkToplevel(self)
        popup.title("⚙️ AI Provider Settings")
        popup.geometry("420x440")
        popup.resizable(False, False)
        popup.configure(fg_color=COLORS["bg_dark"])
        popup.transient(self)
        popup.grab_set()

        # Center popup on main window
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 420) // 2
        y = self.winfo_y() + (self.winfo_height() - 440) // 2
        popup.geometry(f"+{x}+{y}")

        # Title
        ctk.CTkLabel(
            popup, text="⚙️  AI Provider Settings",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=COLORS["neon_blue"]
        ).pack(pady=(20, 16))

        # Content frame
        content = ctk.CTkFrame(popup, fg_color=COLORS["bg_card"], corner_radius=12,
                                border_width=1, border_color=COLORS["border"])
        content.pack(padx=24, pady=(0, 16), fill="x")

        # Provider
        self._field_label(content, "Provider")
        popup_provider_var = ctk.StringVar(value=self.provider_var.get())
        popup_provider = ctk.CTkComboBox(
            content, values=get_provider_names(), variable=popup_provider_var,
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            button_color=COLORS["accent_blue"], button_hover_color=COLORS["neon_blue"],
            dropdown_fg_color=COLORS["bg_card"], dropdown_hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"], font=ctk.CTkFont(size=13), width=360, height=32
        )
        popup_provider.pack(padx=16, pady=(0, 8))

        # Model
        self._field_label(content, "Model")
        current_models = get_models_for_provider(self.provider_var.get())
        popup_model_var = ctk.StringVar(value=self.model_var.get())
        popup_model = ctk.CTkComboBox(
            content, values=current_models, variable=popup_model_var,
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            button_color=COLORS["accent_blue"], button_hover_color=COLORS["neon_blue"],
            dropdown_fg_color=COLORS["bg_card"], dropdown_hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"], font=ctk.CTkFont(size=12), width=360, height=32
        )
        popup_model.pack(padx=16, pady=(0, 8))

        # API Key
        self._field_label(content, "API Key")
        popup_api = ctk.CTkEntry(
            content, placeholder_text="Enter your API key...", show="•",
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            text_color=COLORS["text_primary"], placeholder_text_color=COLORS["text_muted"],
            font=ctk.CTkFont(size=13), width=360, height=32
        )
        popup_api.pack(padx=16, pady=(0, 4))

        # Load current key
        current_key = self.api_keys.get(self.provider_var.get(), "")
        if not current_key:
            current_key = self.api_key_entry.get().strip()
        if current_key:
            popup_api.insert(0, current_key)

        # Show key checkbox
        popup_show_var = ctk.BooleanVar(value=False)
        def toggle_popup_key():
            popup_api.configure(show="" if popup_show_var.get() else "•")

        ctk.CTkCheckBox(
            content, text="Show API Key", variable=popup_show_var,
            command=toggle_popup_key,
            font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"],
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            border_color=COLORS["border"], height=22
        ).pack(padx=16, pady=(0, 12), anchor="w")

        # Provider change handler for popup
        def on_popup_provider_change(name):
            self._on_provider_changed(name, popup_model_dropdown=popup_model, popup_api_entry=popup_api)
            popup_provider_var.set(name)
            popup_model_var.set(self.model_var.get())

        popup_provider.configure(command=on_popup_provider_change)

        # Buttons
        btn_frame = ctk.CTkFrame(popup, fg_color="transparent")
        btn_frame.pack(padx=24, pady=(0, 20), fill="x")

        def save_and_close():
            # Save provider
            provider = popup_provider_var.get()
            self.provider_var.set(provider)

            # Save model
            model = popup_model_var.get()
            self.model_var.set(model)

            # Save API key
            key = popup_api.get().strip()
            self.api_key_entry.delete(0, "end")
            if key:
                self.api_key_entry.insert(0, key)
                self.api_keys[provider] = key

            self._last_provider = provider
            self._save_settings()
            popup.destroy()
            self._show_toast("✅ API Key berhasil disimpan!")

        ctk.CTkButton(
            btn_frame, text="💾  Save Settings", command=save_and_close,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white", font=ctk.CTkFont(size=14, weight="bold"),
            width=200, height=40, corner_radius=10
        ).pack(side="left", expand=True, padx=(0, 6))

        ctk.CTkButton(
            btn_frame, text="Cancel", command=popup.destroy,
            fg_color=COLORS["bg_card"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"], border_width=1, border_color=COLORS["border"],
            font=ctk.CTkFont(size=13), width=120, height=40, corner_radius=10
        ).pack(side="right")

    def _browse_files(self):
        filetypes = [
            ("All Supported", "*.jpg *.jpeg *.png *.eps *.svg *.mp4 *.mov"),
            ("Images", "*.jpg *.jpeg *.png"),
            ("Vectors", "*.eps *.svg"),
            ("Videos", "*.mp4 *.mov"),
        ]
        files = filedialog.askopenfilenames(title="Select Assets", filetypes=filetypes)
        if files:
            self._add_assets(files)

    # ─── Asset Management ────────────────────────────────────────────────────────

    def _add_assets(self, file_paths):
        """Add selected files as assets with progress popup."""
        file_paths = list(file_paths)
        total = len(file_paths)
        if total == 0:
            return

        self.empty_label.grid_forget()

        # Create progress popup
        progress_popup = ctk.CTkToplevel(self)
        progress_popup.title("Uploading Assets...")
        progress_popup.geometry("400x200")
        progress_popup.resizable(False, False)
        progress_popup.configure(fg_color=COLORS["bg_dark"])
        progress_popup.transient(self)
        progress_popup.grab_set()
        progress_popup.protocol("WM_DELETE_WINDOW", lambda: None)  # Prevent closing

        # Center on main window
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 400) // 2
        y = self.winfo_y() + (self.winfo_height() - 200) // 2
        progress_popup.geometry(f"+{x}+{y}")

        # Glow line
        ctk.CTkFrame(progress_popup, fg_color=COLORS["neon_blue"], height=3, corner_radius=0).pack(fill="x")

        # Card
        card = ctk.CTkFrame(progress_popup, fg_color=COLORS["bg_card"], corner_radius=12,
                            border_width=1, border_color=COLORS["border"])
        card.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(
            card, text="📂  Uploading Assets",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=COLORS["neon_blue"]
        ).pack(pady=(16, 8))

        progress_text = ctk.CTkLabel(
            card, text=f"0 / {total} assets",
            font=ctk.CTkFont(size=13), text_color=COLORS["text_primary"]
        )
        progress_text.pack(pady=(0, 8))

        progress_bar = ctk.CTkProgressBar(
            card, width=320, height=12, corner_radius=6,
            fg_color=COLORS["bg_input"], progress_color=COLORS["neon_blue"]
        )
        progress_bar.set(0)
        progress_bar.pack(pady=(0, 8))

        status_text = ctk.CTkLabel(
            card, text="Preparing...",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"]
        )
        status_text.pack(pady=(0, 12))

        def _process_assets():
            for i, file_path in enumerate(file_paths):
                file_type = get_file_type(file_path)
                if file_type is None:
                    self._log(f"⚠ Skipped unsupported: {os.path.basename(file_path)}")
                    self.after(0, lambda idx=i: _update_progress(idx + 1, True))
                    continue

                filename = os.path.basename(file_path)

                try:
                    raw_img = load_preview_image(file_path, file_type, size=PREVIEW_SIZE)
                    if raw_img is not None:
                        preview_img = compress_preview(raw_img)
                    else:
                        preview_img = None
                except Exception as e:
                    self._log(f"⚠ Preview error ({filename}): {type(e).__name__}: {e}")
                    preview_img = None

                asset_id = db.add_asset(file_path, file_type, "", filename)

                # UI updates must happen on main thread
                self.after(0, lambda aid=asset_id, fn=filename, ft=file_type, pi=preview_img, idx=i:
                    _add_row_and_update(aid, fn, ft, pi, idx + 1))

            # Close popup when done
            self.after(100, _finish_upload)

        def _update_progress(current, skip=False):
            try:
                progress_bar.set(current / total)
                progress_text.configure(text=f"{current} / {total} assets")
                if skip:
                    status_text.configure(text="Skipped unsupported file...")
            except Exception:
                pass

        def _add_row_and_update(asset_id, filename, file_type, preview_img, current):
            self._create_table_row(asset_id, filename, file_type, preview_img)
            self._log(f"📁 Added: {filename} ({file_type})")
            _update_progress(current)
            status_text.configure(text=f"Loading: {filename}")

        def _finish_upload():
            self._update_counter()
            try:
                progress_popup.grab_release()
                progress_popup.destroy()
            except Exception:
                pass
            self._show_toast(f"✅ {total} assets berhasil di-upload!")

        # Run in background thread
        threading.Thread(target=_process_assets, daemon=True).start()

    def _create_table_row(self, asset_id, filename, file_type, preview_img):
        """Create a properly aligned table row using a single row-frame with internal grid."""
        self.card_row_counter += 1
        row_idx = self.card_row_counter
        row_bg = COLORS["table_row_even"] if row_idx % 2 == 0 else COLORS["table_row_odd"]

        ROW_H = 60  # fixed row height for alignment

        # ── Row container: one frame spanning all columns ──
        row_frame = ctk.CTkFrame(
            self.asset_scroll, fg_color=row_bg, corner_radius=0, height=ROW_H
        )
        row_frame.grid(row=row_idx, column=0, columnspan=len(self.col_config), sticky="nsew", pady=(0, 1))
        row_frame.grid_propagate(False)
        row_frame.grid_rowconfigure(0, weight=1)

        # Apply the same column config to the row frame
        for i, (_, width_px, weight, _) in enumerate(self.col_config):
            row_frame.grid_columnconfigure(i, weight=weight, minsize=width_px if width_px else 100)

        # Col 0: Row number (centered)
        no_cell = ctk.CTkFrame(row_frame, fg_color="transparent", corner_radius=0)
        no_cell.grid(row=0, column=0, sticky="nsew")
        no_cell.grid_rowconfigure(0, weight=1)
        no_cell.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            no_cell, text=str(row_idx),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["neon_blue"]
        ).grid(row=0, column=0, sticky="")
        # Right border
        ctk.CTkFrame(no_cell, fg_color=COLORS["table_border"], width=1, corner_radius=0).place(
            relx=1.0, rely=0.05, relheight=0.9, anchor="ne")

        # Col 1: Preview image (centered)
        preview_cell = ctk.CTkFrame(row_frame, fg_color="transparent", corner_radius=0)
        preview_cell.grid(row=0, column=1, sticky="nsew")
        preview_cell.grid_rowconfigure(0, weight=1)
        preview_cell.grid_columnconfigure(0, weight=1)

        if preview_img:
            photo = ctk.CTkImage(light_image=preview_img, dark_image=preview_img, size=preview_img.size)
            self.preview_images[asset_id] = photo
            preview_widget = ctk.CTkLabel(preview_cell, image=photo, text="", fg_color="transparent")
        else:
            preview_widget = ctk.CTkLabel(
                preview_cell, text="🖼", font=ctk.CTkFont(size=22), fg_color="transparent"
            )
        preview_widget.grid(row=0, column=0, padx=4, pady=4, sticky="")
        # Right border
        ctk.CTkFrame(preview_cell, fg_color=COLORS["table_border"], width=1, corner_radius=0).place(
            relx=1.0, rely=0.05, relheight=0.9, anchor="ne")

        # Col 2: Filename + type badge
        name_cell = ctk.CTkFrame(row_frame, fg_color="transparent", corner_radius=0)
        name_cell.grid(row=0, column=2, sticky="nsew")
        name_cell.grid_rowconfigure(0, weight=1)
        name_cell.grid_columnconfigure(0, weight=1)

        name_inner = ctk.CTkFrame(name_cell, fg_color="transparent")
        name_inner.grid(row=0, column=0, sticky="w", padx=6)

        type_colors = {
            "image": COLORS["neon_blue"],
            "vector": COLORS["accent_purple"],
            "video": COLORS["warning"]
        }
        ctk.CTkLabel(
            name_inner, text=f" {file_type.upper()} ",
            font=ctk.CTkFont(size=8, weight="bold"),
            text_color="white", fg_color=type_colors.get(file_type, COLORS["text_muted"]),
            corner_radius=3
        ).pack(anchor="w", padx=2, pady=(2, 1))
        ctk.CTkLabel(
            name_inner, text=filename,
            font=ctk.CTkFont(size=10), text_color=COLORS["text_primary"],
            wraplength=130, justify="left"
        ).pack(anchor="w", padx=2, pady=(0, 2))
        # Right border
        ctk.CTkFrame(name_cell, fg_color=COLORS["table_border"], width=1, corner_radius=0).place(
            relx=1.0, rely=0.05, relheight=0.9, anchor="ne")

        # Col 3: Title (editable textbox)
        title_cell = ctk.CTkFrame(row_frame, fg_color="transparent", corner_radius=0)
        title_cell.grid(row=0, column=3, sticky="nsew")
        title_cell.grid_rowconfigure(0, weight=1)
        title_cell.grid_columnconfigure(0, weight=1)

        title_entry = ctk.CTkTextbox(
            title_cell, fg_color=COLORS["bg_input"],
            text_color=COLORS["text_primary"], font=ctk.CTkFont(size=11),
            border_width=1, border_color=COLORS["table_border"],
            height=ROW_H - 8, wrap="word", corner_radius=0
        )
        title_entry.grid(row=0, column=0, padx=(2, 1), pady=3, sticky="nsew")
        # Right border
        ctk.CTkFrame(title_cell, fg_color=COLORS["table_border"], width=1, corner_radius=0).place(
            relx=1.0, rely=0.05, relheight=0.9, anchor="ne")

        # Col 4: Keywords (editable textbox)
        kw_cell = ctk.CTkFrame(row_frame, fg_color="transparent", corner_radius=0)
        kw_cell.grid(row=0, column=4, sticky="nsew")
        kw_cell.grid_rowconfigure(0, weight=1)
        kw_cell.grid_columnconfigure(0, weight=1)

        keywords_entry = ctk.CTkTextbox(
            kw_cell, fg_color=COLORS["bg_input"],
            text_color=COLORS["text_primary"], font=ctk.CTkFont(size=11),
            border_width=1, border_color=COLORS["table_border"],
            height=ROW_H - 8, wrap="word", corner_radius=0
        )
        keywords_entry.grid(row=0, column=0, padx=(2, 1), pady=3, sticky="nsew")
        # Right border
        ctk.CTkFrame(kw_cell, fg_color=COLORS["table_border"], width=1, corner_radius=0).place(
            relx=1.0, rely=0.05, relheight=0.9, anchor="ne")

        # Col 5: Category/Prompt (depends on platform)
        if self.current_platform == "freepik":
            # Col 5: Prompt (editable textbox)
            prompt_cell = ctk.CTkFrame(row_frame, fg_color="transparent", corner_radius=0)
            prompt_cell.grid(row=0, column=5, sticky="nsew")
            prompt_cell.grid_rowconfigure(0, weight=1)
            prompt_cell.grid_columnconfigure(0, weight=1)

            prompt_entry = ctk.CTkTextbox(
                prompt_cell, fg_color=COLORS["bg_input"],
                text_color=COLORS["text_primary"], font=ctk.CTkFont(size=11),
                border_width=1, border_color=COLORS["table_border"],
                height=ROW_H - 8, wrap="word", corner_radius=0
            )
            prompt_entry.grid(row=0, column=0, padx=(2, 1), pady=3, sticky="nsew")
            ctk.CTkFrame(prompt_cell, fg_color=COLORS["table_border"], width=1, corner_radius=0).place(
                relx=1.0, rely=0.05, relheight=0.9, anchor="ne")

            # Col 6: Model (centered entry)
            model_cell = ctk.CTkFrame(row_frame, fg_color="transparent", corner_radius=0)
            model_cell.grid(row=0, column=6, sticky="nsew")
            model_cell.grid_rowconfigure(0, weight=1)
            model_cell.grid_columnconfigure(0, weight=1)

            model_entry = ctk.CTkEntry(
                model_cell, fg_color=COLORS["bg_input"],
                text_color=COLORS["text_primary"], font=ctk.CTkFont(size=9),
                border_width=1, border_color=COLORS["table_border"],
                width=110, height=28, justify="center"
            )
            model_entry.grid(row=0, column=0, padx=2, pady=0, sticky="")
        else:
            prompt_entry = None
            model_entry = None

            # Col 5: Category (centered entry)
            cat_cell = ctk.CTkFrame(row_frame, fg_color="transparent", corner_radius=0)
            cat_cell.grid(row=0, column=5, sticky="nsew")
            cat_cell.grid_rowconfigure(0, weight=1)
            cat_cell.grid_columnconfigure(0, weight=1)

            cat_entry = ctk.CTkEntry(
                cat_cell, fg_color=COLORS["bg_input"],
                text_color=COLORS["text_primary"], font=ctk.CTkFont(size=11),
                border_width=1, border_color=COLORS["table_border"],
                width=140, height=28, justify="center"
            )
            cat_entry.grid(row=0, column=0, padx=4, pady=0, sticky="")

        # Bottom row border
        row_border = ctk.CTkFrame(
            row_frame, fg_color=COLORS["table_border"], height=1, corner_radius=0
        )
        row_border.place(relx=0, rely=1.0, relwidth=1.0, anchor="sw")

        # Store references
        if self.current_platform == "freepik":
            self.asset_cards[asset_id] = {
                "row_frame": row_frame,
                "title": title_entry,
                "keywords": keywords_entry,
                "prompt": prompt_entry,
                "model": model_entry,
                "category": None,
                "category_id": "",
                "row_idx": row_idx
            }
        else:
            cat_width = 180 if self.current_platform == "shutterstock" else 140
            cat_entry.configure(width=cat_width)
            self.asset_cards[asset_id] = {
                "row_frame": row_frame,
                "title": title_entry,
                "keywords": keywords_entry,
                "prompt": None,
                "model": None,
                "category": cat_entry,
                "category_id": "",
                "row_idx": row_idx
            }

    def _update_asset_card(self, asset_id, title, keywords, category, prompt=""):
        """Update an asset card with generated metadata."""
        card = self.asset_cards.get(asset_id)
        if not card:
            return
        card["title"].delete("1.0", "end")
        card["title"].insert("1.0", (title or "").strip())
        card["keywords"].delete("1.0", "end")
        card["keywords"].insert("1.0", (keywords or "").strip())

        if self.current_platform == "freepik":
            # Freepik: update prompt and model
            if card.get("prompt"):
                card["prompt"].delete("1.0", "end")
                card["prompt"].insert("1.0", (prompt or "").strip())
            if card.get("model"):
                card["model"].delete(0, "end")
                if self.freepik_ai_var.get():
                    card["model"].insert(0, self.freepik_model_var.get())
        else:
            # Store raw category for CSV export
            cat_raw = str(category).strip() if category else ""
            card["category_id"] = cat_raw

            # Display category name(s) in UI
            if self.current_platform == "shutterstock":
                cat_display = cat_raw
            else:
                cat_display = ""
                try:
                    cat_num = int(cat_raw)
                    cat_display = ADOBE_STOCK_CATEGORIES.get(cat_num, cat_raw)
                except (ValueError, TypeError):
                    cat_display = cat_raw
            if card.get("category"):
                card["category"].delete(0, "end")
                card["category"].insert(0, cat_display)

    # ─── Generate / Stop ─────────────────────────────────────────────────────────

    def _on_generate_click(self):
        if self.is_generating:
            self._stop_generation()
        else:
            self._start_generation()

    def _start_generation(self):
        api_key = self.api_key_entry.get().strip()
        if not api_key:
            messagebox.showwarning("API Key Required", "Please enter your API key.")
            return

        provider = self.provider_var.get()
        model = self.model_var.get()
        if not model:
            messagebox.showwarning("Model Required", "Please select a model.")
            return

        custom_prompt = self.custom_prompt_entry.get("1.0", "end-1c").strip()

        # Freepik: check AI Generated + model selection
        ai_generated = False
        freepik_model_name = ""
        if self.current_platform == "freepik" and self.freepik_ai_var.get():
            ai_generated = True
            freepik_model_name = self.freepik_model_var.get()
            if not freepik_model_name:
                messagebox.showwarning("Model Required", "Select a Freepik AI model.")
                return

        # Save settings before generating
        self._save_settings()

        assets = db.get_pending_assets()
        error_assets = [a for a in db.get_all_assets() if a["status"] == "error"]
        for ea in error_assets:
            db.update_status(ea["id"], "pending")
            if ea not in assets:
                assets.append(ea)

        if not assets:
            messagebox.showinfo("No Assets", "No pending assets to process.\nAdd files or clear and re-add.")
            return

        self.is_generating = True
        self.stop_event.clear()
        self.generate_btn.configure(text="⏹  Stop", fg_color=COLORS["stop_red"], hover_color="#cc1133")

        platform_names = {"adobestock": "Adobe Stock", "shutterstock": "Shutterstock", "freepik": "Freepik"}
        self._log("─" * 50)
        self._log(f"🚀 Starting metadata generation...")
        self._log(f"   Provider: {provider} | Model: {model}")
        self._log(f"   Platform: {platform_names.get(self.current_platform, self.current_platform)}")
        if self.current_platform == "freepik" and ai_generated:
            self._log(f"   AI Generated: Yes | Freepik Model: {freepik_model_name}")
        if custom_prompt:
            self._log(f"   Custom Prompt: {custom_prompt}")
        self._log(f"   Assets to process: {len(assets)}")
        self._log("─" * 50)

        self.generation_thread = threading.Thread(
            target=self._generation_worker,
            args=(assets, provider, model, api_key, custom_prompt, self.current_platform, ai_generated),
            daemon=True
        )
        self.generation_thread.start()

    def _generation_worker(self, assets, provider, model, api_key, custom_prompt="", platform="adobestock", ai_generated=False):
        def on_log(msg):
            self.after(0, self._log, msg)
        def on_progress(current, total):
            self.after(0, self._update_progress, current, total)
        def on_asset_done(asset_id, result):
            if result:
                self.after(0, self._update_asset_card, asset_id,
                           result["title"], result["keywords"], result.get("category", ""),
                           result.get("prompt", ""))
            self.after(0, self._update_counter)
            self.after(0, self._update_csv_button_state)

        process_all_assets(assets, provider, model, api_key,
                           self.stop_event, on_log, on_progress, on_asset_done,
                           custom_prompt=custom_prompt, platform=platform, ai_generated=ai_generated)
        self.after(0, self._reset_generate_button)

    def _stop_generation(self):
        self.stop_event.set()
        self._log("⏹ Stopping generation...")

    def _reset_generate_button(self):
        self.is_generating = False
        self.generate_btn.configure(
            text="🚀  Generate All",
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"]
        )

    # ─── Clear All ───────────────────────────────────────────────────────────────

    def _clear_all(self):
        if not self.asset_cards:
            return
        if self.is_generating:
            messagebox.showwarning("Busy", "Stop generation first.")
            return
        if not messagebox.askyesno("Clear All", "Remove all assets?"):
            return

        db.clear_all()

        for card in self.asset_cards.values():
            if "row_frame" in card:
                card["row_frame"].destroy()

        self.asset_cards.clear()
        self.preview_images.clear()
        self.card_row_counter = 0

        self.empty_label.grid(row=0, column=0, columnspan=len(self.col_config), pady=80)
        self._update_counter()
        self._update_csv_button_state()
        self._log("🗑 All assets cleared.")
        self.progress_label.configure(text="")

    # ─── Download CSV ────────────────────────────────────────────────────────────

    def _update_csv_button_state(self):
        """Enable/disable CSV download button based on whether any asset has metadata."""
        has_metadata = False
        for card in self.asset_cards.values():
            title = card["title"].get("1.0", "end-1c").strip()
            keywords = card["keywords"].get("1.0", "end-1c").strip()
            if title or keywords:
                has_metadata = True
                break

        if has_metadata:
            self.csv_btn.configure(
                state="normal",
                text_color=COLORS["text_primary"],
                border_color=COLORS["success"]
            )
        else:
            self.csv_btn.configure(
                state="disabled",
                text_color=COLORS["text_muted"],
                border_color=COLORS["border"]
            )

    def _download_csv(self):
        # Only export assets from the current session (cards visible in UI)
        if not self.asset_cards:
            messagebox.showinfo("No Data", "No assets to export.")
            return

        merged = []
        has_metadata = False
        for asset_id, card in self.asset_cards.items():
            # Get filename from DB for this asset
            asset = db.get_asset_by_id(asset_id)
            filename = asset["filename"] if asset else f"asset_{asset_id}"

            title = card["title"].get("1.0", "end-1c").strip()
            keywords = card["keywords"].get("1.0", "end-1c").strip()

            if title or keywords:
                has_metadata = True

            if self.current_platform == "freepik":
                prompt_text = card["prompt"].get("1.0", "end-1c").strip() if card.get("prompt") else ""
                model_text = card["model"].get().strip() if card.get("model") else ""
                merged.append({
                    "filename": filename,
                    "title": title,
                    "keywords": keywords,
                    "prompt": prompt_text,
                    "model": model_text
                })
            else:
                # Use stored category ID for CSV (not the display name)
                category = card.get("category_id", "").strip()
                merged.append({
                    "filename": filename,
                    "title": title,
                    "keywords": keywords,
                    "category": category
                })

        if not has_metadata:
            messagebox.showwarning("No Metadata", "Generate metadata first.")
            return

        desktop = str(pathlib.Path.home() / "Desktop")
        default_names = {
            "adobestock": "adobestock_metadata.csv",
            "shutterstock": "shutterstock_metadata.csv",
            "freepik": "freepik_metadata.csv"
        }
        default_name = default_names.get(self.current_platform, "metadata.csv")
        file_path = filedialog.asksaveasfilename(
            title="Save CSV", defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialdir=desktop, initialfile=default_name
        )
        if not file_path:
            return

        try:
            export_csv(merged, file_path, platform=self.current_platform)
            self._log(f"📥 CSV saved: {file_path}")
            platform_names = {"adobestock": "Adobe Stock", "shutterstock": "Shutterstock", "freepik": "Freepik"}
            platform_name = platform_names.get(self.current_platform, self.current_platform)
            messagebox.showinfo("CSV Exported", f"{platform_name} metadata exported!\n\nFile: {file_path}\nAssets: {len(merged)}")
        except Exception as e:
            self._log(f"❌ CSV error: {e}")
            messagebox.showerror("Export Error", str(e))

    # ─── Logging ─────────────────────────────────────────────────────────────────

    def _log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _update_progress(self, current, total):
        self.progress_label.configure(text=f"Progress: {current}/{total}")

    def _update_counter(self):
        all_assets = db.get_all_assets()
        done = sum(1 for a in all_assets if a["status"] == "done")
        self.counter_label.configure(text=f"Assets: {len(all_assets)}  |  Done: {done}")

    def _show_toast(self, message, duration=2500):
        """Show a brief toast notification at the top of the window."""
        toast = ctk.CTkFrame(
            self, fg_color=COLORS["bg_card"], corner_radius=10,
            border_width=1, border_color=COLORS["success"]
        )
        toast.place(relx=0.5, y=10, anchor="n")

        ctk.CTkLabel(
            toast, text=message,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["success"]
        ).pack(padx=20, pady=10)

        # Animate fade out
        def _remove():
            try:
                toast.destroy()
            except Exception:
                pass

        self.after(duration, _remove)


# ═════════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = RZAutomedata()
    app.mainloop()
