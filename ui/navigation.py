"""
RZ Automedata - Navigation Bar UI
Narrow icon sidebar on the far left for switching between pages:
  - Metadata Generator
  - Keyword Research
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
            border_width=0, width=60
        )
        self.nav_bar.grid(row=0, column=0, sticky="nsew")
        self.nav_bar.grid_propagate(False)

        # Right border glow
        glow = ctk.CTkFrame(self.nav_bar, fg_color=COLORS["border"], width=1, corner_radius=0)
        glow.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")

        # Nav items container (centered vertically at top)
        nav_items = ctk.CTkFrame(self.nav_bar, fg_color="transparent")
        nav_items.pack(fill="x", padx=4, pady=(12, 0))

        # ── Metadata button ──
        self.nav_metadata_btn = ctk.CTkButton(
            nav_items, text="📋", width=48, height=48, corner_radius=10,
            font=ctk.CTkFont(size=20),
            fg_color=COLORS["accent_blue"],
            hover_color=COLORS["neon_blue"],
            command=lambda: self._switch_page("metadata")
        )
        self.nav_metadata_btn.pack(padx=4, pady=(0, 4))

        self.nav_metadata_label = ctk.CTkLabel(
            nav_items, text="Metadata",
            font=ctk.CTkFont(size=8, weight="bold"), text_color=COLORS["neon_blue"]
        )
        self.nav_metadata_label.pack(pady=(0, 8))

        # ── Keyword Research button ──
        self.nav_keyword_btn = ctk.CTkButton(
            nav_items, text="🔍", width=48, height=48, corner_radius=10,
            font=ctk.CTkFont(size=20),
            fg_color=COLORS["bg_card"],
            hover_color=COLORS["bg_card_hover"],
            border_width=1, border_color=COLORS["border"],
            command=lambda: self._switch_page("keyword_research")
        )
        self.nav_keyword_btn.pack(padx=4, pady=(0, 4))

        self.nav_keyword_label = ctk.CTkLabel(
            nav_items, text="Research",
            font=ctk.CTkFont(size=8, weight="bold"), text_color=COLORS["text_muted"]
        )
        self.nav_keyword_label.pack(pady=(0, 8))

        # Track current page
        self._current_page = "metadata"

    def _switch_page(self, page_name):
        """Switch between Metadata and Keyword Research pages."""
        if page_name == self._current_page:
            return

        self._current_page = page_name

        if page_name == "metadata":
            # Show metadata page, hide keyword research
            self.metadata_page_frame.grid(row=0, column=0, sticky="nsew")
            if hasattr(self, 'kr_page_frame'):
                self.kr_page_frame.grid_forget()

            # Update nav button styles
            self.nav_metadata_btn.configure(
                fg_color=COLORS["accent_blue"],
                border_width=0
            )
            self.nav_metadata_label.configure(text_color=COLORS["neon_blue"])

            self.nav_keyword_btn.configure(
                fg_color=COLORS["bg_card"],
                border_width=1, border_color=COLORS["border"]
            )
            self.nav_keyword_label.configure(text_color=COLORS["text_muted"])

        elif page_name == "keyword_research":
            # Show keyword research page, hide metadata
            self.metadata_page_frame.grid_forget()
            if hasattr(self, 'kr_page_frame'):
                self.kr_page_frame.grid(row=0, column=0, sticky="nsew")

            # Update nav button styles
            self.nav_keyword_btn.configure(
                fg_color=COLORS["accent_blue"],
                border_width=0
            )
            self.nav_keyword_label.configure(text_color=COLORS["neon_blue"])

            self.nav_metadata_btn.configure(
                fg_color=COLORS["bg_card"],
                border_width=1, border_color=COLORS["border"]
            )
            self.nav_metadata_label.configure(text_color=COLORS["text_muted"])
