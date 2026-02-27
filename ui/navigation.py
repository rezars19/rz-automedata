"""
RZ Automedata - Navigation Bar UI
Narrow icon sidebar on the far left for switching between pages:
  - Metadata Generator
  - Keyword Research
  - Prompt Generator
  - Media Upscaler
  - Abstract Video
Mixed into the main RZAutomedata class.
"""

import customtkinter as ctk

from ui.theme import COLORS


class NavigationMixin:
    """Mixin that adds the left navigation icon bar to switch between pages."""

    def _build_navigation(self, parent):
        """Build the narrow icon navigation bar on the far left."""
        self.nav_bar = ctk.CTkFrame(
            parent, fg_color=COLORS["bg_dark"], corner_radius=0,
            border_width=0, width=80
        )
        self.nav_bar.grid(row=0, column=0, sticky="nsew")
        self.nav_bar.grid_propagate(False)

        # Right border glow
        glow = ctk.CTkFrame(self.nav_bar, fg_color=COLORS["border"], width=1, corner_radius=0)
        glow.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")

        # Nav items container (centered vertically at top)
        nav_items = ctk.CTkFrame(self.nav_bar, fg_color="transparent")
        nav_items.pack(fill="x", padx=6, pady=(12, 0))

        # ── Metadata button ──
        self.nav_metadata_btn = ctk.CTkButton(
            nav_items, text="📋", width=56, height=56, corner_radius=12,
            font=ctk.CTkFont(size=24),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["neon_blue"],
            command=lambda: self._switch_page("metadata")
        )
        self.nav_metadata_btn.pack(padx=6, pady=(0, 3))

        self.nav_metadata_label = ctk.CTkLabel(
            nav_items, text="Metadata",
            font=ctk.CTkFont(size=10, weight="bold"), text_color="#FFFFFF"
        )
        self.nav_metadata_label.pack(pady=(0, 8))

        # ── Keyword Research button ──
        self.nav_keyword_btn = ctk.CTkButton(
            nav_items, text="🔍", width=56, height=56, corner_radius=12,
            font=ctk.CTkFont(size=24),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1, border_color=COLORS["border"],
            command=lambda: self._switch_page("keyword_research")
        )
        self.nav_keyword_btn.pack(padx=6, pady=(0, 3))

        self.nav_keyword_label = ctk.CTkLabel(
            nav_items, text="Research",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=COLORS["text_secondary"]
        )
        self.nav_keyword_label.pack(pady=(0, 8))

        # ── Prompt Generator button ──
        self.nav_prompt_btn = ctk.CTkButton(
            nav_items, text="✨", width=56, height=56, corner_radius=12,
            font=ctk.CTkFont(size=24),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1, border_color=COLORS["border"],
            command=lambda: self._switch_page("prompt_generator")
        )
        self.nav_prompt_btn.pack(padx=6, pady=(0, 3))

        self.nav_prompt_label = ctk.CTkLabel(
            nav_items, text="Prompt",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=COLORS["text_secondary"]
        )
        self.nav_prompt_label.pack(pady=(0, 8))

        # ── Upscaler button ──
        self.nav_upscaler_btn = ctk.CTkButton(
            nav_items, text="⚡", width=56, height=56, corner_radius=12,
            font=ctk.CTkFont(size=24),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1, border_color=COLORS["border"],
            command=lambda: self._switch_page("upscaler")
        )
        self.nav_upscaler_btn.pack(padx=6, pady=(0, 3))

        self.nav_upscaler_label = ctk.CTkLabel(
            nav_items, text="Upscaler",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=COLORS["text_secondary"]
        )
        self.nav_upscaler_label.pack(pady=(0, 8))

        # ── Abstract Video button ──
        self.nav_abstract_btn = ctk.CTkButton(
            nav_items, text="🎬", width=56, height=56, corner_radius=12,
            font=ctk.CTkFont(size=24),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1, border_color=COLORS["border"],
            command=lambda: self._switch_page("abstract_video")
        )
        self.nav_abstract_btn.pack(padx=6, pady=(0, 3))

        self.nav_abstract_label = ctk.CTkLabel(
            nav_items, text="Abstract",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=COLORS["text_secondary"]
        )
        self.nav_abstract_label.pack(pady=(0, 8))

        # Track current page
        self._current_page = "metadata"

        # All nav buttons and labels for easy iteration
        self._nav_items = {
            "metadata": (self.nav_metadata_btn, self.nav_metadata_label),
            "keyword_research": (self.nav_keyword_btn, self.nav_keyword_label),
            "prompt_generator": (self.nav_prompt_btn, self.nav_prompt_label),
            "upscaler": (self.nav_upscaler_btn, self.nav_upscaler_label),
            "abstract_video": (self.nav_abstract_btn, self.nav_abstract_label),
        }

        # All page frames for easy iteration (set after pages are built)
        self._page_frames = {}

    def _register_page_frame(self, page_name, frame):
        """Register a page frame for switching."""
        self._page_frames[page_name] = frame

    def _switch_page(self, page_name):
        """Switch between pages."""
        if page_name == self._current_page:
            return

        self._current_page = page_name

        # Hide all pages, show selected
        for name, frame in self._page_frames.items():
            if name == page_name:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_forget()

        # Update nav button styles
        for name, (btn, label) in self._nav_items.items():
            if name == page_name:
                btn.configure(fg_color=COLORS["accent_blue"], border_width=0)
                label.configure(text_color="#FFFFFF")
            else:
                btn.configure(
                    fg_color=COLORS["bg_card"],
                    border_width=1, border_color=COLORS["border"]
                )
                label.configure(text_color=COLORS["text_secondary"])

        # Update prompt generator provider info when switching to it
        if page_name == "prompt_generator" and hasattr(self, '_pg_update_provider_info'):
            self._pg_update_provider_info()
