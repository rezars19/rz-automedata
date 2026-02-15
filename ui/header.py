"""
RZ Automedata - Header UI
Builds the top header bar with app title, version badge, license info, and machine ID.
Mixed into the main RZAutomedata class.
"""

import customtkinter as ctk

from ui.theme import COLORS
from core.license_manager import CURRENT_VERSION, is_configured


class HeaderMixin:
    """Mixin that adds header-building methods to the main app."""

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
        self.after(1500, lambda: self._header_mid_btn.configure(text="📋"))
