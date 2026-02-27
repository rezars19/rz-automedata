"""
RZ Automedata - Prompt Generator UI
Premium AI-powered prompt generator for microstock content.
Generates unique prompts for images, vectors, and videos.
Mixed into the main RZAutomedata class.
"""

import customtkinter as ctk
import threading

from ui.theme import COLORS
from core.prompt_generator import generate_prompts, VECTOR_STYLES


class PromptGeneratorMixin:
    """Mixin that adds prompt generator page methods to the main app."""

    def _build_prompt_generator_page(self, parent):
        """Build the prompt generator page."""
        self.pg_page_frame = ctk.CTkFrame(parent, fg_color="transparent")
        # Don't grid yet — controlled by nav

        self.pg_page_frame.grid_rowconfigure(0, weight=1)
        self.pg_page_frame.grid_columnconfigure(0, weight=1, minsize=300)  # Left config panel
        self.pg_page_frame.grid_columnconfigure(1, weight=3)  # Right results panel (wider)

        # ═══════════════════════════════════════════════════════════════════
        # LEFT PANEL — Configuration
        # ═══════════════════════════════════════════════════════════════════
        left_panel = ctk.CTkFrame(
            self.pg_page_frame, fg_color=COLORS["bg_dark"], corner_radius=12,
            border_width=1, border_color=COLORS["border"]
        )
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(8, 6), pady=(8, 12))

        # ── Header ──
        header = ctk.CTkFrame(left_panel, fg_color=COLORS["bg_card"], corner_radius=0, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="✨  AI Prompt Generator",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=COLORS["neon_blue"]
        ).pack(side="left", padx=16, pady=10)

        ctk.CTkFrame(left_panel, fg_color=COLORS["neon_blue"], height=1).pack(fill="x")

        # Scrollable config area
        config_scroll = ctk.CTkScrollableFrame(
            left_panel, fg_color="transparent",
            scrollbar_button_color=COLORS["accent_blue"],
            scrollbar_button_hover_color=COLORS["neon_blue"]
        )
        config_scroll.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Content Type Selector ──
        self._pg_field_label(config_scroll, "📁 Content Type")

        type_frame = ctk.CTkFrame(config_scroll, fg_color="transparent")
        type_frame.pack(fill="x", padx=12, pady=(0, 12))

        self.pg_type_var = ctk.StringVar(value="image")
        self._pg_type_buttons = {}

        content_types = [
            ("📷  Image", "image"),
            ("🎨  Vector", "vector"),
            ("🎬  Video", "video"),
        ]

        for label, value in content_types:
            btn = ctk.CTkButton(
                type_frame, text=label, height=36, corner_radius=8,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color=COLORS["accent_blue"] if value == "image" else COLORS["bg_input"],
                hover_color=COLORS["neon_blue"],
                text_color="white" if value == "image" else COLORS["text_secondary"],
                border_width=1, border_color=COLORS["border"],
                command=lambda v=value: self._pg_select_type(v)
            )
            btn.pack(fill="x", pady=2)
            self._pg_type_buttons[value] = btn

        # ── Vector Style Selector (hidden by default) ──
        self.pg_style_frame = ctk.CTkFrame(config_scroll, fg_color="transparent")

        self._pg_field_label(self.pg_style_frame, "🎨 Vector Style")

        self.pg_style_var = ctk.StringVar(value=VECTOR_STYLES[0])
        style_dropdown = ctk.CTkOptionMenu(
            self.pg_style_frame, variable=self.pg_style_var,
            values=VECTOR_STYLES,
            fg_color=COLORS["bg_input"], button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["neon_blue"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_card_hover"],
            dropdown_text_color=COLORS["text_primary"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=12), width=280, height=34
        )
        style_dropdown.pack(padx=12, pady=(0, 4))

        # Style preview label
        self.pg_style_preview = ctk.CTkLabel(
            self.pg_style_frame, text="",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"],
            wraplength=260
        )
        self.pg_style_preview.pack(padx=12, pady=(0, 8))

        # ── Keyword Input ──
        self._pg_field_label(config_scroll, "🔑 Keyword / Topic")

        self.pg_keyword_entry = ctk.CTkEntry(
            config_scroll,
            placeholder_text="e.g., horses running, sunset beach...",
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            placeholder_text_color=COLORS["text_muted"],
            font=ctk.CTkFont(size=13), height=38, corner_radius=8
        )
        self.pg_keyword_entry.pack(fill="x", padx=12, pady=(0, 12))
        self.pg_keyword_entry.bind("<Return>", lambda e: self._pg_generate())

        # ── Number of Prompts ──
        self._pg_field_label(config_scroll, "🔢 Number of Prompts")

        self.pg_count_var = ctk.StringVar(value="10")
        self.pg_count_entry = ctk.CTkEntry(
            config_scroll,
            textvariable=self.pg_count_var,
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(size=13), height=38, corner_radius=8,
            placeholder_text="1-100",
            placeholder_text_color=COLORS["text_muted"]
        )
        self.pg_count_entry.pack(fill="x", padx=12, pady=(0, 12))

        # ── AI Provider Info ──
        self._pg_field_label(config_scroll, "🤖 AI Provider")

        self.pg_provider_info = ctk.CTkLabel(
            config_scroll, text="Loading...",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"],
            fg_color=COLORS["bg_input"], corner_radius=6, height=30
        )
        self.pg_provider_info.pack(fill="x", padx=12, pady=(0, 16))

        # ── Generate Button ──
        self.pg_generate_btn = ctk.CTkButton(
            config_scroll, text="✨  Generate Prompts",
            command=self._pg_generate,
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white", font=ctk.CTkFont(size=14, weight="bold"),
            height=44, corner_radius=10
        )
        self.pg_generate_btn.pack(fill="x", padx=12, pady=(0, 8))

        # ── Status Label ──
        self.pg_status_label = ctk.CTkLabel(
            config_scroll, text="",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"]
        )
        self.pg_status_label.pack(padx=12)

        # ── Tips ──
        tips_frame = ctk.CTkFrame(
            config_scroll, fg_color=COLORS["bg_card"], corner_radius=10,
            border_width=1, border_color=COLORS["border"]
        )
        tips_frame.pack(fill="x", padx=8, pady=(12, 8))

        ctk.CTkLabel(
            tips_frame, text="💡 Tips",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=COLORS["neon_blue"]
        ).pack(padx=10, pady=(8, 2), anchor="w")

        tips = [
            "• Be specific with keywords for better results",
            "• Image prompts include 8K UHD sharpness",
            "• Vector prompts auto-add white bg, no text",
            "• Video prompts use structured format (4K UHD)",
            "• Copy All to export prompts quickly",
        ]
        for tip in tips:
            ctk.CTkLabel(
                tips_frame, text=tip,
                font=ctk.CTkFont(size=9), text_color=COLORS["text_secondary"],
                justify="left"
            ).pack(padx=10, pady=0, anchor="w")
        ctk.CTkLabel(tips_frame, text="").pack(pady=2)

        # ═══════════════════════════════════════════════════════════════════
        # RIGHT PANEL — Generated Prompts
        # ═══════════════════════════════════════════════════════════════════
        right_panel = ctk.CTkFrame(
            self.pg_page_frame, fg_color=COLORS["bg_dark"], corner_radius=12,
            border_width=1, border_color=COLORS["border"]
        )
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=(8, 12))
        right_panel.grid_rowconfigure(1, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        # ── Results Header ──
        results_header = ctk.CTkFrame(right_panel, fg_color=COLORS["bg_card"], corner_radius=0, height=48)
        results_header.grid(row=0, column=0, sticky="ew")
        results_header.grid_propagate(False)

        ctk.CTkLabel(
            results_header, text="📝  Generated Prompts",
            font=ctk.CTkFont(size=15, weight="bold"), text_color=COLORS["neon_blue"]
        ).pack(side="left", padx=16, pady=10)

        # Action buttons in header
        btn_container = ctk.CTkFrame(results_header, fg_color="transparent")
        btn_container.pack(side="right", padx=12, pady=8)

        self.pg_copy_all_btn = ctk.CTkButton(
            btn_container, text="📋 Copy All", width=100, height=30, corner_radius=6,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["accent_blue"], hover_color=COLORS["neon_blue"],
            text_color="white",
            command=self._pg_copy_all
        )
        self.pg_copy_all_btn.pack(side="left", padx=(0, 6))

        self.pg_clear_btn = ctk.CTkButton(
            btn_container, text="🗑  Clear All", width=100, height=30, corner_radius=6,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["bg_input"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            border_width=1, border_color=COLORS["border"],
            command=self._pg_clear_all
        )
        self.pg_clear_btn.pack(side="left")

        self.pg_count_label = ctk.CTkLabel(
            results_header, text="",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"]
        )
        self.pg_count_label.pack(side="right", padx=8)

        # Glow line
        ctk.CTkFrame(right_panel, fg_color=COLORS["neon_blue"], height=1).grid(
            row=0, column=0, sticky="sew")

        # ── Results Scroll Area ──
        self.pg_results_scroll = ctk.CTkScrollableFrame(
            right_panel, fg_color="transparent",
            scrollbar_button_color=COLORS["accent_blue"],
            scrollbar_button_hover_color=COLORS["neon_blue"]
        )
        self.pg_results_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=(4, 8))

        # Empty state
        self._pg_show_empty_state()

        # ── State ──
        self._pg_generating = False
        self._pg_stop_event = threading.Event()
        self._pg_prompts = []

    # ─── Helper ───────────────────────────────────────────────────────────

    def _pg_field_label(self, parent, text):
        """Create a styled field label."""
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_secondary"]
        ).pack(padx=12, pady=(12, 4), anchor="w")

    # ─── Type Selector ────────────────────────────────────────────────────

    def _pg_select_type(self, content_type):
        """Handle content type selection."""
        self.pg_type_var.set(content_type)

        for val, btn in self._pg_type_buttons.items():
            if val == content_type:
                btn.configure(fg_color=COLORS["accent_blue"], text_color="white")
            else:
                btn.configure(fg_color=COLORS["bg_input"], text_color=COLORS["text_secondary"])

        # Show/hide vector style selector
        if content_type == "vector":
            self.pg_style_frame.pack(fill="x", after=self._pg_type_buttons["video"].master, pady=(0, 4))
        else:
            self.pg_style_frame.pack_forget()

    # ─── Empty State ──────────────────────────────────────────────────────

    def _pg_show_empty_state(self):
        """Show empty state in results area."""
        for w in self.pg_results_scroll.winfo_children():
            w.destroy()

        empty = ctk.CTkFrame(self.pg_results_scroll, fg_color="transparent")
        empty.pack(expand=True, fill="both")

        inner = ctk.CTkFrame(empty, fg_color="transparent")
        inner.place(relx=0.5, rely=0.35, anchor="center")

        ctk.CTkLabel(
            inner, text="✨", font=ctk.CTkFont(size=48)
        ).pack(pady=(0, 8))

        ctk.CTkLabel(
            inner, text="AI Prompt Generator",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack()

        ctk.CTkLabel(
            inner, text="Generate unique, high-quality prompts\nfor microstock images, vectors, and videos",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"],
            justify="center"
        ).pack(pady=(4, 0))

    # ─── Update Provider Info ─────────────────────────────────────────────

    def _pg_update_provider_info(self):
        """Update the AI provider info label."""
        if hasattr(self, 'pg_provider_info'):
            provider = self.provider_var.get() if hasattr(self, 'provider_var') else "N/A"
            model = self.model_var.get() if hasattr(self, 'model_var') else "N/A"
            # Shorten model name for display
            short_model = model.split("/")[-1] if "/" in model else model
            self.pg_provider_info.configure(text=f"  {provider} / {short_model}")

    # ─── Generate ─────────────────────────────────────────────────────────

    def _pg_generate(self):
        """Start prompt generation."""
        if self._pg_generating:
            self._pg_stop_event.set()
            self.pg_generate_btn.configure(text="✨  Generate Prompts", fg_color=COLORS["accent_blue"])
            self._pg_generating = False
            return

        keyword = self.pg_keyword_entry.get().strip()
        if not keyword:
            self.pg_status_label.configure(
                text="⚠️ Please enter a keyword/topic",
                text_color=COLORS["warning"]
            )
            return

        try:
            count = int(self.pg_count_var.get())
            if count < 1 or count > 100:
                raise ValueError
        except ValueError:
            self.pg_status_label.configure(
                text="⚠️ Enter a valid number (1-100)",
                text_color=COLORS["warning"]
            )
            return

        # Get AI provider settings
        provider_name = self.provider_var.get() if hasattr(self, 'provider_var') else None
        model = self.model_var.get() if hasattr(self, 'model_var') else None
        api_key = self.api_keys.get(provider_name, "") if hasattr(self, 'api_keys') else ""
        if not api_key and hasattr(self, 'api_key_entry'):
            api_key = self.api_key_entry.get().strip()

        if not provider_name or not model or not api_key:
            self.pg_status_label.configure(
                text="⚠️ Configure AI provider in Metadata settings first",
                text_color=COLORS["warning"]
            )
            return

        content_type = self.pg_type_var.get()
        vector_style = self.pg_style_var.get() if content_type == "vector" else None

        # Update UI
        self._pg_generating = True
        self._pg_stop_event.clear()
        self.pg_generate_btn.configure(text="⏹  Stop", fg_color=COLORS["error"])
        self.pg_status_label.configure(text="⏳ Generating...", text_color=COLORS["neon_blue"])

        # Clear previous results
        for w in self.pg_results_scroll.winfo_children():
            w.destroy()

        # Show loading
        self.pg_loading_label = ctk.CTkLabel(
            self.pg_results_scroll,
            text=f"🤖 Generating {count} {content_type} prompts for \"{keyword}\"...",
            font=ctk.CTkFont(size=13), text_color=COLORS["text_muted"]
        )
        self.pg_loading_label.pack(pady=40)

        # Worker thread
        def _worker():
            try:
                prompts = generate_prompts(
                    keyword=keyword,
                    prompt_type=content_type,
                    count=count,
                    provider_name=provider_name,
                    model=model,
                    api_key=api_key,
                    vector_style=vector_style,
                    on_progress=lambda msg: self.after(0, lambda m=msg: self.pg_status_label.configure(text=m)),
                    stop_event=self._pg_stop_event,
                )
                self.after(0, lambda: self._pg_on_complete(prompts, content_type))
            except Exception as e:
                self.after(0, lambda: self._pg_on_error(str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    # ─── Callbacks ────────────────────────────────────────────────────────

    def _pg_on_complete(self, prompts, content_type):
        """Called when generation is complete."""
        self._pg_generating = False
        self._pg_prompts = prompts
        self.pg_generate_btn.configure(text="✨  Generate Prompts", fg_color=COLORS["accent_blue"])

        # Clear loading
        for w in self.pg_results_scroll.winfo_children():
            w.destroy()

        if not prompts:
            self.pg_status_label.configure(
                text="⚠️ No prompts generated", text_color=COLORS["warning"]
            )
            self._pg_show_empty_state()
            return

        self.pg_status_label.configure(
            text=f"✅ Generated {len(prompts)} {content_type} prompts",
            text_color=COLORS["success"]
        )
        self.pg_count_label.configure(text=f"{len(prompts)} prompts")

        # Render prompt cards
        for idx, prompt in enumerate(prompts):
            self._pg_create_prompt_card(idx, prompt, content_type)

    def _pg_on_error(self, error_msg):
        """Called when generation fails."""
        self._pg_generating = False
        self.pg_generate_btn.configure(text="✨  Generate Prompts", fg_color=COLORS["accent_blue"])

        for w in self.pg_results_scroll.winfo_children():
            w.destroy()

        self.pg_status_label.configure(
            text=f"❌ {error_msg[:80]}", text_color=COLORS["error"]
        )

        # Show error card
        error_card = ctk.CTkFrame(
            self.pg_results_scroll, fg_color=COLORS["bg_card"], corner_radius=10,
            border_width=1, border_color=COLORS["error"]
        )
        error_card.pack(fill="x", padx=8, pady=8)

        ctk.CTkLabel(
            error_card, text="❌ Generation Failed",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=COLORS["error"]
        ).pack(padx=16, pady=(12, 4))

        ctk.CTkLabel(
            error_card, text=error_msg,
            font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"],
            wraplength=500, justify="left"
        ).pack(padx=16, pady=(0, 12))

    # ─── Prompt Card ──────────────────────────────────────────────────────

    def _pg_create_prompt_card(self, idx, prompt, content_type):
        """Create a styled prompt card."""
        # Type icons
        type_icons = {"image": "📷", "vector": "🎨", "video": "🎬"}
        icon = type_icons.get(content_type, "✨")

        # Card colors — alternate
        card_bg = COLORS["bg_card"] if idx % 2 == 0 else COLORS["table_row_odd"]

        card = ctk.CTkFrame(
            self.pg_results_scroll, fg_color=card_bg, corner_radius=10,
            border_width=1, border_color=COLORS["border"]
        )
        card.pack(fill="x", padx=6, pady=3)

        # Top row: number + type icon + copy button
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(8, 4))

        # Badge
        badge_text = f" {icon} Prompt {idx + 1} "
        ctk.CTkLabel(
            top, text=badge_text,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["neon_blue"],
            fg_color=COLORS["bg_input"], corner_radius=4
        ).pack(side="left")

        # Copy single prompt button
        copy_btn = ctk.CTkButton(
            top, text="📋 Copy", width=70, height=24, corner_radius=4,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=COLORS["bg_input"], hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            border_width=1, border_color=COLORS["border"],
            command=lambda p=prompt, b=None: self._pg_copy_single(p, b)
        )
        copy_btn.pack(side="right")
        # Re-bind with correct button ref
        copy_btn.configure(command=lambda p=prompt, b=copy_btn: self._pg_copy_single(p, b))

        # Prompt text
        prompt_label = ctk.CTkLabel(
            card, text=prompt,
            font=ctk.CTkFont(size=12), text_color=COLORS["text_primary"],
            wraplength=700, justify="left", anchor="w"
        )
        prompt_label.pack(fill="x", padx=14, pady=(0, 10))

    # ─── Copy / Clear ─────────────────────────────────────────────────────

    def _pg_copy_single(self, prompt, btn=None):
        """Copy a single prompt to clipboard."""
        self.clipboard_clear()
        self.clipboard_append(prompt)
        if btn:
            original_text = btn.cget("text")
            btn.configure(text="✅ Copied!", text_color=COLORS["success"])
            self.after(1500, lambda: btn.configure(text=original_text, text_color=COLORS["text_secondary"]))

    def _pg_copy_all(self):
        """Copy all prompts to clipboard."""
        if not self._pg_prompts:
            return

        all_text = "\n\n".join(self._pg_prompts)
        self.clipboard_clear()
        self.clipboard_append(all_text)

        self.pg_copy_all_btn.configure(text="✅ Copied!")
        self.after(1500, lambda: self.pg_copy_all_btn.configure(text="📋 Copy All"))

    def _pg_clear_all(self):
        """Clear all generated prompts."""
        self._pg_prompts = []
        self.pg_count_label.configure(text="")
        self.pg_status_label.configure(text="")
        self._pg_show_empty_state()
