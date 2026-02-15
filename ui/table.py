"""
RZ Automedata - Asset Table & Log Panel UI
Builds the scrollable asset table, table rows, log panel, and log toggle.
Mixed into the main RZAutomedata class.
"""

import customtkinter as ctk

from ui.theme import COLORS
from core.ai_providers import ADOBE_STOCK_CATEGORIES


class TableMixin:
    """Mixin that adds asset-table and log-panel methods to the main app."""

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

    # ─── TABLE ROW ───────────────────────────────────────────────────────────────

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

    # ─── UPDATE ASSET CARD ───────────────────────────────────────────────────────

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
