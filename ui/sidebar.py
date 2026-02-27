"""
RZ Automedata - Sidebar UI
Builds the left sidebar: platform selector, upload zone, custom prompt, action buttons, settings popup.
Mixed into the main RZAutomedata class.
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox

from ui.theme import COLORS
from core.ai_providers import get_provider_names, get_models_for_provider, FREEPIK_MODELS

# Check drag-and-drop availability
try:
    from tkinterdnd2 import DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False


class SidebarMixin:
    """Mixin that adds sidebar-building and settings popup methods to the main app."""

    def _build_sidebar(self, parent):
        """Build left sidebar with settings and action buttons."""
        sidebar_outer = ctk.CTkFrame(
            parent, fg_color=COLORS["bg_dark"], corner_radius=12,
            border_width=1, border_color=COLORS["border"], width=290
        )
        sidebar_outer.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 12))
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

    # ─── Platform & Freepik handlers ──────────────────────────────────────────────

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
            import core.database as db
            db.clear_all()
            self._clear_tree()
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

    # ─── Provider & Settings ──────────────────────────────────────────────────────

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

    # ─── File Browser & Drag-Drop ─────────────────────────────────────────────────

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

    # ─── UI Helpers ───────────────────────────────────────────────────────────────

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
