"""
RZ Automedata - Asset Table & Log Panel UI (Treeview-based)
Uses ttk.Treeview for maximum performance and stability.
Supports thousands of rows without RecursionError.
Thumbnails are lazy-loaded for visible rows only (LRU capped at 300).
Mixed into the main RZAutomedata class.
"""

import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
from PIL import ImageTk, Image as PILImage
from concurrent.futures import ThreadPoolExecutor
import os

from ui.theme import COLORS, PREVIEW_SIZE, compress_preview
from core.ai_providers import ADOBE_STOCK_CATEGORIES
from core.metadata_processor import load_preview_image, get_file_type

_MAX_THUMBS = 300          # max thumbnail images kept in memory
_THUMB_WORKERS = 3         # background threads for thumbnail generation
_LOAD_DEBOUNCE_MS = 80     # debounce delay before loading thumbnails


class TableMixin:
    """Mixin that adds asset-table and log-panel methods to the main app."""

    # ─── ASSET TABLE ─────────────────────────────────────────────────────────────

    def _build_asset_table(self, parent):
        """Build the Treeview-based asset table with grid-line styling."""
        container = ctk.CTkFrame(
            parent, fg_color=COLORS["bg_dark"], corner_radius=12,
            border_width=1, border_color=COLORS["border"]
        )
        container.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        self.table_container = container

        # Column definitions for current platform
        self._configure_tree_columns()

        # Grid line color
        _grid = COLORS.get("table_border", "#1e2d6a")

        # ── ttk Style ──────────────────────────────────────────────────
        style = ttk.Style()
        style.theme_use("clam")

        # Layout: add cell borders via the 'clam' theme elements
        style.layout("Asset.Treeview", [
            ("Asset.Treeview.treearea", {"sticky": "nsew"})
        ])

        style.configure("Asset.Treeview",
            background=COLORS["bg_dark"],
            foreground=COLORS["text_primary"],
            fieldbackground=COLORS["bg_dark"],
            borderwidth=1,
            bordercolor=_grid,
            rowheight=52,
            indent=0,
            font=("Segoe UI", 11),
        )
        style.map("Asset.Treeview",
            background=[("selected", COLORS["accent_blue"])],
            foreground=[("selected", "white")],
        )

        # Header with bottom border
        style.configure("Asset.Treeview.Heading",
            background=COLORS["table_header"],
            foreground=COLORS["neon_blue"],
            borderwidth=1,
            bordercolor=_grid,
            font=("Segoe UI", 11, "bold"),
            relief="groove",
            padding=(8, 8),
        )
        style.map("Asset.Treeview.Heading",
            background=[("active", COLORS["bg_card"])],
            relief=[("active", "groove")],
        )

        # ── Inner frame ────────────────────────────────────────────────
        tree_frame = tk.Frame(container, bg=COLORS["bg_dark"], bd=0, highlightthickness=0)
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self._tree_frame = tree_frame  # Store reference for grid line updates

        # Create Treeview
        col_ids = [c[0] for c in self._tree_col_defs]
        self.tree = ttk.Treeview(
            tree_frame,
            columns=col_ids,
            show="tree headings",
            style="Asset.Treeview",
            selectmode="browse",
        )

        # #0 column — row number + preview thumbnail
        self.tree.column("#0", width=90, minwidth=70, stretch=False, anchor="center")
        self.tree.heading("#0", text="#", anchor="center")

        for col_id, heading, width, stretch in self._tree_col_defs:
            self.tree.column(col_id, width=width, minwidth=60, stretch=stretch, anchor="center")
            self.tree.heading(col_id, text=heading, anchor="center")

        # Alternating row tags with grid-line bottom border effect
        self.tree.tag_configure("even", background=COLORS["table_row_even"])
        self.tree.tag_configure("odd", background=COLORS["table_row_odd"])

        # Vertical scrollbar
        scrollbar = tk.Scrollbar(
            tree_frame, orient="vertical", command=self.tree.yview,
            bg=COLORS["accent_blue"], troughcolor=COLORS["bg_dark"],
            activebackground=COLORS["neon_blue"], width=14,
            relief="flat", bd=0, highlightthickness=0,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # ── Vertical grid lines between columns ──────────────────────
        self._col_separators = []
        self._grid_color = _grid
        self._grid_update_timer = None  # debounce timer for grid line redraws

        # Bind Configure to both treeview and parent frame for reliable redraw
        self.tree.bind("<Configure>", lambda e: self._schedule_grid_update(), add="+")
        tree_frame.bind("<Configure>", lambda e: self._schedule_grid_update(), add="+")
        # Initial draw after layout settles
        self.tree.after(200, self._update_grid_lines)

        # ── Empty state overlay ────────────────────────────────────────
        self.empty_label = ctk.CTkLabel(
            container,
            text="📁  No assets loaded\nClick 'Browse Files' to add images, vectors, or videos",
            font=ctk.CTkFont(size=14), text_color=COLORS["text_muted"], justify="center"
        )
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

        # ── Inline editing ─────────────────────────────────────────────
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self._edit_widget = None
        self._editing_in_progress = False

        # ── Reverse lookup: tree_item_id -> asset_id (O(1) for editing) ──
        self._item_to_asset = {}

        # ── Lazy thumbnail loading ─────────────────────────────────────
        self._thumb_pool = ThreadPoolExecutor(max_workers=_THUMB_WORKERS)
        self._thumb_pending = set()   # asset_ids currently loading
        self._thumb_loaded = set()    # asset_ids with loaded thumbnails
        self._thumb_timer = None      # debounce timer ID

        # Bind scroll events for lazy thumbnail loading (use add='+' to not overwrite)
        self.tree.bind("<Configure>", lambda e: self._schedule_thumb_load(), add="+")
        # Also trigger on mouse scroll / scrollbar drag
        def _on_scroll(*args):
            scrollbar.set(*args)
            self._schedule_thumb_load()
        self.tree.configure(yscrollcommand=_on_scroll)

        # Backward compat
        self.col_config = col_ids

    def _configure_tree_columns(self):
        """Set column definitions based on the current platform."""
        if self.current_platform == "freepik":
            self._tree_col_defs = [
                ("filename", "Filename", 140, False),
                ("title",    "Title",    220, True),
                ("keywords", "Keywords", 280, True),
                ("prompt",   "Prompt",   220, True),
                ("model",    "Model",    120, False),
            ]
            self._editable_cols = {"title", "keywords", "prompt", "model"}
            self._multiline_cols = {"title", "keywords", "prompt"}
        elif self.current_platform == "shutterstock":
            self._tree_col_defs = [
                ("filename",  "Filename",     150, False),
                ("title",     "Description",  280, True),
                ("keywords",  "Keywords",     320, True),
                ("category",  "Categories",   200, False),
            ]
            self._editable_cols = {"title", "keywords", "category"}
            self._multiline_cols = {"title", "keywords"}
        else:  # adobestock
            self._tree_col_defs = [
                ("filename", "Filename", 150, False),
                ("title",    "Title",    280, True),
                ("keywords", "Keywords", 320, True),
                ("category", "Category", 160, False),
            ]
            self._editable_cols = {"title", "keywords", "category"}
            self._multiline_cols = {"title", "keywords"}

    # ─── SCROLL FREEZE/THAW (no-op for Treeview) ─────────────────────────────────

    def _freeze_table_scroll(self):
        """No-op: Treeview handles bulk inserts natively."""
        pass

    def _thaw_table_scroll(self):
        """No-op: Treeview handles bulk inserts natively."""
        pass

    # ─── TABLE ROW ───────────────────────────────────────────────────────────────

    def _create_table_row(self, asset_id, filename, file_type, preview_img=None, file_path=""):
        """Insert a row into the Treeview. Thumbnail is lazy-loaded later."""
        self.card_row_counter += 1
        row_idx = self.card_row_counter
        tag = "even" if row_idx % 2 == 0 else "odd"

        self.empty_label.place_forget()

        # Type emoji badge
        type_badges = {"image": "📷", "vector": "🎨", "video": "🎬"}
        badge = type_badges.get(file_type, "📄")

        if self.current_platform == "freepik":
            values = (f"{badge} {filename}", "", "", "", "")
        else:
            values = (f"{badge} {filename}", "", "", "")

        kwargs = {"text": str(row_idx), "values": values, "tags": (tag,)}
        item_id = self.tree.insert("", "end", **kwargs)

        self.asset_cards[asset_id] = {
            "tree_item": item_id,
            "title": "",
            "keywords": "",
            "category": "",
            "category_id": "",
            "prompt": "",
            "model": "",
            "filename": filename,
            "file_type": file_type,
            "file_path": file_path,
            "row_idx": row_idx,
        }

        # Reverse lookup for O(1) inline editing
        self._item_to_asset[item_id] = asset_id

    # ─── UPDATE ASSET CARD ───────────────────────────────────────────────────────

    def _update_asset_card(self, asset_id, title, keywords, category, prompt=""):
        """Update an asset card with generated metadata."""
        card = self.asset_cards.get(asset_id)
        if not card:
            return

        item_id = card["tree_item"]
        title = (title or "").strip()
        keywords = (keywords or "").strip()
        card["title"] = title
        card["keywords"] = keywords

        type_badges = {"image": "📷", "vector": "🎨", "video": "🎬"}
        badge = type_badges.get(card.get("file_type", ""), "📄")

        if self.current_platform == "freepik":
            card["prompt"] = (prompt or "").strip()
            model_val = ""
            if hasattr(self, 'freepik_ai_var') and self.freepik_ai_var.get():
                model_val = self.freepik_model_var.get()
            card["model"] = model_val
            self.tree.item(item_id, values=(
                f"{badge} {card['filename']}", title, keywords,
                card["prompt"], model_val
            ))
        elif self.current_platform == "shutterstock":
            cat_raw = str(category).strip() if category else ""
            card["category_id"] = cat_raw
            card["category"] = cat_raw
            self.tree.item(item_id, values=(
                f"{badge} {card['filename']}", title, keywords, cat_raw
            ))
        else:
            cat_raw = str(category).strip() if category else ""
            card["category_id"] = cat_raw
            try:
                cat_num = int(cat_raw)
                cat_display = ADOBE_STOCK_CATEGORIES.get(cat_num, cat_raw)
            except (ValueError, TypeError):
                cat_display = cat_raw
            card["category"] = cat_display
            self.tree.item(item_id, values=(
                f"{badge} {card['filename']}", title, keywords, cat_display
            ))

    # ─── INLINE EDITING ──────────────────────────────────────────────────────────

    def _on_tree_double_click(self, event):
        """Double-click a cell to edit its contents."""
        self._cancel_edit()

        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        column = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return

        col_idx = int(column.replace("#", "")) - 1
        col_ids = [c[0] for c in self._tree_col_defs]
        if col_idx < 0 or col_idx >= len(col_ids):
            return

        col_name = col_ids[col_idx]
        if col_name not in self._editable_cols:
            return

        bbox = self.tree.bbox(item_id, column)
        if not bbox:
            return

        x, y, w, h = bbox
        values = self.tree.item(item_id, "values")
        current_value = values[col_idx] if col_idx < len(values) else ""

        # O(1) reverse lookup instead of linear scan
        asset_id = self._item_to_asset.get(item_id)
        if asset_id is None:
            return

        if col_name in self._multiline_cols:
            edit = tk.Text(
                self.tree, wrap="word", font=("Segoe UI", 11),
                bg=COLORS["bg_input"], fg=COLORS["text_primary"],
                insertbackground=COLORS["neon_blue"],
                selectbackground=COLORS["accent_blue"], selectforeground="white",
                relief="solid", borderwidth=2, highlightthickness=0,
            )
            edit.insert("1.0", current_value)
            edit.place(x=x, y=y, width=max(w, 200), height=max(h, 100))
            edit.focus_set()
            edit.bind("<Escape>", lambda e: self._cancel_edit())
            edit.bind("<FocusOut>", lambda e, a=asset_id, c=col_name:
                      self._finish_edit(a, c, edit, True))
            edit.bind("<Control-Return>", lambda e, a=asset_id, c=col_name:
                      self._finish_edit(a, c, edit, True))
        else:
            edit = tk.Entry(
                self.tree, font=("Segoe UI", 11),
                bg=COLORS["bg_input"], fg=COLORS["text_primary"],
                insertbackground=COLORS["neon_blue"],
                selectbackground=COLORS["accent_blue"], selectforeground="white",
                relief="solid", borderwidth=2, highlightthickness=0,
            )
            edit.insert(0, current_value)
            edit.place(x=x, y=y, width=max(w, 120), height=h)
            edit.focus_set()
            edit.select_range(0, "end")
            edit.bind("<Return>", lambda e, a=asset_id, c=col_name:
                      self._finish_edit(a, c, edit, False))
            edit.bind("<Escape>", lambda e: self._cancel_edit())
            edit.bind("<FocusOut>", lambda e, a=asset_id, c=col_name:
                      self._finish_edit(a, c, edit, False))

        self._edit_widget = edit

    def _finish_edit(self, asset_id, col_name, widget, is_text=False):
        """Save edited value back to data and refresh the row."""
        if self._editing_in_progress:
            return
        self._editing_in_progress = True

        try:
            if not widget.winfo_exists():
                return
            new_val = widget.get("1.0", "end-1c").strip() if is_text else widget.get().strip()
        except Exception:
            self._editing_in_progress = False
            self._cancel_edit()
            return

        card = self.asset_cards.get(asset_id)
        if card:
            card[col_name] = new_val
            if col_name == "category":
                card["category_id"] = new_val
            self._refresh_tree_item(asset_id)

        self._cancel_edit()
        self._editing_in_progress = False

    def _cancel_edit(self):
        """Destroy the current inline edit widget."""
        if self._edit_widget:
            try:
                self._edit_widget.destroy()
            except Exception:
                pass
            self._edit_widget = None

    def _refresh_tree_item(self, asset_id):
        """Refresh one Treeview row from its stored data dict."""
        card = self.asset_cards.get(asset_id)
        if not card:
            return

        item_id = card["tree_item"]
        type_badges = {"image": "📷", "vector": "🎨", "video": "🎬"}
        badge = type_badges.get(card.get("file_type", ""), "📄")

        if self.current_platform == "freepik":
            vals = (f"{badge} {card['filename']}", card.get("title", ""),
                    card.get("keywords", ""), card.get("prompt", ""),
                    card.get("model", ""))
        else:
            vals = (f"{badge} {card['filename']}", card.get("title", ""),
                    card.get("keywords", ""), card.get("category", ""))

        self.tree.item(item_id, values=vals)

    # ─── VERTICAL GRID LINES ────────────────────────────────────────────────────

    def _schedule_grid_update(self):
        """Debounced trigger for grid line redraw to prevent flickering on resize."""
        if self._grid_update_timer:
            try:
                self.after_cancel(self._grid_update_timer)
            except Exception:
                pass
        self._grid_update_timer = self.after(50, self._update_grid_lines)

    def _update_grid_lines(self):
        """Draw vertical separator lines between columns."""
        self._grid_update_timer = None

        # Clear existing separators
        for sep in self._col_separators:
            try:
                sep.destroy()
            except Exception:
                pass
        self._col_separators = []

        # Bail if tree not yet realized
        try:
            if not self.tree.winfo_exists() or self.tree.winfo_width() < 10:
                return
        except Exception:
            return

        grid_color = self._grid_color
        all_cols = ["#0"] + [c[0] for c in self._tree_col_defs]
        x_pos = 0

        for i in range(len(all_cols) - 1):  # no separator after last column
            col_width = self.tree.column(all_cols[i], "width")
            x_pos += col_width

            sep = tk.Frame(self.tree, bg=grid_color, width=1, cursor="")
            sep.place(x=x_pos - 1, y=0, width=1, relheight=1.0)
            # Prevent the separator from stealing focus or interaction
            sep.lower()

            # Forward mouse events through separator to the tree
            for ev in ("<Button-1>", "<Double-1>", "<ButtonRelease-1>",
                       "<Button-3>", "<MouseWheel>"):
                sep.bind(ev, self._forward_sep_event)

            self._col_separators.append(sep)

    def _forward_sep_event(self, event):
        """Forward mouse event from grid separator to Treeview."""
        try:
            tree_x = event.x_root - self.tree.winfo_rootx()
            tree_y = event.y_root - self.tree.winfo_rooty()
            # Generate same event on tree at the correct position
            event_map = {
                "4": "<Button-1>", "5": "<Double-1>",
                "6": "<ButtonRelease-1>", "7": "<Button-3>",
                "38": "<MouseWheel>",
            }
            ev_str = event_map.get(str(event.type), None)
            if ev_str:
                self.tree.event_generate(ev_str, x=tree_x, y=tree_y,
                                         rootx=event.x_root, rooty=event.y_root)
        except Exception:
            pass

    # ─── LAZY THUMBNAIL LOADING ───────────────────────────────────────────────────

    def _schedule_thumb_load(self):
        """Debounced trigger for lazy thumbnail loading."""
        if self._thumb_timer:
            self.after_cancel(self._thumb_timer)
        self._thumb_timer = self.after(_LOAD_DEBOUNCE_MS, self._load_visible_thumbnails)

    def _get_visible_items(self):
        """Get currently visible Treeview item IDs."""
        items = []
        try:
            tree_height = self.tree.winfo_height()
            # rowheight = 52 (set in style config)
            for y in range(0, tree_height, 52):
                item = self.tree.identify_row(y)
                if item and item not in items:
                    items.append(item)
        except Exception:
            pass
        return items

    def _load_visible_thumbnails(self):
        """Load thumbnails for visible rows that don't have them yet."""
        visible = self._get_visible_items()
        for item_id in visible:
            asset_id = self._item_to_asset.get(item_id)
            if not asset_id:
                continue
            if asset_id in self._thumb_loaded or asset_id in self._thumb_pending:
                continue
            card = self.asset_cards.get(asset_id)
            if not card:
                continue
            file_path = card.get("file_path", "")
            if not file_path or not os.path.exists(file_path):
                continue
            self._thumb_pending.add(asset_id)
            self._thumb_pool.submit(
                self._gen_and_apply_thumb, asset_id,
                file_path, card.get("file_type", "image")
            )

    def _gen_and_apply_thumb(self, asset_id, file_path, file_type):
        """Generate thumbnail in background thread, apply on main thread."""
        try:
            raw_img = load_preview_image(file_path, file_type, size=PREVIEW_SIZE)
            if raw_img is None:
                return
            preview = compress_preview(raw_img)
            if raw_img is not preview:
                try:
                    raw_img.close()
                except Exception:
                    pass

            # Create 40x40 thumbnail with dark background
            thumb = preview.copy()
            thumb.thumbnail((40, 40))
            bg = PILImage.new("RGB", (40, 40), (10, 14, 39))
            offset = ((40 - thumb.width) // 2, (40 - thumb.height) // 2)
            if thumb.mode == "RGBA":
                bg.paste(thumb, offset, mask=thumb.split()[3])
            else:
                bg.paste(thumb, offset)

            photo = ImageTk.PhotoImage(bg)

            # Close PIL images to free memory
            try:
                thumb.close()
                bg.close()
                preview.close()
            except Exception:
                pass

            # Apply on main thread
            self.after(0, lambda: self._apply_thumb(asset_id, photo))
        except Exception:
            pass
        finally:
            self._thumb_pending.discard(asset_id)

    def _apply_thumb(self, asset_id, photo):
        """Apply thumbnail to treeview item on main thread."""
        card = self.asset_cards.get(asset_id)
        if not card:
            return
        item_id = card["tree_item"]
        try:
            if self.tree.exists(item_id):
                self.preview_images[asset_id] = photo  # prevent GC
                self.tree.item(item_id, image=photo)
                self._thumb_loaded.add(asset_id)
        except Exception:
            pass
        # Evict old thumbnails if over memory limit
        self._evict_thumbnails()

    def _evict_thumbnails(self):
        """Remove offscreen thumbnails if over memory limit."""
        if len(self.preview_images) <= _MAX_THUMBS:
            return
        visible_items = set(self._get_visible_items())
        visible_assets = {self._item_to_asset.get(i) for i in visible_items}
        # Remove thumbnails that are not visible (keep 70% of max)
        target = int(_MAX_THUMBS * 0.7)
        to_remove = []
        for aid in list(self.preview_images.keys()):
            if aid not in visible_assets:
                to_remove.append(aid)
            if len(self.preview_images) - len(to_remove) <= target:
                break
        for aid in to_remove:
            del self.preview_images[aid]
            self._thumb_loaded.discard(aid)
            card = self.asset_cards.get(aid)
            if card:
                try:
                    self.tree.item(card["tree_item"], image="")
                except Exception:
                    pass

    # ─── CLEAR TREE ──────────────────────────────────────────────────────────────

    def _clear_tree(self):
        """Remove all Treeview items and reset state."""
        self._cancel_edit()
        if hasattr(self, 'tree') and self.tree.winfo_exists():
            self.tree.delete(*self.tree.get_children())
        self.asset_cards.clear()
        self.preview_images.clear()
        self._item_to_asset.clear()
        self._thumb_loaded.clear()
        self._thumb_pending.clear()
        self.card_row_counter = 0
        if hasattr(self, 'empty_label'):
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

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
        """Toggle log panel visibility."""
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
